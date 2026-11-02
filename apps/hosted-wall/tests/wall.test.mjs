import assert from "node:assert/strict";
import { test } from "node:test";
import { issueToken, verifyToken } from "../lib/gate.js";

const person = "00000000-0000-4000-8000-000000000001";
const violation = "00000000-0000-4000-8000-000000000002";
const jwt = `e30.${Buffer.from(JSON.stringify({ role: "authenticated" })).toString("base64url")}.test`;

function settings() {
  process.env.SUPABASE_URL = "https://example.supabase.co";
  process.env.SUPABASE_PUBLISHABLE_KEY = "sb_publishable_test";
  process.env.SUPABASE_WORKSPACE_ID = "00000000-0000-4000-8000-000000000003";
  process.env.SUPABASE_REFRESH_TOKEN = "synthetic-only";
  delete process.env.SUPABASE_AUTH_FILE;
  delete process.env.SUPABASE_READER_EMAIL;
  delete process.env.SUPABASE_READER_PASSWORD;
  delete process.env.SUPABASE_SESSION_VERSION;
  delete process.env.VERCEL;
}

test("gate accepts signed tokens and rejects tampering", async () => {
  const token = await issueToken("synthetic-secret");
  assert.equal(await verifyToken("synthetic-secret", token), true);
  assert.equal(await verifyToken("another-secret", token), false);
  assert.equal(await verifyToken("", token), false);
});

test("wall totals cover all 1000 citations while returning a bounded page", async () => {
  settings();
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (input, options) => {
    const url = new URL(input);
    calls.push([url, options]);
    if (url.pathname.startsWith("/auth/")) return Response.json({
      access_token: jwt, refresh_token: "rotated-synthetic", expires_in: 3600,
    });
    assert.equal(options.headers["Accept-Profile"], "hosaka_citations");
    assert.equal(url.searchParams.get("workspace_id"), "eq." + process.env.SUPABASE_WORKSPACE_ID);
    if (options.method === "HEAD") {
      const count = url.searchParams.has("due_at") ? 50 : url.searchParams.get("status") === "eq.open" ? 800 : 100;
      return new Response(null, { headers: { "content-range": `*/${count}` } });
    }
    if (url.pathname.endsWith("/hc_persons")) return Response.json([{ id: person, display_name: "Synthetic Guest" }]);
    if (url.pathname.endsWith("/hc_violation_types")) return Response.json([{ id: violation, title: "Synthetic Violation" }]);
    if (url.pathname.endsWith("/hc_remedies")) return Response.json([{ citation_id: "00000000-0000-4000-8000-000000000004", description: "Dance", required: true, completed: false, position: 0 }]);
    assert.equal(url.searchParams.get("offset"), "950");
    assert.equal(url.searchParams.get("limit"), "50");
    return Response.json([{
      id: "00000000-0000-4000-8000-000000000004",
      person_id: person, violation_type_id: violation, status: "open",
      issued_at: "2026-10-01T00:00:00Z", due_at: null,
    }]);
  };
  try {
    const { loadWall } = await import("../lib/supabase.js?paging");
    const wall = await loadWall({ offset: 950, limit: 50 });
    assert.equal(wall.total, 1000);
    assert.equal(wall.counts.open, 800);
    assert.equal(wall.items.length, 1);
    assert.equal(wall.items[0].offender, "Synthetic Guest");
    assert.equal(wall.items[0].remedies[0].description, "Dance");
    assert.equal(calls.filter(([url]) => url.pathname.startsWith("/auth/")).length, 1);
    assert.ok(calls.find(([url]) => url.pathname.endsWith("/hc_persons"))[0].searchParams.get("id").startsWith("in.("));
  } finally { globalThis.fetch = original; }
});

test("bad pages and missing exact totals fail explicitly", async () => {
  settings();
  const original = globalThis.fetch;
  globalThis.fetch = async (input) => String(input).includes("/auth/")
    ? Response.json({ access_token: jwt, refresh_token: "synthetic", expires_in: 3600 })
    : Response.json([]);
  try {
    const { loadWall } = await import("../lib/supabase.js?bad-total");
    await assert.rejects(loadWall({ limit: 1000 }), /Invalid wall page/);
    await assert.rejects(loadWall(), /no exact citation count/);
  } finally { globalThis.fetch = original; }
});

test("production cache failure never silently falls back to an old environment token", async () => {
  settings();
  process.env.VERCEL = "1";
  const symbol = Symbol.for("@vercel/request-context");
  const previous = globalThis[symbol];
  globalThis[symbol] = { get: () => ({ cache: { get: async () => { throw new Error("Synthetic storage outage"); } } }) };
  try {
    const { loadWall } = await import("../lib/supabase.js?cache-error");
    await assert.rejects(loadWall(), /session storage is unavailable/);
  } finally {
    delete process.env.VERCEL;
    globalThis[symbol] = previous;
  }
});

test("reader email and password automatically recover when refresh token is expired or absent", async () => {
  settings();
  process.env.SUPABASE_SESSION_VERSION = "password-fallback";
  process.env.SUPABASE_REFRESH_TOKEN = "expired-token";
  process.env.SUPABASE_READER_EMAIL = "reader@example.test";
  process.env.SUPABASE_READER_PASSWORD = "synthetic-password";
  const original = globalThis.fetch;
  let passwordCalls = 0;
  globalThis.fetch = async (input, options) => {
    const url = new URL(input);
    if (url.pathname.startsWith("/auth/") && url.searchParams.get("grant_type") === "refresh_token") {
      return new Response("expired", { status: 400 });
    }
    if (url.pathname.startsWith("/auth/") && url.searchParams.get("grant_type") === "password") {
      passwordCalls += 1;
      return Response.json({ access_token: jwt, refresh_token: "fresh-from-password", expires_in: 3600 });
    }
    if (options.method === "HEAD") {
      return new Response(null, { headers: { "content-range": "*/0" } });
    }
    return Response.json([]);
  };
  try {
    const { loadWall } = await import("../lib/supabase.js?password-fallback");
    const wall = await loadWall();
    assert.equal(wall.total, 0);
    assert.equal(passwordCalls, 1);
  } finally {
    globalThis.fetch = original;
    delete process.env.SUPABASE_READER_EMAIL;
    delete process.env.SUPABASE_READER_PASSWORD;
  }
});
