# Statusline Context Meter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A two-line Claude Code status line showing a free-context battery bar, occupied tokens (color-coded by absolute thresholds), model/window, folder, git branch, cost, and session time — using Nerd Font glyphs.

**Architecture:** A single Python 3 script reads the session JSON from stdin and prints two ANSI-colored lines. Pure helper functions (formatting, coloring, bar, git) are unit-tested via stdlib `unittest`; `main()` wires stdin→render→stdout. Wired into `~/.claude/settings.json` under `statusLine`.

**Tech Stack:** Python 3 (stdlib only — `json`, `subprocess`, `os`, `sys`, `unittest`). No `jq`, no pip installs.

**Environment note:** Neither `~/.claude/` nor the `ContextPlugin` working dir is a git repository, so there are **no commit steps** — each task ends by running tests. (The plan/spec docs live under `ContextPlugin/docs/` and are unversioned unless the user runs `git init`.)

**Files:**
- Create: `~/.claude/statusline.py` — the status line script (helpers + `main`).
- Create: `~/.claude/test_statusline.py` — unittest tests importing the helpers.
- Modify: `~/.claude/settings.json` — add the `statusLine` key.

**Glyph constants (Nerd Font, confirmed rendering in Ghostty):**
`FOLDER=""`, `GIT=""`, `CTX=""` (database), `CLOCK=""`.

**ANSI constants:**
`RESET="\033[0m"`, `DIM="\033[2m"`, `CYAN="\033[1;36m"`, `WHITE="\033[97m"`, `YELLOW="\033[33m"`, `RED="\033[31m"`.

---

### Task 1: Token formatting helper

**Files:**
- Create: `~/.claude/statusline.py`
- Create: `~/.claude/test_statusline.py`

- [ ] **Step 1: Write the failing test**

In `~/.claude/test_statusline.py`:
```python
import unittest
import statusline as s


class TestFmtTokens(unittest.TestCase):
    def test_under_1000_raw(self):
        self.assertEqual(s.fmt_tokens(0), "0")
        self.assertEqual(s.fmt_tokens(823), "823")

    def test_thousands_rounded_k(self):
        self.assertEqual(s.fmt_tokens(187000), "187k")
        self.assertEqual(s.fmt_tokens(1500), "2k")
        self.assertEqual(s.fmt_tokens(47483), "47k")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/.claude && python3 -m unittest test_statusline -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'statusline'` (or `AttributeError: fmt_tokens`).

- [ ] **Step 3: Write minimal implementation**

Create `~/.claude/statusline.py`:
```python
#!/usr/bin/env python3
"""Claude Code status line: free-context battery + occupied tokens."""


def fmt_tokens(n):
    n = int(n or 0)
    if n < 1000:
        return str(n)
    return f"{round(n / 1000)}k"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/.claude && python3 -m unittest test_statusline -v`
Expected: PASS (3 tests).

---

### Task 2: Duration and window formatting

**Files:**
- Modify: `~/.claude/statusline.py`
- Test: `~/.claude/test_statusline.py`

- [ ] **Step 1: Write the failing test**

Append to `test_statusline.py`:
```python
class TestFmtDuration(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(s.fmt_duration(0), "0s")
        self.assertEqual(s.fmt_duration(45000), "45s")

    def test_minutes(self):
        self.assertEqual(s.fmt_duration(423000), "7m 3s")

    def test_hours(self):
        self.assertEqual(s.fmt_duration(3_661_000), "1h 1m")


class TestFmtWindow(unittest.TestCase):
    def test_window(self):
        self.assertEqual(s.fmt_window(1000000), "1M")
        self.assertEqual(s.fmt_window(200000), "200k")
        self.assertEqual(s.fmt_window(0), "?")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/.claude && python3 -m unittest test_statusline -v`
Expected: FAIL — `AttributeError: module 'statusline' has no attribute 'fmt_duration'`.

- [ ] **Step 3: Write minimal implementation**

Add to `statusline.py`:
```python
def fmt_duration(ms):
    secs = int((ms or 0) / 1000)
    h, rem = divmod(secs, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def fmt_window(size):
    size = int(size or 0)
    if size >= 1_000_000:
        return f"{round(size / 1_000_000)}M"
    if size >= 1000:
        return f"{round(size / 1000)}k"
    return "?"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/.claude && python3 -m unittest test_statusline -v`
Expected: PASS (all tests so far).

---

### Task 3: Threshold color + battery bar

**Files:**
- Modify: `~/.claude/statusline.py`
- Test: `~/.claude/test_statusline.py`

- [ ] **Step 1: Write the failing test**

Append to `test_statusline.py`:
```python
class TestColorForTokens(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(s.color_for_tokens(0), s.WHITE)
        self.assertEqual(s.color_for_tokens(200000), s.WHITE)
        self.assertEqual(s.color_for_tokens(200001), s.YELLOW)
        self.assertEqual(s.color_for_tokens(400000), s.YELLOW)
        self.assertEqual(s.color_for_tokens(400001), s.RED)


class TestBatteryBar(unittest.TestCase):
    def test_fill_count(self):
        # 10 segments; filled = round(remaining/10)
        self.assertEqual(s.battery_bar(100).count("▰"), 10)
        self.assertEqual(s.battery_bar(0).count("▰"), 0)
        self.assertEqual(s.battery_bar(58).count("▰"), 6)
        self.assertEqual(s.battery_bar(58).count("▱"), 4)

    def test_null_remaining_treated_full(self):
        self.assertEqual(s.battery_bar(None).count("▰"), 10)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/.claude && python3 -m unittest test_statusline -v`
Expected: FAIL — missing `color_for_tokens` / `battery_bar` / `WHITE`.

- [ ] **Step 3: Write minimal implementation**

Add near the top of `statusline.py` (constants) and the functions:
```python
RESET = "\033[0m"
DIM = "\033[2m"
CYAN = "\033[1;36m"
WHITE = "\033[97m"
YELLOW = "\033[33m"
RED = "\033[31m"


def color_for_tokens(n):
    n = int(n or 0)
    if n <= 200000:
        return WHITE
    if n <= 400000:
        return YELLOW
    return RED


def battery_bar(remaining_pct):
    pct = 100 if remaining_pct is None else int(remaining_pct)
    filled = round(pct / 10)
    filled = max(0, min(10, filled))
    return "▰" * filled + "▱" * (10 - filled)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/.claude && python3 -m unittest test_statusline -v`
Expected: PASS.

---

### Task 4: Git branch segment

**Files:**
- Modify: `~/.claude/statusline.py`
- Test: `~/.claude/test_statusline.py`

- [ ] **Step 1: Write the failing test**

Append to `test_statusline.py`:
```python
import os
import tempfile
import subprocess


class TestGitSegment(unittest.TestCase):
    def test_non_repo_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(s.git_segment(d))

    def test_missing_dir_returns_none(self):
        self.assertIsNone(s.git_segment("/no/such/path/xyz"))

    def test_repo_returns_branch(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", "-b", "main", d], check=True)
            seg = s.git_segment(d)
            self.assertIsNotNone(seg)
            self.assertIn("main", seg)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/.claude && python3 -m unittest test_statusline -v`
Expected: FAIL — missing `git_segment`.

- [ ] **Step 3: Write minimal implementation**

Add to `statusline.py` (with `import subprocess` at top):
```python
import subprocess


def _git(cwd, args):
    try:
        out = subprocess.run(
            ["git", "-C", cwd] + args,
            capture_output=True, text=True, timeout=1,
        )
        return out
    except (OSError, subprocess.SubprocessError):
        return None


def git_segment(cwd):
    """Return 'branch[*]' string, or None when cwd is not a git repo."""
    res = _git(cwd, ["branch", "--show-current"])
    if res is None or res.returncode != 0:
        return None
    branch = res.stdout.strip()
    if not branch:
        return None
    dirty = ""
    diff = _git(cwd, ["diff-index", "--quiet", "HEAD", "--"])
    if diff is not None and diff.returncode == 1:
        dirty = "*"
    return f"{branch}{dirty}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/.claude && python3 -m unittest test_statusline -v`
Expected: PASS.

---

### Task 5: Render the two lines

**Files:**
- Modify: `~/.claude/statusline.py`
- Test: `~/.claude/test_statusline.py`

- [ ] **Step 1: Write the failing test**

Append to `test_statusline.py`:
```python
class TestRender(unittest.TestCase):
    def _data(self, **ctx):
        base = {
            "model": {"display_name": "Opus 4.8"},
            "workspace": {"current_dir": "/x/ContextPlugin"},
            "context_window": {
                "context_window_size": 1000000,
                "remaining_percentage": 81,
                "total_input_tokens": 187000,
            },
            "cost": {"total_cost_usd": 0.08, "total_duration_ms": 423000},
        }
        base["context_window"].update(ctx)
        return base

    def test_two_lines(self):
        out = s.render(self._data())
        self.assertEqual(len(out.split("\n")), 2)

    def test_line1_has_model_window_folder(self):
        out = s.render(self._data())
        line1 = out.split("\n")[0]
        self.assertIn("Opus 4.8", line1)
        self.assertIn("1M", line1)
        self.assertIn("ContextPlugin", line1)

    def test_line2_has_pct_tokens_cost_time(self):
        out = s.render(self._data())
        line2 = out.split("\n")[1]
        self.assertIn("81%", line2)
        self.assertIn("187k", line2)
        self.assertIn("$0.08", line2)
        self.assertIn("7m 3s", line2)

    def test_color_white_under_200k(self):
        out = s.render(self._data(total_input_tokens=187000))
        self.assertIn(s.WHITE, out)

    def test_color_yellow_300k(self):
        out = s.render(self._data(total_input_tokens=300000))
        self.assertIn(s.YELLOW, out)

    def test_color_red_over_400k(self):
        out = s.render(self._data(total_input_tokens=500000))
        self.assertIn(s.RED, out)

    def test_null_percentage_full_bar(self):
        out = s.render(self._data(remaining_percentage=None))
        self.assertIn("100%", out)

    def test_no_branch_when_not_repo(self):
        # /x/ContextPlugin doesn't exist -> no branch glyph segment
        out = s.render(self._data())
        self.assertNotIn(s.GIT, out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/.claude && python3 -m unittest test_statusline -v`
Expected: FAIL — missing `render` / glyph constants `GIT`.

- [ ] **Step 3: Write minimal implementation**

Add glyph constants near the other constants and the `render` function:
```python
import os

FOLDER = ""
GIT = ""
CTX = ""
CLOCK = ""
SEP = f" {DIM}│{RESET} "


def render(data):
    cw = data.get("context_window") or {}
    model = (data.get("model") or {}).get("display_name", "Claude")
    cwd = (data.get("workspace") or {}).get("current_dir") or data.get("cwd") or "."
    folder = os.path.basename(cwd.rstrip("/")) or cwd
    window = fmt_window(cw.get("context_window_size"))

    remaining = cw.get("remaining_percentage")
    tokens = cw.get("total_input_tokens") or 0
    color = color_for_tokens(tokens)

    cost = (data.get("cost") or {}).get("total_cost_usd") or 0
    duration = (data.get("cost") or {}).get("total_duration_ms") or 0

    # Line 1
    line1 = f"{CYAN}[{model} · {window}]{RESET}"
    line1 += f"  {FOLDER} {folder}"
    branch = git_segment(cwd)
    if branch:
        line1 += f"{SEP}{GIT} {branch}"

    # Line 2
    pct = 100 if remaining is None else int(remaining)
    bar = battery_bar(remaining)
    line2 = f"{color}{bar} {pct}%{RESET}"
    line2 += f"{SEP}{color}{CTX} {fmt_tokens(tokens)}{RESET}"
    line2 += f"{SEP}{DIM}${cost:.2f}{RESET}"
    line2 += f"{SEP}{DIM}{CLOCK} {fmt_duration(duration)}{RESET}"

    return line1 + "\n" + line2
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/.claude && python3 -m unittest test_statusline -v`
Expected: PASS (all tests).

---

### Task 6: stdin entry point + manual smoke test

**Files:**
- Modify: `~/.claude/statusline.py`

- [ ] **Step 1: Add `main()` and the `__main__` guard**

Append to `statusline.py`:
```python
import sys
import json


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}
    sys.stdout.write(render(data) + "\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Re-run the full test suite (no regressions)**

Run: `cd ~/.claude && python3 -m unittest test_statusline -v`
Expected: PASS (all tests).

- [ ] **Step 3: Smoke test each color band via mock stdin**

Run (white ≤200k):
```bash
echo '{"model":{"display_name":"Opus 4.8"},"workspace":{"current_dir":"/x/ContextPlugin"},"context_window":{"context_window_size":1000000,"remaining_percentage":81,"total_input_tokens":187000},"cost":{"total_cost_usd":0.08,"total_duration_ms":423000}}' | python3 ~/.claude/statusline.py
```
Expected: two lines; `[Opus 4.8 · 1M]  <folder glyph> ContextPlugin`; bar + `81%`, `<db glyph> 187k`, `$0.08`, `<clock glyph> 7m 3s` — all in white.

Run (yellow 300k) — change `total_input_tokens` to `300000`: tokens + bar render yellow.
Run (red 500k) — change `total_input_tokens` to `500000`: tokens + bar render red.
Run (git branch) — change `current_dir` to a real repo path (e.g. a temp `git init` dir): branch segment appears on line 1.

---

### Task 7: Wire into settings.json

**Files:**
- Modify: `~/.claude/settings.json`

- [ ] **Step 1: Back up settings**

Run: `cp ~/.claude/settings.json ~/.claude/settings.json.bak`

- [ ] **Step 2: Add the `statusLine` key**

Add this top-level key to `~/.claude/settings.json` (keep existing keys intact):
```json
"statusLine": {
  "type": "command",
  "command": "python3 ~/.claude/statusline.py"
}
```

- [ ] **Step 3: Validate JSON**

Run: `python3 -c "import json; json.load(open('$HOME/.claude/settings.json')); print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Verify live**

The status line updates on the next render tick. Confirm the two-line bar appears under the input box with the battery + token color matching the current session. (Cleanup: `rm ~/glyphtest.sh` — the earlier render-test scratch file.)

---

## Self-Review

**Spec coverage:**
- No `src` label / real folder basename → Task 5 (`os.path.basename`). ✓
- Battery bar of free % → Tasks 3, 5. ✓
- Occupied tokens color-coded ≤200k white / 200–400k yellow / >400k red → Tasks 3, 5 (and battery shares the color). ✓
- Nerd Font glyphs → Task 5 constants. ✓
- Model + window, folder, git branch (repo-only), cost, duration → Tasks 2, 4, 5. ✓
- python3, no jq → all tasks. ✓
- Null `remaining_percentage`/`current_usage`, non-repo, formatting edge cases → Tasks 1–5 tests. ✓
- Wiring into settings.json → Task 7. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✓

**Type consistency:** `fmt_tokens`, `fmt_duration`, `fmt_window`, `color_for_tokens`, `battery_bar`, `git_segment`, `render`, `main` — names and signatures consistent across tasks and tests. Constants `WHITE/YELLOW/RED/CYAN/DIM/RESET/FOLDER/GIT/CTX/CLOCK/SEP` defined once in Tasks 3 & 5, referenced consistently. ✓
