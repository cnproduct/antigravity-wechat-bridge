#!/usr/bin/env bash
set -e

# 获取脚本所在目录根路径
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "=================================================="
echo "🚀 启动 Antigravity WeChat Bridge 本地服务"
echo "=================================================="

# 检查 .env 是否存在
if [ ! -f ".env" ]; then
    echo "⚠️ 未检测到 .env 文件，正在从 .env.example 复制..."
    cp .env.example .env
    echo "💡 请记得在 .env 中填入真实的企业微信密钥及工作区配置！"
fi

# 检查依赖安装
if ! python3 -c "import fastapi, uvicorn, wechatpy" 2>/dev/null; then
    echo "📦 正在安装基础依赖..."
    pip3 install -r requirements.txt
fi

# 读取配置的端口
PORT=${SERVER_PORT:-8000}
HOST=${SERVER_HOST:-"0.0.0.0"}

echo "📡 服务启动在 http://${HOST}:${PORT}"
echo "🔍 调试文档地址: http://127.0.0.1:${PORT}/docs"
echo "--------------------------------------------------"

python3 -m uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
