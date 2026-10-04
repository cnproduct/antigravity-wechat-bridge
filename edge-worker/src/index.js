import crypto from "node:crypto";

function decryptWeChatPayload(encryptedBase64, encodingAesKey, expectedCorpId) {
  const aesKey = Buffer.from(encodingAesKey + "=", "base64");
  const iv = aesKey.subarray(0, 16);

  const decipher = crypto.createDecipheriv("aes-256-cbc", aesKey, iv);
  decipher.setAutoPadding(false);

  const encryptedBuf = Buffer.from(encryptedBase64, "base64");
  const decrypted = Buffer.concat([decipher.update(encryptedBuf), decipher.final()]);

  // PKCS#7 unpad
  const padLen = decrypted[decrypted.length - 1];
  if (padLen < 1 || padLen > 32) {
    throw new Error("Invalid PKCS#7 padding length");
  }
  const unpadded = decrypted.subarray(0, decrypted.length - padLen);

  const msgLen = unpadded.readUInt32BE(16);
  const msg = unpadded.subarray(20, 20 + msgLen).toString("utf8");
  const corpId = unpadded.subarray(20 + msgLen).toString("utf8");

  if (expectedCorpId && corpId !== expectedCorpId) {
    throw new Error(`CorpId mismatch: expected ${expectedCorpId}, got ${corpId}`);
  }

  return { msg, corpId };
}

function verifySignature(token, timestamp, nonce, encryptStr, signature) {
  const list = [token, timestamp, nonce, encryptStr].sort();
  const sha1 = crypto.createHash("sha1").update(list.join("")).digest("hex");
  return sha1 === signature;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. Health check
    if (url.pathname === "/" || url.pathname === "/health") {
      return new Response(JSON.stringify({
        service: "Antigravity WeChat Edge Gateway",
        status: "healthy",
        corpId: env.WECHAT_CORP_ID ? `${env.WECHAT_CORP_ID.slice(0, 4)}...` : "not configured",
        timestamp: new Date().toISOString()
      }), {
        headers: { "content-type": "application/json; charset=utf-8" }
      });
    }

    // 2. WeChat Webhook
    if (url.pathname === "/wechat/webhook") {
      const msgSignature = url.searchParams.get("msg_signature");
      const timestamp = url.searchParams.get("timestamp");
      const nonce = url.searchParams.get("nonce");

      // A. GET: URL Verification from WeChat Admin
      if (request.method === "GET") {
        const echostr = url.searchParams.get("echostr");
        if (!echostr || !msgSignature || !timestamp || !nonce) {
          return new Response("Missing parameters", { status: 400 });
        }

        const isSigValid = verifySignature(env.WECHAT_TOKEN, timestamp, nonce, echostr, msgSignature);
        if (!isSigValid) {
          return new Response("Invalid signature", { status: 403 });
        }

        try {
          const { msg } = decryptWeChatPayload(echostr, env.WECHAT_ENCODING_AES_KEY, env.WECHAT_CORP_ID);
          // Return decrypted echostr in plain text
          return new Response(msg, {
            status: 200,
            headers: { "content-type": "text/plain; charset=utf-8" }
          });
        } catch (e) {
          return new Response(`Decrypt error: ${e.message}`, { status: 400 });
        }
      }

      // B. POST: Incoming messages / events from WeChat
      if (request.method === "POST") {
        const bodyText = await request.text();
        const encryptMatch = bodyText.match(/<Encrypt><!\[CDATA\[(.*?)\]\]><\/Encrypt>/s) || bodyText.match(/<Encrypt>(.*?)<\/Encrypt>/s);
        const encryptedData = encryptMatch ? encryptMatch[1] : null;

        if (!encryptedData) {
          return new Response("Missing Encrypt node", { status: 400 });
        }

        const isSigValid = verifySignature(env.WECHAT_TOKEN, timestamp, nonce, encryptedData, msgSignature);
        if (!isSigValid) {
          return new Response("Invalid signature", { status: 403 });
        }

        try {
          const { msg } = decryptWeChatPayload(encryptedData, env.WECHAT_ENCODING_AES_KEY, env.WECHAT_CORP_ID);
          console.log("Decrypted incoming WeChat message:", msg.slice(0, 200));

          // Forward to local bridge or webhook forwarder if configured
          if (env.FORWARD_URL) {
            ctx.waitUntil(
              fetch(env.FORWARD_URL, {
                method: "POST",
                headers: request.headers,
                body: bodyText
              }).catch(err => console.error("Forwarding failed:", err))
            );
          }

          // Acknowledge WeChat immediately with 200 success
          return new Response("success", {
            status: 200,
            headers: { "content-type": "text/plain; charset=utf-8" }
          });
        } catch (e) {
          console.error("Decrypt error on POST:", e);
          return new Response("error", { status: 400 });
        }
      }

      return new Response("Method not allowed", { status: 405 });
    }

    return new Response("Not found", { status: 404 });
  }
};
