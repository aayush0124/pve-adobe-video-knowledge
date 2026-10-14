"""AdobeBridge engine, offline: the spec logic (normalize + transition planning) with a stubbed ffprobe, the original
test scripts (test_adobe_bridge.py, test_ae_lib.js) when their inputs exist, imports and install-path overrides."""
import os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import adobe_bridge as ab  # noqa: E402

# what ffprobe reports for the example media (make_test_media.py writes exactly these)
PROBE = {"still.png": (1080, 1350, 0.0), "clip_a.mp4": (1920, 1080, 6.0), "clip_b.mp4": (1280, 720, 6.0),
         "music.mp3": (None, None, 12.0)}


class Normalize(unittest.TestCase):
    """The assertions of test_adobe_bridge.py, runnable anywhere (no ffprobe, no After Effects presets)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for n in PROBE:
            (self.tmp / n).write_bytes(b"")
        preset = self.tmp / "presets" / "Text" / "Animate In" / "Typewriter.ffx"
        preset.parent.mkdir(parents=True)
        preset.write_bytes(b"")
        self.patches = [mock.patch.object(ab, "probe", lambda src: PROBE[Path(src).name]),
                        mock.patch.object(ab, "PRESETS", self.tmp / "presets")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def norm(self, spec):
        return ab.normalize(spec, self.tmp)

    def exits(self, spec, needle):
        with self.assertRaises(SystemExit) as cm:
            self.norm(spec)
        self.assertIn(needle, str(cm.exception))

    def test_snap_chain_cover_fit_music_text(self):
        base = {"width": 1080, "height": 1920, "fps": 25}
        S = self.norm({**base, "clips": [{"src": "still.png", "dur": 2}, {"src": "clip_b.mp4", "start": 2.5, "dur": 1.01},
                                         {"src": "clip_a.mp4"}],
                       "audio": [{"src": "music.mp3"}],
                       "texts": [{"text": "\u0939\u093f", "start": 0.33, "dur": 1, "presets": ["Text/Animate In/Typewriter.ffx"]}]})
        c1, c2, c3 = S["clips"]
        self.assertEqual((c2["start"], c2["dur"]), (2.48, 1.0))                    # snapped to the 25 fps grid
        self.assertEqual((c3["start"], c3["dur"]), (c2["start"] + c2["dur"], 6.0))  # chained, runs to its last frame
        self.assertEqual(c1["scale"], round(100 * max(1080 / 1080, 1920 / 1350), 3))  # cover-fit
        self.assertEqual(S["audio"][0]["dur"], c3["start"] + c3["dur"])             # music stops with the picture
        t = S["texts"][0]
        self.assertEqual((t["start"], t["font"], t["indic"], t["anim"]), (0.32, "NirmalaUI-Bold", True, False))
        self.assertTrue(t["presets"][0]["file"].endswith("Typewriter.ffx"))
        self.assertEqual(S["duration"], c3["start"] + c3["dur"])

    def test_effects_default_premiere_side(self):
        fx = self.norm({"width": 1080, "height": 1920, "fps": 25, "clips": [
            {"src": "still.png", "effects": ["Tint", {"name": "Gaussian Blur", "props": {"Blurriness": 5}}]}]})
        e1, e2 = fx["clips"][0]["effects"]
        self.assertEqual(e1, {"name": "Tint", "props": {}, "pr_name": "Tint", "pr_props": {}})
        self.assertEqual(e2["pr_props"], {"Blurriness": 5})

    def test_transition_windows(self):
        f, near = 1 / 30, (lambda a, b: abs(a - b) < 1e-6)
        T = self.norm({"width": 1080, "height": 1920, "fps": 30, "clips": [
            {"src": "clip_a.mp4", "in": 1, "dur": 2, "transition": "Dip to Black"},
            {"src": "still.png", "dur": 2, "transition": "Cross Dissolve"},
            {"src": "clip_b.mp4", "in": 0.1, "dur": 2, "transition": "Cross Dissolve (Legacy)"},
            {"src": "still.png", "dur": 1, "transition_out": {"name": "Dip to White", "dur": 0.8}}]})
        c1, c2, c3, c4 = T["clips"]
        self.assertTrue(near(c1["trans"][0]["t0"], 0) and near(c1["trans"][0]["t1"], 15 * f))
        self.assertTrue(near(c2["trans"][0]["t0"], 2 - 7 * f) and near(c2["trans"][0]["t1"], 2 + 8 * f))
        self.assertEqual(c2["opacity"], [[c2["trans"][0]["t0"], 0, c2["trans"][0]["t1"], 100, "smooth"]])
        self.assertTrue(near(c2["ae_in"], 2 - 7 * f) and near(c1["ae_out"], 2 + 8 * f))
        self.assertTrue(near(c3["trans"][0]["t0"], 4 - 3 * f) and c3["opacity"][0][4] == "lin")
        self.assertTrue(any("only 11 of 15 frames" in w for w in T["pywarn"]), T["pywarn"])
        self.assertTrue(near(c4["trans"][0]["t0"], 6.2) and c4["solids"][0]["above"])

    def test_bad_specs_exit(self):
        base = {"width": 1080, "height": 1920, "fps": 30}
        self.exits({**base, "clips": [{"src": "still.png", "transition_out": "Cross Dissolve"}, {"src": "still.png"}]},
                   "follows directly")
        self.exits({**base, "clips": []}, "at least one clip")
        self.exits({**base, "clips": [{"src": "nope.mp4"}]}, "missing source")
        self.exits({**base, "clips": [{"src": "still.png", "presets": ["Text/Nope.ffx"]}]}, "preset not found")

    def test_preflight_is_advisory(self):
        issues = ab._pve_check("check_spec", {"clips": [{"src": "x", "effects": ["No Such Effect"]}]})
        self.assertIsInstance(issues, list)
        self.assertFalse(any("unavailable" in i for i in issues), issues)


class InstallPaths(unittest.TestCase):
    def run_py(self, code, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith("PVE_")}
        e.update(env)
        return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=e, capture_output=True, text=True, check=True).stdout

    def test_defaults_resolve_as_before(self):
        out = self.run_py("import adobe_bridge as a; print(a.AFTERFX); print(a.AERENDER); print(a.PRESETS); print(a.PREMIERE)")
        ae = Path(r"C:\Program Files\Adobe") / "Adobe After Effects 2026/Support Files"
        self.assertEqual(out.splitlines(), [str(ae / "AfterFX.exe"), str(ae / "aerender.exe"), str(ae / "Presets"),
                                            str(Path(r"C:\Program Files\Adobe") / "Adobe Premiere Pro 2026/Adobe Premiere Pro.exe")])

    def test_env_overrides(self):
        exe = str(Path("/opt/ae/Support Files/AfterFX.exe"))
        out = self.run_py("import adobe_bridge as a; print(a.AERENDER); print(a.PRESETS); print(a.PREMIERE)",
                          PVE_AFTERFX=exe, PVE_PREMIERE="pr.exe")
        self.assertEqual(out.splitlines(), [str(Path(exe).with_name("aerender.exe")), str(Path(exe).parent / "Presets"), "pr.exe"])

    def test_engine_imports(self):
        self.assertIn("ok", self.run_py("import adobe_bridge, vfx; print('ok')"))


class AeNotes(unittest.TestCase):
    """AB_ERROR / AB_WARN comp names read back from a saved .aep (UTF-8 in the file)."""

    def test_unicode_notes_survive(self):
        warn = "Effect Glow: Can not “set value” with this property, because it is hidden"
        err = "Unable to apply ‘Strobe Light’ (हि) line 12"
        aep = (b"RIFX\x00\x00\x10\x00Utf8\x00\x00\x00\x40" + f"AB_WARN {warn} ~".encode()
               + b"\x00\x00LIST\xff\xfe" + f"AB_ERROR {err} ~".encode() + b"\x00Utf8" + f"AB_WARN {warn} ~".encode())
        self.assertEqual(ab.ae_notes(aep), [f"WARN {warn}", f"ERROR {err}"])  # in order, once each

    def test_binary_is_not_swallowed(self):
        # a note cut short by binary (no " ~" before the junk) must not run on into the next note
        aep = b"AB_WARN broken \x00\xff\xfe junk AB_WARN plain ascii ~ AB_ERROR bad \xe2\x80 byte ~"
        self.assertEqual(ab.ae_notes(aep), ["WARN plain ascii"])

    def test_every_reader_uses_it(self):
        """build(), vfx.render_scenes and make_house_mogrts read notes through ae_notes, not an ASCII-only pattern."""
        for f in ("adobe_bridge.py", "vfx.py", "make_house_mogrts.py", "pve/live_tests.py"):
            src = (ROOT / f).read_text(encoding="utf-8")
            self.assertNotIn("[ -}]{1,200}", src, f)
            self.assertIn("ae_notes(", src, f)


class OriginalScripts(unittest.TestCase):
    """The repo's original test scripts, unchanged. They need their real inputs, so they skip where those are missing."""

    def test_test_adobe_bridge_py(self):
        need = [ROOT / "example" / n for n in PROBE] + [ab.PRESETS / "Text/Animate In/Typewriter.ffx"]
        if not shutil.which("ffprobe") or not all(p.exists() for p in need):
            self.skipTest("needs ffprobe, the example media (python make_test_media.py) and the After Effects presets")
        r = subprocess.run([sys.executable, "test_adobe_bridge.py"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok"), r.stderr[-2000:])

    def test_test_ae_lib_js(self):
        if not shutil.which("node"):
            self.skipTest("needs node")
        r = subprocess.run(["node", "test_ae_lib.js"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertRegex(r.stdout, r"^ok: \d+ properties across \d+ AE effects")


if __name__ == "__main__":
    unittest.main()
