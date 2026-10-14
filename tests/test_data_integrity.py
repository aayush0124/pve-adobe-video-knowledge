"""The knowledge base itself: every card validates against the batch it was written from, the batches agree with the
harvest, recipes pre-flight clean, enum values are legal, nothing is duplicated."""
import contextlib, io, json, shutil, sys, tempfile, unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pve"))
import prepare_batches  # noqa: E402
import pve  # noqa: E402
import validate_cards  # noqa: E402

HOME = pve.HERE
BATCHES = sorted((HOME / "work" / "batches").glob("*.json"))
CARD_FILES = sorted((HOME / "cards").glob("*.json"))
KINDS = {"ae_effect", "pr_effect", "pr_transition", "pr_audio", "ae_preset", "pr_preset", "lut", "mogrt", "tool", "recipe"}
# prepare_batches.py has changed since the Premiere batches were cut (a field rename, 15 instead of 16 transitions per
# batch): those batches are frozen as the agents saw them, so only their item names are compared with a fresh split.
FROZEN = {"pr_effect", "pr_audio", "pr_transition"}


def items(files):
    out = {}
    for f in files:
        b = json.loads(f.read_text(encoding="utf-8"))
        out.setdefault(b["kind"], []).extend(b["items"])
    return out


class Cards(unittest.TestCase):
    def test_every_batch_has_a_valid_card_file(self):
        self.assertGreater(len(BATCHES), 90)
        bad = []
        for b in BATCHES:
            c = HOME / "cards" / b.name
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = validate_cards.main(b, c) if c.exists() else 1
            if rc:
                bad.append(f"{b.name}: " + " | ".join(buf.getvalue().splitlines()[:3]))
        self.assertEqual(bad, [])

    def test_card_files_parse(self):
        for f in CARD_FILES:
            d = json.loads(f.read_text(encoding="utf-8"))
            if f.name != "zz_critic.json":
                self.assertIsInstance(d.get("cards"), list, f.name)

    def test_enum_values(self):
        bad = []
        for c in pve.cards():
            v = (c.get("verified") or {}).get("verdict")
            status_ok = c.get("status") in validate_cards.STATUS or (c.get("kind") == "tool" and c.get("status") == "beta")
            if c.get("kind") not in KINDS or c.get("app") not in ("AE", "PR", "both") or not status_ok \
                    or c.get("confidence") not in ("high", "medium", "low") or (v is not None and not pve.verdict_meaning(v)):
                bad.append((c.get("kind"), c.get("name"), c.get("app"), c.get("status"), c.get("confidence"), v))
            missing = [k for k in validate_cards.REQUIRED if not c.get(k)]
            if missing:
                bad.append((c.get("name"), "missing", missing))
        self.assertEqual(bad, [])

    def test_no_duplicates_within_a_file(self):
        """Harvested kinds are covered by the validator (one card per batch item). Tool and recipe names repeat only
        across files (the same feature seen from two topics), never inside one."""
        dups = []
        for f in CARD_FILES:
            if f.name.startswith(("tools_", "recipe_")):
                n = Counter(c.get("name") for c in json.loads(f.read_text(encoding="utf-8"))["cards"])
                dups += [(f.name, k) for k, v in n.items() if v > 1]
        self.assertEqual(dups, [])

    def test_recipes_preflight_clean(self):
        bad = []
        for r in (c for c in pve.cards() if c.get("kind") == "recipe"):
            bad += [(r["name"], i) for i in pve.check_effects(r.get("premiere"), "PR", "premiere")
                    + pve.check_effects(r.get("after_effects"), "AE", "after_effects")]
        self.assertGreater(sum(1 for c in pve.cards() if c.get("kind") == "recipe"), 100)
        self.assertEqual(bad, [])


class Batches(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = Path(tempfile.mkdtemp())
        out, prepare_batches.OUT = prepare_batches.OUT, tmp
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                prepare_batches.main()
            cls.fresh = items(sorted(tmp.glob("*.json")))
            cls.fresh_files = {f.name: f.read_text(encoding="utf-8") for f in tmp.glob("*.json")}
        finally:
            prepare_batches.OUT = out
            shutil.rmtree(tmp, ignore_errors=True)
        cls.tracked = items(BATCHES)

    def test_same_kinds(self):
        self.assertEqual(set(self.fresh), set(self.tracked))

    def test_batches_match_the_harvest(self):
        for kind in self.tracked:
            if kind in FROZEN:
                self.assertEqual(Counter(i["name"] for i in self.fresh[kind]), Counter(i["name"] for i in self.tracked[kind]), kind)
            else:
                self.assertEqual(self.fresh[kind], self.tracked[kind], kind)
                for b in (b for b in BATCHES if b.name.startswith(kind + "_")):  # same chunking: cards are per file
                    self.assertEqual(json.loads(self.fresh_files[b.name]), json.loads(b.read_text(encoding="utf-8")), b.name)

    def test_every_harvested_effect_has_a_card(self):
        names = Counter((c.get("kind"), c.get("name")) for c in pve.cards())
        for kind, its in self.tracked.items():
            missing = [i["name"] for i in its if not names[(kind, i["name"])]]
            self.assertEqual(missing, [], kind)


def run_validator(batch, cards):
    """validate_cards.main on throw-away files -> (exit code, printed lines)."""
    with tempfile.TemporaryDirectory() as d:
        b, c = Path(d, "b.json"), Path(d, "c.json")
        b.write_text(json.dumps(batch), encoding="utf-8")
        c.write_text(json.dumps({"cards": cards}), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = validate_cards.main(b, c)
    return rc, buf.getvalue().splitlines()


class VerdictModel(unittest.TestCase):
    """corrected_unverified / untested:<cause>: legal, used, counted, and never at the cost of the old evidence."""

    def test_new_states_are_used(self):
        """Every documented untested cause is in use now or kept in a verdict's history, none undocumented; a card
        re-judged after a correction keeps the corrected_unverified step and the verdict before it."""
        hist = [[p.get("verdict") for p in (c.get("verified") or {}).get("previous") or []] + [(c.get("verified") or {}).get("verdict")]
                for c in pve.cards()]
        seen = Counter(v for h in hist for v in h)
        used = {k.split(":", 1)[1] for k in seen if (k or "").startswith("untested:")}
        self.assertEqual(used, set(pve.UNTESTED))
        self.assertGreater(seen["corrected_unverified"], 50)
        for h in hist:
            for i, v in enumerate(h[:-1]):
                if v == "corrected_unverified":
                    self.assertIn(h[i - 1] if i else None, ("wrong", "weak"), h)

    def test_changed_verdicts_keep_their_history(self):
        for c in pve.cards():
            v = c.get("verified") or {}
            if v.get("verdict") == "corrected_unverified" or (v.get("verdict") or "").startswith("untested:"):
                prev = v.get("previous") or []
                self.assertTrue(prev and all(p.get("verdict") and p.get("changed") and p.get("why") for p in prev), c["name"])
                if v["verdict"] == "corrected_unverified":
                    self.assertIn(prev[-1]["verdict"], ("wrong", "weak"), c["name"])

    def test_after_effects_halves(self):
        """verified.ae (a recipe's After Effects half): only on recipes with an After Effects stack, legal, and a
        corrected half keeps the render's verdict."""
        halves = [(c, c["verified"]["ae"]) for c in pve.cards() if (c.get("verified") or {}).get("ae")]
        self.assertGreater(len(halves), 100)
        for c, ae in halves:
            self.assertEqual(c["kind"], "recipe", c["name"])
            self.assertTrue(c.get("after_effects") or c.get("spec") or c.get("presets"), c["name"])
            self.assertTrue(pve.verdict_meaning(ae["verdict"]), (c["name"], ae["verdict"]))
            self.assertTrue(ae.get("note"), c["name"])
            if ae["verdict"] == "corrected_unverified":
                self.assertIn(ae["previous"][-1]["verdict"], ("wrong", "weak"), c["name"])
                self.assertTrue(ae["previous"][-1]["why"], c["name"])

    def test_counted_by_stats_and_status(self):
        s = pve.stats()["verdicts"]
        self.assertTrue(any(k.startswith("untested:") for k in s))
        self.assertEqual(sum(s.values()), len(pve.cards()))
        sys.path.insert(0, str(ROOT))
        import pipeline  # noqa: E402
        row = pipeline.verdict_tally(pve.cards())
        self.assertIn("pass", row)
        self.assertRegex(row, r"untested \d+ \(.*needs_setup \d+")

    def test_validator_rejects_illegal_verdicts(self):
        batch = {"kind": "pr_transition", "items": [{"app": "PR", "name": "X", "props": []}]}
        card = {"name": "X", "app": "PR", "kind": "pr_transition", "status": "current", "summary": "s",
                "use_for": ["u"], "confidence": "low"}
        for v, ok in (("pass", True), ("corrected_unverified", True), ("untested:gpu_only", True),
                      ("untested:bogus", False), ("works", False)):
            rc, out = run_validator(batch, [dict(card, verified={"verdict": v})])
            self.assertEqual(rc == 0, ok, (v, out))
        rc, out = run_validator(batch, [dict(card, verified={"verdict": "pass", "previous": [{"verdict": "fine"}]})])
        self.assertEqual(rc, 1, out)

    def test_validator_checks_flash_fields(self):
        batch = {"kind": "pr_effect", "items": [{"app": "PR", "name": "X", "match": "M",
                                                 "props": [{"name": "Rate", "type": "number", "min": 0, "max": 10}]}]}
        card = {"name": "X", "app": "PR", "kind": "pr_effect", "match": "M", "status": "current", "summary": "s",
                "use_for": ["u"], "confidence": "low", "demo_settings": {"Rate": 1}}
        self.assertEqual(run_validator(batch, [dict(card, flash={"period": "Rate", "basis": "render"})])[0], 0)
        for bad in ({"period": "Rate"}, {"period": "Nope", "basis": "b"}, {"rate": -1, "basis": "b"},
                    {"per_use": 1.5, "basis": "b"}, {"speed": 2, "basis": "b"}):
            self.assertEqual(run_validator(batch, [dict(card, flash=bad)])[0], 1, bad)


class Duplicates(unittest.TestCase):
    def test_one_card_per_item(self):
        """Same kind + app + name is allowed only for distinct items (different match name or folder)."""
        groups = {}
        for c in pve.cards():
            groups.setdefault((c.get("kind"), c.get("app"), c.get("name")), []).append(c)
        bad = []
        for k, cs in groups.items():
            ids = [(c.get("match"), c.get("category")) for c in cs]
            if len(cs) > 1 and (k[0] in ("tool", "recipe") or len(set(ids)) < len(ids)):
                bad.append(k)
        self.assertEqual(bad, [])

    def test_no_repeated_match_name(self):
        """A repeated app + match name is a duplicate card. (The mono harvest once gave seven stereo-only Premiere audio
        effects the clip Volume component's match name; live wave 3's stereo dump replaced those rows.)"""
        n = Counter((c.get("app"), c.get("match")) for c in pve.cards() if c.get("match"))
        self.assertEqual([k for k, v in n.items() if v > 1], [])

    def test_stereo_audio_rows_come_from_the_stereo_dump(self):
        dump = {e["name"]: e for e in json.loads((HOME / "harvest" / "pr_audio_params_w3.json").read_text(encoding="utf-8"))["effects"]}
        rows = {i["name"]: i for i in items(b for b in BATCHES if b.name.startswith("pr_audio_"))["pr_audio"]}
        for n in ("Channel Mixer", "Balance", "Swap Channels", "Fill Left with Right", "Fill Right with Left",
                  "Channel Volume", "Stereo Expander"):
            self.assertEqual(rows[n]["match"], dump[n]["match"], n)
            self.assertEqual([p["name"] for p in rows[n]["props"]], [p["name"] for p in dump[n]["props"]], n)
            self.assertEqual(pve.card(dump[n]["match"])["name"], n)  # the card carries the real match name

    def test_merged_stubs_point_at_their_card(self):
        stubs = [(f.name, c) for f in CARD_FILES if f.name != "zz_critic.json"
                 for c in json.loads(f.read_text(encoding="utf-8"))["cards"] if c.get("merged_into")]
        self.assertGreaterEqual(len(stubs), 5)
        for f, s in stubs:
            there = json.loads((HOME / "cards" / s["merged_into"]).read_text(encoding="utf-8"))["cards"]
            hit = [c for c in there if (c["kind"], c["name"], c.get("match")) == (s["kind"], s["name"], s.get("match"))
                   and not c.get("merged_into")]
            self.assertEqual(len(hit), 1, (f, s["name"]))
            self.assertTrue(hit[0].get("merged") and hit[0].get("merged_from"), s["name"])  # the evidence travelled

    def test_card_warns_on_several_matches(self):
        with self.assertWarns(pve.AmbiguousCard) as w:
            self.assertEqual(pve.card("Spin In")["name"], "Spin In")
        self.assertIn("2 cards match", str(w.warning))
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning fails these
            self.assertEqual(pve.card("Text/Rotation/Spin In")["category"], "Text/Rotation")
            self.assertEqual(pve.card("Noise", "PR")["match"], "AE.ADBE Noise2")  # merged: one card left
            self.assertEqual(pve.card("Dip to White (Legacy)")["flash"]["per_use"], 1)

    def test_validator_rejects_a_dangling_stub(self):
        batch = {"kind": "pr_transition", "items": [{"app": "PR", "name": "X", "props": []}]}
        rc, out = run_validator(batch, [{"name": "X", "app": "PR", "kind": "pr_transition", "match": None,
                                         "merged_into": "no_such_file.json"}])
        self.assertEqual(rc, 1, out)


if __name__ == "__main__":
    unittest.main()
