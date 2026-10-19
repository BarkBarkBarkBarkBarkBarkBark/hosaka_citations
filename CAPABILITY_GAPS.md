# Current capability and verification gaps - 0.3.0

This replaces the historical 64 KiB prototype report. The operator authorized
generic host upgrades and real SQLite/Supabase synchronization. The
[README deployment runbook](README.md#complete-deployment-handoff-for-a-new-coding-agent)
is the handoff entrypoint; [protocol details](docs/SYNCHRONIZATION.md) and
[test reproduction](docs/TESTING.md) provide implementation evidence.

## Architecture and ownership

| Boundary | Current implementation |
| --- | --- |
| Package identity | `halloween-citations`, unchanged; Git repository is `hosaka_citations` |
| Source | Independent extension checkout, not Hosaka core |
| Data | Canonical `hosaka paths` extension-data root plus `halloween-citations` |
| SQLite, migration SQL, seeds, auth, replication, evidence | Extension-owned |
| Generic path/grant/worker/file/scheduling infrastructure | Hosaka core, tracked in Hosaka issue 35 |
| User interface | Existing host-owned declarative app; no arbitrary extension JS or exposed DB credentials |
| Installation/update | Source replacement only; no populated/template DB, data-directory rename or automatic purge |
| Approval | Explicit digest-bound trusted-local approval, storage/directory grants, optional network/background grants |

SQLite is always the immediate local write target. Enabling or disabling remote
sync does not select another backend, move storage, or replace the SQLite file.
The old JSON file is left untouched and ignored for new SQLite initialization:
the owner explicitly allowed this prototype to start fresh. Existing SQLite
data/configuration must survive all later upgrades.

## Implemented capabilities

| Requirement | Implementation and evidence |
| --- | --- |
| Offline persistence | Normalized SQLite, WAL, enforced foreign keys, repeatable numbered migrations; offline create/edit/resolve/restart tests |
| Record model | Persons with optional contact fields, violation/remedy templates, citations, multiple remedies, append-only events, attachment metadata |
| Time semantics | UTC persisted timestamps; allowed resolution duration/deadline separate from elapsed resolution time; resolve/reopen/amend history retained |
| Setup/seeding | SQLite and equivalent workspace-scoped PostgreSQL DDL; operator `remote-migrate`; separate safe default seeds; opt-in demo only |
| Durable queue | Domain row mutation plus UUID outbox operation in the same SQLite transaction; failed outbox insertion rolls back the mutation |
| Two-way sync | Record-level `hc_push` / `hc_pull`, durable acknowledgments/checkpoints, authenticated user REST client; actual PostgreSQL synchronization tests |
| Duplicate delivery | Operation ledger rejects changed content under a reused ID and replays committed acknowledgments without duplicate side effects |
| Remote edits | PostgreSQL triggers assign revisions/log direct updates; remote resolution pulls locally; downloaded changes do not generate echo outbox operations |
| Cursor ordering | Server transactional workspace counter plus writer serialization, not device timestamps; slow-writer commit-order test |
| Conflict behavior | Preserve both versions and archived operations, show conflicts, explicitly choose local/remote; no default last-write-wins |
| Deletions | Persistent tombstones; generic local conflict choice cannot resurrect a remote deletion |
| Evidence | Generated immutable local objects, metadata in SQLite, size/signature/hash checks, separate durable upload/download retries and private bucket paths |
| Scheduling/status | Manual sync, configured interval while native backend runs, explicit background grant; queue/file/error/conflict/cursor/last-success status |
| Backup/update | Consistent SQLite/evidence ZIP backup, private configuration, separate user tokens; repeatable initialization/seed and lifecycle preservation tests |
| Tenant access | Workspace membership, RLS, authenticated RPC, private Storage SELECT/INSERT policies; unrelated-user denial tested in PostgreSQL |

This is implemented synchronization, not a setting that switches database
connections and not a whole-file upload. Runtime client dependencies are Python
standard library only. The PostgreSQL driver is isolated in the development
environment for tests, not installed into Hosaka core.

## Verification boundary: what remains unverified

No real Supabase project/deployment/organizer credentials were supplied.

| Check | Current evidence | Remaining hosted acceptance |
| --- | --- | --- |
| PostgreSQL migrations/RPC/revisions/idempotence/cursor | Real isolated PostgreSQL 17 tests | Apply using the actual project's deployment role, TLS and pooler configuration |
| Workspace RLS | Real PostgreSQL authenticated role and stand-in `auth.uid()` | Confirm genuine project JWT validation and the project's entire policy set |
| GoTrue login/token refresh | Production transport code plus controlled response tests | Actual account sign-in, expiry/rotation and project Auth configuration |
| Private evidence Storage | SQL policy checks, retry/hash tests and transport contracts | Actual Storage HTTP responses, interrupted transfer, authenticated download and cross-workspace denial |
| PostgREST | Implemented typed RPC calls and schema-reload notification | Confirm hosted exposure/cache reload with real authenticated clients |
| Multi-device deployment | Two independent SQLite clients against real local PostgreSQL | Two real installed machines across an outage and restart |

These rows are not marked passed merely because configuration, SQL or a mock
exists. A new deployment agent must follow the README's hosted acceptance
workflow and report actual results before claiming a complete Supabase deployment.

## Genuine limitations, not completed features

- Trusted-local execution is **not an OS sandbox**. Grants/context checks and
  sanitized worker environments do not hide arbitrary user-readable files from
  malicious approved Python. Protected Linux isolation remains separate work.
- Background sync runs while the native Hosaka backend runs, not when the
  machine/backend is stopped. This is a bounded scheduler, not a generic
  long-running service/dependency/container manager.
- Initial remote writes are deliberately serialized with an application advisory
  lock for commit-order correctness; high-throughput optimization is not claimed.
- Conflict resolution is explicit per entity. Automatic field merging, automatic
  resurrection and automatic tombstone/conflict purging are not implemented.
- Evidence inspection checks signatures, limits and hashes, not complete
  image/PDF semantic validity or malware. EXIF/document metadata is not stripped;
  downloads remain private attachments and users are warned before external sharing.
- CLI login uses email/password Auth; an interactive MFA challenge is not implemented.
- Public citation pages, guest comments, QR sharing, external notifications,
  email/SMS providers and direct printer integration remain out of scope.
- The original advanced CLI actions remain for compatibility. The normal GUI
  uses human names and opaque selections; it does not require citation IDs.
- JSON export is not a complete backup of file binaries/auth/sync internals.
  Use the SQLite/evidence backup command. Tokens are intentionally excluded.

## Rules for the next agent

Do not put service-role/admin PostgreSQL credentials in clients or source,
disable RLS/TLS verification, grant public Storage access, silently reapprove
code, change the extension ID, delete SQLite/outbox data as recovery, edit applied
migrations, or run destructive tests against production. Preserve native
providers, PicoClaw, other extensions and grants. Keep application-specific
capabilities out of core and distinguish local test proof from hosted acceptance.
