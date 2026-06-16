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


if __name__ == "__main__":
    unittest.main()
