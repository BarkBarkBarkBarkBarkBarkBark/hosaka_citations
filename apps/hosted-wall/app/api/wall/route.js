import { loadWall } from "../../../lib/supabase";
import { verifyRequest } from "../../../lib/gate";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request) {
  try {
    if (!await verifyRequest(request)) {
      return Response.json({ error: "Sign in to view citations." }, { status: 401, headers: { "Cache-Control": "no-store" } });
    }
    const params = new URL(request.url).searchParams;
    const offset = Number(params.get("offset") || 0);
    const limit = Number(params.get("limit") || 50);
    if (!Number.isInteger(offset) || offset < 0 || offset > 10000000 || !Number.isInteger(limit) || limit < 1 || limit > 100) {
      return Response.json({ error: "Invalid wall page." }, { status: 400, headers: { "Cache-Control": "no-store" } });
    }
    return Response.json(await loadWall({ offset, limit }), { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Citations are temporarily unavailable.";
    return Response.json({ error: message }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
