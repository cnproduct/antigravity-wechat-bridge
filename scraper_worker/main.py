import asyncio
import time
from fastapi import FastAPI, HTTPException
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

app = FastAPI()

# 单台抓取节点只允许同时开启有限个抓取任务，防止内存溢出或页面串改
_lock = asyncio.Lock()

async def fetch_dom(session: ClientSession, url: str) -> str:
    try:
        await session.call_tool("navigate", arguments={"url": url})
        await asyncio.sleep(8)
        result = await session.call_tool("evaluate", arguments={"script": "document.documentElement.outerHTML"})
        for content in result.content:
            if content.type == 'text':
                return content.text
        return ""
    except Exception as e:
        if "Timeout" in str(e):
            result = await session.call_tool("evaluate", arguments={"script": "document.documentElement.outerHTML"})
            for content in result.content:
                if content.type == 'text':
                    return content.text
        raise e

@app.post("/api/scrape/{sku}")
async def scrape_sku(sku: str):
    if _lock.locked():
        raise HTTPException(status_code=503, detail="Worker is currently busy with another scraping task. Please try another node.")
    
    async with _lock:
        main_url = f"https://www.ozon.ru/product/{sku}/"
        features_url = f"https://www.ozon.ru/product/{sku}/features/"
        
        server_params = StdioServerParameters(
            command="npx",
            args=["-y", "chrome-devtools-mcp@latest", "--slim"]
        )
        
        try:
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    
                    t1 = int(time.time())
                    main_html = await fetch_dom(session, main_url)
                    
                    t2 = int(time.time())
                    features_html = await fetch_dom(session, features_url)
                    
                    if "cloudflare-bypassing" in main_html.lower() or "captcha" in main_html.lower():
                        raise HTTPException(status_code=403, detail="Detected Captcha/Cloudflare")
                    
                    return {
                        "sku": sku,
                        "pages": [
                            {"url": main_url, "captured_at": t1, "html": main_html},
                            {"url": features_url, "captured_at": t2, "html": features_html}
                        ]
                    }
        except HTTPException as he:
            raise he
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
