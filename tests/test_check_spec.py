"""PVE pre-flight (pve.check_spec / check_scenes): good specs pass, bad ones ARE flagged."""
import json, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pve"))
import pve  # noqa: E402

PRESETS = json.loads((pve.HERE / "harvest" / "files.json").read_text(encoding="utf-8"))["ae_presets"]


def preset(pred):
    return next(p["file"] for p in PRESETS if pred(" ".join(p.get("match_names") or [])))


TEXT_PRESET = preset(lambda mn: "ADBE Text Animators" in mn)
SHAPE_PRESET = preset(lambda mn: "ADBE Root Vectors Group" in mn and "ADBE Text" not in mn)


def clip(*effects, **kw):
    return {"fps": 25, "clips": [{"src": "x.mp4", "effects": list(effects), **kw}]}


def scene(**layer):
    return [{"name": "S", "layers": [{"name": "L", **layer}]}]


class Premiere(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(pve.check_spec(clip({"name": "Gaussian Blur", "pr_props": {}}, transition="Cross Dissolve")), [])
        self.assertEqual(pve.check_spec(json.loads((ROOT / "example" / "spec.json").read_text(encoding="utf-8"))), [])

    def test_unknown_effect(self):
        self.assertIn("no Premiere effect 'Warp Drive'", pve.check_spec(clip({"name": "Warp Drive"}))[0])

    def test_bare_name_effects_do_not_crash(self):
        self.assertIn("no Premiere effect 'Warp Drive'", pve.check_spec(clip("Black & White", "Warp Drive"))[0])

    def test_unknown_property(self):
        self.assertIn("has no property 'Wobble'", pve.check_spec(clip({"name": "Gaussian Blur", "pr_props": {"Wobble": 1}}))[0])

    def test_out_of_range(self):
        name, prop, hi = next((n, p, s["max"]) for n, e in pve.pr_effects().items() for p, s in e["props"].items()
                              if s.get("type") == "number" and s.get("max") is not None and s["max"] < 1e6 and "#" not in p)
        issues = pve.check_spec(clip({"name": name, "pr_props": {prop: hi + 1}}))
        self.assertTrue(issues and "outside" in issues[0], (name, prop, issues))
        self.assertEqual(pve.check_spec(clip({"name": name, "pr_props": {prop: hi}})), [])
        kf = pve.check_spec(clip({"name": name, "pr_props": {prop: [[0, hi], [1, hi + 1]]}}))  # keyframes checked too
        self.assertTrue(kf and "outside" in kf[0], kf)

    def test_broken_harvested_range_is_no_range(self):
        """Track Matte Key 'Matte:' harvested min 3294967296 > max 1e9 (a uint32 probe artefact): 1-based track numbers
        pass (measured live wave 3), a real range still bites."""
        self.assertGreater(pve.pr_effects()["Track Matte Key"]["props"]["Matte:"]["min"],
                           pve.pr_effects()["Track Matte Key"]["props"]["Matte:"]["max"])
        self.assertEqual(pve.check_spec(clip({"name": "Track Matte Key", "pr_props": {"Matte:": 2, "Composite Using:": 1}})), [])
        self.assertTrue(pve.check_spec(clip({"name": "Track Matte Key", "pr_props": {"Composite Using:": 5}})))

    def test_unknown_transition(self):
        self.assertIn("no Premiere transition 'Bogus Wipe'", pve.check_spec(clip(transition="Bogus Wipe"))[0])
        self.assertIn("no Premiere transition", pve.check_spec(clip(transition_out={"name": "Nope"}))[0])

    def test_strobe_flash_rate(self):
        fast = {"name": "Strobe Light", "pr_props": {"Random Strobe Probablity": 50}}
        self.assertIn("random bursts", pve.check_spec(clip(fast))[0])
        self.assertEqual(pve.check_spec(clip(dict(fast, pr_props={**fast["pr_props"], "Blend With Original": 95}))), [])
        slow = {"name": "Strobe Light", "pr_props": {"Strobe Period (secs)": 0.5}}
        self.assertEqual(pve.check_spec(clip(slow)), [])

    def test_mgts_and_aecomps_checked(self):
        spec = {"mgts": [{"name": "T", "effects": [{"name": "Warp Drive"}]}], "aecomps": [{"comp": "C", "effects": ["Nope FX"]}]}
        issues = pve.check_spec(spec)
        self.assertEqual(len(issues), 2, issues)


class AfterEffects(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(pve.check_scenes(scene(type="solid", effects=[{"name": "ADBE Gaussian Blur 2",
                                                                         "props": {"Blurriness": 20}}])), [])
        self.assertEqual(pve.check_scenes(scene(type="text", presets=[{"file": TEXT_PRESET}])), [])

    def test_display_name_accepted(self):
        self.assertEqual(pve.check_scenes(scene(type="solid", effects=[{"name": "Gaussian Blur"}])), [])

    def test_unknown_effect(self):
        self.assertIn("no After Effects effect 'ADBE Nope'", pve.check_scenes(scene(type="solid", effects=[{"name": "ADBE Nope"}]))[0])

    def test_unknown_property(self):
        i = pve.check_scenes(scene(type="solid", effects=[{"name": "ADBE Gaussian Blur 2", "props": {"Nope": 1}}]))
        self.assertIn("has no property 'Nope'", i[0])

    def test_out_of_range(self):
        i = pve.check_scenes(scene(type="solid", effects=[{"name": "ADBE Gaussian Blur 2", "props": {"Blurriness": 99999}}]))
        self.assertIn("outside 0..30000", i[0])

    def test_text_preset_on_non_text_layer(self):
        i = pve.check_scenes(scene(type="footage", presets=[{"file": TEXT_PRESET}]))
        self.assertTrue(i and "animates text but the layer is 'footage'" in i[0], i)

    def test_shape_preset_needs_shape_layer_and_origin(self):
        self.assertIn("is a shape preset", pve.check_scenes(scene(type="solid", presets=[{"file": SHAPE_PRESET}]))[0])
        self.assertIn("layer origin", pve.check_scenes(scene(type="shape", presets=[{"file": SHAPE_PRESET}]))[0])
        self.assertEqual(pve.check_scenes(scene(type="shape", origin=[960, 540], presets=[{"file": SHAPE_PRESET}])), [])

    def test_unsettable_property_type(self):
        name, prop = next((m, p) for m, e in pve.ae_effects().items() for p, s in e["props"].items()
                          if s.get("type") in ("custom", "layer") and "#" not in p)
        i = pve.check_scenes(scene(type="solid", effects=[{"name": name, "props": {prop: 1}}]))
        self.assertTrue(i and "cannot be set from a spec" in i[0], (name, prop, i))

    def test_keys_on_a_property_ae_cannot_animate(self):
        """Live wave 2: AE refused Hue/Saturation Master Saturation keys and dropped the rest of the layer's stack."""
        keyed = {"Master Saturation": [[0, 0], [1.2, -100]]}
        i = pve.check_scenes(scene(type="solid", effects=[{"name": "ADBE HUE SATURATION", "props": keyed}]))
        self.assertTrue(i and "cannot be keyframed" in i[0], i)
        still = {"Master Saturation": -100}  # one value is fine, and so are keys on an animatable twin
        self.assertEqual(pve.check_scenes(scene(type="solid", effects=[{"name": "ADBE HUE SATURATION", "props": still}])), [])
        hls = {"Saturation": [[0, 0], [1.2, -100]]}
        self.assertEqual(pve.check_scenes(scene(type="solid", effects=[{"name": "ADBE Color Balance (HLS)", "props": hls}])), [])


def preset_file(name, folder=""):
    return next(p["file"] for p in PRESETS if Path(p["file"].replace("\\", "/")).stem == name
                and folder in p["file"].replace("\\", "/"))


class FlashRule(unittest.TestCase):
    """Photosensitivity: no more than 3 flashes in any second, for every card that says it flashes."""

    def strobe(self, **props):
        return pve.check_spec(clip({"name": "Strobe Light", "pr_props": props}))

    def test_5hz_strobe_flagged_2hz_passes(self):
        i = self.strobe(**{"Strobe Period (secs)": 0.2, "Strobe Duration (secs)": 0.1})
        self.assertTrue(i and "flashes about 5 times a second" in i[0] and not pve.advisory(i[0]), i)
        self.assertEqual(self.strobe(**{"Strobe Period (secs)": 0.5, "Strobe Duration (secs)": 0.1}), [])
        kf = self.strobe(**{"Strobe Period (secs)": [[0, 1], [1, 0.2]], "Strobe Duration (secs)": 0.1})  # worst key counts
        self.assertTrue(kf and "5 times" in kf[0], kf)

    def test_5hz_strobe_flagged_in_after_effects_scenes(self):
        fx = lambda per: scene(type="solid", effects=[{"name": "ADBE Strobe", "props": {
            "Strobe Period (secs)": per, "Strobe Duration (secs)": 0.1}}])
        i = pve.check_scenes(fx(0.2))
        self.assertTrue(i and "flashes about 5 times a second" in i[0], i)
        self.assertEqual(pve.check_scenes(fx(0.5)), [])

    def test_no_colour_phase_no_flash(self):
        """The colour shows for Period - Duration: Duration 0 or >= Period leaves nothing to flash."""
        for dur in (0, 0.2, 0.5):
            self.assertEqual(self.strobe(**{"Strobe Period (secs)": 0.2, "Strobe Duration (secs)": dur}), [], dur)
        kf = self.strobe(**{"Strobe Period (secs)": 0.2, "Strobe Duration (secs)": [[0, 0.2], [1, 0.1]]})
        self.assertTrue(kf and "5 times" in kf[0], kf)  # a keyed Duration passes through the flashing range

    def test_random_probability_is_a_risk(self):
        """Measured (work/pve_live/strobe.json): 10 reached 6-7 flashes in one second, so any value strictly between 0
        and 100 is flagged, never advisory; 100 is the exact inverse of 0 and keeps the Period's rate."""
        for p in (10, 6, 50, 99):
            i = self.strobe(**{"Random Strobe Probablity": p, "Strobe Period (secs)": 0.5, "Strobe Duration (secs)": 0.1})
            self.assertTrue(len(i) == 1 and "random bursts" in i[0] and not pve.advisory(i[0]), (p, i))
        self.assertEqual(self.strobe(**{"Random Strobe Probablity": 100, "Strobe Period (secs)": 0.5,
                                        "Strobe Duration (secs)": 0.1}), [])  # 2 a second
        fast = self.strobe(**{"Random Strobe Probablity": 100, "Strobe Period (secs)": 0.2, "Strobe Duration (secs)": 0.1})
        self.assertTrue(fast and "5 times" in fast[0], fast)
        ramp = self.strobe(**{"Random Strobe Probablity": [[0, 0], [1, 100]], "Strobe Period (secs)": 0.5})
        self.assertTrue(ramp and "random bursts" in ramp[0], ramp)  # keys 0 -> 100 pass through the middle
        self.assertEqual(self.strobe(**{"Random Strobe Probablity": 50, "Blend With Original": 95}), [])
        ae = pve.check_scenes(scene(type="solid", effects=[{"name": "ADBE Strobe", "props": {"Random Strobe Probablity": 10}}]))
        self.assertTrue(ae and "random bursts" in ae[0], ae)

    def test_flash_transitions_on_the_timeline(self):
        tight = {"fps": 25, "clips": [{"src": "a.mp4", "dur": 0.25, "transition": "Flash"} for _ in range(5)]}
        i = pve.check_spec(tight)
        self.assertTrue(len(i) == 1 and "4 flash transitions within one second" in i[0], i)
        spaced = {"fps": 25, "clips": [{"src": "a.mp4", "dur": 1.1, "transition": "Flash"} for _ in range(5)]}
        self.assertEqual(pve.check_spec(spaced), [])
        unplaced = {"fps": 25, "clips": [{"src": "a.mp4", "transition": "Flash"} for _ in range(5)]}
        self.assertTrue(all(pve.advisory(x) for x in pve.check_spec(unplaced)) and pve.check_spec(unplaced))

    def test_presets_rated_from_their_card(self):
        loop = pve.check_scenes(scene(type="text", presets=[{"file": preset_file("Flicker Loop")}]))
        self.assertTrue(loop and "flashes about 4 times" in loop[0] and not pve.advisory(loop[0]), loop)
        self.assertEqual(pve.check_scenes(scene(type="text", presets=[{"file": preset_file("Flicker In")}])), [])
        spec = {"clips": [{"src": "a.mp4", "dur": 2}], "texts": [{"text": "x", "presets": [preset_file("Flicker Loop")]}]}
        self.assertTrue(any("Flicker Loop flashes" in x for x in pve.check_spec(spec)))

    # measured worst second over 3.9 s (work/pve_live/flash_rates.json): the cards that pass the limit
    MEASURED_FAST = {("Text/Lights and Optical", "Fluorescent Light"): 6,
                     ("Transitions - Dissolves", "Flickering Opacity - at layer out"): 6,
                     ("Text/Animate In", "Opacity Flicker In"): 5, ("Text/Animate Out", "Opacity Flicker Out"): 5,
                     ("Text/Lights and Optical", "Flicker Exposure"): 4}

    def test_measured_fast_presets_flagged(self):
        for (folder, name), n in self.MEASURED_FAST.items():
            host = "text" if folder.startswith("Text") else "footage"
            i = pve.check_scenes(scene(type=host, presets=[{"file": preset_file(name, folder)}]))
            self.assertTrue(len(i) == 1 and f"flashes about {n} times" in i[0] and not pve.advisory(i[0]), (name, i))
        fast = {(c.get("category"), c["name"]) for c in pve.cards()
                if c.get("kind") == "ae_preset" and (c.get("flash") or {}).get("rate", 0) > pve.FLASH_LIMIT}
        self.assertEqual(fast, set(self.MEASURED_FAST) | {("Adobe Express/Looping", "Flicker Loop")})

    def test_measured_2_a_second_passes(self):
        for name in ("Random On-Off", "Opacity Flash - random"):
            self.assertEqual(pve.check_scenes(scene(type="text", presets=[{"file": preset_file(name)}])), [], name)
        spec = {"clips": [{"src": "a.mp4", "dur": 2}],
                "texts": [{"text": "x", "start": 0, "dur": 2, "presets": [preset_file("Random On-Off")]}]}
        self.assertEqual(pve.check_spec(spec), [])

    def test_no_flasher_left_to_check_by_eye(self):
        """Every card with a flash field now carries a countable rate (the 21 by-eye cards were measured)."""
        for c in (c for c in pve.cards() if c.get("flash")):
            self.assertTrue({"per_frame", "period", "rate", "per_use"} & set(c["flash"]), c["name"])

    def test_every_flash_card_is_reachable(self):
        """A flash field on a card the checks never look up would be dead weight."""
        for c in (c for c in pve.cards() if c.get("flash")):
            self.assertIn(c["kind"], ("pr_effect", "ae_effect", "pr_transition", "ae_preset"), c["name"])
            self.assertTrue(c["flash"].get("basis"), c["name"])


if __name__ == "__main__":
    unittest.main()
