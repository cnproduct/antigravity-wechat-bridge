#!/usr/bin/env python3
"""
本地免微信模拟测试脚本：
无需经过真实的微信服务器，直接向本地服务的 /api/dispatch 接口投递测试指令，
验证 Antigravity Agent 是否能正常唤起并处理工作区任务。
"""

import sys
import time
import requests

SERVER_URL = "http://127.0.0.1:8000"


def test_agent_dispatch(prompt: str):
    print(f"📡 检查本地网关服务连通性: {SERVER_URL}/health ...")
    try:
        health = requests.get(f"{SERVER_URL}/health", timeout=3).json()
        print(f"✅ 服务运行正常: {health}")
    except Exception as e:
        print(f"❌ 无法连接到本地服务，请先在另一个终端执行 `bash scripts/run_local.sh`！错误: {e}")
        sys.exit(1)

    print(f"\n📤 正在模拟微信用户投递任务指令:\n「{prompt}」")
    payload = {
        "user_id": "test_developer",
        "prompt": prompt
    }
    
    resp = requests.post(f"{SERVER_URL}/api/dispatch", json=payload, timeout=5)
    if resp.status_code == 200:
        print(f"✅ 任务投递成功，服务端响应: {resp.json()}")
        print("\n⏳ Agent 正在后台异步执行任务，请观察运行服务窗口输出的思考与执行日志！")
    else:
        print(f"❌ 投递失败，状态码: {resp.status_code}, 内容: {resp.text}")


if __name__ == "__main__":
    test_prompt = sys.argv[1] if len(sys.argv) > 1 else "请检查当前工作区，列出项目主要结构并给出项目概览"
    test_agent_dispatch(test_prompt)
