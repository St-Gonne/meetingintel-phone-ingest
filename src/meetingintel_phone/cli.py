from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Sequence

from .chunks import (
    ReviewPlan,
    collect_review_sets,
    format_review,
    normalize,
    parse_partition,
)
from .fetch import FetchConfig, fetch, status
from .util import IntakeError, atomic_write_json, load_json

DEFAULT_RUNTIME_ROOT = Path.home() / ".local" / "share" / "mi-phone"


def _config_path(runtime_root: Path) -> Path:
    return runtime_root / "config.json"


def _load_config(runtime_root: Path) -> dict[str, Any]:
    return load_json(_config_path(runtime_root), {"schema_version": 1})


def _configured_paths(runtime_root: Path, config: dict[str, Any]) -> tuple[Path, Path]:
    staging = Path(str(config.get("staging_root", runtime_root / "staging"))).expanduser()
    canonical = Path(
        str(config.get("canonical_root", runtime_root / "canonical"))
    ).expanduser()
    return staging, canonical


def _runtime_from_args(args: argparse.Namespace) -> Path:
    return Path(args.runtime_root).expanduser().resolve()


def _configure(args: argparse.Namespace) -> int:
    runtime = _runtime_from_args(args)
    existing = _load_config(runtime)
    existing.update(
        {
            "schema_version": 1,
            "remote": args.remote,
            "timezone": args.timezone,
            "staging_root": str(Path(args.staging_root).expanduser().resolve()),
            "canonical_root": str(Path(args.canonical_root).expanduser().resolve()),
        }
    )
    atomic_write_json(_config_path(runtime), existing)
    print("Configuration saved locally.")
    print("No remote access or audio processing occurred.")
    return 0


def _fetch(args: argparse.Namespace) -> int:
    runtime = _runtime_from_args(args)
    configured = _load_config(runtime)
    remote = args.remote or configured.get("remote")
    if not isinstance(remote, str) or not remote:
        raise IntakeError("no rclone remote folder is configured")
    staging, _ = _configured_paths(runtime, configured)
    summary = fetch(
        FetchConfig(
            remote=remote,
            staging_root=staging,
            runtime_root=runtime,
            settlement_seconds=args.settlement_seconds,
            dry_run=args.dry_run,
        )
    )
    return 0 if summary.ok else 1


def _status(args: argparse.Namespace) -> int:
    runtime = _runtime_from_args(args)
    counts = status(runtime / "fetch-ledger.json")
    print(
        "MI_PHONE_STATUS "
        + " ".join(f"{key}={value}" for key, value in counts.items())
    )
    return 0 if not counts["failed"] and not counts["requires_operator_decision"] else 1


def _parse_indexes(value: str, segment_count: int) -> tuple[int, ...]:
    try:
        indexes = tuple(int(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise IntakeError("selection must be comma-separated segment numbers") from exc
    if (
        not indexes
        or tuple(sorted(indexes)) != indexes
        or len(set(indexes)) != len(indexes)
        or any(index < 1 or index > segment_count for index in indexes)
    ):
        raise IntakeError("selection must be unique, chronological, and in range")
    return indexes


def _preview(plan: ReviewPlan) -> str:
    lines = ["Proposed plan:"]
    for number, group in enumerate(plan.groups, 1):
        lines.append(
            f"  Meeting {number}: segments {', '.join(str(item) for item in group)}"
        )
    if plan.discarded:
        lines.append(
            f"  Discard from future review: {', '.join(str(item) for item in plan.discarded)}"
        )
        lines.append("  Original staged files remain on disk.")
    return "\n".join(lines)


def _prompt(
    review_count: int,
    suggestion: tuple[tuple[int, ...], ...] | None,
    *,
    input_func: Callable[[str], str] = input,
) -> ReviewPlan | None:
    for _ in range(12):
        try:
            choice = input_func(
                "\n[a] Accept suggestion  [e] Edit groups  [s] Separate "
                "[d] Discard  [q] Quit safely: "
            )
        except (EOFError, KeyboardInterrupt):
            return None
        plan: ReviewPlan | None = None
        if choice == "q":
            return None
        if choice == "a":
            if suggestion is None:
                print("No safe automatic suggestion is available.")
                continue
            plan = ReviewPlan(groups=suggestion)
        elif choice == "s":
            plan = ReviewPlan(groups=tuple((index,) for index in range(1, review_count + 1)))
        elif choice == "e":
            print("Examples: 1,2,3   1,2;3   1;2;3")
            try:
                raw = input_func("Grouping: ")
                plan = ReviewPlan(groups=parse_partition(raw, review_count))
            except (EOFError, KeyboardInterrupt):
                return None
            except IntakeError as exc:
                print(f"Invalid grouping: {exc}")
                continue
        elif choice == "d":
            try:
                discarded = _parse_indexes(
                    input_func("Segments to discard: "), review_count
                )
            except (EOFError, KeyboardInterrupt):
                return None
            except IntakeError as exc:
                print(f"Invalid selection: {exc}")
                continue
            remaining = tuple(
                (index,)
                for index in range(1, review_count + 1)
                if index not in discarded
            )
            plan = ReviewPlan(groups=remaining, discarded=discarded)
        else:
            print("Enter one lowercase option shown above.")
            continue
        print(_preview(plan))
        try:
            if plan.discarded:
                confirmed = input_func("Type DISCARD to confirm: ") == "DISCARD"
            else:
                confirmed = input_func("[c] Confirm  [q] Cancel: ") == "c"
        except (EOFError, KeyboardInterrupt):
            return None
        if confirmed:
            return plan
    return None


def _review(args: argparse.Namespace) -> int:
    if not sys.stdin.isatty() and not args.dry_run:
        raise IntakeError("interactive review requires a terminal; use --dry-run to inspect")
    runtime = _runtime_from_args(args)
    configured = _load_config(runtime)
    staging, canonical = _configured_paths(runtime, configured)
    timezone_name = str(configured.get("timezone", "UTC"))
    reviews = collect_review_sets(
        fetch_ledger_path=runtime / "fetch-ledger.json",
        staging_root=staging,
        normalize_ledger_path=runtime / "normalize-ledger.json",
        timezone_name=timezone_name,
    )
    if args.date:
        try:
            wanted = date.fromisoformat(args.date)
        except ValueError as exc:
            raise IntakeError("--date must be YYYY-MM-DD") from exc
        reviews = [review for review in reviews if review.recording_date == wanted]
    elif reviews:
        reviews = [reviews[-1]]
    if not reviews:
        print("No settled recordings require review.")
        return 0

    for review in reviews:
        print(format_review(review))
        if args.dry_run:
            continue
        plan = _prompt(len(review.segments), review.suggested_groups)
        if plan is None:
            print("No changes made.")
            continue
        summary = normalize(
            review,
            plan,
            canonical_root=canonical,
            normalize_ledger_path=runtime / "normalize-ledger.json",
        )
        print(
            f"NORMALIZED meetings={summary.normalized} "
            f"discarded={summary.discarded} failed={summary.failed}"
        )
        print("Original staged files: retained")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mi-phone",
        description="Safely retrieve and normalize segmented phone recordings.",
    )
    parser.add_argument(
        "--runtime-root",
        default=str(DEFAULT_RUNTIME_ROOT),
        help="private local state directory",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser("configure", help="save local configuration")
    configure.add_argument("--remote", required=True, help="rclone remote folder")
    configure.add_argument("--timezone", required=True, help="IANA timezone name")
    configure.add_argument("--staging-root", required=True)
    configure.add_argument("--canonical-root", required=True)
    configure.set_defaults(handler=_configure)

    fetch_parser = subparsers.add_parser("fetch", help="retrieve and settle recordings")
    fetch_parser.add_argument("--remote", help="override configured rclone remote folder")
    fetch_parser.add_argument(
        "--settlement-seconds", type=int, default=600
    )
    fetch_parser.add_argument("--dry-run", action="store_true")
    fetch_parser.set_defaults(handler=_fetch)

    status_parser = subparsers.add_parser("status", help="show local intake state")
    status_parser.set_defaults(handler=_status)

    review = subparsers.add_parser("review", help="review and normalize settled chunks")
    review.add_argument("--date", help="review one local date (YYYY-MM-DD)")
    review.add_argument("--dry-run", action="store_true")
    review.set_defaults(handler=_review)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except IntakeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
