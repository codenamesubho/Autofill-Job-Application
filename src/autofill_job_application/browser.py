"""Browser session construction.

Drives the system Chrome binary against a **dedicated** profile directory. The
user's own Chrome profile is never opened: sharing it would put live logged-in
sessions under an autonomous agent's control, and Chrome refuses a second process
on the same profile anyway.
"""

from __future__ import annotations

import os
from pathlib import Path

from browser_use import BrowserProfile, BrowserSession

from .guard import inject_submit_guard

#: Standard install location on macOS. Overridable for other platforms.
DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

DEFAULT_PROFILE_DIR = "~/.autofill/chrome-profile"


def resolve_chrome(path: str | None = None) -> str | None:
    """Locate a Chrome binary. Returns None to let browser-use pick its own."""
    candidate = path or os.environ.get("AUTOFILL_CHROME_PATH") or DEFAULT_CHROME
    return candidate if Path(candidate).exists() else None


def build_profile(
    *,
    headless: bool = True,
    profile_dir: str = DEFAULT_PROFILE_DIR,
    chrome_path: str | None = None,
) -> BrowserProfile:
    user_data_dir = str(Path(profile_dir).expanduser())
    Path(user_data_dir).mkdir(parents=True, exist_ok=True)

    kwargs = {
        "headless": headless,
        "user_data_dir": user_data_dir,
        # ATS forms are routinely embedded in a cross-origin iframe. Without this
        # the agent's page representation stops at the frame boundary and the
        # form is invisible to it.
        "cross_origin_iframes": True,
    }
    executable = resolve_chrome(chrome_path)
    if executable:
        kwargs["executable_path"] = executable
    return BrowserProfile(**kwargs)


async def start_session(
    *,
    headless: bool = True,
    profile_dir: str = DEFAULT_PROFILE_DIR,
    chrome_path: str | None = None,
) -> BrowserSession:
    """Start a guarded session. The guard is installed before any navigation."""
    session = BrowserSession(
        browser_profile=build_profile(
            headless=headless, profile_dir=profile_dir, chrome_path=chrome_path
        )
    )
    await session.start()
    await inject_submit_guard(session)
    return session
