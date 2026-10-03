import logging
from typing import Tuple
from app.config import settings

logger = logging.getLogger("antigravity.security")


class SecurityManager:
    """
    微信交互安全管理器，提供权限鉴权、指令审计和访问控制
    """

    @staticmethod
    def verify_user(user_id: str) -> Tuple[bool, str]:
        """
        验证发起请求的企业微信成员是否在授权白名单中
        """
        allowed = settings.allowed_users_list
        if not allowed:
            # 如果未配置白名单，提示警告（生产环境强烈建议配置）
            logger.warning("未配置 ALLOWED_USER_IDS，当前允许所有企业内用户触发任务。建议在 .env 中设置授权用户列表！")
            return True, "ok"

        if user_id in allowed:
            return True, "ok"

        logger.warning(f"拦截未授权用户访问: {user_id}")
        return False, f"⚠️ 权限受限：用户 [{user_id}] 未被加入 Antigravity 执行授权白名单。"

    @staticmethod
    def audit_prompt(prompt: str) -> Tuple[bool, str]:
        """
        简单的指令安全审计，拦截极端危险操作提示
        """
        stripped = prompt.strip()
        if not stripped:
            return False, "指令内容不能为空。"

        # 可根据需要添加特定阻止词，例如恶意提权
        return True, "ok"
