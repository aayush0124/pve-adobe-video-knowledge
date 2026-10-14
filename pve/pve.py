"""PVE ("professional video editor"): what After Effects 2026 and Premiere Pro 2026 can do, harvested from the
installed apps, render-tested, and written up as knowledge cards. The edit system reads it to choose effects and to
check specs before anything is sent to an app.

    python pve/pve.py find "film grain"            search cards (name, summary, look, use_for)
                      [--kind K] [--app AE|PR|both] [--verdict pass|untested|...]
    python pve/pve.py card "Camera Shake" [PR|AE]   one card, in full (name, match name or "Category/Name";
                                                    warns on stderr when several cards match)
    python pve/pve.py check spec.json               validate an AdobeBridge spec (clips/mgts/aecomps effects)
    python pve/pve.py scenes scenes.json            validate After Effects VFX scenes (vfx.py input)
    python pve/pve.py stats                         what PVE knows

    from pve import pve; pve.check_spec(spec) -> [issues]; pve.find("glitch")

Issues that start with "check by eye" are advisory: `check` / `scenes` exit 1 only on the others.
"""
import json, os, re, sys, warnings
from collections import Counter
from functools import lru_cache
from pathlib import Path

HERE = Path(os.environ.get("PVE_HOME") or Path(__file__).resolve().parent)  # the folder holding cards/ and harvest/

# Render verdicts (card["verified"]["verdict"]). Each says what the test render showed about the CARD, never more.
VERDICTS = {
    "pass": "matched its render: the test frame showed what the card says. Not a promise that the effect suits a job "
            "(an unusable transition can pass by rendering its failure, a shake can pass on one still frame)",
    "weak": "it showed, but too faintly at the tested values to judge",
    "wrong": "the card disagreed with its render and has not been rewritten yet",
    "broken": "the render showed nothing usable (no change, black, garbage or a refused export) and the test setup "
              "was not shown to be the cause",
    "not_rendered": "never rendered: no test frame exists",
    "corrected_unverified": "the card disagreed with its render, then its text or demo values were changed: neither "
                            "the old verdict nor the corrected card has been checked against a render",
}
UNTESTED = {  # "untested:<cause>": the test harness could not exercise it, so the render says nothing about the effect
    "gpu_only": "it draws only with GPU acceleration; the CPU-only test render showed the 'requires GPU acceleration' "
                "slate",
    "needs_analysis": "the test rendered before its background analysis (stabilise / camera solve) had finished",
    "needs_second_layer": "it needs a second layer, a stack or a second instance that the one-effect test does not build",
    "needs_second_track": "it needs a matte or clip on another track (or a real edit) that the one-clip test does not "
                          "build",
    "no_suitable_footage": "the test footage lacks what it acts on (camera shake, rolling-shutter skew, a chroma screen, "
                           "an alpha edge, interlacing, a frame-rate mismatch, a low-res or blocky source)",
    "needs_setup": "it needs a setup the harness does not script (layer markers, a text or shape host, matching copy, "
                   "a stroke, several lines, a modal-dialog choice, a loaded LUT, a linked expression, a smaller layer)",
    "audio_only": "it makes only sound, which a still frame cannot show",
}
VERDICT_RANK = {"pass": 0.5, "broken": -0.5}  # find() tie-breaks; text relevance still decides
FLASH_LIMIT = 3  # flashes in any 1-second window (Harding / ITU-R BT.1702; saturated red flashes count too)
ADVISORY = "check by eye"  # prefix of advisory issues: they never fail `check` / `scenes` or pipeline validate


def verdict_meaning(v):
    """Legend text for a verdict, or None when the value is not a legal verdict."""
    if v in VERDICTS:
        return VERDICTS[v]
    cause = v[len("untested:"):] if isinstance(v, str) and v.startswith("untested:") else None
    return f"untested: {UNTESTED[cause]}" if cause in UNTESTED else None


def advisory(issue):
    return issue.startswith(ADVISORY)


def _verdict(c):
    return (c.get("verified") or {}).get("verdict")


@lru_cache(None)
def cards():
    """Every card. A stub with "merged_into" (a duplicate harvest row whose card was merged into another) is skipped."""
    out = []
    for f in sorted((HERE / "cards").glob("*.json")):
        try:
            out += [c for c in json.loads(f.read_text(encoding="utf-8")).get("cards", []) if not c.get("merged_into")]
        except Exception:  # noqa: BLE001  (a card file mid-write is skipped, not fatal)
            pass
    return out


@lru_cache(None)
def ae_effects():
    """match name -> {name, category, props: {path (#n for repeats): spec}} from the live harvest. Header rows are
    skipped and don't count towards #n, as in ae_lib.jsx child() and validate_cards.leaf_props."""
    p = HERE / "harvest" / "ae_effects.json"
    out = {}
    if not p.exists():
        return out

    def flat(props, prefix="", res=None, seen=None):
        res, seen = ({}, {}) if res is None else (res, seen)
        for q in props or []:
            if q.get("match") == "ADBE Effect Built In Params":
                continue
            path = prefix + q["name"]
            if q["kind"] != "prop":
                flat(q.get("props"), path + "/", res, seen)
            elif q.get("type") != "none":
                seen[path] = seen.get(path, 0) + 1
                res[path if seen[path] == 1 else f"{path}#{seen[path]}"] = q
                if seen[path] == 1:
                    res[f"{path}#1"] = q
        return res

    for e in json.loads(p.read_text(encoding="utf-8")):
        if e.get("match"):
            out[e["match"]] = {"name": e.get("name"), "category": e.get("category"), "props": flat(e.get("props")),
                               "error": e.get("error")}
    return out


@lru_cache(None)
def pr_effects():
    """display name -> {props: {name (#n for repeats): spec}} from the live harvest."""
    p = HERE / "harvest" / "pr" / "pr_video_effects.json"
    lists = HERE / "harvest" / "pr" / "pr_lists.json"
    out = {}
    if lists.exists():
        for n in json.loads(lists.read_text(encoding="utf-8")).get("video_effects", []):
            out[n] = {"props": {}}
    if p.exists():
        try:
            for e in json.loads(p.read_text(encoding="utf-8")):
                props, seen = {}, {}
                for q in e.get("props", []):
                    n = q.get("name")
                    if not n:
                        continue
                    seen[n] = seen.get(n, 0) + 1
                    props[n if seen[n] == 1 else f"{n}#{seen[n]}"] = q
                    if seen[n] == 1:
                        props[f"{n}#1"] = q
                out[e["name"]] = {"props": props, "component": e.get("component"), "match": e.get("match")}
        except Exception:  # noqa: BLE001
            pass
    return out


@lru_cache(None)
def pr_transitions():
    p = HERE / "harvest" / "pr" / "pr_lists.json"
    return set(json.loads(p.read_text(encoding="utf-8")).get("video_transitions", [])) if p.exists() else set()


def find(query, kind=None, app=None, limit=25, verdict=None):
    """Rank cards by how many query words appear in name / look / summary / use_for (name hits weigh most); current
    cards and render passes win ties, broken renders lose them. verdict filters: a verdict, "untested" (any cause) or
    "unverified" (never render-checked)."""
    words = [w for w in re.split(r"\W+", query.lower()) if w]
    scored = []
    for c in cards():
        if kind and c.get("kind") != kind or app and c.get("app") != app:
            continue
        v = _verdict(c)
        if verdict and not (v == verdict or verdict == "untested" and (v or "").startswith("untested:")
                            or verdict == "unverified" and v is None):
            continue
        name = (c.get("name") or "").lower()
        hay = " ".join([name, " ".join(c.get("look") or []), c.get("summary") or "", " ".join(c.get("use_for") or [])]).lower()
        s = sum(3 * (w in name) + hay.count(w) for w in words)
        if s:
            scored.append((s + (c.get("status") == "current") + VERDICT_RANK.get(v, 0), c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:limit]]


class AmbiguousCard(UserWarning):
    """More than one card matches a pve.card() query."""


def card(name, app=None):
    """One card by name, match name or "Category/Name", optionally for one app. When several cards match, the first is
    returned with an AmbiguousCard warning naming them all (distinct effects can share a display name)."""
    q = name.lower()
    hit = [c for c in cards() if (c.get("name") or "").lower() == q or (c.get("match") or "") == name
           or f"{c.get('category')}/{c.get('name')}".lower() == q]
    if app:
        hit = [c for c in hit if c.get("app") == app] or hit
    if len(hit) > 1:
        warnings.warn(f"{len(hit)} cards match {name!r}, returning the first: " + "; ".join(
            f"{c.get('app')} {c.get('kind')} " + (c.get("match") or f"{c.get('category') or c.get('topic') or c.get('family')}"
                                                  f"/{c.get('name')}") for c in hit)
            + " (ask by match name, app or \"Category/Name\")", AmbiguousCard, stacklevel=2)
    return hit[0] if hit else None


def _check_value(where, spec, v):
    t = spec.get("type")
    if isinstance(v, list) and v and isinstance(v[0], list):  # keyframes [[t, value], ...]
        if spec.get("animatable") is False:  # AE harvest canVaryOverTime; the refusal ends the rest of the AE stack
            return [f"{where}: this property cannot be keyframed (After Effects refuses the keys and drops the "
                    f"effects after it on the layer): set one value, or key an animatable property instead"]
        return [x for k in v for x in _check_value(where, spec, k[1])]
    if t in ("1d", "number") and isinstance(v, (int, float)) and not isinstance(v, bool):
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and hi is not None and lo > hi:  # a uint32 probe artefact (Track Matte Key 'Matte:'): no range
            lo = hi = None
        if lo is not None and v < lo - 1e-9 or hi is not None and v > hi + 1e-9:
            return [f"{where}: {v} outside {lo}..{hi} (After Effects kills the script on out-of-range values; Premiere clamps)"]
    if t in ("custom", "layer", "mask", "shape", "text", "marker", "json", "string"):
        return [f"{where}: a {t} property cannot be set from a spec"]
    return []


def check_effects(effects, app, where):
    issues = []
    for e in effects or []:
        e = e if isinstance(e, dict) else {"name": e}
        if app == "AE":
            reg = ae_effects()
            m = e["name"] if e["name"] in reg else next((k for k, v in reg.items() if v["name"] == e["name"]), None)
            if not reg:
                continue
            if not m:
                issues.append(f"{where}: no After Effects effect {e['name']!r}")
                continue
            props = reg[m]["props"]
            for pn, v in (e.get("props") or {}).items():
                key = pn if pn in props else next((k for k in props if k.split("/")[-1] == pn.split("/")[-1]), None)
                if not key:
                    issues.append(f"{where}: {reg[m]['name']} has no property {pn!r}")
                else:
                    issues += _check_value(f"{where}: {reg[m]['name']}.{pn}", props[key], v)
        else:
            reg = pr_effects()
            n = e.get("pr_name", e["name"])
            if not reg:
                continue
            if n not in reg:
                issues.append(f"{where}: no Premiere effect {n!r}")
                continue
            props = reg[n]["props"]
            for pn, v in (e.get("pr_props", e.get("props")) or {}).items():
                if props and pn not in props:
                    issues.append(f"{where}: {n} has no property {pn!r}")
                elif props:
                    issues += _check_value(f"{where}: {n}.{pn}", props[pn], v)
    return issues


def check_spec(spec):
    """Pre-flight for an AdobeBridge spec: effect names, property names, ranges, transition names, flash rates."""
    issues = []
    fps = spec.get("fps", 30)  # adobe_bridge.normalize's default
    layers = [(c, f"clip {i + 1}") for i, c in enumerate(spec.get("clips", []))] + \
             [(g, f"mgt {i + 1}") for i, g in enumerate(spec.get("mgts", []))] + \
             [(a, f"AE comp {a.get('comp')}") for a in spec.get("aecomps", [])]
    for x, where in layers:
        issues += _flash_rate(x.get("effects"), where, fps)
    for i, c in enumerate(spec.get("clips", [])):
        issues += check_effects(c.get("effects"), "PR", f"clip {i + 1}")
        for key in ("transition", "transition_out"):
            t = c.get(key)
            if t and pr_transitions():
                n = t if isinstance(t, str) else t.get("name")
                if n not in pr_transitions():
                    issues.append(f"clip {i + 1}: no Premiere transition {n!r}")
    for i, g in enumerate(spec.get("mgts", [])):
        issues += check_effects(g.get("effects"), "PR", f"mgt {i + 1} {g.get('name', '')}")
    for a in spec.get("aecomps", []):
        issues += check_effects(a.get("effects"), "PR", f"AE comp {a.get('comp')}")
    for x, where in layers + [(t, f"text {i + 1}") for i, t in enumerate(spec.get("texts", []))]:
        issues += [m for p in x.get("presets") or [] for m in _preset_flash(p, where)]
    return issues + _transition_flashes(spec)


# Photosensitivity (Harding test / ITU-R BT.1702): no more than 3 flashes in any one second where the flashing area is
# a quarter of the screen or more; a transition to or from saturated red counts as a flash even when it is not bright.
# A card that flashes says so in its "flash" field, with how to count it:
#   per_frame: property holding a % share of frames the effect inverts at random (Strobe Light's Random Strobe
#              Probablity). 0 and 100 keep the clock (100 is its exact inverse); anything between makes random bursts
#              with no reliable bound below the frame rate (measured: 10 reached 6-7 flashes in one second)
#   period:    property holding the seconds from one flash to the next (rate 1 / value)
#   duration:  property that, with period, decides whether anything flashes: the colour shows for Period - Duration,
#              so Duration 0 or >= Period gives no flash (Duration = Period measured; the other cases follow from it)
#   rate:      flashes in the worst second at the item's own timing (presets: their .ffx defaults, measured)
#   per_use:   flashes each time it plays (transitions; one-shot presets)
#   quiet:     {property: minimum}: at or above it each flash is too faint to count
#   basis:     where those numbers come from (a render, the .ffx, or "not measured")
# None of per_frame / period / rate / per_use = PVE cannot compute the rate: an advisory "check by eye" issue.


@lru_cache(None)
def _flashers():
    """(kind, name or match name) -> the cards that carry a "flash" field."""
    out = {}
    for c in cards():
        if isinstance(c.get("flash"), dict):
            for k in {c.get("name"), c.get("match")} - {None}:
                out.setdefault((c["kind"], k), []).append(c)
    return out


def _harvest_default(c, prop):
    if c.get("app") == "AE":
        return ((ae_effects().get(c.get("match") or "") or {}).get("props", {}).get(prop) or {}).get("default")
    return ((pr_effects().get(c.get("name")) or {}).get("props", {}).get(prop) or {}).get("value")


def _effect_flash(c, props, where, fps):
    """One use of a flashing card (props = what the spec sets; unset properties take the harvested default)."""
    f, name = c["flash"], c["name"]

    def vals(p):  # every value the spec gives p (each key of a keyframed one), else the harvested default
        key = p if p in props else next((k for k in props if k.split("/")[-1] == p), None)
        v = props[key] if key else _harvest_default(c, p)
        vs = [k[1] for k in v] if isinstance(v, list) and v and isinstance(v[0], list) else [v]
        return [x for x in vs if isinstance(x, (int, float)) and not isinstance(x, bool)]

    def val(p, pick):
        vs = vals(p)
        return pick(vs) if vs else None

    quiet = f.get("quiet") or {}
    if quiet and all((val(p, min) or 0) >= t for p, t in quiet.items()):
        return []
    also = "".join(f", or raise {p} to {t}+" for p, t in quiet.items())
    chance = vals(f["per_frame"]) if f.get("per_frame") else []
    if any(0 < x < 100 for x in chance) or len(set(chance)) > 1:  # keys from 0 to 100 pass through the middle too
        return [f"{where}: {name} {f['per_frame']} {'/'.join(f'{x:g}' for x in chance)} makes random bursts of flashes "
                f"(rendered: 10 reached 6-7 in one second): no rate below about half the frame rate ({fps / 2:.0f} a "
                f"second at {fps:g} fps) can be promised, a photosensitivity risk (limit {FLASH_LIMIT} in any second): "
                f"set it to 0 (or 100) and time the flashes with {f.get('period') or 'keyframes'}{also}"]
    if f.get("period"):  # 0 or 100: one flash per Period while the colour phase (Period - Duration) has a length
        per, dur = vals(f["period"]), vals(f["duration"]) if f.get("duration") else []
        if len(per) == len(dur) == 1 and per[0] > 0 and not 0 < dur[0] < per[0]:
            return []
        per = min(per) if per else None
        rate, fix = (1 / per if per else 0), f"set {f['period']} to {1 / FLASH_LIMIT + 0.01:.2f} or more"
    elif "rate" in f or "per_use" in f:
        rate, fix = f.get("rate", f.get("per_use")), f"keep it off large areas or pick a calmer one ({f.get('basis')})"
    else:
        return [f"{ADVISORY}: {where}: {name} flashes and PVE cannot compute how often ({f.get('basis')}): preview it "
                f"and keep it to {FLASH_LIMIT} flashes or fewer in any second"]
    if rate > FLASH_LIMIT:
        return [f"{where}: {name} flashes about {rate:.0f} times a second; the photosensitivity limit is {FLASH_LIMIT} "
                f"in any second: {fix}{also}"]
    return []


def _flash_rate(effects, where, fps, app="PR"):
    """Flash-rate guard for one effect stack (Premiere stacks by default): every effect whose card has a "flash"
    field, rated from the spec's own values (keyframes: their worst case). A bare name uses the harvested defaults."""
    kind = "ae_effect" if app == "AE" else "pr_effect"
    out = []
    for e in effects or []:
        e = e if isinstance(e, dict) else {"name": e}
        n = e.get("pr_name", e.get("name")) if app == "PR" else e.get("name")
        for c in _flashers().get((kind, n), [])[:1]:
            out += _effect_flash(c, (e.get("pr_props", e.get("props")) if app == "PR" else e.get("props")) or {}, where, fps)
    return out


def _preset_card(file):
    """The ae_preset card for an .ffx path: by file name, and by folder when two presets share a name."""
    p = Path(str(file).replace("\\", "/"))  # harvest paths are Windows paths; read them the same way on any OS
    same = [c for c in cards() if c.get("kind") == "ae_preset" and c.get("name") == p.stem]
    if len(same) > 1:
        same = [c for c in same if p.parent.as_posix().lower().endswith((c.get("category") or "").lower())]
    return same[0] if len(same) == 1 else None


def _preset_flash(pr, where):
    c = _preset_card((pr.get("file") if isinstance(pr, dict) else pr) or "")
    return _effect_flash(c, {}, f"{where} preset", 25) if c and isinstance(c.get("flash"), dict) else []


def _transition_flashes(spec):
    """Flash transitions counted in every 1-second window of the edit (their cards: "flash": {"per_use": n}). A clip's
    time comes from its start / dur (start defaults to the end of the clip before it on the same track).
    ponytail: each source is counted on its own; a strobe clip next to a Flash transition is not summed with it."""
    out, placed, unplaced, ends = [], [], 0, {}
    for i, c in enumerate(spec.get("clips", [])):
        tr = c.get("track", 1)
        start = c.get("start", ends.get(tr, 0))
        dur = c.get("dur")
        ends[tr] = start + dur if isinstance(start, (int, float)) and isinstance(dur, (int, float)) else None
        for key, at in (("transition", start), ("transition_out", ends[tr])):
            t = c.get(key)
            n = t if isinstance(t, str) else (t or {}).get("name")
            fc = next(iter(_flashers().get(("pr_transition", n), [])), None) if n else None
            if not fc:
                continue
            f = fc["flash"]
            if "per_use" not in f:
                out.append(f"{ADVISORY}: clip {i + 1} {key}: {n} flashes and PVE cannot count how often "
                           f"({f.get('basis')}): preview it")
            elif isinstance(at, (int, float)):
                placed.append((at, f["per_use"], f"clip {i + 1} {n}"))
            else:
                unplaced += f["per_use"]
    placed.sort()
    for j, (t, _, _) in enumerate(placed):
        win = [e for e in placed[j:] if e[0] < t + 1]
        if sum(e[1] for e in win) > FLASH_LIMIT:
            out.append(f"clips: {sum(e[1] for e in win)} flash transitions within one second from {t:g} s "
                       f"({', '.join(e[2] for e in win)}): more than {FLASH_LIMIT} flashes in a second for photosensitive "
                       f"viewers: space them at least a second apart or use cuts / dissolves")
            break
    if unplaced and unplaced + sum(e[1] for e in placed) > FLASH_LIMIT:
        out.append(f"{ADVISORY}: clips: {unplaced} flash transition(s) sit on clips without a start / dur, so PVE cannot "
                   f"place them: make sure no second holds more than {FLASH_LIMIT}")
    return out


def check_scenes(scenes):
    """Pre-flight for vfx.py scenes: every layer effect against the After Effects harvest, flash rates included."""
    issues = []
    for s in scenes:
        for L in s.get("layers", []):
            where = f"{s.get('name')}/{L.get('name', L.get('type'))}"
            issues += check_effects(L.get("effects"), "AE", where)
            issues += [f"{where}: {m}" for m in _preset_host(L)]
            issues += _flash_rate(L.get("effects"), where, s.get("fps", 25), "AE")
            issues += [m for p in L.get("presets") or [] for m in _preset_flash(p, where)]
    return issues


@lru_cache(None)
def _preset_contents():
    """.ffx path (lower case, forward slashes) -> the match names inside it, from the harvest."""
    p = HERE / "harvest" / "files.json"
    if not p.exists():
        return {}
    return {Path(x["file"]).as_posix().lower(): " ".join(x.get("match_names") or [])
            for x in json.loads(p.read_text(encoding="utf-8")).get("ae_presets", [])}


def _preset_host(L):
    """Render-verified: text-animator presets do nothing off a text layer; shape presets need a shape layer and draw
    around its origin."""
    out = []
    for pr in L.get("presets") or []:
        mn = _preset_contents().get(Path(pr.get("file", "")).as_posix().lower(), "")
        name = Path(pr.get("file", "")).stem
        if "ADBE Text Animators" in mn and L.get("type") != "text":
            out.append(f"preset {name!r} animates text but the layer is {L.get('type')!r}: it will do nothing")
        elif "ADBE Root Vectors Group" in mn and "ADBE Text" not in mn and L.get("type") != "shape":
            out.append(f"preset {name!r} is a shape preset but the layer is {L.get('type')!r}")
        elif "ADBE Root Vectors Group" in mn and L.get("type") == "shape" and not L.get("origin"):
            out.append(f"shape preset {name!r} draws around the layer origin, which is the comp's top-left corner here: "
                       f"give the layer an \"origin\" where the graphic should appear")
    return out


def stats():
    c = Counter((x.get("kind"), x.get("app")) for x in cards())
    return {"cards": dict(sorted(((f"{k}:{a}", n) for (k, a), n in c.items()))), "ae_effects_harvested": len(ae_effects()),
            "pr_effects_harvested": len(pr_effects()), "pr_transitions": len(pr_transitions()),
            "verdicts": dict(sorted(Counter(_verdict(x) or "unverified" for x in cards()).items()))}


def _main(argv):
    if not argv:
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "find":
        opts, words, it = {}, [], iter(argv[1:])
        for a in it:
            if a in ("--kind", "--app", "--verdict"):
                opts[a[2:]] = next(it, None)
            else:
                words.append(a)
        for c in find(" ".join(words), **opts):
            print(f"[{c.get('app')} {c.get('kind')}] {c.get('name')}: {c.get('summary', '')[:110]}")
    elif cmd == "card":
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            c = card(argv[1], argv[2] if len(argv) > 2 else None)
        for w in seen:
            print(f"WARNING: {w.message}", file=sys.stderr)
        if c and _verdict(c):  # spell out what the verdict does (and does not) say
            c = dict(c, verified=dict(c["verified"], meaning=verdict_meaning(_verdict(c))))
        print(json.dumps(c, indent=1, ensure_ascii=False))
    elif cmd in ("check", "scenes"):
        data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        issues = check_spec(data) if cmd == "check" else check_scenes(data if isinstance(data, list) else data["scenes"])
        print("\n".join(issues) or "OK: nothing PVE objects to")
        return 1 if any(not advisory(i) for i in issues) else 0
    elif cmd == "stats":
        print(json.dumps(stats(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
