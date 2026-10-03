import pytest
import tempfile
import shutil
from pathlib import Path
from app.session_manager import SessionManager


@pytest.fixture
def temp_session_mgr():
    tmp_dir = tempfile.mkdtemp()
    store_file = Path(tmp_dir) / "test_sessions.json"
    mgr = SessionManager(store_path=str(store_file))
    yield mgr
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_session_creation_and_isolation(temp_session_mgr):
    user_a = "user_zhangsan"
    user_b = "user_lisi"

    cid_a1 = temp_session_mgr.get_or_create_session(user_a)
    cid_a2 = temp_session_mgr.get_or_create_session(user_a)
    cid_b1 = temp_session_mgr.get_or_create_session(user_b)

    # 同一用户多次调用应复用同一会话 ID
    assert cid_a1 == cid_a2
    # 不同用户会话 ID 必须完全独立隔离
    assert cid_a1 != cid_b1
    assert len(cid_a1) == 36  # UUID4 length
    assert len(cid_b1) == 36


def test_session_reset(temp_session_mgr):
    user = "user_wangwu"
    cid_old = temp_session_mgr.get_or_create_session(user)
    cid_new = temp_session_mgr.reset_session(user)

    assert cid_old != cid_new
    info = temp_session_mgr.get_session_info(user)
    assert info["conversation_id"] == cid_new
    assert len(info["history_conversations"]) == 1
    assert info["history_conversations"][0]["conversation_id"] == cid_old


def test_session_persistence_across_instances(temp_session_mgr):
    user = "user_zhaoliu"
    cid = temp_session_mgr.get_or_create_session(user)

    # 新建另一个 SessionManager 实例指向同一文件
    new_mgr = SessionManager(store_path=str(temp_session_mgr.store_path))
    assert new_mgr.get_or_create_session(user) == cid
