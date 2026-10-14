"""Live wave 3 code, offline: the After Effects dropdown-label harvester (string-table parsing, list filters, the
order-preserving alignment, core-table matching, readback verdicts, text menus), the measuring helpers of
pve/live_tests.py (jitter, track-matte reading, sharpness, FFT / band transfer, cut-off, echoes, pitch, THD, stereo,
blockiness, skew, combing, mapping) and the second test-footage set's commands. No app, network or LLM."""
import math, random, struct, sys, tempfile, unittest, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pve"))
sys.path.insert(0, str(ROOT))
import harvest_enums as he  # noqa: E402
import live_tests as lt  # noqa: E402
import make_test_media  # noqa: E402


def zs(key, n, text):
    return f"$$$/AE/{key}/LStr/{n:04d}={text}".encode() + b"\x00"


class LabelHarvest(unittest.TestCase):
    def test_strings_index_reads_zstrings_lists_names_and_pascal_match_names(self):
        data = (b"\x00\x00" + zs("Foo", 1, "Alpha|Beta|(-|Gamma") + zs("Foo", 2, "Mode") + b"\x0bADBE Foo FX\x00\x00"
                + b"junk|x\x00")
        ix = he.strings(data)
        self.assertEqual([xs for _, xs in ix["lists"]], [["Alpha", "Beta", "(-", "Gamma"]])
        s, _ = ix["lists"][0]
        self.assertEqual((s[1], s[2]), ("Foo", 1))
        self.assertIn("mode", ix["names"])
        self.assertIn("ADBE Foo FX", ix["pascal"])
        self.assertEqual(he.find_binary("ADBE Foo FX", {"a.aex": ix, "b.aex": he.strings(b"\x00nothing\x00")}), ["a.aex"])

    def test_item_filters_keep_menus_and_drop_binary_noise(self):
        self.assertEqual(he.items("Straight Zoom|Fading Zoom", strict=True), ["Straight Zoom", "Fading Zoom"])
        self.assertIsNone(he.items("jXlNj|nBp|rZp", strict=True))
        self.assertIsNone(he.items("t|H", strict=True))
        self.assertEqual(he.items("X|Y|Z"), ["X", "Y", "Z"])  # ZStrings are trusted
        self.assertIsNone(he.items("no pipes here"))
        self.assertIsNone(he.items("%d|%s"))

    def test_unescape_and_norm(self):
        self.assertEqual(he.unescape("Don^}t#{nbsp}stop"), "Don't stop")
        self.assertEqual(he.norm("  Noise   Type "), "noise type")

    def test_alignment_follows_parameter_order_and_skips_sliders(self):
        # Fractal Noise: Fractal Type 20, Noise Type 4, Overflow 4, Complexity 20 (a slider), Blending Mode 21
        res = he.align([20, 4, 4, 20, 21], [[0] * 20, [0] * 4, [1] * 4, [0] * 21])
        self.assertEqual([p for p, _ in res], [[0], [1], [2], [], [3]])
        self.assertFalse(any(skip for _, skip in (res[0], res[1], res[2], res[4])))

    def test_match_effect_on_a_numbered_table(self):
        data = (zs("FN", 1, "Basic|Turbulent") + zs("FN", 2, "Block|Linear|Soft Linear|Spline")
                + zs("FN", 3, "Clip|Soft Clamp|Wrap Back|Allow HDR Results") + zs("FN", 4, "Fractal Type")
                + zs("FN", 5, "Noise Type") + zs("FN", 6, "Overflow"))
        got = he.match_effect(he.strings(data), [("Fractal Type", "Fractal Type", 2), ("Noise Type", "Noise Type", 4),
                                                ("Overflow", "Overflow", 4)])
        self.assertEqual(got["Noise Type"]["labels"], ["Block", "Linear", "Soft Linear", "Spline"])
        self.assertEqual(got["Overflow"]["labels"][0], "Clip")
        self.assertEqual(got["Fractal Type"]["confidence"], "high")

    def test_plain_strings_take_only_a_unique_or_name_adjacent_list(self):
        data = b"\x00".join([b"Composite|Screen|Add", b"Mode A", b"Birth|Current|From Start", b"Filler one",
                             b"Filler two", b"Filler three", b"Texture Time", b"Only|Four|Items|Here", b""])
        ix = he.strings(data)
        got = he.match_effect(ix, [("T", "Texture Time", 3), ("Q", "Quality", 3), ("F", "Four", 4)])
        self.assertEqual(got["F"]["labels"], ["Only", "Four", "Items", "Here"])  # the one 4-item list
        self.assertIsNone(got["Q"]["labels"])  # two 3-item lists, no name next to either
        self.assertEqual(len(got["Q"]["candidates"]), 2)
        data2 = data.replace(b"Filler one\x00Filler two\x00Filler three\x00", b"")
        got2 = he.match_effect(he.strings(data2), [("T", "Texture Time", 3)])
        self.assertEqual(got2["T"]["labels"], ["Birth", "Current", "From Start"])  # now right beside its name

    def test_core_table_matches_by_name_and_count(self):
        data = (b"$$$/AE/BEE/LStr/0696=Characters|Characters Excluding Spaces|Words|Lines\x00"
                b"$$$/AE/BEE/LStr/0697=Based On\x00$$$/AE/BEE/LStr/0721=Square|Ramp Up|Ramp Down|Triangle|Round|Smooth\x00"
                b"$$$/AE/BEE/LStr/0722=Shape\x00")
        ix = he.strings(data)
        self.assertEqual(he.match_popup(ix, "Based On", 4)["labels"][1], "Characters Excluding Spaces")
        self.assertEqual(he.match_popup(ix, "Shape", 6)["distance"], 1)
        self.assertIsNone(he.match_popup(ix, "Shape", 5))

    def test_readback_verdicts(self):
        pop = {"linear_ok": False, "api_min": 1, "api_max": 3, "settable": [1, 2, 3]}
        self.assertEqual(he.verdict(pop), "popup")
        self.assertEqual(he.verdict({**pop, "linear_ok": True}), "slider")
        self.assertEqual(he.verdict({**pop, "settable": [1, 2]}), "unclear")
        self.assertEqual(he.verdict({"error": "x"}), "unprobed")
        self.assertEqual(he.verdict({"parameters": ["A", "B"], "linear_ok": True}), "popup")  # the API lists a menu
        self.assertEqual(he.verdict({"parameters_error": "Error: This property does not have dropdown menu options.",
                                     "linear_ok": False}), "slider")

    def test_api_labels_confirm_or_replace_the_table(self):
        rec = {"count": 3, "labels": {"1": "A", "2": "(-", "3": "C"}}
        self.assertEqual(he.api_labels(rec, {"parameters": ["A", "(-", "C"]}), "api_confirms_table")
        self.assertIn("propertyParameters", rec["confirmed_by"])
        rec2 = {"count": 2, "labels": {"1": "X", "2": "Y"}, "confidence": "medium"}
        self.assertEqual(he.api_labels(rec2, {"parameters": ["Multiply", "Screen"]}), "api_differs_from_table")
        self.assertEqual((rec2["labels"], rec2["string_table_labels"], rec2["confidence"]),
                         ({"1": "Multiply", "2": "Screen"}, {"1": "X", "2": "Y"}, "high"))
        rec3 = {"count": 2, "labels": None, "unmatched": "x"}
        self.assertEqual(he.api_labels(rec3, {"value_texts": {"1": "Low", "2": "High"}}), "api_only")
        self.assertNotIn("unmatched", rec3)
        self.assertEqual(he.api_labels({"count": 3, "labels": None}, {"parameters": ["a"]}), "api_labels_missing")

    def test_candidates_carry_match_paths_and_repeat_suffixes(self):
        e = {"props": [{"name": "Mode", "match": "M-1", "kind": "prop", "type": "1d", "min": 1, "max": 3, "default": 1},
                       {"name": "G", "match": "G-1", "kind": "group", "props": [
                           {"name": "Mode", "match": "M-2", "type": "1d", "min": 1, "max": 2, "default": 1},
                           {"name": "Mode", "match": "M-3", "type": "1d", "min": 1, "max": 2, "default": 2}]},
                       {"name": "Amount", "match": "A", "type": "1d", "min": 0, "max": 100, "default": 5}]}
        c = he.popup_candidates(e)
        self.assertEqual([q for q, _ in c], ["Mode", "G/Mode", "G/Mode#2"])
        self.assertEqual(c[2][1]["mpath"], ["G-1", "M-3"])

    def test_text_menus_from_a_readback(self):
        core = {"BEE.dll": he.strings(b"$$$/AE/BEE/LStr/0674=Percentage|Index\x00$$$/AE/BEE/LStr/0675=Units\x00")}
        rb = {"text": [{"names": ["Animators", "Animator 1", "Selectors", "Range Selector 1", "Advanced", "Units"],
                        "name": "Units", "match": "ADBE Text Range Units", "api_min": 1, "api_max": 2, "linear_ok": False,
                        "settable": [1, 2]},
                       {"names": ["X"], "name": "Correlation", "match": "c", "api_min": 0, "api_max": 100, "linear_ok": False}]}
        out = he.text_menus(core, rb)
        self.assertEqual(list(out), ["Animators/Animator 1/Selectors/Range Selector 1/Advanced/Units"])
        self.assertEqual(list(out.values())[0]["labels"], {"1": "Percentage", "2": "Index"})


def gray(w, h, fn):
    return bytes(max(0, min(255, int(fn(x, y)))) for y in range(h) for x in range(w))


def texture(x, y):
    return 128 + 60 * math.sin(x * 0.7) + 40 * math.sin(y * 0.45 + x * 0.1)


class FrameMeasures(unittest.TestCase):
    def test_shift_finds_subpixel_offsets(self):
        a = [math.sin(i * 0.3) * 50 for i in range(200)]
        b = [math.sin((i + 3.4) * 0.3) * 50 for i in range(200)]
        d, err = lt.shift(a, b, 10)
        self.assertAlmostEqual(d, 3.4, delta=0.25)

    def test_jitter_separates_shake_from_a_still(self):
        w, h = 64, 36
        still = [gray(w, h, texture)] * 6
        offs = [0, 3, -2, 4, -3, 1]
        shaky = [gray(w, h, lambda x, y, o=o: texture(x + o, y)) for o in offs]
        self.assertLess(lt.jitter(still, w, h, 6)["rms_px"], 0.05)
        j = lt.jitter(shaky, w, h, 8)
        self.assertGreater(j["rms_px"], 2)
        self.assertEqual([round(x) for x in j["dx"]], [b - a for a, b in zip(offs, offs[1:])])

    def test_track_matte_regions_and_reading(self):
        w, h = 192, 108
        fg, bg = gray(w, h, lambda x, y: 200), gray(w, h, lambda x, y: 30)
        comp = gray(w, h, lambda x, y: 200 if math.hypot(x - 50, y - 54) < 26 else 30)
        reg = {k: lt.which_layer(comp, fg, bg, w, b) for k, b in lt.TMK_BOXES.items()}
        self.assertEqual(reg, {"left_disc": "fg", "right_disc": "bg", "corner": "bg"})
        self.assertTrue(lt.tmk_reading(reg).startswith("V1"))
        self.assertTrue(lt.tmk_reading({"left_disc": "fg", "right_disc": "fg", "corner": "fg"}).startswith("foreground every"))

    def test_analysis_banner_detector(self):
        w, h = 40, 100
        plain = bytes([90, 120, 140] * w * h)
        band = bytearray(plain)
        for y in range(42, 45):
            for x in range(w):
                band[(y * w + x) * 3:(y * w + x) * 3 + 3] = bytes([200, 80, 80] if x % 4 < 2 else [150, 150, 150])
        self.assertEqual(lt.banner(plain, w, h), 0)
        self.assertGreater(lt.banner(bytes(band), w, h), 0.3)

    def test_specks_and_wire_contrast(self):
        w, h = 900, 120
        clean = bytearray(gray(w, h, lambda x, y: 180))
        for y in range(h):  # the dust_wire line: x 700 at the top to 760 at the bottom
            x = round(700 + 60 * y / h)
            for dx in (-1, 0, 1, 2):  # 4 px wide, as on the plate
                clean[y * w + x + dx] = 40
        dusty = bytearray(clean)
        for x, y in ((100, 20), (300, 50), (500, 90)):
            dusty[y * w + x] = 255 if x != 300 else 0
        self.assertEqual(lt.specks(bytes(clean), w, h), 0)
        self.assertEqual(lt.specks(bytes(dusty), w, h), 3)
        self.assertGreater(lt.wire_contrast(bytes(clean), w, h), 100)
        self.assertEqual(lt.wire_contrast(gray(w, h, lambda x, y: 180), w, h), 0)

    def test_sharpness_drops_on_blur(self):
        w, h = 48, 32
        sharp = gray(w, h, lambda x, y: 255 * ((x // 4 + y // 4) % 2))
        soft = gray(w, h, lambda x, y: 128 + 40 * math.sin(x * 0.4))
        self.assertGreater(lt.sharpness(sharp, w, (0, 0, w, h)), lt.sharpness(soft, w, (0, 0, w, h)))

    def test_blockiness_skew_and_combing(self):
        w, h = 96, 48
        blocky = gray(w, h, lambda x, y: 100 + 20 * ((x // 8) % 3) + 0.2 * (x % 8))
        smooth = gray(w, h, lambda x, y: 100 + 40 * math.sin(x * 0.3))
        self.assertGreater(lt.blockiness(blocky, w, h, 8), 5)
        self.assertLess(lt.blockiness(smooth, w, h, 8), 1.5)
        sheared = gray(w, h, lambda x, y: texture(x - 6 * y / h, 0))
        self.assertAlmostEqual(lt.skew(sheared, w, h, band=0.15, lim=12), -5.1, delta=1.2)
        combed = gray(w, h, lambda x, y: texture(x + (4 if y % 2 else 0), 0))
        prog = gray(w, h, lambda x, y: texture(x, y * 0.2))
        self.assertGreater(lt.combing(combed, w, h), 3 * lt.combing(prog, w, h))


class AudioMeasures(unittest.TestCase):
    R = 8000

    def sine(self, f, a=0.5, n=8000):
        return [a * math.sin(2 * math.pi * f * i / self.R) for i in range(n)]

    def test_fft_matches_a_direct_dft(self):
        x = [random.Random(1).uniform(-1, 1) for _ in range(16)]
        X = lt.fft(x)
        for k in (0, 3, 7):
            d = sum(x[n] * complex(math.cos(-2 * math.pi * k * n / 16), math.sin(-2 * math.pi * k * n / 16)) for n in range(16))
            self.assertAlmostEqual(abs(X[k] - d), 0, places=9)

    def test_band_transfer_and_cutoff(self):
        b = lt.bands(self.sine(1000), self.R, n=1024, frames=4)
        self.assertEqual(lt.THIRD[b.index(max(b))], 1000.0)
        hp = [-40 if f < 400 else (-3 if f < 600 else 0) for f in lt.THIRD]
        self.assertTrue(400 <= lt.cutoff(hp, "highpass") <= 640)
        lp = [0 if f < 2000 else -30 for f in lt.THIRD]
        self.assertTrue(1500 <= lt.cutoff(lp, "lowpass") <= 2600)
        self.assertEqual(lt.extreme([0, 5, -9, 1] + [0] * 26, "notch"), (lt.THIRD[2], -9))
        self.assertEqual(lt.band_at(list(range(30)), 1000), lt.THIRD.index(1000.0))

    def test_levels_pitch_thd(self):
        x = self.sine(440, a=0.5)
        self.assertAlmostEqual(lt.peak_db(x), -6.02, delta=0.05)
        self.assertAlmostEqual(lt.rms_db(x), -9.03, delta=0.05)
        self.assertAlmostEqual(lt.tone_freq(x, self.R), 440, delta=0.5)
        self.assertLess(lt.thd(x, self.R, 440), 0.5)
        clipped = [max(-0.2, min(0.2, v)) for v in x]
        self.assertGreater(lt.thd(clipped, self.R, 440), 10)

    def test_echoes_find_a_delay_and_decay(self):
        n = self.R
        x = [0.0] * n
        x[400], x[400 + 2400] = 0.9, 0.3  # an echo 0.3 s later, -9.5 dB
        e = lt.echoes(x, self.R)
        self.assertAlmostEqual(e["echoes"][0][0], 0.3, delta=0.006)
        self.assertAlmostEqual(e["echoes"][0][1], -9.5, delta=0.5)
        decay = [0.9 * math.exp(-i / 800) * (1 if i % 2 else -1) for i in range(n)]
        self.assertAlmostEqual(lt.echoes(decay, self.R)["t30_s"], 0.35, delta=0.05)  # 30 dB = 3.45 time constants

    def test_energy_decay_t30_survives_a_late_tail(self):
        n = self.R
        x = [0.0] * n
        x[400] = 0.9
        for i in range(2000, n):  # a quiet gap, then a tail that carries most of the energy left
            x[i] = 0.05 * math.exp(-(i - 2000) / 400) * (1 if i % 2 else -1)
        t = lt.edc_t30(x, self.R, 400 / self.R)
        self.assertGreater(t, 0.25)  # past the gap (0.2 s) into the tail, not 5 ms after the click
        self.assertIsNone(lt.edc_t30([0.0] * 100, self.R, 0))

    def test_stereo_identifies_swapped_and_inverted_channels(self):
        L, R = self.sine(440), self.sine(1000, 0.1)
        s = lt.stereo([R, L], [L, R])
        self.assertAlmostEqual(s["L_vs_dryR"], 1, places=3)
        self.assertAlmostEqual(s["R_vs_dryL"], 1, places=3)
        self.assertAlmostEqual(lt.stereo([[-v for v in L], R], [L, R])["L_vs_dryL"], -1, places=3)

    def test_fit_line_interpret_and_mapping(self):
        self.assertEqual(lt.fit_line([0, 1, 2], [1, 3, 5])[:2], (2.0, 1.0))
        self.assertIsNone(lt.fit_line([1], [2]))
        self.assertEqual(lt.interpret("gain", {"rms_db": -12.0}, {"rms_db": -18.0}), {"gain_db": 6.0})
        self.assertEqual(lt.interpret("pitch", {"freq_hz": 2000.0}, {})["semitones"], 12.0)
        rows = [{"effect": "Amplify", "set": {"Gain": v}, "measure": "gain", "result": {"gain_db": 30 * v - 20}}
                for v in (0, 0.5, 1)]
        m = lt.mapping(rows)
        self.assertEqual(m["Amplify | Gain | gain"]["line"][:2], (30.0, -20.0))

    def test_tests_table_reaches_stereo_only_effects_through_the_dump(self):
        t = lt.tests_table({"Balance": ["", "Balance", "Level"]})
        bal = [x for x in t if x[0] == "Balance"]
        self.assertTrue(any("Balance" in s for _, s, _, _ in bal))
        self.assertTrue(all(sig == "split" for _, _, sig, _ in bal))
        self.assertEqual({fx for fx, *_ in t} >= {"Amplify", "Highpass", "Invert", "Swap Channels"}, True)

    def test_read_wav_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "t.wav")
            with wave.open(str(p), "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(48000)
                wf.writeframes(b"".join(struct.pack("<hh", 16384, -8192) for _ in range(10)))
            rate, ch = lt.read_wav(p)
        self.assertEqual((rate, len(ch), len(ch[0])), (48000, 2, 10))
        self.assertAlmostEqual(ch[0][0], 0.5)
        self.assertAlmostEqual(ch[1][0], -0.25)


class SecondFootageSet(unittest.TestCase):
    def test_every_documented_plate_has_a_command(self):
        names = {k.rsplit("/", 1)[1] for k in make_test_media.MEDIA if k.startswith("pve/media/set2/")}
        self.assertEqual(names, {"handheld_shake.mp4", "rolling_shutter_pan.mp4", "interlaced_25i.mp4", "archive_4x3.mp4",
                                 "vfr_phone.mp4", "locked_off_motion.mp4", "noisy_lowlight.mp4", "presenter_hair.mp4",
                                 "talking_head.mp4", "dust_wire.mp4"})
        for n in names:
            self.assertIn(n, make_test_media.__doc__)

    def test_steps_substitute_scratch_and_root(self):
        cmds = make_test_media.commands("pve/media/set2/locked_off_motion.mp4", "/r")
        self.assertEqual(len(cmds), 2)
        self.assertTrue(cmds[0][-1].endswith("_locked_off_motion_tmp.png"))
        self.assertIn(cmds[0][-1], cmds[1])
        self.assertTrue(cmds[1][-1].endswith("locked_off_motion.mp4"))
        noisy = make_test_media.commands("pve/media/set2/noisy_lowlight.mp4", "/r")[0]
        self.assertIn("/r/pve/media/set2/locked_off_motion.mp4", noisy)
        self.assertLess(list(make_test_media.MEDIA).index("pve/media/set2/locked_off_motion.mp4"),
                        list(make_test_media.MEDIA).index("pve/media/set2/noisy_lowlight.mp4"))

    def test_blurred_frames_follow_the_plate_recipe(self):
        self.assertEqual(lt.blurred_frames(14), [1, 5, 9, 12, 13])
        self.assertIn("n%4==1", make_test_media.__doc__)


if __name__ == "__main__":
    unittest.main()
