# claude-statusline

A two-line [Claude Code](https://code.claude.com) status line: a **battery bar of free context** plus the **occupied context in tokens**, color-coded by absolute thresholds. Nerd Font glyphs, no external dependencies (Python 3 stdlib only — no `jq`).

```
[Opus 4.8 · 1M]   ContextPlugin  │   feature/auth
▰▰▰▰▰▰▰▰▱▱ 81%  │   187k  │  $0.08  │   7m 3s
```

**Line 1** — model + context window size, current folder (real basename), and git branch (only inside a repo; `*` when dirty).

**Line 2** — battery bar of *free* context (`remaining_percentage`), occupied tokens (`total_input_tokens`), session cost, and session duration.

### Color thresholds

The battery bar and the token number share one color, driven by occupied tokens:

| Occupied tokens | Color |
|---|---|
| ≤ 200k | white |
| 200k – 400k | yellow |
| > 400k | red |

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
