from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable


class IntakeError(RuntimeError):
    """A safe, operator-facing ingestion failure."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise IntakeError("stored timestamp is missing or invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise IntakeError("stored timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise IntakeError("stored timestamp has no timezone")
    return parsed


def safe_relative_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise IntakeError("remote item has no usable relative path")
    posix = PurePosixPath(value)
    if posix.is_absolute() or ".." in posix.parts or "." in posix.parts:
        raise IntakeError("remote item path is unsafe")
    if any(not part or "\x00" in part for part in posix.parts):
        raise IntakeError("remote item path is unsafe")
    return Path(*posix.parts)


def reject_symlink(path: Path, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode):
        raise IntakeError(f"{label} must not be a symlink")


def ensure_private_directory(path: Path) -> None:
    reject_symlink(path, "runtime directory")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def hash_file(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    reject_symlink(path, "ledger")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntakeError("ledger is unreadable or malformed") from exc
    if not isinstance(payload, dict):
        raise IntakeError("ledger root must be an object")
    return payload


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_private_directory(path.parent)
    reject_symlink(path, "ledger")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def stable_identity(parts: list[str]) -> str:
    encoded = json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class RunLock(AbstractContextManager["RunLock"]):
    """Single-user lock with conservative dead-process stale recovery."""

    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], datetime] = utc_now,
        stale_seconds: int = 3600,
    ) -> None:
        self.path = path
        self.now = now
        self.stale_seconds = stale_seconds
        self.acquired = False

    def _owner_is_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _recover_if_stale(self) -> None:
        reject_symlink(self.path, "fetch lock")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(payload["pid"])
            if pid <= 0:
                raise ValueError
            created_at = parse_time(payload["created_at"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, IntakeError):
            raise IntakeError("fetch lock exists but cannot be safely validated")
        age = (self.now() - created_at).total_seconds()
        if self._owner_is_alive(pid) or age < self.stale_seconds:
            raise IntakeError("another fetch appears to be running")
        self.path.unlink()

    def __enter__(self) -> "RunLock":
        ensure_private_directory(self.path.parent)
        for attempt in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                if attempt:
                    raise IntakeError("fetch lock could not be acquired")
                self._recover_if_stale()
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "pid": os.getpid(),
                        "created_at": self.now().isoformat(),
                    },
                    handle,
                    sort_keys=True,
                )
            self.acquired = True
            return self
        raise IntakeError("fetch lock could not be acquired")

    def __exit__(self, *_args: object) -> None:
        if self.acquired:
            reject_symlink(self.path, "fetch lock")
            self.path.unlink(missing_ok=True)
            self.acquired = False
