"""Luma-scan flash counter (pve/flash_scan.py) and the gallery re-render helpers, offline.

The counter is checked on synthetic luma series and, when ffmpeg is installed, on a synthetic flashing clip.
"""
import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pve"))
import flash_scan as fs  # noqa: E402
import gallery  # noqa: E402


def strobe(n=100, every=10, at=5, lo=16, hi=235, width=1):
    """A 25 fps luma series with a `width`-frame white flash every `every` frames."""
    return [hi if (i - at) % every < width and i >= at else lo for i in range(n)]


class Counter(unittest.TestCase):
    def test_one_frame_strobe(self):
        ch = fs.changes(strobe())
        self.assertEqual(len(ch), 20)
        self.assertEqual(ch[:2], [(5, 1), (6, -1)])
        self.assertEqual(fs.rate(ch, 25, 100), {"flashes": 10, "max_per_second": 3, "mean_per_second": 2.5})

    def test_noise_and_drift_are_not_flashes(self):
        self.assertEqual(fs.changes([100 + (5 if i % 2 else -5) for i in range(100)]), [])
        ramp = fs.changes([16 + i for i in range(120)])  # one slow rise, never back: no opposing pair
        self.assertEqual(len(ramp), 1)
        self.assertEqual(fs.rate(ramp, 25, 120)["flashes"], 0)

    def test_dips_count_like_flashes(self):
        dips = [200 - 150 * (i % 8 == 3) for i in range(50)]  # dark blinks on a bright layer
        self.assertEqual(fs.rate(fs.changes(dips), 25, 50)["flashes"], 6)

    def test_runs(self):
        self.assertEqual(fs.runs(strobe(30, width=3), 100), [(5, 3), (15, 3), (25, 3)])

    def test_signal_against_a_reference(self):
        plate = [bytes([40 + i % 7] * 12) for i in range(10)]  # the plate changes on its own
        fx = [bytes([255] * 12) if i == 4 else p for i, p in enumerate(plate)]
        sig, area = fs.signal(fx, plate)
        self.assertEqual(area, 1.0)
        self.assertEqual([i for i, v in enumerate(sig) if v], [4])
        self.assertEqual(fs.signal(plate, plate), ([0.0] * 10, 0.0))

    def test_a_chase_shows_in_cells_not_in_the_mean(self):
        w, h = fs.W, fs.H  # left half and right half swap bright / dark every 2 frames: the mean never moves
        half = lambda left: bytes((235 if (x < w // 2) == left else 16) for _ in range(h) for x in range(w))  # noqa: E731
        fr = [half(i // 2 % 2 == 0) for i in range(50)]
        sig, area = fs.signal(fr)
        self.assertEqual((fs.changes(sig), area), ([], 1.0))
        loc = fs.local(fr, None, 25)
        self.assertEqual(loc["local_max_per_second"], 6)  # 12 changes in any 25 frames
        self.assertAlmostEqual(loc["local_cell_area"], 1 / (fs.GRID[0] * fs.GRID[1]), places=4)

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_synthetic_clip(self):
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d) / "strobe.avi"  # lossless, so the decoded levels are the generated ones
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "color=c=black:s=96x54:r=25:d=4,format=gray,geq=lum='if(eq(mod(N\\,10)\\,5)\\,235\\,16)'",
                            "-c:v", "ffv1", str(clip)], check=True)
            res = fs.scan(clip, 25)
            self.assertEqual((res["frames"], res["flashes"], res["max_per_second"]), (100, 10, 3))
            self.assertEqual(res["area"], 1.0)


class GalleryHelpers(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)
        self.out = self.d / "gallery" / "presets"
        (self.out / "renders").mkdir(parents=True)
        self.pre = gallery.PRE_FIX
        gallery.PRE_FIX = self.d / "pre_fix"
        self.addCleanup(setattr, gallery, "PRE_FIX", self.pre)

    def test_which_clip_a_frame_shows(self):
        a, b = bytes([20] * 8), bytes([200] * 8)
        self.assertEqual([gallery.which(bytes([v] * 8), a, b) for v in (22, 198, 110)], ["A", "B", "m"])

    def test_index_keeps_rows_that_left_the_todo(self):
        (self.out / "index.json").write_text(json.dumps([{"k": 0, "name": "a"}, {"k": 1, "name": "gone"}]))
        gallery.write_index(self.out, [{"k": 0, "name": "a"}, {"k": 2, "name": "new"}])
        self.assertEqual([r["name"] for r in json.loads((self.out / "index.json").read_text())], ["a", "gone", "new"])

    def test_only_retires_old_frames_and_retries_failures(self):
        for f in ("007a.png", "007b.png", "008a.png", "renders/G007.mp4"):
            (self.out / f).write_text("old")
        failed = {"x.ffx": "x"}
        (self.out / "failed.json").write_text(json.dumps(failed))
        todo = [(7, {"file": "x.ffx"}), (8, {"file": "y.ffx"})]
        got = gallery.picked(todo, lambda p: p["file"], {"x.ffx"}, self.out, ["a", "b"], failed)
        self.assertEqual(got, [(7, {"file": "x.ffx"})])
        self.assertFalse((self.out / "007a.png").exists() or (self.out / "renders" / "G007.mp4").exists())
        self.assertTrue((self.out / "008a.png").exists())
        kept = gallery.PRE_FIX / "presets"
        self.assertEqual(sorted(p.relative_to(kept).as_posix() for p in kept.rglob("*.*")),
                         ["007a.png", "007b.png", "renders/G007.mp4"])
        self.assertEqual(json.loads((self.out / "failed.json").read_text()), {})
        (self.out / "007a.png").write_text("new")  # a second re-render keeps both older copies
        gallery.retire(self.out, [7], ["a"])
        self.assertTrue((kept / "007a_v2.png").exists())
        self.assertIs(gallery.picked(todo, lambda p: p["file"], None, self.out, ["a"], {}), todo)


if __name__ == "__main__":
    unittest.main()
