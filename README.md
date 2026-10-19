# Hosaka Halloween Citations (`hosaka_citations`) - 0.3.0

Offline-first party citations. **SQLite is always the immediate write target.**
Optional Supabase sync pushes individual records and pulls remote changes; it
never switches databases or uploads/replaces the SQLite file.

The existing full-window citation/attendee/remedy workflow is unchanged.
**App menu -> Synchronization** provides status, manual sync and conflict review.
Private evidence can be uploaded from **Manage citation -> Private evidence**.
Fictional party fun only, not a legal notice.

## Install locally

Keep this repository outside Hosaka core. Install the generic host upgrade
tracked in Hosaka issue 35, build its frontend, and restart its backend.
Old text-only hosts reject the new capabilities instead of silently bypassing
them. Runtime Python uses only the standard library.

```sh
hosaka extensions link /absolute/path/to/halloween-citations
hosaka extensions validate halloween-citations
# Inspect source first. This is trusted-local execution, NOT an OS sandbox.
hosaka extensions trust halloween-citations --trusted-local \
  --allow-storage --allow-managed-data
hosaka extensions enable halloween-citations
hosaka extensions run halloween-citations dashboard --params '{}'
```

First launch creates and migrates SQLite, then safely seeds missing default
violation/remedy templates. No sample or populated database ships with source.
No internet, account, API key or deployment credential is required locally.

Data comes from `hosaka paths --json` -> `paths.extension_data` plus the existing
ID **`halloween-citations`**. Default contents:

```text
extension-data/halloween-citations/
  citations.sqlite3           # plus SQLite WAL/SHM while open
  citations-config.json       # optional, private, never overwritten on update
  database-location.json      # guards against accidental path switching
  supabase-auth.json          # optional private user tokens
  files/                     # immutable evidence objects
  uploads/                   # bounded staged uploads
```

The old prototype JSON is left untouched and is not the SQLite write target.
The operator explicitly allowed starting fresh. Existing SQLite records,
evidence, settings, history and outbox survive subsequent restarts/code updates.

## Enable actual two-way Supabase sync

1. Create a Supabase project and an organizer Auth user. Record their user UUID.
   Prepare a PostgreSQL **deployment** connection through `PGSERVICE` / your
   private `.pg_service.conf`; enable certificate verification.
2. From this repository, run the versioned deployment migrations and create the
   workspace membership. These credentials stay on the deployment machine:

   ```sh
   PGSERVICE=halloween_admin PGSSLMODE=verify-full python3 -m citations.cli \
     remote-migrate --workspace WORKSPACE-UUID --member AUTH-USER-UUID
   ```

3. Configure the client with the project URL, **publishable/anon** key and the
   same workspace UUID. No service-role, secret project key or PostgreSQL
   administrator credentials belong in a client:

   ```sh
   python3 -m citations.cli configure \
     --project-url https://YOUR-PROJECT.supabase.co \
     --publishable-key YOUR-PUBLISHABLE-KEY --workspace WORKSPACE-UUID \
     --interval 60 --enable-sync
   python3 -m citations.cli --allow-network login --email organizer@example.com
   ```

   The password is prompted without echo; refresh/access tokens are stored
   privately in extension-data, not in source, action parameters or the browser.
   Standalone setup commands execute deliberately as the operator; they never
   approve or enable host code.
4. Explicitly grant optional network/background execution after inspecting the
   updated source, retaining the storage grants:

   ```sh
   hosaka extensions trust halloween-citations --trusted-local \
     --allow-storage --allow-managed-data --allow-network --allow-background
   hosaka extensions run halloween-citations sync-now --params '{}'
   ```

While the native Hosaka backend runs, the granted background action honors the
persisted interval. A browser need not remain open. Manual sync uses the same
durable worker. `configure --disable-sync` stops remote activity without changing
SQLite or deleting pending work. See [configuration example](config.example.json).
Configuration edits preserve unspecified existing fields.

## Safety, recovery and updates

- Every domain mutation and its pending row operation commit in one SQLite
  transaction. Queue IDs, retries, cursors and conflict snapshots are durable.
- Server revisions detect concurrent edits. Review both versions and explicitly
  choose local or remote. Remote deletions cannot be silently resurrected.
- Deadlines (`due_at`, allowed seconds) are separate from elapsed time to
  resolution. Resolution/reopening/amendment history remains append-only.
- Evidence metadata is independent of retryable private-object transfers.
  Allowed initial content: JPEG, PNG, WebP, PDF, up to 10 MiB. Original metadata
  is retained; inspect it before any external sharing. No public evidence URL
  or guest API is exposed.
- Default seeds never overwrite edits. Optional admin `remote-seed --workspace
  WORKSPACE-UUID` uses the same defaults. `seed --demo` is an explicit opt-in
  demonstration; it is never run during install/update.
- `python3 -m citations.cli backup /private/path/citations.zip` creates a
  consistent SQLite backup with evidence and non-secret configuration. User
  tokens are excluded. JSON export is for record inspection, not full restore.
- To restore: disable the package and stop background work, back up the current
  data, validate the archive, restore `database.sqlite3` to the configured
  database path and `files/` underneath the same extension-data directory.
  Do not restore while connections are open; remove only stale WAL/SHM sidecars
  belonging to that database after all workers stop. Retain/review configuration
  and sign in again if necessary. Never extract an untrusted archive blindly.
- Updates replace source only. Back up, disable, update, validate/reapprove,
  then enable. Unregistering with `extensions remove` preserves source/data.
  No installer erases storage. Database path/workspace changes are refused after
  binding rather than silently starting empty or sending data to another event.

See [protocol and deployment details](docs/SYNCHRONIZATION.md) and
[actual verification and remaining hosted checks](docs/TESTING.md).

## Complete deployment handoff for a new coding agent

This section is intentionally self-contained. Do not assume that a previous
agent deployed anything merely because configuration or SQL files exist.

### 0. Establish scope and location before changing anything

- Application repository: `BarkBarkBarkBarkBarkBarkBark/hosaka_citations`.
  Extension ID and data-directory suffix are always **`halloween-citations`**,
  never `halloween-citation`, `citations`, or the Git repository's name.
- Generic host work is tracked in
  [BarkBarkBarkBarkBarkBarkBark/Hosaka#35](https://github.com/BarkBarkBarkBarkBarkBarkBark/Hosaka/issues/35);
  application persistence/sync in
  [BarkBarkBarkBarkBarkBarkBark/hosaka_citations#1](https://github.com/BarkBarkBarkBarkBarkBarkBark/hosaka_citations/issues/1).
- This handoff describes the **local 0.3 working tree**. Do not assume all files
  have been committed/pushed or that a remote checkout contains the host upgrade.
  Check `git status`, the manifest version and host CLI capabilities. Do not
  commit, publish, approve, enable, restart or change credentials without the
  operator's applicable authorization.
- Keep application SQL, migrations, seeds, sync/auth code and docs here. Do not
  embed this application in Hosaka core, its installer, provider settings or
  upstream PicoClaw. A separate service host is not needed for this implementation.
- Run as the owning unprivileged operator. Use the same `HOSAKA_DATA_DIR` and
  execution environment as the backend. Docker paths are inside the container;
  do not guess their corresponding host path.
- The owner allowed this prototype installation to start SQLite fresh. Its old
  JSON is preserved. That is **not permission to discard data on another
  installation** or delete an existing SQLite database to fix deployment.

```sh
hosaka paths --json
hosaka extensions info halloween-citations --json
hosaka extensions doctor --json
hosaka extensions trust --help
# The help must include --allow-managed-data, --allow-network and --allow-background.
```

On an existing installation, obtain the source directory from `info.path`;
obtain storage from `paths.extension_data/halloween-citations`. On a fresh
installation, link an inspected independent checkout, do not clone over an
existing package or data directory. In commands below, `CITATIONS_SOURCE` means
that independent source directory. Execute Python commands from it:

```sh
cd "$CITATIONS_SOURCE"
python3 --version                         # 3.10+
python3 -c 'import sqlite3; print(sqlite3.sqlite_version); print(sqlite3.connect(":memory:").execute("select json_valid(?)", ("{}",)).fetchone())'
hosaka extensions validate "$CITATIONS_SOURCE" --json
```

The host also needs its normal dependencies and a rebuilt frontend containing
`FileInput`/`Files`. The application runtime itself is stdlib-only. Restart the
native backend only with permission: it disconnects existing terminal sessions.
Do not launch a second backend on an occupied port or kill unrelated processes.

### 1. Gather the deployment inputs; stop if they are absent

| Input | Where/how obtained | Allowed in client data? |
| --- | --- | --- |
| Project HTTPS origin | Supabase project's API settings | Yes |
| Publishable key or legacy anon key | Project API settings | Yes; not an administrator key |
| Workspace UUID | Generate once with `python3 -c 'import uuid; print(uuid.uuid4())'`, or reuse the event's existing UUID | Yes; same value on every device |
| Organizer Auth user UUID | Create/invite and confirm an organizer in Supabase Authentication; copy its real user UUID | Membership setup only |
| Organizer email/password | Operator-controlled account; enter password only at the CLI prompt | User session tokens only, privately |
| PostgreSQL deployment connection | Project Connect panel, held by the deployment operator | **No** |
| Trusted CA settings | Provider/system trust configuration for libpq certificate verification | Deployment machine only |

If no project exists, ask the operator to create one or grant a controlled
provisioning path. This repository does not create cloud projects or billing
resources. Never invent project/workspace/user IDs or substitute a service-role
key for a user login. Enable the email/password Auth provider and complete any
required email confirmation. The login CLI does not implement an MFA challenge.

Use an operator-managed `~/.pg_service.conf` (mode `0600`) and a private
`PGPASSFILE`/`.pgpass`, or another controlled libpq credential source. Do not put
passwords in Git, this README, command history, browser code or action parameters.
Use the **exact** host/port/user supplied by the project's Connect panel:
direct connections and session poolers use different connection settings.
For an IPv4-only deployment machine, choose a suitable session-pooler endpoint.
Use `sslmode=verify-full` with trusted CA roots, not an insecure verification
override. Never print the private connection files in an agent transcript.

Example private service structure, with placeholders only:

```ini
[halloween_admin]
host=HOST-FROM-THE-PROJECT-CONNECT-PANEL
port=5432
dbname=postgres
user=USER-FROM-THE-PROJECT-CONNECT-PANEL
sslmode=verify-full
sslrootcert=/private/path/to/trusted-provider-ca.pem
```

Set non-secret deployment variables:

```sh
export SUPABASE_URL='https://YOUR-PROJECT.supabase.co'
export SUPABASE_PUBLISHABLE_KEY='YOUR-PUBLISHABLE-OR-ANON-KEY'
export WORKSPACE_UUID='YOUR-EVENT-WORKSPACE-UUID'
export ORGANIZER_USER_UUID='EXISTING-SUPABASE-AUTH-USER-UUID'
export ORGANIZER_EMAIL='organizer@example.com'
export PGSERVICE='halloween_admin'
export PGSSLMODE='verify-full'
# PGPASSFILE, if used, points to a private external file, not a source file.
```

These values are not supplied by this repository. Obtain actual values through
the operator's approved channel; do not search unrelated provider credential
files. Confirm that deployment is targeting the intended project before writing.

### 2. Deploy only versioned remote migrations

```sh
psql --version
psql -X --set ON_ERROR_STOP=1 -c 'select current_database(), current_user;'
python3 -m citations.cli remote-migrate \
  --workspace "$WORKSPACE_UUID" --member "$ORGANIZER_USER_UUID" \
  --name 'Halloween event'
```

`remote-migrate` uses the deployment connection, creates/records migrations,
adds membership for the real Auth user and notifies PostgREST to reload its schema.
It can be rerun; existing workspace names, records and memberships are preserved.
Each migration is transactional. If setup stops after a migration, rerun rather
than dropping tables. Deployment failure is explicit and identifies the migration.

Files, in order:

1. `citations/migrations/postgres/001_domain.sql`: workspace-scoped normalized
   domain tables, foreign keys, membership and RLS.
2. `002_replication.sql`: server revisions, immutable history, idempotent operation
   ledger and commit-order cursor; `hc_push` / `hc_pull` RPC functions.
3. `003_private_storage.sql`: private `halloween-citations-evidence` bucket and
   workspace-member SELECT/INSERT policies.

Do not edit an applied migration; add the next numbered file for changes.
Do not grant direct client table UPDATE/DELETE, disable RLS, make the evidence
bucket public, or add anonymous/broad Storage policies to “make it work.”
The application advisory writer lock intentionally serializes remote writers.

Useful deployment checks:

```sql
SELECT version FROM public.hc_schema_migrations ORDER BY version;
SELECT id, name FROM public.hc_workspaces;
SELECT workspace_id, user_id FROM public.hc_members;
SELECT id, public, file_size_limit, allowed_mime_types
FROM storage.buckets WHERE id = 'halloween-citations-evidence';
```

Expect versions `1,2,3`, the intended workspace/member, a **private** bucket,
10 MiB limit and PNG/JPEG/WebP/PDF MIME types. Review the project's existing
policies too: PostgreSQL permissive policies combine with OR, so an unrelated
broad policy can undermine a narrow application policy.

### 3. Seed defaults only if desired

Normal first launch seeds local defaults, which upload through normal sync.
Remote seeding is optional:

```sh
python3 -m citations.cli remote-seed --workspace "$WORKSPACE_UUID"
```

Default seed SQL is separate from migrations and uses conflict-ignore semantics.
Reruns do not duplicate templates or overwrite organizer changes. Never run
`seed --demo` on a real event without explicit permission; demo citations are not
required for installation, migration, verification or production use.

### 4. Configure and authenticate each client

Use the same workspace on every device; add each distinct organizer's real Auth
user as a member by rerunning `remote-migrate` with that user UUID if necessary.
Deployment credentials stay on the deployment machine.

On each client, as its owning operator:

```sh
cd "$CITATIONS_SOURCE"
python3 -m citations.cli init
python3 -m citations.cli configure \
  --project-url "$SUPABASE_URL" \
  --publishable-key "$SUPABASE_PUBLISHABLE_KEY" \
  --workspace "$WORKSPACE_UUID" --interval 60 --enable-sync
python3 -m citations.cli --allow-network login --email "$ORGANIZER_EMAIL"
```

The login command prompts for the password without echo. The private
`supabase-auth.json` contains an authenticated user session, not a deployment or
service-role credential. Its permissions must be `0600`. Neither this file nor
the real `citations-config.json` belongs in source control.

After source inspection and explicit owner approval:

```sh
hosaka extensions trust halloween-citations --trusted-local \
  --allow-storage --allow-managed-data --allow-network --allow-background
hosaka extensions enable halloween-citations
hosaka extensions run halloween-citations sync-now --params '{}'
hosaka extensions run halloween-citations sync-status --params '{}'
```

Only enable after the code digest/grants are approved. Reapproval does not erase
SQLite/configuration. Without network/background grants, local actions remain
available; manual remote sync reports the missing permission. With background
granted, the native backend schedules the extension every 15 seconds and the
extension honors the configured interval; closing the browser does not stop it.
Stopping the backend does. The scheduler has no access to deployment credentials.

### 5. Prove the hosted behavior before calling deployment complete

Use a **disposable acceptance workspace and separate client data roots** for
destructive/interruption tests, not an existing event. Do not send real attendee
contacts to a test project. Both clients must use the same acceptance workspace.

1. Disable remote sync or disconnect the network. Use the normal UI to create
   an attendee/citation, edit it and record a resolution. Restart the client.
   Confirm all fields/history persist locally.
2. Enable sync and run `sync-now`; verify those records appear remotely.
   Repeat sync and interrupt/retry once. Record/operation counts and server
   audit side effects must not duplicate.
3. Sync client B. Resolve/reopen/amend from B, then sync A. Verify the change and
   history appear on A and that downloaded changes do not create an endless queue.
4. Edit the same citation offline on A and B. Sync A, then B. B must preserve
   its local version and the server version as a conflict. Review them and choose
   explicitly; another sync must converge or surface another newer conflict.
5. Delete on A while B has a stale edit. Sync both. The tombstone must survive;
   generic conflict resolution must not resurrect the deleted citation.
6. Upload a private test PDF/image; interrupt the transfer. Retry, then retrieve
   it on B. Confirm stable attachment identity, matching SHA-256/size and no
   citation loss. Logged-out and unrelated-workspace users must not read it.
7. Verify `pending_changes`, `pending_files`, `unresolved_conflicts`,
   `remote_cursor`, `last_error` and `last_successful_sync`. A partially exchanged
   page or pending conflict is not full synchronization.
8. Back up SQLite/evidence, update code, reapprove and relaunch. Verify the
   records, evidence, private configuration and pending operations remain.

Use the actual user JWT when checking HTTP access; a service-role/admin query
bypasses RLS and cannot prove a client policy. Test unrelated and anonymous
accounts explicitly. Never place tokens in a browser bundle, action parameters,
GitHub issue comment or transcript to demonstrate the test.

### 6. Failure recovery and things an agent must not do

| Symptom | Correct next step |
| --- | --- |
| Host rejects manifest/capabilities | Install/verify the generic host upgrade; do not bypass context paths or permissions |
| 401 / expired session | Check project/publishable key and sign in again; do not use a service-role key |
| 403 | Check actual Auth user UUID, workspace membership and policies |
| RPC not found / schema error | Confirm all migration versions and request PostgREST schema reload |
| Storage failure | Keep pending work; check private bucket, MIME/size limits, token and workspace prefix |
| TLS failure | Fix trusted CA/hostname/connect settings, never disable verification |
| Source changed / missing grant | Ask the owner to inspect/reapprove; no API may grant trust |
| Conflict | Review both snapshots; do not force last-write-wins or change server revisions by hand |
| Wrong workspace/project binding | Stop; back up and ask about an explicitly separate event profile, never clear replica state to bypass it |
| SQLite/cursor/outbox problem | Back up first; inspect integrity/migration state, never delete the DB/queue as an automatic reset |

`configure --disable-sync` stops remote work while retaining SQLite and queue.
Local operations must remain usable during outages and authentication failures.
Do not replace SQLite with PostgreSQL, upload the SQLite file, delete tombstones
to make queues empty, prune conflict snapshots, restore backups over active
connections, rename the extension/data directory, or run tests against production.

### 7. Current evidence and remaining handoff

Implemented and locally exercised: offline CRUD/restart, durable outbox, real
PostgreSQL push/pull, idempotent retries, remote resolution, conflicts, tombstones,
evidence recovery, repeatable setup/seeds, commit-order cursors, host grants and
binary bounds. See `docs/TESTING.md` for exact reproduction commands.

**A hosted Supabase deployment has not been claimed.** No real project credentials
were supplied. Local PostgreSQL tests use stand-in Auth/Storage catalog objects;
transport tests use controlled HTTP responses. The next agent still must verify
real project deployment privileges, GoTrue login/refresh/JWT verification,
PostgREST exposure/schema reload, and actual private Storage upload/download/
interruption behavior with the deployed policy set.

Report commands and actual outcomes, record unresolved live checks separately,
and leave all production data/credentials intact. Do not turn a successful
configuration command or local fixture into a “Supabase synchronization passed”
claim.
