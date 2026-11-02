// Shared between the Edge proxy and the Node route handler: Web Crypto only.

export const COOKIE_NAME = "citations_gate";
export const SESSION_SECONDS = 12 * 60 * 60;

const encoder = new TextEncoder();

function base64url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

async function hmac(secret, message) {
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  return base64url(new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(message))));
}

function timingSafeEqual(left, right) {
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i += 1) diff |= left.charCodeAt(i) ^ right.charCodeAt(i);
  return diff === 0;
}

export function gateSettings() {
  return {
    secret: process.env.GATE_SECRET ?? "",
    accessCode: process.env.GATE_ACCESS_CODE ?? "",
    origin: (process.env.CITATIONS_ORIGIN ?? "").replace(/\/+$/, ""),
    originToken: process.env.CITATIONS_GATE_TOKEN ?? "",
  };
}

export async function issueToken(secret) {
  const expires = Math.floor(Date.now() / 1000) + SESSION_SECONDS;
  const nonce = base64url(crypto.getRandomValues(new Uint8Array(12)));
  const payload = `${expires}.${nonce}`;
  return `${payload}.${await hmac(secret, payload)}`;
}

export async function verifyToken(secret, token) {
  if (!secret || typeof token !== "string") return false;
  const parts = token.split(".");
  if (parts.length !== 3) return false;
  const [expires, nonce, signature] = parts;
  if (!/^\d+$/.test(expires) || Number(expires) < Math.floor(Date.now() / 1000)) return false;
  return timingSafeEqual(await hmac(secret, `${expires}.${nonce}`), signature);
}

export function codeMatches(expected, provided) {
  return Boolean(expected) && typeof provided === "string" && timingSafeEqual(expected, provided.trim());
}

export async function verifyRequest(request) {
  const cookie = (request.headers.get("cookie") || "").split(";").map((part) => part.trim())
    .find((part) => part.startsWith(COOKIE_NAME + "="))?.slice(COOKIE_NAME.length + 1);
  if (await verifyToken(gateSettings().secret, cookie)) return true;
  if (process.env.NODE_ENV === "development" && !process.env.VERCEL) {
    const host = new URL(request.url).hostname;
    if (host === "127.0.0.1" || host === "localhost" || host === "::1") return true;
  }
  return false;
}
