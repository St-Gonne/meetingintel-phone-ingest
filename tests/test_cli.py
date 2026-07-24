from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from meetingintel_phone.cli import build_parser, main


class CliTests(unittest.TestCase):
    def test_commands_parse(self) -> None:
        parser = build_parser()
        self.assertEqual("fetch", parser.parse_args(["fetch", "--dry-run"]).command)
        self.assertEqual("status", parser.parse_args(["status"]).command)
        self.assertEqual("review", parser.parse_args(["review", "--dry-run"]).command)

    def test_configure_is_local_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = main(
                [
                    "--runtime-root",
                    str(root / "runtime"),
                    "configure",
                    "--remote",
                    "drive:recordings",
                    "--timezone",
                    "Asia/Kolkata",
                    "--staging-root",
                    str(root / "staging"),
                    "--canonical-root",
                    str(root / "canonical"),
                ]
            )
            self.assertEqual(0, result)
            self.assertTrue((root / "runtime" / "config.json").exists())


if __name__ == "__main__":
    unittest.main()
