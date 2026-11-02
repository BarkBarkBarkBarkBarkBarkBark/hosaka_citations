// Server-side reader for the shared hosaka_citations schema.
// Uses a workspace member session, never a service-role key.
// The rotated refresh token is kept in the regional runtime cache so this
// machine does not have to stay online for the wall to render.

import { getCache } from "@vercel/functions";
import fs from "node:fs/promises";
import path from "node:path";

const SCHEMA = "hosaka_citations";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

let memory = null;
let refreshing = null;

function sessionKey() {
  const version = process.env.SUPABASE_SESSION_VERSION || "v1";
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(version)) throw new Error("Reader session version is invalid.");
  return `citations-gate:wall-session:${settings().workspace}:${version}`;
}

function settings() {
  const url = (process.env.SUPABASE_URL ?? "").replace(/\/+$/, "");
  const key = process.env.SUPABASE_PUBLISHABLE_KEY ?? "";
  const workspace = process.env.SUPABASE_WORKSPACE_ID ?? "";
  if (!url.startsWith("https://") || !key || !UUID.test(workspace)) {
    throw new Error("Supabase wall is not configured.");
  }
  return { url, key, workspace };
}

function claims(token) {
  try {
    const body = token.split(".")[1];
    const json = JSON.parse(Buffer.from(body, "base64url").toString());
    if (!json || json.role !== "authenticated") throw new Error("bad role");
    return json;
  } catch {
    throw new Error("Refusing a non-user Supabase token.");
  }
}

async function readSession() {
  if (!process.env.VERCEL && process.env.SUPABASE_AUTH_FILE) {
    try {
      const local = JSON.parse(await fs.readFile(process.env.SUPABASE_AUTH_FILE, "utf8"));
      if (local?.refresh_token) return local;
    } catch {
      // Fall back to runtime/in-memory cache or env token.
    }
  }
  try {
    const cached = await getCache().get(sessionKey());
    if (cached?.refresh_token) return cached;
  } catch (error) {
    if (process.env.VERCEL) throw new Error("Reader session storage is unavailable. Ask the organizer to recover the reader session.");
    console.info("Local wall uses an in-memory reader session; Vercel runtime cache is unavailable.");
  }
  if (memory?.refresh_token) return memory;
  return {
    access_token: "",
    refresh_token: process.env.SUPABASE_REFRESH_TOKEN ?? "",
    expires_at: 0,
  };
}

async function writeSession(session) {
  memory = session;
  if (!process.env.VERCEL && process.env.SUPABASE_AUTH_FILE) {
    try {
      const target = path.resolve(process.env.SUPABASE_AUTH_FILE);
      const tmp = `${target}.tmp-${process.pid}`;
      await fs.writeFile(tmp, JSON.stringify(session), { mode: 0o600 });
      await fs.rename(tmp, target);
    } catch {
      // Keep in-memory session even if file write fails.
    }
  }
  try {
    await getCache().set(sessionKey(), session, { ttl: 60 * 60 * 24 * 30, name: "citations-wall-session" });
  } catch (error) {
    if (process.env.VERCEL) throw new Error("Reader session could not be persisted. Ask the organizer to recover the reader session.");
    console.info("Local reader session is retained in memory only.");
  }
}

async function refresh(refreshToken) {
  const { url, key } = settings();
  const response = await fetch(`${url}/auth/v1/token?grant_type=refresh_token`, {
    method: "POST",
    headers: { apikey: key, "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Reader sign-in needs organizer recovery (HTTP ${response.status}).`);
  const body = await response.json();
  if (typeof body.access_token !== "string" || typeof body.refresh_token !== "string") {
    throw new Error("Supabase session refresh returned no user token.");
  }
  claims(body.access_token);
  return {
    access_token: body.access_token,
    refresh_token: body.refresh_token,
    expires_at: Math.floor(Date.now() / 1000) + Number(body.expires_in ?? 3600) - 30,
  };
}

async function passwordLogin() {
  const email = (process.env.SUPABASE_READER_EMAIL ?? "").trim();
  const password = (process.env.SUPABASE_READER_PASSWORD ?? "").replace(/[\r\n]+$/, "");
  if (!email || !password) return null;
  const { url, key } = settings();
  const response = await fetch(`${url}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { apikey: key, "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Reader password sign-in failed (HTTP ${response.status}).`);
  const body = await response.json();
  if (typeof body.access_token !== "string" || typeof body.refresh_token !== "string") {
    throw new Error("Supabase password sign-in returned no user token.");
  }
  claims(body.access_token);
  return {
    access_token: body.access_token,
    refresh_token: body.refresh_token,
    expires_at: Math.floor(Date.now() / 1000) + Number(body.expires_in ?? 3600) - 30,
  };
}

async function accessToken() {
  const session = await readSession();
  if (session.access_token && session.expires_at > Date.now() / 1000 + 60) {
    claims(session.access_token);
    return session.access_token;
  }
  if (session.refresh_token) {
    try {
      const next = await refresh(session.refresh_token);
      await writeSession(next);
      return next.access_token;
    } catch (err) {
      const fallback = await passwordLogin();
      if (fallback) {
        await writeSession(fallback);
        return fallback.access_token;
      }
      throw err;
    }
  }
  const fallback = await passwordLogin();
  if (fallback) {
    await writeSession(fallback);
    return fallback.access_token;
  }
  throw new Error("Supabase reader session is not configured.");
}

async function token() {
  if (!refreshing) {
    refreshing = accessToken().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

async function rest(table, query, { countOnly = false } = {}) {
  const { url, key, workspace } = settings();
  const response = await fetch(`${url}/rest/v1/${table}?${query}&workspace_id=eq.${workspace}`, {
    method: countOnly ? "HEAD" : "GET",
    headers: {
      apikey: key,
      Authorization: `Bearer ${await token()}`,
      "Accept-Profile": SCHEMA,
      ...(countOnly ? { Prefer: "count=exact" } : {}),
    },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Supabase read failed (HTTP ${response.status}).`);
  if (countOnly) {
    const total = response.headers.get("content-range")?.split("/")[1];
    if (!total || !/^\d+$/.test(total)) throw new Error("Supabase returned no exact citation count.");
    return Number(total);
  }
  const rows = await response.json();
  if (!Array.isArray(rows)) throw new Error("Supabase returned an unexpected payload.");
  return rows;
}

export async function loadWall({ offset = 0, limit = 50 } = {}) {
  if (!Number.isInteger(offset) || offset < 0 || !Number.isInteger(limit) || limit < 1 || limit > 100) {
    throw new Error("Invalid wall page.");
  }
  const now = Date.now();
  const [citations, open, resolved, voided, overdue] = await Promise.all([
    rest(
      "hc_citations",
      `deleted_at=is.null&select=id,citation_number,person_id,violation_type_id,issued_at,description,status,due_at,issued_by,first_offense,resolved_at,resolved_by,resolution_notes&order=issued_at.desc,id.desc&offset=${offset}&limit=${limit}`,
    ),
    rest("hc_citations", "deleted_at=is.null&status=eq.open&select=id", { countOnly: true }),
    rest("hc_citations", "deleted_at=is.null&status=eq.resolved&select=id", { countOnly: true }),
    rest("hc_citations", "deleted_at=is.null&status=eq.void&select=id", { countOnly: true }),
    rest("hc_citations", `deleted_at=is.null&status=eq.open&due_at=lt.${encodeURIComponent(new Date(now).toISOString())}&select=id`, { countOnly: true }),
  ]);
  const total = open + resolved + voided;
  const citationIds = citations.map((row) => row.id);
  const personIds = [...new Set(citations.map((row) => row.person_id))];
  const violationIds = [...new Set(citations.map((row) => row.violation_type_id))];
  if ([...citationIds, ...personIds, ...violationIds].some((id) => !UUID.test(id))) throw new Error("Supabase returned an invalid relation ID.");
  const [persons, violations, remedies] = citations.length ? await Promise.all([
    rest("hc_persons", `deleted_at=is.null&select=id,display_name&id=in.(${personIds.join(",")})`),
    rest("hc_violation_types", `deleted_at=is.null&select=id,title&id=in.(${violationIds.join(",")})`),
    rest("hc_remedies", `deleted_at=is.null&select=citation_id,description,required,completed,completed_at,completed_by,completion_notes,position&citation_id=in.(${citationIds.join(",")})&order=position.asc`),
  ]) : [[], [], []];
  const names = new Map(persons.map((row) => [row.id, row.display_name]));
  const titles = new Map(violations.map((row) => [row.id, row.title]));
  const remediesByCitation = new Map();
  for (const rem of remedies) {
    const list = remediesByCitation.get(rem.citation_id) ?? [];
    list.push({
      description: rem.description,
      required: Boolean(rem.required),
      completed: Boolean(rem.completed),
      completed_at: rem.completed_at ?? null,
      completed_by: rem.completed_by ?? null,
      completion_notes: rem.completion_notes ?? null,
    });
    remediesByCitation.set(rem.citation_id, list);
  }
  const items = citations.map((row, idx) => {
    const due = row.due_at ? Date.parse(row.due_at) : null;
    const number = Math.max(1, total - (offset + idx));
    return {
      id: row.id,
      display_code: `#${number}`,
      citation_number: row.citation_number,
      offender: names.get(row.person_id) ?? "Unknown",
      violation: titles.get(row.violation_type_id) ?? "Unknown",
      status: row.status,
      issued_at: row.issued_at,
      due_at: row.due_at,
      issued_by: row.issued_by,
      resolved_at: row.resolved_at ?? null,
      resolved_by: row.resolved_by ?? null,
      resolution_notes: row.resolution_notes ?? null,
      description: row.description ?? "",
      first_offense: row.first_offense,
      overdue: row.status === "open" && due !== null && due < now,
      remedies: remediesByCitation.get(row.id) ?? [],
    };
  });
  return {
    checked_at: new Date().toISOString(),
    counts: { open, resolved, void: voided, overdue },
    total,
    offset,
    limit,
    items,
  };
}
