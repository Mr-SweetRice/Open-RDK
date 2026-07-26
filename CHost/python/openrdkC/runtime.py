from __future__ import annotations

from . import _openrdkC


class CommsRuntimeC:
    """Lifecycle-only native runtime scaffold introduced by CHost Step 2.

    Device discovery and communication are intentionally not implemented yet.
    This class never imports or delegates to the standard ``openrdk`` runtime.
    """

    def __init__(self, *, auto_start: bool = True):
        self._native = _openrdkC.RuntimeHandle()
        if auto_start:
            self.start()

    @property
    def is_running(self) -> bool:
        return bool(self._native.is_running)

    @property
    def native_version(self) -> str:
        return str(_openrdkC.native_version())

    def start(self) -> "CommsRuntimeC":
        self._native.start()
        return self

    def ensure_running(self) -> "CommsRuntimeC":
        if not self.is_running:
            raise RuntimeError("openrdkC runtime is not running")
        return self

    def stop(self, timeout_sec: float = 2.0) -> None:
        # Reserved for parity with the standard API. No worker exists in Step 2.
        if float(timeout_sec) < 0:
            raise ValueError("timeout_sec must be >= 0")
        self._native.stop()

    def __enter__(self) -> "CommsRuntimeC":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def list_devices(self, *args, **kwargs):
        del args, kwargs
        raise NotImplementedError("device discovery begins after CHost Step 2")

