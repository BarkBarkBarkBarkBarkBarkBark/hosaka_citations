# Verification

## Reproduce

From the package source, use the installed Hosaka environment for host contract
and existing workflow tests. This executes trusted source deliberately:

```sh
PYTHONPATH=/absolute/path/to/Hosaka \
  /absolute/path/to/Hosaka/.hosakavenv/bin/python -m unittest discover -s tests -v
```

PostgreSQL tests skip unless explicitly configured. Run them only against a
disposable local cluster with permission to create/drop test databases and roles,
**never a production database**:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
CITATIONS_TEST_PG='host=127.0.0.1 port=16432 user=citest dbname=postgres' \
  .venv/bin/python -m unittest discover -s tests -p test_sqlite_sync.py -v
.venv/bin/python -m unittest discover -s tests -p test_transport.py -v
```

The tests create/drop uniquely named databases on that cluster. They create
stand-in `auth.uid()` and Storage catalog tables to test the actual PostgreSQL
migrations/RLS, not a hosted Supabase Auth/Storage server.

## Covered behavior

- Offline creation/edit/resolution and restart retrieval; data larger than the
  former 64 KiB limit; outbox insertion failure rolls back the domain mutation.
- Repeatable SQLite initialization/migrations; default seed customization
  preservation; code/lifecycle remove/relink retains data; complete evidence backup.
- Two clients push/pull through the real PostgreSQL functions with independent
  local SQLite databases.
- Enabling sync after offline use uploads existing data. Lost acknowledgments
  replay the same operation without duplicate records or repeated server audit
  side effects.
- Remote resolutions (including direct PostgreSQL updates) pull locally and do
  not generate endless outbox work.
- Concurrent edits preserve both versions, explicit reconciliation converges,
  and remote deletions cannot resurrect stale records.
- Evidence interruption/retry and checksum-verified download; upload identity
  retries do not duplicate local metadata.
- Workspace RLS and private Storage policies reject an unrelated authenticated
  user; operation identity reuse with altered payload is rejected.
- An uncommitted earlier writer blocks later writers; the cursor cannot skip
  its eventual commit. Remote setup/default seeding are repeatable.
- Transport contracts verify user-token refresh, authorization headers, default
  certificate-verifying TLS usage, redacted error bodies, private credential
  permissions and rejection of admin/service-role credentials.
- Host tests cover managed path confinement, explicit grants, root/public/safe
  mode boundaries, separate bounded background execution, binary upload size and
  download confinement, and existing v1 behavior.

## Observed local results (2026-10-18)

- 145 focused Hosaka host/terminal tests passed.
- 44 distinct extension tests passed across the host Python and isolated
  PostgreSQL test environment: 36 ordinary/local/transport tests plus 8 real
  PostgreSQL integration cases. The PostgreSQL command runs 11 tests because it
  also repeats the 3 standalone SQLite cases.
- PostgreSQL 17 exercised the actual migration/RPC/RLS functions, two-device
  exchange, lost acknowledgments, remote SQL resolution, explicit conflicts,
  tombstones, file interruption/recovery, cursor commit ordering and repeatable
  deployment/default seeds.
- Frontend type-check/build and package validation passed.
- Browser smoke checks exercised first-launch SQLite initialization, offline
  citation entry, the actual FileInput upload/attach/private download workflow,
  and synchronization status with no credentials configured. Pending record/file
  work remained visible while remote synchronization was disabled.

These are local results, not hosted Supabase deployment evidence.

## Not verified without real Supabase credentials

No real project URL, organizer login or deployment credentials were provided.
The following remain live integration checks, **not claimed as passing**:

1. Applying migrations through the project's actual PostgreSQL deployment role,
   its TLS/pooler settings and PostgREST schema cache.
2. Real GoTrue password sign-in, JWT verification, expiration and refresh-token
   rotation for the deployed organizer account (transport tests use responses).
3. Actual Supabase Storage HTTP upload/duplicate response behavior, interrupted
   transfer and authenticated download against the private bucket.
4. End-to-end enforcement of the deployed project's existing database/storage
   policy set over HTTP, and two real machines across an outage.

The synchronizer and server functions are implemented and exercised against real
local PostgreSQL; this is not a configuration-only backend switch. The above
distinction prevents local protocol tests from masquerading as hosted acceptance.
