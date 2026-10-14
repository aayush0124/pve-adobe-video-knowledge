"""pve/pve.py: every CLI subcommand (exit code + output shape) and golden queries."""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pve(*args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")  # cards hold curly quotes; a Windows pipe defaults to cp1252
    r = subprocess.run([sys.executable, "pve/pve.py", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       env=env)
    return r.returncode, r.stdout, r.stderr


class Cli(unittest.TestCase):
    def test_no_args_prints_usage(self):
        rc, out, _ = pve()
        self.assertEqual(rc, 0)
        for sub in ("find", "card", "check", "scenes", "stats"):
            self.assertIn(f"pve/pve.py {sub}", out)

    def test_find_shape_and_golden(self):
        rc, out, _ = pve("find", "film grain")
        lines = out.splitlines()
        self.assertEqual(rc, 0)
        self.assertTrue(0 < len(lines) <= 25)
        self.assertTrue(all(l.startswith("[") and "] " in l for l in lines), lines[:3])
        self.assertTrue(any("Grain" in l.split(":")[0] for l in lines[:5]), lines[:5])  # a grain card ranks near the top
        _, out, _ = pve("find", "camera shake")
        self.assertIn("Camera Shake", out)
        _, out, _ = pve("find", "cross dissolve")
        self.assertIn("Cross Dissolve", out)

    def test_find_nothing(self):
        rc, out, _ = pve("find", "zzqx_no_such_word")
        self.assertEqual((rc, out.strip()), (0, ""))

    def test_card(self):
        rc, out, _ = pve("card", "Camera Shake", "PR")
        c = json.loads(out)
        self.assertEqual((rc, c["name"], c["app"]), (0, "Camera Shake", "PR"))
        for f in ("kind", "status", "summary", "use_for", "confidence"):
            self.assertIn(f, c)
        rc, out, _ = pve("card", "ADBE Gaussian Blur 2")  # by match name
        self.assertEqual(json.loads(out)["app"], "AE")
        rc, out, _ = pve("card", "No Such Card")
        self.assertEqual((rc, out.strip()), (0, "null"))

    def test_check_clean_and_flagged(self):
        rc, out, _ = pve("check", "example/spec.json")
        self.assertEqual((rc, out.strip()), (0, "OK: nothing PVE objects to"))
        rc, out, _ = pve("check", "example/trans_test.json")
        self.assertEqual(rc, 1)
        self.assertIn("no Premiere transition 'Bogus Wipe'", out)
        rc, out, err = pve("check", "example/fx_test.json")  # bare-name effects ("Black & White") used to crash it
        self.assertEqual(rc, 1, err)
        self.assertIn("no Premiere effect 'Nonexistent FX'", out)
        self.assertIn("Tint has no property 'Bogus'", out)

    def test_scenes(self):
        scenes = {"scenes": [{"name": "S1", "layers": [
            {"type": "solid", "name": "BG", "effects": [{"name": "ADBE Gaussian Blur 2", "props": {"Blurriness": 20}}]}]}]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "scenes.json")
            p.write_text(json.dumps(scenes), encoding="utf-8")
            rc, out, _ = pve("scenes", str(p))
            self.assertEqual((rc, out.strip()), (0, "OK: nothing PVE objects to"))
            scenes["scenes"][0]["layers"][0]["effects"].append({"name": "ADBE Nope"})
            p.write_text(json.dumps(scenes["scenes"]), encoding="utf-8")  # a bare list works too
            rc, out, _ = pve("scenes", str(p))
            self.assertEqual(rc, 1)
            self.assertIn("S1/BG: no After Effects effect 'ADBE Nope'", out)

    def test_stats(self):
        rc, out, _ = pve("stats")
        s = json.loads(out)
        self.assertEqual(rc, 0)
        self.assertEqual(set(s), {"cards", "ae_effects_harvested", "pr_effects_harvested", "pr_transitions", "verdicts"})
        self.assertIn("pass", s["verdicts"])
        self.assertGreater(s["ae_effects_harvested"], 400)
        self.assertGreater(sum(s["cards"].values()), 2000)


    def test_card_warns_when_several_match(self):
        rc, out, err = pve("card", "Spin In")  # two different presets share the name
        self.assertEqual((rc, json.loads(out)["name"]), (0, "Spin In"))
        self.assertIn("WARNING: 2 cards match 'Spin In'", err)
        self.assertIn("Text/Rotation/Spin In", err)
        rc, out, err = pve("card", "Text/Rotation/Spin In")  # the way out the warning names
        self.assertEqual((json.loads(out)["category"], err.strip()), ("Text/Rotation", ""))
        _, _, err = pve("card", "Camera Shake", "PR")
        self.assertEqual(err.strip(), "")
        _, _, err = pve("card", "Transform")  # AE current + AE obsolete + the merged PR card
        self.assertIn("3 cards match", err)

    def test_card_spells_out_its_verdict(self):
        _, out, _ = pve("card", "ADBE SubspaceStabilizer")  # Warp Stabilizer, by match name
        v = json.loads(out)["verified"]
        self.assertEqual(v["verdict"], "untested:needs_analysis")
        self.assertIn("background analysis", v["meaning"])
        self.assertTrue(v["previous"][0]["verdict"] == "broken")
        _, out, _ = pve("card", "VR Blur", "AE")  # re-judged on a GPU render: the old verdicts stay in previous
        v = json.loads(out)["verified"]
        self.assertEqual([p["verdict"] for p in v["previous"]] + [v["verdict"]], ["broken", "untested:gpu_only", "pass"])

    def test_find_verdict_filter(self):
        _, out, _ = pve("find", "blur", "--verdict", "untested")
        names = [l.split("] ", 1)[1].split(":")[0] for l in out.splitlines()]
        self.assertIn("Interlace combing and line twitter fix", names)  # untested:needs_setup (live wave 3)
        self.assertNotIn("VR Blur", names)
        self.assertNotIn("Camera-Shake Deblur", names)  # judged weak on the second footage set
        _, out, _ = pve("find", "deblur", "--verdict", "weak")
        self.assertIn("Camera-Shake Deblur", out)
        _, out, _ = pve("find", "blur", "--verdict", "pass", "--app", "AE")
        self.assertTrue(out and all(l.startswith("[AE ") for l in out.splitlines()), out)
        self.assertIn("VR Blur:", out)

    def test_check_exit_code_ignores_advisories(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "spec.json")
            p.write_text(json.dumps({"fps": 25, "clips": [{"src": "x.mp4", "transition": "Flicker"},  # 2nd clip has no
                                                          {"src": "y.mp4", "transition": "Flicker"}]}),  # known start
                         encoding="utf-8")
            rc, out, _ = pve("check", str(p))
            self.assertEqual(rc, 0, out)
            self.assertTrue(out.startswith("check by eye"), out)
            p.write_text(json.dumps({"fps": 25, "clips": [{"src": "x.mp4", "effects": [
                {"name": "Strobe Light", "pr_props": {"Strobe Period (secs)": 0.2, "Strobe Duration (secs)": 0.1}}]}]}),
                         encoding="utf-8")
            rc, out, _ = pve("check", str(p))
            self.assertEqual(rc, 1, out)
            self.assertIn("flashes about 5 times a second", out)


if __name__ == "__main__":
    unittest.main()
