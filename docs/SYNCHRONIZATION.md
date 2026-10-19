# Persistence and replication contract

## Local model and migration rules

`citations/migrations/sqlite/` owns SQLite DDL; `migrations/postgres/` owns its
PostgreSQL counterpart. Both persist persons, violation templates, citations,
remedies, append-only citation events and attachment metadata, with foreign
keys, UUID identities, unique display numbers/codes, status/boolean constraints,
UTC timestamps and tombstones. PostgreSQL adds an explicit workspace to all keys
and native UUID/timestamptz/jsonb types; SQLite uses canonical text/integers/JSON.
Synchronization/control tables are engine-specific.

Migration versions commit atomically. Initialization is repeatable, never ships
a database, and never replaces existing tables/data. Add numbered migrations
for future changes; do not edit an already deployed SQL file. Local downgrades
with unknown applied versions are refused. Changing configured database paths
requires an explicit verified move; normal configure/update cannot start empty.
SQLite uses WAL and foreign-key enforcement. No network call holds a SQLite
write transaction. A generation check prevents a foreground write from losing
a concurrently pulled update.

Seeds are separate from DDL. Defaults have deterministic template UUIDs and
insert only absent IDs/codes. Per-citation remedy rows are created from the
template when issuing. Customizations are never overwritten. Demo fixtures are
explicitly opt-in and marked transactionally.

## Row protocol

Local mutations upsert normalized rows and append UUID outbox operations in one
transaction. Outbox payloads are immutable JSON row snapshots, not database files.
Per-entity expected remote revisions chain offline edits in order. A server
operation ledger returns the original acknowledgment for duplicate delivery and
rejects reusing an operation ID with different content.

`hc_push` authenticates membership, checks the expected server revision and either
applies the row or returns the conflicting server snapshot. Only whitelisted
entity types/fields are accepted. Direct client table writes are not granted.
`hc_pull` returns rows after a durable workspace cursor. Writer statement locks
and a transactional counter guarantee commit-order positions: a late commit with
a lower allocated sequence cannot be missed. Device timestamps are not used for
sync ordering. This initial implementation deliberately serializes server writers
through one application advisory lock; it favors correctness over high throughput.

Server triggers track direct administrative updates too, including remotely
entered resolutions. Citation updates append server audit history. Events are
immutable. Physical domain deletes are refused; use tombstones.

The client persists each acknowledgment and each pulled page transactionally.
Downloaded rows do not enqueue upload operations. Already acknowledged old
changes are ignored by revision, preventing own-history echo loops. UI selection
versions are local monotonic metadata, separate from server revisions, so pulls
also invalidate stale forms without making replicated rows diverge.

Failures retain pending work and use bounded persistent exponential backoff.
Manual sync may retry immediately. Each worker exchanges a bounded time slice;
partial progress is not reported as full convergence. Background invocations
require explicit grants and stop with the native host. They do not replace a
general service manager or operate when Hosaka is stopped.

## Conflicts and deletion

Concurrent versions are both stored in `sync_conflicts`; failed/superseded outbox
payloads remain archived. Ordinary local edits remain available during an outage
or conflict. A conflicted entity's successor operations wait for review, while
unrelated records can continue.

Choosing local queues the latest local snapshot against the reviewed server
revision. Choosing remote applies that snapshot and archives the discarded local
version. A subsequent remote edit creates another conflict rather than silently
winning. A remote tombstone cannot be overridden by the generic local choice.
Tombstones are retained after propagation; no automatic purge/resurrection occurs.

## Evidence

Generic host upload/download endpoints are authenticated, digest/grant checked
and confined to this extension's managed uploads/files areas. Evidence is
content-inspected for supported signatures, bounded in size, SHA-256 checked,
and assigned a generated immutable key. This is not a full image/PDF parser or
malware scanner. Downloads are attachments with `nosniff`, not executable HTML.

Metadata is queued independently of `file_transfers`. Objects upload before their
initial metadata is published. Duplicate uploads verify the existing object's
bytes rather than overwriting it. Downloads verify size/hash before replacement.
Interrupted or absent objects retry durably without losing a citation. Deleting
a citation tombstones its evidence metadata; objects/history are retained.

The private Supabase bucket is `halloween-citations-evidence`. Object names begin
with the workspace UUID. INSERT/SELECT policies require authenticated membership;
no public bucket, anonymous read, object overwrite, or client admin credential is
provided. Do not add broad existing policies to this bucket.

## Deployment and authentication

The remote migration CLI uses `psql`, with `PGSERVICE`/private deployment settings.
It creates membership only for an existing Supabase Auth user. It records applied
migration versions and requests PostgREST schema reload. Client code uses the
publishable/anon key plus user JWT, not PostgreSQL credentials or service-role
keys. User tokens rotate through GoTrue refresh and stay in a private data file.
TLS uses certificate verification; there is no insecure production switch.

Runtime dependencies are Python standard library only. The isolated development
environment's PostgreSQL driver is for tests, not Hosaka core or client runtime.
Host trusted-local execution is not an OS sandbox. Optional contact fields and
all conflict/export data are private organizer information; a guest interface
would need separate policies/API and is not implemented here.
