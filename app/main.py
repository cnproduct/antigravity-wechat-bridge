import os
import logging
from pathlib import Path
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from typing import Optional

from app.config import settings
from app.wechat_handler import wechat_handler
from app.agent_runner import agent_runner, ANTIGRAVITY_SDK_AVAILABLE
from app.security import SecurityManager
from app.session_manager import session_manager
from app.workspace_manager import workspace_manager
from app.task_queue import task_queue

from contextlib import asynccontextmanager
from app.pull_worker import pull_worker

# 配置日志格式
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("antigravity.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动边缘队列拉取线程 (支持无公网 IP / 无隧道零配置工作)
    pull_worker.start()
    yield
    pull_worker.stop()


app = FastAPI(
    title="Antigravity WeChat Bridge",
    description="轻量级微信 / 企业微信与 Google Antigravity Agent 远程调度桥接网关（支持多租户与强隔离）",
    version="1.1.0",
    lifespan=lifespan
)


class DispatchRequest(BaseModel):
    user_id: str = "test_user"
    prompt: str
    workspace: Optional[str] = None
    conversation_id: Optional[str] = None


@app.get("/")
async def root():
    return {
        "service": "Antigravity WeChat Bridge",
        "status": "online",
        "version": "1.1.0",
        "antigravity_sdk_loaded": ANTIGRAVITY_SDK_AVAILABLE,
        "wechat_configured": wechat_handler.is_configured,
        "tenants_dir": settings.tenants_dir,
        "max_concurrent_tasks": task_queue.max_concurrent,
        "active_tasks": task_queue.active_tasks,
        "queued_tasks": task_queue.queued_tasks
    }


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "antigravity_sdk": ANTIGRAVITY_SDK_AVAILABLE,
        "wechat_status": "ready" if wechat_handler.is_configured else "waiting_credentials",
        "queue_active": task_queue.active_tasks,
        "queue_waiting": task_queue.queued_tasks
    }


@app.get("/api/sessions")
async def list_sessions():
    """管理接口：查看当前所有微信用户绑定的会话与工作区信息"""
    sessions = session_manager.list_active_sessions()
    return {
        "total_users": len(sessions),
        "sessions": sessions
    }


@app.post("/api/sessions/reset")
async def reset_user_session(user_id: str):
    """管理接口：重置指定微信用户的会话 ID"""
    new_cid = session_manager.reset_session(user_id)
    ws = workspace_manager.get_user_workspace(user_id, new_cid)
    return {
        "user_id": user_id,
        "new_conversation_id": new_cid,
        "new_workspace": str(ws)
    }


@app.get("/wechat/webhook", response_class=PlainTextResponse)
async def wechat_verify(
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...)
):
    """
    企业微信管理后台保存回调 URL 时的签名验证接口
    """
    try:
        echo = wechat_handler.check_signature(msg_signature, timestamp, nonce, echostr)
        logger.info("企业微信回调 URL 验证成功！")
        return echo
    except Exception as e:
        logger.error(f"企业微信回调 URL 验证失败: {e}")
        raise HTTPException(status_code=403, detail=str(e))


@app.post("/wechat/webhook", response_class=PlainTextResponse)
async def wechat_receive(
    request: Request,
    background_tasks: BackgroundTasks,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...)
):
    """
    企业微信接收用户消息入口（支持文本、文件与控制指令）
    """
    body = await request.body()
    try:
        msg = wechat_handler.decrypt_message(body, msg_signature, timestamp, nonce)
    except Exception as e:
        logger.error(f"解密微信消息失败: {e}")
        raise HTTPException(status_code=400, detail="Decrypt failed")

    user_id = getattr(msg, "source", "unknown")

    # 1. 权限与安全校验
    is_allowed, auth_msg = SecurityManager.verify_user(user_id)
    if not is_allowed:
        reply = wechat_handler.encrypt_reply(auth_msg, msg, nonce, timestamp)
        return reply

    # 2. 处理文件消息 (msg.type == "file")
    if getattr(msg, "type", "") == "file":
        file_name = getattr(msg, "file_name", "upload.txt")
        media_id = getattr(msg, "media_id", "")
        logger.info(f"收到微信用户 [{user_id}] 发送的文件: {file_name} (media_id: {media_id})")

        # 获取该用户独立的 conversation_id 与物理沙盒
        cid = session_manager.get_or_create_session(user_id)
        ws_path = workspace_manager.get_user_workspace(user_id, cid)
        uploads_dir = workspace_manager.get_uploads_dir(user_id, cid)
        dest_path = uploads_dir / workspace_manager.sanitize_name(file_name)

        # 下载微信文件到该用户的隔离沙盒中
        download_ok = wechat_handler.download_media(media_id, str(dest_path))
        if not download_ok:
            return wechat_handler.encrypt_reply("❌ 文件接收失败，请检查网络后重新发送。", msg, nonce, timestamp)

        prompt = (
            f"用户在微信上传了文件【{file_name}】，已保存在隔离工作区：{dest_path}。\n"
            f"请先读取并分析该文件内容（如提取 SKU 列表），然后根据用户店铺准备自动化上架。"
        )

        # 异步加入排队队列调度执行
        background_tasks.add_task(
            task_queue.submit_task,
            agent_runner.execute_task,
            user_id=user_id,
            prompt=prompt,
            conversation_id=cid,
            workspace=str(ws_path)
        )

        return wechat_handler.encrypt_reply(
            f"📁 已安全接收文件【{file_name}】并存放于您的独立沙盒！\n"
            f"智能体已接入处理，执行完毕后将主动推送结果报告...",
            msg,
            nonce,
            timestamp
        )

    # 3. 处理文本消息 (msg.type == "text")
    if getattr(msg, "type", "") == "text":
        user_prompt = msg.content.strip()
        logger.info(f"收到微信用户 [{user_id}] 发送的指令: {user_prompt}")

        # 快捷指令支持
        if user_prompt == "/reset":
            new_cid = session_manager.reset_session(user_id)
            new_ws = workspace_manager.get_user_workspace(user_id, new_cid)
            reply_text = (
                f"🔄 会话重置成功！\n"
                f"- 新会话 ID: `{new_cid}`\n"
                f"- 独立沙盒: `{new_ws.name}`\n"
                f"原对话历史与上下文已归档，您现在拥有全新的独立上架会话。"
            )
            return wechat_handler.encrypt_reply(reply_text, msg, nonce, timestamp)

        if user_prompt in ("/status", "/identity"):
            cid = session_manager.get_or_create_session(user_id)
            ws = workspace_manager.get_user_workspace(user_id, cid)
            reply_text = (
                f"📊 [当前会话状态]\n"
                f"- 用户 ID: `{user_id}`\n"
                f"- 会话 ID: `{cid}`\n"
                f"- 独立工作区: `{ws}`\n"
                f"- 当前服务并发数: {task_queue.active_tasks}/{task_queue.max_concurrent}\n"
                f"- 排队任务数: {task_queue.queued_tasks}"
            )
            return wechat_handler.encrypt_reply(reply_text, msg, nonce, timestamp)

        if user_prompt == "/clean":
            count = workspace_manager.cleanup_expired_files()
            return wechat_handler.encrypt_reply(f"🧹 已完成历史文件清理，共清理 {count} 个过期附件。", msg, nonce, timestamp)

        if user_prompt in ("/help", "帮助", "?"):
            help_text = (
                f"💡 [Antigravity 微信上架助手指令说明]\n"
                f"1. 直接发文字：自然语言交待任务（如“检查我的店铺状态”、“按6倍上架刚才的文件”）。\n"
                f"2. 发送文件：直接在微信点击“+”发送包含 SKU 的 .txt 文本或表格，智能体将自动读取并准备上架。\n"
                f"3. /reset：重置当前会话，开启全新的独立上架窗口。\n"
                f"4. /status：查看您当前的会话 ID、工作区路径与服务器并发状态。\n"
                f"5. /clean：清理超过 {settings.file_retention_days} 天的过期临时文件。"
            )
            return wechat_handler.encrypt_reply(help_text, msg, nonce, timestamp)

        # 指令审计校验
        is_valid_prompt, audit_msg = SecurityManager.audit_prompt(user_prompt)
        if not is_valid_prompt:
            reply = wechat_handler.encrypt_reply(f"⚠️ {audit_msg}", msg, nonce, timestamp)
            return reply

        # 获取该用户独立的 conversation_id 与沙盒目录
        cid = session_manager.get_or_create_session(user_id)
        ws_path = workspace_manager.get_user_workspace(user_id, cid)

        # 异步提交给任务队列
        background_tasks.add_task(
            task_queue.submit_task,
            agent_runner.execute_task,
            user_id=user_id,
            prompt=user_prompt,
            conversation_id=cid,
            workspace=str(ws_path)
        )

        # 5秒内向微信返回即时收单确认
        reply_xml = wechat_handler.encrypt_reply(
            "收到指令！您的专属 Antigravity Agent 已接入任务并在后台调度中，请稍候...",
            msg,
            nonce,
            timestamp
        )
        return reply_xml

    # 其他非文本/非文件消息
    return "success"


@app.post("/api/dispatch")
async def manual_dispatch(payload: DispatchRequest, background_tasks: BackgroundTasks):
    """
    本地调试与外部调用接口：支持携带或自动生成 conversation_id 与 workspace
    """
    cid = payload.conversation_id or session_manager.get_or_create_session(payload.user_id)
    ws_path = payload.workspace or str(workspace_manager.get_user_workspace(payload.user_id, cid))

    background_tasks.add_task(
        task_queue.submit_task,
        agent_runner.execute_task,
        user_id=payload.user_id,
        prompt=payload.prompt,
        conversation_id=cid,
        workspace=ws_path
    )
    return {
        "status": "dispatched",
        "user_id": payload.user_id,
        "conversation_id": cid,
        "prompt": payload.prompt,
        "workspace": ws_path
    }
