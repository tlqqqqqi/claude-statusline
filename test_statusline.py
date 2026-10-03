import unittest
import statusline as s
import os
import tempfile as _tempfile

_CFG = _tempfile.TemporaryDirectory()
_real_spawn_codex_refresh = s.spawn_codex_refresh


def setUpModule():
    # render() must neither read the real live-limits cache nor boot a real
    # `codex app-server` in the background.
    os.environ["CLAUDE_CONFIG_DIR"] = _CFG.name
    s.spawn_codex_refresh = lambda *a, **k: None


def tearDownModule():
    s.spawn_codex_refresh = _real_spawn_codex_refresh


class TestFmtTokens(unittest.TestCase):
    def test_under_1000_raw(self):
        self.assertEqual(s.fmt_tokens(0), "0")
        self.assertEqual(s.fmt_tokens(823), "823")

    def test_thousands_rounded_k(self):
        self.assertEqual(s.fmt_tokens(187000), "187k")
        self.assertEqual(s.fmt_tokens(1500), "2k")
        self.assertEqual(s.fmt_tokens(47483), "47k")


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


class TestColorForTokens(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(s.color_for_tokens(0), s.WHITE)
        self.assertEqual(s.color_for_tokens(200000), s.WHITE)
        self.assertEqual(s.color_for_tokens(200001), s.YELLOW)
        self.assertEqual(s.color_for_tokens(400000), s.YELLOW)
        self.assertEqual(s.color_for_tokens(400001), s.RED)


class TestBatteryBar(unittest.TestCase):
    def test_fill_count(self):
        self.assertEqual(s.battery_bar(100).count("▰"), 10)
        self.assertEqual(s.battery_bar(0).count("▰"), 0)
        self.assertEqual(s.battery_bar(58).count("▰"), 6)
        self.assertEqual(s.battery_bar(58).count("▱"), 4)

    def test_null_remaining_treated_full(self):
        self.assertEqual(s.battery_bar(None).count("▰"), 10)


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


class TestRender(unittest.TestCase):
    def setUp(self):
        # Point CODEX_HOME at an empty dir so tests don't read real ~/.codex.
        self._codex_tmp = tempfile.TemporaryDirectory()
        self._old_codex = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = self._codex_tmp.name

    def tearDown(self):
        if self._old_codex is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self._old_codex
        self._codex_tmp.cleanup()

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
            "effort": {"level": "high"},
        }
        base["context_window"].update(ctx)
        return base

    def test_two_lines(self):
        out = s.render(self._data())
        self.assertEqual(len(out.split("\n")), 2)

    def test_line1_has_model_window_folder(self):
        line1 = s.render(self._data()).split("\n")[0]
        self.assertIn("Opus 4.8", line1)
        self.assertIn("1M", line1)
        self.assertIn("ContextPlugin", line1)

    def test_line1_format_model_window_effort(self):
        line1 = s.render(self._data()).split("\n")[0]
        self.assertIn("Opus 4.8 (1M) · high", line1)

    def test_line1_no_effort_segment_when_absent(self):
        d = self._data()
        d.pop("effort", None)
        line1 = s.render(d).split("\n")[0]
        self.assertIn("Opus 4.8 (1M)", line1)
        # no trailing " · effort" after the window
        self.assertNotIn("·", line1.split("(1M)")[1])

    def test_line2_has_pct_tokens_cost_time(self):
        line2 = s.render(self._data()).split("\n")[1]
        self.assertIn("81%", line2)
        self.assertIn("187k", line2)
        self.assertIn("$0.08", line2)
        self.assertIn("7m 3s", line2)

    def test_color_white_under_200k(self):
        self.assertIn(s.WHITE, s.render(self._data(total_input_tokens=187000)))

    def test_color_yellow_300k(self):
        self.assertIn(s.YELLOW, s.render(self._data(total_input_tokens=300000)))

    def test_color_red_over_400k(self):
        self.assertIn(s.RED, s.render(self._data(total_input_tokens=500000)))

    def test_null_percentage_full_bar(self):
        self.assertIn("100%", s.render(self._data(remaining_percentage=None)))

    def test_no_branch_when_not_repo(self):
        self.assertNotIn(s.GIT, s.render(self._data()))


class TestUpdateDailyCost(unittest.TestCase):
    # New schema: {"days": {date: total}, "sessions": {sid: last_cumulative_cost}}.
    # The daily total accumulates only the per-render *delta* in a session's
    # cumulative cost, attributed to the day the delta is observed.

    def test_new_session_attributes_full_cost_today(self):
        state, total = s.update_daily_cost(
            {"days": {}, "sessions": {}}, "a", "2026-06-29", 1.5
        )
        self.assertAlmostEqual(total, 1.5)
        self.assertAlmostEqual(state["sessions"]["a"], 1.5)
        self.assertAlmostEqual(state["days"]["2026-06-29"], 1.5)

    def test_delta_only_attributed_to_today(self):
        start = {"days": {"2026-06-29": 5.0}, "sessions": {"a": 5.0}}
        state, total = s.update_daily_cost(start, "a", "2026-06-29", 7.5)
        self.assertAlmostEqual(total, 7.5)  # 5.0 already + 2.5 new delta
        self.assertAlmostEqual(state["sessions"]["a"], 7.5)

    def test_sums_deltas_across_sessions(self):
        start = {"days": {"2026-06-29": 2.5}, "sessions": {"a": 7.5}}
        state, total = s.update_daily_cost(start, "b", "2026-06-29", 0.08)
        self.assertAlmostEqual(total, 2.58)

    def test_yesterday_session_does_not_leak_into_today(self):
        # The bug: a session that did all its spend yesterday but whose window
        # stays open re-renders today with the SAME cumulative cost. It must
        # contribute $0 to today.
        start = {"days": {"2026-06-28": 80.0}, "sessions": {"old": 80.0}}
        state, total = s.update_daily_cost(start, "old", "2026-06-29", 80.0)
        self.assertAlmostEqual(total, 0.0)
        self.assertNotIn("2026-06-28", state["days"])

    def test_spanning_session_splits_at_midnight(self):
        # Session spent 80 yesterday, then 5 more today -> today shows only 5.
        start = {"days": {"2026-06-28": 80.0}, "sessions": {"x": 80.0}}
        state, total = s.update_daily_cost(start, "x", "2026-06-29", 85.0)
        self.assertAlmostEqual(total, 5.0)

    def test_negative_delta_clamped_to_zero(self):
        start = {"days": {"2026-06-29": 5.0}, "sessions": {"a": 10.0}}
        state, total = s.update_daily_cost(start, "a", "2026-06-29", 4.0)
        self.assertAlmostEqual(total, 5.0)  # cost can't decrease; ignore
        self.assertAlmostEqual(state["sessions"]["a"], 4.0)

    def test_prunes_old_day_buckets(self):
        start = {"days": {"2026-06-28": 999.0}, "sessions": {"a": 1.0}}
        state, total = s.update_daily_cost(start, "a", "2026-06-29", 1.0)
        self.assertEqual(list(state["days"].keys()), ["2026-06-29"])
        self.assertAlmostEqual(total, 0.0)

    def test_migrates_old_format_as_baseline_without_leaking(self):
        # Old format {sid: {date, cost}} -> costs become baselines, today = 0,
        # so an upgrade mid-day doesn't re-count already-spent money.
        old = {"sessA": {"date": "2026-06-28", "cost": 80.0}}
        state, total = s.update_daily_cost(old, "sessA", "2026-06-29", 80.0)
        self.assertAlmostEqual(total, 0.0)
        self.assertAlmostEqual(state["sessions"]["sessA"], 80.0)

    def test_ignores_malformed_records(self):
        start = {"days": {"2026-06-29": "bad"}, "sessions": {"junk": "nope"}}
        state, total = s.update_daily_cost(start, "a", "2026-06-29", 0.5)
        self.assertAlmostEqual(total, 0.5)


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


class TestDailyTotalFor(unittest.TestCase):
    def _isolated_env(self, d):
        # Point the state file at a temp dir for the duration of a test.
        os.environ["CLAUDE_CONFIG_DIR"] = d

    def test_none_without_session_id(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("CLAUDE_CONFIG_DIR")
            self._isolated_env(d)
            try:
                self.assertIsNone(s.daily_total_for({"cost": {"total_cost_usd": 1.0}}))
            finally:
                if old is None:
                    os.environ.pop("CLAUDE_CONFIG_DIR", None)
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = old

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


def _write_rollout(home, day, name, lines):
    d = os.path.join(home, "sessions", "2026", "07", day)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _rl_line(primary_used=4.0, secondary_used=1.0, resets_at=9_999_999_999,
             ts="2026-07-12T00:00:00.000Z"):
    import json
    return json.dumps({
        "timestamp": ts,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {},
            "rate_limits": {
                "primary": {
                    "used_percent": primary_used,
                    "window_minutes": 300,
                    "resets_at": resets_at,
                },
                "secondary": {
                    "used_percent": secondary_used,
                    "window_minutes": 10080,
                    "resets_at": resets_at,
                },
            },
        },
    })


def _rl_line_weekly_only(used=0.0, resets_at=9_999_999_999,
                         ts="2026-07-12T00:00:00.000Z"):
    """New payload shape (codex-cli 0.144.x): weekly in primary, no 5h."""
    import json
    return json.dumps({
        "timestamp": ts,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {},
            "rate_limits": {
                "primary": {
                    "used_percent": used,
                    "window_minutes": 10080,
                    "resets_at": resets_at,
                },
                "secondary": None,
            },
        },
    })


def _tc_line(model="gpt-5.6-sol", effort=None):
    import json
    return json.dumps({
        "timestamp": "2026-07-12T00:00:00.000Z",
        "type": "turn_context",
        "payload": {
            "turn_id": "t1",
            "model": model,
            "collaboration_mode": {
                "mode": "default",
                "settings": {"model": model, "reasoning_effort": effort},
            },
        },
    })


def _rl_line_no_windows(ts="2026-07-12T05:00:00.000Z"):
    """Logged by a turn rejected for hitting the usage limit."""
    import json
    return json.dumps({
        "timestamp": ts,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": None,
            "rate_limits": {"limit_id": "premium", "primary": None,
                            "secondary": None},
        },
    })


class TestCodexLive(unittest.TestCase):
    TS = 1_783_814_400  # 2026-07-12T00:00:00Z, the _rl_line default

    def _live(self, fetched_at, used=100.0):
        return {"fetched_at": fetched_at,
                "rate_limits": {"primary": {"used_percent": used,
                                            "window_minutes": 300,
                                            "resets_at": 9_999_999_999}}}

    def test_windowless_snapshot_does_not_shadow_real_one(self):
        with tempfile.TemporaryDirectory() as home:
            _write_rollout(home, "12", "rollout-2026-07-12T01-00-00-x.jsonl",
                           [_rl_line(primary_used=7.0), _rl_line_no_windows()])
            info = s.latest_codex_info(home)
            self.assertEqual(info["rate_limits"]["primary"]["used_percent"], 7.0)

    def test_newer_windowless_file_does_not_shadow_older_file(self):
        with tempfile.TemporaryDirectory() as home:
            old = _write_rollout(home, "11", "rollout-2026-07-11T10-00-00-a.jsonl",
                                 [_rl_line(primary_used=7.0)])
            new = _write_rollout(home, "12", "rollout-2026-07-12T05-00-00-b.jsonl",
                                 [_rl_line_no_windows()])
            os.utime(old, (1_000_000_100, 1_000_000_100))
            os.utime(new, (1_000_000_200, 1_000_000_200))
            info = s.latest_codex_info(home)
            self.assertEqual(info["rate_limits"]["primary"]["used_percent"], 7.0)

    def test_fresher_live_snapshot_wins(self):
        with tempfile.TemporaryDirectory() as home:
            _write_rollout(home, "12", "rollout-2026-07-12T01-00-00-x.jsonl",
                           [_rl_line(primary_used=7.0)])
            info = s.latest_codex_info(home, live=self._live(self.TS + 60))
            self.assertEqual(info["rate_limits"]["primary"]["used_percent"], 100.0)

    def test_older_live_snapshot_loses(self):
        with tempfile.TemporaryDirectory() as home:
            _write_rollout(home, "12", "rollout-2026-07-12T01-00-00-x.jsonl",
                           [_rl_line(primary_used=7.0)])
            info = s.latest_codex_info(home, live=self._live(self.TS - 60))
            self.assertEqual(info["rate_limits"]["primary"]["used_percent"], 7.0)

    def test_live_ignored_when_codex_unused(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertIsNone(s.latest_codex_info(home, live=self._live(self.TS)))

    def test_live_window_converted_to_rollout_shape(self):
        self.assertEqual(
            s._live_window({"usedPercent": 16, "windowDurationMins": 10080,
                            "resetsAt": 5}),
            {"used_percent": 16, "window_minutes": 10080, "resets_at": 5})
        self.assertIsNone(s._live_window(None))

    def test_load_live_rejects_missing_or_malformed(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "live.json")
            self.assertIsNone(s.load_codex_live(path))
            for bad in ({"rate_limits": self._live(1)["rate_limits"]},
                        {"fetched_at": 1, "rate_limits": {"primary": None}},
                        []):
                with open(path, "w") as f:
                    json.dump(bad, f)
                self.assertIsNone(s.load_codex_live(path))
            with open(path, "w") as f:
                json.dump(self._live(1), f)
            self.assertEqual(s.load_codex_live(path)["fetched_at"], 1)

    def _spawn_calls(self, home, d, now_ts):
        from unittest import mock
        with mock.patch("shutil.which", return_value="/bin/codex"), \
                mock.patch("subprocess.Popen") as popen:
            _real_spawn_codex_refresh(os.path.join(d, "live.json"),
                                      now_ts=now_ts, home=home)
        return popen.call_count

    def test_spawn_throttled_by_attempt_marker(self):
        import time
        with tempfile.TemporaryDirectory() as home, \
                tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(home, "sessions"))
            now = time.time()
            self.assertEqual(self._spawn_calls(home, d, now), 1)
            self.assertEqual(self._spawn_calls(home, d, now + 1), 0)
            self.assertEqual(self._spawn_calls(home, d, now + s.CODEX_LIVE_TTL + 1), 1)

    def test_no_spawn_without_codex_sessions(self):
        with tempfile.TemporaryDirectory() as home, \
                tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._spawn_calls(home, d, 1e12), 0)


class TestCodexWindowLeft(unittest.TestCase):
    NOW = 1_783_800_000

    def test_remaining_is_100_minus_used(self):
        win = {"used_percent": 4.0, "resets_at": self.NOW + 1000}
        self.assertEqual(s.codex_window_left(win, self.NOW), 96)

    def test_past_reset_means_full_window(self):
        win = {"used_percent": 87.0, "resets_at": self.NOW - 1}
        self.assertEqual(s.codex_window_left(win, self.NOW), 100)

    def test_over_100_used_clamped_to_zero(self):
        win = {"used_percent": 130.0, "resets_at": self.NOW + 1000}
        self.assertEqual(s.codex_window_left(win, self.NOW), 0)

    def test_remaining_is_floored_never_overstated(self):
        # 0.4% used is NOT "100% left": floor, don't round.
        win = {"used_percent": 0.4, "resets_at": self.NOW + 1000}
        self.assertEqual(s.codex_window_left(win, self.NOW), 99)
        self.assertEqual(
            s.codex_window_left({"used_percent": 0.0, "resets_at": self.NOW + 1000},
                                self.NOW), 100)

    def test_missing_resets_at_still_computes(self):
        self.assertEqual(s.codex_window_left({"used_percent": 25.0}, self.NOW), 75)

    def test_malformed_returns_none(self):
        self.assertIsNone(s.codex_window_left(None, self.NOW))
        self.assertIsNone(s.codex_window_left({}, self.NOW))
        self.assertIsNone(s.codex_window_left({"used_percent": "x"}, self.NOW))


class TestLatestCodexInfo(unittest.TestCase):
    def test_missing_home_returns_none(self):
        self.assertIsNone(s.latest_codex_info("/no/such/codex/home"))

    def test_reads_last_snapshot_of_newest_file(self):
        with tempfile.TemporaryDirectory() as home:
            _write_rollout(home, "11", "rollout-2026-07-11T10-00-00-old.jsonl",
                           [_rl_line(primary_used=50.0)])
            _write_rollout(home, "12", "rollout-2026-07-12T01-00-00-new.jsonl",
                           ['{"type":"event_msg","payload":{"type":"other"}}',
                            _rl_line(primary_used=10.0),
                            _rl_line(primary_used=4.0)])
            info = s.latest_codex_info(home)
            self.assertEqual(info["rate_limits"]["primary"]["used_percent"], 4.0)

    def test_falls_back_when_newest_file_has_no_snapshot(self):
        with tempfile.TemporaryDirectory() as home:
            _write_rollout(home, "11", "rollout-2026-07-11T10-00-00-old.jsonl",
                           [_rl_line(primary_used=42.0)])
            _write_rollout(home, "12", "rollout-2026-07-12T01-00-00-new.jsonl",
                           ['{"type":"event_msg","payload":{"type":"other"}}'])
            info = s.latest_codex_info(home)
            self.assertEqual(info["rate_limits"]["primary"]["used_percent"], 42.0)

    def test_ignores_corrupt_lines(self):
        with tempfile.TemporaryDirectory() as home:
            _write_rollout(home, "12", "rollout-2026-07-12T01-00-00-x.jsonl",
                           [_rl_line(primary_used=7.0), '{broken "rate_limits"'])
            info = s.latest_codex_info(home)
            self.assertEqual(info["rate_limits"]["primary"]["used_percent"], 7.0)

    def test_extracts_model_and_effort_from_turn_context(self):
        with tempfile.TemporaryDirectory() as home:
            _write_rollout(home, "12", "rollout-2026-07-12T01-00-00-x.jsonl",
                           [_tc_line(model="gpt-5.6-sol", effort="high"),
                            _rl_line()])
            info = s.latest_codex_info(home)
            self.assertEqual(info["model"], "gpt-5.6-sol")
            self.assertEqual(info["effort"], "high")

    def test_null_effort_stays_absent(self):
        with tempfile.TemporaryDirectory() as home:
            _write_rollout(home, "12", "rollout-2026-07-12T01-00-00-x.jsonl",
                           [_tc_line(model="gpt-5.6-sol", effort=None)])
            info = s.latest_codex_info(home)
            self.assertEqual(info["model"], "gpt-5.6-sol")
            self.assertIsNone(info.get("effort"))

    def test_last_turn_context_wins(self):
        with tempfile.TemporaryDirectory() as home:
            _write_rollout(home, "12", "rollout-2026-07-12T01-00-00-x.jsonl",
                           [_tc_line(model="gpt-5.5"),
                            _tc_line(model="gpt-5.6-sol")])
            self.assertEqual(s.latest_codex_info(home)["model"], "gpt-5.6-sol")

    def test_merges_model_from_older_file(self):
        with tempfile.TemporaryDirectory() as home:
            _write_rollout(home, "11", "rollout-2026-07-11T10-00-00-old.jsonl",
                           [_tc_line(model="gpt-5.6-sol")])
            _write_rollout(home, "12", "rollout-2026-07-12T01-00-00-new.jsonl",
                           [_rl_line(primary_used=4.0)])
            info = s.latest_codex_info(home)
            self.assertEqual(info["model"], "gpt-5.6-sol")
            self.assertEqual(info["rate_limits"]["primary"]["used_percent"], 4.0)

    def test_freshest_snapshot_wins_regardless_of_file_name(self):
        # The stale-quota bug: a long-running session started EARLIER keeps
        # the freshest rate_limits, while a newer-started (idle) session file
        # holds an old snapshot. The newest event timestamp must win, not the
        # newest file name.
        with tempfile.TemporaryDirectory() as home:
            active = _write_rollout(
                home, "12", "rollout-2026-07-12T00-27-00-active.jsonl",
                [_rl_line(primary_used=12.0, secondary_used=2.0,
                          ts="2026-07-12T02:50:00.000Z")])
            idle = _write_rollout(
                home, "12", "rollout-2026-07-12T01-35-00-idle.jsonl",
                [_rl_line(primary_used=2.0, secondary_used=0.4,
                          ts="2026-07-12T01:35:10.000Z")])
            os.utime(idle, (1_000_000_100, 1_000_000_100))
            os.utime(active, (1_000_000_200, 1_000_000_200))
            info = s.latest_codex_info(home)
            self.assertEqual(info["rate_limits"]["primary"]["used_percent"], 12.0)

    def test_timestamp_formats_compared_as_time_not_text(self):
        # "…00Z" sorts lexicographically AFTER "…00.500Z" although it is the
        # EARLIER instant — timestamps must be compared as time, not text.
        with tempfile.TemporaryDirectory() as home:
            _write_rollout(home, "12", "rollout-2026-07-12T02-00-00-later.jsonl",
                           [_rl_line(primary_used=12.0,
                                     ts="2026-07-12T02:50:00.500Z")])
            _write_rollout(home, "12", "rollout-2026-07-12T02-00-01-earlier.jsonl",
                           [_rl_line(primary_used=2.0,
                                     ts="2026-07-12T02:50:00Z")])
            info = s.latest_codex_info(home)
            self.assertEqual(info["rate_limits"]["primary"]["used_percent"], 12.0)

    def test_valid_json_with_wrong_shapes_is_skipped_not_fatal(self):
        # Lines that parse as JSON but aren't shaped like events must be
        # skipped — an AttributeError here would kill the whole status line.
        with tempfile.TemporaryDirectory() as home:
            _write_rollout(home, "12", "rollout-2026-07-12T01-00-00-x.jsonl",
                           [_rl_line(primary_used=7.0),
                            '["rate_limits"]',
                            '{"type":"event_msg","payload":["rate_limits"]}',
                            '{"type":"turn_context","payload":{"model":"m",'
                            '"collaboration_mode":{"settings":["reasoning_effort"]}}}'])
            info = s.latest_codex_info(home)
            self.assertEqual(info["rate_limits"]["primary"]["used_percent"], 7.0)

    def test_equal_timestamps_keep_first_mtime_ordered_file(self):
        with tempfile.TemporaryDirectory() as home:
            a = _write_rollout(home, "12", "rollout-2026-07-12T01-00-00-a.jsonl",
                               [_rl_line(primary_used=5.0)])
            b = _write_rollout(home, "12", "rollout-2026-07-12T01-00-01-b.jsonl",
                               [_rl_line(primary_used=9.0)])
            os.utime(a, (1_000_000_200, 1_000_000_200))
            os.utime(b, (1_000_000_100, 1_000_000_100))
            info = s.latest_codex_info(home)
            self.assertEqual(info["rate_limits"]["primary"]["used_percent"], 5.0)

    def test_snapshot_timestamps_compared_across_files(self):
        # Even when the stale file has the newer mtime, the event timestamp
        # inside decides which snapshot is current.
        with tempfile.TemporaryDirectory() as home:
            fresh = _write_rollout(
                home, "12", "rollout-2026-07-12T00-27-00-fresh.jsonl",
                [_rl_line(primary_used=12.0, ts="2026-07-12T02:50:00.000Z")])
            stale = _write_rollout(
                home, "12", "rollout-2026-07-12T01-35-00-stale.jsonl",
                [_rl_line(primary_used=2.0, ts="2026-07-12T01:35:10.000Z")])
            os.utime(fresh, (1_000_000_100, 1_000_000_100))
            os.utime(stale, (1_000_000_200, 1_000_000_200))
            info = s.latest_codex_info(home)
            self.assertEqual(info["rate_limits"]["primary"]["used_percent"], 12.0)

    def _write_config(self, home, text):
        with open(os.path.join(home, "config.toml"), "w") as f:
            f.write(text)

    def test_config_defaults_used(self):
        with tempfile.TemporaryDirectory() as home:
            self._write_config(home, 'model = "gpt-5.6-sol"\n'
                                     'model_reasoning_effort = "high"\n')
            info = s.latest_codex_info(home)
            self.assertEqual(info["model"], "gpt-5.6-sol")
            self.assertEqual(info["effort"], "high")

    def test_config_wins_over_session(self):
        with tempfile.TemporaryDirectory() as home:
            self._write_config(home, 'model = "gpt-5.6-sol"\n'
                                     'model_reasoning_effort = "high"\n')
            _write_rollout(home, "12", "rollout-2026-07-12T01-00-00-x.jsonl",
                           [_tc_line(model="gpt-5.5", effort=None)])
            info = s.latest_codex_info(home)
            self.assertEqual(info["model"], "gpt-5.6-sol")
            self.assertEqual(info["effort"], "high")

    def test_config_keys_inside_tables_ignored(self):
        with tempfile.TemporaryDirectory() as home:
            self._write_config(home, 'personality = "pragmatic"\n'
                                     '[projects."/x"]\n'
                                     'model = "not-a-default"\n')
            self.assertIsNone(s.latest_codex_info(home))

    def test_config_strips_comments_and_single_quotes(self):
        with tempfile.TemporaryDirectory() as home:
            self._write_config(home, "model = 'gpt-5.6-sol'  # picked in TUI\n")
            self.assertEqual(s.latest_codex_info(home)["model"], "gpt-5.6-sol")


class TestCodexSegment(unittest.TestCase):
    NOW = 1_783_800_000

    def _rl(self, p=4.0, w=1.0):
        return {
            "primary": {"used_percent": p, "window_minutes": 300,
                        "resets_at": self.NOW + 100},
            "secondary": {"used_percent": w, "window_minutes": 10080,
                          "resets_at": self.NOW + 100},
        }

    def test_quota_shows_both_windows_remaining(self):
        seg = s.codex_quota(self._rl(p=4.0, w=1.0), now_ts=self.NOW)
        self.assertIn("5h 96%", seg)
        self.assertIn("week 99%", seg)
        self.assertNotIn("cdx", seg)

    def test_quota_weekly_alone_in_primary_labeled_week(self):
        # codex-cli 0.144.x: the weekly window moved into `primary` and the
        # 5h window vanished (`secondary: null`). Label must follow the
        # window's duration, not which key it sits under.
        rl = {"primary": {"used_percent": 0.0, "window_minutes": 10080,
                          "resets_at": self.NOW + 100},
              "secondary": None}
        seg = s.codex_quota(rl, now_ts=self.NOW)
        self.assertIn("week 100%", seg)
        self.assertNotIn("5h", seg)

    def test_quota_swapped_keys_keep_labels_and_short_window_first(self):
        rl = {"primary": {"used_percent": 9.0, "window_minutes": 10080,
                          "resets_at": self.NOW + 100},
              "secondary": {"used_percent": 30.0, "window_minutes": 300,
                            "resets_at": self.NOW + 100}}
        seg = s.codex_quota(rl, now_ts=self.NOW)
        self.assertIn("5h 70%", seg)
        self.assertIn("week 91%", seg)
        self.assertLess(seg.index("5h"), seg.index("week"))

    def test_quota_unknown_durations_formatted_exactly(self):
        for minutes, label in ((45, "45m"), (90, "90m"), (360, "6h"),
                               (4320, "3d")):
            rl = {"primary": {"used_percent": 50.0, "window_minutes": minutes,
                              "resets_at": self.NOW + 100}}
            self.assertIn(f"{label} 50%", s.codex_quota(rl, now_ts=self.NOW))

    def test_quota_window_without_duration_is_skipped(self):
        # No window_minutes -> no honest label; a guessed one could lie.
        rl = {"primary": {"used_percent": 5.0, "resets_at": self.NOW + 100},
              "secondary": {"used_percent": 1.0, "window_minutes": 10080,
                            "resets_at": self.NOW + 100}}
        seg = s.codex_quota(rl, now_ts=self.NOW)
        self.assertIn("week 99%", seg)
        self.assertNotIn("5h", seg)
        self.assertIsNone(s.codex_quota(
            {"primary": {"used_percent": 5.0, "resets_at": self.NOW + 100}},
            now_ts=self.NOW))

    def test_quota_nonfinite_numbers_are_skipped_not_crash(self):
        nan = float("nan")
        rl = {"primary": {"used_percent": nan, "window_minutes": 300,
                          "resets_at": self.NOW + 100},
              "secondary": {"used_percent": 1.0, "window_minutes": nan,
                            "resets_at": self.NOW + 100}}
        self.assertIsNone(s.codex_quota(rl, now_ts=self.NOW))

    def test_quota_none_when_no_snapshot(self):
        self.assertIsNone(s.codex_quota(None, now_ts=self.NOW))

    def test_quota_none_when_both_windows_malformed(self):
        self.assertIsNone(s.codex_quota({"primary": {}, "secondary": {}},
                                        now_ts=self.NOW))

    def test_quota_low_remaining_colors(self):
        self.assertIn(s.YELLOW, s.codex_quota(self._rl(p=80.0), now_ts=self.NOW))
        self.assertIn(s.RED, s.codex_quota(self._rl(p=95.0), now_ts=self.NOW))
        self.assertNotIn(s.YELLOW, s.codex_quota(self._rl(), now_ts=self.NOW))
        self.assertNotIn(s.RED, s.codex_quota(self._rl(), now_ts=self.NOW))

    def test_segment_labelled_codex_not_model(self):
        info = {"model": "gpt-5.6-sol", "effort": "high",
                "rate_limits": self._rl()}
        seg = s.codex_segment(info, now_ts=self.NOW)
        self.assertIn("[codex · high]", seg)
        self.assertNotIn("gpt-5.6-sol", seg)
        self.assertIn("5h 96%", seg)

    def test_segment_label_is_dim(self):
        seg = s.codex_segment({"model": "gpt-5.6-sol"}, now_ts=self.NOW)
        self.assertEqual(seg, f"{s.DIM}[codex]{s.RESET}")

    def test_segment_no_effort_when_absent(self):
        seg = s.codex_segment({"model": "gpt-5.6-sol", "effort": None,
                               "rate_limits": self._rl()}, now_ts=self.NOW)
        self.assertIn("[codex]", seg)

    def test_segment_quota_only_still_labelled(self):
        seg = s.codex_segment({"rate_limits": self._rl()}, now_ts=self.NOW)
        self.assertIn("[codex]", seg)
        self.assertIn("5h 96%", seg)

    def test_segment_none_when_empty(self):
        self.assertIsNone(s.codex_segment(None, now_ts=self.NOW))
        self.assertIsNone(s.codex_segment({}, now_ts=self.NOW))


class TestRenderCodex(unittest.TestCase):
    def _data(self):
        return {
            "model": {"display_name": "Opus 4.8"},
            "workspace": {"current_dir": "/x/ContextPlugin"},
            "context_window": {
                "context_window_size": 1000000,
                "remaining_percentage": 81,
                "total_input_tokens": 187000,
            },
            "cost": {"total_cost_usd": 0.08, "total_duration_ms": 423000},
        }

    def _with_codex_home(self, home):
        old = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = home
        return old

    def _restore(self, old):
        if old is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = old

    def test_codex_is_own_third_line(self):
        with tempfile.TemporaryDirectory() as home:
            _write_rollout(home, "12", "rollout-2026-07-12T01-00-00-x.jsonl",
                           [_tc_line(model="gpt-5.6-sol"),
                            _rl_line(primary_used=4.0, secondary_used=1.0)])
            old = self._with_codex_home(home)
            try:
                lines = s.render(self._data()).split("\n")
                self.assertEqual(len(lines), 3)
                self.assertNotIn("5h", lines[1])
                self.assertIn("[codex]", lines[2])
                self.assertNotIn("gpt-5.6-sol", lines[2])
                self.assertIn("5h 96%", lines[2])
                self.assertIn("week 99%", lines[2])
            finally:
                self._restore(old)

    def test_codex_line_weekly_only_shape(self):
        with tempfile.TemporaryDirectory() as home:
            _write_rollout(home, "12", "rollout-2026-07-12T02-00-00-y.jsonl",
                           [_tc_line(model="gpt-5.6-sol"),
                            _rl_line_weekly_only(used=0.0)])
            old = self._with_codex_home(home)
            try:
                line3 = s.render(self._data()).split("\n")[2]
                self.assertIn("week 100%", line3)
                self.assertNotIn("5h", line3)
            finally:
                self._restore(old)

    def test_two_lines_without_codex(self):
        with tempfile.TemporaryDirectory() as home:
            old = self._with_codex_home(home)
            try:
                out = s.render(self._data())
                self.assertNotIn("5h", out)
                self.assertEqual(len(out.split("\n")), 2)
            finally:
                self._restore(old)


if __name__ == "__main__":
    unittest.main()
