import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.session_manager import session_manager
from app.workspace_manager import workspace_manager

client = TestClient(app)


class MockTextMessage:
    type = "text"
    source = "user_test_wx_001"
    content = "请帮我检查当前店铺上架状态"


class MockFileMessage:
    type = "file"
    source = "user_test_wx_002"
    file_name = "test_skus_2026.txt"
    media_id = "mock_media_999888"


class MockResetMessage:
    type = "text"
    source = "user_test_wx_003"
    content = "/reset"


class MockStatusMessage:
    type = "text"
    source = "user_test_wx_004"
    content = "/status"


def test_wechat_webhook_text_instruction():
    with patch("app.wechat_handler.wechat_handler.decrypt_message", return_value=MockTextMessage()):
        with patch("app.wechat_handler.wechat_handler.encrypt_reply", return_value="<xml>mock_reply</xml>") as mock_reply:
            res = client.post(
                "/wechat/webhook?msg_signature=sig&timestamp=123&nonce=456",
                content=b"<xml>dummy</xml>"
            )
            assert res.status_code == 200
            assert res.text == "<xml>mock_reply</xml>"
            # 确认回复文本包含接收确认
            call_args = mock_reply.call_args[0]
            assert "已接入任务并在后台调度中" in call_args[0]


def test_wechat_webhook_file_upload_isolation():
    user_id = MockFileMessage.source
    with patch("app.wechat_handler.wechat_handler.decrypt_message", return_value=MockFileMessage()):
        with patch("app.wechat_handler.wechat_handler.download_media", return_value=True) as mock_download:
            with patch("app.wechat_handler.wechat_handler.encrypt_reply", return_value="<xml>file_reply</xml>") as mock_reply:
                res = client.post(
                    "/wechat/webhook?msg_signature=sig&timestamp=123&nonce=456",
                    content=b"<xml>dummy</xml>"
                )
                assert res.status_code == 200
                assert res.text == "<xml>file_reply</xml>"

                # 确认下载接口被正确调用且保存到该用户的独立沙盒目录
                assert mock_download.called
                media_id, dest_path = mock_download.call_args[0]
                assert media_id == MockFileMessage.media_id
                assert user_id in dest_path
                assert MockFileMessage.file_name in dest_path

                # 确认会话 ID 已经初始化
                cid = session_manager.get_session_info(user_id)["conversation_id"]
                assert cid in dest_path


def test_wechat_webhook_reset_command():
    user_id = MockResetMessage.source
    cid_before = session_manager.get_or_create_session(user_id)

    with patch("app.wechat_handler.wechat_handler.decrypt_message", return_value=MockResetMessage()):
        with patch("app.wechat_handler.wechat_handler.encrypt_reply", return_value="<xml>reset_reply</xml>") as mock_reply:
            res = client.post(
                "/wechat/webhook?msg_signature=sig&timestamp=123&nonce=456",
                content=b"<xml>dummy</xml>"
            )
            assert res.status_code == 200
            # 确认生成了全新会话 ID
            cid_after = session_manager.get_session_info(user_id)["conversation_id"]
            assert cid_after != cid_before
            call_args = mock_reply.call_args[0]
            assert "会话重置成功" in call_args[0]
            assert cid_after in call_args[0]


def test_wechat_webhook_status_command():
    user_id = MockStatusMessage.source
    with patch("app.wechat_handler.wechat_handler.decrypt_message", return_value=MockStatusMessage()):
        with patch("app.wechat_handler.wechat_handler.encrypt_reply", return_value="<xml>status_reply</xml>") as mock_reply:
            res = client.post(
                "/wechat/webhook?msg_signature=sig&timestamp=123&nonce=456",
                content=b"<xml>dummy</xml>"
            )
            assert res.status_code == 200
            call_args = mock_reply.call_args[0]
            assert "当前会话状态" in call_args[0]
            assert user_id in call_args[0]
