"""Offline checks for adobe_bridge.normalize and the PVE pre-flight (no Adobe apps needed): python test_adobe_bridge.py"""
import json, sys
from pathlib import Path

from adobe_bridge import normalize

EX = Path(__file__).parent / "example"


def exits(spec, needle):
    try:
        normalize(spec, EX)
    except SystemExit as e:
        assert needle in str(e), e
        return
    raise AssertionError(f"expected exit mentioning {needle!r}")


base = {"width": 1080, "height": 1920, "fps": 25}
S = normalize({**base, "clips": [{"src": "still.png", "dur": 2}, {"src": "clip_b.mp4", "start": 2.5, "dur": 1.01},
                                 {"src": "clip_a.mp4"}],
               "audio": [{"src": "music.mp3"}],
               "texts": [{"text": "हि", "start": 0.33, "dur": 1, "presets": ["Text/Animate In/Typewriter.ffx"]}]}, EX)
c1, c2, c3 = S["clips"]
assert c2["start"] == 2.48 and c2["dur"] == 1.0, c2                 # snapped to the 25 fps grid
assert c3["start"] == c2["start"] + c2["dur"] and c3["dur"] == 6.0, c3   # chained; video runs to its last whole frame
assert c1["scale"] == round(100 * max(1080 / 1080, 1920 / 1350), 3)  # cover-fit
assert S["audio"][0]["dur"] == c3["start"] + c3["dur"]                # music stops with the picture (12 s source)
t = S["texts"][0]
assert t["start"] == 0.32 and t["font"] == "NirmalaUI-Bold" and t["indic"] and not t["anim"], t
assert t["presets"][0]["file"].endswith("Typewriter.ffx") and t["presets"][0]["at"] == 0
assert S["duration"] == c3["start"] + c3["dur"]

fx = normalize({**base, "clips": [{"src": "still.png", "effects": ["Tint", {"name": "Gaussian Blur", "props": {"Blurriness": 5}}]}]}, EX)
e1, e2 = fx["clips"][0]["effects"]
assert e1 == {"name": "Tint", "props": {}, "pr_name": "Tint", "pr_props": {}}, e1
assert e2["pr_props"] == {"Blurriness": 5}                            # Premiere params default to the AE ones

f = 1 / 30
T = normalize({"width": 1080, "height": 1920, "fps": 30, "clips": [
    {"src": "clip_a.mp4", "in": 1, "dur": 2, "transition": "Dip to Black"},                  # head: one-sided
    {"src": "still.png", "dur": 2, "transition": "Cross Dissolve"},                         # centred, 7 + 8 frames
    {"src": "clip_b.mp4", "in": 0.1, "dur": 2, "transition": "Cross Dissolve (Legacy)"},    # only 3 frames before
    {"src": "still.png", "dur": 1, "transition_out": {"name": "Dip to White", "dur": 0.8}}]}, EX)
c1, c2, c3, c4 = T["clips"]
near = lambda a, b: abs(a - b) < 1e-6
assert near(c1["trans"][0]["t0"], 0) and near(c1["trans"][0]["t1"], 15 * f), c1["trans"]
assert near(c2["trans"][0]["t0"], 2 - 7 * f) and near(c2["trans"][0]["t1"], 2 + 8 * f), c2["trans"]
assert c2["opacity"] == [[c2["trans"][0]["t0"], 0, c2["trans"][0]["t1"], 100, "smooth"]]    # Premiere 26 curve
assert near(c2["ae_in"], 2 - 7 * f) and near(c1["ae_out"], 2 + 8 * f)                      # crossfade uses handles
assert near(c3["trans"][0]["t0"], 4 - 3 * f) and c3["opacity"][0][4] == "lin"               # trimmed, legacy = linear
assert any("only 11 of 15 frames" in w for w in T["pywarn"]), T["pywarn"]
assert near(c4["trans"][0]["t0"], 6.2) and c4["solids"][0]["above"] and c4["solids"][0]["segs"][0][4] == "in"
exits({"width": 1080, "height": 1920, "fps": 30, "clips": [{"src": "still.png", "transition_out": "Cross Dissolve"},
                                                          {"src": "still.png"}]}, "follows directly")
exits({**base, "clips": []}, "at least one clip")
exits({**base, "clips": [{"src": "nope.mp4"}]}, "missing source")
exits({**base, "clips": [{"src": "still.png", "presets": ["Text/Nope.ffx"]}]}, "preset not found")

# PVE pre-flight addresses repeated AE property names as "Name#n" (header rows don't count), like ae_lib.jsx child()
# and the card validator: every key the validator accepts must pre-flight with the same type and range.
sys.path.insert(0, str(Path(__file__).parent / "pve"))
import pve  # noqa: E402
from prepare_batches import flat_props  # noqa: E402
from validate_cards import leaf_props  # noqa: E402
ck = lambda m, props: pve.check_effects([{"name": m, "props": props}], "AE", "t")  # noqa: E731
assert ck("Card Dance", {"Offset#4": 0}) == [] and ck("APC Vegas", {"Segments": 24}) == []
assert "no property 'Segments#2'" in ck("APC Vegas", {"Segments#2": 1})[0]                 # only one settable one
assert ck("Checkerboard", {"Width": 1000}) == [] and "0..400" in ck("Checkerboard", {"Width#2": 1000})[0]
for e in json.loads((Path(__file__).parent / "pve/harvest/ae_effects.json").read_text(encoding="utf-8")):
    reg = pve.ae_effects().get(e.get("match"), {}).get("props", {})
    for k, spec in leaf_props({"props": flat_props(e.get("props"))}).items():
        assert [reg.get(k, {}).get(x) for x in ("type", "min", "max")] == [spec.get(x) for x in ("type", "min", "max")], \
            (e["name"], k)
print("ok")
