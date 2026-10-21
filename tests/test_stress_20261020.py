"""Regressions from the 2026-10-20 stress test: the pre-flight fails closed and checks every half a build applies,
search ranks whole words, card() and the CLI say what they did, the pipeline stages keep their data, and the small
tools (flash_scan, make_test_media, sfx_synth, grunge) report failures instead of passing them on."""
import contextlib, importlib.util, io, json, os, shutil, subprocess, sys, tempfile, unittest, warnings
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pve"))
import adobe_bridge as ab  # noqa: E402
import build_docs  # noqa: E402
import flash_scan as fs  # noqa: E402
import make_test_media  # noqa: E402
import prepare_batches  # noqa: E402
import pve  # noqa: E402
import validate_cards  # noqa: E402

HAS_NUMPY = all(importlib.util.find_spec(m) for m in ("numpy", "scipy"))
STROBE_10HZ = {"name": "Strobe Light", "props": {"Strobe Period (secs)": 0.1, "Strobe Duration (secs)": 0.05}}


def run(*args, cwd=ROOT, env=None, drop=("PYTHONIOENCODING", "PYTHONUTF8", "PVE_HOME", "PVE_LIVE")):
    """A python script in a child -> (exit code, stdout, stderr), with the console encoding left at the OS default."""
    e = {k: v for k, v in os.environ.items() if k not in drop}
    e.update(env or {})
    r = subprocess.run([sys.executable, *map(str, args)], cwd=cwd, capture_output=True, env=e)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def tmpdir(test):
    d = Path(tempfile.mkdtemp())
    test.addCleanup(shutil.rmtree, d, True)
    return d


def clip(*effects, **kw):
    return {"fps": 30, "clips": [{"src": "a.mp4", "dur": 2, "effects": list(effects), **kw}]}


def scene(**layer):
    return [{"name": "S", "layers": [{"name": "L", **layer}]}]


class KnowledgeBaseFailsClosed(unittest.TestCase):
    """A missing, empty or partly unreadable knowledge base is 'could not check', never 'OK'."""

    def test_empty_home(self):
        home = tmpdir(self)
        rc, out, _ = run("pve/pve.py", "check", "example/fx_test.json", env={"PVE_HOME": str(home)})
        self.assertEqual(rc, 2, out)
        self.assertTrue(out.startswith("knowledge base incomplete at "), out)
        self.assertNotIn("OK: nothing PVE objects to", out)
        rc, out, _ = run("pipeline.py", "run", "validate", env={"PVE_HOME": str(home)})
        self.assertEqual(rc, 1, out)
        self.assertIn("no batch files", out)
        self.assertEqual(run("pipeline.py", "run", "docs", env={"PVE_HOME": str(home)})[0], 1)
        self.assertFalse((home / "PVE.md").exists())  # no 0-card guide

    def test_unreadable_card_file_is_named(self):
        home = tmpdir(self)
        (home / "cards").mkdir()
        (home / "cards" / "pr_effect_07.json").write_text('{"cards": [{"name": "Strobe', encoding="utf-8")
        spec = home / "spec.json"
        spec.write_text(json.dumps(clip(dict(STROBE_10HZ, pr_props=STROBE_10HZ["props"]))), encoding="utf-8")
        rc, out, _ = run("pve/pve.py", "check", spec, env={"PVE_HOME": str(home)})
        self.assertEqual(rc, 2, out)
        self.assertIn("cards/pr_effect_07.json unreadable", out.splitlines()[0])
        rc, _, err = run("pve/build_docs.py", env={"PVE_HOME": str(home)})
        self.assertEqual(rc, 1)
        self.assertIn("pr_effect_07.json", err)

    def test_validate_keeps_the_unreadable_line(self):
        """validate_cards prints one line (no summary) for an unreadable card file; validate used to drop it."""
        import pipeline
        d = tmpdir(self)
        b, c = d / "lut_01.json", d / "cards_lut_01.json"
        b.write_text(json.dumps({"kind": "lut", "items": []}), encoding="utf-8")
        c.write_text('{"cards": [', encoding="utf-8")
        with mock.patch.object(pipeline, "pairs", return_value=[(b, c)]), contextlib.redirect_stdout(io.StringIO()):
            issues = pipeline.validate()
        self.assertTrue(any("card file unreadable" in i for i in issues), issues[:5])

    def test_bom_files_load(self):
        d = tmpdir(self)
        p = d / "spec.json"
        p.write_bytes(b"\xef\xbb\xbf" + json.dumps(json.loads((ROOT / "example" / "spec.json").read_text("utf-8"))).encode())
        rc, out, _ = run("pve/pve.py", "check", p)
        self.assertEqual((rc, out.strip()), (0, "OK: nothing PVE objects to"))
        self.assertEqual(pve._load(p)["name"], json.loads((ROOT / "example" / "spec.json").read_text("utf-8"))["name"])


class Cli(unittest.TestCase):
    def test_help_and_usage_errors(self):
        rc, out, _ = run("pve/pve.py", "--help")
        self.assertEqual(rc, 0)
        self.assertIn("pve/pve.py check", out)
        for args in (("chek", "example/fx_test.json"), ("Check", "example/fx_test.json"), ("card",), ("check",),
                     ("find", "blur", "--kind", "effect"), ("find", "blur", "--verdict", "brokn"), ("find", "blur", "--app"),
                     ("find", "blur", "--limit", "3"), ("card", "Gaussian Blur", "XX")):
            rc, out, err = run("pve/pve.py", *args)
            self.assertEqual((rc, out), (2, ""), args)
            self.assertTrue(err.strip(), args)

    def test_input_errors_exit_2(self):
        d = tmpdir(self)
        for name, text, cmd in (("list.json", "[1, 2]", "check"), ("spec.json", '{"clips": []}', "scenes"),
                                ("bad.json", "{nope", "check")):
            (d / name).write_text(text, encoding="utf-8")
            rc, _, err = run("pve/pve.py", cmd, d / name)
            self.assertEqual(rc, 2, (name, err))
            self.assertIn("error:", err)
        self.assertEqual(run("pve/pve.py", "check", d / "missing.json")[0], 2)

    def test_non_latin_text_on_a_cp1252_pipe(self):
        rc, out, err = run("pve/pve.py", "card", "Sports Intro")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["name"], "Sports Intro")
        d = tmpdir(self)
        p = d / "spec.json"
        p.write_text(json.dumps(clip({"name": "Glow \u2728"}, {"name": "\u091a\u092e\u0915"}), ensure_ascii=False),
                     encoding="utf-8")
        rc, out, err = run("pve/pve.py", "check", p)
        self.assertEqual(rc, 1, err)
        self.assertEqual(len(out.splitlines()), 2, out)

    def test_find_lines_flag_status_and_verdict(self):
        rc, out, _ = run("pve/pve.py", "find", "whip", "--app", "pr")
        self.assertEqual(rc, 0)
        line = next(l for l in out.splitlines() if "] Whip (Legacy): " in l)
        self.assertIn("Whip (Legacy): [obsolete", line)


class Search(unittest.TestCase):
    def names(self, q, n=5, **kw):
        return [c["name"] for c in pve.find(q, limit=n, **kw)]

    def test_whole_words_and_exact_names(self):
        self.assertEqual(self.names("fade in", 1), ["Fade In"])
        self.assertEqual(self.names("Scale In", 1), ["Scale In"])
        self.assertTrue(any("Roto" in n for n in self.names("rotoscope a person")), self.names("rotoscope a person"))
        self.assertTrue(pve.find("a"))  # only stop words: they are kept

    def test_current_cards_beat_obsolete_ones(self):
        for q in ("whip pan", "Levels", "glow", "fade to black"):
            self.assertEqual(pve.find(q, limit=1)[0].get("status"), "current", q)

    def test_split_screen_finds_the_layouts(self):
        self.assertIn(self.names("split screen", 1)[0], ("Two-way (double box) split", "Three-up row"))

    def test_after_effects_half_verdict_filters(self):
        self.assertIn("Grungy film reel", self.names("grungy film", 10, verdict="corrected_unverified"))


class Card(unittest.TestCase):
    def test_app_is_never_crossed(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            c = pve.card("Gaussian Blur", "pr")
            self.assertEqual(c["app"], "PR")
            self.assertIsNone(pve.card("", None))
            self.assertIsNone(pve.card("   "))
            with self.assertRaises(ValueError):
                pve.card("Gaussian Blur", "XX")
            self.assertEqual(pve.card("ae_tools/Warp Stabilizer")["kind"], "tool")
            self.assertNotIn(pve.card("Wigglerama")["status"], ("hidden", "obsolete", "unusable"))

    def test_every_card_is_reachable(self):
        """By name and app, by match name, or by the "Category/Name" the ambiguity warning names."""
        warnings.simplefilter("ignore", pve.AmbiguousCard)
        self.addCleanup(warnings.resetwarnings)
        names = {}
        for c in pve.cards():
            names.setdefault((c.get("name") or "").lower(), []).append(c)
        bad = []
        for group in (g for g in names.values() if len(g) > 1):
            for c in group:
                tries = [(c.get("name"), c.get("app")), (pve._label(c), None), (pve._label(c), c.get("app"))]
                tries += [(c["match"], None), (c["match"], c.get("app"))] if c.get("match") else []
                if not any(pve.card(q, a) is c for q, a in tries if q):
                    bad.append((c.get("kind"), c.get("app"), c.get("name")))
        self.assertEqual(bad, [])


class SpecHalves(unittest.TestCase):
    """Each half of a clip effect is checked against the app that receives it; texts against After Effects."""

    def test_text_effects(self):
        spec = {"fps": 30, "clips": [{"src": "a.mp4", "dur": 2}],
                "texts": [{"text": "HI", "start": 0, "dur": 2, "effects": [STROBE_10HZ, {"name": "Nonexistent FX"},
                                                                         {"name": "Gaussian Blur", "props": {"Blurriness": 99999}}]}]}
        i = pve.check_spec(spec)
        self.assertTrue(any("flashes about 10 times" in x for x in i), i)
        self.assertTrue(any("no After Effects effect 'Nonexistent FX'" in x for x in i), i)
        self.assertTrue(any("outside 0..30000" in x for x in i), i)

    def test_after_effects_half_of_clips(self):
        fx = {"name": "Gaussian Blur", "props": {"Blurriness": 99999, "Bogus AE prop": 1}, "pr_props": {"Amount": 60}}
        self.assertEqual(pve.check_spec(clip(fx)), [])  # programmatic default: Premiere half only
        i = pve.check_spec(clip(fx), ("AE", "PR"))
        self.assertTrue(any("outside 0..30000" in x for x in i) and any("Bogus AE prop" in x for x in i), i)
        ae_strobe = dict(STROBE_10HZ, pr_props={"Strobe Period (secs)": 1, "Strobe Duration (secs)": 0.5})
        self.assertTrue(any("10 times" in x for x in pve.check_spec(clip(ae_strobe), ("AE", "PR"))))
        self.assertEqual(pve.check_spec(clip({"name": "Bend"}, {"name": "Track Matte Key", "pr_props": {"Matte:": 2}}),
                                        ("AE", "PR")), [])  # Premiere-only effects: After Effects only warns
        both = pve.check_spec(clip(dict(STROBE_10HZ, pr_props=STROBE_10HZ["props"])), ("AE", "PR"))
        self.assertEqual(len(both), 1, both)  # the two halves' identical line once
        self.assertTrue(pve.check_spec(clip(presets=["Text/Animate In/Typewriter.ffx"]), ("AE", "PR")))

    def test_after_effects_only_build_still_names_unknown_effects(self):
        """With no Premiere half to name it, an effect After Effects lacks is the AE half's to flag."""
        i = pve.check_spec(clip({"name": "Nonexistent FX"}), ("AE",))
        self.assertEqual(i, ["clip 1: no After Effects effect 'Nonexistent FX'"])
        both = pve.check_spec(clip({"name": "Nonexistent FX"}), ("AE", "PR"))
        self.assertEqual(both, ["clip 1: no Premiere effect 'Nonexistent FX'"])  # named once, by Premiere
        spec = tmpdir(self) / "s.json"
        spec.write_text(json.dumps(clip({"name": "Nonexistent FX"})), encoding="utf-8")
        rc, out, _ = run("pve/pve.py", "check", "--no-pr", spec)
        self.assertEqual(rc, 1, out)
        self.assertIn("no After Effects effect 'Nonexistent FX'", out)

    def test_no_app_left_to_check_is_a_usage_error(self):
        spec = tmpdir(self) / "s.json"
        spec.write_text(json.dumps(clip(dict(STROBE_10HZ, pr_props=STROBE_10HZ["props"]))), encoding="utf-8")
        rc, out, err = run("pve/pve.py", "check", "--no-ae", "--no-pr", spec)
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("no app to check", err)

    def test_build_passes_the_apps_it_drives(self):
        class Stop(Exception):
            pass
        for kw, apps in (({}, ("AE", "PR")), ({"ae": False}, ("PR",)), ({"premiere": False}, ("AE",))):
            with mock.patch.object(ab, "normalize", return_value={"texts": []}), \
                    mock.patch.object(ab, "_pve_check", side_effect=Stop) as m, self.assertRaises(Stop):
                ab.build({}, ROOT, **kw)
            self.assertEqual(m.call_args.args, ("check_spec", {}, apps))
        i = ab._pve_check("check_spec", clip({"name": "Gaussian Blur", "props": {"Blurriness": 99999}}), ("AE", "PR"))
        self.assertTrue(any("outside 0..30000" in x for x in i), i)


class Values(unittest.TestCase):
    def pr(self, name, **props):
        return pve.check_spec(clip({"name": name, "pr_props": props}))

    def test_wrong_types_are_flagged(self):
        for v in (float("nan"), "20", [20, 30], None):
            self.assertTrue(self.pr("Gaussian Blur", Amount=v), v)
        self.assertEqual(self.pr("Tint", **{"Map Black To": "#1a0033"}), [])  # colours harvested as numbers
        self.assertTrue(pve.check_scenes(scene(type="solid", effects=[{"name": "Tint", "props": {"Map White To": "red"}}])))
        self.assertTrue(pve.check_scenes(scene(type="solid", effects=[{"name": "ADBE Gaussian Blur 2",
                                                                         "props": {"Blurriness": "20"}}])))

    def test_track_matte_track_numbers(self):
        for v in (-1, 1.5):
            self.assertTrue(self.pr("Track Matte Key", **{"Matte:": v}), v)
        zero = self.pr("Track Matte Key", **{"Matte:": 0})
        self.assertTrue(zero and all(pve.advisory(x) for x in zero), zero)

    def test_one_malformed_entry_keeps_the_rest(self):
        spec = {"fps": 30, "clips": [
            {"src": "a", "dur": 2, "effects": [{"name": "Gaussian Blur", "pr_props": {"Amount": [[0]]}}]},
            {"src": "b", "dur": 2, "effects": [{"name": "Tint", "props": ["Amount to Tint"]}]},
            {"src": "c", "dur": 2, "effects": [{"name": "Strobe Light", "pr_props": {"Strobe Period (secs)": 0.2,
                                                                                   "Strobe Duration (secs)": 0.1}}]}]}
        i = pve.check_spec(spec)
        self.assertTrue(any("clip 3: Strobe Light flashes about 5 times" in x for x in i), i)
        self.assertTrue(any(x.startswith("clip 1:") for x in i) and any(x.startswith("clip 2") for x in i), i)
        i = pve.check_scenes(scene(type="solid", presets=["Text/Animate In/Typewriter.ffx"]))
        self.assertTrue(i and "must be {" in i[0], i)
        self.assertEqual(pve.check_spec({"fps": 0, "clips": [{"src": "a", "dur": 1}]})[0], "fps 0: must be a number above 0")


class Flashes(unittest.TestCase):
    def ae(self, per, dur=0.1, **layer):
        return pve.check_scenes(scene(effects=[{"name": "ADBE Strobe", "props": {"Strobe Period (secs)": per,
                                                                             "Strobe Duration (secs)": dur}}], **layer))

    def test_period_zero_is_not_zero_flashes(self):
        i = pve.check_spec(clip({"name": "Strobe Light", "pr_props": {"Strobe Period (secs)": 0,
                                                                       "Strobe Duration (secs)": 0.01}}))
        self.assertTrue(i and pve.advisory(i[0]) and "unmeasured" in i[0], i)

    def test_trimmed_after_effects_layer(self):
        """AE's strobe clock is the layer's source time: a mid-period in point opens with a partial flash."""
        i = self.ae(0.4, type="footage", **{"in": 0.65})
        self.assertTrue(i and "4 times in the layer's first second" in i[0] and not pve.advisory(i[0]), i)
        self.assertEqual(self.ae(0.4, type="footage", **{"in": 0.8}), [])  # whole periods: in phase
        self.assertEqual(self.ae(0.5, type="footage", **{"in": 0.3}), [])
        self.assertTrue(self.ae(0.4, type="solid", t0=0.65))  # other layers: their clock starts at 0, shown at t0
        self.assertEqual(self.ae(0.4, type="solid"), [])


class Presets(unittest.TestCase):
    def test_any_spelling_of_a_preset_path(self):
        for f in ("Text/Animate In/Typewriter.ffx", "/opt/Adobe After Effects 2026/Support Files/Presets/Text/Animate In/"
                  "Typewriter.ffx", "Presets\\Text\\Animate In\\Typewriter.ffx"):
            i = pve.check_scenes(scene(type="solid", presets=[{"file": f}]))
            self.assertTrue(i and "animates text" in i[0], (f, i))
        self.assertEqual(pve.check_scenes(scene(type="counter", presets=[{"file": "Text/Animate In/Typewriter.ffx"}])), [])


class AfterEffectsNames(unittest.TestCase):
    def check(self, name, props):
        return [x for x in pve.check_scenes(scene(type="solid", effects=[{"name": name, "props": props}]))
                if not pve.advisory(x)]

    def test_property_paths_ae_lib_cannot_reach(self):
        for props in ({"Grow": 1}, {"Nope/Screen Gain": 100}):
            self.assertTrue(self.check("Keylight 906", props), props)
        self.assertTrue(self.check("ADBE Scribble Fill", {"End Apply To": 1}))
        self.assertEqual(self.check("Keylight 906", {"Screen Shrink/Grow": 1}), [])
        self.assertEqual(self.check("CC Particle Systems II", {"Particle/Birth Color": "#ff0000", "Birth Color": "#00ff00",
                                                               "Producer/Birth Rate": 2}), [])  # header rows walked past

    def test_shared_display_names(self):
        self.assertEqual(self.check("Color Balance", {"Highlight Red Balance": 20}), [])
        self.assertEqual(self.check("Levels", {"Clip To Output White": 3}), [])
        self.assertTrue(self.check("Levels", {"Nope": 3}))


class Recipes(unittest.TestCase):
    def test_every_effect_stack_is_known(self):
        """A recipe key holding effects that RECIPE_STACKS does not list would never be pre-flighted."""
        known = set(pve.RECIPE_STACKS) | {"scenes"}
        stray = set()
        for r in (c for c in pve.cards() if c.get("kind") == "recipe"):
            for k, v in r.items():
                for kk, vv in ([(k, v)] + [(f"{k}.{a}", b) for a, b in v.items()] if isinstance(v, dict) else [(k, v)]):
                    if isinstance(vv, list) and any(isinstance(x, dict) and "name" in x and ("props" in x or "pr_props" in x)
                                                    for x in vv) and kk not in known:
                        stray.add(kk)
        self.assertEqual(stray, set())

    def test_outgoing_and_audio_stacks_are_checked(self):
        keyed = {"name": "ADBE HUE SATURATION", "props": {"Master Saturation": [[-0.3, 0], [-0.01, -60]]}}
        self.assertTrue(any("cannot be keyframed" in x for x in pve.check_recipe({"after_effects_out": [keyed]})))
        self.assertTrue(pve.check_recipe({"premiere_audio": [{"name": "DeNoise", "pr_props": {"Bogus": 1}}]}))
        self.assertEqual(pve.check_recipe({"premiere_audio": [{"name": "Channel Mixer", "pr_props": {"L to L": 1}}]}), [])

    def test_popup_values_name_real_items(self):
        """A recipe's After Effects popup value must not land on a menu separator; Grungy film reel's scratch layer
        is Screen (5 is Multiply in the harvested labels, which darkened its render)."""
        labels = json.loads((pve.HERE / "harvest" / "ae_enum_labels.json").read_text(encoding="utf-8"))["effects"]
        bad = []
        for r in (c for c in pve.cards() if c.get("kind") == "recipe"):
            for key, app, fx in pve.recipe_stacks(r):
                for e in (e for e in fx if app == "AE" and isinstance(e, dict)):
                    pops = (labels.get(e.get("name")) or {}).get("popups") or {}
                    for p, v in (e.get("props") or {}).items():
                        lab = (pops.get(p) or {}).get("labels") or {}
                        if lab and type(v) is int and lab.get(str(v), "(-").startswith("(-"):
                            bad.append((r["name"], key, e["name"], p, v))
        self.assertEqual(bad, [])
        r = pve.card("Grungy film reel")
        scratch = next(e for e in r["after_effects"] if e["name"] == "ADBE Fractal Noise" and e["props"].get("Opacity") == 40)
        self.assertEqual(labels["ADBE Fractal Noise"]["popups"]["Blending Mode"]["labels"][str(scratch["props"]["Blending Mode"])],
                         "Screen")

    def test_no_card_hedges_on_a_harvested_label(self):
        hedges = ("believed", "probably", "(unverified)")
        bad = [(c["name"], p["name"]) for c in pve.cards() if c.get("kind") == "ae_effect"
               for p in c.get("params") or [] if p.get("name") in (c.get("enum_labels") or {})
               and any(h in f"{p.get('does', '')} {p.get('typical', '')}" for h in hedges)]
        self.assertEqual(bad, [])

    def test_grungy_film_reel_blending_mode_matches_the_harvested_labels(self):
        labels = json.loads((pve.HERE / "harvest" / "ae_enum_labels.json").read_text(encoding="utf-8"))
        labels = labels["effects"]["ADBE Fractal Noise"]["popups"]["Blending Mode"]["labels"]
        screen = next(int(k) for k, v in labels.items() if v == "Screen")
        r = pve.card("Grungy film reel")
        modes = [e["props"].get("Blending Mode") for e in r["after_effects"] if e["name"] == "ADBE Fractal Noise"]
        self.assertIn(screen, modes)  # the scratch layer screens its white lines on
        ae = r["verified"]["ae"]
        self.assertNotIn("unverified", ae["note"])  # no hedge left on a label the harvest settles
        self.assertIn(f"{screen} = Screen", r["keyframes_note"])

    def test_no_frame_named_where_none_exists(self):
        bad = [c["name"] for c in pve.cards() if (c.get("verified") or {}).get("frame")
               and ("No frame exists" in c["verified"].get("note", "") or c["verified"].get("verdict") == "not_rendered")]
        self.assertEqual(bad, [])

    def test_wave_prompts_check_premiere_stacks_as_premiere(self):
        """The wave prompts feed `pve.py check` a Premiere-only stack; without --no-ae the After Effects half objects to
        Premiere property names and an agent 'fixes' a correct stack."""
        spec = tmpdir(self) / "pr_only.json"
        spec.write_text(json.dumps({"clips": [{"src": "x", "effects": [{"name": "Noise", "props": {"Use Color Noise": True}}]}]}))
        self.assertEqual(run("pve/pve.py", "check", str(spec))[0], 1)  # the two-app default does object
        for js in ("recipes_wave.js", "verify_wave.js"):
            cmds = [ln.split("/pve.py check", 1)[1].split() for ln in (pve.HERE / js).read_text(encoding="utf-8").splitlines()
                    if "/pve.py check" in ln]
            self.assertTrue(cmds, js)
            for cmd in cmds:
                flags = [a for a in cmd if a.startswith("--")]
                self.assertEqual(flags, ["--no-ae"], js)
                self.assertEqual(run("pve/pve.py", "check", *flags, str(spec))[0], 0, js)


class Validator(unittest.TestCase):
    def run_validator(self, batch, cards):
        d = tmpdir(self)
        b, c = d / "b.json", d / "c.json"
        b.write_text(json.dumps(batch), encoding="utf-8")
        c.write_text(json.dumps({"cards": cards}), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = validate_cards.main(b, c)
        return rc, buf.getvalue()

    def test_wrong_match_and_frame_rules(self):
        batch = {"kind": "pr_transition", "items": [{"app": "PR", "name": "X", "match": "M", "props": []}]}
        card = {"name": "X", "app": "PR", "kind": "pr_transition", "match": "M", "status": "current", "summary": "s",
                "use_for": ["u"], "confidence": "low"}
        self.assertEqual(self.run_validator(batch, [card])[0], 0)
        rc, out = self.run_validator(batch, [dict(card, match="M_TYPO")])
        self.assertEqual(rc, 1)
        self.assertIn("match 'M_TYPO' should be 'M'", out)
        rc, out = self.run_validator(batch, [dict(card, verified={"verdict": "not_rendered", "frame": "x.png"})])
        self.assertIn("not_rendered but frame", out)


class PipelineStages(unittest.TestCase):
    def test_writers_ignore_help(self):
        d = tmpdir(self)
        with mock.patch.object(build_docs, "HERE", d), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build_docs.main(["--help"]), 0)
        self.assertFalse((d / "PVE.md").exists())
        (d / "a.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(prepare_batches, "OUT", d), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(prepare_batches.main(["--help"]), 0)
            self.assertEqual(prepare_batches.main([]), 1)  # existing batches: refused without --force
        self.assertEqual((d / "a.json").read_text(encoding="utf-8"), "{}")

    def test_corrupt_harvest_keeps_old_batches(self):
        h, out = tmpdir(self), tmpdir(self)
        (h / "ae_effects.json").write_text('[{"name": "Gl', encoding="utf-8")
        (out / "ae_effect_01.json").write_text("old", encoding="utf-8")
        with mock.patch.object(prepare_batches, "H", h), mock.patch.object(prepare_batches, "OUT", out), \
                self.assertRaises(ValueError):
            prepare_batches.main(["--force"])
        self.assertEqual((out / "ae_effect_01.json").read_text(encoding="utf-8"), "old")

    def test_corrupt_harvest_is_one_line_from_the_script(self):
        home = tmpdir(self)
        (home / "harvest").mkdir()
        (home / "harvest" / "ae_effects.json").write_text('[{"name": "Gl', encoding="utf-8")
        (home / "work" / "batches").mkdir(parents=True)
        (home / "work" / "batches" / "ae_effect_01.json").write_text("old", encoding="utf-8")
        rc, out, err = run("pve/prepare_batches.py", "--force", env={"PVE_HOME": str(home)})
        self.assertEqual(rc, 1, out + err)
        self.assertNotIn("Traceback", err)
        self.assertIn("JSONDecodeError", err)
        self.assertEqual((home / "work" / "batches" / "ae_effect_01.json").read_text(encoding="utf-8"), "old")

    def test_force_keeps_the_frozen_batches(self):
        tracked = pve.HERE / "work" / "batches"
        out, home = tmpdir(self), tmpdir(self)
        (home / "cards").mkdir()
        for f in tracked.glob("*.json"):
            shutil.copy(f, out / f.name)
            (home / "cards" / f.name).write_text("{}", encoding="utf-8")
        with mock.patch.object(prepare_batches, "OUT", out), mock.patch.object(prepare_batches, "HERE", home), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(prepare_batches.main(["--force"]), 0)
        self.assertEqual(sorted(f.name for f in out.glob("*.json")), sorted(f.name for f in tracked.glob("*.json")))
        for f in tracked.glob("*.json"):
            self.assertEqual(json.loads((out / f.name).read_text(encoding="utf-8")),
                             json.loads(f.read_text(encoding="utf-8")), f.name)

    def test_force_keeps_a_frozen_kind_whole(self):
        """One frozen batch without its card file is kept with the rest of its kind, not deleted."""
        tracked = pve.HERE / "work" / "batches"
        out, home = tmpdir(self), tmpdir(self)
        (home / "cards").mkdir()
        for f in tracked.glob("*.json"):
            shutil.copy(f, out / f.name)
            if f.name != "pr_audio_04.json":
                (home / "cards" / f.name).write_text("{}", encoding="utf-8")
        with mock.patch.object(prepare_batches, "OUT", out), mock.patch.object(prepare_batches, "HERE", home), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(prepare_batches.main(["--force"]), 0)
        audio = sorted(f.name for f in tracked.glob("pr_audio_*.json"))
        self.assertIn("pr_audio_04.json", audio)
        self.assertEqual(sorted(f.name for f in out.glob("pr_audio_*.json")), audio)
        for n in audio:
            self.assertEqual((out / n).read_bytes(), (tracked / n).read_bytes(), n)

    def test_force_and_home_reach_the_children(self):
        import pipeline
        with mock.patch.object(pipeline.subprocess, "run") as m:
            m.return_value.returncode = 0
            self.assertEqual(pipeline.run("batches", force=True), 0)
        self.assertIn("--force", m.call_args.args[0])
        self.assertEqual(m.call_args.kwargs["env"]["PVE_HOME"], str(pipeline.PVE))

    def test_relative_home_is_one_folder(self):
        d = tmpdir(self)
        rel = f"kb_rel_{os.getpid()}"
        (d / rel / "cards").mkdir(parents=True)
        (d / rel / "cards" / "tools_x.json").write_text(json.dumps({"cards": [
            {"name": "X tool", "app": "both", "kind": "tool", "status": "current", "summary": "s", "use_for": ["u"],
             "confidence": "low"}]}), encoding="utf-8")
        rc, out, err = run(ROOT / "pipeline.py", "run", "docs", cwd=d, env={"PVE_HOME": rel})
        self.assertEqual(rc, 0, out + err)
        self.assertTrue((d / rel / "PVE.md").exists())
        self.assertFalse((ROOT / rel).exists())


class FlashScan(unittest.TestCase):
    def test_flicker_around_the_plate_counts(self):
        plate = [bytes([128] * 12) for _ in range(50)]
        fx = [bytes([235 if i // 2 % 2 else 21] * 12) for i in range(50)]
        sig, area = fs.signal(fx, plate)
        self.assertEqual(area, 1.0)
        self.assertEqual(round(max(sig) - min(sig)), 214)
        self.assertEqual(fs.rate(fs.changes(sig), 25, 50)["max_per_second"], 6)

    def test_nothing_to_measure_is_an_error(self):
        with mock.patch.object(fs, "frames", return_value=[]), self.assertRaises(ValueError):
            fs.scan("x.mp4", 25)
        clip10 = [bytes([16] * 12)] * 75
        with mock.patch.object(fs, "frames", side_effect=[clip10, clip10[:10]]), self.assertRaises(ValueError):
            fs.scan("x.mp4", 25, ref="r.mp4")
        with mock.patch.object(fs, "frames", side_effect=[clip10, clip10[:74]]):
            self.assertEqual(fs.scan("x.mp4", 25, ref="r.mp4")["frames_compared"], 74)  # one frame off is normal

    def test_frame_rate_comes_from_the_clip(self):
        seen = {}
        with mock.patch.object(fs, "clip_fps", return_value=59.94), \
                mock.patch.object(fs, "scan", side_effect=lambda v, fps, *a: seen.setdefault("fps", fps) and {}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(fs.main(["x.mp4"]), 0)
        self.assertEqual(seen["fps"], 60)
        with mock.patch.object(fs, "clip_fps", return_value=None), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(fs.main(["x.mp4"]), 2)


class Bridge(unittest.TestCase):
    def test_notes_with_a_tilde(self):
        aep = (b"AB_ERROR File C:/PROGRA~1/x.ffx not found line 4 ~\x00AB_WARN clip 1: no AE effect 'Glow~2' ~"
               b"\x00AB_ERROR File ~/Documents/y.ffx not found ~")
        self.assertEqual(ab.ae_notes(aep), ["ERROR File C:/PROGRA~1/x.ffx not found line 4",
                                            "WARN clip 1: no AE effect 'Glow~2'",
                                            "ERROR File"])  # " ~" inside a note cuts it short, but it still fails the build

    def test_music_stops_with_the_picture_and_fps_is_checked(self):
        d = tmpdir(self)
        for n in ("a.mp4", "m.mp3"):
            (d / n).write_bytes(b"")
        probe = {"a.mp4": (1920, 1080, 10.0), "m.mp3": (None, None, 30.0)}
        base = {"width": 1920, "height": 1080, "fps": 25, "audio": [{"src": "m.mp3"}]}
        with mock.patch.object(ab, "probe", lambda src: probe[Path(src).name]):
            S = ab.normalize({**base, "clips": [{"src": "a.mp4", "start": 4, "dur": 2}, {"src": "a.mp4", "start": 0, "dur": 2}]}, d)
            self.assertEqual(S["audio"][0]["dur"], 6.0)
            for bad in (0, -30, "30", None):
                with self.assertRaises(SystemExit) as cm:
                    ab.normalize({**base, "fps": bad, "clips": [{"src": "a.mp4", "dur": 2}]}, d)
                self.assertIn("fps", str(cm.exception))
            with self.assertRaises(SystemExit) as cm:
                ab.normalize({**base, "clips": [{"src": "a.mp4", "dur": 2}], "texts": [{"text": "x"}]}, d)
            self.assertIn("start", str(cm.exception))

    def test_mgts_and_aecomps_need_their_keys(self):
        """normalize() names the missing key instead of a KeyError traceback (check_spec keeps checking such fragments'
        effects: tests/test_check_spec.py feeds it mgts / aecomps without file or times)."""
        d = tmpdir(self)
        (d / "a.mp4").write_bytes(b"")
        base = {"width": 1920, "height": 1080, "fps": 25, "clips": [{"src": "a.mp4", "dur": 2}]}
        bad = {"mgts": {"start": 0, "dur": 1}, "aecomps": {"aep": "p.aep", "start": 0, "dur": 1}}
        with mock.patch.object(ab, "probe", lambda src: (1920, 1080, 10.0)):
            for k, x in bad.items():
                with self.assertRaises(SystemExit) as cm:
                    ab.normalize({**base, k: [x]}, d)
                self.assertIn('"file"' if k == "mgts" else '"comp"', str(cm.exception))


    def test_malformed_clip_keeps_the_transition_flash_finding(self):
        clips = [{"src": "a.mp4", "dur": 0.25, "transition": "Flash"} for _ in range(5)]
        for bad in ("oops", None, 3, {"src": "a.mp4", "dur": 0.25, "transition": ["Flash"]}):
            i = pve.check_spec({"fps": 25, "clips": clips + [bad]})
            self.assertTrue(any("4 flash transitions within one second" in x for x in i), (bad, i))
        # clips not a list: the shape issue alone, no second "malformed entry" line from the flash pass
        self.assertEqual(pve.check_spec({"fps": 25, "clips": {"src": "a.mp4"}}), ["clips must be a list, got dict"])
        self.assertEqual(pve.check_spec({"fps": 25, "clips": None}), [])

    def test_aerender_error_exit_with_a_broken_output_fails(self):
        import vfx
        d = tmpdir(self)
        out = d / "shot.mp4"
        out.write_bytes(b"\x00" * 64)  # what a render cut short leaves behind

        class Launcher:  # aerender exited 3 before the output settled
            pid, returncode = 0, 3

            def poll(self):
                return 3

        def render(plays):
            with mock.patch.object(vfx.subprocess, "Popen", lambda *a, **k: Launcher()), \
                    mock.patch.object(vfx.subprocess, "run"), mock.patch.object(vfx.time, "sleep"), \
                    mock.patch.object(vfx, "_plays", lambda f: plays):
                return vfx._aerender(d / "p.aep", {"shot": out}, 0, settle=-1)

        self.assertEqual(render(False), 3)  # error exit + undecodable output = failed render
        self.assertEqual(render(True), 0)   # the lingering-worker case: the launcher erred but the file is whole

    def test_plays_rejects_a_truncated_file(self):
        import vfx
        if not shutil.which("ffprobe"):
            self.skipTest("ffprobe not installed")
        f = tmpdir(self) / "cut.mp4"
        f.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 40)  # header, no moov atom
        self.assertFalse(vfx._plays(f))


class Tools(unittest.TestCase):
    def test_media_appears_only_when_complete(self):
        d = tmpdir(self)
        rel = "example/fake.mp4"

        def ffmpeg(cmd, check=True):
            if "fail" in cmd:
                raise subprocess.CalledProcessError(1, cmd)
            Path(cmd[-1]).write_bytes(b"whole")

        with mock.patch.dict(make_test_media.MEDIA, {rel: ["-i", "x"]}, clear=True), \
                mock.patch.object(make_test_media.subprocess, "run", side_effect=ffmpeg):
            (d / "example").mkdir()
            (d / "example" / "fake.part.mp4").write_bytes(b"half")
            self.assertEqual(make_test_media.main(d), [rel])
            self.assertEqual((d / rel).read_bytes(), b"whole")
            self.assertFalse((d / "example" / "fake.part.mp4").exists())
        with mock.patch.dict(make_test_media.MEDIA, {"example/f2.mp4": ["fail"]}, clear=True), \
                mock.patch.object(make_test_media.subprocess, "run", side_effect=ffmpeg), \
                self.assertRaises(subprocess.CalledProcessError):
            make_test_media.main(d)
        self.assertFalse((d / "example" / "f2.mp4").exists())

    @unittest.skipUnless(HAS_NUMPY, "needs numpy and scipy")
    def test_beds_do_not_depend_on_all(self):
        import numpy as np
        import sfx_synth
        plain, every = tmpdir(self), tmpdir(self)
        out, rng = sfx_synth.OUT, sfx_synth.rng
        self.addCleanup(setattr, sfx_synth, "OUT", out)
        self.addCleanup(setattr, sfx_synth, "rng", rng)
        for d, steps in ((plain, [sfx_synth.beds2]), (every, [sfx_synth.build, sfx_synth.beds2])):
            sfx_synth.OUT, sfx_synth.rng = d, np.random.default_rng(7)  # a fresh process
            for f in steps:
                f()
        for n in ("amb_wind.wav", "amb_sea.wav"):
            self.assertEqual((plain / n).read_bytes(), (every / n).read_bytes(), n)

    @unittest.skipUnless(HAS_NUMPY, "needs numpy")
    def test_grunge_refuses_zero_frames(self):
        import grunge
        with mock.patch.object(grunge.subprocess, "Popen") as p, self.assertRaises(SystemExit):
            grunge.main("x.mov", 0.02)
        p.assert_not_called()


if __name__ == "__main__":
    unittest.main()
