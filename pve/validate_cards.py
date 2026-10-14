"""PVE: check a card file against the harvested ground truth for its batch.

    python pve/validate_cards.py pve/work/batches/ae_effect_03.json pve/cards/ae_effect_03.json

Exit 0 = clean. Otherwise prints one issue per line. Checks: one card per input item; required fields; status values;
verdicts (pve.VERDICTS or "untested:<cause>" with a cause from pve.UNTESTED, also in verified.previous); the "flash"
field; for effects: every `params[].name` and `demo_settings` key is a real property of that effect, numbers sit inside
the harvested range, booleans are booleans, colours are "#RRGGBB", points are [x, y]. A duplicate harvest row may be a
stub {"name", "kind", "app", "match", "merged_into": "<card file>"} pointing at the card it was merged into.
"""
import json, re, sys
from pathlib import Path

try:
    from pve import verdict_meaning
except ImportError:  # `pve` is the namespace package here (a caller did `from pve import pve`)
    from pve.pve import verdict_meaning

REQUIRED = {"name", "app", "kind", "status", "summary", "use_for", "confidence"}
STATUS = {"current", "obsolete", "hidden", "needs_input", "unusable"}
EFFECT_KINDS = {"ae_effect", "pr_effect", "pr_audio"}
FLASH_KEYS = {"per_frame", "period", "duration", "rate", "per_use", "quiet", "basis"}  # see the flash notes in pve.py


def check_verdicts(c):
    """Legal verdict values on one card (and on a recipe's After Effects half, verified.ae) -> issue strings."""
    v = c.get("verified") or {}
    ae = v.get("ae") or {}
    out = []
    for x in ([v] + list(v.get("previous") or []) if v else []) + ([ae] + list(ae.get("previous") or []) if ae else []):
        if x.get("verdict") is not None and not verdict_meaning(x.get("verdict")):
            out.append(f"{c.get('name')}: verdict {x.get('verdict')!r} is not legal (pve.VERDICTS or untested:<pve.UNTESTED cause>)")
    return out


def check_flash(c, props):
    """The "flash" field: known keys, a basis, timing properties that exist on the item, sane numbers."""
    f, n = c.get("flash"), c.get("name")
    if f is None:
        return []
    if not isinstance(f, dict) or not f.get("basis"):
        return [f"{n}: flash must be an object with a \"basis\" (where its timing numbers come from)"]
    out = [f"{n}: flash key {k!r} not in {sorted(FLASH_KEYS)}" for k in f if k not in FLASH_KEYS]
    out += [f"{n}: flash {k} {f[k]!r} is not a property of this item" for k in ("per_frame", "period", "duration")
            if k in f and f[k] not in props]
    out += [f"{n}: flash quiet {p!r} is not a property of this item" for p in f.get("quiet") or {} if p not in props]
    if "rate" in f and (isinstance(f["rate"], bool) or not isinstance(f["rate"], (int, float)) or f["rate"] < 0):
        out.append(f"{n}: flash rate must be a number >= 0")
    if "per_use" in f and (isinstance(f["per_use"], bool) or not isinstance(f["per_use"], int) or f["per_use"] < 1):
        out.append(f"{n}: flash per_use must be a whole number >= 1")
    return out


def check_stub(c, card_path):
    """A merged duplicate: its target file must hold the real card for the same item."""
    target = Path(card_path).parent / c["merged_into"]
    try:
        there = json.loads(target.read_text(encoding="utf-8"))["cards"]
    except Exception:  # noqa: BLE001
        return [f"{c.get('name')}: merged_into {c['merged_into']!r} is not a readable card file"]
    same = [x for x in there if not x.get("merged_into") and
            (x.get("kind"), x.get("name"), x.get("match")) == (c.get("kind"), c.get("name"), c.get("match"))]
    return [] if same else [f"{c.get('name')}: merged_into {c['merged_into']!r} holds no card for this item"]


def leaf_props(item):
    """name -> spec. Premiere names repeat (Lumetri 'Contrast'): the 2nd is addressable as 'Contrast#2'."""
    out, seen = {}, {}
    for p in item.get("props", []):
        n = p.get("name")
        if not n or p.get("type") == "header":
            continue
        seen[n] = seen.get(n, 0) + 1
        key = n if seen[n] == 1 else f"{n}#{seen[n]}"
        out[key] = p
        if seen[n] == 1:
            out[f"{n}#1"] = p
    return out


def check_value(name, spec, v, app):
    t = spec.get("type")
    if app == "AE":
        if t in ("1d",):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return f"{name}: expects a number, got {v!r}"
        elif t in ("2d", "2d_spatial", "3d", "3d_spatial"):
            if not (isinstance(v, list) and len(v) in (2, 3) and all(isinstance(x, (int, float)) for x in v)):
                return f"{name}: expects [x, y] (pixels), got {v!r}"
        elif t == "color":
            if not (isinstance(v, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", v)):
                return f"{name}: expects \"#RRGGBB\", got {v!r}"
        elif t in ("custom", "layer", "mask", "shape", "text", "marker", "none"):
            return f"{name}: type {t} cannot be set from a spec"
    else:
        if t == "bool" and not isinstance(v, bool):
            return f"{name}: expects true/false, got {v!r}"
        if t == "number" and (isinstance(v, bool) or not isinstance(v, (int, float))):
            return f"{name}: expects a number, got {v!r}"
        if t == "color" and not (isinstance(v, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", v)):
            return f"{name}: expects \"#RRGGBB\", got {v!r}"
        if t == "point" and not (isinstance(v, list) and len(v) == 2):
            return f"{name}: expects [x, y] as frame fractions (0.5, 0.5 = centre), got {v!r}"
        if t in ("json", "string"):
            return f"{name}: type {t} is not settable from a spec"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and hi is not None and lo > hi:  # a broken harvested range (uint32 probe artefact): no range
            lo = hi = None
        if lo is not None and v < lo - 1e-9:
            return f"{name}: {v} below the harvested minimum {lo}"
        if hi is not None and v > hi + 1e-9:
            return f"{name}: {v} above the harvested maximum {hi}"
    return None


def main(batch_path, card_path):
    batch = json.loads(Path(batch_path).read_text(encoding="utf-8"))
    try:
        cards = json.loads(Path(card_path).read_text(encoding="utf-8"))["cards"]
    except Exception as e:  # noqa: BLE001
        print(f"card file unreadable: {e}")
        return 1
    kind = batch["kind"]
    issues = []
    by_key = {}
    for c in cards:
        by_key.setdefault((c.get("name"), c.get("match")), []).append(c)
    for it in batch["items"]:
        key = (it.get("name"), it.get("match"))
        got = by_key.get(key) or [c for c in cards if c.get("name") == it.get("name")]
        if not got:
            issues.append(f"missing card for {it.get('name')!r}")
            continue
        if len(got) > 1:
            issues.append(f"{len(got)} cards for {it.get('name')!r}")
        c = got[0]
        if c.get("merged_into"):
            issues += check_stub(c, card_path)
            continue
        issues += check_verdicts(c) + check_flash(c, leaf_props(it))
        miss = [f for f in REQUIRED if not c.get(f)]
        if miss:
            issues.append(f"{it['name']}: missing {miss}")
        if c.get("status") and c["status"] not in STATUS:
            issues.append(f"{it['name']}: status {c['status']!r} not in {sorted(STATUS)}")
        if c.get("kind") and c["kind"] != kind:
            issues.append(f"{it['name']}: kind {c['kind']!r} should be {kind!r}")
        if kind in EFFECT_KINDS:
            props = leaf_props(it)
            for p in c.get("params", []):
                if p.get("name") not in props:
                    issues.append(f"{it['name']}: params lists {p.get('name')!r}, not a property (have: {', '.join(list(props)[:60])})")
            for n, v in (c.get("demo_settings") or {}).items():
                if n not in props:
                    issues.append(f"{it['name']}: demo_settings {n!r} is not a property")
                    continue
                err = check_value(n, props[n], v, it["app"])
                if err:
                    issues.append(f"{it['name']}: {err}")
            # props empty = effect has no controls (Vertical Flip, Black & White): applying it is the demo
            if c.get("status") == "current" and not c.get("demo_settings") and kind != "pr_audio" and props:
                issues.append(f"{it['name']}: current effect needs demo_settings that make it clearly visible")
    names = {it.get("name") for it in batch["items"]}
    for c in cards:
        if c.get("name") not in names:
            issues.append(f"card {c.get('name')!r} is not in the batch")
    for i in issues:
        print(i)
    print(f"{len(cards)} cards, {len(issues)} issues")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
