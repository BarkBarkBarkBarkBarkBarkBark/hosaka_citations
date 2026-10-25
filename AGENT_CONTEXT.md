# Halloween Citations authoring context

This is independent application source, not a core customization workspace.
Read README.md and docs/SYNCHRONIZATION.md before editing. The operator authorized
generic managed-directory, optional network/background and binary capabilities
(Hosaka issue 35), then SQLite and Supabase replication (this repository issue 1).

- Keep the existing ID halloween-citations, all action schemas, callable and UI aligned.
- UI 0.2 uses root Navigation and collapsed Details, requiring the updated host.
  Keep citation entry first and five primary fields visible. All existing tools
  remain accessible through subpanels. LookupInput stores the visible attendee
  name and a separate selected ID; do not require manual UUID entry to issue.
  issue-for-attendee creates/reuses the person within the same snapshot commit;
  ambiguous names require selection. Preserve all existing domain invariants
  and explicit versioned migration rules.
- Management looks up human names/violations/remedies, never asks for identifiers,
  and passes an opaque citation/revision selection to manage-citation. Keep
  revision conflicts explicit. The citation table declares a read-only page load,
  returns all citations newest-first on entry, and retains explicit filter tools.
- SQLite is always primary. Use documented context.data_path beneath extension-data,
  never infer HOME paths, inspect context internals or import Hosaka in runtime.
  Commit domain changes/outbox together; downloads must not enqueue echo writes.
- Keep DDL, seeds, network/auth, conflicts and evidence in this repository. Core
  owns only generic capabilities. Do not introduce backend-switch configurations.
- Preserve UUIDs, revision checks, historical first-offense snapshots and events.
- No native trust/enable/grant changes without explicit owner action.
- Tests may approve only their disposable registries. Test fixtures are not a
  sandbox. Do not change reference-extension or add application-specific core code.
- Source updates must not overwrite SQLite, files or private configuration.
  Add numbered migrations, never populated/template databases. Default seeds
  preserve customizations. Demo data is explicit opt-in.
- Old JSON is left untouched, not silently migrated; the owner allowed starting fresh.
- Remote objects live in schema hosaka_citations, owned by hosaka_citations_owner,
  to stay isolated from other apps sharing the Supabase project. Never use public.
- Client credentials are publishable key + user session only. No service-role or
  PostgreSQL deployment credentials in clients, logs, action params or source.
- Hosted checks without credentials must remain explicitly unverified.
- Do not claim full-plan completion, sandboxing, successful sync or deployment.
