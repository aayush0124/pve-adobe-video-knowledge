"""The owner's first name may appear in tracked files only as part of his full-name credit (LICENSE / README).

A bare first name in a card, sample or doc ties public content to his personal pages. The name is built from pieces
so this file does not contain it either.
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRST, LAST = "Aay" + "ush", "Pali" + "wal"
BARE = re.compile(r"\b" + FIRST + r"\b(?!\s+" + LAST + r")", re.I)
TEXT = {".py", ".js", ".jsx", ".json", ".jsonl", ".md", ".txt", ".yml", ".yaml", ".csv", ".html", ".toml", ".cfg"}


def tracked():
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, check=True).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.CalledProcessError) as e:
        raise unittest.SkipTest(f"not a git checkout ({e})")
    return [ROOT / p for p in out.split("\0") if p and Path(p).suffix.lower() in TEXT]


class FirstNameTest(unittest.TestCase):
    def test_rule(self):
        self.assertTrue(BARE.search(f"ask {FIRST} about it"))
        self.assertFalse(BARE.search(f"Copyright 2026 {FIRST} {LAST}"))

    def test_no_bare_first_name(self):
        hits = []
        for f in tracked():
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if BARE.search(line):
                    hits.append(f"{f.relative_to(ROOT)}:{n}")
        self.assertEqual([], hits)


if __name__ == "__main__":
    unittest.main()
