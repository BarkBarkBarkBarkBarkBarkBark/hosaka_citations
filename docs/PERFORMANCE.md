# Local gateway and citation verification

All measurements below use disposable synthetic data on Linux x86_64,
Python 3.12.15. They do not measure production Supabase, Vercel, a Pi or macOS.

## Direct actions

Run from the citations checkout:

```sh
../Hosaka/.hosakavenv/bin/python scripts/benchmark.py \
  --citations 1000 --writes 1000 --reads 50 --workload edit
../Hosaka/.hosakavenv/bin/python scripts/benchmark.py \
  --citations 1000 --writes 1000 --reads 50 --workload mixed
```

Each invocation creates and removes its own SQLite database. The mixed workload
is exactly 1,000 measured mutations: 250 each of edit, remedy completion,
resolution and reopening. The database contains 1,000 citations throughout.
Issuing is separately covered by domain and real-browser workflow tests; it is
not part of this mixed timing distribution.

| Direct action | Original p95 | Optimized p95 |
| --- | ---: | ---: |
| Get citation | 43.069 ms | 3.220 ms |
| Legacy search | 64.969 ms | 46.955 ms |
| Edit | 153.367 ms | 5.102 ms |

These are equivalent 1,000-row/1,000-edit runs, not HTTP timings. Get improved
about 92.5%; edit about 96.7%. Legacy search remains compatible; the new bounded
SQL `list-citations` action measured 4.388 ms p95. A separate mixed run measured
5.944 ms p95 across all 1,000 writes.

The implementation scopes common detail/mutation snapshots, indexes citation
relations and list paths, avoids rewriting the database locator on every open,
and replaces terminal N+1 detail calls with a bounded SQL page containing
remedy summaries. Multi-page terminal reads detect generation changes.
Schema structural checks use a bounded content cache; duplicate-key-safe YAML
parsing uses PyYAML's C safe loader when available. Trust/digest/snapshot,
instance validation, revisions, transaction and outbox checks remain in place.

## Full transport measurements

Install the optional native packages into a disposable prefix, then start:

```sh
cd ../hosaka-gateway
PYTHONPATH=../Hosaka .web-venv/bin/python tests/serve_fixture.py --port 18986
```

This explicitly links/trusts source **only in a disposable registry**, seeds
1,000 synthetic citations and prints its temporary data root. The fixture
password is `synthetic-browser-password`; it is not an installation credential.
Stop with Ctrl-C to remove fixture data and shut down app workers.

The measured real gateway requests and installed terminal commands produced:

| Surface | Samples | p95 | Budget |
| --- | ---: | ---: | ---: |
| HTTP list, 50-row page of 1,000 | 50 | 58.466 ms | 250 ms |
| HTTP detail | 50 | 57.754 ms | 250 ms |
| HTTP mixed mutations | 1,000 | 56.874 ms | 500 ms |
| `cite show` | 20 | 670.503 ms | 1,000 ms |
| `cite open`, all 1,000 open rows | 20 | 984.269 ms | 1,000 ms |

CLI measurements include process startup, guarded Hosaka worker execution and
rendering, from an unrelated working directory. Terminal selection used a
pseudo-terminal. The initial new paginated CLI took 2.1 seconds and failed its
budget; duplicate YAML parsing/path resolution and two action invocations were
identified and reduced before accepting the result.

The HTTP mutation run also keeps exactly 1,000 citations throughout, using the
same four mutation kinds. No pre-change HTTP/CLI distribution was collected,
so no end-to-end percentage improvement is claimed.

## Browser and regression coverage

Real Chromium checks exercised widths 320, 390, 768 and 1280 without horizontal
overflow, deep-link reload/back navigation, issue/remedy/resolve, and VirgoZone
prefix-aware assets/WebSockets. No browser page errors were observed.
Installed commands run outside their source directories; wheel builds include
schemas, SQLite/PostgreSQL migrations, templates and static assets.

Pi/macOS hardware, trusted-phone TLS, real remote synchronization, durable hosted
reader-session coordination, production metrics and deployment cutover remain
unverified. Do not equate development-machine budgets with Pi acceptance.
