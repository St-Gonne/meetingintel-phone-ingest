from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from meetingintel_phone.util import IntakeError, RunLock, atomic_write_json


class UtilTests(unittest.TestCase):
    def test_atomic_json_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private" / "ledger.json"
            atomic_write_json(path, {"ok": True})
            self.assertEqual({"ok": True}, json.loads(path.read_text()))
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual(0o700, path.parent.stat().st_mode & 0o777)

    def test_live_lock_blocks_second_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fetch.lock"
            with RunLock(path):
                with self.assertRaises(IntakeError):
                    with RunLock(path):
                        pass
            self.assertFalse(path.exists())

    def test_dead_stale_lock_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "fetch.lock"
            created = datetime(2026, 7, 24, tzinfo=timezone.utc)
            path.write_text(
                json.dumps({"pid": 999999, "created_at": created.isoformat()}),
                encoding="utf-8",
            )
            with patch.object(RunLock, "_owner_is_alive", return_value=False):
                with RunLock(
                    path,
                    now=lambda: created + timedelta(hours=2),
                    stale_seconds=3600,
                ):
                    self.assertTrue(path.exists())
            self.assertFalse(path.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_lock_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("x", encoding="utf-8")
            link = root / "fetch.lock"
            link.symlink_to(target)
            with self.assertRaises(IntakeError):
                with RunLock(link):
                    pass


if __name__ == "__main__":
    unittest.main()
