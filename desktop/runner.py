"""ExamOS lightweight desktop launcher (no Electron required).

Renamed from ``app.py`` to ``runner.py`` so PyInstaller's import graph can see
the FastAPI application package (a module named ``app`` would shadow it).

Starts the FastAPI backend in-process and opens the dashboard in an Edge/Chrome
application window (``--app``: no address bar, no tabs).

Usage:
    python desktop/runner.py                 # project-seeded database
    python desktop/runner.py --browser chrome
    python desktop/runner.py --fresh         # clean per-user database
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROZEN = bool(getattr(sys, "frozen", False))


def _pick_frozen_data_dir() -> Path:
    """Portable first (data next to the exe), then per-user AppData."""
    exe_dir = Path(sys.executable).resolve().parent
    candidates = [exe_dir / "data", Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ExamOS"]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except Exception:
            continue
    return exe_dir / "data"


if FROZEN:
    # PyInstaller onedir: app/ and web/out/ live in the _internal bundle directory
    BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    BACKEND = BUNDLE
    WEB_OUT = BUNDLE / "web" / "out"
    VAR_DIR = _pick_frozen_data_dir()
else:
    BACKEND = ROOT / "backend"
    WEB_OUT = ROOT / "web" / "out"
    VAR_DIR = BACKEND / "var"

# --fresh must be applied before app.config is imported (settings are frozen at import time)
if "--fresh" in sys.argv:
    VAR_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ExamOS"
VAR_DIR.mkdir(parents=True, exist_ok=True)
_TMP_DIR = VAR_DIR / "tmp"
_TMP_DIR.mkdir(parents=True, exist_ok=True)
os.environ["EXAMOS_VAR_DIR"] = str(VAR_DIR)
os.environ["EXAMOS_WEB_OUT"] = str(WEB_OUT)
os.environ.setdefault("EXAMOS_DATABASE_URL", f"sqlite:///{(VAR_DIR / 'examos.db').as_posix()}")
os.environ["TMP"] = str(_TMP_DIR)
os.environ["TEMP"] = str(_TMP_DIR)

# Imported at module level on purpose: PyInstaller follows this import graph and
# therefore bundles FastAPI / SQLAlchemy / LangGraph / SymPy with the executable.
sys.path.insert(0, str(BACKEND))
from app.db import init_db, session_scope  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models import Question  # noqa: E402
from app.seed.generator import seed_demo  # noqa: E402

BROWSERS = {
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/usr/bin/microsoft-edge",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/google-chrome",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ],
}


def _fatal(message: str) -> None:
    """Windowed executables have no console: log and show a native dialog."""
    try:
        VAR_DIR.mkdir(parents=True, exist_ok=True)
        with open(VAR_DIR / "desktop-error.log", "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except Exception:
        pass
    if FROZEN and sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "ExamOS 启动失败", 0x10)
        except Exception:
            pass
    print(message, file=sys.stderr)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_ready(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("ExamOS backend did not become ready")


def choose_port(requested: int) -> int:
    """Prefer the stable EXAMOS_PORT/8000 so bookmarks keep working."""
    candidates = [requested] if requested else [int(os.environ.get("EXAMOS_PORT", "8000"))]
    for candidate in candidates:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", candidate))
                return candidate
        except OSError:
            continue
    return free_port()


def find_browser(preferred: str | None) -> tuple[str, str] | None:
    order = [preferred] if preferred else ["edge", "chrome"]
    for name in order:
        if not name:
            continue
        for candidate in BROWSERS.get(name, []):
            if Path(candidate).exists():
                return name, candidate
    return None


def ensure_demo_data() -> None:
    from sqlalchemy import func, select

    init_db()
    with session_scope() as db:
        if (db.scalar(select(func.count(Question.id))) or 0) == 0:
            seed_demo(db, force=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="ExamOS desktop")
    parser.add_argument("--browser", choices=["edge", "chrome"], default=None)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--fresh", action="store_true", help="use a clean per-user database")
    parser.add_argument("--no-window", action="store_true", help="only serve, print the URL")
    parser.add_argument(
        "--browser-window",
        action="store_true",
        help="force the Edge/Chrome app-window experience (default outside a packaged exe)",
    )
    parser.add_argument(
        "--control",
        action="store_true",
        help="force the native control window (default for the packaged exe)",
    )
    args = parser.parse_args()

    if not (WEB_OUT / "index.html").exists():
        _fatal(f"缺少前端构建产物：{WEB_OUT}\n请先在 web/ 目录运行 npm run build")
        return 2

    ensure_demo_data()

    # A windowed PyInstaller build has no console: uvicorn's logging formatter
    # calls stream.isatty(), so give the process valid streams and skip its
    # logging configuration entirely.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    import uvicorn

    port = choose_port(args.port)
    config = uvicorn.Config(
        fastapi_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        log_config=None,
    )
    server = uvicorn.Server(config)
    _install_ui_probe()
    thread = threading.Thread(target=server.run, name="examos-api", daemon=True)
    thread.start()
    wait_ready(f"http://127.0.0.1:{port}/health")

    url = f"http://127.0.0.1:{port}/"
    if args.no_window:
        print(url, flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            server.should_exit = True
        return 0

    # The packaged exe defaults to the native control window because a detached
    # PyInstaller process cannot always drive Edge's window station; the source
    # launcher (scripts/desktop.ps1) defaults to the chromeless app window.
    force_control = args.control or (FROZEN and not args.browser_window)
    if force_control:
        _run_control_window(url, server)
        server.should_exit = True
        thread.join(timeout=8)
        return 0

    browser = find_browser(args.browser)
    if browser is None:
        _fatal(f"未找到 Edge/Chrome，请手动打开：{url}")
        return 1

    _, executable = browser
    profile = VAR_DIR / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)

    try:
        # Tier 1: normal sandboxed browser. Tier 2: the same window with the
        # Chromium sandbox disabled — required in some restricted Windows
        # sessions where the renderer otherwise paints a blank page (local-only
        # page, so the sandbox trade-off is acceptable). Tier 3: native Tk
        # control window + default browser.
        _launch_browser(executable, url, profile, [])
        if _wait_for_app_window(profile, timeout=15):
            _wait_for_browser_window(profile, timeout=3600)
        else:
            _kill_browser_processes(profile)
            _launch_browser(executable, url, profile, ["--no-sandbox"])
            if _wait_for_app_window(profile, timeout=25):
                _wait_for_browser_window(profile, timeout=3600)
            else:
                _kill_browser_processes(profile)
                _run_control_window(url, server)
    finally:
        server.should_exit = True
        thread.join(timeout=8)
    return 0


def _launch_browser(executable: str, url: str, profile: Path, extra_flags: list[str]) -> None:
    cli = [
        executable,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
        "--window-size=1480,940",
        *extra_flags,
    ]
    if sys.platform == "win32":
        # ShellExecute via PowerShell's Start-Process mirrors a user double-click;
        # a raw CreateProcess with detached stdio can produce an empty window.
        arg_list = ", ".join("'" + part.replace("'", "''") + "'" for part in cli[1:])
        ps_command = (
            f"Start-Process -FilePath '{cli[0].replace(chr(39), chr(39) * 2)}' "
            f"-ArgumentList @({arg_list})"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps_command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    else:
        subprocess.Popen(cli, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _browser_pids(profile: Path) -> list[int]:
    marker = str(profile).replace("\\", "/").lower()
    try:
        import psutil
    except Exception:
        return []
    pids: list[int] = []
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = " ".join(proc.info["cmdline"] or []).replace("\\", "/").lower()
        except Exception:
            continue
        if marker and marker in cmdline:
            pids.append(proc.pid)
    return pids


def _kill_browser_processes(profile: Path) -> None:
    try:
        import psutil
    except Exception:
        return
    for pid in _browser_pids(profile):
        try:
            psutil.Process(pid).terminate()
        except Exception:
            pass


def _window_titles_for_pids(pids: set[int]) -> list[str]:
    """Enumerate top-level window titles belonging to the given process ids."""
    if sys.platform != "win32" or not pids:
        return []
    import ctypes

    user32 = ctypes.windll.user32
    titles: list[str] = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _lparam):
        window_pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value in pids and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                if buffer.value:
                    titles.append(buffer.value)
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return titles


def _wait_for_app_window(profile: Path, timeout: float = 25.0) -> bool:
    """Wait for a real (on-screen, usable-size) app window showing ExamOS."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        pids = set(_browser_pids(profile))
        for hwnd, title, width, height in _exam_app_windows(pids):
            if width >= 600 and height >= 400:
                _focus_window(hwnd)
                return True
            # a parked/off-screen/tiny window: try to normalise it and re-check
            _focus_window(hwnd)
        time.sleep(0.5)
    return False


def _exam_app_windows(pids: set[int]) -> list[tuple[int, str, int, int]]:
    if sys.platform != "win32" or not pids:
        return []
    import ctypes

    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    windows: list[tuple[int, str, int, int]] = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _):
        window_pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value not in pids or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if "ExamOS" not in buffer.value:
            return True
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        windows.append((int(hwnd), buffer.value, rect.right - rect.left, rect.bottom - rect.top))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return windows


def _focus_window(hwnd: int) -> None:
    if sys.platform != "win32":
        return
    import ctypes

    user32 = ctypes.windll.user32
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.MoveWindow(hwnd, 50, 40, 1460, 900, True)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _install_ui_probe() -> None:
    """Mark when the browser actually requested the dashboard ("/" route)."""
    if getattr(fastapi_app.state, "ui_probe_installed", False):
        return
    from starlette.middleware.base import BaseHTTPMiddleware

    class UiProbe(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path in ("/", "/index.html"):
                fastapi_app.state.ui_loaded = True
            return await call_next(request)

    fastapi_app.add_middleware(UiProbe)
    fastapi_app.state.ui_probe_installed = True
    fastapi_app.state.ui_loaded = False


def _wait_for_ui_hit(timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if getattr(fastapi_app.state, "ui_loaded", False):
            return True
        time.sleep(0.3)
    return False


def _run_control_window(url: str, server) -> None:
    """Native Tk control window used when the app-window browser cannot render."""
    try:
        import tkinter as tk
        import webbrowser
    except Exception:
        _fatal(f"无法打开界面，请手动访问：{url}")
        return

    root = tk.Tk()
    root.title("ExamOS · 个人定制化考研应试智能体")
    root.geometry("560x260")
    root.configure(bg="#12110e")
    tk.Label(
        root,
        text="ExamOS 正在运行",
        bg="#12110e",
        fg="#ede6d6",
        font=("Microsoft YaHei UI", 16, "bold"),
    ).pack(pady=(28, 6))
    tk.Label(
        root,
        text=url,
        bg="#12110e",
        fg="#c8543a",
        font=("Consolas", 11),
    ).pack()
    tk.Label(
        root,
        text="浏览器应用窗口在当前会话无法渲染时，会显示本控制窗口。\n点击下方按钮在默认浏览器打开看板；关闭本窗口即退出 ExamOS。",
        bg="#12110e",
        fg="#a79e8b",
        font=("Microsoft YaHei UI", 9),
        justify="center",
    ).pack(pady=12)

    def open_dashboard() -> None:
        webbrowser.open(url)

    def quit_app() -> None:
        root.destroy()

    row = tk.Frame(root, bg="#12110e")
    row.pack()
    tk.Button(row, text="打开看板", command=open_dashboard, width=14, bg="#c8543a", fg="#f7f0e2", bd=0).pack(side="left", padx=8)
    tk.Button(row, text="退出", command=quit_app, width=10, bd=0).pack(side="left", padx=8)

    open_dashboard()
    root.mainloop()
    server.should_exit = True


def _wait_for_browser_window(profile: Path, timeout: float = 90.0) -> None:
    """Keep the backend alive until the app window (dedicated profile) is closed."""
    marker = str(profile).replace("\\", "/").lower()
    try:
        import psutil
    except Exception:  # pragma: no cover - psutil is bundled with PyInstaller
        while True:
            time.sleep(1)
    deadline = time.time() + timeout
    target = None
    while time.time() < deadline:
        for proc in psutil.process_iter(["cmdline"]):
            try:
                cmdline = " ".join(proc.info["cmdline"] or []).replace("\\", "/").lower()
            except Exception:
                continue
            if marker and marker in cmdline:
                target = proc
                break
        if target is not None:
            break
        time.sleep(0.4)
    if target is None:
        return
    try:
        target.wait()
    except Exception:  # pragma: no cover - window closed or process recycled
        pass


if __name__ == "__main__":
    import traceback

    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        _fatal("ExamOS 启动失败：\n" + traceback.format_exc())
        raise SystemExit(1)
