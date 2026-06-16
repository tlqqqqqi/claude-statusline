#!/usr/bin/env python3
"""Claude Code status line: free-context battery + occupied tokens."""

import os
import subprocess

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
    line2 += f"{SEP}{DIM}${cost:.2f}{RESET}"
    line2 += f"{SEP}{DIM}{CLOCK} {fmt_duration(duration)}{RESET}"

    return line1 + "\n" + line2


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


if __name__ == "__main__":
    main()
