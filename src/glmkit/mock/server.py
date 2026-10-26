"""Run the mock in a background thread (in-process) on a real socket.

Used by the conformance runner for ``spawn_mock`` profiles and by tests of any slice::

    with MockServer(MockConfig(defects=["forced-thinking-500"])) as srv:
        profile = get_profile("mock").with_base_url(srv.url + "/api/paas/v4")
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
import yaml

from glmkit.mock.app import create_app
from glmkit.mock.types import MockConfig


def load_rules(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rules = data.get("rules", data) if isinstance(data, dict) else data
    if not isinstance(rules, list):
        raise ValueError(f"{path}: expected a list of rules or {{rules: [...]}}")
    return rules


class MockServer:
    def __init__(self, config: MockConfig | None = None, host: str = "127.0.0.1", port: int = 0) -> None:
        self.config = config or MockConfig()
        self.host = host
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self.port = self._sock.getsockname()[1]
        app = create_app(self.config)
        self._server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off", access_log=False))
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> MockServer:
        self._thread = threading.Thread(target=self._server.run, kwargs={"sockets": [self._sock]}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("mock server failed to start")
            time.sleep(0.01)
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=10)
        self._sock.close()

    def __enter__(self) -> MockServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
