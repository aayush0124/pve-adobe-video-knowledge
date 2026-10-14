"""The live wave 2 gallery code, offline: the After Effects recipe pass and its repeated-effect check, the spec /
scenes gallery builders and its measurements (probe dots, ducking levels, scroll speed, AE vs Premiere), the text
gallery's specs and in / out timing, and the new synthetic plates. No app, network or LLM; ffmpeg only where noted."""
import copy, json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pve"))
import gallery as g  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class AfterEffectsPass(unittest.TestCase):
    def test_recipe_scene_mirrors_the_premiere_clip(self):
        c = {"after_effects": [{"name": "ADBE Wave Warp", "props": {"Wave Height": [[0, 30], [-0.2, 0]]}}] * 2}
        s = g.recipe_scene(c, 7)
        L = s["layers"][0]
        self.assertEqual((s["name"], s["dur"], L["in"], L["t0"], L["t1"]), ("G007", 2.0, 1.0, 0, 2.0))
        self.assertEqual(L["effects"], c["after_effects"])
        L["effects"][0]["props"]["Wave Height"] = 1  # a copy: the card is never touched
        self.assertNotEqual(c["after_effects"][0]["props"]["Wave Height"], 1)

    def test_second_instance_scenes_repeat_one_effect(self):
        for s in g.second_instance_scenes():
            fx = s["layers"][0]["effects"]
            self.assertGreaterEqual(len(fx), 2)
            self.assertEqual(len({e["name"] for e in fx}), 1)
            self.assertEqual(len({json.dumps(e["props"]) for e in fx}), len(fx))  # every instance distinguishable

    def test_every_subset_of_the_shifts_lands_elsewhere(self):
        from itertools import combinations
        sums = [tuple(map(sum, zip(*c))) if c else (0, 0) for n in range(len(g.SHIFTS) + 1)
                for c in combinations(g.SHIFTS, n)]
        self.assertEqual(len(set(sums)), len(sums))

    def test_second_instance_check_tells_kept_values_from_defaults(self):
        dx, dy = map(sum, zip(*g.SHIFTS))
        good = g.second_instance_check([0, 0, 120], [960 + dx, 540 + dy])
        self.assertTrue(all(r["ok"] for r in good.values()), good)
        self.assertEqual(good["FILL2"]["expected_rgb"], [0, 0, 128])
        bad = g.second_instance_check([255, 0, 0], [960 + dx - g.SHIFTS[-1][0], 540 + dy])  # Fill default; last shift lost
        self.assertFalse(any(r["ok"] for r in bad.values()))
        self.assertFalse(any(r["ok"] for r in g.second_instance_check(None, None).values()))


def canvas(w, h, squares):
    """rgb24 frame with filled squares {colour: (cx, cy, size)} on black."""
    rgb = {"red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255)}
    f = bytearray(w * h * 3)
    for c, (cx, cy, s) in squares.items():
        for y in range(round(cy - s / 2), round(cy + s / 2)):
            for x in range(round(cx - s / 2), round(cx + s / 2)):
                f[(y * w + x) * 3:(y * w + x) * 3 + 3] = bytes(rgb[c])
    return bytes(f)


class SpecGallery(unittest.TestCase):
    def test_probe_dots_give_back_the_transform_position(self):
        # a Transform effect (anchor = the probe's centre A) with Position P, on a layer centred in a 960x540 frame at 40%:
        # source point q lands at (q - A + P - A) * s from the frame centre
        w, h, s, P, A = 960, 540, 0.4, (540, 600), (540, 675)
        at = lambda x, y: (w / 2 + (x - 2 * A[0] + P[0]) * s, h / 2 + (y - 2 * A[1] + P[1]) * s)  # noqa: E731
        sq = {c: (*at(*xy), 30 * s) for c, xy in g.PROBE_DOTS.items()}
        r = g.transform_reading(g.dots(canvas(w, h, sq), w), frame=(w, h))
        self.assertAlmostEqual(r["position_src_px"][0], 540, delta=2)
        self.assertAlmostEqual(r["position_src_px"][1], 600, delta=2)
        self.assertAlmostEqual(r["scale_pct"][1], 40, delta=1)
        self.assertIn("error", g.transform_reading({"red": [1, 1], "green": None, "blue": [0, 0]}))

    def test_fraction_readings(self):
        self.assertEqual(g.fraction_readings([0.5, 0.4444]),
                         {"of_clip_frame": [540.0, 599.9], "of_sequence_frame": [960.0, 480.0]})

    def test_duck_regions(self):
        lv = [-6.0] * 10 + [-18.0] * 40 + [-6.0] * 20  # 0.1 s windows; VO 1.0-5.0 s
        r = g.duck_regions(lv, [(1.0, 5.0)])
        self.assertEqual((r["under_vo"]["mean_db"], r["clear"]["mean_db"], r["duck_db"]), (-18.0, -6.0, 12.0))

    def test_scroll_speed(self):
        base = [0] * 40 + [200] * 6 + [0] * 30 + [120] * 4 + [0] * 120
        prof = [bytes(base[4 * i:] + [0] * (4 * i)) for i in range(10)]  # moves 4 px toward 0 every frame
        m = g.motion_speed(prof, 0, 12)
        self.assertEqual((m["median_px_per_frame"], m["min"], m["max"], m["pairs"]), (4, 4, 4, 9))
        self.assertIsNone(g.motion_speed([bytes(50)] * 3, 0, 12))  # nothing on screen

    def test_spec_builds_add_the_measurement_variants(self):
        rs = {c["name"]: c for c in g.spec_recipes()}
        self.assertEqual(g.spec_recipes()[0]["name"], g.GAP_ORDER[0])
        kb = g.spec_builds(rs["Ken Burns pan and zoom on a still"], Path("/probe.png"))
        self.assertEqual([t for t, _, _ in kb], ["main", "probe"])
        self.assertTrue(all(ae for _, _, ae in kb))
        self.assertEqual(kb[1][1]["clips"][0]["src"], "/probe.png")
        self.assertEqual(rs["Ken Burns pan and zoom on a still"]["spec"]["clips"][0]["src"], "still.png")  # untouched
        duck = g.spec_builds(rs["Music ducks under VO (scripted, from VO ranges)"], Path("/p.png"))
        self.assertEqual([(t, ae) for t, _, ae in duck], [("main", False), ("music_only", False)])
        self.assertFalse([a for a in duck[1][1]["audio"] if "test_vo" in a["src"]])
        self.assertTrue([a for a in duck[0][1]["audio"] if "test_vo" in a["src"]])

    def test_spec_scenes_resolve_footage_and_add_the_full_key_stack(self):
        rs = {c["name"]: c for c in g.spec_recipes()}
        sc = g.spec_scenes(rs["2.5D photo parallax (two planes)"], 23)
        self.assertEqual(sc[0]["name"], "G023_0")
        self.assertTrue(all(Path(L["src"]).is_absolute() for L in sc[0]["layers"] if L.get("src")))
        self.assertEqual(rs["2.5D photo parallax (two planes)"]["scenes"][0]["layers"][0]["src"], "parallax/bg_clean_plate.png")
        gs = rs["Green-screen finish: key, despill, edge, then grade-match"]
        full = g.spec_scenes(gs, 14)
        self.assertEqual([s["name"] for s in full], ["G014_aefull"])
        self.assertEqual(full[0]["layers"][1]["effects"], gs["after_effects"])
        self.assertTrue(full[0]["layers"][1]["src"].endswith("greenscreen/presenter.mp4"))


class TextGallery(unittest.TestCase):
    def test_copy_text(self):
        got = g.copy_text([{"text": "<A>", "font": "BebasNeueBold", "size": 9}, {"text": "<B>"}, {"text": "<C>"}], "dev")
        self.assertEqual([e["text"] for e in got], [g.COPY["dev"][0], g.COPY["dev"][1], g.COPY["dev"][1]])
        self.assertEqual({e["font"] for e in got}, {g.DEVA_FONT})
        self.assertEqual(got[0]["size"], 9)
        ctr = g.copy_text([{"param": "From", "text": "0"}, {"param": "To", "text": "<N>"}, {"param": "Label", "text": "<L>"}],
                          "lat")
        self.assertEqual([e["text"] for e in ctr], ["0", "1947", g.COPY["lat"][0]])
        self.assertTrue(all("ऀ" <= ch <= "ॿ" or ch == " " for ch in "".join(g.COPY["dev"])))

    def test_text_spec_alternates_template_and_title_slots(self):
        recipes = [c for c in g.cards("recipe") if c.get("family") == "text_motion"]
        files = {g.HOST: str(Path(tempfile.gettempdir(), "HOST_X.mogrt"))}
        spec, slots = g.text_spec(list(enumerate(recipes)), "lat", Path("/plate.png"), files)
        self.assertEqual(len(slots), 2 * len(recipes))
        self.assertEqual([s["half"] for s in slots[:2]], ["templates", "ae_title"])
        self.assertEqual([s["start"] for s in slots[:3]], [1.0, 6.0, 11.0])
        self.assertEqual(spec["clips"][0]["dur"], 1.0 + 5.0 * len(slots))
        self.assertEqual(len(spec["texts"]), len(recipes))
        hosted = [m for m in spec["mgts"] if Path(m["file"]).name == "HOST_X.mogrt"]
        self.assertEqual(len(hosted), sum(1 for c in recipes if not c.get("mogrts")))
        seen = set()
        for m in spec["mgts"]:  # the Premiere stack rides on the first template of a slot only
            if m["start"] in seen:
                self.assertEqual(m["effects"], [])
            seen.add(m["start"])
        self.assertTrue(all(t["anim"] is False for t in spec["texts"]))
        if os.name == "nt":  # %LOCALAPPDATA% template paths expand only on Windows, where the apps run
            self.assertFalse(any("%" in m["file"] for m in spec["mgts"]))

    def test_mogrt_sample_resolves(self):
        files = g.mogrt_files()
        self.assertEqual([k for k in g.MOGRT_SAMPLE if k not in files], [])
        self.assertTrue(files[("house", "HB Counter")].replace("\\", "/").endswith("mgt/house/HB Counter.mogrt"))
        spec, slots = g.mogrt_spec(list(enumerate({"folder": f, "name": n, "path": files[(f, n)]}
                                                  for f, n in g.MOGRT_SAMPLE)), "dev", Path("/p.png"))
        self.assertEqual(len(spec["mgts"]), len(g.MOGRT_SAMPLE))
        hb = next(m for m, s in zip(spec["mgts"], slots) if s["name"] == "HB Counter")
        self.assertEqual([e.get("param") for e in hb["text"]], ["From", "To", "Label"])

    def test_timing(self):
        bg = bytes(100)
        ink = lambda n: bytes([200] * n + [0] * (100 - n))  # noqa: E731
        fr = [bg] * 5 + [ink(2 * i) for i in range(1, 11)] + [ink(20)] * 20 + [ink(20 - 4 * i) for i in range(1, 6)] + [bg] * 5
        r = g.timing(fr, bg, 25, t0=-0.2, least=0.01, still=0.001)
        self.assertEqual((r["first_visible_s"], r["in_settled_s"], r["out_starts_s"], r["last_visible_s"]),
                         (0.0, 0.36, 1.2, 1.32))
        self.assertEqual((r["hold_ink"], r["ends"]), (0.2, "animated"))
        self.assertEqual(g.timing([bg] * 5, bg, 25), {"visible": False, "peak_ink": 0.0})
        # a uniform fade: every text pixel is past the threshold from its first frame, only the level rises; then a cut
        fade = [bytes([30 + 9 * i] * 30 + [0] * 70) for i in range(20)] + [bytes([200] * 30 + [0] * 70)] * 20 + [bg] * 5
        r = g.timing(fade, bg, 25, least=0.01, still=0.001)
        self.assertEqual((r["first_visible_s"], r["in_settled_s"], r["last_visible_s"], r["ends"]), (0.0, 0.76, 1.56, "cut"))

    def test_devanagari_compare(self):
        row = lambda lang, vis, a: {"k": 1, "name": "X", "half": "template", "render": "pr", "lang": lang,  # noqa: E731
                                    "visible": vis, "first_visible_s": a, "in_settled_s": a + 0.4, "out_starts_s": 3.0,
                                    "last_visible_s": 4.4, "hold_ink": 0.02}
        res = {"text_motion": [], "mogrts": [row("lat", True, 0.1), row("dev", True, 0.3)]}
        c = g.devanagari_compare(res)[0]
        self.assertEqual((c["d_first_visible_s"], c["d_in_settled_s"], c["ink_ratio_dev_over_lat"]), (0.2, 0.2, 1.0))
        res["mogrts"][1] = dict(row("dev", False, 0), visible=False)
        self.assertEqual(g.devanagari_compare(res)[0]["devanagari_visible"], False)


@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg")
class Plates(unittest.TestCase):
    def test_parallax_plates(self):
        import make_test_media
        with tempfile.TemporaryDirectory() as d:
            keep = make_test_media.MEDIA
            make_test_media.MEDIA = {k: v for k, v in keep.items() if k.startswith("example/parallax/")}
            try:
                made = make_test_media.main(d)
            finally:
                make_test_media.MEDIA = keep
            self.assertEqual(len(made), 2)
            fmt = {}
            for f in made:
                r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,pix_fmt", "-of", "json",
                                    str(Path(d, f))], capture_output=True, text=True, check=True)
                s = json.loads(r.stdout)["streams"][0]
                fmt[Path(f).name] = (s["width"], s["height"], s["pix_fmt"])
            self.assertEqual(fmt, {"bg_clean_plate.png": (1920, 1080, "rgb24"), "subject_cutout.png": (1920, 1080, "rgba")})


class PlateList(unittest.TestCase):
    def test_media_list_has_the_recipe_plates(self):
        import make_test_media
        for rel in ("example/parallax/bg_clean_plate.png", "example/parallax/subject_cutout.png",
                    "example/greenscreen/presenter.mp4"):
            self.assertIn(rel, make_test_media.MEDIA)
        rs = {c["name"]: c for c in g.spec_recipes()}
        used = {L["src"] for s in rs["2.5D photo parallax (two planes)"]["scenes"] for L in s["layers"] if L.get("src")}
        used |= {x["src"] for x in rs["Green-screen finish: key, despill, edge, then grade-match"]["spec"]["clips"]}
        self.assertTrue({"parallax/bg_clean_plate.png", "parallax/subject_cutout.png", "greenscreen/presenter.mp4"} <= used)


if __name__ == "__main__":
    unittest.main()
