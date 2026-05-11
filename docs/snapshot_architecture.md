# Shared Snapshot Architecture (D.1)

Repo-wide snapshot layer that lets every module read pre-computed ranked
output without ever blocking a user request on a live universe scan.

D.1 ships the generic store + BullWatch as the first consumer. D.2 will
migrate BullAlpha and Radar. D.3 introduces tiered refresh cadences.

## Why

- borsapy throughput limits live scans to ~5–25 min/full BIST.
- 5 modules each scanning their own universe duplicates work and locks
  borsapy bandwidth.
- `/api/bullwatch?refresh=true` historically blocked for 3–5 min on
  cold cache, blanking the page.

Shape of the fix: one canonical universe, one shared snapshot pipeline,
per-module sorted snapshots that endpoints read without ever triggering
a fetch.

## Universe Terminology

| Term | Count | Meaning |
|---|---:|---|
| `UNIVERSE_BIST30` | 30 | Large-caps |
| `UNIVERSE_EXTRA` | 78 | Mid-caps |
| `UNIVERSE_EXTENDED` | 406 | Long tail |
| `UNIVERSE` | 108 | Legacy alias = BIST30 + EXTRA (heatmap, watchlist, technical) |
| **`FULL_BIST`** | **437** | **dedup(BIST30 ∪ EXTRA ∪ EXTENDED) — canonical** |
| BullWatch scannable | 429 | dedup(EXTRA ∪ EXTENDED). BIST30 intentionally excluded — float-mcap caps make them ineligible. |
| BullWatch eligible | ~329 | Post free-float / market-cap / float-mcap sanity filter |
| Displayed | 50 | UI default `limit` |

The numbers 484, 429, 437, 329 are not interchangeable. Use the table.

## Key Schema

All keys live under the global Redis prefix `bb:` (from
`config.REDIS_KEY_PREFIX`), then `snapshots:{module}:…`.

```
snapshots:{module}:latest                       STRING (scan_id)        NO TTL
snapshots:{module}:previous                     STRING (scan_id)        NO TTL
snapshots:{module}:lock                         STRING (token)          600 s
snapshots:{module}:{scan_id}:meta               JSON                    7 days
snapshots:{module}:{scan_id}:score              ZSET ticker→score       7 days
snapshots:{module}:{scan_id}:items_set          SET of tickers          7 days
snapshots:{module}:{scan_id}:item:{TICKER}      JSON payload            7 days
```

Future spine layer (D.2) — schema reserved, not written by D.1:

```
market:symbol:{TICKER}:metrics                  HASH                    24 h
market:symbol:{TICKER}:ohlcv_meta               HASH                    24 h
market:symbol:{TICKER}:tech                     STRING (JSON)           24 h
```

`meta.schema_version` is `"d1"`. Readers reject mismatched versions.

## Write Flow (Atomic)

1. Generate `scan_id` — 13-digit ms timestamp + 8-char random suffix
   (`YYYYYYYYYYYYY-aaaaaaaa`). Sortable, collision-resistant.
2. Open Redis pipeline (`MULTI/EXEC`).
3. For every `(ticker, score, payload)`: `SET` item key + 7-day TTL.
4. `ZADD` score ZSET + `EXPIRE`.
5. `SADD` items_set manifest + `EXPIRE`.
6. `SET` meta JSON + `EXPIRE`.
7. `pipe.execute()` — atomic.
8. **Only on success**: read old `latest`, `SET latest = new scan_id`,
   `SET previous = old scan_id`.

Step 8 is the pointer swap. Until it lands, readers continue seeing the
old `latest`. A crash between step 7 and step 8 leaves a "ghost" scan
under its `scan_id` that will TTL away in 7 days — `cleanup_orphans()`
also reaps it explicitly.

## Read Flow

```
scan_id  = GET snapshots:{module}:latest
meta     = GET snapshots:{module}:{scan_id}:meta
top      = ZREVRANGE snapshots:{module}:{scan_id}:score 0 N-1 WITHSCORES
items    = MGET snapshots:{module}:{scan_id}:item:{T1}, {T2}, …
```

If any of those return None / fail integrity check:

1. Try `fallback_to_previous`: if `previous` snapshot is healthy, promote
   it to `latest` and re-read.
2. If previous is also broken or absent: cold-start path.

Integrity check (`is_healthy`):
- meta exists, parses, matches `SCHEMA_VERSION`
- score ZSET non-empty
- items_set non-empty
- sample (≤5) item keys exist via `MGET`

## Endpoint UX Contract (`/api/bullwatch`)

| Request | Snapshot state | Behavior | `cache_status` |
|---|---|---|---|
| any | snapshot fresh (< 30 min) | serve, no scan | `snapshot_hit` |
| `refresh=false` | snapshot stale | serve stale, schedule bg refresh | `stale_with_refresh` |
| `refresh=true` | snapshot exists | serve current + `refresh_scheduled=true`, **NON-BLOCKING** | `snapshot_with_refresh` |
| `refresh=true` | only in-mem mirror | serve mirror + schedule | `memory_with_refresh` |
| any | no snapshot, no mirror | cold-start (blocking) | `cold_start` |
| `cap_tl=N` or `diagnostic=true` | bypass everything | fresh scan, no cache touch | `experimental` |

Response `_meta` always carries:
- `from_snapshot: bool` — was data read from snapshot store
- `scan_id: str` — present when `from_snapshot=true`
- `refresh_scheduled: bool` — present only when a background scan was kicked
- `stale: bool` — present only when serving past `SOFT_MAX_AGE`

Existing top-level fields (`items`, `scanned`, `eligible_count`, `cap_tl`,
`near_misses`, `duration_ms`, `asof`) are unchanged.

## Refresh Cadence (D.1)

Single tier: `bullwatch_refresh_loop` in `engine/background_tasks.py`.

```
BULLWATCH_REFRESH_STARTUP_DELAY = 240   # s, after heatmap loop starts
BULLWATCH_REFRESH_INTERVAL      = 1800  # s (30 min)
BULLWATCH_RETRY_AFTER_ERROR     = 300   # s
```

Each tick calls `api.bullwatch._refresh_and_persist()` which is
idempotent — returns `None` immediately if a scan is already running.

D.3 will split this into hot/warm/cold tiers (5 min / 15 min / 30 min).

## Module Migration

### BullWatch (D.1, done)

- `api/bullwatch.py`: snapshot-first read; cold-start only when no
  snapshot or in-mem mirror exists.
- Background loop: `engine/background_tasks.bullwatch_refresh_loop`.
- In-mem `_CACHE` is a write-through mirror, not source of truth.

### BullAlpha / Radar / Fundamental (D.2, next)

Migration pattern:

1. Wherever the module currently runs `engine.X.scan(universe)` and
   stores results module-locally:
   ```python
   from core.snapshot_store import get_default_store
   scored = [(s.ticker, s.score, s.to_dict()) for s in results]
   get_default_store().write_snapshot("bullalpha", scored, meta={...})
   ```
2. Endpoint reads:
   ```python
   payload = read_snapshot_payload("bullalpha", limit=200)
   if payload is None:
       payload = await _cold_start_scan("bullalpha")
   ```
3. Add a refresh loop to `engine/background_tasks.py`.

The `SnapshotStore` API is module-agnostic — no changes needed in the
store itself, only callers.

### Fundamental, Momentum, Portfolio (later)

Same pattern. Module names are reserved: `fundamental`, `momentum`,
`portfolio`. Use these literally to keep key naming consistent.

## Out of Scope (D.1)

- Market spine layer (`market:symbol:*`) — schema reserved only.
- BullAlpha/Radar migration.
- Tiered refresh cadence.
- Scoring/threshold changes.
- Phase B engines.
- UI changes.

## Operations

### Inspect a snapshot

```bash
redis-cli GET   bb:snapshots:bullwatch:latest
redis-cli GET   bb:snapshots:bullwatch:{scan_id}:meta
redis-cli ZREVRANGE bb:snapshots:bullwatch:{scan_id}:score 0 9 WITHSCORES
redis-cli SMEMBERS bb:snapshots:bullwatch:{scan_id}:items_set
```

### Force a fresh snapshot

```bash
curl 'http://localhost:8080/api/bullwatch?refresh=true&limit=5'
# Returns current snapshot immediately + schedules background scan
```

### Clean up orphan snapshots

`SnapshotStore.cleanup_orphans(module)` — automatic on lifespan would
be a future improvement; for now run manually if Redis disk fills.

### Recover from corruption

Latest snapshot corrupt? `fallback_to_previous(module)` promotes the
previous pointer. The next refresh cycle overwrites both.

Both pointers gone? `redis-cli DEL bb:snapshots:bullwatch:latest
bb:snapshots:bullwatch:previous`, then the next request triggers a
cold-start scan.

## Tests

- `tests/test_snapshot_store.py` — 31 unit tests of the generic store.
- `tests/test_bullwatch_snapshot.py` — 14 integration tests
  (persist/read/round-trip, fallback, cold-start, idempotent refresh).
- `tests/test_bullwatch_refresh_contract.py` — 10 contract tests
  enforcing the UX guarantees in §"Endpoint UX Contract".

All three suites use `tests/_fake_redis.py` (in-process Redis stub).
No real Redis required.
