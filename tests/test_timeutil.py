import unittest

import _path  # noqa: F401
from contribution_mirror.timeutil import parse_iso, to_utc_z


class Timestamps(unittest.TestCase):
    def test_every_vendor_format_becomes_utc_z(self):
        cases = {
            "2026-11-16T15:44:09Z": "2026-11-16T15:44:09Z",                 # GitHub
            "2026-04-28T00:23:45+05:00": "2026-04-27T19:23:45Z",            # offsets are converted, not dropped
            "2026-01-01T10:00:00.123Z": "2026-01-01T10:00:00Z",             # GitLab: milliseconds
            "2026-01-01T10:00:00.1234567Z": "2026-01-01T10:00:00Z",         # Azure DevOps: 7 digits (py<3.11 chokes)
            "2026-01-01T10:00:00+0530": "2026-01-01T04:30:00Z",             # offset without colon
            "2026-01-01T10:00:00.5-03:30": "2026-01-01T13:30:00Z",
            "2026-01-01T10:00:00": "2026-01-01T10:00:00Z",                  # naive = UTC
        }
        for given, expected in cases.items():
            self.assertEqual(to_utc_z(given), expected, given)

    def test_result_matches_what_git_prints_for_utc(self):
        # git's %aI prints a UTC date as ...Z, so API timestamps dedupe against commits already in the mirror
        self.assertTrue(to_utc_z("2026-02-03T04:05:06+00:00").endswith("Z"))

    def test_parse_iso_is_timezone_aware(self):
        self.assertIsNotNone(parse_iso("2026-01-01T00:00:00").tzinfo)


if __name__ == "__main__":
    unittest.main()
