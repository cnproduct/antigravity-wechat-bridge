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


wechat_handler = WeChatHandler()
