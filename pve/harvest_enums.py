"""PVE: After Effects dropdown (popup) labels -> pve/harvest/ae_enum_labels.json

    python pve/harvest_enums.py              # read the labels from the installed plug-ins' string resources (offline)
    python pve/harvest_enums.py --readback   # live: also set every candidate index in After Effects and read it back

ae_effects.json stores a popup as a plain 1d number (min 1, max N): the labels live in the plug-ins. Adobe effects
carry their English UI strings as ZStrings ("$$$/AE/Ramp/LStr/0008=Linear Ramp|Radial Ramp"), third-party ones as
plain C strings with '|' between the items, pseudo effects in PresetEffects.xml, and the text animator / selector
menus in the core string table (BEE.dll). A numbered table lists the menus in parameter order, so it is aligned with
the popups by item count (= the harvested max); plain strings only give a list that is unique in size or next to the
property's name. --readback applies each effect in After Effects, sets 1..N and 1.5 on every candidate (never an
out-of-range value: that kills an AE script) and records what comes back and whether the property takes only Hold
keyframes, which tells a popup from an integer slider. After Effects 26.0 also exposes
Property.propertyParameters (the menu's items) and Property.valueText (the current item's text): where they answer,
the API's own list is the label set and the string-table list is its cross-check. AE scripts may not write files
here, so each batch's JSON goes to Premiere over BridgeTalk (Premiere must be running), as in harvest_ae.py.
"""
import json, re, sys, time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import adobe_bridge as ab  # noqa: E402

SUPPORT = ab.AFTERFX.parent  # <After Effects 2026>/Support Files
OUT = HERE / "harvest" / "ae_enum_labels.json"
LIVE = HERE.parent / "work" / "pve_live" / "wave3"
READBACK = LIVE / "enum_readback.json"
CORE = ("BEE.dll", "FLT.dll")  # core string tables: text animator / selector menus; Compositing Options
RUN = re.compile(rb"(?:[\x20-\x7e]|[\xc2-\xf4][\x80-\xbf]{1,3}){2,}")  # printable ASCII / UTF-8 runs
ZSTR = re.compile(r"^\$\$\$/(?:AE/)?([^/=]+)/(?:LStr/(\d+)|[^=]*)=(.*)$", re.S)
ESC = {"#{nbsp}": " ", "#{rsquo}": "'", "#{lsquo}": "'", "#{ldquo}": '"', "#{rdquo}": '"', "#{cr}": " ", "#{copy}": "(c)",
       "#{deg}": " deg", "#{emdash}": "-", "#{endash}": "-", "#{reg}": "(R)", "#{trade}": "(TM)", "#{hellip}": "...",
       "^}": "'", "^[": "[", "^]": "]", "^^": "^", "^Q": '"', "^q": "'"}
SEP = "(-"  # an AE popup separator: occupies an index, not selectable in the UI


def unescape(s):
    for k, v in ESC.items():
        s = s.replace(k, v)
    return re.sub(r"\{\{\*[^}]*\*\}\}", "", s).strip()


def norm(s):
    return re.sub(r"\s+", " ", unescape(s)).strip().lower()


def strings(data):
    """Index of a binary's printable runs: {"strs": [(i, key, num, text)], "lists": [(s, items)], "names": {norm: [s]},
    "runs": set, "pascal": set}. ZStrings give their plug-in key, LStr number (None for other keys) and the text after
    '='; plain strings give key None, num None; i = run order. pascal: length-prefixed strings (a PiPL's match name)."""
    strs, runs, pascal = [], set(), set()
    for m in RUN.finditer(data):
        t = m.group().decode("utf-8", "replace")
        runs.add(t)
        n = data[m.start() - 1] if m.start() else 0
        if 3 <= n <= len(t):
            pascal.add(t[:n])
        if len(t) > 3 and 3 <= ord(t[0]) <= len(t) - 1:  # the length byte itself was printable
            pascal.add(t[1:1 + ord(t[0])])
        z = ZSTR.match(t) if t.startswith("$$$/") else None
        strs.append((len(strs), z.group(1), int(z.group(2)) if z.group(2) else None, z.group(3)) if z else
                    (len(strs), None, None, t))
    lists, names = [], {}
    for s in strs:
        xs = items(s[3], strict=s[1] is None)
        if xs:
            lists.append((s, xs))
        elif len(s[3]) <= 80:
            names.setdefault(norm(s[3]), []).append(s)
    return {"strs": strs, "lists": lists, "names": names, "runs": runs, "pascal": pascal}


def items(text, strict=False):
    """A popup list's items, or None when the text is not one ('|'-separated, 2+ short items). strict (plain strings,
    not ZStrings): mostly letters, and some item has a 3-letter word, so binary noise holding a '|' is not a menu."""
    if "|" not in text or len(text) > 2000:
        return None
    xs = [unescape(x) for x in text.split("|")]
    if len(xs) < 2 or any(len(x) > 64 or not x for x in xs) or any(re.search(r"[%\\{}]", x) for x in xs):
        return None
    if strict and (any(x != SEP and (sum(ch.isalpha() or ch == " " for ch in x) < 0.5 * len(x)
                                     or not re.match(r"[A-Z0-9(]", x) or re.search(r"[;@^`=]", x)) for x in xs)
                   or not any(re.search(r"[A-Za-z]{3}", x) for x in xs)):
        return None
    return xs


def pos(s):
    """Where a string sits for distance purposes: (plug-in key, LStr number) for numbered ZStrings, else run order."""
    i, key, num, _ = s
    return (key, num) if num is not None else (None, i)


def dist(a, b):
    pa, pb = pos(a), pos(b)
    return abs(pa[1] - pb[1]) if pa[0] == pb[0] else None


def match_popup(ix, name, count=None):
    """Nearest label list (with `count` items; None = any) to a string equal to `name` in a string index: for core
    tables, where a menu sits right before or after its name. {labels, source, basis, distance, options,
    confidence} or None."""
    lists = [(s, xs) for s, xs in ix["lists"] if count is None or len(xs) == count]
    best = None
    for a in ix["names"].get(norm(name), []):
        for s, xs in lists:
            d = dist(a, s)
            if d:
                key = (d, 0 if pos(s)[1] > pos(a)[1] else 1)
                if best is None or key < best[0]:
                    best = (key, s, xs, a)
    if not best:
        return None
    (d, _), s, xs, a = best
    return {"labels": xs, "source": src_of(s), "basis": "name+count" if count else "name", "distance": d,
            "options": len(lists), "confidence": "high" if d <= 2 else "low"}


def pool(ix):
    """The label lists to align with an effect's popups: its numbered ZStrings (Adobe plug-ins: table order) when it
    has any, else every list in file order (third-party plug-ins, Adobe ones with named ZStrings)."""
    z = sorted(((s, xs) for s, xs in ix["lists"] if s[2] is not None), key=lambda t: pos(t[0]))
    return z or list(ix["lists"])


def align(counts, lists):
    """Order-preserving matching of popups (item counts, parameter order) to lists (table order) on equal counts,
    as many pairs as possible. Per popup: (the lists it pairs with in SOME maximum matching, whether some maximum
    matching leaves it out)."""
    n, m = len(counts), len(lists)
    ok = [[counts[i] == len(lists[j]) for j in range(m)] for i in range(n)]
    pre = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            pre[i][j] = max(pre[i - 1][j], pre[i][j - 1], pre[i - 1][j - 1] + ok[i - 1][j - 1])
    suf = [[0] * (m + 2) for _ in range(n + 2)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            suf[i][j] = max(suf[i + 1][j], suf[i][j + 1], suf[i + 1][j + 1] + ok[i][j])
    M = pre[n][m]
    out = []
    for i in range(n):
        pairs = [j for j in range(m) if ok[i][j] and pre[i][j] + 1 + suf[i + 1][j + 1] == M]
        skip = any(pre[i][j] + suf[i + 1][j] == M for j in range(m + 1))
        out.append((pairs, skip))
    return out


def match_effect(ix, cands):
    """{q: match record or None} for an effect's popup candidates [(q, name, count)] in one plug-in binary. A numbered
    ZString table (Adobe plug-ins) lists the menus in parameter order, so it is aligned with the candidates; plain
    strings (third-party plug-ins) come in no useful order, so a list is only taken when it is the plug-in's one list
    with that many items (else the record names the candidate lists, labels None)."""
    lists = pool(ix)
    if not any(s[2] is not None for s, _ in lists):
        out = {}
        for q, name, count in cands:
            opts = [(s, xs) for s, xs in lists if len(xs) == count]
            near = [(s, xs) for s, xs in opts if any(0 < (dist(a, s) or 0) <= 2 for a in ix["names"].get(norm(name), []))]
            if len(opts) == 1:
                out[q] = {"labels": opts[0][1], "source": src_of(opts[0][0]), "basis": "the only list with this many "
                          "items in the plug-in's strings", "same_count_lists": 1, "confidence": "medium"}
            elif len(near) == 1:
                out[q] = {"labels": near[0][1], "source": src_of(near[0][0]), "basis": "the one same-size list within "
                          "2 strings of the property name", "same_count_lists": len(opts), "confidence": "medium"}
            elif opts:
                out[q] = {"labels": None, "candidates": ["|".join(xs) for _, xs in opts][:8],
                          "unmatched": f"{len(opts)} lists with this many items; plain strings give no order to pick by"}
            else:
                out[q] = None
        return out
    res = align([c for _, _, c in cands], [xs for _, xs in lists])
    used = {j for pairs, _ in res for j in pairs[:1]}
    out = {}
    for (q, name, count), (pairs, skip) in zip(cands, res):
        if not pairs:
            out[q] = None
            continue
        near = {}
        for j in pairs:
            ds = [dist(a, lists[j][0]) for a in ix["names"].get(norm(name), [])]
            ds = [d for d in ds if d]
            near[j] = min(ds) if ds else None
        named = [j for j in pairs if near[j] is not None and near[j] <= 3]
        j = named[0] if len(named) == 1 else pairs[0]
        unique = len(pairs) == 1 and not skip
        same = sum(1 for _, xs in lists if len(xs) == count)
        full = len(used) == len(lists)
        conf = ("high" if unique and (full or named or same == 1) or same == 1 else
                "medium" if unique or len(named) == 1 else "low")
        out[q] = {"labels": lists[j][1], "source": src_of(lists[j][0]), "basis": "table order + count",
                  "unique": unique, "name_distance": near[j], "same_count_lists": same, "table_consumed": full,
                  "confidence": conf}
    return out


def src_of(s):
    i, key, num, t = s
    if key and num is not None:
        return f"$$$/AE/{key}/LStr/{num:04d}"
    if key:
        return f"ZString {key}"
    return f"string #{i}"


def popup_candidates(effect):
    """[(path, prop)] of the harvested properties that can be popups: 1d, min 1, whole max 2..256, whole default.
    A name repeated inside one group gets '#2', '#3' (the PVE convention for repeated names)."""
    out = []

    def walk(ps, path, mpath):
        seen = Counter()
        for p in ps:
            seen[p["name"]] += 1
            nm = p["name"] + (f"#{seen[p['name']]}" if seen[p["name"]] > 1 else "")
            q = f"{path}/{nm}" if path else nm
            if p.get("props"):
                walk(p["props"], q, mpath + [p["match"]])
            elif (p.get("type") == "1d" and p.get("min") == 1 and isinstance(p.get("max"), (int, float))
                  and float(p["max"]).is_integer() and 2 <= p["max"] <= 256
                  and isinstance(p.get("default"), (int, float)) and float(p["default"]).is_integer()):
                out.append((q, dict(p, mpath=mpath + [p["match"]])))
    walk(effect.get("props") or [], "", [])
    return out


def binaries(root):
    """{plug-in path: string index} of every .aex under Plug-ins."""
    return {f: strings(f.read_bytes()) for f in sorted(Path(root, "Plug-ins").rglob("*.aex"))}


def find_binary(match, bins):
    """The plug-in binaries registering this match name: as a length-prefixed string (the PiPL), else as a whole
    C string."""
    return [f for f, ix in bins.items() if match in ix["pascal"]] or [f for f, ix in bins.items() if match in ix["runs"]]


def pseudo_effects(root):
    """{match: {name: (labels, source)}} from PresetEffects.xml (pseudo effects such as Timer / Text Box controls)."""
    p = Path(root, "PresetEffects.xml")
    if not p.exists():
        return {}
    out = {}
    for ef in ET.parse(p).getroot().iter("Effect"):
        for pu in list(ef.iter("Popup")) + list(ef.iter("Popup_UTF8")):
            nm = unescape(pu.get("name", "").split("=", 1)[-1])
            raw = pu.get("popup_string", "")
            out.setdefault(ef.get("matchname"), {})[nm] = (items(raw.split("=", 1)[-1] if raw.startswith("$$$/") else raw),
                                                          "PresetEffects.xml " + (raw.split("=", 1)[0] if raw.startswith("$$$/") else "popup_string"))
    return out


def usage(cards_dir, files_json):
    """How often each AE effect match name is used: shipped presets + recipe after_effects stacks + VFX scenes."""
    c = Counter()
    for p in json.loads(Path(files_json).read_text(encoding="utf-8"))["ae_presets"]:
        c.update(m for m in set(p.get("match_names") or []) if not re.search(r"-\d{4}$", m))
    for f in sorted(Path(cards_dir).glob("recipe_*.json")):
        for r in json.loads(f.read_text(encoding="utf-8")).get("cards", []):
            for e in r.get("after_effects") or []:
                c[e.get("name")] += 3
            for s in r.get("scenes") or []:
                for L in s.get("layers", []):
                    for e in L.get("effects", []):
                        c[e.get("name")] += 3
    return c


HEDGE = re.compile(r"assumed identical|labels? (?:are |is )?(?:unverified|not verified|not harvested|unknown)|unverified "
                   r"(?:enum|label|index|order)|index(?:es)? (?:order )?unverified|order unverified|labels? not "
                   r"(?:harvested|confirmed)|other labels|index order", re.I)


def hedged(cards_dir):
    """Match names of AE effect cards whose text hedges on popup labels or index order."""
    out = set()
    for f in sorted(Path(cards_dir).glob("ae_effect_*.json")):
        for c in json.loads(f.read_text(encoding="utf-8")).get("cards", []):
            if c.get("match") and HEDGE.search(json.dumps({k: c.get(k) for k in ("summary", "params", "avoid",
                                                                                 "scripting", "enum_labels", "use_for")})):
                out.add(c["match"])
    return out


def scan(root=SUPPORT, harvest=HERE / "harvest" / "ae_effects.json", cards_dir=HERE / "cards", sliders=None, index=None):
    """Offline label harvest: {effects: {match: {...popups}}, core: {...}, stats}. sliders: {match: {q}} the readback
    showed to be integer sliders, left out of the alignment so they cannot take a menu's place. index: (binaries,
    core) from an earlier call, to skip re-reading the plug-ins."""
    sliders = sliders or {}
    H = json.loads(Path(harvest).read_text(encoding="utf-8"))
    bins, core = index or (binaries(root), {n: strings(Path(root, n).read_bytes()) for n in CORE
                                             if Path(root, n).exists()})
    pseudo = pseudo_effects(root)
    used, hed = usage(cards_dir, HERE / "harvest" / "files.json"), hedged(cards_dir)
    effects, stats = {}, Counter()
    for e in H:
        cands = popup_candidates(e)
        if not cands or e.get("error"):
            continue
        row = {"name": e["name"], "category": e.get("category"), "uses": used.get(e["match"], 0),
               "hedged_card": e["match"] in hed, "binary": None, "popups": {}}
        files = find_binary(e["match"], bins)
        skip = sliders.get(e["match"], set())
        best = None
        for f in files:  # the binary that explains most candidates wins (CC Composite sits in two)
            got = match_effect(bins[f], [(q, p["name"], int(p["max"])) for q, p in cands
                                                if q not in skip and p["match"] != "ADBE Force CPU GPU"])
            score = (sum(1 for g in got.values() if g and g.get("confidence") in ("high", "medium")), "/Effects/" in f.as_posix())
            if best is None or score > best[0]:  # ties: an effect plug-in over a format / import one
                best = (score, f, got)
        ps = pseudo.get(e["match"], {})
        for q, p in cands:
            rec = {"match": p["match"], "count": int(p["max"]), "default": int(p["default"])}
            g = best[2].get(q) if best else None
            if p["match"] == "ADBE Force CPU GPU" and core.get("FLT.dll"):
                g = match_popup(core["FLT.dll"], p["name"], int(p["max"]))
                if g:
                    g["source"] = "<AE Support Files>/FLT.dll " + g["source"]
            elif p["name"] in ps and ps[p["name"]][0] and len(ps[p["name"]][0]) == int(p["max"]):
                g = {"labels": ps[p["name"]][0], "source": "<AE Support Files>/" + ps[p["name"]][1],
                     "basis": "name+count", "confidence": "high"}
            elif g:
                g["source"] = f"{rel_bin(best[1], root)} {g['source']}" if g.get("source") else rel_bin(best[1], root)
            if g and g.get("labels") is None:  # several same-size lists, none to pick
                rec.update(g)
                stats["ambiguous"] += 1
            elif g:
                rec.update(labels={str(i + 1): x for i, x in enumerate(g.pop("labels"))}, **g)
                if SEP in rec["labels"].values():
                    rec["separators"] = [int(k) for k, v in rec["labels"].items() if v == SEP]
                stats["labelled"] += 1
                stats[f"conf_{rec['confidence']}"] += 1
            else:
                rec["labels"] = None
                rec["unmatched"] = "no list with this many items in the plug-in's strings" if files else "plug-in binary not found"
                stats["unmatched"] += 1
            row["popups"][q] = rec
        if best:
            row["binary"] = rel_bin(best[1], root)
        effects[e["match"]] = row
    return {"effects": effects, "stats": dict(stats), "core": core, "index": (bins, core)}


def rel_bin(f, root):
    return "<AE Support Files>/" + Path(f).relative_to(root).as_posix()


# text animator / selector menus: property name -> names as the core string table spells them
TEXT_NAMES = {"Based On": "Based On", "Shape": "Shape", "Mode": "Mode", "Units": "Units"}


def text_menus(core, readback=None):
    """Labels for the text-layer popups (animator properties, selectors, More Options) from BEE.dll, by name + count.
    The readback lists every Hold-only 1d text property; a popup is one with min 1 and a max of 2 or more."""
    strs = core.get("BEE.dll") or {"lists": [], "names": {}}
    out = {}
    for r in (readback or {}).get("text", []):
        if not (r.get("names") and r.get("api_min") == 1 and (r.get("api_max") or 0) >= 2 and r.get("linear_ok") is False):
            continue
        path, n = "/".join(r["names"]), int(r["api_max"])
        g = match_popup(strs, r["name"], n)
        rec = {"match": r["match"], "count": n, "readback": slim(r)}
        if g:
            rec.update(labels={str(i + 1): x for i, x in enumerate(g.pop("labels"))}, **g)
            rec["source"] = "<AE Support Files>/BEE.dll " + rec["source"]
        else:
            rec["labels"], rec["unmatched"] = None, "no list with this many items next to the name in BEE.dll"
        api_labels(rec, r)
        if r.get("value_text_now") and rec.get("labels") is None:
            rec["current_value_text"] = r["value_text_now"]
        out[path] = rec
    if not out:  # offline only: the selector menus by name
        for nm in TEXT_NAMES.values():
            g = match_popup(strs, nm)
            if g and g["distance"] <= 2:
                out[f"Range Selector/Advanced/{nm}"] = {"count": len(g["labels"]), "labels": {
                    str(i + 1): x for i, x in enumerate(g.pop("labels"))}, **g,
                    "source": "<AE Support Files>/BEE.dll " + g["source"], "note": "count from the list itself (no readback)"}
    return out


def slim(r):
    """The readback facts kept per popup: Hold-only keys, API range, how many indices were set and read back, what
    1.5 came back as, and any refusal."""
    out = {k: r[k] for k in ("linear_ok", "api_min", "api_max", "half", "error", "mismatch") if k in r}
    if r.get("parameters"):
        out["api_menu_items"] = len(r["parameters"])
    if "settable" in r:
        out["set_and_read_back"] = len(r["settable"])
    return out


# ---------------- live readback ----------------
def jobs(H, effects):
    """Per AE effect index: the candidate properties to probe (match-name paths from the effect root)."""
    idx = {e["match"]: e["i"] for e in H}
    out = []
    for e in H:
        if e["match"] not in effects:
            continue
        out.append({"i": idx[e["match"]], "match": e["match"], "props": [
            {"path": p["mpath"], "name": q, "max": int(p["max"]), "default": int(p["default"])}
            for q, p in popup_candidates(e)]})
    return out


def run_batch(batch, text, n, timeout=180):
    LIVE.mkdir(parents=True, exist_ok=True)
    out = LIVE / f"enum_rb_{n:03d}.json"
    out.unlink(missing_ok=True)
    hv = {"out": out.as_posix(), "scratch": (LIVE / f"_enum_scratch_{n:03d}.aep").as_posix(), "jobs": batch, "text": text}
    ab.run_in_ae("var HV = " + json.dumps(hv) + ";\n" + (HERE / "harvest_enums.jsx").read_text(encoding="ascii"),
                 LIVE / f"_enum_job_{n:03d}.jsx")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if out.exists() and out.stat().st_size:
            time.sleep(1)
            return json.loads(out.read_text(encoding="utf-8"))
        time.sleep(1)
    return None


# They open a modal file dialog (applied, or when a popup reaches its "Browse..." item), which blocks the batch: the
# first full readback lost two minutes and an AE restart on each.
DIALOG_EFFECTS = {"ADBE Apply Color LUT", "ADBE Apply Color LUT2", "ADBE Lumetri", "ADBE ProfileToProfile"}


def readback(H, effects, batch=20):
    """Probe every candidate in After Effects; walk a batch one effect at a time when it does not come back."""
    ab.ensure_premiere()
    if not [t for t in ab.ae_titles() if t.startswith("Adobe After Effects 20")]:
        ab.restart_ae()  # a cold `-r` start answers neither Crash Repair nor the cache warning
    todo = [j for j in jobs(H, effects) if j["match"] not in DIALOG_EFFECTS]
    res, n = {"effects": {}, "text": [], "reflect": None, "skipped": sorted(DIALOG_EFFECTS & set(effects))}, 0
    first = run_batch([], True, n)
    if first is None:
        ab.restart_ae()
        first = run_batch([], True, n)
    if first is None:
        sys.exit("After Effects / Premiere did not return the text-selector probe")
    res["text"], res["reflect"] = first["text"], first.get("api_reflect")
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        n += 1
        got = run_batch(part, False, n)
        if got is None:
            ab.restart_ae()
            for j in part:
                n += 1
                one = run_batch([j], False, n, timeout=120)
                if one is None:
                    res["skipped"].append(j["match"])
                    ab.restart_ae()
                else:
                    res["effects"].update(one["effects"])
        else:
            res["effects"].update(got["effects"])
            res["reflect"] = res["reflect"] or got.get("api_reflect")
        print(f"readback {min(i + batch, len(todo))}/{len(todo)}", flush=True)
    READBACK.write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


def verdict(rb):
    """What the readback says about one candidate: popup (the API lists a menu for it, or Hold-only keys with 1..N
    kept), slider (no menu, or linear keys allowed), unprobed or unclear."""
    if not rb or (rb.get("error") and not rb.get("parameters")):
        return "unprobed"
    if rb.get("parameters"):
        return "popup"  # the API lists its menu
    if "does not have dropdown" in str(rb.get("parameters_error")):
        return "slider"
    if rb.get("linear_ok") is False and rb.get("api_min") == 1 and rb.get("settable") == list(
            range(1, int(rb.get("api_max") or 0) + 1)):
        return "popup"
    if rb.get("linear_ok"):
        return "slider"
    return "unclear"


def merge(scanned, rb):
    """Fold the readback into the scanned effects: per popup readback facts, kind (popup / slider) and a drop of label
    lists on candidates the API shows to be sliders."""
    st = Counter()
    for m, row in scanned["effects"].items():
        got = (rb or {}).get("effects", {}).get(m, {})
        for q, rec in row["popups"].items():
            r = got.get(q)
            if r is None:
                rec["kind"] = "unprobed"
                continue
            rec["readback"] = slim(r)
            rec["kind"] = verdict(r)
            if rec["kind"] == "slider" and rec.get("labels"):
                rec["dropped_labels"], rec["labels"] = rec["labels"], None
                rec["unmatched"] = "the API shows an integer slider (linear keyframes allowed)"
            if rec["kind"] == "popup":
                st[api_labels(rec, r)] += 1
            st[rec["kind"]] += 1
            st[f"{rec['kind']}_labelled" if rec.get("labels") else f"{rec['kind']}_unlabelled"] += 1
    return st


def api_labels(rec, r):
    """Put the labels AE itself reports on a popup record: Property.propertyParameters (the whole menu, AE 26.0), else
    Property.valueText read after setting each index. Agreement with the string-table list confirms it; where they
    differ or the table had none, AE's own list wins (the table's moves to string_table_labels). Returns a stats key."""
    n = rec.get("count") or 0
    pl = r.get("parameters") or []
    vt = {k: v for k, v in (r.get("value_texts") or {}).items() if v not in ("", "undefined", "null")}
    if n and len(pl) == n:
        api, how = {str(i + 1): unescape(x) for i, x in enumerate(pl)}, "Property.propertyParameters"
        if vt and any(vt.get(k) not in (None, v) for k, v in api.items()):
            rec["value_text_differs"] = vt
    elif n and len(vt) >= n:
        api, how = {str(i): vt[str(i)] for i in range(1, n + 1)}, "Property.valueText after setting each index"
    else:
        return "api_labels_missing"
    old = rec.get("labels")
    same = old is not None and all(norm(old[k]) == norm(api[k]) for k in api if old[k] != SEP)
    rec["api_labels_agree"] = same if old is not None else None
    if same:
        rec["confirmed_by"] = f"AE 26.0 scripting {how}"
        return "api_confirms_table"
    if old is not None:
        rec["string_table_labels"] = old
    rec.update(labels=api, source=f"AE 26.0 scripting: {how}", basis="API readback", confidence="high")
    rec.pop("unmatched", None)
    rec.pop("candidates", None)
    return "api_differs_from_table" if old is not None else "api_only"


def build(do_readback=False, index=None):
    """Scan (and with do_readback probe AE first), then write OUT. Without do_readback an earlier readback file
    (READBACK) is used when present. index: a binaries() / core pair from an earlier scan, to skip re-reading."""
    H = json.loads((HERE / "harvest" / "ae_effects.json").read_text(encoding="utf-8"))
    if do_readback:
        rb = readback(H, {e["match"] for e in H if popup_candidates(e) and not e.get("error")})
    else:
        rb = json.loads(READBACK.read_text(encoding="utf-8")) if READBACK.exists() else None
    sliders = {m: {q for q, r in v.items() if isinstance(r, dict) and verdict(r) == "slider"}
               for m, v in (rb or {}).get("effects", {}).items()}
    s = scan(sliders=sliders, index=index)
    st = merge(s, rb) if rb else {}
    eff = s["effects"]
    pops = [p for r in eff.values() for p in r["popups"].values()]
    lab = [p for p in pops if p.get("labels")]
    doc = {
        "about": "After Effects popup (dropdown) labels for the harvested effects, index -> label (1-based, as the "
                 "scripting API and PVE specs count them). Labels are the English UI strings the installed plug-ins "
                 "carry; each popup records where its list came from and how it was matched to the property.",
        "method": {
            "source": "where the readback answered (basis 'API readback' or confirmed_by): the menu After Effects "
                      "itself lists; else string resources inside each plug-in binary (Adobe ZStrings "
                      "'$$$/AE/<plugin>/LStr/<n>=a|b|c', third-party plain C strings), PresetEffects.xml for pseudo "
                      "effects, BEE.dll for the text animator / selector menus and FLT.dll for Compositing Options",
            "matching": "a candidate is a 1d property with min 1 and a whole max N (the harvest). A numbered ZString "
                        "table lists the menus in parameter order, so its '|'-lists are aligned with the candidates "
                        "on equal item counts, order preserved (basis 'table order + count'; unique = the alignment "
                        "allows no other list); plain strings come in no useful order, so a list is taken only when it "
                        "is the plug-in's one list of that size or the one beside the property's name; core tables "
                        "by name + count (the list next to the name string)",
            "readback": "After Effects 26.0 scripting, every candidate on a solid: Property.propertyParameters (the "
                        "menu's items; it throws 'does not have dropdown menu options' on a slider), then 1..N set and "
                        "read back with Property.valueText (the current item's text), 1.5 (a popup snaps it) and "
                        "isInterpolationTypeValid(LINEAR) (false = Hold-only keyframes). Where the API lists a menu, "
                        "its items are the labels; the string-table list either matches (confirmed_by) or is kept as "
                        "string_table_labels. Effects that open a file dialog (DIALOG_EFFECTS) are not probed",
            "separators": "'(-' is a menu separator: it occupies an index (counted in N) but cannot be picked in "
                          "the UI",
            "cards": "uses = how often the match name appears in shipped presets (1 each) and recipe stacks (3 "
                     "each); hedged_card = the effect's card hedges on its labels or index order (read-only check, "
                     "cards not edited)"},
        "stats": {"effects_with_candidates": len(eff), "candidates": len(pops), "labelled": len(lab),
                  "by_confidence": dict(Counter(p["confidence"] for p in lab)),
                  "by_basis": dict(Counter(p["basis"] for p in lab)),
                  "hedged_cards": sum(1 for r in eff.values() if r["hedged_card"]),
                  "hedged_cards_all_labelled": sum(1 for r in eff.values() if r["hedged_card"] and all(
                      p.get("labels") or p.get("kind") == "slider" for p in r["popups"].values())),
                  "top50_used_all_labelled": sum(1 for r in sorted(eff.values(), key=lambda r: -r["uses"])[:50] if all(
                      p.get("labels") or p.get("kind") == "slider" for p in r["popups"].values())),
                  "readback": dict(st) if rb else "not run",
                  "popups_labelled": sum(1 for p in pops if p.get("kind") == "popup" and p.get("labels")),
                  "popups_unlabelled": sum(1 for p in pops if p.get("kind") == "popup" and not p.get("labels"))},
        "reflect": (rb or {}).get("reflect"),
        "readback_skipped": (rb or {}).get("skipped", []),
        "text": text_menus(s["core"], rb),
        "effects": dict(sorted(eff.items(), key=lambda kv: (-kv[1]["uses"], kv[0]))),
    }
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8", newline=chr(10))
    print("wrote", OUT.relative_to(HERE.parent).as_posix(), json.dumps(doc["stats"]))
    return doc


if __name__ == "__main__":
    build("--readback" in sys.argv)
