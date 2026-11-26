"""Experiment: which Chromium flags make a Python-launched Edge --app window render
in this restricted desktop session? Prints a matrix of results and cleans up.

Usage: python scripts/edge_flag_test.py [port]
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
TMP = ROOT / ".tmp" / "edge-flags"
TMP.mkdir(parents=True, exist_ok=True)

VARIANTS = [
    ("baseline", []),
    ("no-sandbox", ["--no-sandbox"]),
    ("disable-gpu", ["--disable-gpu"]),
    ("gpu+nosandbox", ["--disable-gpu", "--no-sandbox"]),
    ("in-process-gpu", ["--in-process-gpu", "--no-sandbox"]),
    ("single-process", ["--single-process", "--no-sandbox", "--disable-gpu"]),
    ("no-zygote", ["--no-zygote", "--no-sandbox", "--disable-gpu"]),
    ("renderer-integrity", ["--disable-features=RendererCodeIntegrity", "--no-sandbox"]),
    ("swiftshader", ["--use-gl=swiftshader", "--no-sandbox"]),
    ("disable-gpu-sandbox", ["--disable-gpu-sandbox", "--no-sandbox"]),
]


def window_titles_for_pids(pids: set[int]) -> list[str]:
    user32 = ctypes.windll.user32
    titles: list[str] = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                if buffer.value:
                    titles.append(buffer.value)
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return titles


def pids_for_profile(marker: str) -> set[int]:
    try:
        import psutil
    except Exception:
        return set()
    pids: set[int] = set()
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = " ".join(proc.info["cmdline"] or []).lower()
        except Exception:
            continue
        if marker.lower() in cmdline:
            pids.add(proc.pid)
    return pids


def kill(pids: set[int]) -> None:
    import psutil

    for pid in pids:
        try:
            psutil.Process(pid).terminate()
        except Exception:
            pass


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "8000"
    url = f"http://127.0.0.1:{port}/"
    print(f"target: {url}")
    for index, (name, flags) in enumerate(VARIANTS):
        profile = TMP / f"profile-{index}"
        marker = f"profile-{index}"
        cmd = [
            EDGE,
            f"--app={url}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate",
            "--window-size=1280,820",
            *flags,
        ]
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 12
        rendered = False
        while time.time() < deadline:
            pids = pids_for_profile(marker)
            if any("ExamOS" in title for title in window_titles_for_pids(pids)):
                rendered = True
                break
            time.sleep(0.5)
        print(f"{'RENDER' if rendered else 'BLANK ':6} {name:22} {' '.join(flags)}")
        kill(pids_for_profile(marker))
        time.sleep(1.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
