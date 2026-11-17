"""The user's settings, read from config/.env (and the real environment): who they are and where the mirror is."""
import os
from dataclasses import dataclass

from . import envfile
from .settings import PROJECT_ROOT

DEFAULT_ENV_FILE = os.path.join(PROJECT_ROOT, "config", ".env")
REQUIRED = ("WORK_EMAIL", "PERSONAL_NAME", "PERSONAL_EMAIL", "EXPECTED_REMOTE")


@dataclass
class Config:
    work_emails: list
    personal_name: str
    personal_email: str
    expected_remote: str
    mirror_dir: str
    ssh_key: str = ""            # used only for pushing the mirror
    gh_user: str = ""            # force one gh account as the work account
    pr_reviews: bool = True      # False: commits only
    log_dir: str = ""


def load_config(path=None):
    """Load the env file (MIRROR_ENV_FILE overrides the default location) and return a Config.
    Raises envfile.EnvFileError with a readable message for a missing file or missing settings."""
    envfile.load(path or os.environ.get("MIRROR_ENV_FILE") or DEFAULT_ENV_FILE)
    envfile.require(REQUIRED)
    e = os.environ.get
    return Config(
        work_emails=e("WORK_EMAIL", "").split(),
        personal_name=e("PERSONAL_NAME", ""),
        personal_email=e("PERSONAL_EMAIL", ""),
        expected_remote=e("EXPECTED_REMOTE", ""),
        mirror_dir=os.path.abspath(os.path.expanduser(e("MIRROR_DIR", "") or os.path.join("~", "work-contributions"))),
        ssh_key=e("PERSONAL_SSH_KEY", ""),
        gh_user=e("WORK_GH_USER", ""),
        pr_reviews=e("MIRROR_PR_REVIEWS", "auto").strip().lower() != "off",
        log_dir=e("MIRROR_LOG_DIR", ""),
    )
