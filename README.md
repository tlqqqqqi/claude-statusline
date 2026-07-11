# claude-statusline

A three-line [Claude Code](https://code.claude.com) status line: a **battery bar of free context** plus the **occupied context in tokens**, color-coded by absolute thresholds, and a **Codex line** with the selected model and remaining quota. Nerd Font glyphs, no external dependencies (Python 3 stdlib only — no `jq`).

```
[Opus 4.8 (1M) · high]   ContextPlugin  │   feature/auth
▰▰▰▰▰▰▰▰▱▱ 81%  │   187k  │  $0.08 · $5.55 (day)  │   7m 3s
[gpt-5.6-sol · high]  5h 93% · week 99%
```

**Line 1** — model + context window size + reasoning effort (`effort.level`; omitted when the model doesn't support it), current folder (real basename), and git branch (only inside a repo; `*` when dirty).

**Line 2** — battery bar of *free* context (`remaining_percentage`), occupied tokens (`total_input_tokens`), cost (`$session · $today (day)`), and session duration.

**Line 3** — [Codex](https://github.com/openai/codex): selected model + reasoning effort (dim, in brackets) and remaining quota. Omitted entirely when Codex isn't installed.

### Color thresholds

The battery bar and the token number share one color, driven by occupied tokens:

| Occupied tokens | Color |
|---|---|
| ≤ 200k | white |
| 200k – 400k | yellow |
| > 400k | red |

### Daily cost total

`$5.55 (day)` is the spend across **every** Claude Code session on this
machine for the current local day. Claude Code's status line input only
reports the *current* session's cumulative cost, so the script aggregates it
itself in a small state file (`$CLAUDE_CONFIG_DIR/statusline_cost.json`,
default `~/.claude/statusline_cost.json`).

Attribution is **per-day delta**: on each render the script adds only the
*increase* in a session's cumulative cost since its last render to the current
day's bucket, keyed by the local date. So the total resets to `$0.00` at local
midnight, and a session that spent money earlier — but is merely still open
(its window kept alive by `refreshInterval`) — contributes nothing to today.
A session that spans midnight has yesterday's spend counted yesterday and only
today's new spend counted today.

State shape: `{"days": {"<YYYY-MM-DD>": <total>}, "sessions": {"<session_id>":
<last cumulative cost>}}`. Old day buckets are pruned; per-session baselines
are retained so deltas stay correct across days (including resumed sessions).
The legacy `{session_id: {date, cost}}` format is migrated automatically on
first render — existing costs become baselines, so upgrading mid-day does not
re-count money already spent.

### Codex line

`[gpt-5.6-sol · high]  5h 93% · week 99%` is the state of your OpenAI
**Codex** install: the currently selected model and reasoning effort, then
how much of the rate limit is still *unused* — the 5-hour window and the
weekly window. Codex CLI has no command that prints this, so the script
reads its files directly (no network calls, no quota spent):

- **Model + effort** come from the top-level `model` /
  `model_reasoning_effort` keys of `$CODEX_HOME/config.toml` — that's where
  an explicit selection (e.g. via `/model` in the TUI) lands. When unset
  there, they fall back to the latest `turn_context` event of the session
  rollout files under `$CODEX_HOME/sessions/`; effort is omitted while Codex
  leaves it null (the model's default).
- **Quota** comes from the `rate_limits` snapshot (used percent + reset time
  per window) that every `token_count` event of the rollout files records.
  Only the tails of the newest files are read.

Each quota window is colored by what's left: dim above 25%, yellow at 25%
and below, red at 10% and below. A snapshot only updates while Codex is
actually running, so it can be stale; once a window's recorded reset time
has passed, the script shows 100% for it. The line is omitted entirely when
there is no Codex install or nothing to read. (`$CODEX_HOME` defaults to
`~/.codex`.)

## Install

1. Copy the script into your Claude config dir:
   ```bash
   cp statusline.py ~/.claude/statusline.py
   ```
2. Add this to `~/.claude/settings.json`:
   ```json
   "statusLine": {
     "type": "command",
     "command": "python3 ~/.claude/statusline.py",
     "refreshInterval": 30
   }
   ```

The bar updates on Claude Code events (new assistant message, `/compact`, permission/vim mode changes; debounced at 300ms). Those triggers go quiet while the session is idle, so the time-based segments (session duration, cost) would otherwise freeze. `refreshInterval` re-runs the script on a fixed timer to keep them live — `30` (seconds) is a light default; the minimum is `1`. Drop the field to run on events only.

The script writes a small state file at `$CLAUDE_CONFIG_DIR/statusline_cost.json`
(default `~/.claude/statusline_cost.json`) to track the daily cost total. Add it
to your ignore lists if needed; delete it any time to reset the running total.

### Requirements
- Python 3 (stdlib only)
- A terminal with **Nerd Font** glyphs (e.g. Ghostty ships them; otherwise install a Nerd Font). Without one, the icon glyphs show as boxes — edit the `FOLDER`/`GIT`/`CTX`/`CLOCK` constants near the top of `statusline.py`.

## Customize

All knobs are constants at the top of `statusline.py`: color thresholds in `color_for_tokens`, the glyph constants, and the ANSI color codes.

## Test

```bash
python3 -m unittest test_statusline -v
```

## Design docs

- [Spec](docs/superpowers/specs/2026-06-16-statusline-context-meter-design.md)
- [Implementation plan](docs/superpowers/plans/2026-06-16-statusline-context-meter.md)
