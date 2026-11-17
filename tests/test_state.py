import json
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

    def save(self, when, account="a", scope="github.com", source="gh:a"):
        state.save_state(self.tmp.name, {scope: (account, source)}, when)
        return state.load_state(self.tmp.name)

    def test_full_scan_when_state_cannot_be_trusted(self):
        since, how = state.api_since({}, "github.com", "a", True, False)
        self.assertEqual((since, how), (self.full, "full (no saved state)"))
        st = self.save(datetime.now(timezone.utc))
        self.assertIn("full (--full-api)", state.api_since(st, "github.com", "a", True, True)[1])
        self.assertIn("mirror is empty", state.api_since(st, "github.com", "a", False, False)[1])
        self.assertIn("account changed", state.api_since(st, "github.com", "someone-else", True, False)[1])
        self.assertIn("no saved state", state.api_since(st, "gitlab.com", "a", True, False)[1])   # another host

    def test_incremental_goes_back_by_the_safety_margin(self):
        st = self.save(datetime.now(timezone.utc))
        since, how = state.api_since(st, "github.com", "a", True, False)
        self.assertEqual(since, self.today - timedelta(days=API_MARGIN_DAYS))
        self.assertTrue(how.startswith("incremental"))

    def test_old_state_never_reaches_before_the_graph_window(self):
        st = self.save(datetime.now(timezone.utc) - timedelta(days=900))
        self.assertEqual(state.api_since(st, "github.com", "a", True, False)[0], self.full)

    def test_unreadable_or_old_format_state_is_ignored(self):
        with open(state.state_path(self.tmp.name), "w") as f:
            f.write("not json")
        self.assertEqual(state.load_state(self.tmp.name), {})
        with open(state.state_path(self.tmp.name), "w") as f:      # the single-account format of earlier versions
            json.dump({"account": "github.com/a", "last_success": "2026-01-01T00:00:00+00:00"}, f)
        self.assertEqual(state.load_state(self.tmp.name), {})

    def test_hosts_are_saved_separately_and_kept(self):
        now = datetime.now(timezone.utc)
        self.save(now, "a", "github.com", "gh:a")
        st = self.save(now, "b", "gitlab.com", "glab")
        self.assertEqual(state.preferred_accounts(st), {"github.com": ("gh:a", "a"), "gitlab.com": ("glab", "b")})

    def test_probe_results_are_remembered(self):
        state.save_state(self.tmp.name, {}, None, {"git.corp.example": "gitlab"})
        st = state.load_state(self.tmp.name)
        self.assertEqual(state.known_hosts(st), {"git.corp.example": "gitlab"})
        self.assertEqual(st["scopes"], {})


if __name__ == "__main__":
    unittest.main()
