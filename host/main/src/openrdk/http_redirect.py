from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _RedirectHandler(BaseHTTPRequestHandler):
    target_port = 8765
    target_scheme = "http"

    def do_GET(self):
        self._redirect()

    def do_HEAD(self):
        self._redirect()

    def do_POST(self):
        self._redirect()

    def log_message(self, _format, *_args):
        return

    def _redirect(self):
        host = self.headers.get("Host", "rdk.local").split(":", 1)[0] or "rdk.local"
        path = self.path or "/"
        target = f"{self.target_scheme}://{host}:{int(self.target_port)}{path}"
        self.send_response(307)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()


class HttpRedirectServer:
    def __init__(
        self,
        target_port: int = 8765,
        target_scheme: str = "http",
        listen_host: str = "0.0.0.0",
        listen_port: int = 80,
    ):
        self.target_port = int(target_port)
        self.target_scheme = "https" if str(target_scheme).lower() == "https" else "http"
        self.listen_host = str(listen_host or "0.0.0.0")
        self.listen_port = int(listen_port)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://rdk.local"

    def start(self) -> bool:
        if self._server is not None:
            return True

        handler = type(
            "OpenRdkRedirectHandler",
            (_RedirectHandler,),
            {
                "target_port": self.target_port,
                "target_scheme": self.target_scheme,
            },
        )
        try:
            server = ThreadingHTTPServer((self.listen_host, self.listen_port), handler)
        except OSError as exc:
            print(
                f"[redirect] disabled: cannot bind {self.listen_host}:{self.listen_port} ({exc})",
                flush=True,
            )
            return False

        thread = threading.Thread(
            target=server.serve_forever,
            name="openrdk-http-redirect",
            daemon=True,
        )
        thread.start()
        self._server = server
        self._thread = thread
        print(
            f"[redirect] http://rdk.local -> {self.target_scheme}://rdk.local:{self.target_port}",
            flush=True,
        )
        return True

    def stop(self):
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        if thread and thread.is_alive():
            thread.join(timeout=1)
