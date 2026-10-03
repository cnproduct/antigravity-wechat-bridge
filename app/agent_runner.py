import os
import logging
import asyncio
from typing import Optional
from app.config import settings
from app.wechat_handler import wechat_handler

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
    负责调度本地 Antigravity Agent 执行工作区任务，并向微信异步汇报进展与结果
    """

    @staticmethod
    def _build_system_instruction(workspace: str) -> str:
        return (
            f"你是运行在用户本地 Mac/服务器上的 Antigravity 远程研发与项目执行助理。\n"
            f"【当前默认工作区】: {workspace}\n"
            f"【工作原则】:\n"
            f"1. 你有权在授权工作区内使用工具进行代码检索、文件读写、指令调试和项目推进。\n"
            f"2. 最终回复将通过微信推送到用户的手机端，请保持清晰、结构化：\n"
            f"   - 明确指出完成了哪些关键改动或分析结论\n"
            f"   - 涉及到的文件路径使用简明代码块标出\n"
            f"   - 避免无意义的冗长刷屏，突出重点与下一步建议。"
        )

    async def execute_task(self, user_id: str, prompt: str, workspace: Optional[str] = None):
        """
        异步执行 Agent 任务
        """
        target_workspace = workspace or settings.agent_default_workspace
        logger.info(f"开始为用户 [{user_id}] 执行任务: {prompt[:80]}... 工作区: {target_workspace}")

        # 1. 向微信推送任务启动进度
        wechat_handler.send_proactive_text(
            user_id=user_id,
            content=(
                f"🤖 [Antigravity Agent 启动]\n"
                f"📂 工作区: {os.path.basename(target_workspace)}\n"
                f"📝 任务: {prompt}\n"
                f"⏳ 正在分析项目上下文并执行中..."
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
                    system_prompt = self._build_system_instruction(target_workspace)
                    
                    # 组合工作区指令
                    full_prompt = f"【当前工作区：{target_workspace}】\n任务需求：\n{prompt}"
                    
                    logger.info("Agent 正在生成思考与执行步骤...")
                    response = await agent.chat(full_prompt)
                    final_text = await response.text()
            else:
                # 模拟测试模式
                await asyncio.sleep(2)
                final_text = (
                    f"【模拟测试模式】已接收您的任务：{prompt}\n"
                    f"目标工作区为：{target_workspace}\n"
                    f"提示：在实际运行环境中安装并加载 google-antigravity SDK 即可直接执行真实工作区任务。"
                )

            # 3. 结果汇总推回微信
            summary_markdown = (
                f"### ✅ Antigravity 任务执行完成\n"
                f"**任务**: {prompt[:50]}...\n\n"
                f"{final_text}"
            )
            
            # 优先尝试 Markdown 卡片推送，若格式不支持则降级为纯文本
            wechat_handler.send_proactive_markdown(user_id=user_id, markdown_content=summary_markdown)
            logger.info(f"用户 [{user_id}] 的任务执行完成并已推送微信。")

        except Exception as e:
            logger.error(f"Agent 任务执行失败: {e}", exc_info=True)
            wechat_handler.send_proactive_text(
                user_id=user_id,
                content=f"❌ Antigravity Agent 执行出错：\n{str(e)}"
            )


agent_runner = AgentRunner()
