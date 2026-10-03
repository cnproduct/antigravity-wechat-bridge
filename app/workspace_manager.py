import os
import re
import time
import shutil
import logging
from pathlib import Path
from typing import Optional, List
from app.config import settings

logger = logging.getLogger("antigravity.workspace")


class WorkspaceManager:
    """
    租户物理工作区沙盒管理器：
    为每个微信用户及其会话分配独立的物理目录，确保文件上传、日志产物与智能体检索物理隔离，
    彻底消除不同用户/会话间的跨目录覆盖或注意力污染。
    """

    def __init__(self, root_dir: Optional[str] = None):
        self.root_dir = Path(root_dir or settings.tenants_dir).expanduser()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sanitize_name(name: str) -> str:
        """安全清洗文件名或标识，杜绝目录穿越漏洞"""
        clean = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name)
        clean = clean.strip('._')
        return clean or "unknown"

    def get_user_workspace(self, user_id: str, conversation_id: str) -> Path:
        """
        获取用户专属会话工作区目录并确保创建
        目录结构: <tenants_dir>/<user_id>/<conversation_id>/
        """
        safe_user = self.sanitize_name(user_id)
        safe_cid = self.sanitize_name(conversation_id)
        ws_path = self.root_dir / safe_user / safe_cid
        ws_path.mkdir(parents=True, exist_ok=True)
        (ws_path / "uploads").mkdir(exist_ok=True)
        (ws_path / "artifacts").mkdir(exist_ok=True)
        return ws_path

    def get_uploads_dir(self, user_id: str, conversation_id: str) -> Path:
        """获取微信上传文件存放目录"""
        ws = self.get_user_workspace(user_id, conversation_id)
        return ws / "uploads"

    def get_artifacts_dir(self, user_id: str, conversation_id: str) -> Path:
        """获取任务产物报告存放目录"""
        ws = self.get_user_workspace(user_id, conversation_id)
        return ws / "artifacts"

    def save_uploaded_file(self, user_id: str, conversation_id: str, filename: str, content: bytes) -> Path:
        """
        将微信上传的文件保存到隔离的工作区 uploads 目录中
        """
        uploads_dir = self.get_uploads_dir(user_id, conversation_id)
        safe_filename = self.sanitize_name(filename)
        dest_path = uploads_dir / safe_filename

        # 若已存在同名文件，添加时间戳防覆盖
        if dest_path.exists():
            stem = dest_path.stem
            suffix = dest_path.suffix
            dest_path = uploads_dir / f"{stem}_{int(time.time())}{suffix}"

        dest_path.write_bytes(content)
        logger.info(f"微信文件已保存至独立沙盒: {dest_path} (大小: {len(content)} 字节)")
        return dest_path

    def cleanup_expired_files(self, retention_days: Optional[int] = None) -> int:
        """
        清理超过保留天数的历史临时文件，防止磁盘胀满
        """
        days = retention_days if retention_days is not None else settings.file_retention_days
        if days <= 0:
            return 0

        cutoff = time.time() - (days * 86400)
        removed_count = 0

        for user_dir in self.root_dir.iterdir():
            if not user_dir.is_dir():
                continue
            for conv_dir in user_dir.iterdir():
                if not conv_dir.is_dir():
                    continue
                uploads = conv_dir / "uploads"
                if uploads.exists() and uploads.is_dir():
                    for item in uploads.iterdir():
                        if item.is_file() and item.stat().st_mtime < cutoff:
                            try:
                                item.unlink()
                                removed_count += 1
                            except Exception as e:
                                logger.warning(f"清理文件失败: {item}: {e}")

        if removed_count > 0:
            logger.info(f"已清理超过 {days} 天的历史上传文件 {removed_count} 个")
        return removed_count


workspace_manager = WorkspaceManager()
