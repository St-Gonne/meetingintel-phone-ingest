from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from meetingintel_phone.fetch import (
    FetchConfig,
    RemoteItem,
    _remote_file,
    fetch,
    parse_listing,
    status,
)
from meetingintel_phone.util import IntakeError, atomic_write_json


class FetchTests(unittest.TestCase):
    def test_listing_accepts_audio_and_requires_immutable_id(self) -> None:
        items = parse_listing(
            [
                {
                    "ID": "drive-1",
                    "Path": "2026/07/24/2026_07_24_10_00_00.m4a",
                    "Size": 4,
                    "ModTime": "2026-07-24T04:30:00Z",
                    "Hashes": {"MD5": "abcd"},
                },
                {
                    "ID": "text-1",
                    "Path": "notes.txt",
                    "Size": 2,
                    "ModTime": "2026-07-24T04:31:00Z",
                },
            ]
        )
        self.assertEqual(1, len(items))
        self.assertEqual("drive-1", items[0].object_id)

    def test_listing_rejects_missing_id_and_unsafe_path(self) -> None:
        with self.assertRaises(IntakeError):
            parse_listing(
                [
                    {
                        "Path": "recording.m4a",
                        "Size": 1,
                        "ModTime": "now",
                    }
                ]
            )
        with self.assertRaises(IntakeError):
            parse_listing(
                [
                    {
                        "ID": "x",
                        "Path": "../recording.m4a",
                        "Size": 1,
                        "ModTime": "now",
                    }
                ]
            )

    def test_remote_path_shape(self) -> None:
        self.assertEqual("drive:folder/file.m4a", _remote_file("drive:", Path("folder/file.m4a")))
        self.assertEqual(
            "drive:root/folder/file.m4a",
            _remote_file("drive:root", Path("folder/file.m4a")),
        )

    def test_dry_run_never_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            item = RemoteItem(
                "id-1", Path("2026/recording.m4a"), 4, "now", {}
            )
            summary = fetch(
                FetchConfig("drive:root", root / "staging", root / "runtime", dry_run=True),
                listing=[item],
                emit=lambda _line: None,
            )
            self.assertEqual(1, summary.downloaded)
            self.assertFalse((root / "runtime" / "fetch-ledger.json").exists())
            self.assertFalse((root / "staging").exists())

    def test_download_then_settle_after_unchanged_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = b"test"
            item = RemoteItem(
                "id-1",
                Path("2026/recording.m4a"),
                len(content),
                "2026-07-24T00:00:00Z",
                {"MD5": hashlib.md5(content).hexdigest()},
            )
            now = datetime(2026, 7, 24, tzinfo=timezone.utc)

            def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                Path(arguments[-1]).write_bytes(content)
                return subprocess.CompletedProcess(arguments, 0, "", "")

            config = FetchConfig(
                "drive:root",
                root / "staging",
                root / "runtime",
                settlement_seconds=600,
            )
            with patch("meetingintel_phone.fetch._rclone_binary", return_value="/rclone"):
                first = fetch(
                    config,
                    listing=[item],
                    runner=runner,
                    clock=lambda: now,
                    emit=lambda _line: None,
                )
                second = fetch(
                    config,
                    listing=[item],
                    runner=runner,
                    clock=lambda: now + timedelta(minutes=11),
                    emit=lambda _line: None,
                )
            self.assertEqual((1, 1), (first.downloaded, first.pending))
            self.assertEqual((1, 1), (second.unchanged, second.settled))
            self.assertEqual(1, status(config.ledger_path)["settled_ready"])

    def test_remote_path_move_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runtime.mkdir()
            item = RemoteItem("same-id", Path("new/file.m4a"), 4, "now", {})
            atomic_write_json(
                runtime / "fetch-ledger.json",
                {
                    "schema_version": 1,
                    "records": {
                        "same-id": {
                            "relative_path": "old/file.m4a",
                            "signature": "old",
                        }
                    },
                },
            )
            summary = fetch(
                FetchConfig("drive:root", root / "staging", runtime),
                listing=[item],
                emit=lambda _line: None,
            )
            self.assertEqual(1, summary.decisions)

    def test_changed_tracked_file_is_atomically_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "staging"
            runtime = root / "runtime"
            destination = staging / "recording.m4a"
            staging.mkdir()
            destination.write_bytes(b"old!")
            previous = RemoteItem("id-1", Path("recording.m4a"), 4, "old", {})
            atomic_write_json(
                runtime / "fetch-ledger.json",
                {
                    "schema_version": 1,
                    "records": {
                        "id-1": {
                            "relative_path": "recording.m4a",
                            "signature": previous.signature,
                            "sha256": hashlib.sha256(b"old!").hexdigest(),
                            "first_observed_at": "2026-07-24T00:00:00+00:00",
                        }
                    },
                },
            )
            current = RemoteItem("id-1", Path("recording.m4a"), 4, "new", {})

            def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                Path(arguments[-1]).write_bytes(b"new!")
                return subprocess.CompletedProcess(arguments, 0, "", "")

            with patch("meetingintel_phone.fetch._rclone_binary", return_value="/rclone"):
                summary = fetch(
                    FetchConfig("drive:root", staging, runtime),
                    listing=[current],
                    runner=runner,
                    clock=lambda: datetime(2026, 7, 24, tzinfo=timezone.utc),
                    emit=lambda _line: None,
                )
            self.assertEqual((1, 1), (summary.downloaded, summary.changed))
            self.assertEqual(b"new!", destination.read_bytes())


if __name__ == "__main__":
    unittest.main()
