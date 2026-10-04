# Antigravity WeChat Edge Gateway (Cloudflare Worker)

企业微信与 Antigravity 自动化上架体系的边缘安全网关。

## 架构优势

1. **瞬时响应 (50ms)**：
   - 企业微信要求 Webhook 回调必须在 5 秒内响应，否则会重试 3 次并报警。
   - 本网关收到企业微信消息后，在 50ms 内完成 AES 解密并向微信返回 HTTP 200 成功确认，彻底避免超时重试。
2. **边缘验签与解密**：
   - 在 Cloudflare 全球边缘节点自动校验 `msg_signature` (SHA-1) 与解密微信密文 (`AES-256-CBC + PKCS#7`)。
   - 过滤非法探测请求，确保内网/本地终端仅接收合法合规指令。
3. **国内网络连通性优化**：
   - 支持通过自定义域名（如 `wechat.yourdomain.com`）直接接入，完美绕过微信对 `*.workers.dev` 的连接限制，确保中国大陆节点毫秒级触达。
4. **异步队列转发**：
   - 将已解密或原始 Webhook 负载异步推送到本地或内网终端运行的 `antigravity-wechat-bridge` 进行任务调度。

## 部署配置

### 1. 配置机密环境变量 (Cloudflare Secrets)

```bash
echo "YOUR_WECHAT_CORP_ID" | npx wrangler secret put WECHAT_CORP_ID
echo "YOUR_WECHAT_TOKEN" | npx wrangler secret put WECHAT_TOKEN
echo "YOUR_WECHAT_ENCODING_AES_KEY" | npx wrangler secret put WECHAT_ENCODING_AES_KEY

# 可选：配置消息异步转发目标（如本地 Tunnel 或反向代理地址）
echo "https://your-tunnel-domain.com/wechat/webhook" | npx wrangler secret put FORWARD_URL
```

### 2. 部署到 Cloudflare

```bash
npx wrangler deploy
```

### 3. 绑定企业可信自定义域名

在 Cloudflare 控制台或通过 API 将企业已有主域名（如 `wechat.diytale.com`）作为 Custom Domain 挂载至本 Worker。

### 4. 在企业微信后台配置

在自建应用「API 接收消息」配置页中填入：
- **URL**: `https://wechat.yourdomain.com/wechat/webhook`
- **Token**: 与 `WECHAT_TOKEN` 一致
- **EncodingAESKey**: 与 `WECHAT_ENCODING_AES_KEY` 一致
- 点击保存，秒级通过校验！
