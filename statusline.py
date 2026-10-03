#!/usr/bin/env python3
"""Claude Code status line: free-context battery + occupied tokens."""

import glob
import math
import os
import subprocess
import sys
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
    try:
        spawn_codex_refresh()
        codex = codex_segment(latest_codex_info(live=load_codex_live()))
    except Exception:
        codex = None  # never let the Codex extras take the status line down
    if codex:
        out += "\n" + codex
    return out


def main():
    if sys.argv[1:2] == ["--refresh-codex"]:
        refresh_codex_live(sys.argv[2] if len(sys.argv) > 2 else None)
        return
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
    # NaN/inf would crash int() or poison comparisons downstream.
    return isinstance(x, (int, float)) and not isinstance(x, bool) \
        and math.isfinite(x)


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


def _event_ts(ts):
    """ISO-8601 event timestamp -> epoch seconds; 0.0 when absent/unparsable.

    Compared as time, not text: "…00Z" sorts lexicographically after the
    later "…00.500Z", so string comparison would resurrect stale snapshots.
    """
    if not isinstance(ts, str) or not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _has_window(rl):
    """True if a rate_limits snapshot carries at least one window.

    A turn rejected for hitting the usage limit still logs a snapshot, but
    under another limit_id ("premium") with both windows null. Taken as the
    freshest, it would blank the quota exactly when it matters most.
    """
    return isinstance(rl, dict) and any(
        isinstance(rl.get(key), dict) for key in ("primary", "secondary"))


def _codex_scan_tail(path, tail_bytes=262144):
    """Latest rate_limits / model / effort found in a rollout file's tail.

    Scans newest lines first, so each key reflects its most recent event.
    Each found key comes with its event epoch time (<key>_ts) for cross-file
    freshness comparison. Lines that parse but aren't shaped like events are
    skipped — a malformed rollout must never take the status line down.
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
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        ts = _event_ts(event.get("timestamp"))
        if want_rl and _has_window(payload.get("rate_limits")):
            found["rate_limits"] = payload["rate_limits"]
            found["rate_limits_ts"] = ts
        if want_tc and event.get("type") == "turn_context" \
                and isinstance(payload.get("model"), str):
            mode = payload.get("collaboration_mode")
            settings = mode.get("settings") if isinstance(mode, dict) else None
            effort = settings.get("reasoning_effort") if isinstance(settings, dict) else None
            found["model"] = payload["model"]
            found["effort"] = effort if isinstance(effort, str) else None
            found["model_ts"] = ts
        if "rate_limits" in found and "model" in found:
            break
    return found


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


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


CODEX_LIVE_TTL = 60  # seconds between live rate-limit fetches


def _codex_live_path():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return os.path.join(base, "statusline_codex_limits.json")


def _live_window(window):
    """App-server camelCase window -> the rollout's snake_case shape."""
    if not isinstance(window, dict):
        return None
    return {"used_percent": window.get("usedPercent"),
            "window_minutes": window.get("windowDurationMins"),
            "resets_at": window.get("resetsAt")}


def fetch_codex_live_limits(timeout=20):
    """Ask `codex app-server` for the account's current rate limits.

    Rollout files only record limits while Codex runs a turn, so they go
    stale between sessions; the app-server's account/rateLimits/read is
    what the Codex app itself polls. Takes seconds (it boots the server),
    so it only ever runs in a detached background process.
    Returns a rate_limits dict in rollout shape, or None.
    """
    import threading
    try:
        proc = subprocess.Popen(
            ["codex", "app-server"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return None
    watchdog = threading.Timer(timeout, proc.kill)
    watchdog.start()
    try:
        for msg in (
                {"id": 1, "method": "initialize",
                 "params": {"clientInfo": {"name": "statusline", "version": "1"}}},
                {"method": "initialized", "params": {}},
                {"id": 2, "method": "account/rateLimits/read", "params": {}}):
            proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        for line in proc.stdout:
            try:
                reply = json.loads(line)
            except ValueError:
                continue
            if isinstance(reply, dict) and reply.get("id") == 2:
                result = reply.get("result")
                rl = result.get("rateLimits") if isinstance(result, dict) else None
                if not isinstance(rl, dict):
                    return None
                out = {key: _live_window(rl.get(key)) for key in ("primary", "secondary")}
                return out if _has_window(out) else None
        return None
    except (OSError, ValueError):
        return None
    finally:
        watchdog.cancel()
        proc.kill()
        proc.wait()


def refresh_codex_live(path=None):
    """Fetch live limits and store them with their fetch time (atomically)."""
    path = _codex_live_path() if path is None else path
    rl = fetch_codex_live_limits()
    if rl is None:
        return
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".statusline_codex.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"fetched_at": time.time(), "rate_limits": rl}, f)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def load_codex_live(path=None):
    """Cached live snapshot {'fetched_at', 'rate_limits'}, or None."""
    path = _codex_live_path() if path is None else path
    data = load_state(path)
    if not _is_num(data.get("fetched_at")) or not _has_window(data.get("rate_limits")):
        return None
    return data


def spawn_codex_refresh(path=None, now_ts=None, ttl=CODEX_LIVE_TTL, home=None):
    """Start a detached live fetch when the cache is older than ttl.

    Only for a Codex home that has sessions: no point booting the server
    (or showing a Codex line) for someone who doesn't use Codex. The marker is touched before spawning, so renders arriving
    while a fetch runs (or after one failed) don't pile up more of them:
    at most one attempt per ttl, success or not.
    """
    import shutil
    path = _codex_live_path() if path is None else path
    now_ts = time.time() if now_ts is None else now_ts
    home = _codex_home() if home is None else home
    marker = path + ".attempt"
    if now_ts - _mtime(marker) < ttl or not shutil.which("codex") \
            or not os.path.isdir(os.path.join(home, "sessions")):
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(marker, "w"):
            pass
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--refresh-codex", path],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def latest_codex_info(home=None, max_files=8, live=None):
    """Current Codex state (model, effort, rate limits), or None if nothing.

    Codex CLI has no command that prints this. The selected model and
    reasoning effort land in config.toml when chosen explicitly (that wins);
    otherwise they come from the latest turn_context event of the session
    rollout files, which also record a rate_limits object in every
    token_count event (which window sits in primary vs secondary varies
    by CLI version; each window's window_minutes says what it is).

    A file's name carries the session *start* time, so it says nothing about
    which file holds the freshest data — a long-running session started hours
    ago can be the active one. The newest files by mtime are scanned and the
    snapshot with the newest event timestamp wins across all of them.

    `live` is a load_codex_live() snapshot; it replaces the rollout's
    rate limits when it was fetched after the newest rollout event. It
    only applies once rollouts show Codex is in use under this home.
    """
    home = _codex_home() if home is None else home
    pattern = os.path.join(home, "sessions", "*", "*", "*", "rollout-*.jsonl")
    files = sorted(glob.glob(pattern), key=_mtime, reverse=True)

    def fresher(found, best, key):
        if key not in found:
            return False
        if key not in best:
            return True
        return found.get(key + "_ts", 0.0) > best.get(key + "_ts", 0.0)

    best = {}
    for path in files[:max_files]:
        found = _codex_scan_tail(path)
        if fresher(found, best, "rate_limits"):
            best["rate_limits"] = found["rate_limits"]
            best["rate_limits_ts"] = found.get("rate_limits_ts", "")
        if fresher(found, best, "model"):
            best["model"] = found["model"]
            best["effort"] = found.get("effort")
            best["model_ts"] = found.get("model_ts", "")
    if live and best and live["fetched_at"] > best.get("rate_limits_ts", 0.0):
        best["rate_limits"] = live["rate_limits"]
    info ={k: v for k, v in best.items() if not k.endswith("_ts") and v is not None}
    for key, value in _codex_config_defaults(home).items():
        info[key] = value  # an explicit choice in config.toml wins
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
    # Floor, don't round: 99.6% left must show 99, never a false 100.
    return max(0, min(100, int(100 - window["used_percent"])))


def color_for_remaining(pct):
    if pct <= 10:
        return RED
    if pct <= 25:
        return YELLOW
    return DIM


def codex_window_label(window):
    """Label a rate-limit window by its own duration, or None if unknown.

    Which key a window sits under means nothing: codex-cli 0.144.x moved
    the weekly window from `secondary` into `primary` and dropped the 5h
    one. A window without a usable duration gets no label — showing a
    guessed one could lie about which quota is running out.
    """
    if not isinstance(window, dict):
        return None
    minutes = window.get("window_minutes")
    if not _is_num(minutes) or minutes <= 0:
        return None
    minutes = int(minutes)
    if minutes == 300:
        return "5h"
    if minutes == 10080:
        return "week"
    # Exact single-unit fallbacks: 90 must read "90m", never a rounded "2h".
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def codex_quota(rl, now_ts=None):
    """Render '5h N% · week M%' (remaining quota), or None without data.

    Shorter windows come first regardless of which key held them, so the
    layout is stable across the API reshuffling its primary/secondary slots.
    """
    if not isinstance(rl, dict):
        return None
    now_ts = time.time() if now_ts is None else now_ts
    windows = []
    for key in ("primary", "secondary"):
        window = rl.get(key)
        label = codex_window_label(window)
        left = codex_window_left(window, now_ts)
        if label is None or left is None:
            continue
        windows.append((int(window["window_minutes"]), key, label, left))
    if not windows:
        return None
    windows.sort(key=lambda w: (w[0], w[1]))
    return f"{DIM} · {RESET}".join(
        f"{color_for_remaining(left)}{label} {left}%{RESET}"
        for _, _, label, left in windows)


def codex_segment(info, now_ts=None):
    """Render the Codex status line: '[codex · effort]  5h N% · week M%'.

    Labelled "codex" rather than by model name. Effort comes from config.toml
    or the latest turn_context (omitted when Codex leaves it null, i.e. the
    model's default). Returns None when there is nothing to show.
    """
    if not isinstance(info, dict):
        return None
    quota = codex_quota(info.get("rate_limits"), now_ts)
    if not (quota or info.get("model") or info.get("effort")):
        return None
    head = "codex"
    if info.get("effort"):
        head += f" · {info['effort']}"
    parts = [f"{DIM}[{head}]{RESET}"]
    if quota:
        parts.append(quota)
    return "  ".join(parts)


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
