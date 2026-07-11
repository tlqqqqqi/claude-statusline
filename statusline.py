#!/usr/bin/env python3
"""Claude Code status line: free-context battery + occupied tokens."""

import glob
import os
import subprocess
import json
import tempfile
import time
from datetime import datetime

RESET = "\033[0m"
DIM = "\033[2m"
CYAN = "\033[1;36m"
WHITE = "\033[97m"
YELLOW = "\033[33m"
RED = "\033[31m"

# Nerd Font glyphs (confirmed rendering in Ghostty)
FOLDER = ""
GIT = ""
CTX = ""
CLOCK = ""
SEP = f" {DIM}│{RESET} "


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


def _git(cwd, args):
    try:
        return subprocess.run(
            ["git", "-C", cwd] + args,
            capture_output=True, text=True, timeout=1,
        )
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
    effort = (data.get("effort") or {}).get("level")

    # Line 1
    head = f"{model} ({window})"
    if effort:
        head += f" · {effort}"
    line1 = f"{CYAN}[{head}]{RESET}"
    line1 += f"  {FOLDER} {folder}"
    branch = git_segment(cwd)
    if branch:
        line1 += f"{SEP}{GIT} {branch}"

    # Line 2
    pct = 100 if remaining is None else int(remaining)
    bar = battery_bar(remaining)
    line2 = f"{color}{bar} {pct}%{RESET}"
    line2 += f"{SEP}{color}{CTX} {fmt_tokens(tokens)}{RESET}"
    cost_seg = f"{DIM}${cost:.2f}"
    daily = daily_total_for(data)
    if daily is not None:
        cost_seg += f" · ${daily:.2f} (day)"
    cost_seg += RESET
    line2 += f"{SEP}{cost_seg}"
    line2 += f"{SEP}{DIM}{CLOCK} {fmt_duration(duration)}{RESET}"

    out = line1 + "\n" + line2
    codex = codex_segment(latest_codex_info())
    if codex:
        out += "\n" + codex
    return out


def main():
    import sys
    import json
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}
    sys.stdout.write(render(data) + "\n")


def fmt_tokens(n):
    n = int(n or 0)
    if n < 1000:
        return str(n)
    return f"{round(n / 1000)}k"


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


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _normalize_state(state):
    """Return state as {"days": {date: total}, "sessions": {sid: last_cost}}.

    Migrates the legacy {sid: {"date", "cost"}} format by treating each
    session's stored cost as its baseline (so an upgrade mid-day does not
    re-count already-spent money: today starts fresh and only new deltas count).
    """
    if not isinstance(state, dict):
        return {"days": {}, "sessions": {}}
    if "days" in state or "sessions" in state:
        days = state.get("days")
        sessions = state.get("sessions")
        return {
            "days": {d: c for d, c in days.items() if _is_num(c)} if isinstance(days, dict) else {},
            "sessions": {s: c for s, c in sessions.items() if _is_num(c)} if isinstance(sessions, dict) else {},
        }
    # Legacy format: migrate per-session costs into baselines.
    sessions = {}
    for sid, rec in state.items():
        if isinstance(rec, dict) and _is_num(rec.get("cost")):
            sessions[sid] = rec["cost"]
    return {"days": {}, "sessions": sessions}


def update_daily_cost(state, session_id, date, cost):
    """Attribute this render's cost *delta* for the session to `date`.

    Only the increase in a session's cumulative cost since its last render is
    added to the day's total, so a session that spent money on an earlier day
    (but is merely still open) contributes nothing today. Old day buckets are
    pruned; per-session baselines are kept so deltas stay correct across days.
    """
    norm = _normalize_state(state)
    sessions = norm["sessions"]
    prev = sessions.get(session_id)
    prev = prev if _is_num(prev) else 0
    delta = cost - prev
    if delta < 0:
        delta = 0  # cumulative cost should never decrease; ignore if it does

    today = norm["days"].get(date)
    today = today if _is_num(today) else 0
    new_sessions = dict(sessions)
    new_sessions[session_id] = cost
    new_total = today + delta
    return {"days": {date: new_total}, "sessions": new_sessions}, new_total


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
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _codex_home():
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def _codex_scan_tail(path, tail_bytes=65536):
    """Latest rate_limits / model / effort found in a rollout file's tail.

    Scans newest lines first, so each key reflects its most recent event.
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            chunk = f.read()
    except OSError:
        return {}
    found = {}
    for line in reversed(chunk.splitlines()):
        want_rl = "rate_limits" not in found and b'"rate_limits"' in line
        want_tc = "model" not in found and b'"turn_context"' in line
        if not (want_rl or want_tc):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue  # corrupt line, or a fragment cut by the tail seek
        payload = event.get("payload") or {}
        if want_rl and isinstance(payload.get("rate_limits"), dict):
            found["rate_limits"] = payload["rate_limits"]
        if want_tc and event.get("type") == "turn_context" and payload.get("model"):
            found["model"] = payload["model"]
            settings = (payload.get("collaboration_mode") or {}).get("settings") or {}
            found["effort"] = settings.get("reasoning_effort")
        if "rate_limits" in found and "model" in found:
            break
    return found


def _toml_value(raw):
    raw = raw.strip()
    if raw[:1] in ('"', "'"):
        quote = raw[0]
        end = raw.find(quote, 1)
        return raw[1:end] if end > 0 else None
    return raw.split("#", 1)[0].strip() or None


def _codex_config_defaults(home):
    """model / effort explicitly set in config.toml's top-level section.

    Only lines before the first [table] header count — that is where TOML
    keeps top-level keys, so a `model` inside some table is never mistaken
    for the default. Stdlib tomllib needs 3.11+, hence the manual parse.
    """
    out = {}
    try:
        with open(os.path.join(home, "config.toml")) as f:
            for line in f:
                line = line.strip()
                if line.startswith("["):
                    break
                key, eq, raw = line.partition("=")
                if not eq:
                    continue
                key = key.strip()
                if key == "model":
                    out["model"] = _toml_value(raw)
                elif key == "model_reasoning_effort":
                    out["effort"] = _toml_value(raw)
    except OSError:
        return {}
    return {k: v for k, v in out.items() if v}


def latest_codex_info(home=None, max_files=5):
    """Current Codex state (model, effort, rate limits), or None if nothing.

    Codex CLI has no command that prints this. The selected model and
    reasoning effort land in config.toml when chosen explicitly (that wins);
    otherwise they come from the latest turn_context event of the session
    rollout files, which also record a rate_limits object (primary = 5h
    window, secondary = weekly) in every token_count event. Rollout filenames
    embed a sortable timestamp, so the newest files are checked first; only
    their tails are read.
    """
    home = _codex_home() if home is None else home
    info = _codex_config_defaults(home)
    pattern = os.path.join(home, "sessions", "*", "*", "*", "rollout-*.jsonl")
    files = sorted(glob.glob(pattern), key=os.path.basename, reverse=True)
    for path in files[:max_files]:
        for key, value in _codex_scan_tail(path).items():
            info.setdefault(key, value)
        if "rate_limits" in info and "model" in info:
            break
    return info or None


def codex_window_left(window, now_ts):
    """Percent of a rate-limit window still unused, or None if malformed.

    A snapshot only updates while Codex runs, so it can be arbitrarily stale:
    once past resets_at the window has refilled and the answer is 100.
    """
    if not isinstance(window, dict) or not _is_num(window.get("used_percent")):
        return None
    resets = window.get("resets_at")
    if _is_num(resets) and now_ts >= resets:
        return 100
    return max(0, min(100, round(100 - window["used_percent"])))


def color_for_remaining(pct):
    if pct <= 10:
        return RED
    if pct <= 25:
        return YELLOW
    return DIM


def codex_quota(rl, now_ts=None):
    """Render '5h N% · week M%' (remaining quota), or None without data."""
    if not isinstance(rl, dict):
        return None
    now_ts = time.time() if now_ts is None else now_ts
    parts = []
    for label, key in (("5h", "primary"), ("week", "secondary")):
        left = codex_window_left(rl.get(key), now_ts)
        if left is not None:
            parts.append(f"{color_for_remaining(left)}{label} {left}%{RESET}")
    if not parts:
        return None
    return f"{DIM} · {RESET}".join(parts)


def codex_segment(info, now_ts=None):
    """Render the Codex status line: '[model · effort]  cdx 5h N% · wk M%'.

    Model and effort come from the latest turn_context (effort is omitted
    when Codex leaves it null, i.e. the model's default). Returns None when
    there is nothing to show.
    """
    if not isinstance(info, dict):
        return None
    parts = []
    if info.get("model"):
        head = info["model"]
        if info.get("effort"):
            head += f" · {info['effort']}"
        parts.append(f"{DIM}[{head}]{RESET}")
    quota = codex_quota(info.get("rate_limits"), now_ts)
    if quota:
        parts.append(quota)
    return "  ".join(parts) if parts else None


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
    except Exception:
        pass
    return total


if __name__ == "__main__":
    main()
