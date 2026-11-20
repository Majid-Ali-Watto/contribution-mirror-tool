"""Private files and folders: settings, logs and state hold emails, logins, hostnames and repo names, so on Linux and
macOS only their owner may read them (Windows keeps per-user folders private already)."""
import os


def make_private(path, mode=0o600):
    """Restrict an existing file (0600) or folder (0700) to its owner; never raises."""
    if os.name != "nt":
        try:
            os.chmod(path, mode)
        except OSError:
            pass


def private_dir(path):
    """Create `path` (and parents) if needed. A folder created here is owner-only; an existing one is left as it is,
    since it may be a folder the user chose and shares on purpose (the files inside are private either way)."""
    if not os.path.isdir(path):
        os.makedirs(path, mode=0o700, exist_ok=True)
        make_private(path, 0o700)                   # makedirs' mode is filtered by the umask
    return path


def write_private(path, text):
    """Write a text file only its owner can read, atomically (a crash leaves the old file or the new one)."""
    private_dir(os.path.dirname(os.path.abspath(path)))
    tmp = path + ".tmp"
    if os.path.lexists(tmp):
        os.remove(tmp)                              # never write through a planted file or symlink
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    make_private(tmp)
    os.replace(tmp, path)
