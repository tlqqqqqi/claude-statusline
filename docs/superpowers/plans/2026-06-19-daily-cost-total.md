# Daily Cost Total Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show machine-wide total spend for the current local day next to the per-session cost in line 2 of the status line, as `$0.08 · $5.55 (day)`.

**Architecture:** A self-maintained JSON state file keyed by `session_id` persists each session's cumulative cost stamped with its local date. On every render the current session's entry is upserted, stale-date entries are pruned, and today's entries are summed. A pure aggregation function holds the logic; thin I/O helpers keep the filesystem and clock at the edges.

**Tech Stack:** Python 3 standard library only (`json`, `os`, `tempfile`, `datetime`). No external dependencies. `unittest` for tests.

## Global Constraints

- **Stdlib only** — no third-party packages, no `jq`, no shelling out to `ccusage`.
- **Never crash** — a malformed/missing state file or a failed write must degrade gracefully; the status line must always print.
- **State file path** — `<config_dir>/statusline_cost.json` where `<config_dir>` is `os.environ["CLAUDE_CONFIG_DIR"]` if set, else `~/.claude`.
- **Local date** — "today" is `datetime.now().strftime("%Y-%m-%d")` (user's local time).
- **Cost format** — `${value:.2f}`, matching the existing session-cost format.
- **Daily figure always shown** when a `session_id` is present, even if `$0.00`.
- All code lives in the existing `statusline.py`; tests in the existing `test_statusline.py`.

---

## File Structure

- Modify: `statusline.py` — add top-level imports (`json`, `datetime`), four new functions (`_state_path`, `load_state`, `save_state`, `update_daily_cost`, `daily_total_for`), and edit the cost segment in `render`.
- Modify: `test_statusline.py` — add test classes for the new functions and the render integration.
- Modify: `README.md` — document the daily total and the midnight limitation.

---

### Task 1: Pure aggregation function `update_daily_cost`

**Files:**
- Modify: `statusline.py` (add `update_daily_cost`)
- Test: `test_statusline.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `update_daily_cost(state: dict, session_id: str, date: str, cost: float) -> (new_state: dict, daily_total: float)`. Returns a new state dict containing only entries whose `date` equals the given `date` (with `session_id` upserted to `{"date": date, "cost": cost}`), and the sum of all `cost` values in that new state.

- [ ] **Step 1: Write the failing tests**

Add to `test_statusline.py`:

```python
class TestUpdateDailyCost(unittest.TestCase):
    def test_adds_new_session(self):
        state, total = s.update_daily_cost({}, "sid1", "2026-06-19", 0.08)
        self.assertEqual(state, {"sid1": {"date": "2026-06-19", "cost": 0.08}})
        self.assertAlmostEqual(total, 0.08)

    def test_updates_existing_session_not_double_counted(self):
        start = {"sid1": {"date": "2026-06-19", "cost": 0.08}}
        state, total = s.update_daily_cost(start, "sid1", "2026-06-19", 0.20)
        self.assertEqual(state["sid1"]["cost"], 0.20)
        self.assertAlmostEqual(total, 0.20)

    def test_sums_multiple_today_sessions(self):
        start = {"sid1": {"date": "2026-06-19", "cost": 5.47}}
        state, total = s.update_daily_cost(start, "sid2", "2026-06-19", 0.08)
        self.assertAlmostEqual(total, 5.55)
        self.assertEqual(len(state), 2)

    def test_prunes_stale_dates(self):
        start = {
            "old": {"date": "2026-06-18", "cost": 9.99},
            "sid1": {"date": "2026-06-19", "cost": 1.00},
        }
        state, total = s.update_daily_cost(start, "sid2", "2026-06-19", 0.50)
        self.assertNotIn("old", state)
        self.assertAlmostEqual(total, 1.50)

    def test_ignores_malformed_records(self):
        start = {"bad": "not-a-dict", "sid1": {"date": "2026-06-19", "cost": 1.0}}
        state, total = s.update_daily_cost(start, "sid2", "2026-06-19", 0.50)
        self.assertNotIn("bad", state)
        self.assertAlmostEqual(total, 1.50)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_statusline.TestUpdateDailyCost -v`
Expected: FAIL with `AttributeError: module 'statusline' has no attribute 'update_daily_cost'`

- [ ] **Step 3: Write minimal implementation**

Add to `statusline.py` (place near the other helpers, e.g. after `fmt_window`):

```python
def update_daily_cost(state, session_id, date, cost):
    new_state = {
        sid: rec
        for sid, rec in state.items()
        if isinstance(rec, dict) and rec.get("date") == date
    }
    new_state[session_id] = {"date": date, "cost": cost}
    total = sum((rec.get("cost") or 0) for rec in new_state.values())
    return new_state, total
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_statusline.TestUpdateDailyCost -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add statusline.py test_statusline.py
git commit -m "feat: add pure daily-cost aggregation function"
```

---

### Task 2: State file I/O (`_state_path`, `load_state`, `save_state`)

**Files:**
- Modify: `statusline.py` (add top-level `import json`; add `_state_path`, `load_state`, `save_state`)
- Test: `test_statusline.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `_state_path() -> str` — `<CLAUDE_CONFIG_DIR or ~/.claude>/statusline_cost.json`.
  - `load_state(path: str) -> dict` — parsed dict, or `{}` on missing file, unreadable file, invalid JSON, or non-dict top level.
  - `save_state(path: str, state: dict) -> None` — atomically writes `state` as JSON (temp file in the same dir + `os.replace`); creates the dir if missing.

- [ ] **Step 1: Write the failing tests**

Add to `test_statusline.py`:

```python
import os


class TestStateIO(unittest.TestCase):
    def test_load_missing_file_returns_empty(self):
        self.assertEqual(s.load_state("/no/such/path/state.json"), {})

    def test_load_corrupt_json_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            with open(p, "w") as f:
                f.write("{not json")
            self.assertEqual(s.load_state(p), {})

    def test_load_non_dict_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            with open(p, "w") as f:
                f.write("[1, 2, 3]")
            self.assertEqual(s.load_state(p), {})

    def test_save_then_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            data = {"sid1": {"date": "2026-06-19", "cost": 5.55}}
            s.save_state(p, data)
            self.assertEqual(s.load_state(p), data)

    def test_save_creates_missing_dir(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "nested", "state.json")
            s.save_state(p, {"a": 1})
            self.assertTrue(os.path.exists(p))

    def test_state_path_respects_env(self):
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = "/tmp/cfgdir"
        try:
            self.assertEqual(s._state_path(), "/tmp/cfgdir/statusline_cost.json")
        finally:
            if old is None:
                del os.environ["CLAUDE_CONFIG_DIR"]
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_statusline.TestStateIO -v`
Expected: FAIL with `AttributeError: module 'statusline' has no attribute 'load_state'`

- [ ] **Step 3: Write minimal implementation**

At the top of `statusline.py`, add to the imports (next to `import os`):

```python
import json
import tempfile
from datetime import datetime
```

Add these functions to `statusline.py` (after `update_daily_cost`):

```python
def _state_path():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return os.path.join(base, "statusline_cost.json")


def load_state(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path, state):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".statusline_cost.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

Note: `import json` is also referenced inside `main()`; the new top-level import makes the inner one redundant but harmless. Leave `main()` as-is to keep this task's diff minimal.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_statusline.TestStateIO -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add statusline.py test_statusline.py
git commit -m "feat: add atomic state-file load/save for cost tracking"
```

---

### Task 3: Orchestrator + render integration

**Files:**
- Modify: `statusline.py` (add `daily_total_for`; edit the cost segment in `render`)
- Test: `test_statusline.py`

**Interfaces:**
- Consumes: `update_daily_cost`, `load_state`, `save_state`, `_state_path` (Tasks 1–2); `datetime` import.
- Produces: `daily_total_for(data: dict, now=None) -> float | None`. Returns `None` when `data` has no truthy `session_id` (caller omits the daily segment). Otherwise records this session's cost into the state file and returns today's total. `now` is an optional `datetime` for tests; defaults to `datetime.now()`. A failed save does not prevent returning the in-memory total.

- [ ] **Step 1: Write the failing tests**

Add to `test_statusline.py`:

```python
class TestDailyTotalFor(unittest.TestCase):
    def _isolated_env(self, d):
        # Point the state file at a temp dir for the duration of a test.
        os.environ["CLAUDE_CONFIG_DIR"] = d

    def test_none_without_session_id(self):
        self.assertIsNone(s.daily_total_for({"cost": {"total_cost_usd": 1.0}}))

    def test_records_and_returns_total(self):
        from datetime import datetime
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("CLAUDE_CONFIG_DIR")
            self._isolated_env(d)
            try:
                now = datetime(2026, 6, 19, 12, 0, 0)
                t1 = s.daily_total_for(
                    {"session_id": "a", "cost": {"total_cost_usd": 5.47}}, now=now
                )
                self.assertAlmostEqual(t1, 5.47)
                t2 = s.daily_total_for(
                    {"session_id": "b", "cost": {"total_cost_usd": 0.08}}, now=now
                )
                self.assertAlmostEqual(t2, 5.55)
            finally:
                if old is None:
                    os.environ.pop("CLAUDE_CONFIG_DIR", None)
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = old


class TestRenderDaily(unittest.TestCase):
    def _data(self):
        return {
            "session_id": "sid-x",
            "model": {"display_name": "Opus 4.8"},
            "workspace": {"current_dir": "/x/ContextPlugin"},
            "context_window": {
                "context_window_size": 1000000,
                "remaining_percentage": 81,
                "total_input_tokens": 187000,
            },
            "cost": {"total_cost_usd": 0.08, "total_duration_ms": 423000},
        }

    def test_daily_segment_present_and_formatted(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("CLAUDE_CONFIG_DIR")
            os.environ["CLAUDE_CONFIG_DIR"] = d
            try:
                line2 = s.render(self._data()).split("\n")[1]
                self.assertIn("$0.08", line2)
                self.assertIn("(day)", line2)
                self.assertIn("·", line2.split("$0.08")[1])
            finally:
                if old is None:
                    os.environ.pop("CLAUDE_CONFIG_DIR", None)
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = old

    def test_daily_segment_omitted_without_session_id(self):
        data = self._data()
        del data["session_id"]
        line2 = s.render(data).split("\n")[1]
        self.assertIn("$0.08", line2)
        self.assertNotIn("(day)", line2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_statusline.TestDailyTotalFor test_statusline.TestRenderDaily -v`
Expected: FAIL — `TestDailyTotalFor` with `AttributeError: ... 'daily_total_for'`; `TestRenderDaily.test_daily_segment_present_and_formatted` with an assertion error (no `(day)` yet).

- [ ] **Step 3: Write minimal implementation**

Add `daily_total_for` to `statusline.py` (after `save_state`):

```python
def daily_total_for(data, now=None):
    session_id = data.get("session_id")
    if not session_id:
        return None
    cost = (data.get("cost") or {}).get("total_cost_usd") or 0
    today = (now or datetime.now()).strftime("%Y-%m-%d")
    path = _state_path()
    state = load_state(path)
    state, total = update_daily_cost(state, session_id, today, float(cost))
    try:
        save_state(path, state)
    except OSError:
        pass
    return total
```

In `render`, replace the current cost line:

```python
    line2 += f"{SEP}{DIM}${cost:.2f}{RESET}"
```

with:

```python
    cost_seg = f"{DIM}${cost:.2f}"
    daily = daily_total_for(data)
    if daily is not None:
        cost_seg += f" · ${daily:.2f} (day)"
    cost_seg += RESET
    line2 += f"{SEP}{cost_seg}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_statusline.TestDailyTotalFor test_statusline.TestRenderDaily -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `python3 -m unittest test_statusline -v`
Expected: PASS — all tests, including the pre-existing `TestRender` (its `_data()` has no `session_id`, so it does no file I/O and still sees `$0.08`).

- [ ] **Step 6: Commit**

```bash
git add statusline.py test_statusline.py
git commit -m "feat: show machine-wide daily cost total in status line"
```

---

### Task 4: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: behavior from Tasks 1–3.
- Produces: nothing (docs only).

- [ ] **Step 1: Update the example and Line 2 description**

In `README.md`, update the example block so the cost shows the daily figure:

```
[Opus 4.8 (1M) · high]   ContextPlugin  │   feature/auth
▰▰▰▰▰▰▰▰▱▱ 81%  │   187k  │  $0.08 · $5.55 (day)  │   7m 3s
```

Update the **Line 2** bullet to mention the daily total. Change:

> **Line 2** — battery bar of *free* context (`remaining_percentage`), occupied tokens (`total_input_tokens`), session cost, and session duration.

to:

> **Line 2** — battery bar of *free* context (`remaining_percentage`), occupied tokens (`total_input_tokens`), cost (`$session · $today (day)`), and session duration.

- [ ] **Step 2: Add a "Daily cost total" subsection**

Add this after the "Color thresholds" section in `README.md`:

```markdown
### Daily cost total

`$5.55 (day)` is the summed cost of **every** Claude Code session on this
machine for the current local day. Claude Code's status line input only
reports the *current* session's cost, so the script aggregates it itself:
each render upserts the session's cost into a small state file
(`$CLAUDE_CONFIG_DIR/statusline_cost.json`, default `~/.claude/`), keyed by
`session_id` and stamped with the local date. Entries from previous days are
pruned automatically, which also resets the total at local midnight.

**Limitation:** a session that spans local midnight (or is resumed on a later
day) has its full cumulative cost attributed to the day of the next render,
since Claude Code reports only a session-lifetime total.
```

- [ ] **Step 2b: Mention the new state file in Requirements/notes**

Add a sentence to the README (e.g. end of the Install section) so users know a file is written:

```markdown
The script writes a small state file at `$CLAUDE_CONFIG_DIR/statusline_cost.json`
(default `~/.claude/statusline_cost.json`) to track the daily cost total. Add it
to your ignore lists if needed; delete it any time to reset the running total.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document daily cost total and state file"
```

---

## Self-Review

**Spec coverage:**
- Appearance `$0.08 · $5.55 (day)`, dim, single segment → Task 3 render edit. ✓
- Self-maintained state file, `CLAUDE_CONFIG_DIR`-aware path, structure → Task 2. ✓
- Per-render algorithm (upsert, prune, sum, atomic write) → Tasks 1 (pure) + 2 (atomic save) + 3 (orchestration). ✓
- Pure `update_daily_cost` signature → Task 1 matches spec exactly. ✓
- Edge: corrupt/missing file → `{}` → Task 2 tests. ✓
- Edge: no `session_id` → omit segment, no write → Task 3 `daily_total_for` returns `None`, render omits. ✓
- Edge: concurrent writers → atomic `os.replace` in Task 2 `save_state`. ✓
- Edge: midnight/resume limitation → documented in Task 4. ✓
- "Always shown when session present, even $0.00" → render adds segment whenever `daily is not None`. ✓
- Testing list from spec → Tasks 1–3 cover update/load/save/round-trip/render-present/render-absent. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; every command shows expected output. ✓

**Type consistency:** `update_daily_cost(state, session_id, date, cost) -> (new_state, total)` used identically in Task 1 and consumed in Task 3's `daily_total_for`. `_state_path`/`load_state`/`save_state` signatures consistent across Tasks 2–3. ✓
