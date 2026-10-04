import asyncio
import json
import logging
import time
import sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import subprocess

logger = logging.getLogger("antigravity.scraper")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('[%(levelname)s] %(asctime)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)

# 全局采集锁，防止多个会话同时操作 MCP 浏览器互相覆盖页面
_scraper_lock = asyncio.Lock()

async def scrape_sku_with_mcp(sku: str) -> dict:
    """使用 Antigravity 内置 MCP 浏览器抓取商品和规格页 DOM"""
    server_params = StdioServerParameters(
        command="npx",
        args=["chrome-devtools-mcp@latest", "--slim"]
    )
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # 抓取主页
            main_url = f"https://www.ozon.ru/product/{sku}/"
            logger.info(f"[{sku}] 正在导航至主页: {main_url}")
            await session.call_tool("navigate", {"url": main_url})
            await asyncio.sleep(8)  # 等待加载
            main_ts = int(time.time())
            main_html_res = await session.call_tool("evaluate", {"script": "document.documentElement.outerHTML"})
            main_html = main_html_res.content[0].text
            
            if "Cloudflare" in main_html or "captcha" in main_html.lower():
                raise Exception("Detected Captcha or Cloudflare blocking on main page")
                
            # 抓取规格页
            features_url = f"https://www.ozon.ru/product/{sku}/features/"
            logger.info(f"[{sku}] 正在导航至规格页: {features_url}")
            await session.call_tool("navigate", {"url": features_url})
            await asyncio.sleep(8)
            features_ts = int(time.time())
            features_html_res = await session.call_tool("evaluate", {"script": "document.documentElement.outerHTML"})
            features_html = features_html_res.content[0].text
            
            return {
                "main_url": main_url,
                "main_ts": main_ts,
                "main_html": main_html,
                "features_url": features_url,
                "features_ts": features_ts,
                "features_html": features_html
            }

async def process_awaiting_browser(cid: str, sku: str, start_time: int, wb_cmd: list):
    """处理单个 sku，调用内置浏览器获取 DOM，生成并提交流程"""
    async with _scraper_lock:
        logger.info(f"[{cid}] 开始调用内置浏览器抓取 {sku}")
        try:
            doms = await scrape_sku_with_mcp(sku)
            
            # 严格遵守 SKILL.md 的 capture 文件格式
            capture_data = {
                "conversation_id": cid,
                "sku": sku,
                "pages": [
                    {
                        "url": doms["main_url"],
                        "captured_at": doms["main_ts"],
                        "html": doms["main_html"]
                    },
                    {
                        "url": doms["features_url"],
                        "captured_at": doms["features_ts"],
                        "html": doms["features_html"]
                    }
                ]
            }
            
            capture_file = Path(f"/tmp/ozon_capture_{sku}.json")
            capture_file.write_text(json.dumps(capture_data, ensure_ascii=False), encoding="utf-8")
            
            # 提交结果
            submit_cmd = wb_cmd + [
                "capture", 
                "--source", "browser", 
                "--capture-file", str(capture_file),
                "--sku", sku, 
                "--capture-after", str(start_time),
                "--conversation-id", cid
            ]
            logger.info(f"[{sku}] 回传 DOM 结果...")
            
            res = subprocess.run(submit_cmd, capture_output=True, text=True, timeout=60)
            if res.returncode != 0:
                logger.error(f"[{sku}] 回传结果失败: {res.stderr} \n {res.stdout}")
            else:
                logger.info(f"[{sku}] 采集完成，已回传。")
                
        except Exception as e:
            logger.error(f"[{sku}] 采集发生异常: {str(e)}")
            # 连接报错/验证码告警，在此打印并拦截，不再重试，由云端告警

