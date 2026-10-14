"""PVE: inventory the preset / LUT / template files AE and Premiere ship -> pve/harvest/files.json

    python pve/harvest_files.py

- AE animation presets (.ffx): category folder, name, and every match name the preset touches (RIFX 'tdmn' chunks),
  so a preset can be described by the effects it applies.
- Premiere effect presets (.prfpset XML): bins, presets, and the effects (match names) inside each.
- Lumetri LUTs (.cube) and looks (.look): kind (creative / technical / legacy).
- Motion graphics templates (.mogrt zips): editable parameters from definition.json, for Premiere's bundled templates,
  the Premiere Composer pack and the house templates.
"""
import json, os, re, struct, sys, zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adobe_bridge as ab  # noqa: E402  (install paths and their PVE_* env overrides live there)

HERE = Path(__file__).resolve().parent
AE = ab.AFTERFX.parents[1]
PR = ab.PREMIERE.parent
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
MOGRT_DIRS = {"premiere_bundled": PR / "Essential Graphics",
              "premiere_composer": LOCALAPPDATA / "MisterHorse" / "ProductManager" / "Products",
              "house": HERE.parent / "mgt" / "house"}


def portable(p):
    """The path as recorded in the harvest: repo files relative to the repo, per-user files under %LOCALAPPDATA%, so
    the committed harvest names no user folder. Install paths (Program Files) stay absolute."""
    p = Path(p)
    for root, label in ((HERE.parent, None), (LOCALAPPDATA, "%LOCALAPPDATA%")):
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        return str(rel) if label is None else str(Path(label) / rel)
    return str(p)


def ffx_matchnames(p):
    """'tdmn' chunks hold 40-byte null-padded match names (effects, groups, properties) in a RIFX tree."""
    b = p.read_bytes()
    out = []
    for m in re.finditer(rb"tdmn", b):
        i = m.end()
        if i + 4 > len(b):
            continue
        n = struct.unpack(">I", b[i:i + 4])[0]
        s = b[i + 4:i + 4 + min(n, 64)].split(b"\0")[0].decode("latin-1", "replace")
        if s and s not in out and not s.startswith("ADBE Group End"):
            out.append(s)
    return out


def ae_presets():
    root = AE / "Support Files" / "Presets"
    res = []
    for p in sorted(root.rglob("*.ffx")):
        rel = p.relative_to(root)
        res.append({"category": "/".join(rel.parts[:-1]), "name": p.stem, "file": str(p), "match_names": ffx_matchnames(p)})
    return res


def prfpset(path):
    """Bins and presets with the effects each preset applies."""
    tree = ET.parse(path)
    objs = {e.get("ObjectID"): e for e in tree.getroot() if e.get("ObjectID")}

    def name_of(e):
        n = e.find("./TreeItemBase/Name")
        if n is None:
            n = e.find(".//Name")
        return n.text if n is not None else ""

    def effects_under(e, seen):
        found = []
        for sub in e.iter():
            if sub.tag in ("MatchName", "FilterMatchName") and sub.text and sub.text not in found:
                found.append(sub.text)
            ref = sub.get("ObjectRef")
            if ref and ref not in seen and ref in objs:
                seen.add(ref)
                found += [x for x in effects_under(objs[ref], seen) if x not in found]
        return found

    out = []

    def walk(e, path):
        if e.tag == "BinTreeItem":
            nm = name_of(e)
            for it in e.findall("./Items/Item"):
                ch = objs.get(it.get("ObjectRef"))
                if ch is not None:
                    walk(ch, path + [nm] if nm not in ("Root",) else path)
        else:
            nm = name_of(e)
            if nm:
                out.append({"bin": "/".join(path), "name": nm, "effects": effects_under(e, {e.get("ObjectID")})})

    top = objs.get(tree.getroot().find("./Tree/RootBin").get("ObjectRef")) if tree.getroot().find("./Tree/RootBin") is not None else None
    if top is None:
        top = next(e for e in objs.values() if e.tag == "BinTreeItem")
    walk(top, [])
    return out


def luts():
    res = []
    for p in sorted((PR / "Lumetri").rglob("*")):
        if p.suffix.lower() in (".cube", ".look", ".lut"):
            res.append({"kind": p.parent.name, "group": p.parent.parent.name, "name": p.stem, "file": str(p)})
    return res


def mogrt_params(p):
    try:
        with zipfile.ZipFile(p) as z:
            d = json.loads(z.read("definition.json").decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}
    caps = d.get("capsuleName") or d.get("capsulename") or {}
    params = []
    for c in d.get("clientControls", []):
        ui = c.get("uiName", {})
        params.append({"name": ui.get("strDB", [{}])[0].get("str") if isinstance(ui, dict) else ui, "type": c.get("type")})
    src = d.get("sourceInfoLocalized", {})
    dur = d.get("duration") or d.get("durationInSeconds")
    return {"params": params, "app": d.get("apiVersion") and ("AE" if "aeApp" in json.dumps(d)[:5000] else "PR"),
            "duration": dur}


def mogrts():
    res = []
    for origin, root in MOGRT_DIRS.items():
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.mogrt")):
            if p.is_dir():
                continue
            r = {"origin": origin, "name": p.stem, "folder": p.parent.name, "file": portable(p)}
            r.update(mogrt_params(p))
            res.append(r)
    return res


def main():
    out = {"ae_presets": ae_presets(), "pr_presets": [], "luts": luts(), "mogrts": mogrts()}
    for f in ("Factory Presets.prfpset", "Lumetri Presets.prfpset"):
        try:
            for r in prfpset(PR / "LocalizedPresets" / "en_US" / "Effect Presets" / f):
                r["file"] = f
                out["pr_presets"].append(r)
        except Exception as e:  # noqa: BLE001
            out.setdefault("errors", []).append(f"{f}: {e}")
    (HERE / "harvest").mkdir(exist_ok=True)
    (HERE / "harvest" / "files.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print({k: len(v) for k, v in out.items()})


if __name__ == "__main__":
    main()
