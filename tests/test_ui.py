"""The local setup/sync page: security checks, editing .env and repos.json, and real preview/sync runs."""
import http.client
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest import mock

import _path
from contribution_mirror import envfile, ui


class Server(unittest.TestCase):
    """A real server on 127.0.0.1 with its own temporary config files."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, _path.GIT_ENV)
        env.start()
        self.addCleanup(env.stop)
        for k in (*ui.FIELDS, *ui.TOKEN_VARS, "MIRROR_LOG_DIR", "MIRROR_ENV_FILE", "MIRROR_REPOS_FILE"):
            os.environ.pop(k, None)
        t = self.tmp.name
        self.env_path, self.repos_path = os.path.join(t, "config", ".env"), os.path.join(t, "config", "repos.json")
        self.example = os.path.join(t, "example.env")
        with open(self.example, "w", encoding="utf-8") as f:
            f.write("# a comment that must survive\nWORK_EMAIL=you@company.com\nWORK_TOKEN=\n")
        self.schedule_calls = []
        self.app = ui.App(self.env_path, self.repos_path, self.example, schedule_ops=self.fake_scheduler())
        self.addCleanup(self.app.close)
        self.httpd, self.url, self.token = ui.make_server(self.app)
        self.port = self.httpd.server_address[1]
        self.serving = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.serving.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path, body=None, token=True, host=None, origin=None, ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers["X-Mirror-Token"] = self.token if token is True else token
        if origin:
            headers["Origin"] = origin
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = ctype
        try:
            conn.request(method, path, body=data, headers=headers)
            r = conn.getresponse()
            raw = r.read()
            return r.status, (json.loads(raw) if raw and r.getheader("Content-Type", "").startswith("application/json")
                              else raw), dict(r.getheaders())
        finally:
            conn.close()

    def fake_scheduler(self):
        """The page never touches the real OS scheduler in tests (that is covered in test_platforms)."""
        self.sched = {"supported": True, "platform": "linux", "login": False, "daily": False, "time": None, "timezone": ""}

        def apply(login, daily, time_of_day, timezone, say=print):
            self.schedule_calls.append((login, daily, time_of_day, timezone))
            if time_of_day == "13:13":
                raise ui.scheduler.ScheduleError("`systemctl --user enable` failed: no user session")
            say("running: systemctl --user daemon-reload")
            self.sched.update(login=login, daily=daily, time=time_of_day if daily else None, timezone=timezone)
            return dict(self.sched)
        return (lambda: dict(self.sched)), apply

    def settings(self, **over):
        s = {"WORK_EMAIL": "me@work.com", "PERSONAL_NAME": "Me", "PERSONAL_EMAIL": "me@personal.com",
             "EXPECTED_REMOTE": "git@github.com:me/mirror.git", "MIRROR_DIR": "", "MIRROR_PR_REVIEWS": "on",
             "MIRROR_CLONE_CACHE": ""}
        s.update(over)
        return s


class Security(Server):
    def test_the_link_carries_the_token_in_the_fragment_only(self):
        self.assertTrue(self.url.startswith(f"http://127.0.0.1:{self.port}/#t="))
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    def test_the_browser_gets_a_one_time_code_that_is_traded_for_the_token_once(self):
        """Other users can read the browser's command line: it carries a code that works once, never the token."""
        launch = self.httpd.launch_url
        self.assertTrue(launch.startswith(f"http://127.0.0.1:{self.port}/#c="))
        self.assertNotIn(self.token, launch)
        code = launch.split("#c=", 1)[1]
        self.assertEqual(self.request("POST", "/api/session", {"code": "guess"}, token=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/session", {"code": code}, token=False,
                                      origin="https://evil.example")[0], 403)
        status, body, _ = self.request("POST", "/api/session", {"code": code}, token=False)
        self.assertEqual((status, body), (200, {"token": self.token}))
        status, body, _ = self.request("POST", "/api/session", {"code": code}, token=False)
        self.assertEqual(status, 403)
        self.assertIn("already used", body["error"])
        self.serving.join(timeout=10)                                  # a second use stops the server
        self.assertFalse(self.serving.is_alive())

    def test_malformed_requests_are_400_not_crashes(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest("POST", "/api/settings", skip_host=True)
            for k, v in (("Host", f"127.0.0.1:{self.port}"), ("X-Mirror-Token", self.token),
                         ("Content-Type", "application/json"), ("Content-Length", "-1")):
                conn.putheader(k, v)
            conn.endheaders()
            r = conn.getresponse()
            self.assertEqual(r.status, 400)
            r.read()
        finally:
            conn.close()
        self.assertEqual(self.request("GET", "/api/logs?limit=abc")[0], 400)
        self.assertEqual(self.request("GET", "/api/job?since=x")[0], 400)

    def test_every_control_explains_itself_on_hover(self):
        """Each button, field, choice and expandable section has a tooltip (its own title, or its label's)."""
        from html.parser import HTMLParser

        class Controls(HTMLParser):
            def __init__(self):
                super().__init__()
                self.labels, self.missing = [], []

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag == "label":
                    self.labels.append(bool(a.get("title")))
                elif tag in ("button", "input", "select", "summary") and not a.get("title") \
                        and not (self.labels and self.labels[-1]):
                    self.missing.append(a.get("id") or a.get("name") or tag)

            def handle_endtag(self, tag):
                if tag == "label" and self.labels:
                    self.labels.pop()

        page = Controls()
        with open(os.path.join(ui.STATIC, "index.html"), encoding="utf-8") as f:
            page.feed(f.read())
        self.assertEqual(page.missing, [])

    def test_the_page_loads_and_is_locked_down(self):
        status, body, headers = self.request("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"Contribution Mirror", body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual((headers["X-Frame-Options"], headers["Referrer-Policy"]), ("DENY", "no-referrer"))
        for path in ("/app.js", "/app.css", "/theme.js"):
            self.assertEqual(self.request("GET", path, token=False)[0], 200)

    def test_favicon_and_theme_switch(self):
        status, body, headers = self.request("GET", "/favicon.svg", token=False)
        self.assertEqual((status, headers["Content-Type"]), (200, "image/svg+xml"))
        self.assertIn(b"<svg", body)
        page = self.request("GET", "/", token=False)[1]
        self.assertIn(b'rel="icon" href="/favicon.svg"', page)
        self.assertLess(page.index(b'src="/theme.js"'), page.index(b"<body"))     # applied before the page is drawn
        css = self.request("GET", "/app.css", token=False)[1]
        self.assertIn(b':root[data-theme="dark"]', css)
        self.assertIn(b':root:not([data-theme="light"])', css)

    def test_api_needs_the_token(self):
        self.assertEqual(self.request("GET", "/api/state", token=False)[0], 403)
        self.assertEqual(self.request("GET", "/api/state", token="wrong")[0], 403)
        self.assertEqual(self.request("GET", "/api/state")[0], 200)
        self.assertEqual(self.request("POST", "/api/settings", self.settings(), token=False)[0], 403)
        self.assertFalse(os.path.exists(self.env_path))

    def test_another_host_name_is_refused_dns_rebinding(self):
        self.assertEqual(self.request("GET", "/", host="evil.example")[0], 403)
        self.assertEqual(self.request("GET", "/api/state", host=f"evil.example:{self.port}")[0], 403)

    def test_writes_from_another_site_are_refused(self):
        status, _, _ = self.request("POST", "/api/settings", self.settings(), origin="https://evil.example")
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/settings", self.settings(), ctype="text/plain")
        self.assertEqual(status, 415)                                    # what a plain cross-site form could send
        self.assertFalse(os.path.exists(self.env_path))
        status, _, _ = self.request("POST", "/api/settings", self.settings(), origin=f"http://127.0.0.1:{self.port}")
        self.assertEqual(status, 200)

    def test_token_values_never_reach_the_page(self):
        os.makedirs(os.path.dirname(self.env_path))
        with open(self.env_path, "w") as f:
            f.write("WORK_EMAIL=me@work.com\nWORK_GITHUB_TOKEN=ghp_SECRET\n")
        status, state, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertNotIn("ghp_SECRET", json.dumps(state))
        self.assertEqual(state["tokens"], ["WORK_GITHUB_TOKEN"])


class Settings(Server):
    def test_first_save_starts_from_the_example_and_is_private(self):
        status, state, _ = self.request("POST", "/api/settings", self.settings(WORK_EMAIL="a@w.com, b@w.com"))
        self.assertEqual(status, 200, state)
        with open(self.env_path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("# a comment that must survive", text)
        values = envfile.parse(text)
        self.assertEqual(values["WORK_EMAIL"], "a@w.com b@w.com")
        self.assertEqual(values["EXPECTED_REMOTE"], "git@github.com:me/mirror.git")
        self.assertEqual(text.count("WORK_EMAIL="), 1)                  # replaced in place, not appended
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(self.env_path).st_mode), 0o600)
        self.assertEqual(state["missing"], [])

    def test_other_keys_and_tokens_are_kept(self):
        os.makedirs(os.path.dirname(self.env_path))
        with open(self.env_path, "w") as f:
            f.write("export WORK_EMAIL=old@w.com\nWORK_GITHUB_TOKEN=keep-me\nGIT_HOST_PROVIDERS=git.corp=gitlab\n")
        self.request("POST", "/api/settings", self.settings(MIRROR_PR_REVIEWS="off"))
        with open(self.env_path) as f:
            values = envfile.parse(f.read())
        self.assertEqual((values["WORK_EMAIL"], values["WORK_GITHUB_TOKEN"], values["GIT_HOST_PROVIDERS"],
                          values["MIRROR_PR_REVIEWS"]), ("me@work.com", "keep-me", "git.corp=gitlab", "off"))

    def test_values_that_need_quotes_read_back_unchanged(self):
        self.request("POST", "/api/settings", self.settings(PERSONAL_NAME='"Quoted"'))
        _, state, _ = self.request("GET", "/api/state")
        self.assertEqual(state["settings"]["PERSONAL_NAME"], '"Quoted"')

    def test_date_limits_are_checked_and_saved(self):
        status, body, _ = self.request("POST", "/api/settings", self.settings(MIRROR_SINCE="2026-13-01"))
        self.assertEqual(status, 400)
        self.assertIn("From:", body["error"])
        status, body, _ = self.request("POST", "/api/settings", self.settings(MIRROR_SINCE="2026-05-01",
                                                                              MIRROR_UNTIL="2026-01-01"))
        self.assertIn("'from' is after 'until'", body["error"])
        status, state, _ = self.request("POST", "/api/settings", self.settings(MIRROR_SINCE="2025-03-01"))
        self.assertEqual((status, state["settings"]["MIRROR_SINCE"]), (200, "2025-03-01"))

    def test_profiles_have_their_own_files(self):
        _, state, _ = self.request("GET", "/api/state")
        self.assertEqual((state["profile"], state["profiles"]), ("", [""]))
        self.assertTrue(state["version"])
        self.request("POST", "/api/settings", self.settings(WORK_EMAIL="default@w.com"))
        self.assertEqual(self.request("POST", "/api/profile", {"name": "../x"})[0], 400)
        status, state, _ = self.request("POST", "/api/profile", {"name": "acme"})
        self.assertEqual((status, state["profile"]), (200, "acme"))
        self.assertEqual(state["settings"]["WORK_EMAIL"], "")                # a new profile starts empty
        self.request("POST", "/api/settings", self.settings(WORK_EMAIL="acme@w.com"))
        acme = os.path.join(os.path.dirname(self.env_path), "profiles", "acme", ".env")
        self.assertEqual(envfile.read(acme)["WORK_EMAIL"], "acme@w.com")
        self.assertEqual(envfile.read(self.env_path)["WORK_EMAIL"], "default@w.com")   # untouched
        self.assertEqual(self.app.child_env()["MIRROR_PROFILE"], "acme")
        _, state, _ = self.request("POST", "/api/profile", {"name": ""})
        self.assertEqual((state["settings"]["WORK_EMAIL"], state["profiles"]), ("default@w.com", ["", "acme"]))
        self.assertNotIn("MIRROR_PROFILE", self.app.child_env())

    def test_bad_values_are_explained_and_nothing_is_written(self):
        status, body, _ = self.request("POST", "/api/settings", self.settings(WORK_EMAIL="not-an-email",
                                                                              PERSONAL_EMAIL="", EXPECTED_REMOTE=""))
        self.assertEqual(status, 400)
        for words in ("not-an-email", "Personal email", "Mirror repo"):
            self.assertIn(words, body["error"])
        status, body, _ = self.request("POST", "/api/settings", self.settings(PERSONAL_NAME="a\nWORK_TOKEN=x"))
        self.assertEqual(status, 400)                                    # no injecting a second line
        self.assertFalse(os.path.exists(self.env_path))


class Projects(Server):
    def test_save_shows_the_status_of_each_entry_and_migrates_the_old_key(self):
        repo = _path.make_repo(os.path.join(self.tmp.name, "work", "app"), [])
        os.makedirs(os.path.dirname(self.repos_path))
        with open(self.repos_path, "w") as f:
            json.dump({"repos": [repo], "remote_repos": ["git@github.com:acme/api.git"]}, f)
        _, state, _ = self.request("GET", "/api/state")
        self.assertTrue(state["repos_legacy"])
        missing = os.path.join(self.tmp.name, "nope")
        status, state, _ = self.request("POST", "/api/repos", {"repos": [repo, "git@github.com:acme/api.git", missing,
                                                                         repo, "  "]})
        self.assertEqual(status, 200)
        self.assertEqual([(r["kind"], r["ok"]) for r in state["repos"]], [("path", True), ("url", True), ("path", False)])
        self.assertEqual(state["repos"][2]["detail"], "folder not found")
        with open(self.repos_path) as f:
            self.assertEqual(json.load(f), {"repos": [repo, "git@github.com:acme/api.git", missing]})
        self.assertFalse(state["repos_legacy"])

    def test_find_repos_in_a_folder(self):
        base = os.path.join(self.tmp.name, "work")
        a = _path.make_repo(os.path.join(base, "a"), [])
        b = _path.make_repo(os.path.join(base, "group", "b"), [])
        os.makedirs(os.path.join(base, "a", "node_modules", "x"))
        status, body, _ = self.request("POST", "/api/find-repos", {"folder": base})
        self.assertEqual((status, sorted(body["found"]), body["complete"]), (200, sorted([a, b]), True))
        self.assertEqual(self.request("POST", "/api/find-repos", {"folder": base + "-missing"})[0], 400)

    def test_check_remote(self):
        bare = os.path.join(self.tmp.name, "mirror.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        _, body, _ = self.request("POST", "/api/check-remote", {"remote": bare})
        self.assertTrue(body["ok"])
        self.assertIn("empty repo", body["message"])
        _, body, _ = self.request("POST", "/api/check-remote", {"remote": bare + "-missing"})
        self.assertFalse(body["ok"])


class Runs(Server):
    """Preview and sync as the page runs them: a real `mirror.py sync` child process."""
    def setUp(self):
        super().setUp()
        t = self.tmp.name
        self.remote = os.path.join(t, "personal.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.remote], check=True)
        self.work = _path.make_repo(os.path.join(t, "work"), [("me@work.com", "2026-01-01T10:00:00+05:00"),
                                                              ("me@work.com", "2026-01-02T10:00:00+00:00")])
        self.request("POST", "/api/settings", self.settings(EXPECTED_REMOTE=self.remote,
                                                            MIRROR_DIR=os.path.join(t, "mirror"), MIRROR_PR_REVIEWS="off"))
        with open(self.env_path, "a") as f:
            f.write(f"MIRROR_LOG_DIR={os.path.join(t, 'logs')}\n")
        self.request("POST", "/api/repos", {"repos": [self.work]})

    def wait(self):
        deadline = time.time() + 60
        while time.time() < deadline:
            _, view, _ = self.request("GET", "/api/job?since=0")
            if not view["running"]:
                return view
            time.sleep(0.1)
        self.fail("run did not finish")

    def test_preview_then_sync_then_nothing_new(self):
        _, state, _ = self.request("GET", "/api/state")
        self.assertTrue(state["ready"], state)
        status, _, _ = self.request("POST", "/api/run", {"mode": "preview"})
        self.assertEqual(status, 200)
        view = self.wait()
        self.assertEqual(view["exit_code"], 0, view["lines"])
        self.assertEqual(view["result"]["summary"]["new"], 2)
        self.assertEqual(view["result"]["sources"], [{"name": "work", "matching": 2, "mirrored": 0, "new": 2}])
        self.assertEqual(view["days"], {"existing": {}, "new": {"2026-01-01": 1, "2026-01-02": 1}})   # the calendar
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "mirror")))       # a preview writes nothing

        self.request("POST", "/api/run", {"mode": "sync"})
        view = self.wait()
        self.assertEqual((view["exit_code"], view["result"]["pushed"]), (0, "pushed"), view["lines"])
        count = subprocess.run(["git", "-C", self.remote, "rev-list", "--count", "main"], capture_output=True,
                               text=True).stdout.strip()
        self.assertEqual(count, "2")

        self.request("POST", "/api/run", {"mode": "sync"})
        view = self.wait()
        self.assertEqual((view["result"]["summary"]["new"], view["result"]["pushed"]), (0, "nothing to push"))
        self.assertEqual(view["days"]["existing"], {"2026-01-01": 1, "2026-01-02": 1})
        _, logs, _ = self.request("GET", "/api/logs")                  # the runs appear in the log view
        self.assertEqual([(r["mode"], r["new"]) for r in logs["runs"]], [("dry-run", 2), ("apply", 2), ("apply", 0)])

    def test_check_my_setup_lists_every_check_with_the_pages_own_scheduler(self):
        self.sched.update(daily=True, time="18:00")
        status, body, _ = self.request("POST", "/api/doctor", {})
        self.assertEqual(status, 200, body)
        got = {c["name"]: c["status"] for c in body["checks"]}
        self.assertEqual((got["Settings file"], got["Mirror repo"], got["Work email"]), ("ok", "ok", "ok"))
        self.assertEqual(got["Automatic runs"], "warn")                      # set up, but no sync has run yet
        names = [c["name"] for c in body["checks"]]
        self.assertLess(names.index("Automatic runs"), names.index("Log files"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "mirror")))   # checking changes nothing

    def test_accounts_lists_each_host_without_any_token(self):
        subprocess.run(["git", "-C", self.work, "remote", "add", "origin", "git@git.example.invalid:acme/app.git"],
                       check=True)
        other = _path.make_repo(os.path.join(self.tmp.name, "no-remote"), [])
        self.request("POST", "/api/repos", {"repos": [self.work, other]})
        os.environ["GIT_HOST_PROVIDERS"] = "git.example.invalid=generic"      # no probe of a made-up host
        status, body, _ = self.request("POST", "/api/accounts", {"recheck": False})
        self.assertEqual(status, 200, body)
        self.assertEqual([(h["scope"], h["api"], h["login"]) for h in body["hosts"]], [("git.example.invalid", False, None)])
        self.assertEqual(body["local_without_remote"], 1)

    def test_only_one_run_at_a_time_and_unknown_modes_are_refused(self):
        self.assertEqual(self.request("POST", "/api/run", {"mode": "rm -rf"})[0], 400)
        self.request("POST", "/api/run", {"mode": "preview"})
        status, body, _ = self.request("POST", "/api/run", {"mode": "preview"})
        if status == 400:                                             # the first one may already have finished
            self.assertIn("already in progress", body["error"])
        self.wait()


@unittest.skipIf(os.name == "nt", "Windows ends the process tree with taskkill instead")
class Cancel(unittest.TestCase):
    def test_cancel_stops_the_programs_a_run_started_too(self):
        """A run's git processes must stop with it, or they keep a temporary clone busy after Cancel."""
        with tempfile.TemporaryDirectory() as d:
            pid_file = os.path.join(d, "pid")
            script = textwrap.dedent(f"""
                import subprocess, sys, time
                child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
                with open({pid_file!r}, "w") as f:
                    f.write(str(child.pid))
                print("ready", flush=True)
                time.sleep(60)
            """)
            job = ui.Job("sync", [sys.executable, "-I", "-c", script], dict(os.environ))
            deadline = time.monotonic() + 30
            while not job.lines and time.monotonic() < deadline:
                time.sleep(0.05)
            with open(pid_file) as f:
                grandchild = int(f.read())
            job.cancel()
            job.reader.join(timeout=30)
            self.assertFalse(job.running)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    os.kill(grandchild, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("the run's own child process survived Cancel")


class SameFilesAsScheduledRuns(Server):
    """A scheduled run must use exactly what the page saved: same files, same values."""
    def test_the_page_and_scheduled_runs_use_the_same_files_by_default(self):
        from contribution_mirror import cli, config, settings
        app = ui.App()
        self.addCleanup(app.close)
        self.assertEqual(app.env_path, config.DEFAULT_ENV_FILE)          # what `mirror.py scheduled` loads
        self.assertEqual(app.repos_path, settings.REPOS_FILE)
        self.assertIsNone(cli.build_parser().get_default("repos_file"))      # resolved like below at run time
        self.assertEqual(config.repos_path(), settings.REPOS_FILE)

    def test_what_the_page_saves_is_what_a_run_reads(self):
        from contribution_mirror import config, sync
        self.request("POST", "/api/settings", self.settings(WORK_EMAIL="a@w.com b@w.com", MIRROR_DIR="~/mirror-x",
                                                            MIRROR_PR_REVIEWS="off"))
        cfg = config.load_config(self.env_path)
        self.assertEqual((cfg.work_emails, cfg.personal_name, cfg.expected_remote, cfg.pr_reviews),
                         (["a@w.com", "b@w.com"], "Me", "git@github.com:me/mirror.git", False))
        self.assertEqual(cfg.mirror_dir, os.path.join(os.path.expanduser("~"), "mirror-x"))
        self.assertIn("--no-api", sync.core_args(cfg))


class ScheduleView(Server):
    def test_turn_on_change_time_and_turn_off(self):
        _, state, _ = self.request("GET", "/api/schedule")
        self.assertEqual(state["summary"], "automatic runs: off")
        status, state, _ = self.request("POST", "/api/schedule", {"login": True, "daily": True, "time": "14:00"})
        self.assertEqual(status, 200, state)
        self.assertEqual(state["summary"], "automatic runs: at login and every day at 14:00")
        self.assertEqual(state["steps"], ["running: systemctl --user daemon-reload"])
        self.request("POST", "/api/schedule", {"login": False, "daily": True, "time": "09:30"})
        self.request("POST", "/api/schedule", {"login": False, "daily": False, "time": "09:30"})
        self.assertEqual(self.schedule_calls, [(True, True, "14:00", ""), (False, True, "09:30", ""),
                                               (False, False, "09:30", "")])
        _, state, _ = self.request("GET", "/api/schedule")
        self.assertEqual(state["summary"], "automatic runs: off")

    def test_bad_input_and_scheduler_errors_are_messages(self):
        for body, words in (({"login": True, "time": "25:00"}, "HH:MM"),
                            ({"daily": True, "time": "10:00", "timezone": "x; rm -rf"}, "time zone"),
                            ({"daily": True, "time": "13:13"}, "no user session")):
            status, r, _ = self.request("POST", "/api/schedule", body)
            self.assertEqual(status, 400)
            self.assertIn(words, r["error"])
        self.assertEqual(self.request("POST", "/api/schedule", {"daily": True}, token=False)[0], 403)


class LogView(Server):
    def test_newest_first_with_runs_and_no_tokens(self):
        logs = os.path.join(self.tmp.name, "logs")
        os.makedirs(logs)
        self.request("POST", "/api/settings", self.settings())
        with open(self.env_path, "a") as f:
            f.write(f"MIRROR_LOG_DIR={logs}\n")
        with open(os.path.join(logs, "mirror.log"), "w", encoding="utf-8") as f:
            f.write("2026-11-01 18:00:01,123 INFO SUMMARY mode=apply repos=3 matching=10 already_mirrored=8 created=2 | x\n"
                    "2026-11-02 18:00:01,123 WARNING clone of https://u:ghp_SECRET@github.com/o/r failed\n"
                    "not a log line\n"
                    "2026-11-03 18:00:01,123 INFO SUMMARY mode=dry-run repos=3 matching=10 already_mirrored=10 would_create=0 | -\n")
        status, body, _ = self.request("GET", "/api/logs")
        self.assertEqual(status, 200)
        self.assertEqual([e["time"][:10] for e in body["entries"]], ["2026-11-03", "2026-11-02", "2026-11-01"])
        self.assertEqual([(r["mode"], r["new"]) for r in body["runs"]], [("apply", 2), ("dry-run", 0)])
        self.assertNotIn("ghp_SECRET", json.dumps(body))

    def test_no_log_yet_is_empty_not_an_error(self):
        os.environ["MIRROR_LOG_DIR"] = os.path.join(self.tmp.name, "no-logs-yet")     # never the real logs/
        status, body, _ = self.request("GET", "/api/logs")
        self.assertEqual((status, body["entries"], body["runs"]), (200, [], []))


class Parsing(unittest.TestCase):
    def test_result_lines_become_numbers(self):
        r = ui.parse_result(["repo /home/u/work/app: matching=5 already_mirrored=3 new=2",
                             "repo remote:api: matching=1 already_mirrored=0 new=1",
                             "repo api:pull-requests: matching=4 already_mirrored=4 new=0",
                             "SUMMARY mode=apply repos=2 matching=10 already_mirrored=7 created=3 | new per repo: app=2",
                             "pushed"])
        self.assertEqual([s["name"] for s in r["sources"]], ["app", "remote:api", "Pull requests"])
        self.assertEqual(r["summary"], {"mode": "apply", "repos": 2, "matching": 10, "mirrored": 7, "new": 3})
        self.assertEqual(r["pushed"], "pushed")

    def test_env_lines_are_replaced_in_place(self):
        text = "# c\nA=1\nexport B=2\n# B=commented\nB=3\n"
        self.assertEqual(envfile.update_text(text, {"B": "x", "C": "y z"}), "# c\nA=1\nB=x\n# B=commented\n\nC=y z\n")


if __name__ == "__main__":
    unittest.main()
