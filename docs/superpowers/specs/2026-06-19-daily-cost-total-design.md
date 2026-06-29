# Daily Cost Total — Design

**Date:** 2026-06-19
**Status:** Approved for planning

> **Amendment 2026-06-29:** The original "self-maintained state file" stored each
> session's full cumulative cost stamped with the date of its last render. A
> long-running session kept open across midnight (or simply idle while
> `refreshInterval` re-rendered it) re-stamped its entry to the new day and
> re-counted its entire prior spend — inflating "today" by yesterday's costs.
> Fixed by switching to **per-day delta attribution**: the state is now
> `{"days": {date: total}, "sessions": {sid: last_cost}}`, and each render adds
> only the delta in a session's cumulative cost to the current day's bucket. The
> legacy format migrates automatically (old costs become baselines). This
> supersedes the "session spanning midnight" limitation below. See the README
> "Daily cost total" section for the current behavior.

## Goal

Show the **total daily spend across all sessions on the machine** next to the existing per-session cost in line 2 of the status line. The user wants, in the cost segment, an additional `(day)` figure:

```
$0.08 · $5.55 (day)
```

`$0.08` is the current session cost (unchanged, from `cost.total_cost_usd`); `$5.55 (day)` is the summed cost of every session that ran **today** (user's local calendar day).

The crux: Claude Code's status line JSON provides only the **current session's** cost — there is no daily total. We must aggregate it ourselves across sessions.

## Appearance

Line 2 cost segment changes from:
```
… │  $0.08  │ …
```
to:
```
… │  $0.08 · $5.55 (day)  │ …
```

- Both figures are **dim**, like the current cost.
- Session and day are joined by a dim ` · ` inside one segment (not a new `│`-separated segment), keeping line width tight.
- The session figure stays unlabeled (understood by default); the daily figure carries the ` (day)` suffix to disambiguate.
- The `(day)` figure is **always shown**, even when it is `$0.00` or equals the session cost, for visual consistency.

## Data source — self-maintained state file

A small JSON file persists per-session costs keyed by `session_id`, stamped with the local date. Each render updates the current session's entry and re-sums today's entries.

**File location:** `<config_dir>/statusline_cost.json`, where `<config_dir>` is `os.environ["CLAUDE_CONFIG_DIR"]` if set, else `~/.claude`. (Respecting `CLAUDE_CONFIG_DIR` matters because the state file must live alongside whatever config dir Claude Code actually uses; otherwise totals would be split across files.)

**Structure:**
```json
{
  "<session_id>": {"date": "2026-06-19", "cost": 5.55},
  "<session_id>": {"date": "2026-06-19", "cost": 0.08}
}
```

**Per-render algorithm:**
1. Read the state file. Missing file or malformed JSON → start from `{}` (never raise).
2. `state[session_id] = {"date": today, "cost": session_cost}`, where `today = datetime.now().strftime("%Y-%m-%d")` (local time) and `session_cost = cost.total_cost_usd`.
3. Drop every entry whose `date != today` (file stays tiny; this is also what resets the daily total at local midnight).
4. Daily total = sum of `cost` over the remaining entries.
5. Write the state back **atomically**: write to a temp file in the same dir, then `os.replace` onto the target.

## Testability

The aggregation is a **pure function**, tested without touching the filesystem:

```
update_daily_cost(state: dict, session_id: str, date: str, cost: float) -> (new_state: dict, daily_total: float)
```

It performs steps 2–4 above. A thin I/O wrapper (`load_state` / `save_state`) handles file read/write/atomic-replace and corrupt-file fallback; `render` calls the wrapper then the pure function. This keeps date/clock and filesystem at the edges so the core logic is deterministic in tests.

## Field reference (verified against Claude Code statusline schema)

Confirmed against official docs (`code.claude.com/docs/en/statusline.md`):
- `session_id` — top-level string, **always present**.
- `cost.total_cost_usd` — cumulative cost for the whole session (accumulates over its lifetime).

## Edge cases / fallbacks

- **Corrupt / missing state file** → treat as `{}`. The status line must never crash on cost state.
- **No `session_id` in JSON** (shouldn't happen per docs, but defensive) → skip the daily segment entirely; the rest of line 2 renders normally. Do not write state.
- **Concurrent writers** (multiple terminals): atomic `os.replace` guarantees no torn/corrupt reads. A race can cause one render to overwrite another session's just-written entry with a stale value; that session's next render (≤ `refreshInterval`, default 30s) self-heals it. The only un-healed loss is a session's final ~30s delta if its last render races — negligible, and we accept it rather than add file locking.
- **Session spanning local midnight / resumed on a later day**: `total_cost_usd` is cumulative for the whole session, so on the new day its full lifetime cost is attributed to that new day. Known minor limitation; documented in README. Not worth correcting in v1.
- **Idle session after midnight without `refreshInterval`**: the displayed daily figure may show the prior day until the next render event. Inherent to event-driven rendering; `refreshInterval` already mitigates it.

## Out of scope (YAGNI)

- Per-project daily totals (user chose machine-wide).
- Reading/parsing JSONL transcripts or shelling out to `ccusage` (rejected: slower, external deps; conflicts with the repo's stdlib-only, per-render-fast philosophy).
- Color-coding the daily figure by a budget threshold — dim only, matching session cost.
- Multi-day history / weekly/monthly rollups.
- File locking for the state file (self-healing is sufficient).

## Testing

Extend `test_statusline.py`:
- `update_daily_cost`: new session added; existing session cost updated (not double-counted); stale-date entries pruned; sum across multiple today-entries; empty state.
- `load_state`: missing file → `{}`; corrupt JSON → `{}`.
- `save_state` + `load_state` round-trip via a temp dir.
- `render`: daily segment present and formatted `$X.XX (day)`; daily segment omitted when `session_id` absent.
