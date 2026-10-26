# Auth and sync issues on this client

Two separate failures showed up while connecting this machine to the shared citations backend. Neither one means the Supabase account or the stored credentials are bad.

No passwords, session tokens, or API keys belong in this note.

## What was run

From `hosaka_citations`, after local install and `citations.cli configure`:

```sh
python -m citations.cli --allow-network login --email "$(hosaka secrets get ORGANIZER_EMAIL --reveal)"
hosaka extensions run halloween-citations sync-now --params '{}'
hosaka extensions run halloween-citations sync-status --params '{}'
```

The extension is linked, enabled, and trusted with storage, managed data, network, and background. `execution_available` is true. Sync is enabled in the private client config. There is still no `supabase-auth.json` under the extension data directory, so this client has never completed a login.

## 1. Interactive login returns HTTP 400

The password prompt appeared, a password was entered, and the CLI printed only:

```text
Supabase request failed (HTTP 400).
```

That text is produced in `citations/supabase.py`. On any HTTP error the client throws away the response body and keeps the status code. A 400 from `POST /auth/v1/token?grant_type=password` is therefore opaque here. Supabase uses that status for a rejected password grant, including invalid credentials.

The Hosaka secrets file is not what the login command authenticates with.

- `ORGANIZER_EMAIL` was used only because the shell substituted `hosaka secrets get ORGANIZER_EMAIL --reveal` into `--email`. That command prints the stored address plus a newline, and command substitution strips the newline, so the address passed to the CLI matches the stored value.
- `ORGANIZER_PASSWORD` is never read by `citations.cli login`. The password is collected only by `getpass` at `Supabase user password:`.
- `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, and `WORKSPACE_UUID` match the already written `citations-config.json`. Putting them in `~/.hosaka/secrets.json` does not change login or sync.

A direct password grant with the stored email, stored password, configured project URL, and configured publishable key returned HTTP 200 and a normal user session (`access_token` and `refresh_token`). That session was not written to disk. The account and the stored secret are usable. The 400 is from the password typed at the prompt, which did not match that secret. The client then hid Supabase's reason.

## 2. sync-now and sync-status return busy

Both later runs failed in about 800 ms with:

```json
{"code": "busy", "message": "This extension already has an active invocation."}
```

Foreground actions take an exclusive flock on `~/.hosaka/state/extension-jobs/halloween-citations.lock` (`hosaka/extensions/runner.py`). The lock is still held by a stopped process, so every new foreground run fails immediately.

Observed holders, still present about 35 minutes after they started (~13:46 local time):

| PID | State | Command | Lock |
| --- | --- | --- | --- |
| 263164 | `T` (stopped, `do_signal_stop`) | `python -B -m hosaka.extensions run halloween-citations sync-status --params {}` | holds `halloween-citations.lock` and `slot-0.lock`; stdio is `/dev/pts/5` |
| 263174 | `Zs` (zombie, child of 263164) | extension worker | |
| 263154 | `T` (stopped) | `python -B -m hosaka.extensions run halloween-citations sync-now --params {}` | does not hold the extension lock |

`263164` also left `halloween-citations.fe9a7d95-945e-4909-8ff5-8684e016e942.active`.

The manifest limit is 30 seconds, and the runner does enforce that deadline, but only while the parent is running. A stopped process does not reach the deadline check, does not exit, and does not drop the flock. Background sync uses a different lock (`halloween-citations.background.lock`) and is not what blocked these calls.

`hosaka extensions info` was also run. That command is healthy and is not the auth or busy failure. Its full JSON was mixed into the same terminal scrollback.

## What this is not

- Not a missing schema-isolation deploy. Login never got far enough to call `hc_push`.
- Not an absent network grant.
- Not an empty or corrupted secrets file. The stored organizer email and password are internally consistent and accepted by the project.
- Not a saved session. `supabase-auth.json` was not created.

## Unblock

1. End the stopped jobs (continue them and let them exit, or kill PIDs 263154 and 263164) so `halloween-citations.lock` is released. Remove the stale `.active` marker only after those processes are gone.
2. Run login again and type the organizer password at the prompt. The shell will not fill it in from `ORGANIZER_PASSWORD`.
3. Confirm `supabase-auth.json` exists and is mode `0600`, then run `sync-now` and `sync-status`.

## Resolution

Done on this machine:

- Stopped PIDs 263154 and 263164. The extension lock and the stale `.active` marker were released.
- Signed in with the stored organizer email and password through the existing login path. `supabase-auth.json` is present and mode `0600`. The password was not passed on the command line.
- `auth-issues.md` changed the package digest, so the same storage, managed-data, network, and background grants were approved again.
- `sync-now` completed. `sync-status` then reported `"last_error": null`, `"pending_changes": 0`, `"pending_files": 0`, `"unresolved_conflicts": 0`, `"pull_incomplete": false`, and `"remote_cursor": 8` at `2026-10-25T21:24:58.675658Z`.

The client still discards Supabase auth error bodies, and `citations.cli login` still does not read `ORGANIZER_PASSWORD`. Those are why the interactive attempt was opaque. They are unchanged.
