import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import _path  # noqa: F401
from contribution_mirror import state
from contribution_mirror.settings import API_MARGIN_DAYS, API_WINDOW_DAYS


class IncrementalWindow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.today = datetime.now(timezone.utc).date()
        self.full = self.today - timedelta(days=API_WINDOW_DAYS)

    def test_full_scan_when_state_cannot_be_trusted(self):
        since, how = state.api_since(self.tmp.name, "a", True, False)
        self.assertEqual((since, how), (self.full, "full (no saved state)"))
        state.save_state(self.tmp.name, "a", datetime.now(timezone.utc))
        self.assertIn("full (--full-api)", state.api_since(self.tmp.name, "a", True, True)[1])
        self.assertIn("mirror is empty", state.api_since(self.tmp.name, "a", False, False)[1])
        self.assertIn("account changed", state.api_since(self.tmp.name, "someone-else", True, False)[1])

    def test_incremental_goes_back_by_the_safety_margin(self):
        state.save_state(self.tmp.name, "a", datetime.now(timezone.utc))
        since, how = state.api_since(self.tmp.name, "a", True, False)
        self.assertEqual(since, self.today - timedelta(days=API_MARGIN_DAYS))
        self.assertTrue(how.startswith("incremental"))

    def test_old_state_never_reaches_before_the_graph_window(self):
        state.save_state(self.tmp.name, "a", datetime.now(timezone.utc) - timedelta(days=900))
        self.assertEqual(state.api_since(self.tmp.name, "a", True, False)[0], self.full)

    def test_unreadable_state_falls_back_to_full(self):
        with open(state.state_path(self.tmp.name), "w") as f:
            f.write("not json")
        self.assertIn("full", state.api_since(self.tmp.name, "a", True, False)[1])


if __name__ == "__main__":
    unittest.main()
