"""PVE: split the harvested ground truth into agent-sized batch files -> pve/work/batches/<kind>_<n>.json

    python pve/prepare_batches.py            first cut (refuses, exit 1, when batch files already exist)
    python pve/prepare_batches.py --force    re-cut them (-h / --help: this text, nothing changed)

Every item carries exactly what an agent may rely on: names, match names, categories and (for effects) the property
list with types, ranges, defaults. Agents write cards for these and nothing else. The whole harvest is read and cut
before any old batch file is touched, so a missing or corrupt harvest file leaves the old batches in place. The
Premiere batches (FROZEN) were cut by an older version of this script and then patched by hand; while cards exist for
them, --force keeps them as they are.
"""
import json, os, sys
from pathlib import Path

HERE = Path(os.environ.get("PVE_HOME") or Path(__file__).resolve().parent).resolve()
H = HERE / "harvest"
OUT = HERE / "work" / "batches"


def flat_props(props, prefix=""):
    """AE property tree -> flat list of settable leaves (skips the built-in Compositing Options group)."""
    out = []
    for p in props or []:
        if p.get("match") == "ADBE Effect Built In Params":
            continue
        path = prefix + p["name"]
        if p["kind"] == "prop":
            if p.get("type") in ("none",):
                out.append({"name": path, "type": "header"})
                continue
            d = {k: p[k] for k in ("type", "min", "max", "default", "dropdown") if k in p}
            d["name"] = path
            out.append(d)
        else:
            out += flat_props(p.get("props"), path + "/")
    return out


def chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


FROZEN = {"pr_effect", "pr_audio", "pr_transition"}  # cut by an older version of this script, then hand-patched


def write(kind, items, size, out):
    """kind's chunks -> {file name: text} in out; returns the number of files."""
    k = 0
    for k, ch in enumerate(chunks(items, size), 1):
        out[f"{kind}_{k:02d}.json"] = json.dumps({"kind": kind, "items": ch}, indent=1)
    return k


def main(argv=()):
    if set(argv) & {"-h", "--help"}:
        print(__doc__)
        return 0
    old = sorted(OUT.glob("*.json"))
    if old and "--force" not in argv:  # the cards were written against these exact batches (chunking included)
        print(f"{len(old)} batch files already exist in {OUT}; kept (--force re-cuts them, after which every card file "
              "must be re-validated)", file=sys.stderr)
        return 1
    new, counts = {}, {}  # everything is read and cut before any old batch file is touched
    ae = json.loads((H / "ae_effects.json").read_text(encoding="utf-8"))
    items = [{"app": "AE", "name": e.get("name"), "match": e.get("match"), "category": e.get("category"),
              "error": e.get("error"), "props": flat_props(e.get("props"))} for e in ae]
    items.sort(key=lambda x: (x["category"] or "~", x["name"] or ""))  # batches stay within a category where possible
    counts["ae_effect"] = write("ae_effect", items, 12, new)

    pr = H / "pr"
    if (pr / "pr_video_effects.json").exists():
        v = json.loads((pr / "pr_video_effects.json").read_text(encoding="utf-8"))
        items = [{"app": "PR", "name": e["name"], "match": e.get("match"), "error": e.get("error"),
                  "props": [{k: p[k] for k in ("name", "type", "value", "min", "max") if k in p} for p in e.get("props", [])]}
                 for e in v]
        counts["pr_effect"] = write("pr_effect", items, 12, new)
        a = json.loads((pr / "pr_audio_effects.json").read_text(encoding="utf-8"))
        items = [{"app": "PR", "name": e["name"], "match": e.get("match"), "error": e.get("error"),
                  "props": [{k: p[k] for k in ("name", "type", "value", "min", "max") if k in p} for p in e.get("props", [])]}
                 for e in a]
        counts["pr_audio"] = write("pr_audio", items, 15, new)
        t = json.loads((pr / "pr_transitions.json").read_text(encoding="utf-8"))
        lists = json.loads((pr / "pr_lists.json").read_text(encoding="utf-8"))
        items = [{"app": "PR", "name": e["name"], "placed": e.get("placed"), "error": e.get("error")} for e in t]
        items += [{"app": "PR", "name": n, "audio": True} for n in lists.get("audio_transitions", [])]
        counts["pr_transition"] = write("pr_transition", items, 15, new)

    files = json.loads((H / "files.json").read_text(encoding="utf-8"))
    ae_names = {e.get("match"): e.get("name") for e in ae}
    pres = []
    for p in files["ae_presets"]:
        fx = sorted({ae_names[m] for m in p["match_names"] if m in ae_names})
        pres.append({"app": "AE", "category": p["category"], "name": p["name"], "effects_used": fx, "file": p["file"]})
    counts["ae_preset"] = write("ae_preset", pres, 40, new)
    counts["pr_preset"] = write("pr_preset", [dict(p, app="PR") for p in files["pr_presets"]], 50, new)
    counts["lut"] = write("lut", [dict(p, app="PR") for p in files["luts"]], 55, new)
    mg = [{"app": "PR", "origin": m["origin"], "folder": m["folder"], "name": m["name"], "file": m["file"],
           "params": [x["name"] for x in m.get("params", []) if x.get("name")][:40]} for m in files["mogrts"]]
    counts["mogrt"] = write("mogrt", mg, 30, new)

    # Frozen kinds this script can no longer reproduce (component key, placed_1s_centred, 16 transitions per file, the
    # stereo audio rows from pr_audio_params_w3.json): while cards exist for them their batch files stay as they are.
    # Kept kind by kind: one batch without its card file must not be deleted while the fresh cut of its kind is dropped.
    frozen = {f.name.rsplit("_", 1)[0] for f in old
              if f.name.rsplit("_", 1)[0] in FROZEN and (HERE / "cards" / f.name).exists()}
    keep = {f.name for f in old if f.name.rsplit("_", 1)[0] in frozen}
    for kind in sorted({k.rsplit("_", 1)[0] for k in keep}):
        new = {k: v for k, v in new.items() if k.rsplit("_", 1)[0] != kind}
        counts[kind] = f"kept {sum(1 for k in keep if k.rsplit('_', 1)[0] == kind)} (cards were written against them)"
    for f in old:
        if f.name not in keep:
            f.unlink()
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in new.items():
        (OUT / name).write_text(text, encoding="utf-8")
    print(counts)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError) as e:  # main() reads and cuts the whole harvest before it touches a batch file
        sys.exit(f"prepare_batches: {type(e).__name__}: {e} (no batch file is touched until the whole harvest has "
                 "been read)")
