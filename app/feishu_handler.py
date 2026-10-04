import json
import logging
import threading
import asyncio
from typing import Optional, Dict, Any

from app.config import settings
from app.session_manager import session_manager
from app.workspace_manager import workspace_manager
from app.agent_runner import agent_runner

logger = logging.getLogger("antigravity.feishu")

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest, CreateMessageRequestBody,
        PatchMessageRequest, PatchMessageRequestBody,
        P2ImMessageReceiveV1
    )
    LARK_AVAILABLE = True
except ImportError:
    LARK_AVAILABLE = False
    logger.warning("未检测到 lark-oapi 模块，飞书长连接功能将被禁用。")


class FeishuHandler:
    """
    飞书官方 WebSocket 长连接与消息处理封装：
    支持零公网 IP / 零域名直连、双向交互卡片、原地局部刷新上架进度与文件自动归档沙盒。
    """

    def __init__(self):
        self._api_client = None
        self._ws_client = None
        self._thread = None
        self._running = False
        self._init_client()

    def _init_client(self):
        if not LARK_AVAILABLE or not settings.feishu_app_id or not settings.feishu_app_secret:
            return

        try:
            self._api_client = lark.Client.builder() \
                .app_id(settings.feishu_app_id) \
                .app_secret(settings.feishu_app_secret) \
                .build()
            logger.info("飞书 API Client 初始化就绪")
        except Exception as e:
            logger.error(f"初始化飞书 API Client 失败: {e}")

    @property
    def is_configured(self) -> bool:
        return bool(self._api_client and settings.feishu_app_id and settings.feishu_app_secret)

    def send_text(self, receive_id: str, content: str, id_type: str = "open_id") -> bool:
        """向飞书用户或群聊发送纯文本消息"""
        if not self._api_client:
            return False

        try:
            req = CreateMessageRequest.builder() \
                .receive_id_type(id_type) \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type("text")
                    .content(json.dumps({"text": content}))
                    .build()
                ).build()
            resp = self._api_client.im.v1.message.create(req)
            if not resp.success():
                logger.error(f"飞书发送文本消息失败: code={resp.code}, msg={resp.msg}")
                return False
            return True
        except Exception as e:
            logger.error(f"调用飞书发送接口异常: {e}")
            return False

    def send_interactive_card(self, receive_id: str, card_dict: Dict[str, Any], id_type: str = "open_id") -> Optional[str]:
        """向飞书用户或群聊发送富交互卡片（支持原地更新进度条）"""
        if not self._api_client:
            return None

        try:
            req = CreateMessageRequest.builder() \
                .receive_id_type(id_type) \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type("interactive")
                    .content(json.dumps(card_dict))
                    .build()
                ).build()
            resp = self._api_client.im.v1.message.create(req)
            if resp.success() and resp.data:
                return resp.data.message_id
            logger.error(f"飞书发送卡片失败: code={resp.code}, msg={resp.msg}")
            return None
        except Exception as e:
            logger.error(f"发送飞书交互卡片异常: {e}")
            return None

    def update_card(self, message_id: str, card_dict: Dict[str, Any]) -> bool:
        """原地 Patch 局部刷新已发出的卡片（进度条丝滑跳动无闪烁）"""
        if not self._api_client or not message_id:
            return False

        try:
            req = PatchMessageRequest.builder() \
                .message_id(message_id) \
                .request_body(
                    PatchMessageRequestBody.builder()
                    .content(json.dumps(card_dict))
                    .build()
                ).build()
            resp = self._api_client.im.v1.message.patch(req)
            return resp.success()
        except Exception as e:
            logger.error(f"更新飞书卡片异常: {e}")
            return False

    def build_progress_card(self, title: str, progress_pct: int, status_text: str, details: str = "") -> Dict[str, Any]:
        """构造带有实时进度条的标准化上架监控卡片"""
        filled_bars = int(progress_pct / 10)
        empty_bars = 10 - filled_bars
        progress_bar_visual = "🟩" * filled_bars + "⬜" * empty_bars

        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**进度**: {progress_bar_visual} **{progress_pct}%**\n**状态**: {status_text}"
                }
            }
        ]
        if details:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": details
                }
            })

        return {
            "config": {
                "wide_screen_mode": True
            },
            "header": {
                "template": "blue" if progress_pct < 100 else "green",
                "title": {
                    "tag": "plain_text",
                    "content": title
                }
            },
            "elements": elements
        }

    def _handle_incoming_message(self, data: 'P2ImMessageReceiveV1'):
        """处理通过 WebSocket 收到的飞书实时消息"""
        try:
            msg = data.event.message
            sender = data.event.sender
            sender_id = sender.sender_id.open_id or sender.sender_id.user_id or "feishu_user"
            chat_id = msg.chat_id
            msg_type = msg.message_type

            # 解析消息文本
            prompt = ""
            if msg_type == "text":
                content_json = json.loads(msg.content)
                prompt = content_json.get("text", "").strip()
            elif msg_type == "file":
                prompt = f"[用户在飞书发送了文件: {msg.content}]"
            else:
                prompt = f"收到不支持的消息类型: {msg_type}"

            if not prompt:
                return

            logger.info(f"收到飞书用户 [{sender_id}] 消息: {prompt}")

            # 区分会话 ID，统一隔离到租户沙盒
            feishu_user_prefix = f"feishu_{sender_id}"
            cid = session_manager.get_or_create_session(feishu_user_prefix)
            workspace = workspace_manager.get_user_workspace(feishu_user_prefix, cid)

            # 立即推送首张任务卡片
            card = self.build_progress_card(
                title="⚡ WB极速上架助手 · 任务启动",
                progress_pct=10,
                status_text="智能体已激活，正在核验店铺与准备执行环境...",
                details=f"🆔 **会话 ID**: `{cid[:8]}...{cid[-4:]}`\n📂 **隔离沙盒**: `{workspace.name}`\n📝 **用户指令**: {prompt[:100]}"
            )
            card_msg_id = self.send_interactive_card(sender_id, card, id_type="open_id")

            # 启动异步线程调用 Agent 执行任务
            def run_agent_task():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    # 模拟进度刷新为 50%
                    if card_msg_id:
                        self.update_card(
                            card_msg_id,
                            self.build_progress_card(
                                title="⚡ WB极速上架助手 · 执行中",
                                progress_pct=50,
                                status_text="正在解析商品参数与调用上架核心...",
                                details=f"🆔 **会话 ID**: `{cid[:8]}...{cid[-4:]}`\n⏳ 智能体推理与上架进行中..."
                            )
                        )

                    # 调度本地 Agent
                    loop.run_until_complete(
                        agent_runner.execute_task(
                            user_id=feishu_user_prefix,
                            prompt=prompt,
                            conversation_id=cid,
                            workspace=str(workspace)
                        )
                    )

                    # 完成后更新卡片为 100% 绿色
                    if card_msg_id:
                        self.update_card(
                            card_msg_id,
                            self.build_progress_card(
                                title="✅ WB极速上架助手 · 处理完成",
                                progress_pct=100,
                                status_text="上架任务与业务检查已执行完成。",
                                details=f"详情请参见下方生成的业务结论与明细回显。"
                            )
                        )
                except Exception as e:
                    logger.error(f"飞书 Agent 任务执行异常: {e}")
                    if card_msg_id:
                        self.update_card(
                            card_msg_id,
                            self.build_progress_card(
                                title="❌ WB极速上架助手 · 执行受阻",
                                progress_pct=100,
                                status_text=f"任务异常: {e}",
                                details="请核对卡密授权、店铺绑定或网络状态。"
                            )
                        )
                finally:
                    loop.close()

            threading.Thread(target=run_agent_task, daemon=True).start()

        except Exception as e:
            logger.error(f"处理飞书消息异常: {e}")

    def start_worker(self):
        """在后台守护线程中启动飞书 WebSocket 客户端"""
        if not self.is_configured or not settings.feishu_enabled:
            logger.info("飞书配置未启用或缺少凭证，跳过飞书长连接监听。")
            return

        if self._running:
            return

        def _ws_runner():
            self._running = True
            logger.info("飞书 WebSocket 客户端后台长连接正在建立...")
            try:
                event_handler = lark.EventDispatcherHandler.builder("", "") \
                    .register_p2_im_message_receive_v1(self._handle_incoming_message) \
                    .build()

                self._ws_client = lark.ws.Client(
                    app_id=settings.feishu_app_id,
                    app_secret=settings.feishu_app_secret,
                    event_handler=event_handler,
                    log_level=lark.LogLevel.INFO
                )
                self._ws_client.start()
            except Exception as e:
                logger.error(f"飞书 WebSocket 客户端运行异常: {e}")
            finally:
                self._running = False

        self._thread = threading.Thread(target=_ws_runner, daemon=True, name="FeishuWSWorker")
        self._thread.start()
        logger.info("飞书 WebSocket 监听线程已启动 (官方长连接模式，免公网IP/免域名)")

    def stop_worker(self):
        """停止飞书客户端"""
        self._running = False
        logger.info("飞书 WebSocket 客户端已请求停止")


feishu_handler = FeishuHandler()
