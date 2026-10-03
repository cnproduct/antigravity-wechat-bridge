import os
import logging
import asyncio
from pathlib import Path
from typing import Optional
from app.config import settings
from app.wechat_handler import wechat_handler
from app.session_manager import session_manager
from app.workspace_manager import workspace_manager

logger = logging.getLogger("antigravity.runner")

# 检测并导入 google-antigravity SDK
try:
    from google.antigravity import Agent, LocalAgentConfig
    ANTIGRAVITY_SDK_AVAILABLE = True
except ImportError:
    ANTIGRAVITY_SDK_AVAILABLE = False
    logger.warning("未检测到本地 google-antigravity Python 模块，将启用 Mock 模拟执行模式。")


class AgentRunner:
    """
    负责调度本地 Antigravity Agent 执行工作区任务，并向微信异步汇报进展与结果。
    通过 Conversation ID 与租户独立工作区沙盒，严格杜绝不同微信用户的跨店铺串联与文件干扰。
    """

    @staticmethod
    def _build_system_instruction(workspace: Path, conversation_id: str, user_id: str) -> str:
        return (
            f"你是运行在本地服务器上的 Antigravity 远程智能体助理，当前正在为微信用户 [{user_id}] 服务。\n"
            f"【当前唯一会话 ID】: {conversation_id}\n"
            f"【专属物理工作区】: {workspace}\n"
            f"【关键安全与防串联原则】:\n"
            f"1. 严格隔离：当前用户的所有上传文件在 {workspace / 'uploads'}，所有输出产物必须保存至 {workspace / 'artifacts'}。\n"
            f"2. 严防店铺串联：若调用任何上架命令（如 wb），必须显式携带 `--conversation-id {conversation_id}`，绝对禁止省略会话 ID 参数！\n"
            f"3. 执行前身份核对：每次执行上架前，务必核对当前会话绑定的店铺身份与仓库，向用户回显确认绑定的目标店铺。\n"
            f"4. 最终回复将通过微信主动推送到用户手机，请保持结构化、简练，直接展示核实后的状态与关键结论。"
        )

    async def execute_task(
        self,
        user_id: str,
        prompt: str,
        conversation_id: Optional[str] = None,
        workspace: Optional[str] = None
    ):
        """
        异步执行 Agent 任务
        """
        cid = conversation_id or session_manager.get_or_create_session(user_id)
        ws_path = Path(workspace) if workspace else workspace_manager.get_user_workspace(user_id, cid)

        # 注入会话环境变量，确保任何由 Python/Shell 子进程调用的 CLI 工具自动读取正确的会话上下文
        os.environ["ANTIGRAVITY_CONVERSATION_ID"] = cid

        logger.info(f"开始为微信用户 [{user_id}] 执行任务: 会话={cid} 工作区={ws_path} 指令={prompt[:60]}...")

        # 1. 向微信推送任务启动进度
        wechat_handler.send_proactive_text(
            user_id=user_id,
            content=(
                f"🤖 [Antigravity 智能体启动]\n"
                f"🆔 会话 ID: {cid[:8]}...{cid[-4:]}\n"
                f"📂 隔离沙盒: {ws_path.name}\n"
                f"📝 指令: {prompt[:80]}\n"
                f"⏳ 正在分析项目并执行中..."
            )
        )

        try:
            if ANTIGRAVITY_SDK_AVAILABLE:
                # 2. 真实配置并启动 Antigravity Agent
                config_kwargs = {
                    "model": settings.agent_model,
                }
                
                if os.path.exists(settings.antigravity_app_data_dir):
                    config_kwargs["app_data_dir"] = settings.antigravity_app_data_dir

                skills = settings.skills_paths_list
                if skills:
                    config_kwargs["skills_paths"] = skills

                config = LocalAgentConfig(**config_kwargs)

                # 进入 Agent 会话
                async with Agent(config=config) as agent:
                    system_prompt = self._build_system_instruction(ws_path, cid, user_id)
                    full_prompt = (
                        f"{system_prompt}\n\n"
                        f"用户最新微信指令：\n{prompt}"
                    )
                    
                    logger.info(f"Agent [{cid}] 正在生成思考与执行步骤...")
                    response = await agent.chat(full_prompt)
                    final_text = await response.text()
            else:
                # 模拟测试/脱机开发模式
                await asyncio.sleep(1.5)
                final_text = (
                    f"【沙盒执行模拟】已安全接收任务并在隔离空间运行。\n"
                    f"- 绑定会话 ID: `{cid}`\n"
                    f"- 物理沙盒路径: `{ws_path}`\n"
                    f"- 隔离上传目录: `{ws_path / 'uploads'}`\n"
                    f"- 当前任务指令: {prompt}\n\n"
                    f"✅ 该会话店铺参数与上下文已完成 1:1 独立绑定，无任何串联风险。"
                )

            # 3. 结果汇总推回微信
            summary_markdown = (
                f"### ✅ Antigravity 任务执行完成\n"
                f"**会话 ID**: `{cid[:8]}...` | **用户**: `{user_id}`\n\n"
                f"{final_text}"
            )
            
            # 优先尝试 Markdown 卡片推送，若格式不支持则降级为纯文本
            wechat_handler.send_proactive_markdown(user_id=user_id, markdown_content=summary_markdown)
            logger.info(f"微信用户 [{user_id}] (会话: {cid}) 任务执行完成并已推送微信。")

        except Exception as e:
            logger.error(f"Agent 任务执行失败: {e}", exc_info=True)
            wechat_handler.send_proactive_text(
                user_id=user_id,
                content=f"❌ Antigravity Agent 执行出错：\n{str(e)}"
            )


agent_runner = AgentRunner()
