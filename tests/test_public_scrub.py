"""Public-repo gate: nothing tracked (or about to be tracked) may carry a machine path, a client / employer / episode
name, personal data, a credential, a vendor-owned or project file, or anything over 5 MB.

Walks `git ls-files --cached --others --exclude-standard`, i.e. every file a plain `git add -A` would commit.
The names the owner must never publish are stored as SHA-256 prefixes, so this file does not publish them either.
"""
import hashlib, re, subprocess, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 5 * 1024 * 1024
VENDOR_EXT = {".ffx", ".mogrt", ".cube", ".look", ".lut", ".prfpset", ".epr", ".abr", ".atn", ".grd", ".pat", ".asl",
              ".csh", ".aia", ".aep", ".aepx", ".prproj", ".prin", ".psd", ".ai", ".cdr", ".exe", ".dll", ".zip", ".onnx",
              ".mp4", ".mov", ".wav", ".mp3", ".png", ".jpg", ".jpeg"}
PATTERNS = {
    "Windows user-profile path": r"(?i)\b[a-z]:[\\/]{1,2}users[\\/]{1,2}(?!public\b)[^\\/\s\"'<>%*]+",
    "non-system drive folder": r"\b[D-Z]:[\\/]{1,2}[A-Za-z_][\w -]{2,}[\\/]",  # D:/t.aep in an example is fine
    "home directory path": r"(?<![\w.])/(?:home|Users)/(?!<)[A-Za-z][\w.-]*/",
    "temp / scratch path": r"(?i)appdata[\\/]{1,2}local[\\/]{1,2}temp|scratchpad",
    "client episode path": r"(?i)\bepisodes[\\/]{1,2}[a-z0-9_]",
    "personal email": r"(?i)[\w.+-]+@(?:gmail|yahoo|outlook|hotmail|icloud|proton(?:mail)?)\.[a-z]{2,}",
    "phone number": r"\+91[ -]?\d{5}[ -]?\d{5}\b",
    "GitHub token": r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    "API key": r"\b(?:sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|xox[abprs]-[A-Za-z0-9-]{10,})",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}
# sha256(word)[:16] of client, employer, party and episode names and the owner's personal handles
NAME_HASHES = {"c9c61db581934f1d", "1c2d1d71cee24643", "9402bbacd28022cd", "8b719d108e8a63a9", "44c8cf4ce0611762",
               "0a16bea95629a2ec", "e75eb6e2d3fb49fb", "3db20e1df89a35ea", "ea9724e2e43fd7fd", "4fddbb2dab7dc1da",
               "93080e35013dc52a", "678ed88a253ce834", "918a4aba37371d84", "72fa5fff8ed9b186"}
# What the original (client) gallery plates showed, split so this file does not spell it. Card text may say "plate A /
# plate B" and describe tone, edges or motion, never who or what is in the footage. A FOOTAGE word is banned only within
# 120 characters of a plate / gallery / frame / render reference on the same line, so generic use-cases ("slow motion
# on a handshake", "speaker ID in a press-conference clip") stay legal; the snow-soldier label is banned anywhere.
FOOTAGE = re.compile("|".join(("hand" + "shak", "sol" + "dier", r"press[- ]" + "conference", "lec" + "tern",
                               "pod" + "ium", r"\b(?:US|Indian) " + "flag", "ash" + "oka", "cha" + "kra")), re.I)
FOOTAGE_ALWAYS = re.compile(r"snow[- ]?" + "sol" + "dier", re.I)
FOOTAGE_CONTEXT = re.compile(r"\bplates?\b|gallery|\bframes?\b|render|test[ _](?:clip|footage|photo)|test_[ab]\b"
                             r"|\bclips? [AB]\b|\b[AB]=", re.I)


def footage_hits(text):
    """Footage-identifying phrases in text: the always-banned label, or a FOOTAGE word next to a plate reference."""
    out = []
    for line in text.splitlines():
        out += [line[max(0, m.start() - 40):m.end() + 40] for m in FOOTAGE_ALWAYS.finditer(line)]
        for m in FOOTAGE.finditer(line):
            near = line[max(0, m.start() - 120):m.end() + 120]
            if FOOTAGE_CONTEXT.search(near):
                out.append(near)
    return out


def would_be_tracked():
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        raise unittest.SkipTest(f"not a git checkout ({e})")
    return [ROOT / p for p in out.decode("utf-8").split("\0") if p and (ROOT / p).is_file()]


class PublicScrub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = would_be_tracked()

    def test_tracked_set_is_sane(self):
        rel = {f.relative_to(ROOT).as_posix() for f in self.files}
        self.assertIn("adobe_bridge.py", rel)
        self.assertIn("pve/pve.py", rel)
        self.assertFalse([r for r in rel if r.startswith(("episodes/", "pve/gallery/", "pve/media/"))])

    def test_no_large_vendor_or_media_files(self):
        big = [f"{f.relative_to(ROOT)} ({f.stat().st_size} B)" for f in self.files if f.stat().st_size > MAX_BYTES]
        vendor = [str(f.relative_to(ROOT)) for f in self.files if f.suffix.lower() in VENDOR_EXT]
        self.assertEqual(big, [], "files over 5 MB")
        self.assertEqual(vendor, [], "vendor, project or media files")

    def test_no_forbidden_content(self):
        rx = {k: re.compile(v) for k, v in PATTERNS.items()}
        hits = []
        for f in self.files:
            if f.resolve() == Path(__file__).resolve():
                continue
            text = f.read_bytes().decode("utf-8", "replace")
            for what, r in rx.items():
                m = r.search(text)
                if m:
                    hits.append(f"{f.relative_to(ROOT)}: {what}: {text[max(0, m.start() - 30):m.end() + 10]!r}")
            for tok in set(re.findall(r"[a-z0-9_]+", text.lower())):
                if hashlib.sha256(tok.encode()).hexdigest()[:16] in NAME_HASHES:
                    hits.append(f"{f.relative_to(ROOT)}: a never-publish name ({tok[:2]}...)")
        self.assertEqual(hits, [], "\n" + "\n".join(hits[:50]))

    def test_no_footage_descriptions(self):
        hits = [f"{f.relative_to(ROOT)}: {h.strip()!r}" for f in self.files if f.resolve() != Path(__file__).resolve()
                for h in footage_hits(f.read_bytes().decode("utf-8", "replace"))]
        self.assertEqual(hits, [], "\n" + "\n".join(hits[:50]))

    def test_footage_rule_is_narrow(self):
        label = "Gallery (A=press-" + "conference clip, B=snow-" + "soldiers clip)"
        self.assertTrue(footage_hits(label))
        self.assertTrue(footage_hits("a 2x crop-in on the hand" + "shake in the gallery frame"))
        self.assertFalse(footage_hits("smooth slow motion on a key moment (hand" + "shake, crowd wave, a goal)"))
        self.assertFalse(footage_hits("speaker ID in a rally or press-" + "conference clip"))


if __name__ == "__main__":
    unittest.main()
