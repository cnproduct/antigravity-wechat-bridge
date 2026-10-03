import os
from typing import List
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """
    桥接服务核心配置类，支持自动读取 .env 文件和系统环境变量
    """
    # 企业微信配置
    wechat_corp_id: str = Field(default="", alias="WECHAT_CORP_ID")
    wechat_agent_id: int = Field(default=0, alias="WECHAT_AGENT_ID")
    wechat_corp_secret: str = Field(default="", alias="WECHAT_CORP_SECRET")
    wechat_token: str = Field(default="", alias="WECHAT_TOKEN")
    wechat_encoding_aes_key: str = Field(default="", alias="WECHAT_ENCODING_AES_KEY")

    # 权限白名单
    allowed_user_ids: str = Field(default="", alias="ALLOWED_USER_IDS")

    # Agent 工作环境配置
    agent_default_workspace: str = Field(
        default=os.path.expanduser("~/"),
        alias="AGENT_DEFAULT_WORKSPACE"
    )
    antigravity_app_data_dir: str = Field(
        default=os.path.expanduser("~/.gemini/antigravity"),
        alias="ANTIGRAVITY_APP_DATA_DIR"
    )
    agent_model: str = Field(default="gemini-3.8-flash", alias="AGENT_MODEL")
    agent_skills_paths: str = Field(default="", alias="AGENT_SKILLS_PATHS")

    # 多租户沙盒与并发管理配置
    tenants_dir: str = Field(
        default=os.path.expanduser("~/.gemini/antigravity/tenants"),
        alias="TENANTS_DIR"
    )
    session_store_path: str = Field(
        default=os.path.expanduser("~/.gemini/antigravity/wechat_sessions.json"),
        alias="SESSION_STORE_PATH"
    )
    max_concurrent_tasks: int = Field(default=5, alias="MAX_CONCURRENT_TASKS")
    file_retention_days: int = Field(default=7, alias="FILE_RETENTION_DAYS")

    # 服务网络配置
    server_host: str = Field(default="0.0.0.0", alias="SERVER_HOST")
    server_port: int = Field(default=8000, alias="SERVER_PORT")
    debug: bool = Field(default=False, alias="DEBUG")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    @property
    def allowed_users_list(self) -> List[str]:
        """解析允许访问的 UserID 列表"""
        if not self.allowed_user_ids:
            return []
        return [uid.strip() for uid in self.allowed_user_ids.split(",") if uid.strip()]

    @property
    def skills_paths_list(self) -> List[str]:
        """解析 Skills 目录路径列表"""
        if not self.agent_skills_paths:
            default_path = os.path.expanduser("~/.gemini/config/skills")
            return [default_path] if os.path.exists(default_path) else []
        paths = [os.path.expanduser(p.strip()) for p in self.agent_skills_paths.split(",") if p.strip()]
        return [p for p in paths if os.path.exists(p)]


settings = Settings()
