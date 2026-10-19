# Halloween Citations 0.2.0

**An offline Hosaka API v1 prototype, not the complete service-backed application.**
Fictional party fun only; these notices have no legal effect.

## Delivered scope

- Optional-contact attendees; eight default and custom violation templates.
- Issue, retrieve, search, edit, void, resolve and reopen citations.
- Transactional first/repeat-offense snapshots with documented corrections.
- Multiple required/optional remedies; completion notes and resolution overrides.
- UUID identity, globally unique display numbers, optimistic citation revisions,
  immutable activity history through the application, UTC deadlines, party
  timezone formatting and printable plain-text notices.
- Dashboard, due-soon and overdue queries evaluated on every action, including
  after restart; private JSON export.
- Twenty schema-backed actions, host-owned declarative controls, no custom JS,
  no runtime dependencies outside Python's standard library.

This implements the Phase 2 narrow proof-of-concept permitted by the supplied
citation plan, not Phase 1's production SQLite acceptance. **SQLite, attachments,
PostgreSQL sync, autonomous notifications, public guest access/comments and the
full mobile web application remain deferred.** Read [the capability report](CAPABILITY_GAPS.md)
before extending this package. Phase 3 is a separately authorized core task.

## Install and approve

Source belongs outside the Hosaka checkout. The default installed location is
`~/.hosaka/extensions/halloween-citations`, but use `hosaka paths --json` to
discover installations with `HOSAKA_DATA_DIR`. On a second machine, copy this
entire package (not runtime data) to an independent directory, then:

```sh
hosaka extensions link /absolute/path/to/halloween-citations --json
hosaka extensions validate halloween-citations --json
```

Linking installs **disabled, unapproved and without grants**. Inspect source
before explicitly running these owner commands:

```sh
hosaka extensions trust halloween-citations --trusted-local --allow-storage
hosaka extensions enable halloween-citations
hosaka extensions run halloween-citations dashboard --params '{}'
```

Approval is tied to the source digest. **Trusted-local is not a sandbox.**
Approved Python retains the operator's privileges. This implementation does not
use that fact to access data outside the context. Root/public execution is
refused by Hosaka. The actor/signature field is an audit label, not an independent
authentication mechanism; every action is private organizer functionality under
the host's existing authorization. Never expose it as a guest API.

Open **Menu -> Extensions -> Halloween Citations**. The extension fills the Hosaka
window, with **Back to Hosaka** returning to the same mounted terminal.
**Issue citation** is the default screen, showing only five everyday fields.
The attendee field searches as you type. Choose an existing match from the
dropdown, or enter a new name: issuing creates the attendee and citation together.
An unambiguous exact name is reused even if you do not select it. Multiple
attendees with the same name require an explicit dropdown selection; matching
never merges their identities. Editing a selected name clears the old selection.
Use **App menu** for the citation table, record management, attendees, templates,
dashboard, and settings/backup. Additional controls are collapsed until needed.
Draft inputs survive panel changes; old results and errors clear when switching
panels so another page never displays stale output from a different action.

Version 0.2 requires the updated Hosaka host with validated `Navigation`,
`Details`, and `LookupInput` components (tracked in Hosaka issue 34). Deploy/build that generic host
upgrade and restart its backend once before opening this version; older hosts
reject the new components. Subsequent compatible package-only edits need no
frontend rebuild, but source changes still require explicit digest reapproval.
No application-specific host code or data migration was introduced.

The primary workflow needs no attendee UUID or separate attendee-creation step.
Advanced tools still use citation numbers, remedy UUIDs and current revisions
from the appropriate menu panels. Refresh
the expected revision after **every** successful mutation. The v1 UI cannot
automatically select server-provided rows, update inputs or download/print files.
Copy the citation notice to a text editor and print there. Receipts exclude contacts and
internal activity but include the organizer signature; review before sharing.

## CLI example

```sh
hosaka extensions run halloween-citations create-person \
  --params '{"display_name":"Count Byte"}'
hosaka extensions run halloween-citations list-violations --params '{}'
hosaka extensions run halloween-citations issue-for-attendee \
  --params '{"display_name":"Count Byte","violation":"no-costume"}'
# The original ID-based action is retained for compatibility and advanced use:
hosaka extensions run halloween-citations issue \
  --params '{"person_id":"COPY-PERSON-UUID","violation":"no-costume","actor":"Spooky Marshal","resolution_duration_seconds":3600}'
hosaka extensions run halloween-citations get \
  --params '{"citation":"COPY-CITATION-NUMBER"}'
hosaka extensions run halloween-citations complete-remedy \
  --params '{"citation":"COPY-CITATION-NUMBER","remedy_id":"COPY-REMEDY-UUID","expected_revision":0,"actor":"Spooky Marshal"}'
hosaka extensions run halloween-citations resolve \
  --params '{"citation":"COPY-CITATION-NUMBER","expected_revision":1,"actor":"Spooky Marshal"}'
hosaka extensions run halloween-citations reopen \
  --params '{"citation":"COPY-CITATION-NUMBER","expected_revision":2,"reason":"Further haunting reported"}'
```

`schemas/*.input.json` are the exact action contracts. Unknown keys and invalid
types are rejected both by the host and package. A controlled rejection returns
`success: false` with an explicit `error`; no partial domain change is saved.
Filesystem/permission errors propagate as host invocation failures, not success.
On a revision conflict, retrieve and reconcile; do not blindly retry.

Durations are integer seconds, measured from incident time, never floating hours.
Supply incident timestamps with explicit offsets; blank means now. UTC is the
initial party timezone. Settings accept IANA names such as `America/Los_Angeles`
when OS timezone data is available; UTC requires no third-party package.
`first_offense` is snapshotted at issuance using earlier non-void citations of the
same attendee/violation; later backdated records or voids do not rewrite history.
An explicit `edit` correction requires `correction_reason`. Completed remedies
remain complete on reopening. Voids are terminal and do not delete history.

## Persistence and limits

The only application data file is
`extension-data/halloween-citations/citations-v1.json`, accessed **only** through
`context.read_data` / `write_data`. It is version 1, initialized lazily on the first
mutation with stable template UUIDs. Unknown versions or corrupt records fail
explicitly; they are never replaced with an empty dataset. This is the initial
JSON format, not a SQLite migration. Any subsequent format requires an explicit
migration preserving existing IDs and history.

One domain mutation, revision increment and audit event are committed in one
atomic snapshot write. Hosaka serializes invocations per extension across host
processes; do not call this repository concurrently outside that runner. No
direct data path, inferred HOME path, internal context attribute, network,
subprocess or serialized SQLite workaround is used.

**The entire snapshot is limited to 65,536 ASCII JSON bytes.** History consumes
space too; this is suitable for a small proof, not a reliable party-sized
production database. Dashboard reports usage. An oversized mutation fails before
writing and leaves the previous snapshot intact. Nothing is silently pruned.
No sync/outbox or autonomous scheduler is claimed. Alerts are derived on actions
and are not delivered when nobody invokes the application.

## Backup, restore, update, uninstall

`export` returns `export_json`, a complete **private** JSON snapshot containing
contacts and history. CLI output is the Hosaka report envelope: extract
`result.export_json` and save the decoded string, not the whole envelope.
Alternatively, the operator can disable the package and copy the snapshot file:

```sh
hosaka extensions disable halloween-citations
# Use the actual extension_data path from `hosaka paths --json`.
umask 077
cp ~/.hosaka/extension-data/halloween-citations/citations-v1.json /your/private/backup.json
```

For restore, keep the package disabled with no in-flight invocation, back up the
current file, and replace only that data file with a known-good decoded snapshot.
Use a regular file owned by the operator, mode `0600`. Re-enable and run
`dashboard`; corruption/version mismatches are explicit errors. A missing file
means an empty prototype, so do not delete the current file as a recovery step.
Keep backups protected; optional contacts are private.

Updates replace source only, never extension-data. Back up first, disable,
replace this independent package, validate, inspect/reapprove the new digest and
re-enable. The application has no destructive reset, purge or delete action.
`hosaka extensions remove halloween-citations` unregisters it while retaining
source and data. `link` restores registration disabled. Never put this application
in the core installer or copy it over the reference extension.

## Tests

Run deliberately after inspecting source, using Hosaka's existing environment:

```sh
PYTHONPATH=/absolute/path/to/Hosaka \
  /absolute/path/to/Hosaka/.hosakavenv/bin/python -m unittest discover \
  -s /absolute/path/to/halloween-citations/tests -v
```

The tests use disposable data roots and the actual host context/worker. They
approve only a **temporary registry**, never the operator's live package.
Contract/integration tests need the existing Hosaka testing dependencies; runtime
does not. No network, provider credentials or production database is needed.
# hosaka_citations
