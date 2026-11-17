"""`ui`: a small local web page for setting up and running the mirror (standard library only).

    python3 -I src/mirror.py ui [--port N] [--no-browser]

Security model: the server listens on the loopback address only, and every API call must carry a random token that
is created at start and handed to the browser in the URL it opens (the page then removes it from the address bar).
Requests must name this server in their Host header (no DNS rebinding) and writes must come from this page's own
origin with a JSON body (no cross-site form posts). The page never receives a token value: it only learns whether one
is set. Runs (preview / sync) are separate `mirror.py sync` processes, so they read the saved settings fresh, cannot
take the page down, and share the run lock with the scheduler."""
import argparse
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import envfile
from . import schedule as scheduler
from .config import DEFAULT_ENV_FILE, REQUIRED
from .gitutil import work_env
from .logsetup import redact
from .remotes import is_url, repo_path, url_host
from .settings import LOG_DIR, PROJECT_ROOT, REPOS_FILE

LOOPBACK = "127.0.0.1"
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_static")
STATIC_FILES = {"/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/app.css": ("app.css", "text/css; charset=utf-8"),
                "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
                "/favicon.svg": ("favicon.svg", "image/svg+xml"),
                "/favicon.ico": ("favicon.svg", "image/svg+xml")}       # browsers that ask for the old name anyway
ENTRY = os.path.join(PROJECT_ROOT, "src", "mirror.py")
EXAMPLE_ENV = os.path.join(PROJECT_ROOT, "config", ".env.example")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TOKEN_VARS = ("WORK_TOKEN", "WORK_GITHUB_TOKEN", "WORK_GITLAB_TOKEN", "WORK_BITBUCKET_TOKEN",
              "WORK_AZURE_DEVOPS_TOKEN", "WORK_GITEA_TOKEN")
# the settings this page edits; everything else in config/.env (tokens, comments, advanced keys) is left as it is
FIELDS = ("WORK_EMAIL", "PERSONAL_NAME", "PERSONAL_EMAIL", "EXPECTED_REMOTE", "MIRROR_DIR", "MIRROR_PR_REVIEWS",
          "MIRROR_CLONE_CACHE")
MAX_LINES = 5000
SUMMARY_RE = re.compile(r"^SUMMARY mode=(\S+) repos=(\d+) matching=(\d+) already_mirrored=(\d+) "
                        r"(would_create|created)=(\d+)")
SOURCE_RE = re.compile(r"^repo (.+): matching=(\d+) already_mirrored=(\d+) new=(\d+)$")
LOG_LINE_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (\w+) (.*)$")
LOG_SUMMARY_RE = re.compile(r"^SUMMARY mode=(\S+) repos=(\d+) matching=(\d+) already_mirrored=(\d+) "
                            r"(?:would_create|created)=(\d+)")


class BadRequest(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------- settings file

def read_env(path):
    """{key: value} of config/.env as written (not expanded into the environment), or {} if missing."""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return envfile.parse(f.read(), path)


def _env_value(value):
    """How a value is written so that envfile.parse reads it back unchanged: quoted only when it has to be."""
    if value != value.strip() or (len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"):
        return f'"{value}"'
    return value


def update_env_text(text, updates):
    """`text` of an env file with `updates` applied: existing KEY= lines are replaced in place (comments, order and
    every other key are kept), new keys are appended."""
    out, done = [], set()
    for line in text.splitlines():
        stripped = line.strip()
        body = stripped[7:].lstrip() if stripped.startswith("export ") else stripped
        key = body.split("=", 1)[0].strip() if "=" in body and not stripped.startswith("#") else None
        if key in updates:
            if key not in done:
                out.append(f"{key}={_env_value(updates[key])}")
                done.add(key)
            continue
        out.append(line)
    missing = [k for k in updates if k not in done]
    if missing:
        out += [""] + [f"{k}={_env_value(updates[k])}" for k in missing]
    return "\n".join(out).rstrip("\n") + "\n"


def write_private(path, text):
    """Write a file only its owner can read (on Linux/macOS), atomically."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


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
    if problems:
        raise BadRequest("; ".join(problems))
    return values


# ---------------------------------------------------------------------------------------------------- project list

def check_entry(entry):
    """What the page shows next to one repos.json entry. Never touches the network."""
    if is_url(entry):
        return {"entry": entry, "kind": "url", "ok": True, "detail": url_host(entry) or "URL"}
    path = os.path.abspath(os.path.expanduser(entry))
    if os.path.isdir(os.path.join(path, ".git")) or os.path.isfile(os.path.join(path, ".git")):
        return {"entry": entry, "kind": "path", "ok": True, "detail": "git repo"}
    detail = "not a git repo" if os.path.isdir(path) else "folder not found"
    return {"entry": entry, "kind": "path", "ok": False, "detail": detail}


def read_repos(path):
    """(entries, used the older "remote_repos" key, error or None)."""
    if not os.path.isfile(path):
        return [], False, None
    try:
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        return [], False, f"{os.path.basename(path)} cannot be read: {e}"
    if not isinstance(cfg, dict):
        return [], False, f"{os.path.basename(path)} is not an object"
    listed = [x for x in cfg.get("repos", []) if isinstance(x, str)]
    legacy = [x for x in cfg.get("remote_repos", []) if isinstance(x, str)]
    return list(dict.fromkeys(listed + legacy)), bool(legacy), None


def clean_entries(entries):
    if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
        raise BadRequest("expected a list of paths and URLs")
    out = []
    for e in entries:
        e = e.strip()
        if "\n" in e or "\r" in e:
            raise BadRequest("an entry must be one line")
        if e and e not in out:
            out.append(e)
    return out


def find_repos(folder, limit=300, seconds=4.0):
    """Git repos under `folder` (not inside each other, skipping node_modules), stopping after `limit` results or a
    few seconds so a huge folder cannot hang the page. Returns (paths, complete)."""
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        raise BadRequest(f"folder not found: {folder}")
    deadline, found = time.monotonic() + seconds, []
    for dp, dn, fn in os.walk(folder):
        if ".git" in dn or ".git" in fn:
            found.append(dp)
            dn[:] = []
        else:
            dn[:] = sorted(d for d in dn if d not in ("node_modules", ".git") and not d.startswith("."))
        if len(found) >= limit or time.monotonic() > deadline:
            return found, False
    return found, True


def check_remote(remote):
    """Can your normal git setup reach the mirror repo? (`git ls-remote`, never prompts). Returns (ok, message)."""
    if not remote:
        return False, "enter the mirror repo first"
    if not is_url(remote) and not os.path.exists(os.path.expanduser(remote)):
        return False, "not a URL, and no such local path"
    try:
        r = subprocess.run(["git", "ls-remote", "--heads", "--", remote], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30, env=work_env(batch_ssh=True),
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, "no answer within 30 s"
    except OSError as e:
        return False, f"git could not be started: {e}"
    if r.returncode == 0:
        branches = len([l for l in r.stdout.splitlines() if l.strip()])
        return True, "reachable" + (" (empty repo: the first sync fills it)" if branches == 0 else "")
    lines = [l for l in r.stderr.strip().splitlines() if l.strip()]
    return False, redact(" | ".join(lines[-2:]) if lines else f"git exit {r.returncode}") + \
        " (the push may still work with a token found automatically)"


# ---------------------------------------------------------------------------------------------------- runs

class Job:
    """The one preview or sync running (or last run): a `mirror.py sync` child process and its output.
    `dates_path`: where the run writes its dates per day (for the calendar), read once it has finished."""
    def __init__(self, mode, cmd, env, dates_path=None):
        self.mode, self.lines, self.code, self.started = mode, [], None, time.time()
        self.dates_path, self._days = dates_path, None
        self.lock = threading.Lock()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace", env=env, creationflags=flags)
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
        if self.running:
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


def parse_result(lines):
    """Totals and per-source counts from a run's output (the SUMMARY line and the `repo ...:` lines)."""
    out = {"sources": [], "summary": None, "pushed": None}
    for line in lines:
        m = SOURCE_RE.match(line)
        if m:
            name = m.group(1)
            if name.startswith("github-api:"):
                name = {"github-api:pull-requests": "Pull requests", "github-api:reviews": "Reviews"}.get(name, name)
            elif not name.startswith("remote:"):
                name = os.path.basename(name.rstrip("/\\")) or name
            out["sources"].append({"name": name, "matching": int(m.group(2)), "mirrored": int(m.group(3)),
                                   "new": int(m.group(4))})
        m = SUMMARY_RE.match(line)
        if m:
            out["summary"] = {"mode": m.group(1), "repos": int(m.group(2)), "matching": int(m.group(3)),
                              "mirrored": int(m.group(4)), "new": int(m.group(6))}
        if line in ("pushed", "nothing to push"):
            out["pushed"] = line
    return out


# ---------------------------------------------------------------------------------------------------- the app

class App:
    """What the page can read and change. Paths are injectable so tests never touch the real config."""
    def __init__(self, env_path=DEFAULT_ENV_FILE, repos_path=REPOS_FILE, example_env=EXAMPLE_ENV, schedule_ops=None):
        self.env_path, self.repos_path, self.example_env = env_path, repos_path, example_env
        self.job, self.job_lock = None, threading.Lock()
        self.write_lock, self.schedule_lock = threading.Lock(), threading.Lock()
        # (current(), apply(login, daily, time, timezone, say)): the real OS scheduler unless a test passes fakes
        self.schedule_ops = schedule_ops or (scheduler.current, scheduler.apply)
        self.tmp = tempfile.mkdtemp(prefix="mirror-ui-")

    def close(self):
        if self.job:
            self.job.cancel()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def child_env(self):
        """Environment of the runs and checks started from the page: the page's own files, nothing else changed."""
        return {**os.environ, "MIRROR_ENV_FILE": self.env_path, "MIRROR_REPOS_FILE": self.repos_path,
                "PYTHONIOENCODING": "utf-8"}

    def log_dir(self):
        """Where runs log: MIRROR_LOG_DIR (shell, then config/.env), else logs/ in the project folder."""
        value = os.environ.get("MIRROR_LOG_DIR", "").strip() or read_env(self.env_path).get("MIRROR_LOG_DIR", "").strip()
        return os.path.abspath(value) if value else LOG_DIR

    def state(self):
        values = read_env(self.env_path)
        entries, legacy, error = read_repos(self.repos_path)
        settings = {k: values.get(k, "") for k in FIELDS}
        settings["MIRROR_PR_REVIEWS"] = "off" if settings["MIRROR_PR_REVIEWS"].lower() == "off" else "on"
        missing = [k for k in REQUIRED if not (values.get(k) or os.environ.get(k, "")).strip()]
        return {
            "settings": settings,
            "env_exists": os.path.isfile(self.env_path),
            "overridden": sorted(k for k in FIELDS if os.environ.get(k, "").strip()),
            "tokens": [k for k in TOKEN_VARS if (values.get(k) or os.environ.get(k, "")).strip()],
            "repos": [check_entry(e) for e in entries],
            "repos_legacy": legacy,
            "repos_error": error,
            "missing": missing,
            "ready": not missing and bool(entries) and not error,
            "paths": {"env": self.env_path, "repos": self.repos_path},
            "job": self.job.view(len(self.job.lines)) if self.job else None,
        }

    def save_settings(self, data):
        values = validate_settings(data)
        with self.write_lock:
            if os.path.isfile(self.env_path):
                with open(self.env_path, encoding="utf-8") as f:
                    text = f.read()
            elif os.path.isfile(self.example_env):
                with open(self.example_env, encoding="utf-8") as f:
                    text = f.read()
            else:
                text = ""
            write_private(self.env_path, update_env_text(text, values))
        return self.state()

    def save_repos(self, data):
        entries = clean_entries((data or {}).get("repos") if isinstance(data, dict) else None)
        with self.write_lock:
            write_private(self.repos_path, json.dumps({"repos": entries}, indent=2, ensure_ascii=False) + "\n")
        return self.state()

    def start(self, mode):
        if mode not in ("preview", "sync"):
            raise BadRequest("mode must be preview or sync")
        with self.job_lock:
            if self.job and self.job.running:
                raise BadRequest("a run is already in progress")
            dates = os.path.join(self.tmp, f"days-{int(time.time() * 1000)}.json")
            cmd = [sys.executable, "-I", "-u", ENTRY, "sync", "--dates-out", dates] + (
                ["--dry-run"] if mode == "preview" else [])
            self.job = Job(mode, cmd, self.child_env(), dates)
        return self.job.view()

    # ----------------------------------------------------------------------------------------------- accounts
    def accounts(self, recheck=False):
        """Which login each host would use (`mirror.py accounts --json`, a separate process like a run)."""
        cmd = [sys.executable, "-I", ENTRY, "accounts", "--json"] + (["--recheck"] if recheck else [])
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
        log_dir = self.log_dir()
        entries, runs = [], []
        files = [os.path.join(log_dir, "mirror.log")] + [os.path.join(log_dir, f"mirror.log.{i}") for i in range(1, 4)]
        for path in files:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                m = LOG_LINE_RE.match(line)
                if not m:
                    continue
                when, level, message = m.group(1), m.group(2), redact(m.group(3))
                s = LOG_SUMMARY_RE.match(message)
                if s and len(runs) < 60:
                    runs.append({"time": when, "mode": s.group(1), "repos": int(s.group(2)),
                                 "matching": int(s.group(3)), "mirrored": int(s.group(4)), "new": int(s.group(5))})
                if len(entries) < limit:
                    entries.append({"time": when, "level": level, "message": message})
            if len(entries) >= limit and len(runs) >= 60:
                break
        return {"dir": log_dir, "entries": entries, "runs": list(reversed(runs))}


def make_handler(app, token, port):
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
            if u.path == "/api/state":
                return self._send(200, app.state())
            if u.path == "/api/schedule":
                return self._send(200, app.schedule_state())
            if u.path == "/api/logs":
                return self._send(200, app.logs(max(1, min(2000, int(q.get("limit", "400") or 400)))))
            if u.path == "/api/job":
                if not app.job:
                    return self._send(200, None)
                return self._send(200, app.job.view(int(q.get("since", "0") or 0)))
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, {"error": "wrong host"})
            origin = self.headers.get("Origin")
            if origin is not None and origin not in allowed_origins:
                return self._send(403, {"error": "wrong origin"})
            if not self._token_ok():
                return self._send(403, {"error": "missing or wrong session token: open the link printed in the terminal"})
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._send(415, {"error": "JSON only"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 1_000_000:
                    raise BadRequest("request too large")
                data = json.loads(self.rfile.read(length) or b"null")
                routes = {
                    "/api/settings": lambda: app.save_settings(data),
                    "/api/repos": lambda: app.save_repos(data),
                    "/api/find-repos": lambda: dict(zip(("found", "complete"),
                                                        find_repos(str((data or {}).get("folder", ""))))),
                    "/api/check-remote": lambda: dict(zip(("ok", "message"),
                                                          check_remote(str((data or {}).get("remote", "")).strip()))),
                    "/api/run": lambda: app.start((data or {}).get("mode")),
                    "/api/accounts": lambda: app.accounts(bool((data or {}).get("recheck"))),
                    "/api/schedule": lambda: app.set_schedule(data),
                    "/api/cancel": lambda: (app.job.cancel() if app.job else None) or {"ok": True},
                }
                route = routes.get(urlsplit(self.path).path)
                if route is None:
                    return self._send(404, {"error": "not found"})
                return self._send(200, route())
            except BadRequest as e:
                return self._send(400, {"error": str(e)})
            except (ValueError, envfile.EnvFileError) as e:
                return self._send(400, {"error": redact(e)})

    return Handler


def make_server(app=None, port=0):
    """(server, url to open, token). Bound to the loopback address only."""
    app = app or App()
    token = secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer((LOOPBACK, port), None)
    httpd.daemon_threads = True
    real_port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, token, real_port)
    httpd.app = app
    return httpd, f"http://{LOOPBACK}:{real_port}/#t={token}", token


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
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        httpd.server_close()
        httpd.app.close()
    return 0
