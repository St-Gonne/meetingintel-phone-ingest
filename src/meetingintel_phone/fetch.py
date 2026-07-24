from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .util import (
    IntakeError,
    atomic_write_json,
    ensure_private_directory,
    hash_file,
    load_json,
    parse_time,
    reject_symlink,
    safe_relative_path,
    stable_identity,
    utc_now,
    RunLock,
)

LEDGER_SCHEMA = 1
DEFAULT_SETTLEMENT_SECONDS = 600


@dataclass(frozen=True)
class RemoteItem:
    object_id: str
    relative_path: Path
    size: int
    modified_at: str
    hashes: dict[str, str]

    @property
    def signature(self) -> str:
        return stable_identity(
            [
                self.object_id,
                self.relative_path.as_posix(),
                str(self.size),
                self.modified_at,
                json.dumps(self.hashes, sort_keys=True),
            ]
        )


@dataclass(frozen=True)
class FetchConfig:
    remote: str
    staging_root: Path
    runtime_root: Path
    settlement_seconds: int = DEFAULT_SETTLEMENT_SECONDS
    dry_run: bool = False

    @property
    def ledger_path(self) -> Path:
        return self.runtime_root / "fetch-ledger.json"


@dataclass
class FetchSummary:
    discovered: int = 0
    downloaded: int = 0
    changed: int = 0
    unchanged: int = 0
    pending: int = 0
    settled: int = 0
    failed: int = 0
    decisions: int = 0

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.decisions == 0


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(
    arguments: list[str],
    *,
    runner: Runner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(
            arguments,
            text=True,
            capture_output=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise IntakeError(f"{Path(arguments[0]).name} could not be executed") from exc
    if result.returncode:
        message = (result.stderr or result.stdout or "").strip().splitlines()
        safe = message[-1][:240] if message else f"exit code {result.returncode}"
        raise IntakeError(f"{Path(arguments[0]).name} failed: {safe}")
    return result


def _rclone_binary() -> str:
    found = shutil.which("rclone")
    if not found:
        raise IntakeError("rclone was not found on PATH")
    return found


def _parse_hashes(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, digest in value.items():
        if isinstance(key, str) and isinstance(digest, str) and digest:
            result[key.upper()] = digest.lower()
    return result


def parse_listing(payload: object) -> list[RemoteItem]:
    if not isinstance(payload, list):
        raise IntakeError("rclone listing did not return a list")
    items: list[RemoteItem] = []
    seen_ids: set[str] = set()
    for raw in payload:
        if not isinstance(raw, dict) or raw.get("IsDir") is True:
            continue
        relative = safe_relative_path(raw.get("Path"))
        if relative.suffix.lower() != ".m4a":
            continue
        object_id = raw.get("ID")
        if not isinstance(object_id, str) or not object_id:
            raise IntakeError(
                "remote item has no immutable ID; this backend cannot safely detect moves"
            )
        if object_id in seen_ids:
            raise IntakeError("remote listing contains a duplicate immutable ID")
        seen_ids.add(object_id)
        size = raw.get("Size")
        modified = raw.get("ModTime")
        if type(size) is not int or size < 0 or not isinstance(modified, str):
            raise IntakeError("remote item metadata is incomplete")
        items.append(
            RemoteItem(
                object_id=object_id,
                relative_path=relative,
                size=size,
                modified_at=modified,
                hashes=_parse_hashes(raw.get("Hashes")),
            )
        )
    return sorted(items, key=lambda item: item.relative_path.as_posix())


def list_remote(
    remote: str,
    *,
    runner: Runner = subprocess.run,
) -> list[RemoteItem]:
    result = _run(
        [
            _rclone_binary(),
            "lsjson",
            remote,
            "--recursive",
            "--files-only",
            "--hash",
        ],
        runner=runner,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise IntakeError("rclone listing returned malformed JSON") from exc
    return parse_listing(payload)


def _remote_file(remote: str, relative: Path) -> str:
    return (
        f"{remote}{relative.as_posix()}"
        if remote.endswith(":")
        else f"{remote.rstrip('/')}/{relative.as_posix()}"
    )


def _verify_local(path: Path, item: RemoteItem, expected_sha256: str | None = None) -> str:
    reject_symlink(path, "staged recording")
    if not path.is_file() or path.stat().st_size != item.size:
        raise IntakeError("staged recording size does not match remote metadata")
    for algorithm in ("SHA-256", "SHA256", "MD5", "SHA-1", "SHA1"):
        expected = item.hashes.get(algorithm)
        if expected:
            local_name = {
                "SHA-256": "sha256",
                "SHA256": "sha256",
                "MD5": "md5",
                "SHA-1": "sha1",
                "SHA1": "sha1",
            }[algorithm]
            if hash_file(path, local_name).lower() != expected:
                raise IntakeError("staged recording checksum does not match remote metadata")
            break
    sha256 = hash_file(path)
    if expected_sha256 and sha256 != expected_sha256:
        raise IntakeError("staged recording changed after download")
    return sha256


def _download(
    config: FetchConfig,
    item: RemoteItem,
    *,
    replace_existing: bool = False,
    runner: Runner = subprocess.run,
) -> str:
    ensure_private_directory(config.staging_root)
    destination = config.staging_root / item.relative_path
    current = config.staging_root
    for part in item.relative_path.parts[:-1]:
        current /= part
        reject_symlink(current, "staging directory")
        current.mkdir(mode=0o700, exist_ok=True)
    reject_symlink(destination, "staged recording")
    if destination.exists() and not replace_existing:
        raise IntakeError("untracked destination already exists")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".download", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        _run(
            [
                _rclone_binary(),
                "copyto",
                _remote_file(config.remote, item.relative_path),
                str(temporary),
            ],
            runner=runner,
        )
        sha256 = _verify_local(temporary, item)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        return sha256
    finally:
        if temporary.exists():
            temporary.unlink()


def _empty_ledger() -> dict[str, Any]:
    return {"schema_version": LEDGER_SCHEMA, "records": {}}


def load_fetch_ledger(path: Path) -> dict[str, Any]:
    ledger = load_json(path, _empty_ledger())
    if ledger.get("schema_version") != LEDGER_SCHEMA or not isinstance(
        ledger.get("records"), dict
    ):
        raise IntakeError("fetch ledger has an unsupported schema")
    return ledger


def _record(item: RemoteItem, *, status: str, now: datetime, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "object_id": item.object_id,
        "relative_path": item.relative_path.as_posix(),
        "size": item.size,
        "modified_at": item.modified_at,
        "hashes": item.hashes,
        "signature": item.signature,
        "observed_at": now.isoformat(),
    }
    result.update(extra)
    return result


def fetch(
    config: FetchConfig,
    *,
    runner: Runner = subprocess.run,
    clock: Callable[[], datetime] = utc_now,
    listing: Iterable[RemoteItem] | None = None,
    emit: Callable[[str], None] = print,
) -> FetchSummary:
    if config.settlement_seconds < 0:
        raise IntakeError("settlement interval cannot be negative")
    reject_symlink(config.staging_root, "staging root")
    reject_symlink(config.runtime_root, "runtime root")
    with RunLock(config.runtime_root / "fetch.lock", now=clock):
        return _fetch_locked(
            config,
            runner=runner,
            clock=clock,
            listing=listing,
            emit=emit,
        )


def _fetch_locked(
    config: FetchConfig,
    *,
    runner: Runner,
    clock: Callable[[], datetime],
    listing: Iterable[RemoteItem] | None,
    emit: Callable[[str], None],
) -> FetchSummary:
    ledger = load_fetch_ledger(config.ledger_path)
    records: dict[str, Any] = ledger["records"]
    items = list(listing) if listing is not None else list_remote(config.remote, runner=runner)
    summary = FetchSummary(discovered=len(items))
    for item in items:
        now = clock()
        previous = records.get(item.object_id)
        if isinstance(previous, dict):
            prior_path = previous.get("relative_path")
            if prior_path != item.relative_path.as_posix():
                summary.decisions += 1
                emit(f"DECISION_REQUIRED {item.relative_path} reason=remote_path_changed")
                if not config.dry_run:
                    records[item.object_id] = _record(
                        item,
                        status="requires_operator_decision",
                        now=now,
                        prior_relative_path=prior_path,
                    )
                    atomic_write_json(config.ledger_path, ledger)
                continue

        destination = config.staging_root / item.relative_path
        if isinstance(previous, dict) and previous.get("signature") == item.signature:
            try:
                sha256 = _verify_local(destination, item, previous.get("sha256"))
                first = parse_time(previous.get("first_observed_at"))
                elapsed = (now - first).total_seconds()
                status = (
                    "settled_ready"
                    if previous.get("status") == "settled_ready"
                    or elapsed >= config.settlement_seconds
                    else "pending_settlement"
                )
                summary.unchanged += 1
                if status == "settled_ready":
                    summary.settled += 1
                else:
                    summary.pending += 1
                emit(f"UNCHANGED {item.relative_path} {status}")
                if not config.dry_run:
                    records[item.object_id] = _record(
                        item,
                        status=status,
                        now=now,
                        first_observed_at=first.isoformat(),
                        sha256=sha256,
                    )
                    atomic_write_json(config.ledger_path, ledger)
                continue
            except IntakeError as exc:
                summary.failed += 1
                emit(f"FAILED {item.relative_path} reason={exc}")
                if not config.dry_run:
                    records[item.object_id] = _record(
                        item, status="failed", now=now, failure=str(exc)
                    )
                    atomic_write_json(config.ledger_path, ledger)
                continue

        tracked_change = (
            isinstance(previous, dict)
            and previous.get("relative_path") == item.relative_path.as_posix()
            and previous.get("signature") != item.signature
        )
        if destination.exists() and not tracked_change:
            summary.failed += 1
            emit(f"FAILED {item.relative_path} reason=untracked_destination")
            if not config.dry_run:
                records[item.object_id] = _record(
                    item, status="failed", now=now, failure="untracked_destination"
                )
                atomic_write_json(config.ledger_path, ledger)
            continue

        if config.dry_run:
            summary.downloaded += 1
            summary.pending += 1
            emit(f"WOULD_DOWNLOAD {item.relative_path} pending_settlement")
            continue

        try:
            sha256 = _download(
                config, item, replace_existing=tracked_change, runner=runner
            )
            summary.downloaded += 1
            if tracked_change:
                summary.changed += 1
            summary.pending += 1
            records[item.object_id] = _record(
                item,
                status="pending_settlement",
                now=now,
                first_observed_at=now.isoformat(),
                sha256=sha256,
            )
            atomic_write_json(config.ledger_path, ledger)
            emit(f"DOWNLOADED {item.relative_path} pending_settlement")
        except IntakeError as exc:
            summary.failed += 1
            records[item.object_id] = _record(
                item, status="failed", now=now, failure=str(exc)
            )
            atomic_write_json(config.ledger_path, ledger)
            emit(f"FAILED {item.relative_path} reason={exc}")

    emit(
        "SUMMARY "
        f"discovered={summary.discovered} downloaded={summary.downloaded} "
        f"changed={summary.changed} unchanged={summary.unchanged} pending={summary.pending} "
        f"settled={summary.settled} failed={summary.failed} "
        f"decisions={summary.decisions} dry_run={str(config.dry_run).lower()}"
    )
    return summary


def status(ledger_path: Path) -> dict[str, int]:
    ledger = load_fetch_ledger(ledger_path)
    counts = {
        "pending_settlement": 0,
        "settled_ready": 0,
        "failed": 0,
        "requires_operator_decision": 0,
    }
    for record in ledger["records"].values():
        if isinstance(record, dict) and record.get("status") in counts:
            counts[str(record["status"])] += 1
    return counts
