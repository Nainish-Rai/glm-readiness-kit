"""Run the bridge proxy on a real socket in a background thread (tests, proof scripts, ``serve``)."""

from __future__ import annotations

import socket
import threading
import time

import uvicorn
from starlette.applications import Starlette


class BridgeServer:
    def __init__(self, app: Starlette, host: str = "127.0.0.1", port: int = 0) -> None:
        self.app = app
        self.host = host
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self.port = self._sock.getsockname()[1]
        self._server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> BridgeServer:
        self._thread = threading.Thread(target=self._server.run, kwargs={"sockets": [self._sock]}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("bridge server failed to start")
            time.sleep(0.01)
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=10)  # lifespan shutdown writes the final result.json
        self._sock.close()

    def __enter__(self) -> BridgeServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
