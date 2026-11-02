# Citations hosted wall

This independently deployable app now belongs to `hosaka_citations` under
`apps/hosted-wall/`. The old gateway/recovery checkouts are preserved; their
Vercel link, project root and production deployment have not been changed.

Identity gate and read-only wall for **citations.hosaka.xyz**. Guests enter the
party access code. The wall then reads `hosaka_citations` in Supabase. The
party laptop is only a writer: local citations sync up, and this site keeps
rendering when that machine is off.

```
browser ──https──▶ citations.hosaka.xyz (this project, Vercel)
                      │ proxy.js: cookie valid? else /gate
                      ▼ /api/wall
                 Supabase hosaka_citations (publishable key + member session)
                      ▲
                 VirgoZone :8069 on the party laptop (sync only)
```

## Files

- `proxy.js` — Edge proxy. Verifies the HMAC cookie; redirects to `/gate`.
- `lib/gate.js` — Web Crypto HMAC tokens (12 h), constant-time compares.
- `lib/supabase.js` — refreshes a member session and reads the wall.
- `app/gate/page.jsx` — access-code form. `app/api/gate/route.js` — POST sets
  the cookie (303 back to `next`), DELETE clears it, 10 attempts / 5 min / IP.
- `app/wall.jsx` — paginated read-only wall. Refreshes without overlapping
  requests and backs off in hidden tabs. Totals cover the whole workspace,
  not just the current page; related people/types are fetched only for that page.

## Environment (Production, all `--sensitive`)

| Name | Purpose |
| --- | --- |
| `GATE_SECRET` | signs the cookie |
| `GATE_ACCESS_CODE` | what guests type |
| `SUPABASE_URL` | project URL |
| `SUPABASE_PUBLISHABLE_KEY` | client key; RLS still requires a member JWT |
| `SUPABASE_WORKSPACE_ID` | shared workspace |
| `SUPABASE_REFRESH_TOKEN` | refresh token from a **second** organizer login, not the laptop session |
| `SUPABASE_SESSION_VERSION` | nonsecret cache namespace version; change during reader recovery |

`CITATIONS_ORIGIN` is unused. Do not point the wall back at a tunnel.

For a future cutover, set the existing project's root to `apps/hosted-wall`
relative to the citations repository and first create a preview. Do not switch
production until gate, reader membership/RLS, global totals, pagination and
availability with the local machine off are verified. No deployment is part of
the native installer.

## Reader-session recovery checkpoint

Runtime cache is **not durable refresh-token storage or cross-instance locking**.
The versioned workspace cache key avoids the previous dated-key source patch,
and storage/refresh failures now produce an explicit organizer-recovery error.
This does not solve concurrent token rotation across server instances.

For manual recovery, the owner obtains a fresh dedicated reader login privately,
updates `SUPABASE_REFRESH_TOKEN` as a sensitive Vercel environment variable and
increments `SUPABASE_SESSION_VERSION`. Never reuse the laptop's refresh token
or paste credentials into source/issues. Verify recovery in a preview.
Adopting durable coordinated session storage and performing actual credential
rotation remain operator decisions, not automatic installation steps.

## Known limits

- The hosted wall is read-only. Issuing and resolving still happen on VirgoZone, then sync into Supabase.
- Comments stay on the laptop's SQLite and do not appear here.
- The access code is one shared secret. Hobby plan has no Vercel-native password protection.
- The reader refresh token lives in the regional runtime cache after refresh.
  Cache loss or concurrent rotation may require the recovery procedure above.
- The current Vercel account did not expose required route-level metrics
  (`observabilityPlusUsable: false`). No production latency/cost improvement is
  claimed from local tests, and no scanner-only optimization ranking was made.
- The imported dependency lockfile reports four high-severity npm audit
  findings. No forced or unrelated dependency upgrade was performed.

## Local smoke test

```bash
npm ci && npm test && npm run build
GATE_SECRET=x GATE_ACCESS_CODE=pumpkin \
  SUPABASE_URL=https://example.supabase.co \
  SUPABASE_PUBLISHABLE_KEY=sb_publishable_example \
  SUPABASE_WORKSPACE_ID=00000000-0000-4000-8000-000000000000 \
  SUPABASE_REFRESH_TOKEN=refresh \
  npx next start -p 3123
```
