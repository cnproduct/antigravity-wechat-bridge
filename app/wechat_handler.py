import logging
from typing import Optional
from app.config import settings

logger = logging.getLogger("antigravity.wechat")

# 尝试导入 wechatpy，具备优雅降级能力
try:
    from wechatpy.enterprise.crypto import WeChatCrypto
    from wechatpy.enterprise import parse_message, create_reply
    from wechatpy.enterprise.client import WeChatClient
    from wechatpy.exceptions import WeChatClientException
    WECHAT_AVAILABLE = True
except ImportError:
    WECHAT_AVAILABLE = False
    logger.warning("未检测到 wechatpy 库，请运行 pip install wechatpy cryptography 安装。")


class WeChatHandler:
    """
    企业微信加解密、被动回复与主动消息推送封装
    """

    def __init__(self):
        self._crypto = None
        self._client = None
        self._init_clients()

    def _init_clients(self):
        if not WECHAT_AVAILABLE:
            return

        if settings.wechat_token and settings.wechat_encoding_aes_key and settings.wechat_corp_id:
            try:
                self._crypto = WeChatCrypto(
                    settings.wechat_token,
                    settings.wechat_encoding_aes_key,
                    settings.wechat_corp_id
                )
            except Exception as e:
                logger.error(f"初始化 WeChatCrypto 失败: {e}")

        if settings.wechat_corp_id and settings.wechat_corp_secret:
            try:
                self._client = WeChatClient(
                    settings.wechat_corp_id,
                    settings.wechat_corp_secret
                )
            except Exception as e:
                logger.error(f"初始化 WeChatClient 失败: {e}")

    @property
    def is_configured(self) -> bool:
        return bool(self._crypto and self._client)

    def check_signature(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        """
        用于企业微信后台保存 URL 时的首次签名校验
        """
        if not self._crypto:
            raise RuntimeError("WeChatCrypto 未正确初始化，请检查 .env 中的 TOKEN/AES_KEY/CORP_ID")
        return self._crypto.check_signature(msg_signature, timestamp, nonce, echostr)

    def decrypt_message(self, raw_xml: bytes, msg_signature: str, timestamp: str, nonce: str):
        """
        解密企业微信发送过来的 XML 数据
        """
        if not self._crypto:
            raise RuntimeError("WeChatCrypto 未初始化")
        decrypted_xml = self._crypto.decrypt_message(raw_xml, msg_signature, timestamp, nonce)
        return parse_message(decrypted_xml)

    def encrypt_reply(self, reply_content: str, source_msg, nonce: str, timestamp: str) -> str:
        """
        生成被动回复并加密成 XML（在5秒超时内直接返回）
        """
        if not self._crypto:
            return reply_content
        reply = create_reply(reply_content, source_msg)
        return self._crypto.encrypt_message(reply.render(), nonce, timestamp)

    def send_proactive_text(self, user_id: str, content: str) -> bool:
        """
        调用企业微信应用接口，主动向用户推送文本消息
        """
        if not self._client:
            logger.warning(f"[Mock Mode] 模拟向微信用户 {user_id} 发送消息: \n{content}")
            return False

        try:
            # 微信单条消息长度限制约为 2048 字符，超长进行安全分段
            max_len = 1900
            chunks = [content[i:i + max_len] for i in range(0, len(content), max_len)]
            for chunk in chunks:
                self._client.message.send_text(
                    agent_id=settings.wechat_agent_id,
                    user_ids=user_id,
                    content=chunk
                )
            return True
        except Exception as e:
            logger.error(f"企业微信主动推送文本失败: {e}")
            return False

    def send_proactive_markdown(self, user_id: str, markdown_content: str) -> bool:
        """
        调用企业微信应用接口，主动推送带排版的 Markdown 消息卡片
        """
        if not self._client:
            logger.warning(f"[Mock Mode] 模拟向微信用户 {user_id} 推送 Markdown: \n{markdown_content}")
            return False

        try:
            self._client.message.send_markdown(
                agent_id=settings.wechat_agent_id,
                user_ids=user_id,
                content=markdown_content[:2000]
            )
            return True
        except Exception as e:
            logger.warning(f"Markdown 推送失败，降级为普通文本: {e}")
            return self.send_proactive_text(user_id, markdown_content)

    def download_media(self, media_id: str, dest_path: str) -> bool:
        """
        根据微信 media_id 下载用户发送的临时文件并保存到本地指定路径
        """
        from pathlib import Path
        if not self._client:
            logger.warning(f"[Mock Mode] 模拟下载微信临时素材 {media_id} 到 {dest_path}")
            p = Path(dest_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("SKU_SAMPLE_1001\nSKU_SAMPLE_1002\n", encoding="utf-8")
            return True

        try:
            res = self._client.media.download(media_id)
            p = Path(dest_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            if hasattr(res, "content"):
                p.write_bytes(res.content)
            elif hasattr(res, "iter_content"):
                with open(p, "wb") as f:
                    for chunk in res.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            elif isinstance(res, (bytes, bytearray)):
                p.write_bytes(res)
            else:
                p.write_bytes(res.read() if hasattr(res, "read") else bytes(res))
            logger.info(f"成功下载微信素材 {media_id} 到 {dest_path}")
            return True
        except Exception as e:
            logger.error(f"下载微信媒体文件失败 (media_id={media_id}): {e}")
            return False


wechat_handler = WeChatHandler()
