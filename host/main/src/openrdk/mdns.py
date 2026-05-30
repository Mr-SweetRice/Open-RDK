from __future__ import annotations

import socket


def _normalize_mdns_name(name: str | None) -> str:
    cleaned = "".join(
        ch.lower()
        for ch in str(name or "rdk").strip()
        if ch.isalnum() or ch == "-"
    ).strip("-")
    return cleaned or "rdk"


def _lan_ipv4() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class MdnsPublisher:
    def __init__(
        self,
        name: str = "rdk",
        port: int = 8765,
        scheme: str = "http",
        service_name: str = "Open-RDK",
    ):
        self.name = _normalize_mdns_name(name)
        self.port = int(port)
        self.scheme = "https" if str(scheme).lower() == "https" else "http"
        self.service_name = str(service_name or "Open-RDK").strip() or "Open-RDK"
        self.url = f"{self.scheme}://{self.name}.local:{self.port}"
        self._zeroconf = None
        self._service_info = None

    def start(self) -> bool:
        if self._zeroconf is not None:
            return True

        try:
            from zeroconf import ServiceInfo, Zeroconf
        except Exception as exc:
            print(f"[mdns] disabled: zeroconf unavailable ({exc})", flush=True)
            return False

        address = _lan_ipv4()
        server = f"{self.name}.local."
        service_type = f"_{self.scheme}._tcp.local."
        service_full_name = f"{self.service_name}.{service_type}"
        info = ServiceInfo(
            type_=service_type,
            name=service_full_name,
            addresses=[socket.inet_aton(address)],
            port=self.port,
            properties={
                "path": "/",
                "name": self.name,
                "app": "openrdk",
            },
            server=server,
        )
        zeroconf = Zeroconf()
        try:
            zeroconf.register_service(info)
        except Exception as exc:
            zeroconf.close()
            print(f"[mdns] registration failed for {self.url}: {exc}", flush=True)
            return False

        self._zeroconf = zeroconf
        self._service_info = info
        print(f"[mdns] advertising {self.url} ({address}:{self.port})", flush=True)
        return True

    def stop(self):
        zeroconf = self._zeroconf
        info = self._service_info
        self._zeroconf = None
        self._service_info = None
        if zeroconf is None:
            return
        try:
            if info is not None:
                zeroconf.unregister_service(info)
        except Exception:
            pass
        try:
            zeroconf.close()
        except Exception:
            pass
