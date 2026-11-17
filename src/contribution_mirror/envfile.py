"""Reading config/.env: plain KEY=VALUE lines, parsed and never executed (so a value can never run code)."""
import os
import re

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
        raise EnvFileError(f"missing {path} -- copy config/.env.example to config/.env and fill it in")
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
        raise EnvFileError("missing in config/.env: " + " ".join(missing))
