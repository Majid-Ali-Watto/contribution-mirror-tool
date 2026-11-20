"""`ui`: a small local web page for setting up and running the mirror (standard library only).

    python3 -I src/mirror.py ui [--port N] [--no-browser]

Security model: the server listens on the loopback address only, and every API call must carry a random token that
is created at start. The browser is opened with a one-time launch code instead of the token (a program's arguments,
and so the link given to the browser, can be read by other users of the computer): the page trades the code for the
token once, and a second use of the code stops the server, since then someone else had it too. The link printed in
the terminal carries the token itself (the page removes it from the address bar). Requests must name this server in
their Host header (no DNS rebinding) and writes must come from this page's own origin with a JSON body (no
cross-site form posts). The page never receives a token value: it only learns whether one
is set. Runs (preview / sync) are separate `mirror.py sync` processes, so they read the saved settings fresh, cannot
take the page down, and share the run lock with the scheduler."""
import argparse
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import doctor, envfile
from . import repos as repolist
from . import schedule as scheduler
from . import __version__
from .config import DEFAULT_ENV_FILE, REQUIRED, check_profile_name, current_profile, list_profiles, profile_paths
from .logsetup import redact
from .push import check_remote
from .providers import WORK_TOKEN_VARS
from .remotes import is_url, repo_path
from .report import parse_result, read_logs
from .settings import ENV_TEMPLATE, LOG_DIR, REPOS_FILE, self_command
from .timeutil import parse_day

LOOPBACK = "127.0.0.1"
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_static")
STATIC_FILES = {"/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/app.css": ("app.css", "text/css; charset=utf-8"),
                "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
                "/favicon.svg": ("favicon.svg", "image/svg+xml"),
                "/favicon.ico": ("favicon.svg", "image/svg+xml")}       # browsers that ask for the old name anyway
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TOKEN_VARS = WORK_TOKEN_VARS
# the settings this page edits; everything else in .env (tokens, comments, advanced keys) is left as it is
FIELDS = ("WORK_EMAIL", "PERSONAL_NAME", "PERSONAL_EMAIL", "EXPECTED_REMOTE", "MIRROR_DIR", "MIRROR_PR_REVIEWS",
          "MIRROR_CLONE_CACHE", "MIRROR_SINCE", "MIRROR_UNTIL")
MAX_LINES = 5000
MAX_BODY = 1_000_000


class BadRequest(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------- checks

def validate_settings(data):
    """Clean the submitted settings or raise BadRequest with every problem in one message."""
    if not isinstance(data, dict):
        raise BadRequest("expected an object")
    get = lambda k: data.get(k, "")
    values, problems = {}, []
    for k in FIELDS:
        v = get(k)
        if not isinstance(v, str) or "\n" in v or "\r" in v:
            problems.append(f"{k}: must be one line of text")
        else:
            values[k] = v.strip()
    if problems:
        raise BadRequest("; ".join(problems))
    emails = [e for e in re.split(r"[\s,;]+", values["WORK_EMAIL"]) if e]
    if not emails:
        problems.append("Work email: at least one is needed")
    problems += [f"Work email: '{e}' does not look like an email address" for e in emails if not EMAIL_RE.match(e)]
    values["WORK_EMAIL"] = " ".join(dict.fromkeys(emails))
    if not values["PERSONAL_NAME"]:
        problems.append("Your name: needed for the mirror commits")
    if not EMAIL_RE.match(values["PERSONAL_EMAIL"]):
        problems.append("Personal email: needed, and it must be verified on your personal account")
    remote = values["EXPECTED_REMOTE"]
    if not remote:
        problems.append("Mirror repo: needed (the URL of your personal repo)")
    elif is_url(remote) and "/" not in repo_path(remote):
        problems.append("Mirror repo: the URL must name owner/repo, e.g. git@github.com:you/work-contributions.git")
    if values["MIRROR_PR_REVIEWS"].lower() not in ("", "on", "auto", "off"):
        problems.append("Pull requests and reviews: on or off")
    values["MIRROR_PR_REVIEWS"] = "off" if values["MIRROR_PR_REVIEWS"].lower() == "off" else ""
    for key, what in (("MIRROR_SINCE", "From"), ("MIRROR_UNTIL", "Until")):
        if values[key]:
            try:
                values[key] = parse_day(values[key]).isoformat()
            except ValueError as e:
                problems.append(f"{what}: {e}")
    if values["MIRROR_SINCE"] and values["MIRROR_UNTIL"] and values["MIRROR_SINCE"] > values["MIRROR_UNTIL"]:
        problems.append("Dates: 'from' is after 'until'")
    if problems:
        raise BadRequest("; ".join(problems))
    return values


# ---------------------------------------------------------------------------------------------------- runs

class Job:
    """The one preview or sync running (or last run): a `mirror.py sync` child process and its output.
    `dates_path`: where the run writes its dates per day (for the calendar), read once it has finished."""
    def __init__(self, mode, cmd, env, dates_path=None):
        self.mode, self.lines, self.code, self.started = mode, [], None, time.time()
        self.dates_path, self._days = dates_path, None
        self.lock = threading.Lock()
        # its own process group, so Cancel stops the git processes it started too (see cancel)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace", env=env, creationflags=flags,
                                     start_new_session=os.name != "nt")
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        with self.proc.stdout:
            for line in self.proc.stdout:
                with self.lock:
                    self.lines.append(redact(line.rstrip("\n")))
                    if len(self.lines) > MAX_LINES:
                        del self.lines[: len(self.lines) - MAX_LINES]
        code = self.proc.wait()
        with self.lock:
            self.code = code

    @property
    def running(self):
        with self.lock:
            return self.code is None

    def cancel(self):
        """Stop the run and every program it started. On Linux/macOS the whole group gets SIGTERM: git stops, and the
        run itself exits through its cleanup (temporary clones are deleted). Windows has no such signal: the tree is
        ended, and the next run deletes whatever temporary clone was left."""
        if not self.running:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True, timeout=30,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(self.proc.pid, signal.SIGTERM)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.terminate()

    def days(self):
        """{"existing": {day: n}, "new": {day: n}} of a finished run, or None."""
        if self._days is None and self.dates_path and not self.running:
            try:
                with open(self.dates_path, encoding="utf-8") as f:
                    self._days = json.load(f)
            except (OSError, ValueError):
                self._days = {}
        return self._days or None

    def view(self, since=0):
        with self.lock:
            lines, code = list(self.lines), self.code
        return {"mode": self.mode, "running": code is None, "exit_code": code, "since": len(lines),
                "lines": lines[since:], "result": parse_result(lines), "started": self.started,
                "days": self.days() if code is not None else None}


# ---------------------------------------------------------------------------------------------------- the app

class App:
    """What the page can read and change. Paths are injectable so tests never touch the real config: `env_path` and
    `repos_path` are the default profile's files; another profile's are in profiles/NAME/ next to them."""
    def __init__(self, env_path=DEFAULT_ENV_FILE, repos_path=REPOS_FILE, example_env=ENV_TEMPLATE, schedule_ops=None,
                 profile=None):
        self.base_env, self.base_repos, self.example_env = env_path, repos_path, example_env
        self.profile = check_profile_name(current_profile() if profile is None else profile)
        self.job, self.job_lock = None, threading.Lock()
        self.write_lock, self.schedule_lock = threading.Lock(), threading.Lock()
        # (current(), apply(login, daily, time, timezone, say)): the real OS scheduler unless a test passes fakes
        self.schedule_ops = schedule_ops or (scheduler.current, scheduler.apply)
        self.tmp = tempfile.mkdtemp(prefix="mirror-ui-")

    def close(self):
        if self.job:
            self.job.cancel()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def env_path(self):
        return profile_paths(self.profile, self.base_env, self.base_repos)[0]

    @property
    def repos_path(self):
        return profile_paths(self.profile, self.base_env, self.base_repos)[1]

    def profiles(self):
        """Every profile the page can switch to: the default one, those with a settings file, and the current one."""
        names = set(list_profiles(self.base_env)) | {"", self.profile}
        return sorted(names, key=lambda n: (n != "", n.lower()))

    def set_profile(self, data):
        """Switch the page to another profile (a new name starts empty; saving creates its files)."""
        try:
            name = check_profile_name(str((data or {}).get("name", "")) if isinstance(data, dict) else "")
        except ValueError as e:
            raise BadRequest(str(e)) from None
        with self.job_lock:
            if self.job and self.job.running:
                raise BadRequest("wait for the run to finish before switching profiles")
            self.profile = name
        return self.state()

    def child_env(self):
        """Environment of the runs and checks started from the page: the page's own files and profile."""
        env = {**os.environ, "MIRROR_ENV_FILE": self.env_path, "MIRROR_REPOS_FILE": self.repos_path,
               "PYTHONIOENCODING": "utf-8"}
        env.pop("MIRROR_PROFILE", None)
        if self.profile:
            env["MIRROR_PROFILE"] = self.profile             # its own api-state file
        return env

    def log_dir(self):
        """Where runs log: MIRROR_LOG_DIR (shell, then .env), else the default log folder (settings.LOG_DIR)."""
        value = (os.environ.get("MIRROR_LOG_DIR", "").strip()
                 or envfile.read(self.env_path).get("MIRROR_LOG_DIR", "").strip())
        return os.path.abspath(value) if value else LOG_DIR

    def state(self):
        values = envfile.read(self.env_path)
        entries, legacy, error = [], False, None
        if os.path.isfile(self.repos_path):
            try:
                entries, legacy = repolist.read_entries(self.repos_path)
            except repolist.ReposFileError as e:
                error = str(e)
        settings = {k: values.get(k, "") for k in FIELDS}
        settings["MIRROR_PR_REVIEWS"] = "off" if settings["MIRROR_PR_REVIEWS"].lower() == "off" else "on"
        missing = [k for k in REQUIRED if not (values.get(k) or os.environ.get(k, "")).strip()]
        return {
            "settings": settings,
            "env_exists": os.path.isfile(self.env_path),
            "overridden": sorted(k for k in FIELDS if os.environ.get(k, "").strip()),
            "tokens": [k for k in TOKEN_VARS if (values.get(k) or os.environ.get(k, "")).strip()],
            "repos": [repolist.check_entry(e) for e in entries],
            "repos_legacy": legacy,
            "repos_error": error,
            "missing": missing,
            "ready": not missing and bool(entries) and not error,
            "paths": {"env": self.env_path, "repos": self.repos_path},
            "profile": self.profile,
            "profiles": self.profiles(),
            "version": __version__,
            "job": self.job.view(len(self.job.lines)) if self.job else None,
        }

    def save_settings(self, data):
        values = validate_settings(data)
        with self.write_lock:
            envfile.update(self.env_path, values, self.example_env)
        return self.state()

    def save_repos(self, data):
        try:
            entries = repolist.clean_entries((data or {}).get("repos") if isinstance(data, dict) else None)
        except ValueError as e:
            raise BadRequest(str(e)) from None
        with self.write_lock:
            repolist.save_entries(self.repos_path, entries)
        return self.state()

    @staticmethod
    def find_repos(folder):
        try:
            found, complete = repolist.find_under(folder)
        except ValueError as e:
            raise BadRequest(str(e)) from None
        return {"found": found, "complete": complete}

    def start(self, mode):
        if mode not in ("preview", "sync"):
            raise BadRequest("mode must be preview or sync")
        with self.job_lock:
            if self.job and self.job.running:
                raise BadRequest("a run is already in progress")
            dates = os.path.join(self.tmp, f"days-{int(time.time() * 1000)}.json")
            cmd = self_command(extra_flags=["-u"]) + ["sync", "--dates-out", dates] + (
                ["--dry-run"] if mode == "preview" else [])
            self.job = Job(mode, cmd, self.child_env(), dates)
        return self.job.view()

    # ----------------------------------------------------------------------------------------------- accounts
    def accounts(self, recheck=False):
        """Which login each host would use (`mirror.py accounts --json`, a separate process like a run)."""
        cmd = self_command() + ["accounts", "--json"] + (["--recheck"] if recheck else [])
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                               env=self.child_env(), stdin=subprocess.DEVNULL, creationflags=flags)
        except subprocess.TimeoutExpired:
            raise BadRequest("looking up the logins took more than 3 minutes") from None
        lines = [l for l in r.stdout.splitlines() if l.startswith("{")]
        try:
            data = json.loads(lines[-1])
        except (IndexError, ValueError):
            tail = [l for l in (r.stderr or r.stdout).strip().splitlines() if l.strip()][-1:] or [f"exit {r.returncode}"]
            raise BadRequest(redact(tail[0])) from None
        if "error" in data:
            raise BadRequest(redact(data["error"]))
        return data

    # ----------------------------------------------------------------------------------------------- doctor
    def doctor(self):
        """`mirror.py doctor --json` (a separate process, like a run: it loads the settings into its environment),
        plus the automatic-runs check made here with this page's scheduler access."""
        cmd = self_command() + ["doctor", "--json", "--skip-schedule"]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                               env=self.child_env(), stdin=subprocess.DEVNULL, creationflags=flags)
        except subprocess.TimeoutExpired:
            raise BadRequest("the checks took more than 5 minutes") from None
        lines = [l for l in r.stdout.splitlines() if l.startswith("{")]
        try:
            checks = json.loads(lines[-1])["checks"]
        except (IndexError, ValueError, KeyError):
            tail = [l for l in (r.stderr or r.stdout).strip().splitlines() if l.strip()][-1:] or [f"exit {r.returncode}"]
            raise BadRequest(redact(tail[0])) from None
        if checks and checks[0]["status"] != doctor.FAIL:       # settings readable: the rest of the setup exists
            try:
                state = self.schedule_ops[0]()
                at = next((i for i, c in enumerate(checks) if c["name"] == "Log files"), len(checks))
                checks.insert(at, doctor.check_schedule(state, self.logs())._asdict())
            except (scheduler.ScheduleError, OSError, SystemExit) as e:
                checks.append(doctor.Check(doctor.SKIP, "Automatic runs", redact(e))._asdict())
        return {"checks": [{k: redact(v) for k, v in c.items()} for c in checks]}

    # ----------------------------------------------------------------------------------------------- schedule
    def schedule_state(self):
        state = self.schedule_ops[0]()
        state["summary"] = scheduler.describe(state)
        state["ready"] = self.state()["ready"]
        return state

    def set_schedule(self, data):
        if not isinstance(data, dict):
            raise BadRequest("expected an object")
        login, daily = bool(data.get("login")), bool(data.get("daily"))
        time_of_day, timezone = str(data.get("time") or scheduler.DEFAULT_TIME), str(data.get("timezone") or "").strip()
        try:
            scheduler.parse_time(time_of_day)
        except ValueError as e:
            raise BadRequest(str(e)) from None
        if timezone and not re.match(r"^[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+)*$", timezone):
            raise BadRequest("time zone: a name such as Europe/London, or empty for local time")
        steps = []
        with self.schedule_lock:
            try:
                state = self.schedule_ops[1](login, daily, time_of_day, timezone, say=steps.append)
            except (scheduler.ScheduleError, OSError) as e:
                raise BadRequest(redact(e)) from None
            except SystemExit as e:                             # an unsupported operating system
                raise BadRequest(str(e)) from None
        state["summary"] = scheduler.describe(state)
        state["steps"] = steps
        return state

    # ----------------------------------------------------------------------------------------------- logs
    def logs(self, limit=400):
        """The newest log lines (mirror.log and its rotated copies), newest first, and the SUMMARY of each run."""
        return read_logs(self.log_dir(), limit)


def make_handler(app, token, port, launch=None):
    allowed_hosts = {f"{LOOPBACK}:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "contribution-mirror-ui"
        sys_version = ""

        def log_message(self, *a):
            pass                                                  # the page shows what matters; keep the terminal calm

        def _send(self, status, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self):
            return self.headers.get("Host", "") in allowed_hosts

        def _token_ok(self):
            return hmac.compare_digest(self.headers.get("X-Mirror-Token", ""), token)

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "wrong host"})
            u = urlsplit(self.path)
            if u.path in STATIC_FILES:
                name, ctype = STATIC_FILES[u.path]
                with open(os.path.join(STATIC, name), "rb") as f:
                    return self._send(200, f.read(), ctype)
            if not u.path.startswith("/api/"):
                return self._send(404, {"error": "not found"})
            if not self._token_ok():
                return self._send(403, {"error": "missing or wrong session token: open the link printed in the terminal"})
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path == "/api/state":
                    return self._send(200, app.state())
                if u.path == "/api/schedule":
                    return self._send(200, app.schedule_state())
                if u.path == "/api/logs":
                    return self._send(200, app.logs(max(1, min(2000, _int(q, "limit", 400)))))
                if u.path == "/api/job":
                    since = max(0, _int(q, "since", 0))
                    return self._send(200, app.job.view(since) if app.job else None)
            except BadRequest as e:
                return self._send(400, {"error": str(e)})
            return self._send(404, {"error": "not found"})

        def _body(self):
            """The decoded JSON body of a POST. Raises BadRequest for a negative or oversized length."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise BadRequest("bad Content-Length") from None
            if not 0 <= length <= MAX_BODY:
                raise BadRequest("request too large" if length > MAX_BODY else "bad Content-Length")
            return json.loads(self.rfile.read(length) or b"null")

        def _session(self, data):
            """Trade the one-time launch code for the session token (see the module docstring)."""
            code = str((data or {}).get("code", "")) if isinstance(data, dict) else ""
            if launch is None or not code or not hmac.compare_digest(code, launch.code):
                return self._send(403, {"error": "this link is not valid: open the link printed in the terminal"})
            if not launch.take():
                return self._send(403, {"error": "this link was already used, so someone else may have it: the page "
                                                 "has stopped. Start it again with `mirror.py ui`"})
            return self._send(200, {"token": token})

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, {"error": "wrong host"})
            origin = self.headers.get("Origin")
            if origin is not None and origin not in allowed_origins:
                return self._send(403, {"error": "wrong origin"})
            path = urlsplit(self.path).path
            if path != "/api/session" and not self._token_ok():
                return self._send(403, {"error": "missing or wrong session token: open the link printed in the terminal"})
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._send(415, {"error": "JSON only"})
            try:
                data = self._body()
                if path == "/api/session":
                    return self._session(data)
                arg = lambda key: (data or {}).get(key) if isinstance(data, dict) else None
                routes = {
                    "/api/settings": lambda: app.save_settings(data),
                    "/api/repos": lambda: app.save_repos(data),
                    "/api/find-repos": lambda: app.find_repos(str(arg("folder") or "")),
                    "/api/check-remote": lambda: dict(zip(("ok", "message"),
                                                          check_remote(str(arg("remote") or "").strip()))),
                    "/api/run": lambda: app.start(arg("mode")),
                    "/api/accounts": lambda: app.accounts(bool(arg("recheck"))),
                    "/api/doctor": lambda: app.doctor(),
                    "/api/profile": lambda: app.set_profile(data),
                    "/api/schedule": lambda: app.set_schedule(data),
                    "/api/cancel": lambda: (app.job.cancel() if app.job else None) or {"ok": True},
                }
                route = routes.get(path)
                if route is None:
                    return self._send(404, {"error": "not found"})
                return self._send(200, route())
            except BadRequest as e:
                return self._send(400, {"error": str(e)})
            except (ValueError, envfile.EnvFileError) as e:
                return self._send(400, {"error": redact(e)})

    return Handler


def _int(query, key, default):
    try:
        return int(query.get(key) or default)
    except ValueError:
        raise BadRequest(f"{key} must be a number") from None


class Launch:
    """The one-time code the browser is opened with. `take()` is True once; a second use calls `on_reuse`."""
    def __init__(self, on_reuse):
        self.code, self.used, self.on_reuse = secrets.token_urlsafe(24), False, on_reuse
        self.lock = threading.Lock()

    def take(self):
        with self.lock:
            first, self.used = not self.used, True
        if not first:
            self.on_reuse()
        return first


def make_server(app=None, port=0):
    """(server, link with the session token, token). Bound to the loopback address only. `server.launch_url` is the
    link with the one-time launch code, for opening a browser."""
    app = app or App()
    token = secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer((LOOPBACK, port), None)
    httpd.daemon_threads = True
    real_port = httpd.server_address[1]

    def reused():
        print("warning: the browser link was used a second time, so another program or person had it; the page is "
              "stopping. Start it again with `mirror.py ui`.", file=sys.stderr, flush=True)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    launch = Launch(reused)
    httpd.RequestHandlerClass = make_handler(app, token, real_port, launch)
    httpd.app = app
    base = f"http://{LOOPBACK}:{real_port}/"
    httpd.launch_url = f"{base}#c={launch.code}"
    return httpd, f"{base}#t={token}", token


def main(argv):
    p = argparse.ArgumentParser(prog="mirror.py ui", description="Open the setup and sync page in your browser.")
    p.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    p.add_argument("--no-browser", action="store_true", help="only print the link, do not open a browser")
    a = p.parse_args(argv)
    try:
        httpd, url, _ = make_server(port=a.port)
    except OSError as e:
        print(f"cannot start the page: {e}", file=sys.stderr)
        return 1
    print("Contribution mirror is open at (this link is your key; do not share it):")
    print(f"  {url}")
    print("Press Ctrl+C here to stop.", flush=True)
    if not a.no_browser:
        threading.Timer(0.3, lambda: webbrowser.open(httpd.launch_url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        httpd.server_close()
        httpd.app.close()
    return 0
