"""Desktop notification + sound, per platform, with a fallback chain. Backends are looked up on PATH at run time."""
from __future__ import annotations

import shutil
from typing import Callable

from . import platform as plat
from .state import log

OGA = "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _toast_script(title: str, msg: str) -> str:
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    return (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent('ToastText02');"
        f"$t = $x.GetElementsByTagName('text'); $t.Item(0).AppendChild($x.CreateTextNode({_ps_quote(esc(title))})) | Out-Null;"
        f"$t.Item(1).AppendChild($x.CreateTextNode({_ps_quote(esc(msg))})) | Out-Null;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('tokens-doctor').Show("
        "[Windows.UI.Notifications.ToastNotification]::new($x))"
    )


def notify(title: str, message: str, *, sound: bool = True, urgent: bool = False,
           run: Callable = plat.run, which: Callable = shutil.which) -> str:
    """Show a notification, return the name of the backend that worked ('terminal' = nothing else did)."""
    def ok(cmd: list) -> bool:
        r = run(cmd)
        return bool(r and r.returncode == 0)

    used = []
    if plat.is_macos():
        script = f'display notification "{message.replace(chr(34), chr(39))}" with title "{title.replace(chr(34), chr(39))}"'
        if which("osascript") and ok(["osascript", "-e", script]):
            used.append("osascript")
        if sound and which("afplay") and ok(["afplay", f"/System/Library/Sounds/{'Sosumi' if urgent else 'Glass'}.aiff"]):
            used.append("afplay")
    else:
        if plat.is_wsl():
            if which("wsl-notify-send.exe") and ok(["wsl-notify-send.exe", "--category", title, message]):
                used.append("wsl-notify-send")
            elif which("powershell.exe") and ok(["powershell.exe", "-NoProfile", "-Command", _toast_script(title, message)]):
                used.append("powershell-toast")
            if sound and which("powershell.exe") and ok(
                    ["powershell.exe", "-NoProfile", "-Command", "[System.Media.SystemSounds]::Exclamation.Play(); Start-Sleep -Milliseconds 800"]):
                used.append("powershell-sound")
        if not used and which("notify-send") and ok(["notify-send", "-u", "critical" if urgent else "normal", title, message]):
            used.append("notify-send")
        if sound and not any("sound" in u for u in used):
            if which("canberra-gtk-play") and ok(["canberra-gtk-play", "-i", "alarm-clock-elapsed"]):
                used.append("canberra")
            elif which("paplay") and ok(["paplay", OGA]):
                used.append("paplay")
    if not used:
        log(f"notify fallback (no desktop notifier worked): {title}: {message}")
        return "terminal"
    return "+".join(used)


def test() -> str:
    """For `tokens-doctor notify-test`: try the chain and say how to get the full version."""
    used = notify("tokens-doctor", "Test notification: if you see and hear this, the monitor can reach you.")
    if used == "terminal":
        hint = ("install libnotify (notify-send) and pulseaudio-utils / libcanberra" if not plat.is_macos()
                else "osascript and afplay ship with macOS; check System Settings > Notifications")
        return f"No desktop notifier worked, only the log. To enable the full version: {hint}."
    return f"Sent through: {used}"
