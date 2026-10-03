#!/usr/bin/env bash
# ================================================================
# 内网穿透配置指引脚本
# 将本地 8000 端口安全暴露给公网，以便微信/企业微信服务器回调
# ================================================================

PORT=8000

echo "请选择您偏好的内网穿透工具："
echo "1) Cloudflare Tunnel (免费、极其稳定、推荐)"
echo "2) cpolar (国内访问快、支持临时域名)"
echo "3) Ngrok"

echo ""
echo "常用启动命令说明："
echo "--------------------------------------------------"
echo "【方案 1: Cloudflare Tunnel (推荐)】"
echo "  brew install cloudflared"
echo "  cloudflared tunnel --url http://localhost:${PORT}"
echo "  -> 将输出的 https://xxx.trycloudflare.com/wechat/webhook 填入微信后台"
echo ""
echo "【方案 2: cpolar】"
echo "  brew install cpolar/cpolar/cpolar"
echo "  cpolar http ${PORT}"
echo "  -> 将生成的 https://xxx.cpolar.cn/wechat/webhook 填入微信后台"
echo "--------------------------------------------------"
