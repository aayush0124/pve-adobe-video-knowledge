"""PVE: split the harvested ground truth into agent-sized batch files -> pve/work/batches/<kind>_<n>.json

    python pve/prepare_batches.py

Every item carries exactly what an agent may rely on: names, match names, categories and (for effects) the property
list with types, ranges, defaults. Agents write cards for these and nothing else.
"""
import json, os
from pathlib import Path

HERE = Path(os.environ.get("PVE_HOME") or Path(__file__).resolve().parent)
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


def write(kind, items, size):
    k = 0
    for k, ch in enumerate(chunks(items, size), 1):
        (OUT / f"{kind}_{k:02d}.json").write_text(json.dumps({"kind": kind, "items": ch}, indent=1), encoding="utf-8")
    return k


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*.json"):
        f.unlink()
    counts = {}
    ae = json.loads((H / "ae_effects.json").read_text(encoding="utf-8"))
    items = [{"app": "AE", "name": e.get("name"), "match": e.get("match"), "category": e.get("category"),
              "error": e.get("error"), "props": flat_props(e.get("props"))} for e in ae]
    items.sort(key=lambda x: (x["category"] or "~", x["name"] or ""))  # batches stay within a category where possible
    counts["ae_effect"] = write("ae_effect", items, 12)

    pr = H / "pr"
    if (pr / "pr_video_effects.json").exists():
        v = json.loads((pr / "pr_video_effects.json").read_text(encoding="utf-8"))
        items = [{"app": "PR", "name": e["name"], "match": e.get("match"), "error": e.get("error"),
                  "props": [{k: p[k] for k in ("name", "type", "value", "min", "max") if k in p} for p in e.get("props", [])]}
                 for e in v]
        counts["pr_effect"] = write("pr_effect", items, 12)
        a = json.loads((pr / "pr_audio_effects.json").read_text(encoding="utf-8"))
        items = [{"app": "PR", "name": e["name"], "match": e.get("match"), "error": e.get("error"),
                  "props": [{k: p[k] for k in ("name", "type", "value", "min", "max") if k in p} for p in e.get("props", [])]}
                 for e in a]
        counts["pr_audio"] = write("pr_audio", items, 15)
        t = json.loads((pr / "pr_transitions.json").read_text(encoding="utf-8"))
        lists = json.loads((pr / "pr_lists.json").read_text(encoding="utf-8"))
        items = [{"app": "PR", "name": e["name"], "placed": e.get("placed"), "error": e.get("error")} for e in t]
        items += [{"app": "PR", "name": n, "audio": True} for n in lists.get("audio_transitions", [])]
        counts["pr_transition"] = write("pr_transition", items, 15)

    files = json.loads((H / "files.json").read_text(encoding="utf-8"))
    ae_names = {e.get("match"): e.get("name") for e in ae}
    pres = []
    for p in files["ae_presets"]:
        fx = sorted({ae_names[m] for m in p["match_names"] if m in ae_names})
        pres.append({"app": "AE", "category": p["category"], "name": p["name"], "effects_used": fx, "file": p["file"]})
    counts["ae_preset"] = write("ae_preset", pres, 40)
    counts["pr_preset"] = write("pr_preset", [dict(p, app="PR") for p in files["pr_presets"]], 50)
    counts["lut"] = write("lut", [dict(p, app="PR") for p in files["luts"]], 55)
    mg = [{"app": "PR", "origin": m["origin"], "folder": m["folder"], "name": m["name"], "file": m["file"],
           "params": [x["name"] for x in m.get("params", []) if x.get("name")][:40]} for m in files["mogrts"]]
    counts["mogrt"] = write("mogrt", mg, 30)
    print(counts)


if __name__ == "__main__":
    main()
