import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.session_manager import session_manager

client = TestClient(app)


def test_root_endpoint():
    res = client.get("/")
    assert res.status_code == 200
    data = res.json()
    assert data["service"] == "Antigravity WeChat Bridge"
    assert data["status"] == "online"
    assert "max_concurrent_tasks" in data


def test_health_endpoint():
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"


def test_sessions_and_reset_endpoints():
    user_id = "test_api_user"
    cid = session_manager.get_or_create_session(user_id)

    # 1. 查询会话列表
    res = client.get("/api/sessions")
    assert res.status_code == 200
    data = res.json()
    assert user_id in data["sessions"]
    assert data["sessions"][user_id]["conversation_id"] == cid

    # 2. 重置会话
    res_reset = client.post(f"/api/sessions/reset?user_id={user_id}")
    assert res_reset.status_code == 200
    new_data = res_reset.json()
    assert new_data["new_conversation_id"] != cid


def test_manual_dispatch():
    payload = {
        "user_id": "test_dispatch_user",
        "prompt": "测试指令：请检查店铺并准备上架"
    }
    res = client.post("/api/dispatch", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "dispatched"
    assert "conversation_id" in data
    assert len(data["conversation_id"]) == 36
