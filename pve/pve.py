"""PVE ("professional video editor"): what After Effects 2026 and Premiere Pro 2026 can do, harvested from the
installed apps, render-tested, and written up as knowledge cards. The edit system reads it to choose effects and to
check specs before anything is sent to an app.

    python pve/pve.py find "film grain"            search cards (name, summary, look, use_for, kind, category)
                      [--kind K] [--app AE|PR|both] [--verdict pass|untested|...]
    python pve/pve.py card "Camera Shake" [PR|AE]   one card, in full (name, match name or "Category/Name";
                                                    warns on stderr when several cards match)
    python pve/pve.py check spec.json [--no-ae|--no-pr]   validate an AdobeBridge spec: clip effects in both apps
                                                    (or the one the build drives), texts, mgts, aecomps, transitions
    python pve/pve.py scenes scenes.json            validate After Effects VFX scenes (vfx.py input)
    python pve/pve.py stats                         what PVE knows

    from pve import pve; pve.check_spec(spec) -> [issues]; pve.find("glitch")

Issues that start with "check by eye" are advisory: `check` / `scenes` exit 1 only on the others.
find lines read "[app kind] name: [status, verdict] summary"; the brackets after the colon appear only for a card that
is not current or whose render (either half) did not pass.
Exit codes: 0 ok (also a find or card with no match), 1 = check / scenes found issues, 2 = could not check (usage
error, unreadable or wrong-shaped file, or a knowledge base that is missing or partly unreadable).
"""
import json, math, os, re, sys, warnings
from collections import Counter
from functools import lru_cache
from pathlib import Path

HERE = Path(os.environ.get("PVE_HOME") or Path(__file__).resolve().parent).resolve()  # holds cards/ and harvest/

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
# find() multiplies a card's text score by these: a current card that passed its render beats an obsolete, hidden,
# unusable or broken one matching the same words (needs_input is a usage precondition, not a defect)
STATUS_WEIGHT = {"current": 1.0, "needs_input": 0.9, "beta": 0.9, "obsolete": 0.6, "unusable": 0.4, "hidden": 0.3}
VERDICT_WEIGHT = {"pass": 1.05, "weak": 0.9, "corrected_unverified": 0.9, "wrong": 0.5, "broken": 0.5}
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


def _verdicts(c):
    """(top-level verdict, After Effects half's verdict or None): a recipe with both stacks keeps its After Effects
    half's verdict in verified.ae, and the top-level one is then Premiere's."""
    v = c.get("verified") or {}
    return v.get("verdict"), (v.get("ae") or {}).get("verdict")


_UNREADABLE = {}  # knowledge-base file (relative to HERE) -> why it could not be loaded; see unreadable()


def _load(p):
    """JSON from a file; utf-8-sig also reads one a Windows tool saved with a byte-order mark."""
    return json.loads(Path(p).read_text(encoding="utf-8-sig"))


def _harvest(rel):
    """A parsed harvest file (path relative to HERE), or None when it is missing or does not parse (recorded)."""
    try:
        data = _load(HERE / rel)
    except FileNotFoundError:
        _UNREADABLE[rel] = "missing"
        return None
    except (OSError, ValueError) as e:
        _UNREADABLE[rel] = f"unreadable ({type(e).__name__}: {e})"
        return None
    _UNREADABLE.pop(rel, None)
    return data


@lru_cache(None)
def cards():
    """Every card. A stub with "merged_into" (a duplicate harvest row whose card was merged into another) is skipped.
    A card file that does not parse (mid-write, truncated) is skipped too, and reported by unreadable() / kb_problems()."""
    out = []
    for k in [k for k in _UNREADABLE if k.startswith("cards/")]:
        del _UNREADABLE[k]
    for f in sorted((HERE / "cards").glob("*.json")):
        try:
            out += [c for c in _load(f).get("cards", []) if not c.get("merged_into")]
        except Exception as e:  # noqa: BLE001
            _UNREADABLE[f"cards/{f.name}"] = f"unreadable ({type(e).__name__}: {e})"
    return out


@lru_cache(None)
def ae_effects():
    """match name -> {name, category, props: {path (#n for repeats): spec}, heads: header row names} from the live
    harvest. Header rows are skipped and don't count towards #n, as in ae_lib.jsx child() and validate_cards.leaf_props."""
    out = {}

    def flat(props, heads, prefix="", res=None, seen=None):
        res, seen = ({}, {}) if res is None else (res, seen)
        for q in props or []:
            if q.get("match") == "ADBE Effect Built In Params":
                continue
            path = prefix + q["name"]
            if q["kind"] != "prop":
                flat(q.get("props"), heads, path + "/", res, seen)
            elif q.get("type") != "none":
                seen[path] = seen.get(path, 0) + 1
                res[path if seen[path] == 1 else f"{path}#{seen[path]}"] = q
                if seen[path] == 1:
                    res[f"{path}#1"] = q
            else:
                heads.add(q["name"])
        return res

    for e in _harvest("harvest/ae_effects.json") or []:
        if e.get("match"):
            heads = set()
            out[e["match"]] = {"name": e.get("name"), "category": e.get("category"), "props": flat(e.get("props"), heads),
                               "heads": heads, "error": e.get("error")}
    return out


def _pr_props(rows):
    """Premiere property rows -> {name (#n for repeats): spec}; a row without a type gets one from its value."""
    props, seen = {}, {}
    for q in rows or []:
        n = q.get("name")
        if not n:
            continue
        if "type" not in q:  # the stereo audio dump stores values only
            q = dict(q, type="bool" if isinstance(q.get("value"), bool) else "number" if _NUM(q.get("value")) else None)
        seen[n] = seen.get(n, 0) + 1
        props[n if seen[n] == 1 else f"{n}#{seen[n]}"] = q
        if seen[n] == 1:
            props[f"{n}#1"] = q
    return props


@lru_cache(None)
def pr_effects():
    """display name -> {props: {name (#n for repeats): spec}} from the live harvest."""
    out = {n: {"props": {}} for n in (_harvest("harvest/pr/pr_lists.json") or {}).get("video_effects", [])}
    for e in _harvest("harvest/pr/pr_video_effects.json") or []:
        out[e["name"]] = {"props": _pr_props(e.get("props")), "component": e.get("component"), "match": e.get("match")}
    return out


@lru_cache(None)
def pr_audio_effects():
    """display name -> {props} for Premiere audio effects: the mono harvest, with every effect the stereo dump
    (pr_audio_params_w3.json) holds taken from that dump (the mono one gave seven stereo-only effects the Volume row)."""
    rows = {e["name"]: e for e in _harvest("harvest/pr/pr_audio_effects.json") or []}
    rows.update({e["name"]: e for e in (_harvest("harvest/pr_audio_params_w3.json") or {}).get("effects", [])})
    return {n: {"props": _pr_props(e.get("props"))} for n, e in rows.items()}


@lru_cache(None)
def pr_transitions():
    return set((_harvest("harvest/pr/pr_lists.json") or {}).get("video_transitions", []))


def unreadable():
    """{file relative to the knowledge base: "missing" / "unreadable (...)"} for every card or harvest file the
    checks need and could not load."""
    cards(), ae_effects(), pr_effects(), pr_audio_effects(), pr_transitions(), _preset_contents()
    return dict(_UNREADABLE)


def kb_problems():
    """[] when the whole knowledge base loaded, else one issue naming what is missing or unreadable. check_spec and
    check_scenes put it first; it is never advisory (a check against a partial knowledge base proves nothing)."""
    bad = [f"{k} {v}" for k, v in sorted(unreadable().items())] + ([] if cards() else ["no cards loaded"])
    return [f"knowledge base incomplete at {HERE.as_posix()}: {'; '.join(bad)}"] if bad else []


# find(): function words carry no weight (English and the Hinglish an editor types); a query of only stop words keeps them
_STOP = frozenset("a an the to of for in on at and or with from by into onto my me i it is be how do make get video clip "
                  "ko ka ki ke se pe par mein hai karo karna kar jab tab koi aur ye yeh wo woh ek dikhana dikhao".split())


def _stem(w):
    """Crude, consistent stemmer: British -ise -> -ize, colour -> color, then one common suffix off."""
    w = re.sub(r"is(e|es|ed|er|ers|ing|ation)$", r"iz\1", w).replace("colour", "color")
    for suf in ("ing", "ers", "er", "es", "s", "e", "y"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def _toks(text):
    return {_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower())}


def _index():
    """(per card in cards() order: (name tokens, other searched tokens), document frequency), rebuilt whenever
    cards() hands back a new list (a PVE_HOME swap plus cards.cache_clear())."""
    cs = cards()
    if getattr(_index, "of", None) is not cs:
        rows, df = [], Counter()
        for c in cs:
            name = _toks(c.get("name") or "")
            rest = _toks(" ".join([" ".join(c.get("look") or []), c.get("summary") or "", " ".join(c.get("use_for") or []),
                                   c.get("kind") or "", c.get("category") or "", c.get("family") or "", c.get("topic") or ""]))
            rows.append((name, rest))
            df.update(name | rest)
        _index.of, _index.rows, _index.df = cs, rows, df
    return _index.rows, _index.df


def _verdict_ok(v, want):
    return v == want or want == "untested" and (v or "").startswith("untested:") or want == "unverified" and v is None


def find(query, kind=None, app=None, limit=25, verdict=None):
    """Rank cards by IDF-weighted whole-word matches of the query (stemmed, stop words dropped) in name / look /
    summary / use_for / kind / category; a name hit weighs 3x, cards matching more of the query's words win, and a card
    whose name is the whole query comes first. Obsolete, hidden and unusable cards and broken / wrong renders are
    scaled down (STATUS_WEIGHT, VERDICT_WEIGHT; a recipe takes the worse of its two halves, or its After Effects half
    under app="AE"). verdict filters (either half): a verdict, "untested" (any cause) or "unverified" (never
    render-checked)."""
    words = re.findall(r"[a-z0-9]+", query.lower())
    q = {_stem(w) for w in words if w not in _STOP} or {_stem(w) for w in words}  # all stop words: keep them
    rows, df = _index()
    idf = {t: math.log((len(rows) + 1) / (df.get(t, 0) + 0.5)) for t in q}
    exact = query.lower().strip()
    scored = []
    for c, (name, rest) in zip(cards(), rows):
        if kind and c.get("kind") != kind or app and c.get("app") != app:
            continue
        top, ae = _verdicts(c)
        if verdict and not (_verdict_ok(top, verdict) or ae is not None and verdict != "unverified" and _verdict_ok(ae, verdict)):
            continue
        s = sum(idf[t] * (3 * (t in name) + (t in rest)) for t in q)
        if s <= 0:
            continue
        if q <= name:  # every query word is in the name: the card IS the thing asked for
            s *= 1.5
        s *= sum(1 for t in q if t in name or t in rest) / len(q)
        if (c.get("name") or "").lower() == exact:  # asked for by its exact name
            s *= 3
        vw = [VERDICT_WEIGHT.get(ae, 1.0)] if app == "AE" and ae else [VERDICT_WEIGHT.get(v, 1.0) for v in (top, ae) if v]
        scored.append((s * STATUS_WEIGHT.get(c.get("status"), 1.0) * min(vw or [1.0]), c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:limit]]


class AmbiguousCard(UserWarning):
    """More than one card matches a pve.card() query."""


APPS = {"ae": "AE", "pr": "PR", "both": "both"}  # app arguments, any case


def _label(c):
    """ "Category/Name" (topic or family for tool and recipe cards): what card() accepts and its warning prints."""
    return f"{c.get('category') or c.get('topic') or c.get('family')}/{c.get('name')}"


def card(name, app=None):
    """One card by name, match name or "Category/Name", optionally for one app ("AE", "PR" or "both", any case; a
    recipe or tool for both apps counts for either; another app's card is never returned). When several cards match,
    a current one comes before hidden, obsolete and unusable ones and the first is returned with an AmbiguousCard
    warning naming them all (distinct effects can share a display name). A blank name matches nothing."""
    if not isinstance(name, str) or not name.strip():
        return None
    a = None
    if app:
        a = APPS.get(str(app).lower())
        if not a:
            raise ValueError(f"app must be AE, PR or both, not {app!r}")
    q = name.lower()
    hit = [c for c in cards() if (c.get("name") or "").lower() == q or c.get("match") and c["match"] == name
           or _label(c).lower() == q]
    if a:
        hit = [c for c in hit if c.get("app") in (a, "both")]
    hit.sort(key=lambda c: c.get("status") in ("hidden", "obsolete", "unusable"))  # stable: otherwise file order
    if len(hit) > 1:
        warnings.warn(f"{len(hit)} cards match {name!r}, returning the first: " + "; ".join(
            f"{c.get('app')} {c.get('kind')} " + (c.get("match") or _label(c)) for c in hit)
            + " (ask by match name, app or \"Category/Name\")", AmbiguousCard, stacklevel=2)
    return hit[0] if hit else None


def _NUM(x):
    """A finite real number (not a bool, not NaN or infinity)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _HEX(x):
    return isinstance(x, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", x) is not None


_DIMS = {"2d_spatial": 2, "2d": 2, "point": 2, "3d_spatial": 3, "3d": 3}


def _keyed(v):
    return isinstance(v, list) and bool(v) and isinstance(v[0], list)


def _values(v):
    """Every value a spec gives a property: the value, or each key's value of keyframes [[t, value], ...]."""
    return [k[1] for k in v if isinstance(k, list) and len(k) > 1] if _keyed(v) else [v]


def _check_value(where, spec, v):
    t = spec.get("type")
    if _keyed(v):  # keyframes [[t, value], ...]
        if spec.get("animatable") is False:  # AE harvest canVaryOverTime; the refusal ends the rest of the AE stack
            return [f"{where}: this property cannot be keyframed (After Effects refuses the keys and drops the "
                    f"effects after it on the layer): set one value, or key an animatable property instead"]
        if not all(isinstance(k, list) and len(k) == 2 and _NUM(k[0]) for k in v):
            return [f"{where}: keyframes must be [[seconds, value], ...]"]
        return [x for k in v for x in _check_value(where, spec, k[1])]
    if t in ("custom", "layer", "mask", "shape", "text", "marker", "json", "string"):
        return [f"{where}: a {t} property cannot be set from a spec"]
    if t in ("1d", "number"):
        if isinstance(v, bool) and spec.get("min") == 0 and spec.get("max") == 1:
            return []  # a checkbox
        if _HEX(v):  # both apps' setters turn "#rrggbb" into a colour, and Premiere harvests some colours as numbers
            return []
        if not _NUM(v):
            return [f"{where}: {v!r} is not a number"]
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and hi is not None and lo > hi:  # a uint32 probe artefact (Track Matte Key 'Matte:', colours)
            # ponytail: only non-negative whole numbers are known good (track numbers, 0 = none); -1 wraps to "none"
            return [] if v >= 0 and v == int(v) else [f"{where}: {v} is not a whole number 0 or above"]
        if lo is not None and v < lo - 1e-9 or hi is not None and v > hi + 1e-9:
            return [f"{where}: {v} outside {lo}..{hi} (After Effects clamps it with a warning; Premiere clamps)"]
    if t == "color" and not (_HEX(v) or _NUM(v) or isinstance(v, list) and len(v) in (3, 4) and all(map(_NUM, v))):
        return [f"{where}: {v!r} is not a colour (\"#rrggbb\" or [r, g, b])"]
    if t in _DIMS and not (isinstance(v, list) and len(v) in (_DIMS[t], 3) and all(map(_NUM, v))):
        return [f"{where}: {v!r} is not a point of {_DIMS[t]} numbers"]
    return []


def _guard(where, fn, *a):
    """fn(*a), or one issue for an entry too malformed to check, so the rest of the spec is still checked."""
    try:
        return fn(*a)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as e:
        return [f"{where}: malformed entry ({type(e).__name__}: {e}); not checked"]


def _ae_key(entry, pn):
    """The harvest key that ae_lib.jsx dress() reaches with a spec's property name: the whole name first (some names
    hold "/": Keylight 'Screen Shrink/Grow'), then a "Group/Prop" path, walking past CC header rows ("Particle/Birth
    Color"). None: After Effects warns "has no ..." and skips the value."""
    if pn in entry["props"]:
        return pn
    parts = pn.split("/")
    key = "/".join([p for p in parts[:-1] if p not in entry["heads"]] + parts[-1:])
    return key if len(parts) > 1 and key in entry["props"] else None


@lru_cache(None)
def _ae_current():
    return {c.get("match") for c in cards() if c.get("kind") == "ae_effect" and c.get("status") == "current"}


def _check_effect(e, app, where):
    e = e if isinstance(e, dict) else {"name": e}
    name = e.get("name")
    if not isinstance(name, str):
        return [f"{where}: effect {json.dumps(e)[:80]} has no \"name\""]
    if app == "AE":
        reg = ae_effects()
        if not reg:
            return []
        # a display name can belong to a current and an obsolete effect: which one AE's addProperty picks was never
        # measured, so a property or value counts as wrong only when every candidate rejects it
        cands = sorted([name] if name in reg else [k for k, v in reg.items() if v["name"] == name],
                       key=lambda m: m not in _ae_current())
        if not cands:
            return [f"{where}: no After Effects effect {name!r}"]
        out, split = [], False
        for pn, v in (e.get("props") or {}).items():
            each = []
            for m in cands:
                key = _ae_key(reg[m], pn)
                each.append(_check_value(f"{where}: {reg[m]['name']}.{pn}", reg[m]["props"][key], v) if key
                            else [f"{where}: {reg[m]['name']} has no property {pn!r}"])
            if all(each):
                out += each[0]
            split = split or any(each) and not all(each)
        if split:
            out.append(f"{ADVISORY}: {where}: {name!r} names {len(cands)} After Effects effects ({', '.join(cands)}) "
                       f"that take different properties or values: use the match name")
        return out
    reg = pr_audio_effects() if app == "PR audio" else pr_effects()
    n = e.get("pr_name", name)
    if not reg:
        return []
    if n not in reg:
        return [f"{where}: no Premiere {'audio ' if app == 'PR audio' else ''}effect {n!r}"]
    props, out = reg[n]["props"], []
    for pn, v in (e.get("pr_props", e.get("props")) or {}).items():
        if props and pn not in props:
            out.append(f"{where}: {n} has no property {pn!r}")
        elif props:
            out += _check_value(f"{where}: {n}.{pn}", props[pn], v)
            if (n, pn) == ("Track Matte Key", "Matte:") and any(_NUM(x) and x == 0 for x in _values(v)):
                out.append(f"{ADVISORY}: {where}: Track Matte Key Matte: 0 means no matte (tracks are 1-based: "
                           f"1 = Video 1)")
    return out


def check_effects(effects, app, where):
    """One effect stack against one app's harvest: app "AE", "PR", or "PR audio" (Premiere audio effects). Unknown
    names, properties and values; a malformed effect is an issue of its own and the rest are still checked."""
    if effects is None:
        return []
    if not isinstance(effects, list):
        return [f"{where}: effects must be a list, got {type(effects).__name__}"]
    return [m for i, e in enumerate(effects) for m in _guard(f"{where}: effect {i + 1}", _check_effect, e, app, where)]


def _items(x):
    return x if isinstance(x, list) else []


def _check_clip(c, where, fps, apps):
    fx, out = c.get("effects"), []
    if "PR" in apps:
        out += _flash_rate(fx, where, fps) + check_effects(fx, "PR", where)
    if "AE" in apps:  # dress() sends name + props to After Effects too; the build only warns on an effect AE lacks,
        ae = check_effects(fx, "AE", where)  # so an unknown name is dropped only while the Premiere half checks it
        out += ae if "PR" not in apps else [m for m in ae if ": no After Effects effect " not in m]
        out += _flash_rate(fx, where, fps, "AE", c.get("in", 0))  # AE's strobe clock: the clip's source time
        out += [f"{where}: {m}" for m in _preset_host(dict(c, type="footage"))]
    for key in ("transition", "transition_out"):
        t = c.get(key)
        if t and pr_transitions():
            n = t if isinstance(t, str) else t.get("name")
            if n not in pr_transitions():
                out.append(f"{where}: no Premiere transition {n!r}")
    return out + [m for p in _items(c.get("presets")) for m in _preset_flash(p, where)]


def _check_text(t, where, fps):
    """Texts are After Effects comps (Premiere gets them Dynamic-Linked): their effects and presets run in AE only."""
    out = [] if all(_NUM(t.get(k)) for k in ("start", "dur")) else [f"{where}: needs \"start\" and \"dur\" in seconds"]
    out += check_effects(t.get("effects"), "AE", where) + _flash_rate(t.get("effects"), where, fps, "AE")
    out += [f"{where}: {m}" for m in _preset_host(dict(t, type="text"))]
    return out + [m for p in _items(t.get("presets")) for m in _preset_flash(p, where)]


def _check_pr_layer(x, flash_where, where, fps):
    """A Premiere-only layer (motion graphics template, Dynamic-Linked comp)."""
    return _flash_rate(x.get("effects"), flash_where, fps) + check_effects(x.get("effects"), "PR", where) \
        + [m for p in _items(x.get("presets")) for m in _preset_flash(p, flash_where)]


def check_spec(spec, apps=("PR",)):
    """Pre-flight for an AdobeBridge spec: effect names, property names, ranges, transition names, flash rates.
    apps = the apps the build drives. Clip effects are checked for Premiere ("PR": pr_name / pr_props) and, with "AE",
    for After Effects too (name / props, flash rate on AE's clock, clip presets); adobe_bridge.build and the CLI pass
    both unless the build skips one. Text effects and presets are always checked against After Effects (texts are AE
    comps). The default stays Premiere-only for clips because callers of Premiere-only builds rely on it."""
    if not isinstance(spec, dict):
        return kb_problems() + [f"spec must be a JSON object, got {type(spec).__name__}"]
    issues = kb_problems()
    fps = spec.get("fps", 30)  # adobe_bridge.normalize's default
    if not _NUM(fps) or fps <= 0:
        issues.append(f"fps {fps!r}: must be a number above 0")
        fps = 30
    lists = {}
    for k in ("clips", "mgts", "aecomps", "texts"):
        lists[k] = spec.get(k) or []
        if not isinstance(lists[k], list):
            issues.append(f"{k} must be a list, got {type(lists[k]).__name__}")
            lists[k] = []
    for i, c in enumerate(lists["clips"]):
        issues += _guard(f"clip {i + 1}", _check_clip, c, f"clip {i + 1}", fps, apps)
    if "PR" in apps:
        for i, g in enumerate(lists["mgts"]):
            issues += _guard(f"mgt {i + 1}", lambda g=g, i=i: _check_pr_layer(g, f"mgt {i + 1}",
                                                                              f"mgt {i + 1} {g.get('name', '')}", fps))
        for i, a in enumerate(lists["aecomps"]):
            issues += _guard(f"aecomp {i + 1}", lambda a=a: _check_pr_layer(a, f"AE comp {a.get('comp')}",
                                                                            f"AE comp {a.get('comp')}", fps))
    for i, t in enumerate(lists["texts"]):
        issues += _guard(f"text {i + 1}", _check_text, t, f"text {i + 1}", fps)
    issues += _guard("clips", _transition_flashes, spec)
    return list(dict.fromkeys(issues))  # a clip's two halves can report the same line


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


def _effect_flash(c, props, where, fps, phase=0):
    """One use of a flashing card (props = what the spec sets; unset properties take the harvested default). phase =
    the After Effects layer's own (source) time where it becomes visible: AE runs a strobe on that clock."""
    f, name = c["flash"], c["name"]
    entry = ae_effects().get(c.get("match") or "") if c.get("app") == "AE" else None

    def vals(p):  # every value the spec gives p (each key of a keyframed one), else the harvested default
        # the spec key that reaches p the way the app does (AE: whole name or a header-prefixed path; Premiere: exact)
        key = p if p in props else next((k for k in props if entry and _ae_key(entry, k) == p), None)
        v = props[key] if key else _harvest_default(c, p)
        return [x for x in _values(v) if _NUM(x)]

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
        if per is not None and per <= 0:  # never measured: it may flash every frame or not at all
            return [f"{ADVISORY}: {where}: {name} {f['period']} {per:g} is unmeasured (it may flash every frame): set it "
                    f"to {1 / FLASH_LIMIT + 0.01:.2f} or more"]
        rate, fix = (1 / per if per else 0), f"set {f['period']} to {1 / FLASH_LIMIT + 0.01:.2f} or more"
        if per and rate <= FLASH_LIMIT and entry is not None and _NUM(phase) and abs(phase / per - round(phase / per)) > 1e-6:
            # each Period opens with the colour, which holds for Period - Duration: a layer that becomes visible
            # mid-period (a trimmed source) opens with a partial colour run, so its first second can hold
            # 1 + ceil((1 - Duration) / Period) flashes (rendered strips, work/pve_live/strobe.json); <= 3 needs 0.5+
            n = 1 + math.ceil((1 - max(0.0, min(dur) if dur else 0.0)) / per - 1e-9)
            if n > FLASH_LIMIT:
                return [f"{where}: {name} can flash {n} times in the layer's first second: After Effects runs it on the "
                        f"layer's source clock, which starts {phase:g} s in (mid-period); the photosensitivity limit is "
                        f"{FLASH_LIMIT} in any second: set {f['period']} to 0.50 or more{also}"]
    elif "rate" in f or "per_use" in f:
        rate, fix = f.get("rate", f.get("per_use")), f"keep it off large areas or pick a calmer one ({f.get('basis')})"
    else:
        return [f"{ADVISORY}: {where}: {name} flashes and PVE cannot compute how often ({f.get('basis')}): preview it "
                f"and keep it to {FLASH_LIMIT} flashes or fewer in any second"]
    if rate > FLASH_LIMIT:
        return [f"{where}: {name} flashes about {rate:.0f} times a second; the photosensitivity limit is {FLASH_LIMIT} "
                f"in any second: {fix}{also}"]
    return []


def _flash_rate(effects, where, fps, app="PR", phase=0):
    """Flash-rate guard for one effect stack (Premiere stacks by default): every effect whose card has a "flash"
    field, rated from the spec's own values (keyframes: their worst case). A bare name uses the harvested defaults.
    phase (After Effects): the layer's source time where it becomes visible (a footage layer's in point)."""
    kind = "ae_effect" if app == "AE" else "pr_effect"
    out = []
    for e in _items(effects):
        e = e if isinstance(e, dict) else {"name": e}
        n = e.get("pr_name", e.get("name")) if app == "PR" else e.get("name")
        if not isinstance(n, str):
            continue  # check_effects reports a nameless effect
        for c in _flashers().get((kind, n), [])[:1]:
            props = (e.get("pr_props", e.get("props")) if app == "PR" else e.get("props")) or {}
            out += _effect_flash(c, props, where, fps, phase)
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
    for i, c in enumerate(_items(spec.get("clips"))):
        if not isinstance(c, dict):  # check_spec already names the malformed entry; the others still get counted
            continue
        tr = c.get("track", 1)
        start = c.get("start", ends.get(tr, 0))
        dur = c.get("dur")
        ends[tr] = start + dur if isinstance(start, (int, float)) and isinstance(dur, (int, float)) else None
        for key, at in (("transition", start), ("transition_out", ends[tr])):
            t = c.get(key)
            n = t if isinstance(t, str) else t.get("name") if isinstance(t, dict) else None
            fc = next(iter(_flashers().get(("pr_transition", n), [])), None) if isinstance(n, str) else None
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


def _check_layer(L, where, fps):
    # AE runs a strobe on the layer's source clock: a footage layer shows source time "in" at t0 (vfx_build.jsx
    # startTime = t0 - in), every other layer starts its clock at 0 and appears at t0
    phase = L.get("in", 0) if L.get("type") == "footage" else L.get("t0", 0)
    pre = _items(L.get("presets"))
    out = check_effects(L.get("effects"), "AE", where)
    out += [f"{where}: preset {p!r} must be {{\"file\": ..., \"at\": ...}} in a scene (vfx_build.jsx reads .file)"
            for p in pre if not isinstance(p, dict)]
    out += [f"{where}: {m}" for m in _preset_host(dict(L, presets=[p for p in pre if isinstance(p, dict)]))]
    out += _flash_rate(L.get("effects"), where, fps, "AE", phase)
    return out + [m for p in pre for m in _preset_flash(p, where)]


def _check_scene(s):
    layers = s.get("layers", [])
    if not isinstance(layers, list):
        return [f"{s.get('name')}: layers must be a list, got {type(layers).__name__}"]
    out = []
    for L in layers:
        where = f"{s.get('name')}/{L.get('name', L.get('type')) if isinstance(L, dict) else '?'}"
        out += _guard(where, _check_layer, L, where, s.get("fps", 25))
    return out


def check_scenes(scenes):
    """Pre-flight for vfx.py scenes: every layer effect against the After Effects harvest, flash rates included. One
    malformed layer gives an issue of its own; the other layers are still checked."""
    if not isinstance(scenes, list):
        return kb_problems() + [f"scenes must be a list, got {type(scenes).__name__}"]
    issues = kb_problems()
    for i, s in enumerate(scenes):
        issues += _guard(f"scene {i + 1}", _check_scene, s)
    return list(dict.fromkeys(issues))


@lru_cache(None)
def _preset_contents():
    """.ffx path below the Presets folder (lower case, forward slashes) -> the match names inside it, from the harvest."""
    return {_preset_key(x["file"]): " ".join(x.get("match_names") or [])
            for x in (_harvest("harvest/files.json") or {}).get("ae_presets", [])}


def _preset_key(file):
    """'text/animate in/typewriter.ffx' for a harvested preset path (below its install's Presets folder)."""
    return str(file).replace("\\", "/").lower().split("/presets/", 1)[-1]


def _preset_match_names(file):
    """The harvested match names inside a preset however its path is spelt: below Presets (bridge specs), absolute
    under any install root or drive, either slash. The longest tail of the path that names a harvested preset wins."""
    parts, idx = str(file).replace("\\", "/").lower().split("/"), _preset_contents()
    return next((idx[k] for k in ("/".join(parts[i:]) for i in range(len(parts))) if k in idx), "")


def _preset_host(L):
    """Render-verified: text-animator presets do nothing off a text layer (a counter is one, vfx_build.jsx); shape
    presets need a shape layer and draw around its origin. A preset is a {file, at} object or (bridge specs) a path."""
    out = []
    for pr in _items(L.get("presets")):
        f = str((pr.get("file") if isinstance(pr, dict) else pr) or "")
        mn, name = _preset_match_names(f), Path(f.replace("\\", "/")).stem
        if "ADBE Text Animators" in mn and L.get("type") not in ("text", "counter"):
            out.append(f"preset {name!r} animates text but the layer is {L.get('type')!r}: it will do nothing")
        elif "ADBE Root Vectors Group" in mn and "ADBE Text" not in mn and L.get("type") != "shape":
            out.append(f"preset {name!r} is a shape preset but the layer is {L.get('type')!r}")
        elif "ADBE Root Vectors Group" in mn and L.get("type") == "shape" and not L.get("origin"):
            out.append(f"shape preset {name!r} draws around the layer origin, which is the comp's top-left corner here: "
                       f"give the layer an \"origin\" where the graphic should appear")
    return out


RECIPE_STACKS = {  # every effect stack a recipe tells the builder to apply -> the app that runs it
    "premiere": "PR", "after_effects": "AE",
    "premiere_out": "PR", "after_effects_out": "AE",  # on the outgoing clip of a transition recipe
    "after_effects_alt": "AE", "premiere_audio": "PR audio",
    "background.premiere": "PR", "background.after_effects": "AE",  # the plate under a card / quote recipe
}


def recipe_stacks(r):
    """(key, app, stack) for every RECIPE_STACKS stack a recipe carries."""
    for key, app in RECIPE_STACKS.items():
        v = r
        for k in key.split("."):
            v = v.get(k) if isinstance(v, dict) else None
        if v:
            yield key, app, v


def check_recipe(r):
    """Every stack of a recipe (RECIPE_STACKS, flash rates included) plus its bridge spec (both apps) and VFX scenes."""
    out = []
    for key, app, fx in recipe_stacks(r):
        out += check_effects(fx, app, key) + (_flash_rate(fx, key, 25, app) if app in ("AE", "PR") else [])
    if r.get("spec"):
        out += check_spec(r["spec"], ("AE", "PR"))
    if r.get("scenes"):
        out += check_scenes(r["scenes"])
    return out


def stats():
    c = Counter((x.get("kind"), x.get("app")) for x in cards())
    return {"cards": dict(sorted(((f"{k}:{a}", n) for (k, a), n in c.items()))), "ae_effects_harvested": len(ae_effects()),
            "pr_effects_harvested": len(pr_effects()), "pr_transitions": len(pr_transitions()),
            "verdicts": dict(sorted(Counter(_verdict(x) or "unverified" for x in cards()).items()))}


def _flags(c):
    """'[obsolete, broken] ' for a card that is not current or whose render (either half) did not pass, else ''."""
    top, ae = _verdicts(c)
    f = ([c["status"]] if c.get("status") not in (None, "current") else []) + ([top] if top and top != "pass" else []) \
        + ([f"AE half {ae}"] if ae and ae != "pass" else [])
    return f"[{', '.join(f)}] " if f else ""


def _usage(msg):
    print(f"{msg}\n{__doc__}", file=sys.stderr)
    return 2


def _main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    if cmd not in ("find", "card", "check", "scenes", "stats"):
        return _usage(f"unknown command {cmd!r}")
    kb = kb_problems()
    if cmd in ("find", "card", "stats"):
        for w in kb:
            print(f"WARNING: {w}", file=sys.stderr)
    if cmd == "find":
        opts, words, it = {}, [], iter(args)
        for a in it:
            if a in ("--kind", "--app", "--verdict"):
                opts[a[2:]] = next(it, None)
                if opts[a[2:]] is None:
                    return _usage(f"find: {a} needs a value")
            elif a.startswith("--"):
                return _usage(f"find: unknown option {a}")
            else:
                words.append(a)
        kinds = sorted({c.get("kind") for c in cards()} - {None})
        if "app" in opts:
            opts["app"] = APPS.get(opts["app"].lower(), opts["app"])
        if "kind" in opts and opts["kind"] not in kinds or "app" in opts and opts["app"] not in APPS.values() \
                or "verdict" in opts and opts["verdict"] not in ("untested", "unverified") \
                and not verdict_meaning(opts["verdict"]):
            print(f"find: --kind is one of {', '.join(kinds)}; --app one of AE, PR, both; --verdict one of "
                  f"{', '.join(VERDICTS)}, untested:<cause> ({', '.join(UNTESTED)}), untested or unverified",
                  file=sys.stderr)
            return 2
        hits = find(" ".join(words), **opts)
        for c in hits:
            print(f"[{c.get('app')} {c.get('kind')}] {c.get('name')}: {_flags(c)}{(c.get('summary') or '')[:110]}")
        if not hits:
            print("no cards matched", file=sys.stderr)
    elif cmd == "card":
        if not args or len(args) > 2:
            return _usage("card: needs NAME and an optional app (AE, PR or both)")
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            try:
                c = card(*args)
            except ValueError as e:
                return _usage(f"card: {e}")
        for w in seen:
            print(f"WARNING: {w.message}", file=sys.stderr)
        if c is None:
            near = [x.get("name") for x in find(args[0], app=APPS.get(args[1].lower()) if len(args) > 1 else None, limit=3)]
            print(f"no card {args[0]!r}" + (f"; closest: {'; '.join(near)}" if near else ""), file=sys.stderr)
        elif c.get("verified"):  # spell out what each verdict does (and does not) say
            top, ae = _verdicts(c)
            v = dict(c["verified"])
            if top:
                v["meaning"] = ("Premiere half: " if ae else "") + (verdict_meaning(top) or "not a legal verdict")
            if ae:
                v["ae"] = dict(v["ae"], meaning=verdict_meaning(ae) or "not a legal verdict")
            c = dict(c, verified=v)
        text = json.dumps(c, indent=1, ensure_ascii=False)
        try:
            text.encode(sys.stdout.encoding or "utf-8")
        except (UnicodeEncodeError, LookupError):  # a cp1252 pipe cannot carry Devanagari or the rupee sign
            text = json.dumps(c, indent=1)  # \u escapes: still the same JSON
        print(text)
    elif cmd in ("check", "scenes"):
        files, flags = [a for a in args if not a.startswith("--")], [a for a in args if a.startswith("--")]
        if len(files) != 1 or set(flags) - ({"--no-ae", "--no-pr"} if cmd == "check" else set()):
            return _usage(f"{cmd}: needs one JSON file" + (" (and optionally --no-ae or --no-pr)" if cmd == "check" else ""))
        try:  # only reading the file is guarded: a checker bug still shows its traceback
            data = _load(files[0])
        except (OSError, ValueError) as e:
            print(f"error: {cmd} {files[0]}: {type(e).__name__}: {e}", file=sys.stderr)
            return 2
        if cmd == "scenes" and isinstance(data, dict):
            data = data.get("scenes")
        if not isinstance(data, dict if cmd == "check" else list):
            print(f"error: {cmd} {files[0]}: expected " + ("a spec object" if cmd == "check" else
                  "a list of scenes or {\"scenes\": [...]}") + f", got {type(data).__name__}", file=sys.stderr)
            return 2
        apps = tuple(a for a, off in (("AE", "--no-ae"), ("PR", "--no-pr")) if off not in flags)
        if not apps:
            return _usage("check: --no-ae and --no-pr together leave no app to check")
        issues = check_spec(data, apps) if cmd == "check" else check_scenes(data)
        print("\n".join(issues) or "OK: nothing PVE objects to")
        return 2 if kb else 1 if any(not advisory(i) for i in issues) else 0
    elif cmd == "stats":
        print(json.dumps(stats(), indent=1))
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):  # never crash on a character the console's code page lacks: escape it
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(errors="backslashreplace")
    sys.exit(_main(sys.argv[1:]))
