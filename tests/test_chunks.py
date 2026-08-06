from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from meetingintel_phone.chunks import (
    ReviewPlan,
    Segment,
    _automatic_groups,
    collect_review_sets,
    format_review,
    normalize,
    parse_partition,
    parse_start,
)
from meetingintel_phone.util import IntakeError, atomic_write_json


def segment(name: str, seconds: float, fingerprint: str) -> Segment:
    started = parse_start(name, "Asia/Kolkata")
    return Segment(
        object_id=fingerprint,
        source_path=Path("/staging") / name,
        relative_path=Path(name),
        started_at=started,
        duration_seconds=seconds,
        size=4,
        sha256=fingerprint,
    )


class ChunkTests(unittest.TestCase):
    def test_filename_timestamp(self) -> None:
        value = parse_start("2026_07_24_12_34_56.m4a", "Asia/Kolkata")
        self.assertEqual("2026-07-24T12:34:56+05:30", value.isoformat())

    def test_impossible_calendar_date_is_rejected(self) -> None:
        with self.assertRaises(IntakeError):
            parse_start("2026_02_30_12_00_00.m4a", "UTC")

    def test_malformed_time_is_rejected(self) -> None:
        with self.assertRaises(IntakeError):
            parse_start("2026_01_01_25_00_00.m4a", "UTC")

    def test_invalid_timezone_is_rejected(self) -> None:
        with self.assertRaises(IntakeError):
            parse_start("2026_01_01_12_00_00.m4a", "Not/AZone")

    def test_daylight_saving_transition_keeps_local_offsets(self) -> None:
        before = parse_start("2026_03_08_01_30_00.m4a", "America/New_York")
        after = parse_start("2026_03_08_03_30_00.m4a", "America/New_York")
        self.assertEqual("2026-03-08T01:30:00-05:00", before.isoformat())
        self.assertEqual("2026-03-08T03:30:00-04:00", after.isoformat())

    def test_three_chunks_form_one_safe_group(self) -> None:
        values = [
            segment("2026_07_24_12_00_00.m4a", 900.0, "a" * 64),
            segment("2026_07_24_12_15_01.m4a", 900.0, "b" * 64),
            segment("2026_07_24_12_30_02.m4a", 100.0, "c" * 64),
        ]
        groups, _reason = _automatic_groups(values)
        self.assertEqual(((1, 2, 3),), groups)

    def test_one_full_plus_tail_is_ambiguous(self) -> None:
        values = [
            segment("2026_07_24_12_00_00.m4a", 900.0, "a" * 64),
            segment("2026_07_24_12_15_01.m4a", 100.0, "b" * 64),
        ]
        groups, reason = _automatic_groups(values)
        self.assertIsNone(groups)
        self.assertIn("ambiguous", reason)

    def test_partition_requires_every_segment_once(self) -> None:
        self.assertEqual(((1, 2), (3,)), parse_partition("1,2;3", 3))
        with self.assertRaises(IntakeError):
            parse_partition("1;3", 3)
        with self.assertRaises(IntakeError):
            parse_partition("2;1;3", 3)

    def test_collect_uses_only_settled_unresolved_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "staging"
            runtime = root / "runtime"
            staging.mkdir()
            path = staging / "2026_07_24_12_00_00.m4a"
            path.write_bytes(b"test")
            digest = hashlib.sha256(b"test").hexdigest()
            atomic_write_json(
                runtime / "fetch-ledger.json",
                {
                    "schema_version": 1,
                    "records": {
                        "id": {
                            "status": "settled_ready",
                            "object_id": "id",
                            "relative_path": path.name,
                            "size": 4,
                            "sha256": digest,
                        }
                    },
                },
            )
            reviews = collect_review_sets(
                fetch_ledger_path=runtime / "fetch-ledger.json",
                staging_root=staging,
                normalize_ledger_path=runtime / "normalize-ledger.json",
                timezone_name="Asia/Kolkata",
                duration_probe=lambda _path: 90.0,
            )
            self.assertEqual(1, len(reviews))
            rendered = format_review(reviews[0])
            self.assertIn("12:00:00", rendered)
            self.assertIn("12:01:30", rendered)

    def test_singleton_normalization_retains_original_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "2026_07_24_12_00_00.m4a"
            source.write_bytes(b"synthetic-audio-placeholder")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            item = Segment(
                object_id="id",
                source_path=source,
                relative_path=Path(source.name),
                started_at=parse_start(source.name, "Asia/Kolkata"),
                duration_seconds=30.0,
                size=source.stat().st_size,
                sha256=digest,
            )
            review = type(
                "Review",
                (),
                {
                    "recording_date": item.started_at.date(),
                    "segments": (item,),
                },
            )()
            summary = normalize(
                review,
                ReviewPlan(groups=((1,),)),
                canonical_root=root / "canonical",
                normalize_ledger_path=root / "runtime" / "normalize-ledger.json",
            )
            self.assertEqual(1, summary.normalized)
            self.assertTrue(source.exists())
            targets = list((root / "canonical").iterdir())
            self.assertEqual(1, len(targets))
            metadata = json.loads(
                (targets[0] / "meeting_source.json").read_text(encoding="utf-8")
            )
            self.assertEqual([digest], metadata["source_sha256"])
            self.assertEqual(
                source.read_bytes(), (targets[0] / "audio.m4a").read_bytes()
            )

    def test_discard_records_decision_but_retains_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "2026_07_24_12_00_00.m4a"
            source.write_bytes(b"test")
            digest = hashlib.sha256(b"test").hexdigest()
            item = Segment(
                "id",
                source,
                Path(source.name),
                parse_start(source.name, "UTC"),
                2.0,
                4,
                digest,
            )
            review = type("Review", (), {"segments": (item,)})()
            summary = normalize(
                review,
                ReviewPlan(groups=(), discarded=(1,)),
                canonical_root=root / "canonical",
                normalize_ledger_path=root / "runtime" / "normalize-ledger.json",
            )
            self.assertEqual(1, summary.discarded)
            self.assertTrue(source.exists())

    def test_group_normalization_invokes_concat_and_records_both_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            segments = []
            for number, name in enumerate(
                [
                    "2026_07_24_12_00_00.m4a",
                    "2026_07_24_12_15_01.m4a",
                ],
                1,
            ):
                source = root / name
                source.write_bytes(f"part-{number}".encode())
                digest = hashlib.sha256(source.read_bytes()).hexdigest()
                segments.append(
                    Segment(
                        f"id-{number}",
                        source,
                        Path(name),
                        parse_start(name, "Asia/Kolkata"),
                        900.0,
                        source.stat().st_size,
                        digest,
                    )
                )
            review = type("Review", (), {"segments": tuple(segments)})()
            commands: list[list[str]] = []

            def runner(
                arguments: list[str], **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                commands.append(arguments)
                Path(arguments[-1]).write_bytes(b"joined")
                return subprocess.CompletedProcess(arguments, 0, "", "")

            with patch(
                "meetingintel_phone.chunks.shutil.which", return_value="/ffmpeg"
            ):
                summary = normalize(
                    review,
                    ReviewPlan(groups=((1, 2),)),
                    canonical_root=root / "canonical",
                    normalize_ledger_path=root / "runtime" / "normalize-ledger.json",
                    runner=runner,
                )
            self.assertEqual(1, summary.normalized)
            self.assertIn("-f", commands[0])
            target = next((root / "canonical").iterdir())
            metadata = json.loads(
                (target / "meeting_source.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [segment.sha256 for segment in segments],
                metadata["source_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
