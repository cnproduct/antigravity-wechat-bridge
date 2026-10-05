import os
import sys
import re
import json
import time
import logging
import asyncio
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List, Set

from app.config import settings
from app.wechat_handler import wechat_handler
from app.session_manager import session_manager
from app.workspace_manager import workspace_manager

logger = logging.getLogger("antigravity.runner")

# 检测并导入 google-antigravity SDK（若安装）
try:
    from google.antigravity import Agent, LocalAgentConfig
    ANTIGRAVITY_SDK_AVAILABLE = True
except ImportError:
    ANTIGRAVITY_SDK_AVAILABLE = False


class AgentRunner:
    """
    WB极速上架智能体核心调度器（微信与飞书双通道版）：
    1. 动态对接本机原生 wb CLI 客户端（~/.codex/wb-cloud-client/wb），实现真实身份查询、授权激活、店铺绑定与批量上架。
    2. 支持逐款增量上架进度播报（“已完成第1款”、“已完成第2款”...），配合飞书交互卡片丝滑局部刷新。
    3. 全批次执行完成自动生成《WB极速上架批次结束报告》，包含核实商品明细与下一步操作指引。
    4. 严格杜绝不同微信/飞书租户跨会话串联，全流程强制携带 --conversation-id。
    """

    def __init__(self):
        self._active_monitors: Dict[str, asyncio.Task] = {}

    @staticmethod
    def get_wb_cmd() -> List[str]:
        """获取本地跨平台 wb 命令行可执行入口"""
        launcher = Path.home() / ".codex/wb-cloud-client/wb"
        if launcher.exists():
            return [str(launcher)]
        cmd_launcher = Path.home() / ".codex/wb-cloud-client/wb.cmd"
        if cmd_launcher.exists():
            return [str(cmd_launcher)]
        skill_client = Path.home() / ".gemini/config/skills/ozon-to-wb-fast-listing/wb_client.py"
        if skill_client.exists():
            return [sys.executable, "-X", "utf8", str(skill_client)]
        return ["wb"]

    def _run_wb(self, args: List[str], conversation_id: str, timeout: int = 60) -> tuple[int, str, str]:
        """执行本地 wb 命令并捕获输出"""
        cmd = self.get_wb_cmd() + args
        if conversation_id and "--conversation-id" not in args:
            cmd.extend(["--conversation-id", conversation_id])

        env = dict(os.environ)
        env["ANTIGRAVITY_CONVERSATION_ID"] = conversation_id
        env["PYTHONUTF8"] = "1"

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env
            )
            stdout = res.stdout.strip() if res.stdout else ""
            stderr = res.stderr.strip() if res.stderr else ""
            return res.returncode, stdout, stderr
        except Exception as e:
            logger.error(f"调用 wb 命令失败 {cmd}: {e}")
            return -1, "", str(e)

    def _get_identity(self, conversation_id: str) -> Dict[str, Any]:
        """获取当前会话的 SID 与授权检查结果"""
        code, out, err = self._run_wb(["identity"], conversation_id)
        sid = ""
        for line in out.splitlines():
            if "设备 SID:" in line:
                sid = line.split(":", 1)[1].strip()

        check_code, check_out, check_err = self._run_wb(["check"], conversation_id)
        is_authorized = (check_code == 0)

        return {
            "sid": sid or "未知",
            "cid": conversation_id,
            "is_authorized": is_authorized,
            "check_msg": check_out if check_code == 0 else (check_err or check_out or "未授权")
        }

    def _notify_user(self, user_id: str, content: str):
        """向飞书或微信用户推送纯文本消息"""
        if user_id.startswith("feishu_"):
            from app.feishu_handler import feishu_handler
            feishu_open_id = user_id[7:]
            feishu_handler.send_text(receive_id=feishu_open_id, content=content)
        else:
            wechat_handler.send_proactive_text(user_id=user_id, content=content)

    def _update_feishu_card(
        self,
        user_id: str,
        card_msg_id: Optional[str],
        title: str,
        pct: int,
        status_text: str,
        details: str = ""
    ):
        """更新飞书互动卡片进度"""
        if not user_id.startswith("feishu_") or not card_msg_id:
            return
        from app.feishu_handler import feishu_handler
        card = feishu_handler.build_progress_card(
            title=title,
            progress_pct=pct,
            status_text=status_text,
            details=details
        )
        feishu_handler.update_card(card_msg_id, card)

    def _get_batch_state(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """读取会话底层的批次状态 JSON"""
        state_dir = Path.home() / ".wb-cloud-client"
        batch_file = state_dir / f"{conversation_id}.batch.json"
        if not batch_file.exists():
            return None
        try:
            return json.loads(batch_file.read_text(encoding="utf-8"))
        except Exception:
            return None

    async def execute_task(
        self,
        user_id: str,
        prompt: str,
        conversation_id: Optional[str] = None,
        workspace: Optional[str] = None,
        card_msg_id: Optional[str] = None
    ):
        """
        处理来自微信或飞书用户的上架业务指令
        """
        cid = conversation_id or session_manager.get_or_create_session(user_id)
        ws_path = Path(workspace) if workspace else workspace_manager.get_user_workspace(user_id, cid)
        os.environ["ANTIGRAVITY_CONVERSATION_ID"] = cid

        clean_prompt = prompt.strip()
        logger.info(f"执行任务 [user={user_id}, cid={cid}]: {clean_prompt[:80]}")

        # 1. 意图分类与提取
        skus = re.findall(r'\b\d{8,12}\b', clean_prompt)
        is_status_query = bool(re.search(r'(几款|状态|进度|情况|完成情况|status|batch-status)', clean_prompt, re.IGNORECASE))
        is_identity_query = bool(re.search(r'(身份|identity|sid|cid|设备码|窗口id|机器码)', clean_prompt, re.IGNORECASE))
        is_activation = bool(re.search(r'(激活|授权|卡密|activate)', clean_prompt, re.IGNORECASE))
        is_store_bind = bool(re.search(r'(绑定|bind|店铺)', clean_prompt, re.IGNORECASE))
        is_listing_cmd = bool(re.search(r'上架', clean_prompt) and (skus or "倍" in clean_prompt or "库存" in clean_prompt) and not is_status_query)

        # 2. 分支处理
        # 分支 A: 身份信息查询
        if is_identity_query:
            ident = self._get_identity(cid)
            auth_desc = "🟢 已授权" if ident["is_authorized"] else "⚠️ 未授权 (需联系代理商获取卡密)"
            reply = (
                f"📱 **[WB极速上架助手 · 设备与窗口身份]**\n\n"
                f"• **设备 SID**: `{ident['sid']}`\n"
                f"• **当前窗口 ID (CID)**: `{ident['cid']}`\n"
                f"• **授权状态**: {auth_desc}\n\n"
                f"💡 如需激活，请向代理商申请当前窗口卡密后，回复：`激活授权 <您的卡密>`"
            )
            self._update_feishu_card(user_id, card_msg_id, "📱 WB极速上架助手 · 身份已核实", 100, "身份与授权状态已就绪", f"SID: `{ident['sid'][:12]}...`")
            self._notify_user(user_id, reply)
            return

        # 分支 B: 激活卡密
        if is_activation:
            # 提取卡密（支持 激活授权 xxx 或 卡密: xxx 或 直接长串）
            key_match = re.search(r'(?:激活|授权|卡密|activate)[:：\s]+([a-zA-Z0-9_\-]+)', clean_prompt, re.IGNORECASE)
            key = key_match.group(1).strip() if key_match else ""
            if not key:
                # 尝试直接匹配 30位以上的卡密码串
                long_match = re.search(r'\b[a-zA-Z0-9_\-]{20,60}\b', clean_prompt)
                key = long_match.group(0).strip() if long_match else ""

            if not key:
                reply = "⚠️ 未识别到有效卡密格式。请回复：`激活授权 <您的卡密>`。"
                self._notify_user(user_id, reply)
                return

            code, out, err = self._run_wb(["activate", "--license-key", key], cid)
            ident = self._get_identity(cid)
            if code == 0 or ident["is_authorized"]:
                reply = (
                    f"🎉 **当前窗口授权已成功激活！**\n\n"
                    f"• **设备 SID**: `{ident['sid']}`\n"
                    f"• **窗口 CID**: `{cid}`\n"
                    f"• **提示信息**: {out or '授权有效'}\n\n"
                    f"⏭️ **下一步**：请绑定您的 WB 店铺，回复指令：\n"
                    f"`绑定店铺 仓库 <FBS仓库ID> 令牌 <WB API Token>`"
                )
                self._update_feishu_card(user_id, card_msg_id, "🎉 授权激活成功", 100, "当前窗口已取得有效授权", f"会话 ID: `{cid[:8]}...`")
            else:
                reply = f"❌ 授权激活未成功：\n{err or out or '卡密无效或与设备不匹配，请联系代理商确认。'}"
                self._update_feishu_card(user_id, card_msg_id, "❌ 授权激活未成功", 100, "卡密校验未通过", err or "请联系代理商")
            self._notify_user(user_id, reply)
            return

        # 分支 C: 绑定店铺
        if is_store_bind:
            wh_match = re.search(r'仓库\s*[:：]?\s*(\d+)', clean_prompt)
            token_match = re.search(r'(?:令牌|token)\s*[:：]?\s*([a-zA-Z0-9_\-\.]+)', clean_prompt, re.IGNORECASE)
            warehouse_id = wh_match.group(1) if wh_match else ""
            token = token_match.group(1) if token_match else ""

            if not warehouse_id or not token:
                reply = (
                    f"⚠️ 绑定店铺参数不完整。\n"
                    f"请提供 FBS 仓库 ID 与 WB API 令牌，标准格式：\n"
                    f"`绑定店铺 仓库 123456 令牌 eyJhbGciOi...`"
                )
                self._notify_user(user_id, reply)
                return

            code, out, err = self._run_wb(["bind", "--warehouse", warehouse_id, "--wb-api-token", token], cid)
            if code == 0:
                reply = (
                    f"✅ **WB 店铺绑定成功！**\n\n"
                    f"• **FBS 仓库 ID**: `{warehouse_id}`\n"
                    f"• **安全保障**: API 令牌已安全写入云端通道。\n\n"
                    f"🚀 现在您可以直接发送 SKU 开启极速上架，例如：\n"
                    f"`上架 5265064372 1680231276 1947063706 1020431776 6倍 5件库存`"
                )
                self._update_feishu_card(user_id, card_msg_id, "✅ WB 店铺绑定就绪", 100, f"仓库 {warehouse_id} 绑定成功", "随时可以开始上架")
            else:
                reply = f"❌ 店铺绑定失败：\n{err or out or '请检查仓库 ID 与令牌有效性。'}"
                self._update_feishu_card(user_id, card_msg_id, "❌ 店铺绑定受阻", 100, err or "绑定失败")
            self._notify_user(user_id, reply)
            return

        # 分支 D: 上架进度 / 几款 / 状态查询 (直接回答用户“上架几款了”等核心问题)
        if is_status_query:
            state = self._get_batch_state(cid)
            ident = self._get_identity(cid)

            if state and state.get("items"):
                items = state["items"]
                total = len(items)
                written = [s for s, i in items.items() if i.get("stage") == "written"]
                blocked = [s for s, i in items.items() if i.get("stage") == "blocked"]
                in_prog = [s for s, i in items.items() if i.get("stage") not in ("written", "blocked")]

                sku_lines = []
                for s, i in items.items():
                    st = i.get("stage", "pending")
                    if st == "written":
                        sku_lines.append(f"• SKU `{s}`: 🟢 **已核实上架**")
                    elif st == "blocked":
                        reason = self._friendly_error(i.get("error") or i.get("last_error") or "待核对")
                        sku_lines.append(f"• SKU `{s}`: ⚠️ **需核对** ({reason})")
                    elif st in ("submitting", "captured"):
                        sku_lines.append(f"• SKU `{s}`: ⏳ **正在录入/回读核验**")
                    else:
                        sku_lines.append(f"• SKU `{s}`: ⏳ **排队等待抓取**")

                details_text = "\n".join(sku_lines)
                pct = int(len(written) / total * 100) if total else 0

                reply = (
                    f"📊 **[WB极速上架助手 · 当前上架进度汇报]**\n\n"
                    f"• **计划总数**: **{total}** 款\n"
                    f"• 🟢 **已完成上架**: **{len(written)}** 款 (占比 {pct}%)\n"
                    f"• ⏳ **正在推进中**: **{len(in_prog)}** 款\n"
                    f"• ⚠️ **需核对/阻塞**: **{len(blocked)}** 款\n\n"
                    f"**各商品明细核实状态**：\n{details_text}\n\n"
                    f"💡 系统在后台持续推进中，每完成一款都会实时播报进度与最终结束报告。"
                )
                self._update_feishu_card(user_id, card_msg_id, f"📊 上架进度 ({len(written)}/{total} 款完成)", pct, f"已完成 {len(written)} 款", f"最新进度: {pct}%")
            else:
                # 尚未启动或尚未授权
                if not ident["is_authorized"]:
                    reply = (
                        f"⚠️ **您安排的 4 款商品目前尚未提交上架（已上架 0 款）。**\n\n"
                        f"**【原因核实】**：\n"
                        f"1. 此前网关处于 Mock 模拟隔离状态，未将任务交付真实上架核心；\n"
                        f"2. 当前会话窗口尚未激活有效卡密（`当前窗口未授权`），且尚未绑定 WB 店铺。\n\n"
                        f"**【您的身份信息】**：\n"
                        f"• 设备 SID: `{ident['sid']}`\n"
                        f"• 窗口 CID: `{cid}`\n\n"
                        f"**【开启真实上架的 3 步操作】**：\n"
                        f"1. 联系代理商获取此窗口卡密后回复：`激活授权 <卡密>`；\n"
                        f"2. 回复：`绑定店铺 仓库 <FBS仓库ID> 令牌 <WB API Token>`；\n"
                        f"3. 回复：`上架 5265064372 1680231276 1947063706 1020431776 6倍 5件库存`。\n"
                        f"完成上述步骤后，系统将立即启动上架，并逐款播报完成进展与结束报告！"
                    )
                    self._update_feishu_card(user_id, card_msg_id, "⚠️ 当前窗口待激活授权", 0, "尚未激活授权卡密", f"CID: `{cid[:8]}...`")
                else:
                    reply = (
                        f"ℹ️ **当前窗口已授权，但尚未启动任何商品上架批次（已上架 0 款）。**\n\n"
                        f"您可以直接在此回复上架指令，例如：\n"
                        f"`上架 5265064372 1680231276 1947063706 1020431776 6倍 5件库存`\n"
                        f"系统将立即开始自动抓取与上架！"
                    )
                    self._update_feishu_card(user_id, card_msg_id, "ℹ️ 暂无运行中的上架批次", 100, "等待用户下发上架指令")

            self._notify_user(user_id, reply)
            return

        # 分支 E: 启动批量上架任务 (Batch Listing)
        if is_listing_cmd or (skus and len(skus) >= 1):
            ident = self._get_identity(cid)

            # 提取倍数与库存
            mult_match = re.search(r'(\d+(?:\.\d+)?)\s*倍', clean_prompt)
            stock_match = re.search(r'(\d+)\s*件', clean_prompt) or re.search(r'库存\s*[:：]?\s*(\d+)', clean_prompt)
            multiplier = mult_match.group(1) if mult_match else "5"
            stock = stock_match.group(1) if stock_match else "5"

            if not ident["is_authorized"]:
                reply = (
                    f"⚠️ **当前窗口尚未激活授权，无法启动上架任务。**\n\n"
                    f"已为您提取 **{len(skus)}** 款待上架商品：\n"
                    + "\n".join(f"• SKU `{s}`" for s in skus)
                    + f"\n\n**【设备与窗口身份】**：\n"
                    f"• 设备 SID: `{ident['sid']}`\n"
                    f"• 窗口 CID: `{cid}`\n\n"
                    f"**【解决步骤】**：\n"
                    f"请向代理商或管理员申请此窗口授权卡密后，回复：\n"
                    f"`激活授权 <您的卡密>`\n"
                    f"激活并绑定店铺后即可立即开始真实上架。"
                )
                self._update_feishu_card(user_id, card_msg_id, "⚠️ 当前窗口未授权", 0, "需要先激活卡密", f"已识别 {len(skus)} 款商品")
                self._notify_user(user_id, reply)
                return

            # 已授权：调用本地 wb batch 注册并启动
            skus_str = ",".join(skus)
            logger.info(f"启动批次上架: cid={cid}, skus={skus_str}, mult={multiplier}, stock={stock}")

            code, out, err = self._run_wb([
                "batch",
                "--skus", skus_str,
                "--multiplier", str(multiplier),
                "--stock", str(stock),
                "--start"
            ], cid)

            if code != 0:
                reply = (
                    f"❌ **上架批次启动受阻**：\n"
                    f"{err or out or '系统启动批次失败，请检查店铺绑定是否完整。'}\n\n"
                    f"如尚未绑定店铺，请回复：`绑定店铺 仓库 <ID> 令牌 <Token>`"
                )
                self._update_feishu_card(user_id, card_msg_id, "❌ 上架启动失败", 100, err or "请检查店铺配置")
                self._notify_user(user_id, reply)
                return

            # 批次成功启动
            startup_msg = (
                f"🚀 **WB 批次上架任务已成功启动！**\n\n"
                f"• **计划总数**: **{len(skus)}** 款商品\n"
                f"• **价格倍数**: {multiplier} 倍 (绿色促销价保护)\n"
                f"• **初始库存**: {stock} 件\n"
                f"• **会话 ID**: `{cid[:8]}...{cid[-4:]}`\n\n"
                f"⚡ 后台智能体正在进行商品抓取、相册解析与价格校验。\n"
                f"系统将**实时为您逐款播报完成进度（完成第1款、第2款...）**，并在全批次结束时输出《结束报告》。"
            )
            self._update_feishu_card(
                user_id,
                card_msg_id,
                f"⚡ WB极速上架中 (0/{len(skus)} 款)",
                10,
                f"已启动 {len(skus)} 款上架任务，后台处理中...",
                f"倍数: {multiplier}x | 库存: {stock}件"
            )
            self._notify_user(user_id, startup_msg)

            # 启动后台实时监控线程/协程
            if cid not in self._active_monitors or self._active_monitors[cid].done():
                self._active_monitors[cid] = asyncio.create_task(
                    self._monitor_batch_progress(user_id, cid, card_msg_id, len(skus), multiplier, stock)
                )
            return

        # 分支 F: 其他通用问答 / 帮助
        help_text = (
            f"💡 **[WB极速上架助手使用指南]**\n\n"
            f"1. **查询身份**: 回复 `身份信息` 查看当前设备 SID 与窗口 CID。\n"
            f"2. **激活授权**: 回复 `激活授权 <卡密>` 激活当前会话窗口。\n"
            f"3. **绑定店铺**: 回复 `绑定店铺 仓库 <FBS仓库ID> 令牌 <WB API Token>`。\n"
            f"4. **上架商品**: 回复 `上架 5265064372 1680231276 6倍 5件库存`。\n"
            f"5. **查询进度**: 回复 `上架几款了` 或 `查询进度` 随时获取各款实时状态。"
        )
        self._update_feishu_card(user_id, card_msg_id, "💡 WB极速上架助手", 100, "支持极速上架与进度监控")
        self._notify_user(user_id, help_text)

    async def _monitor_batch_progress(
        self,
        user_id: str,
        conversation_id: str,
        card_msg_id: Optional[str],
        total_planned: int,
        multiplier: str,
        stock: str
    ):
        """
        后台轮询上架进展：逐款播报完成进度并在最终输出《结束报告》
        """
        logger.info(f"启动批次监控 [cid={conversation_id}, planned={total_planned}]")
        reported_written: Set[str] = set()
        reported_blocked: Set[str] = set()
        start_time = time.monotonic()
        poll_interval = 3
        max_duration = 1800  # 30 分钟超时保护

        while time.monotonic() - start_time < max_duration:
            await asyncio.sleep(poll_interval)
            state = self._get_batch_state(conversation_id)
            if not state or not state.get("items"):
                continue

            items = state["items"]
            total = len(items) or total_planned

            # 1. 检查新完成的 SKU
            for sku, info in items.items():
                stage = info.get("stage")
                if stage == "written" and sku not in reported_written:
                    reported_written.add(sku)
                    done_count = len(reported_written)
                    pct = int(done_count / total * 100)

                    # 发送逐款完成通知
                    progress_text = (
                        f"🟢 **【上架进度播报】已完成第 {done_count} 款 / 共 {total} 款**\n\n"
                        f"• **商品 SKU**: `{sku}`\n"
                        f"• **当前状态**: 已核实商品并正式写入 WB 店铺\n"
                        f"• **整体进度**: {pct}%"
                    )
                    self._notify_user(user_id, progress_text)

                    # 更新飞书卡片
                    self._update_feishu_card(
                        user_id,
                        card_msg_id,
                        f"⚡ WB极速上架中 ({done_count}/{total} 款完成)",
                        pct,
                        f"已完成第 {done_count} 款 ({sku})，正在推进下一款...",
                        f"🟢 最新完成: `{sku}`"
                    )

                elif stage == "blocked" and sku not in reported_blocked:
                    reported_blocked.add(sku)
                    err_msg = self._friendly_error(info.get("error") or info.get("last_error") or "商品资料待核实")
                    alert_text = (
                        f"⚠️ **【商品异常提醒】第 {len(reported_written) + len(reported_blocked)} 款需人工核对**\n\n"
                        f"• **商品 SKU**: `{sku}`\n"
                        f"• **阻塞原因**: {err_msg}\n"
                        f"• **处理建议**: 检查 Ozon 页面价格与币种后可重新提交。"
                    )
                    self._notify_user(user_id, alert_text)

            # 1.5 检查并触发 Antigravity 采集
            batch_state = state.get("state")
            if batch_state == "waiting_browser":
                awaiting_skus = [s for s, i in items.items() if i.get("stage") == "awaiting_browser"]
                if awaiting_skus:
                    from app.ozon_scraper import process_awaiting_browser
                    for s in awaiting_skus:
                        # 使用 batch 的 started_at，客户端用来校验 receipt 是否过期
                        batch_started = int(state.get("started_at", 0))
                        await process_awaiting_browser(conversation_id, s, batch_started, self.get_wb_cmd())

            # 2. 检查是否批次终态（全部完成或全部进入终态）
            terminal_count = len(reported_written) + len(reported_blocked)

            if terminal_count >= total or batch_state in ("completed", "paused"):
                # 生成终态结束报告
                logger.info(f"批次终态到达: written={len(reported_written)}, blocked={len(reported_blocked)}, total={total}")

                written_lines = []
                for idx, sku in enumerate(reported_written, 1):
                    written_lines.append(f"{idx}. SKU `{sku}`: 🟢 **已核实上架** (倍数: {multiplier}x, 库存: {stock}件)")

                blocked_lines = []
                if reported_blocked:
                    for idx, sku in enumerate(reported_blocked, 1):
                        err_msg = self._friendly_error(items.get(sku, {}).get("error") or "待核验")
                        blocked_lines.append(f"{idx}. SKU `{sku}`: ⚠️ **需核对** ({err_msg})")

                details_section = ""
                if written_lines:
                    details_section += "**已成功上架商品明细**：\n" + "\n".join(written_lines) + "\n\n"
                if blocked_lines:
                    details_section += "**需核对商品明细**：\n" + "\n".join(blocked_lines) + "\n\n"

                final_report = (
                    f"### 📋 WB极速上架批次结束报告\n"
                    f"**会话 ID**: `{conversation_id[:8]}...{conversation_id[-4:]}`\n"
                    f"**批次总数**: **{total}** 款商品\n"
                    f"- 🟢 **成功上架**: **{len(reported_written)}** 款\n"
                    f"- ⚠️ **需核对/阻塞**: **{len(reported_blocked)}** 款\n\n"
                    f"{details_section}"
                    f"**⏭️ 客户下一步操作指引**：\n"
                    f"1. 请登录 WB 卖家后台 (WB Seller Portal) 查验新上架商品的卡片与图片；\n"
                    f"2. 核对商品对应 FBS 仓库的库存与标价；\n"
                    f"3. 若有需核对款，核实 Ozon 页面人民币价格后可直接回复重新提交。"
                )

                self._notify_user(user_id, final_report)

                # 卡片置绿
                self._update_feishu_card(
                    user_id,
                    card_msg_id,
                    "✅ WB极速上架助手 · 批次全部完成",
                    100,
                    f"全部 {total} 款处理完毕 (成功 {len(reported_written)} 款, 需核对 {len(reported_blocked)} 款)",
                    "详情请查收上方结束报告与明细。"
                )
                break

    @staticmethod
    def _friendly_error(code: str) -> str:
        """转换常见错误码为客户友好中文说明"""
        mapping = {
            "switch_ozon_to_cny": "需将 Ozon 页面币种核实为人民币 (CNY)",
            "ozon_green_price_unverified": "未找到绿色促销价格或价格待核实",
            "ozon_currency_unverified": "页面币种未核实",
            "brand_unverified": "来源品牌缺失或冲突",
            "brand_readback_mismatch": "WB 品牌与来源品牌不一致",
            "media_readback_mismatch": "图片尚未通过相册回读核验",
            "package_readback_mismatch": "包装尺寸或毛重回读不一致",
            "cost_data_required": "需确认成本与促销保护数据",
            "wb_access_denied": "WB 店铺令牌权限不足或已失效",
            "store_not_bound": "尚未绑定 WB 店铺",
            "product_data_incomplete": "商品基本信息不完整"
        }
        return mapping.get(code, code)


agent_runner = AgentRunner()
