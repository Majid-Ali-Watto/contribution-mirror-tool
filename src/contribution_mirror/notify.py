"""Desktop notifications on Linux, macOS and Windows. Always optional: a missing tool is silently ignored."""
import subprocess
import sys

from .gitutil import child_env


def command(title, message, urgent=False, platform=None):
    """The command that shows a notification on this platform, or None if there is no known way."""
    platform = platform or sys.platform
    if platform.startswith("linux"):
        return ["notify-send", "-u", "critical" if urgent else "normal", "-a", "contribution-mirror", title, message]
    if platform == "darwin":
        esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
        return ["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"']
    if platform in ("win32", "cygwin"):
        esc = lambda s: s.replace("'", "''")
        script = ("Add-Type -AssemblyName System.Windows.Forms; $n = New-Object System.Windows.Forms.NotifyIcon; "
                  "$n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; "
                  f"$n.ShowBalloonTip(10000, '{esc(title)}', '{esc(message)}', 'Info'); Start-Sleep -Seconds 11; $n.Dispose()")
        return ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script]
    return None


def notify(title, message, urgent=False):
    """Show a notification. Never raises, never blocks the run (Windows' balloon needs a few seconds alive)."""
    cmd = command(title, message, urgent)
    if not cmd:
        return
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=child_env())
    except OSError:
        pass
