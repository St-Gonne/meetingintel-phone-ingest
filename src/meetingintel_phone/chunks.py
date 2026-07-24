from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

from .fetch import load_fetch_ledger
from .util import (
    IntakeError,
    atomic_write_json,
    ensure_private_directory,
    hash_file,
    load_json,
    reject_symlink,
    safe_relative_path,
    stable_identity,
    utc_now,
    RunLock,
)

FILENAME_PATTERN = re.compile(
    r"^(?P<year>\d{4})_(?P<month>\d{2})_(?P<day>\d{2})_"
    r"(?P<hour>\d{2})_(?P<minute>\d{2})_(?P<second>\d{2})"
    r"(?:[^/]*)\.m4a$",
    re.IGNORECASE,
)
FULL_CHUNK_MIN_SECONDS = 899.0
FULL_CHUNK_MAX_SECONDS = 901.0
MIN_CONTINUITY_GAP_SECONDS = -1.0
MAX_CONTINUITY_GAP_SECONDS = 5.0
NORMALIZE_LEDGER_SCHEMA = 1


@dataclass(frozen=True)
class Segment:
    object_id: str
    source_path: Path
    relative_path: Path
    started_at: datetime
    duration_seconds: float
    size: int
    sha256: str

    @property
    def ended_at(self) -> datetime:
        return self.started_at + timedelta(seconds=self.duration_seconds)

    @property
    def is_full(self) -> bool:
        return FULL_CHUNK_MIN_SECONDS <= self.duration_seconds <= FULL_CHUNK_MAX_SECONDS


@dataclass(frozen=True)
class ReviewSet:
    recording_date: date
    segments: tuple[Segment, ...]
    suggested_groups: tuple[tuple[int, ...], ...] | None
    suggestion_reason: str


@dataclass(frozen=True)
class ReviewPlan:
    groups: tuple[tuple[int, ...], ...]
    discarded: tuple[int, ...] = ()


@dataclass
class NormalizeSummary:
    normalized: int = 0
    discarded: int = 0
    failed: int = 0


def parse_start(filename: str, timezone_name: str) -> datetime:
    match = FILENAME_PATTERN.match(filename)
    if not match:
        raise IntakeError(
            "filename does not begin with YYYY_MM_DD_HH_MM_SS"
        )
    values = {key: int(value) for key, value in match.groupdict().items()}
    try:
        return datetime(**values, tzinfo=ZoneInfo(timezone_name))
    except (ValueError, KeyError) as exc:
        raise IntakeError("filename timestamp or timezone is invalid") from exc


def probe_duration(
    path: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise IntakeError("ffprobe was not found on PATH")
    try:
        result = runner(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise IntakeError("ffprobe could not inspect the recording") from exc
    if result.returncode:
        raise IntakeError("ffprobe rejected the recording")
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise IntakeError("ffprobe returned an invalid duration") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise IntakeError("recording duration is invalid")
    return duration


def continuity_gap(left: Segment, right: Segment) -> float:
    return (right.started_at - left.ended_at).total_seconds()


def is_continuous(left: Segment, right: Segment) -> bool:
    gap = continuity_gap(left, right)
    return MIN_CONTINUITY_GAP_SECONDS <= gap <= MAX_CONTINUITY_GAP_SECONDS


def _automatic_groups(segments: Sequence[Segment]) -> tuple[tuple[tuple[int, ...], ...] | None, str]:
    groups: list[tuple[int, ...]] = []
    index = 0
    while index < len(segments):
        current = segments[index]
        if (
            index + 1 < len(segments)
            and current.is_full
            and is_continuous(current, segments[index + 1])
        ):
            following = segments[index + 1]
            if not following.is_full:
                return None, "one full recorder chunk followed by a continuous tail is ambiguous"
            group = [index + 1, index + 2]
            index += 2
            while (
                index < len(segments)
                and segments[index - 1].is_full
                and is_continuous(segments[index - 1], segments[index])
            ):
                group.append(index + 1)
                index += 1
                if not segments[index - 1].is_full:
                    break
            groups.append(tuple(group))
            continue
        groups.append((index + 1,))
        index += 1
    return tuple(groups), "recorder-sized chunks and continuity gaps support this complete plan"


def _normalized_fingerprints(ledger_path: Path) -> set[str]:
    ledger = load_json(
        ledger_path, {"schema_version": NORMALIZE_LEDGER_SCHEMA, "records": {}}
    )
    if ledger.get("schema_version") != NORMALIZE_LEDGER_SCHEMA or not isinstance(
        ledger.get("records"), dict
    ):
        raise IntakeError("normalization ledger has an unsupported schema")
    result: set[str] = set()
    for record in ledger["records"].values():
        if not isinstance(record, dict):
            continue
        values = record.get("source_sha256")
        if isinstance(values, list):
            result.update(value for value in values if isinstance(value, str))
    return result


def collect_review_sets(
    *,
    fetch_ledger_path: Path,
    staging_root: Path,
    normalize_ledger_path: Path,
    timezone_name: str,
    duration_probe: Callable[[Path], float] = probe_duration,
) -> list[ReviewSet]:
    staging = staging_root.expanduser().resolve()
    reject_symlink(staging, "staging root")
    fetch_ledger = load_fetch_ledger(fetch_ledger_path)
    resolved = _normalized_fingerprints(normalize_ledger_path)
    buckets: dict[date, list[Segment]] = {}
    for record in fetch_ledger["records"].values():
        if not isinstance(record, dict) or record.get("status") != "settled_ready":
            continue
        relative = safe_relative_path(record.get("relative_path"))
        path = staging / relative
        current = staging
        for part in relative.parts:
            current /= part
            reject_symlink(current, "staged recording path")
        if not path.is_file():
            raise IntakeError("a settled recording is missing from staging")
        expected_size = record.get("size")
        expected_sha256 = record.get("sha256")
        if type(expected_size) is not int or not isinstance(expected_sha256, str):
            raise IntakeError("settled recording has incomplete integrity metadata")
        if path.stat().st_size != expected_size:
            raise IntakeError("settled recording size changed")
        sha256 = hash_file(path)
        if sha256 != expected_sha256:
            raise IntakeError("settled recording checksum changed")
        if sha256 in resolved:
            continue
        started = parse_start(path.name, timezone_name)
        segment = Segment(
            object_id=str(record.get("object_id")),
            source_path=path,
            relative_path=relative,
            started_at=started,
            duration_seconds=duration_probe(path),
            size=expected_size,
            sha256=sha256,
        )
        buckets.setdefault(started.date(), []).append(segment)

    reviews: list[ReviewSet] = []
    for recording_date, values in sorted(buckets.items()):
        segments = tuple(sorted(values, key=lambda item: (item.started_at, item.relative_path.as_posix())))
        if len({item.sha256 for item in segments}) != len(segments):
            raise IntakeError("duplicate recording fingerprints require operator investigation")
        suggestion, reason = _automatic_groups(segments)
        reviews.append(
            ReviewSet(
                recording_date=recording_date,
                segments=segments,
                suggested_groups=suggestion,
                suggestion_reason=reason,
            )
        )
    return reviews


def format_duration(seconds: float) -> str:
    minutes, remainder = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours}h {minutes:02d}m {remainder:06.3f}s"
    if minutes:
        return f"{minutes}m {remainder:06.3f}s"
    return f"{remainder:.3f}s"


def format_review(review: ReviewSet) -> str:
    lines = [
        f"PHONE RECORDING REVIEW — {review.recording_date.isoformat()}",
        f"{len(review.segments)} settled segments · originals retained",
        "",
        " #  Filename                         Start        End*         Duration       Gap",
    ]
    previous: Segment | None = None
    for number, segment in enumerate(review.segments, 1):
        gap = "—" if previous is None else f"{continuity_gap(previous, segment):+.3f}s"
        lines.append(
            f"{number:>2}  {segment.source_path.name:<31} "
            f"{segment.started_at.strftime('%H:%M:%S'):<12} "
            f"{segment.ended_at.strftime('%H:%M:%S'):<12} "
            f"{format_duration(segment.duration_seconds):<14} {gap}"
        )
        previous = segment
    lines.extend(
        [
            "",
            "* End is filename start plus ffprobe-measured duration.",
            "",
        ]
    )
    if review.suggested_groups is None:
        lines.append(f"No safe automatic suggestion: {review.suggestion_reason}.")
    else:
        lines.append("Suggested meetings:")
        for number, group in enumerate(review.suggested_groups, 1):
            lines.append(
                f"  Meeting {number}: segments {', '.join(str(item) for item in group)}"
            )
        lines.append(f"Reason: {review.suggestion_reason}.")
    return "\n".join(lines)


def parse_partition(value: str, segment_count: int) -> tuple[tuple[int, ...], ...]:
    try:
        groups = tuple(
            tuple(int(part.strip()) for part in raw.split(","))
            for raw in value.split(";")
        )
    except ValueError as exc:
        raise IntakeError("grouping must look like 1,2;3") from exc
    if not groups or any(not group for group in groups):
        raise IntakeError("grouping must include at least one meeting")
    flattened = [item for group in groups for item in group]
    if flattened != list(range(1, segment_count + 1)):
        raise IntakeError(
            "grouping must use every segment exactly once in chronological order"
        )
    return groups


def _concat_manifest(paths: Sequence[Path]) -> str:
    lines: list[str] = []
    for path in paths:
        escaped = str(path).replace("\\", "\\\\").replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def _copy_or_join(
    paths: Sequence[Path],
    destination: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    if len(paths) == 1:
        shutil.copyfile(paths[0], destination)
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise IntakeError("ffmpeg was not found on PATH")
    descriptor, manifest_name = tempfile.mkstemp(
        prefix=".concat.", suffix=".txt", dir=destination.parent
    )
    manifest = Path(manifest_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(_concat_manifest(paths))
        result = runner(
            [
                ffmpeg,
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(manifest),
                "-c",
                "copy",
                str(destination),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=600,
        )
        if result.returncode:
            raise IntakeError("ffmpeg could not join the selected segments")
    finally:
        manifest.unlink(missing_ok=True)


def normalize(
    review: ReviewSet,
    plan: ReviewPlan,
    *,
    canonical_root: Path,
    normalize_ledger_path: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> NormalizeSummary:
    with RunLock(normalize_ledger_path.parent / "normalize.lock"):
        return _normalize_locked(
            review,
            plan,
            canonical_root=canonical_root,
            normalize_ledger_path=normalize_ledger_path,
            runner=runner,
        )


def _normalize_locked(
    review: ReviewSet,
    plan: ReviewPlan,
    *,
    canonical_root: Path,
    normalize_ledger_path: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> NormalizeSummary:
    used = [item for group in plan.groups for item in group] + list(plan.discarded)
    if sorted(used) != list(range(1, len(review.segments) + 1)) or len(set(used)) != len(used):
        raise IntakeError("plan must account for every segment exactly once")
    canonical = canonical_root.expanduser().resolve()
    reject_symlink(canonical, "canonical root")
    ensure_private_directory(canonical)
    ledger = load_json(
        normalize_ledger_path,
        {"schema_version": NORMALIZE_LEDGER_SCHEMA, "records": {}},
    )
    if ledger.get("schema_version") != NORMALIZE_LEDGER_SCHEMA or not isinstance(
        ledger.get("records"), dict
    ):
        raise IntakeError("normalization ledger has an unsupported schema")
    records: dict[str, Any] = ledger["records"]
    summary = NormalizeSummary()

    for segment in review.segments:
        reject_symlink(segment.source_path, "reviewed recording")
        if (
            not segment.source_path.is_file()
            or segment.source_path.stat().st_size != segment.size
            or hash_file(segment.source_path) != segment.sha256
        ):
            raise IntakeError("reviewed recording changed before normalization")

    for group in plan.groups:
        segments = [review.segments[index - 1] for index in group]
        group_id = stable_identity([segment.sha256 for segment in segments])
        folder_name = (
            f"phone_{segments[0].started_at.strftime('%Y-%m-%d_%H-%M-%S')}_"
            f"{group_id[:16]}"
        )
        target = canonical / folder_name
        if group_id in records:
            continue
        if target.exists():
            raise IntakeError("canonical target already exists without ledger evidence")
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{folder_name}.", dir=canonical)
        )
        try:
            audio = temporary / "audio.m4a"
            _copy_or_join([segment.source_path for segment in segments], audio, runner=runner)
            if not audio.is_file() or audio.stat().st_size <= 0:
                raise IntakeError("canonical audio was not created")
            metadata = {
                "schema_version": 1,
                "source_type": "phone_recording",
                "created_at": utc_now().isoformat(),
                "started_at": segments[0].started_at.isoformat(),
                "group_id": group_id,
                "source_sha256": [segment.sha256 for segment in segments],
                "source_relative_paths": [
                    segment.relative_path.as_posix() for segment in segments
                ],
                "segment_count": len(segments),
                "canonical_sha256": hash_file(audio),
            }
            atomic_write_json(temporary / "meeting_source.json", metadata)
            os.replace(temporary, target)
            try:
                records[group_id] = {
                    "status": "normalized",
                    "target": folder_name,
                    "source_sha256": metadata["source_sha256"],
                    "created_at": metadata["created_at"],
                }
                atomic_write_json(normalize_ledger_path, ledger)
            except Exception:
                records.pop(group_id, None)
                shutil.rmtree(target, ignore_errors=True)
                raise
            summary.normalized += 1
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    for index in plan.discarded:
        segment = review.segments[index - 1]
        record_id = stable_identity(["discard", segment.sha256])
        if record_id not in records:
            records[record_id] = {
                "status": "discarded",
                "source_sha256": [segment.sha256],
                "created_at": utc_now().isoformat(),
            }
            atomic_write_json(normalize_ledger_path, ledger)
            summary.discarded += 1
    return summary
