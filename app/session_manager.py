import os
import json
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Any
from app.config import settings

logger = logging.getLogger("antigravity.session")


class SessionManager:
    """
    多租户会话管理器：
    负责维护 微信用户ID (UserID) 与 Antigravity 对话会话ID (conversation_id) 的 1:1 强绑定，
    确保不同微信用户的对话上下文、状态、工作区完全隔离。
    """

    def __init__(self, store_path: Optional[str] = None):
        self.store_path = Path(store_path or settings.session_store_path).expanduser()
        self._ensure_store_dir()

    def _ensure_store_dir(self):
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"创建会话存储目录失败: {e}")

    def _load_sessions(self) -> Dict[str, Any]:
        if not self.store_path.exists():
            return {}
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取会话存储文件失败 ({self.store_path}): {e}")
            return {}

    def _save_sessions(self, sessions: Dict[str, Any]):
        self._ensure_store_dir()
        temp_path = self.store_path.with_suffix(".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(sessions, f, ensure_ascii=False, indent=2)
            temp_path.replace(self.store_path)
        except Exception as e:
            logger.error(f"保存会话存储文件失败 ({self.store_path}): {e}")
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def get_or_create_session(self, user_id: str) -> str:
        """
        获取指定微信用户的活跃会话 ID；若不存在则自动生成全新独立 UUID
        """
        sessions = self._load_sessions()
        now_str = datetime.now(timezone.utc).isoformat()

        if user_id in sessions and sessions[user_id].get("conversation_id"):
            session = sessions[user_id]
            session["last_active_at"] = now_str
            self._save_sessions(sessions)
            return session["conversation_id"]

        # 生成新的 conversation_id
        new_cid = str(uuid.uuid4())
        sessions[user_id] = {
            "user_id": user_id,
            "conversation_id": new_cid,
            "created_at": now_str,
            "last_active_at": now_str,
            "history_conversations": []
        }
        self._save_sessions(sessions)
        logger.info(f"为微信用户 [{user_id}] 初始化新会话: {new_cid}")
        return new_cid

    def reset_session(self, user_id: str) -> str:
        """
        重置并开辟全新独立的会话，原会话沉淀至历史记录
        """
        sessions = self._load_sessions()
        now_str = datetime.now(timezone.utc).isoformat()
        old_cid = sessions.get(user_id, {}).get("conversation_id")
        new_cid = str(uuid.uuid4())

        history = sessions.get(user_id, {}).get("history_conversations", [])
        if old_cid:
            history.append({
                "conversation_id": old_cid,
                "archived_at": now_str
            })

        sessions[user_id] = {
            "user_id": user_id,
            "conversation_id": new_cid,
            "created_at": now_str,
            "last_active_at": now_str,
            "history_conversations": history
        }
        self._save_sessions(sessions)
        logger.info(f"微信用户 [{user_id}] 会话已重置: 旧会话={old_cid} -> 新会话={new_cid}")
        return new_cid

    def get_session_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取指定用户的会话信息"""
        sessions = self._load_sessions()
        return sessions.get(user_id)

    def list_active_sessions(self) -> Dict[str, Any]:
        """列出全部活跃用户的会话信息"""
        return self._load_sessions()


session_manager = SessionManager()
