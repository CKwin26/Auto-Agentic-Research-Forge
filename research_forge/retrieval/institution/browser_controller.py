"""Launch a user-controlled local browser without observing authentication data."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BrowserLaunch:
    process_id: int
    browser: str
    storage_security: str = "browser_native_os_encryption"


class LocalInstitutionBrowserController:
    """Starts an isolated visible browser profile and never inspects its data."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def find_browser(self) -> Path | None:
        program_files = Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        program_files_x86 = Path(
            os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")
        )
        local_app_data = Path(
            os.environ.get("LOCALAPPDATA", "C:/Users/Default/AppData/Local")
        )
        candidates = [
            program_files_x86 / "Microsoft/Edge/Application/msedge.exe",
            program_files / "Microsoft/Edge/Application/msedge.exe",
            program_files / "Google/Chrome/Application/chrome.exe",
            program_files_x86 / "Google/Chrome/Application/chrome.exe",
            local_app_data / "Google/Chrome/Application/chrome.exe",
        ]
        return next((item for item in candidates if item.is_file()), None)

    def available(self) -> bool:
        return self.find_browser() is not None

    def launch(self, *, browser_profile_id: str, target_url: str) -> BrowserLaunch:
        executable = self.find_browser()
        if executable is None:
            raise RuntimeError("no supported local Edge or Chrome browser was found")
        profile_root = self.root / "browser_profiles" / browser_profile_id
        profile_root.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            [
                str(executable),
                f"--user-data-dir={profile_root}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                target_url,
            ],
            close_fds=True,
        )
        return BrowserLaunch(
            process_id=process.pid,
            browser=executable.stem.casefold(),
        )
