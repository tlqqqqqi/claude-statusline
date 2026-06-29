import unittest
import statusline as s
import os


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


if __name__ == "__main__":
    unittest.main()
