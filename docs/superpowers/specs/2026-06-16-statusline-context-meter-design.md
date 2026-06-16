# Statusline Context Meter — Design

**Date:** 2026-06-16
**Status:** Approved for planning

## Goal

A custom Claude Code status line (two lines, shown under the input box) that gives an at-a-glance view of context usage. Inspired by the official multi-line example and a community gist, but with these specific requirements:

- No directory-name label artifacts (the reference showed `src` = the current folder's basename; we keep the folder name but it's the real cwd basename, not a hardcoded `src`).
- A "battery" bar showing **free** context as a percentage.
- The **occupied context** shown in tokens, color-coded by absolute thresholds.
- Terminal-style **Nerd Font** glyphs for icons (confirmed rendering in the user's Ghostty terminal), not emoji.

## Appearance

```
 [Opus 4.8 · 1M]   ContextPlugin  │   feature/auth
 ▰▰▰▱▱▱▱▱▱▱ 58%  │   187k  │  $0.08  │   7m 3s
```

### Line 1 — session context
- `[Opus 4.8 · 1M]` — `model.display_name` + window size from `context_window.context_window_size` (rendered as `1M` for 1000000, `200k` for 200000). Color: bold cyan.
- ` <folder>` — Nerd Font folder glyph `` + `basename` of `workspace.current_dir` (fallback `.cwd`). No hardcoded label.
- `│  <branch>` — dim separator + Nerd Font git glyph `` + current git branch, with a trailing `*` if the working tree is dirty. **Shown only when the cwd is a git repository**; otherwise the entire branch segment is omitted (the user's current folder is not a repo, so this segment will be absent there).

### Line 2 — context spend
- `▰▰▰▱▱▱▱▱▱▱ 58%` — battery bar of **free** space from `context_window.remaining_percentage`. 10 segments; filled segments (`▰`) = round(remaining/10), empty = `▱`. The `58%` is the remaining percentage. Bar + percentage are colored by the token-threshold rule below.
- ` 187k` — **occupied context** in tokens, from `context_window.total_input_tokens` (input + cache read + cache creation, matching Claude Code's own context accounting). Nerd Font database glyph ``. Colored by the threshold rule below.
- `$0.08` — session cost from `cost.total_cost_usd`, formatted `${:.2f}`. Dim.
- ` 7m 3s` — session duration from `cost.total_duration_ms`. Nerd Font clock glyph ``. Dim.

### Color thresholds (occupied tokens)
Both the **battery bar** and the **token number** use the same color, driven by `total_input_tokens`:

| Occupied tokens | Color |
|---|---|
| ≤ 200,000 | white |
| 200,001 – 400,000 | yellow |
| > 400,000 | red |

(White in the healthy zone — chosen explicitly over green.)

## Implementation

- **Language: Python 3** (`/Library/Frameworks/.../python3`, v3.13 present). Reads the session JSON from stdin, prints two ANSI-colored lines to stdout. Chosen over bash because `jq` is **not installed** and python3 is available — avoids an external dependency and handles JSON, number/time formatting, and ANSI coloring cleanly.
- **File:** `~/.claude/statusline.sh` (executable). Despite the `.sh` name it has a `#!/usr/bin/env python3` shebang; name kept conventional. (Alternative `~/.claude/statusline.py` is fine — decide in plan.)
- **Wiring:** add to `~/.claude/settings.json`:
  ```json
  "statusLine": { "type": "command", "command": "~/.claude/statusline.sh" }
  ```
- **Git branch:** obtained via `git -C <cwd> branch --show-current` and a dirty check via `git -C <cwd> diff-index --quiet HEAD --`; both wrapped so failure/non-repo → segment omitted. No `cd`.

## Field reference (verified against current Claude Code statusline schema)

- `model.display_name`
- `workspace.current_dir` (fallback `cwd`)
- `context_window.context_window_size` — 200000 or 1000000
- `context_window.remaining_percentage` — free %; may be `null` early in session
- `context_window.total_input_tokens` — occupied input tokens (incl. cache); `0` before first API response
- `cost.total_cost_usd`, `cost.total_duration_ms`

## Edge cases / fallbacks

- `remaining_percentage` is `null` before the first API call and right after `/compact` → treat as `100` (full/empty session) so the bar shows full and color is white.
- `total_input_tokens` missing/`0` → `0`, white, shows `0` (or `0k`).
- `cost.*` missing → `$0.00`, `0s`.
- cwd not a git repo or git absent → omit branch segment entirely.
- Number formatting: tokens ≥ 1000 → `{round(n/1000)}k` (e.g. `187k`); < 1000 → raw.
- Duration: `≥1h` → `Xh Ym`; `≥1m` → `Xm Ys`; else `Xs`.

## Out of scope (YAGNI)

- Agent name, output style, vim mode, detailed in/out/cache token breakdown (present in the gist, dropped here).
- Configurability/flags — thresholds and glyphs are hardcoded constants (easy to edit at the top of the file).

## Testing

Run with mock stdin to verify each state without a live session:
```
echo '{"model":{"display_name":"Opus 4.8"},"workspace":{"current_dir":"/x/ContextPlugin"},"context_window":{"context_window_size":1000000,"remaining_percentage":58,"total_input_tokens":187000},"cost":{"total_cost_usd":0.08,"total_duration_ms":423000}}' | ~/.claude/statusline.sh
```
Cases to cover: ≤200k (white), 200–400k (yellow), >400k (red); `null` percentage; missing cost; git repo vs non-repo cwd; 200k vs 1M window.
