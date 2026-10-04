# Antigravity 独立抓取节点 (Scraper Worker)

该模块是为了执行 **分布式抓取扩容 (方案 B)** 设计的轻量级无头浏览器节点。

## 部署说明

在任意廉价/独立 IP 的 VPS 上：

1. 安装 Node.js (提供 npx 环境) 和 Python 3.10+。
2. 安装依赖：`pip install -r requirements.txt`
3. 启动节点：`uvicorn main:app --host 0.0.0.0 --port 5000`

## 主控机配置

在您的飞书桥接主控机环境（`.env`）中，添加抓取节点地址池（多个用逗号隔开）：

```env
SCRAPER_NODES=http://192.168.1.10:5000,http://192.168.1.11:5000
```

主控机一旦检测到此环境变量，便会自动关闭单机本地浏览器抓取，转而向节点池发送 HTTP 请求，实现高并发任务的分流。
