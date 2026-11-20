"""The settings file (.env): plain KEY=VALUE lines, parsed and never executed (so a value can never run code), and
edited in place by the setup page (only the lines it manages; comments, order and every other key are kept)."""
import os
import re

from .fsutil import write_private

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvFileError(Exception):
    pass


def expand(value):
    """A leading ~ and $HOME / ${HOME} become the home folder (works the same on Windows, macOS and Linux)."""
    home = os.path.expanduser("~")
    if value == "~" or value.startswith(("~/", "~\\")):
        value = home + value[1:]
    return value.replace("${HOME}", home).replace("$HOME", home)


def parse(text, source="env file"):
    """{key: value} from the text of an env file. Blank lines and lines starting with # are ignored, an optional
    leading `export ` is allowed, values may be wrapped in single or double quotes, and there are no inline
    comments (put comments on their own line)."""
    out = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise EnvFileError(f"bad line {n} in {source} (expected KEY=VALUE): {raw.strip()}")
        key, value = (x.strip() for x in line.split("=", 1))
        if not KEY_RE.match(key):
            raise EnvFileError(f"bad variable name in {source} line {n}: {key}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = expand(value)
    return out


def load(path, environ=None):
    """Read `path` into `environ` (default os.environ). Variables that are already set win over the file.
    Returns the parsed {key: value}."""
    environ = os.environ if environ is None else environ
    if not os.path.isfile(path):
        raise EnvFileError(f"missing {path} -- run `mirror.py setup` (or open the page: `mirror.py ui`) and fill it in")
    with open(path, encoding="utf-8") as f:
        values = parse(f.read(), path)
    for key, value in values.items():
        environ.setdefault(key, value)
    return values


def require(names, environ=None):
    """Raise EnvFileError listing the variables that are unset or empty."""
    environ = os.environ if environ is None else environ
    missing = [n for n in names if not environ.get(n, "").strip()]
    if missing:
        raise EnvFileError("missing in your .env settings: " + " ".join(missing))


def read(path):
    """{key: value} of an env file as written (not put into the environment), or {} if it does not exist."""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return parse(f.read(), path)


def _quoted(value):
    """How a value is written so that parse() reads it back unchanged: quoted only when it has to be."""
    if value != value.strip() or (len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"):
        return f'"{value}"'
    return value


def update_text(text, updates):
    """`text` of an env file with `updates` applied: existing KEY= lines are replaced in place (comments, order and
    every other key are kept), new keys are appended."""
    out, done = [], set()
    for line in text.splitlines():
        stripped = line.strip()
        body = stripped[7:].lstrip() if stripped.startswith("export ") else stripped
        key = body.split("=", 1)[0].strip() if "=" in body and not stripped.startswith("#") else None
        if key in updates:
            if key not in done:
                out.append(f"{key}={_quoted(updates[key])}")
                done.add(key)
            continue
        out.append(line)
    missing = [k for k in updates if k not in done]
    if missing:
        out += [""] + [f"{k}={_quoted(updates[k])}" for k in missing]
    return "\n".join(out).rstrip("\n") + "\n"


def update(path, updates, template=None):
    """Apply `updates` to the env file at `path` (created from `template` if missing), privately and atomically."""
    source = path if os.path.isfile(path) else template if template and os.path.isfile(template) else None
    text = ""
    if source:
        with open(source, encoding="utf-8") as f:
            text = f.read()
    write_private(path, update_text(text, updates))
