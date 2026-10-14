"""The technique cards and recipes written for the completeness critic's gaps (2026-10-13): every file is well formed,
golden searches find them, every recipe's bridge spec / VFX scenes pass PVE's pre-flight, broken copies are flagged,
and every critic gap names the cards that cover it."""
import contextlib, copy, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pve"))
import pve  # noqa: E402
import validate_cards  # noqa: E402

CARDS = pve.HERE / "cards"
TOOL_FILES = ["tools_editing_grammar.json", "tools_av_sync.json", "tools_camera_motion.json", "tools_text_animators.json",
              "tools_expressions_extra.json", "tools_graphics_layout.json", "tools_delivery.json",
              "tools_audio_deliverables.json", "tools_media.json", "tools_compositing.json"]
RECIPE_FILES = ["recipe_moves.json", "recipe_infographics.json", "recipe_layouts.json", "recipe_documents.json",
                "recipe_credits.json", "recipe_audio_mix.json", "recipe_compositing.json"]
# vfx_build.jsx: layer types and the properties a seg / expr may name (prop() there)
VFX_TYPES = {"solid", "shape", "marker", "text", "counter", "footage"}
VFX_PROPS = {"opacity", "scale", "position", "rotation", "anchor", "trim", "trim_start", "fill_op", "stroke_op",
             "fill_color", "stroke_color", "source_text"}
EASES = {"lin", "smooth", "out", "in"}


def load(name):
    return json.loads((CARDS / name).read_text(encoding="utf-8"))["cards"]


def recipes():
    return [r for f in RECIPE_FILES for r in load(f)]


def ae_side(spec):
    """The After Effects half of a bridge spec's clip effects ({name, props})."""
    return [{"name": e["name"], "props": e.get("props") or {}} for c in spec.get("clips", []) for e in c.get("effects", [])
            if isinstance(e, dict)]


def preflight(r):
    """Every non-advisory PVE issue for one recipe: its stacks, its bridge spec (both apps) and its VFX scenes."""
    found = pve.check_effects(r.get("premiere"), "PR", "premiere") + pve.check_effects(r.get("after_effects"), "AE", "after_effects")
    if r.get("spec"):
        found += pve.check_spec(r["spec"]) + pve.check_effects(ae_side(r["spec"]), "AE", "spec")
    if r.get("scenes"):
        found += pve.check_scenes(r["scenes"])
    return [i for i in found if not pve.advisory(i)]


def scene_shape(scenes):
    """Structural problems vfx_build.jsx would hit (unknown layer type / property / ease, CAM on layer 0)."""
    bad = []
    for s in scenes:
        for k in ("name", "dur", "layers"):
            if k not in s:
                bad.append(f"{s.get('name')}: no {k}")
        for i, L in enumerate(s.get("layers", [])):
            where = f"{s.get('name')}/{L.get('name')}"
            if L.get("type") not in VFX_TYPES:
                bad.append(f"{where}: type {L.get('type')!r}")
            if L.get("type") in ("text", "counter") and not (L.get("font") and L.get("pos") and L.get("size")):
                bad.append(f"{where}: text needs font, pos and size")
            if L.get("type") == "footage" and not L.get("src"):
                bad.append(f"{where}: footage needs src")
            if L.get("type") == "shape" and not all(p.get("pts") for p in L.get("paths", [None]) if p is not None):
                bad.append(f"{where}: shape needs paths with pts")
            if L.get("cam") and (i == 0 or not s.get("cam")):
                bad.append(f"{where}: cam layer without a scene cam, or on layer 0 (CAM is created after it)")
            for g in L.get("segs", []):
                if g.get("p") not in VFX_PROPS or len(g.get("seg", [])) != 5 or g["seg"][4] not in EASES:
                    bad.append(f"{where}: seg {g}")
            bad += [f"{where}: expr on {p!r}" for p in L.get("expr", {}) if p not in VFX_PROPS]
            m = L.get("matte")
            if m and m.get("layer") not in {x.get("name") for x in s["layers"]}:
                bad.append(f"{where}: matte layer {m.get('layer')!r} missing")
    return bad


class Files(unittest.TestCase):
    def test_tool_files_are_well_formed(self):
        for f in TOOL_FILES:
            cs = load(f)
            self.assertGreater(len(cs), 1, f)
            for c in cs:
                missing = [k for k in validate_cards.REQUIRED | {"how", "scriptable", "script_api", "limits", "sources"} if not c.get(k)]
                self.assertEqual(missing, [], (f, c.get("name")))
                self.assertEqual(c["kind"], "tool", c["name"])
                self.assertIn(c["scriptable"], ("yes", "partial", "no"), c["name"])
                self.assertIn(c["app"], ("AE", "PR", "both"), c["name"])
                self.assertIn(c["status"], validate_cards.STATUS | {"beta"}, c["name"])
                self.assertIn(c["confidence"], ("high", "medium", "low"), c["name"])
                self.assertTrue(all(s.startswith(("https://", "pve/")) for s in c["sources"]), c["name"])

    def test_recipe_files_are_well_formed_and_judged(self):
        """Live wave 2 rendered every gap recipe (pve/gallery/recipe_specs): each carries a judged verdict, the frame or
        render it was judged on and the not_rendered state it left; confidence is high only where every half passed."""
        names = {c.get("name") for c in pve.cards()}
        for r in recipes():
            missing = [k for k in validate_cards.REQUIRED | {"family", "keyframes_note", "built_from", "verified"} if not r.get(k)]
            self.assertEqual(missing, [], r.get("name"))
            self.assertEqual(r["kind"], "recipe")
            self.assertTrue(r.get("spec") or r.get("scenes"), f"{r['name']}: nothing the bridge can build")
            v = r["verified"]
            self.assertNotEqual(v["verdict"], "not_rendered", r["name"])
            self.assertTrue(v["frame"].startswith("pve/gallery/recipe_specs/"), r["name"])
            self.assertIn("not_rendered", [p["verdict"] for p in v["previous"]], r["name"])
            halves = [v["verdict"]] + ([v["ae"]["verdict"]] if v.get("ae") else [])
            if r["confidence"] == "high":
                self.assertEqual(set(halves), {"pass"}, r["name"])
            self.assertEqual([b for b in r["built_from"] if b not in names], [], r["name"])

    def test_measured_fixes_stay_in(self):
        """The two live-wave-2 findings fixed by value: a seamless ticker period and frame-safe ducking lifts."""
        r = {x["name"]: x for x in recipes()}
        layers = {L["name"]: L for L in r["News ticker crawl (looping)"]["scenes"][0]["layers"]}
        self.assertEqual(layers["CRAWL"]["text"], layers["CRAWL_UNIT"]["text"] * 3)
        self.assertEqual(layers["CRAWL_UNIT"]["opacity"], 0)
        self.assertIn('thisComp.layer("CRAWL_UNIT")', layers["CRAWL"]["expr"]["position"])
        self.assertNotIn("w / 3", layers["CRAWL"]["expr"]["position"])
        audio = r["Music ducks under VO (scripted, from VO ranges)"]["spec"]["audio"]
        lifts = [a for a in audio if a["src"] == "music.mp3" and a["track"] == 4]
        self.assertEqual(len(lifts), 3)
        for a in lifts:  # in = start (sample-aligned copy) on the 0.2 s grid shared by 25 and 30 fps
            self.assertEqual(a["in"], a["start"])
            self.assertAlmostEqual(a["in"] * 5, round(a["in"] * 5), places=6, msg=a)

    def test_validator_style_checks_pass(self):
        """The same verdict / flash checks validate_cards applies to batch cards."""
        for f in TOOL_FILES + RECIPE_FILES:
            for c in load(f):
                self.assertEqual(validate_cards.check_verdicts(c), [], c["name"])


class Golden(unittest.TestCase):
    def assertFinds(self, query, expect, top=5, **kw):
        got = [c["name"] for c in pve.find(query, **kw)[:top]]
        self.assertTrue(any(expect in n for n in got), f"{query!r}: {expect!r} not in top {top}: {got}")

    def test_queries(self):
        self.assertFinds("j-cut", "J-cut and L-cut")
        self.assertFinds("split edit", "J-cut and L-cut")
        self.assertFinds("ripple roll slip slide", "Trims:")
        self.assertFinds("three-point edit", "Three-point")
        self.assertFinds("ken burns", "Ken Burns")
        self.assertFinds("safe area", "safe area")
        self.assertFinds("title safe action safe", "Broadcast safe areas")
        self.assertFinds("reels safe zone", "Platform UI safe zones")
        self.assertFinds("ducking", "ducking from VO ranges")
        self.assertFinds("music ducks under vo", "Music ducks under VO", kind="recipe")
        self.assertFinds("rolling credits", "credits")
        self.assertFinds("news ticker", "ticker")
        self.assertFinds("blur fill", "Blur fill")
        self.assertFinds("bar chart", "Bar chart", kind="recipe")
        self.assertFinds("donut", "Donut")
        self.assertFinds("merge clips", "Merge Clips")
        self.assertFinds("synchronize waveform", "ync")
        self.assertFinds("variable frame rate", "ariable frame rate")
        self.assertFinds("youtube upload bitrate", "YouTube upload")
        self.assertFinds("prores 422 hq broadcast", "broadcast masters")
        self.assertFinds("stems", "Stems and split-track export")
        self.assertFinds("music and effects mix", "M&E")
        self.assertFinds("colr nclc gamma", "Colour tags on export")
        self.assertFinds("flash photosensitive", "Photosensitive")
        self.assertFinds("auto-orient", "Auto-Orient")
        self.assertFinds("separate dimensions", "Separate Dimensions")
        self.assertFinds("createpath", "createPath")
        self.assertFinds("light wrap", "Light wrap")
        self.assertFinds("relink offline media", "elink")


class Preflight(unittest.TestCase):
    def test_every_recipe_spec_and_scene_is_clean(self):
        bad = {r["name"]: preflight(r) for r in recipes()}
        self.assertEqual({k: v for k, v in bad.items() if v}, {})

    def test_scene_structure(self):
        bad = [b for r in recipes() if r.get("scenes") for b in scene_shape(r["scenes"])]
        self.assertEqual(bad, [])

    def test_cli_check_and_scenes(self):
        spec = next(r["spec"] for r in recipes() if r.get("spec"))
        scenes = next(r["scenes"] for r in recipes() if r.get("scenes"))
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        with tempfile.TemporaryDirectory() as d:
            for cmd, data in (("check", spec), ("scenes", scenes)):
                p = Path(d, f"{cmd}.json")
                p.write_text(json.dumps(data), encoding="utf-8")
                r = subprocess.run([sys.executable, "pve/pve.py", cmd, str(p)], cwd=ROOT, capture_output=True, text=True,
                                   encoding="utf-8", env=env)
                self.assertEqual((r.returncode, r.stdout.strip()), (0, "OK: nothing PVE objects to"), cmd)

    def test_broken_copies_are_flagged(self):
        r = next(x for x in recipes() if x["name"] == "Blur fill: 16:9 into 9:16")
        spec = copy.deepcopy(r["spec"])
        spec["clips"][0]["effects"][0]["pr_props"]["Blurryness"] = 10  # typo
        self.assertTrue(any("Blurryness" in i for i in pve.check_spec(spec)))
        spec = copy.deepcopy(r["spec"])
        spec["clips"][1]["effects"][0]["pr_props"]["Scale Height"] = 99999  # outside -30000..30000
        self.assertTrue(any("outside" in i for i in pve.check_spec(spec)))
        sc = copy.deepcopy(next(x for x in recipes() if x.get("scenes") and x["name"].startswith("Paper"))["scenes"])
        sc[0]["layers"][1]["effects"][0]["props"]["Opacity"] = 300  # Drop Shadow opacity is 0..255
        self.assertTrue(any("outside" in i for i in pve.check_scenes(sc)))
        sc[0]["layers"][1]["segs"].append({"p": "wobble", "seg": [0, 0, 1, 1, "lin"]})
        self.assertTrue(scene_shape(sc))

    def test_pipeline_validate_covers_recipe_specs(self):
        sys.path.insert(0, str(ROOT))
        import pipeline  # noqa: E402
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pipeline.validate(), [])


class Critic(unittest.TestCase):
    def test_every_gap_names_existing_cards(self):
        z = json.loads((CARDS / "zz_critic.json").read_text(encoding="utf-8"))
        names = {c.get("name") for c in pve.cards()}
        self.assertEqual(len(z["gaps"]), 20)
        for g in z["gaps"]:
            self.assertIsInstance(g, dict)
            self.assertTrue(g["gap"])
            self.assertTrue(g["covered"].startswith("2026-10-13: "), g["gap"][:40])
            listed = [n.strip() for n in g["covered"].split(": ", 1)[1].split("; ")]
            self.assertEqual([n for n in listed if n not in names], [], g["gap"][:40])

    def test_flash_rule_card_matches_the_check(self):
        c = pve.card("Photosensitive flash rule (Harding / ITU-R BT.1702) and pve check")
        self.assertIsNotNone(c)
        self.assertEqual(pve.FLASH_LIMIT, 3)
        self.assertIn("3 flashes", c["summary"])
        self.assertIn(pve.ADVISORY, c["how"])


if __name__ == "__main__":
    unittest.main()
