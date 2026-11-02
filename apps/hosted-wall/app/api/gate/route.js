import { NextResponse } from "next/server";
import { COOKIE_NAME, SESSION_SECONDS, codeMatches, gateSettings, issueToken } from "../../../lib/gate";

export const runtime = "nodejs";

// Tiny in-memory throttle per isolate; the real limit is the long random access code.
const attempts = new Map();
function throttled(ip) {
  const now = Date.now();
  const entry = attempts.get(ip) ?? { count: 0, reset: now + 300_000 };
  if (now > entry.reset) Object.assign(entry, { count: 0, reset: now + 300_000 });
  entry.count += 1;
  attempts.set(ip, entry);
  return entry.count > 10;
}

export async function POST(request) {
  const settings = gateSettings();
  const ip = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "unknown";
  const form = await request.formData();
  const next = String(form.get("next") ?? "/citations");
  const safeNext = next.startsWith("/") && !next.startsWith("//") ? next : "/citations";
  const back = new URL("/gate", request.url);
  back.searchParams.set("next", safeNext);

  if (!settings.secret || !settings.accessCode) {
    return new NextResponse("Gate is not configured (GATE_SECRET / GATE_ACCESS_CODE).", { status: 503 });
  }
  if (throttled(ip) || !codeMatches(settings.accessCode, form.get("code"))) {
    back.searchParams.set("error", "1");
    return NextResponse.redirect(back, 303);
  }
  const response = NextResponse.redirect(new URL(safeNext, request.url), 303);
  response.cookies.set(COOKIE_NAME, await issueToken(settings.secret), {
    httpOnly: true, secure: isHttps(request), sameSite: "lax", path: "/", maxAge: SESSION_SECONDS,
  });
  return response;
}

export async function DELETE(request) {
  const response = NextResponse.json({ ok: true });
  response.cookies.set(COOKIE_NAME, "", { httpOnly: true, secure: isHttps(request), sameSite: "lax", path: "/", maxAge: 0 });
  return response;
}

// Vercel terminates TLS, so production is always https; plain http only happens in local smoke tests.
function isHttps(request) {
  return (request.headers.get("x-forwarded-proto") ?? new URL(request.url).protocol.replace(":", "")) === "https";
}
