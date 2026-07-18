"""Local web launcher for Deep Research Agent.

The packaged app is still a web application internally: this module starts the
FastAPI backend, serves the built React SPA, stores local data in a user data
directory, and opens either an embedded WebView window or the default browser.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Sequence
from pathlib import Path

import uvicorn

APP_NAME = "DeepResearchAgent"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _default_data_dir() -> Path:
    override = os.getenv("DRA_DATA_DIR")
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if base:
            return Path(base) / APP_NAME
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    base = os.getenv("XDG_DATA_HOME")
    if base:
        return Path(base).expanduser() / "deep-research-agent"
    return Path.home() / ".local" / "share" / "deep-research-agent"


def _sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.resolve().as_posix()}"


def _configure_environment(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DATABASE_URL", _sqlite_url(data_dir / "deep_research.db"))
    os.environ.setdefault("RUNTIME_CONFIG_PATH", str(data_dir / "runtime_config.json"))
    os.environ.setdefault("SQLITE_JOURNAL_MODE", "TRUNCATE")


def _can_bind(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
    except OSError:
        return False
    return True


def _find_port(host: str, preferred: int) -> int:
    if preferred > 0 and _can_bind(host, preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _browser_host(host: str) -> str:
    if host in {"0.0.0.0", "::"}:
        return DEFAULT_HOST
    return host


class BackendServer:
    def __init__(self, host: str, port: int, log_level: str) -> None:
        self.host = host
        self.port = port
        self.log_level = log_level
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        from deep_research.api import app

        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level=self.log_level,
            access_log=False,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run,
            name="deep-research-backend",
            daemon=False,
        )
        self.thread.start()

    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=10.0)


def _wait_until_ready(url: str, backend: BackendServer, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if not backend.is_alive():
            raise RuntimeError("Backend stopped before it became ready")
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if 200 <= response.status < 500:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"Backend did not become ready in {timeout:.0f}s: {last_error}")


def _open_webview(url: str) -> bool:
    try:
        import webview  # type: ignore[import-not-found]
    except Exception:
        return False

    webview.create_window(APP_NAME, url, width=1280, height=860, min_size=(960, 640))
    webview.start()
    return True


def _wait_forever(backend: BackendServer) -> None:
    try:
        while backend.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        return


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start Deep Research Agent as a local web app")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Backend bind host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Preferred backend port")
    parser.add_argument("--data-dir", help="Directory for SQLite data and runtime_config.json")
    parser.add_argument(
        "--browser", action="store_true", help="Open the default browser instead of WebView"
    )
    parser.add_argument(
        "--no-open", action="store_true", help="Only start the backend and print the URL"
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    data_dir = Path(args.data_dir).expanduser() if args.data_dir else _default_data_dir()
    _configure_environment(data_dir)

    port = _find_port(args.host, args.port)
    url = f"http://{_browser_host(args.host)}:{port}"
    backend = BackendServer(args.host, port, args.log_level)
    backend.start()
    try:
        _wait_until_ready(f"{url}/healthz", backend)

        print(f"Deep Research Agent: {url}", flush=True)
        print(f"Data directory: {data_dir}", flush=True)

        if args.no_open:
            _wait_forever(backend)
            return 0

        if not args.browser and _open_webview(url):
            return 0

        webbrowser.open(url)
        _wait_forever(backend)
        return 0
    finally:
        backend.stop()


if __name__ == "__main__":
    raise SystemExit(main())
