from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path


STATE_DIR_ENV = "CONTROL_HUB_SERVICE_STATE_DIR"


def default_state_dir() -> Path:
    configured = os.environ.get(STATE_DIR_ENV, "").strip()
    if configured:
        return Path(os.path.expandvars(os.path.expanduser(configured))).resolve()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return (root / "openrdk" / "control-hub-service").resolve()


def atomic_json_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}_", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


class JsonStore:
    def __init__(self, path: Path, default: dict):
        self.path = path
        self.default = default
        self.lock = threading.RLock()

    def load(self) -> dict:
        with self.lock:
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                value = None
            return value if isinstance(value, dict) else json.loads(json.dumps(self.default))

    def save(self, value: dict) -> dict:
        with self.lock:
            atomic_json_write(self.path, value)
        return value


class ScriptStore:
    MAX_BYTES = 50 * 1024 * 1024
    SAFE_UPLOAD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,99}\.py$", re.IGNORECASE)
    HASHED_REFERENCE = re.compile(r"^(?:managed|external):[0-9a-f]{20}$")

    def __init__(self, state_dir: Path):
        self.managed = (state_dir / "scripts").resolve()
        self.managed.mkdir(parents=True, exist_ok=True)
        self.directories_store = JsonStore(
            state_dir / "script_directories.json", {"version": 1, "directories": []}
        )
        self.lock = threading.RLock()

    @staticmethod
    def _key(path: Path) -> str:
        return os.path.normcase(str(path.resolve()))

    @staticmethod
    def _digest(path: Path, length: int) -> str:
        return hashlib.sha256(os.path.normcase(str(path.resolve())).encode("utf-8")).hexdigest()[:length]

    def _records(self) -> list[dict]:
        records = [{"id": "managed", "path": str(self.managed), "label": "Diretório gerenciado", "managed": True}]
        seen = {self._key(self.managed)}
        for raw in self.directories_store.load().get("directories", []):
            if not isinstance(raw, str) or not raw.strip():
                continue
            path = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
            if self._key(path) in seen:
                continue
            seen.add(self._key(path))
            records.append({
                "id": f"directory:{self._digest(path, 12)}", "path": str(path),
                "label": path.name or str(path), "managed": False,
            })
        return records

    def _scan(self, records: list[dict]) -> list[dict]:
        scripts, seen = [], set()
        for record in records:
            folder = Path(record["path"])
            if not folder.is_dir():
                continue
            try:
                paths = list(folder.iterdir())
            except OSError:
                continue
            for path in paths:
                if not path.is_file() or path.suffix.lower() != ".py":
                    continue
                resolved = path.resolve()
                key = self._key(resolved)
                if key in seen:
                    continue
                seen.add(key)
                managed = bool(record["managed"])
                reference = path.name if managed and self.SAFE_UPLOAD.fullmatch(path.name) else (
                    f"{'managed' if managed else 'external'}:{self._digest(resolved, 20)}"
                )
                try:
                    stat = resolved.stat()
                except OSError:
                    continue
                scripts.append({
                    "name": path.name, "reference": reference, "path": str(resolved),
                    "size": stat.st_size, "updated_at": stat.st_mtime,
                    "directory_id": record["id"], "directory_path": record["path"],
                    "directory_label": record["label"], "managed": managed,
                })
        return sorted(scripts, key=lambda item: (
            0 if item["managed"] else 1, item["directory_label"].lower(), item["name"].lower()
        ))

    def list(self) -> list[dict]:
        with self.lock:
            return self._scan(self._records())

    def directories(self) -> list[dict]:
        with self.lock:
            records = self._records()
            scripts = self._scan(records)
        counts: dict[str, int] = {}
        for script in scripts:
            counts[script["directory_id"]] = counts.get(script["directory_id"], 0) + 1
        return [{**record, "available": Path(record["path"]).is_dir(),
                 "script_count": counts.get(record["id"], 0)} for record in records]

    def add_directory(self, raw: str) -> dict:
        text = str(raw or "").strip()
        if not text or "\x00" in text:
            raise ValueError("directory path is required")
        expanded = os.path.expandvars(os.path.expanduser(text))
        if not os.path.isabs(expanded):
            raise ValueError("directory path must be absolute")
        path = Path(expanded).resolve()
        if not path.is_dir():
            raise ValueError("script directory does not exist")
        with self.lock:
            current = self.directories_store.load()
            paths = [str(Path(item).resolve()) for item in current.get("directories", []) if isinstance(item, str)]
            if self._key(path) != self._key(self.managed) and all(self._key(Path(item)) != self._key(path) for item in paths):
                paths.append(str(path))
                self.directories_store.save({"version": 1, "directories": paths})
            return next(item for item in self.directories() if self._key(Path(item["path"])) == self._key(path))

    def remove_directory(self, directory_id: str) -> None:
        if directory_id == "managed":
            raise ValueError("managed directory cannot be removed")
        with self.lock:
            current = self.directories_store.load()
            paths = [item for item in current.get("directories", []) if isinstance(item, str)]
            remaining = [item for item in paths if f"directory:{self._digest(Path(item), 12)}" != directory_id]
            if len(remaining) == len(paths):
                raise KeyError(directory_id)
            self.directories_store.save({"version": 1, "directories": remaining})

    def save(self, filename: str, content: str) -> dict:
        name = str(filename or "").strip()
        if not self.SAFE_UPLOAD.fullmatch(name) or Path(name).name != name:
            raise ValueError("invalid Python filename")
        encoded = str(content).encode("utf-8")
        if len(encoded) > self.MAX_BYTES:
            raise ValueError("script exceeds the 50 MB limit")
        target = self.managed / name
        temporary = target.with_suffix(target.suffix + ".upload")
        temporary.write_bytes(encoded)
        os.replace(temporary, target)
        return next(item for item in self.list() if item["path"] == str(target.resolve()))

    def resolve(self, reference: str) -> Path:
        value = str(reference or "").strip()
        if self.SAFE_UPLOAD.fullmatch(value) and Path(value).name == value:
            target = (self.managed / value).resolve()
            if target.parent != self.managed:
                raise ValueError("invalid script reference")
            if not target.is_file():
                raise FileNotFoundError(value)
            return target
        if not self.HASHED_REFERENCE.fullmatch(value):
            raise ValueError("invalid script reference")
        matches = [Path(item["path"]) for item in self.list() if item["reference"] == value]
        if len(matches) != 1:
            raise FileNotFoundError(value)
        return matches[0]


def reserve_device(state_dir: Path, serial_number: str, device_node: str) -> None:
    store = JsonStore(state_dir / "reserved_devices.json", {"version": 1, "devices": []})
    payload = store.load()
    devices = payload.get("devices", []) if isinstance(payload.get("devices"), list) else []
    match = next((item for item in devices if isinstance(item, dict) and (
        (serial_number and item.get("serial_number") == serial_number)
        or (device_node and item.get("device_node") == device_node)
    )), None)
    if match is None:
        match = {}
        devices.append(match)
    match.update({"serial_number": serial_number, "device_node": device_node,
                  "module_type": "control_hub_module"})
    store.save({"version": 1, "devices": devices})
