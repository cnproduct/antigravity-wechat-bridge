import logging
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.config import settings
from app.wechat_handler import wechat_handler
from app.agent_runner import agent_runner, ANTIGRAVITY_SDK_AVAILABLE
from app.security import SecurityManager

# 配置日志格式
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("antigravity.main")

app = FastAPI(
    title="Antigravity WeChat Bridge",
    description="轻量级微信 / 企业微信与 Google Antigravity Agent 远程调度桥接网关",
    version="1.0.0"
)


class DispatchRequest(BaseModel):
    user_id: str = "test_user"
    prompt: str
    workspace: str = None


@app.get("/")
async def root():
    return {
        "service": "Antigravity WeChat Bridge",
        "status": "online",
        "antigravity_sdk_loaded": ANTIGRAVITY_SDK_AVAILABLE,
        "wechat_configured": wechat_handler.is_configured,
        "default_workspace": settings.agent_default_workspace
    }


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "antigravity_sdk": ANTIGRAVITY_SDK_AVAILABLE,
        "wechat_status": "ready" if wechat_handler.is_configured else "waiting_credentials"
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
    企业微信接收用户消息入口
    """
    body = await request.body()
    try:
        msg = wechat_handler.decrypt_message(body, msg_signature, timestamp, nonce)
    except Exception as e:
        logger.error(f"解密微信消息失败: {e}")
        raise HTTPException(status_code=400, detail="Decrypt failed")

    # 目前仅处理文本消息
    if msg.type == "text":
        user_id = msg.source
        user_prompt = msg.content.strip()
        logger.info(f"收到微信用户 [{user_id}] 发送的指令: {user_prompt}")

        # 1. 权限与安全校验
        is_allowed, auth_msg = SecurityManager.verify_user(user_id)
        if not is_allowed:
            reply = wechat_handler.encrypt_reply(auth_msg, msg, nonce, timestamp)
            return reply

        is_valid_prompt, audit_msg = SecurityManager.audit_prompt(user_prompt)
        if not is_valid_prompt:
            reply = wechat_handler.encrypt_reply(f"⚠️ {audit_msg}", msg, nonce, timestamp)
            return reply

        # 2. 异步将长任务委托给 Antigravity Agent
        background_tasks.add_task(
            agent_runner.execute_task,
            user_id=user_id,
            prompt=user_prompt
        )

        # 3. 5秒内向微信返回即时收单确认
        reply_xml = wechat_handler.encrypt_reply(
            "收到指令！Antigravity Agent 已接入任务并在后台调度中，请稍候...",
            msg,
            nonce,
            timestamp
        )
        return reply_xml

    # 其他非文本类型消息，直接返回 success
    return "success"


@app.post("/api/dispatch")
async def manual_dispatch(payload: DispatchRequest, background_tasks: BackgroundTasks):
    """
    本地调试接口：无需经过微信，直接通过 HTTP POST 模拟发指令给 Agent
    """
    background_tasks.add_task(
        agent_runner.execute_task,
        user_id=payload.user_id,
        prompt=payload.prompt,
        workspace=payload.workspace
    )
    return {
        "status": "dispatched",
        "user_id": payload.user_id,
        "prompt": payload.prompt,
        "workspace": payload.workspace or settings.agent_default_workspace
    }
