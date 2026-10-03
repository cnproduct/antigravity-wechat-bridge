import pytest
import tempfile
import shutil
import time
from pathlib import Path
from app.workspace_manager import WorkspaceManager


@pytest.fixture
def temp_workspace_mgr():
    tmp_dir = tempfile.mkdtemp()
    mgr = WorkspaceManager(root_dir=tmp_dir)
    yield mgr
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_workspace_isolation_and_structure(temp_workspace_mgr):
    user_a = "user_001"
    cid_a = "cid-aaaa-1111"
    user_b = "user_002"
    cid_b = "cid-bbbb-2222"

    ws_a = temp_workspace_mgr.get_user_workspace(user_a, cid_a)
    ws_b = temp_workspace_mgr.get_user_workspace(user_b, cid_b)

    assert ws_a != ws_b
    assert ws_a.exists()
    assert (ws_a / "uploads").exists()
    assert (ws_a / "artifacts").exists()
    assert ws_b.exists()
    assert (ws_b / "uploads").exists()
    assert (ws_b / "artifacts").exists()


def test_sanitize_name_anti_traversal(temp_workspace_mgr):
    malicious_user = "../../etc/passwd"
    clean = temp_workspace_mgr.sanitize_name(malicious_user)
    assert "/" not in clean
    assert ".." not in clean


def test_save_uploaded_file(temp_workspace_mgr):
    user = "user_uploader"
    cid = "cid_test_upload"
    content = b"SKU123456\nSKU654321\n"
    
    saved_path = temp_workspace_mgr.save_uploaded_file(user, cid, "my_skus.txt", content)
    assert saved_path.exists()
    assert saved_path.read_bytes() == content
    assert saved_path.parent == temp_workspace_mgr.get_uploads_dir(user, cid)


def test_cleanup_expired_files(temp_workspace_mgr):
    user = "user_clean"
    cid = "cid_clean"
    saved = temp_workspace_mgr.save_uploaded_file(user, cid, "old.txt", b"old content")
    
    # 模拟文件为 10 天前修改
    old_time = time.time() - (10 * 86400)
    import os
    os.utime(saved, (old_time, old_time))

    cleaned = temp_workspace_mgr.cleanup_expired_files(retention_days=7)
    assert cleaned == 1
    assert not saved.exists()
