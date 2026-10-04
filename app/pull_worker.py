import asyncio
import logging
import httpx
from wechatpy.enterprise import parse_message
from app.config import settings
from app.security import SecurityManager
from app.session_manager import session_manager
from app.workspace_manager import workspace_manager
from app.task_queue import task_queue
from app.agent_runner import agent_runner
from app.wechat_handler import wechat_handler

logger = logging.getLogger("antigravity.pull_worker")


class WeChatPullWorker:
    """
    边缘 KV 消息队列主动长轮询拉取器 (Pull Mode)。
    无需用户电脑具备公网 IP、无需配置反向代理或 Tunnel，
    仅通过标准出网 HTTPS 即可从 Cloudflare Edge 实时拉取用户在微信发送的消息。
    """

    def __init__(self):
        self._running = False
        self._poll_task = None
        self._poll_url = "https://wechat.diytale.com/wechat/poll"

    def start(self):
        if not self._running:
            self._running = True
            self._poll_task = asyncio.create_task(self._poll_loop())
            logger.info("企业微信边缘拉取工作线程已启动 (Cloudflare KV Pull Mode 开启)")

    def stop(self):
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            logger.info("企业微信边缘拉取工作线程已停止")

    async def _poll_loop(self):
        token = settings.wechat_token
        headers = {"Authorization": f"Bearer {token}"}
        params = {"token": token}

        async with httpx.AsyncClient(timeout=15.0) as client:
            while self._running:
                try:
                    resp = await client.get(self._poll_url, headers=headers, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        messages = data.get("messages", [])
                        if messages:
                            logger.info(f"成功从边缘队列拉取到 {len(messages)} 条微信消息")
                            for item in messages:
                                msg_payload = item.get("data", {})
                                await self._handle_incoming_message(msg_payload)
                    elif resp.status_code == 401:
                        logger.error("边缘队列拉取鉴权失败 (401)，请核对 WECHAT_TOKEN 配置")
                        await asyncio.sleep(10)
                    else:
                        await asyncio.sleep(2)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"轮询边缘队列网络等待: {e}")
                    await asyncio.sleep(3)

                # 间隔 1.5 秒轮询一次
                await asyncio.sleep(1.5)

    async def _handle_incoming_message(self, payload: dict):
        try:
            decrypted_xml = payload.get("decrypted_xml")
            if not decrypted_xml:
                return

            msg = parse_message(decrypted_xml)
            user_id = getattr(msg, "source", None)
            if not user_id:
                return

            # 1. 鉴权白名单检查
            is_allowed, auth_msg = SecurityManager.verify_user(user_id)
            if not is_allowed:
                logger.warning(f"拉取消息拦截未授权用户: {user_id}")
                wechat_handler.send_proactive_text(user_id, auth_msg)
                return

            # 2. 会话管理
            cid = session_manager.get_or_create_session(user_id)
            session_info = session_manager.get_session_info(user_id)
            ws_path = workspace_manager.get_user_workspace(user_id, cid)

            # 3. 处理文件消息 (msg.type == "file")
            if getattr(msg, "type", "") == "file":
                file_name = getattr(msg, "file_name", "upload.txt")
                media_id = getattr(msg, "media_id", "")
                logger.info(f"拉取到微信文件消息: 用户={user_id}, 文件={file_name}")

                uploads_dir = workspace_manager.get_uploads_dir(user_id, cid)
                dest_path = uploads_dir / workspace_manager.sanitize_name(file_name)
                download_ok = wechat_handler.download_media(media_id, str(dest_path))

                if download_ok:
                    wechat_handler.send_proactive_text(
                        user_id,
                        f"📁 已安全接收文件【{file_name}】并存放于您的独立沙盒！\n智能体已接入处理..."
                    )
                    prompt = (
                        f"用户在微信上传了文件【{file_name}】，已保存在隔离工作区：{dest_path}。\n"
                        f"请先读取并分析该文件内容（如提取 SKU 列表），然后根据用户店铺准备自动化上架。"
                    )
                    asyncio.create_task(
                        task_queue.submit_task(
                            agent_runner.execute_task,
                            user_id=user_id,
                            prompt=prompt,
                            conversation_id=cid,
                            workspace=str(ws_path)
                        )
                    )
                return

            # 4. 处理文本消息 (msg.type == "text")
            if getattr(msg, "type", "") == "text":
                content = getattr(msg, "content", "").strip()

                if content == "/reset":
                    new_cid = session_manager.reset_session(user_id)
                    new_ws = workspace_manager.get_user_workspace(user_id, new_cid)
                    wechat_handler.send_proactive_text(
                        user_id,
                        f"🔄 会话重置成功！\n- 新会话 ID: `{new_cid}`\n- 独立沙盒: `{new_ws.name}`\n原上下文已封存。"
                    )
                    return

                if content in ("/status", "/identity"):
                    wechat_handler.send_proactive_text(
                        user_id,
                        f"📊 [当前会话状态]\n- 用户 ID: `{user_id}`\n- 会话 ID: `{cid}`\n- 独立工作区: `{ws_path}`\n- 服务并发数: {task_queue.active_tasks}/{task_queue.max_concurrent}\n- 排队任务数: {task_queue.queued_tasks}"
                    )
                    return

                if content in ("/help", "帮助", "?"):
                    help_text = (
                        f"💡 [Antigravity 微信上架助手指令说明]\n"
                        f"1. 直接发文字：自然语言交待任务（如“检查我的店铺状态”、“按6倍上架刚才的文件”）。\n"
                        f"2. 发送文件：直接在微信点击“+”发送包含 SKU 的 .txt 文本或表格，智能体将自动读取并准备上架。\n"
                        f"3. /reset：重置当前会话，开启全新的独立上架窗口。\n"
                        f"4. /status：查看您当前的会话 ID、工作区路径与服务器并发状态。"
                    )
                    wechat_handler.send_proactive_text(user_id, help_text)
                    return

                # 指令安全校验
                is_valid_prompt, audit_msg = SecurityManager.audit_prompt(content)
                if not is_valid_prompt:
                    wechat_handler.send_proactive_text(user_id, f"⚠️ {audit_msg}")
                    return

                wechat_handler.send_proactive_text(
                    user_id,
                    f"收到指令！您的专属 Antigravity Agent 已接入任务并在后台调度中，请稍候..."
                )

                asyncio.create_task(
                    task_queue.submit_task(
                        agent_runner.execute_task,
                        user_id=user_id,
                        prompt=content,
                        conversation_id=cid,
                        workspace=str(ws_path)
                    )
                )

        except Exception as e:
            logger.error(f"处理拉取到的微信消息异常: {e}", exc_info=True)


pull_worker = WeChatPullWorker()
