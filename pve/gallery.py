"""PVE galleries: render every effect with its card's demo settings on the test footage, one frame per effect.

    python pve/gallery.py ae   -> pve/gallery/ae/<idx>.png   (After Effects, batches of comps + aerender)
    python pve/gallery.py pr   -> pve/gallery/pr/<idx>.png   (Premiere, one clip per effect, one render)
    python pve/gallery.py tr   -> pve/gallery/tr/<idx>{a,b,c}.png (every Premiere transition on a cut)
    python pve/gallery.py presets -> pve/gallery/presets/<idx>{a,b}.png (every AE animation preset)
    python pve/gallery.py recipes -> pve/gallery/recipes/<idx>{a,b}.png (Premiere side of every recipe)
    python pve/gallery.py recipes_ae -> pve/gallery/recipes_ae/<idx>{a,b}.png (After Effects side of every recipe, same
                                     clip and frame times; second_instance.json = the repeated-effect builder check)
    python pve/gallery.py specs   -> pve/gallery/recipe_specs/ (every recipe's own bridge spec / VFX scenes, built and
                                     rendered in both apps; index.json carries the measurements)
    python pve/gallery.py text    -> pve/gallery/text_motion/, pve/gallery/mogrts/ (text recipes and a template sample
                                     in Latin and Devanagari) + work/pve_live/wave2/text_timing.json (in / out timing)
    python pve/gallery.py strobe  -> work/pve_live/strobe.json (Strobe Light timing in both apps, luma-scanned)
    python pve/gallery.py flashes -> work/pve_live/flash_rates.json (flashes per second of every "check by eye" card)

    ... ae|presets|recipes|pr --only keys.json   re-render just these (a JSON list of match names / preset files /
                                                 names, any status): their old frames and renders move to
                                                 pve/work/gallery_pre_fix/<gallery>/ first
    ... ae|presets --gpu                          the gallery project renders with GPU acceleration

Reads demo settings from pve/cards/*.json (cards written by the PVE agents). An AE batch that kills AE is split in
half until the culprit is isolated and recorded in pve/gallery/ae/failed.json.
"""
import copy, json, math, os, shutil, subprocess, sys, time
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adobe_bridge as ab  # noqa: E402
from vfx import HERE as AB, render_scenes  # noqa: E402,F401

HERE = Path(__file__).resolve().parent
MEDIA = HERE / "media"
EXAMPLE = HERE.parent / "example"  # what recipe specs' relative paths mean (make_test_media.py fills it)
LIVE = HERE.parent / "work" / "pve_live"  # small JSON reports of live measurements, for the card writers
PRE_FIX = HERE / "work" / "gallery_pre_fix"
FPS = 25


def cards(kind):
    out = []
    for f in sorted((HERE / "cards").glob(f"{kind}_*.json")):
        out += json.loads(f.read_text(encoding="utf-8")).get("cards", [])
    return out


def stable(out, todo, field):
    """[(k, card)] where each card keeps the k it had in out/index.json. Numbering by position shifted every later
    frame onto the wrong effect as soon as a verifier moved one card out of the todo list (status -> needs_input)."""
    p = out / "index.json"
    old = {}  # Premiere lists some effects twice (Noise, Transform): repeats match up in order
    for x in json.loads(p.read_text(encoding="utf-8")) if p.exists() else []:
        old.setdefault(x[field], []).append(x["k"])
    nxt = max((k for ks in old.values() for k in ks), default=-1) + 1
    res = []
    for c in todo:
        ks = old.get(c[field])
        if ks:
            res.append((ks.pop(0), c))
        else:
            res.append((nxt, c))
            nxt += 1
    return res


def write_index(out, rows):
    """index.json = rows + every older row whose k they do not use: a card that left the todo list (a verifier set
    needs_input) keeps its k -> name row, so its frame stays traceable and stable() never hands its k out again."""
    p = out / "index.json"
    ks = {r["k"] for r in rows}
    old = [r for r in json.loads(p.read_text(encoding="utf-8")) if r["k"] not in ks] if p.exists() else []
    p.write_text(json.dumps(sorted(old + rows, key=lambda r: r["k"]), indent=1), encoding="utf-8")


def retire(out, ks, suffixes):
    """Move the frames <k><suffix>.png and the render G<k>.mp4 of each k to PRE_FIX/<gallery>/ (never overwriting an
    older retired copy), so a re-render starts clean and the frames the old verdicts were judged on survive."""
    dest = PRE_FIX / out.name
    for k in ks:
        for f in [out / f"{k:03d}{s}.png" for s in suffixes] + [out / "renders" / f"G{k:03d}.mp4"]:
            if f.exists():
                to, n = dest / f.relative_to(out), 1
                while to.exists():
                    n += 1
                    to = to.with_name(f"{f.stem}_v{n}{f.suffix}")
                to.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(to))


def picked(todo, key, only, out, suffixes, failed):
    """The (k, item) pairs to render: all of todo, or with `only` just those whose key is listed, retired first and
    taken off the failed list so they are tried again."""
    if only is None:
        return todo
    todo = [(k, c) for k, c in todo if key(c) in only]
    retire(out, [k for k, _ in todo], suffixes)
    for _, c in todo:
        failed.pop(key(c), None)
    if (out / "failed.json").exists():
        (out / "failed.json").write_text(json.dumps(failed, indent=1), encoding="utf-8")
    return todo


def frame(video, t, png):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                    "-vf", "scale=640:-1", str(png)], check=False)


def ae_clean():
    """After aerender the open gallery .aep shows "*" and AE goes deaf to -r; render_scenes would then refuse the next
    batch and the halving would blame its items. Restart AE when the dirty project is one of ours."""
    if any(t.rstrip().endswith("*") and "gal_" in t for t in ab.ae_titles()):
        ab.restart_ae()


def ae_batches(todo, scene, out, key, frames, batch=30, w=1920, h=1080, only=None, gpu=False):
    """Build comps for todo[(k, item)] in After Effects batches, aerender them, grab frames. A batch that fails is
    halved until the culprit is isolated; culprits go to out/failed.json and are skipped next time."""
    (out / "renders").mkdir(parents=True, exist_ok=True)
    failed = json.loads((out / "failed.json").read_text()) if (out / "failed.json").exists() else {}
    todo = picked(todo, key, only, out, [s for s, _ in frames], failed)

    def run(items):
        try:
            ae_clean()
            render_scenes([scene(c, k) for k, c in items], out / f"gal_{items[0][0]:03d}.aep", out / "renders", render=True,
                          w=w, h=h, timeout=60 * len(items) + 300, gpu=gpu)
            return True
        except SystemExit as e:
            print("batch failed:", str(e)[:200], flush=True)
            ab.restart_ae()
            return False

    def go(items):
        if not items:
            return
        if run(items):
            for k, c in items:
                for suffix, t in frames:
                    frame(out / "renders" / f"G{k:03d}.mp4", t, out / f"{k:03d}{suffix}.png")
            return
        if len(items) == 1:
            failed[key(items[0][1])] = items[0][1]["name"]
            (out / "failed.json").write_text(json.dumps(failed, indent=1))
            return
        go(items[:len(items) // 2])
        go(items[len(items) // 2:])

    items = [(k, c) for k, c in todo if key(c) not in failed and not (out / f"{k:03d}{frames[0][0]}.png").exists()]
    for i in range(0, len(items), batch):
        go(items[i:i + batch])
        print(f"{out.name} gallery {min(i + batch, len(items))}/{len(items)}", flush=True)
    if gpu and items:
        # A project's renderer also becomes AE's default for every later new project. The galleries are rendered on
        # the software renderer (that is where the VR effects show their GPU slate), so put it back.
        ae_clean()
        render_scenes([{"name": "CPU", "dur": 0.04, "layers": [{"type": "solid", "name": "S", "color": "#000000"}]}],
                      out / "gal_cpu_default.aep", out / "renders", render=False, gpu="cpu")


def ae_gallery(only=None, gpu=False):
    out = HERE / "gallery" / "ae"
    todo = [c for c in cards("ae_effect") if c.get("match") and (
        c.get("status") not in ("hidden", "needs_input", "unusable") or (only is not None and c["match"] in only))]

    def scene(c, k):
        return {"name": f"G{k:03d}", "dur": 0.4, "layers": [
            {"type": "footage", "name": "F", "src": (MEDIA / "test_a.mp4").as_posix(), "in": 1.5, "t0": 0, "t1": 0.4,
             "effects": [{"name": c["match"], "props": c.get("demo_settings") or {}}]}]}

    todo = stable(out, todo, "match")
    ae_batches(todo, scene, out, lambda c: c["match"], [("", 0.2)], only=only, gpu=gpu)
    write_index(out, [{"k": k, "name": c["name"], "match": c["match"]} for k, c in todo])


def preset_layer(p, t1=1.6, src_in=1.0):
    """The layer an AE preset is tested on, with the preset applied at 0 s. Route by what the .ffx contains, then by
    folder: "Legacy" holds text animators and shape presets too."""
    cat, pre, mn = p["category"], [{"file": Path(p["file"]).as_posix(), "at": 0}], " ".join(p.get("match_names") or [])
    if "ADBE Root Vectors Group" in mn and "ADBE Text" not in mn:
        return {"type": "shape", "name": "S", "origin": [480, 270], "fill": "#FF9933", "stroke": "#FFFFFF", "width": 6,
                "paths": [{"closed": True, "pts": [[-50, -50], [50, -50], [50, 50], [-50, 50]]}], "presets": pre}
    if "ADBE Text" in mn or cat.startswith(("Text", "Adobe Express", "Behaviors")):
        return {"type": "text", "name": "T", "text": "ARCHIVE 1947", "pos": [480, 280], "size": 75, "color": "#FFFFFF",
                "font": "BebasNeueBold", "presets": pre}
    if cat.startswith("Shapes"):
        return {"type": "shape", "name": "S", "paths": [{"closed": True, "pts": [[430, 220], [530, 220], [530, 320], [430, 320]]}],
                "fill": "#FF9933", "presets": pre}
    if cat.startswith(("Backgrounds", "Synthetics")):
        return {"type": "solid", "name": "BG", "color": "#3A3F47", "presets": pre}
    return {"type": "footage", "name": "F", "src": (MEDIA / "test_a.mp4").as_posix(), "in": src_in, "t0": 0, "t1": t1,
            "presets": pre}


def preset_scene(name, L, dur):
    """Text and shape hosts get the gallery's dark background under them."""
    return {"name": name, "dur": dur, "layers": ([{"type": "solid", "name": "BG", "color": "#20242B"}]
                                                 if L["type"] in ("text", "shape") else []) + [L]}


def preset_files():
    files = json.loads((HERE / "harvest" / "files.json").read_text(encoding="utf-8"))["ae_presets"]
    return [p for p in files if not p["category"].startswith("Sound Effects")]


def preset_gallery(only=None, gpu=False):
    """Every After Effects animation preset applied at 0 s on the layer type it is made for; frames at 0.5 s and 1.5 s."""
    out = HERE / "gallery" / "presets"
    todo = preset_files()
    ae_batches(list(enumerate(todo)), lambda p, k: preset_scene(f"G{k:03d}", preset_layer(p), 1.6), out,
               lambda p: p["file"], [("a", 0.5), ("b", 1.4)], batch=40, w=960, h=540, only=only, gpu=gpu)
    (out / "index.json").write_text(json.dumps([{"k": k, "name": p["name"], "category": p["category"]}
                                                for k, p in enumerate(todo)], indent=1), encoding="utf-8")


def pr_gallery(batch=15, only=None):
    out = HERE / "gallery" / "pr"
    out.mkdir(parents=True, exist_ok=True)
    todo = stable(out, [c for c in cards("pr_effect") if c.get("status") not in ("needs_input", "unusable")
                        or (only is not None and c["name"] in only)], "name")
    pr_batches(todo, out, lambda c: [{"name": c["name"], "pr_props": dict(c.get("demo_settings") or {})}], [("", 1.0)],
               batch, only)
    write_index(out, [{"k": k, "name": c["name"]} for k, c in todo])


def recipe_gallery(batch=12, only=None):
    """The Premiere side of every recipe (its whole effect stack, keyframes included) on the test footage; frames 0.2 s
    and 1.0 s into each 2 s clip. Text recipes are skipped: their look lives on the template text, not on footage."""
    out = HERE / "gallery" / "recipes"
    out.mkdir(parents=True, exist_ok=True)
    todo = stable(out, [c for c in cards("recipe") if c.get("premiere") and c.get("family") != "text_motion"], "name")
    pr_batches(todo, out, lambda c: c["premiere"], [("a", 0.2), ("b", 1.0)], batch, only)
    write_index(out, [{"k": k, "name": c["name"], "family": c["family"]} for k, c in todo])


def ram_guard(gb=8, wait=600):
    """Long renders want 8 GB free (README). After Effects keeps about 6 GB after a few builds and renders, so the first
    move is a restart of AE (as after aerender); then wait up to `wait` s, then stop rather than start a render that swaps."""
    sys.path.insert(0, str(HERE.parent))
    from pipeline import free_ram_gb
    t0, restarted = time.time(), False
    while (free_ram_gb() or gb) < gb:
        if not restarted:
            print(f"free RAM {free_ram_gb():.1f} GB: restarting After Effects to release its memory", flush=True)
            ab.restart_ae()
            restarted = True
            continue
        if time.time() - t0 > wait:
            sys.exit(f"free RAM stayed under {gb} GB for {wait} s: free some (restart Premiere) and run again")
        print(f"free RAM {free_ram_gb():.1f} GB, waiting for {gb} GB", flush=True)
        time.sleep(30)


def mean_rgb(video, t):
    """Mean [r, g, b] (0-255) of the frame at t."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-vf",
                          "scale=16:9:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True,
                         check=True).stdout
    return [sum(raw[c::3]) / (len(raw) // 3) for c in range(3)] if raw else None


# Builder check (critic next step): stacks holding one effect several times. FILL2: After Effects' Fill replaces the
# layer with its colour at its opacity (measured 2026-10-13: green 100% then blue 50% on grey gave blue over the comp's
# black, no green, no grey), so only the last instance shows; one left at its defaults, or whose values went to an
# earlier instance (the old Premiere bug), shows Fill's default red at 100%. XFORM3: three Transform effects each shift
# a red square; every subset of the three shifts sums differently, so the square lands on the full sum only if each
# instance kept its own Position.
FILLS = [("#00FF00", 1), ("#0000FF", 0.5)]
SHIFTS = [(120, 0), (0, 90), (-40, 0)]


def second_instance_scenes():
    return [{"name": "FILL2", "dur": 1.0, "layers": [{"type": "solid", "name": "G", "color": "#808080", "effects": [
                {"name": "ADBE Fill", "props": {"Color": c, "Opacity": o}} for c, o in FILLS]}]},
            {"name": "XFORM3", "dur": 1.0, "layers": [{"type": "shape", "name": "SQ", "fill": "#FF0000", "paths": [
                {"closed": True, "pts": [[940, 520], [980, 520], [980, 560], [940, 560]]}], "effects": [
                {"name": "ADBE Geometry2", "props": {"Position": [960 + dx, 540 + dy]}} for dx, dy in SHIFTS]}]}]


def second_instance_check(fill_rgb, square_xy, tol=16, px=3):
    """{scene: stack, expected, measured, ok}: FILL2 from the frame's mean colour, XFORM3 from the red square's centre."""
    c, o = FILLS[-1]
    rgb = [round(int(c[i:i + 2], 16) * o) for i in (1, 3, 5)]
    xy = [960 + sum(d for d, _ in SHIFTS), 540 + sum(d for _, d in SHIFTS)]
    return {"FILL2": {"stack": [f"Fill {c} at {o:g}" for c, o in FILLS], "expected_rgb": rgb,
                      "measured_rgb": [round(v) for v in fill_rgb] if fill_rgb else None,
                      "ok": bool(fill_rgb) and all(abs(a - b) <= tol for a, b in zip(fill_rgb, rgb))},
            "XFORM3": {"stack": [f"Transform Position +{dx},+{dy}" for dx, dy in SHIFTS], "expected_square_centre": xy,
                       "measured_square_centre": square_xy,
                       "ok": bool(square_xy) and all(abs(a - b) <= px for a, b in zip(square_xy, xy))}}


def recipe_scene(c, k):
    """The After Effects half of recipe c on the recipe gallery's clip (2 s of test_a.mp4 from 1.0 s), keyframe times
    from the clip start (negative = from its end) as on the Premiere side."""
    return {"name": f"G{k:03d}", "dur": 2.0, "layers": [
        {"type": "footage", "name": "F", "src": (MEDIA / "test_a.mp4").as_posix(), "in": 1.0, "t0": 0, "t1": 2.0,
         "effects": copy.deepcopy(c.get("after_effects") or [])}]}


def recipe_ae_gallery(batch=30, only=None):
    """The After Effects side of every recipe ('after_effects' stack, keyframes included; text_motion excluded, see
    text) on the Premiere gallery's clip with its frame times (0.2 s, 1.0 s). Own k numbering; index rows carry the
    Premiere gallery's k as pr_k. Made once: ref.png (the clip untouched) and second_instance.json (FILLS, SHIFTS)."""
    out = HERE / "gallery" / "recipes_ae"
    out.mkdir(parents=True, exist_ok=True)
    if not (out / "second_instance.json").exists():
        ram_guard()
        ae_clean()
        files = render_scenes(second_instance_scenes() + [dict(recipe_scene({}, 0), name="REF")], out / "gal_controls.aep",
                              out / f"renders_controls_{int(time.time())}")
        frame(files["REF"], 1.0, out / "ref.png")
        (out / "second_instance.json").write_text(json.dumps({
            "method": "After Effects (vfx_build.jsx -> ae_lib.jsx dress), aerender 1920x1080 25 fps, frame at 0.5 s: "
                      "FILL2 mean RGB (tolerance 16 per channel, H.264 4:2:0), XFORM3 centroid of the red square (3 px)",
            "results": second_instance_check(mean_rgb(files["FILL2"], 0.5), dots(rgb_at(files["XFORM3"], 0.5), 1920)["red"]),
            "renders": {n: rel(files[n]) for n in ("FILL2", "XFORM3")}}, indent=1), encoding="utf-8")
    todo = stable(out, [c for c in cards("recipe") if c.get("after_effects") and c.get("family") != "text_motion"], "name")
    ram_guard()
    ae_batches(todo, recipe_scene, out, lambda c: c["name"], [("a", 0.2), ("b", 1.0)], batch=batch, only=only)
    p = HERE / "gallery" / "recipes" / "index.json"
    pr = {r["name"]: r["k"] for r in json.loads(p.read_text(encoding="utf-8"))} if p.exists() else {}
    write_index(out, [{"k": k, "name": c["name"], "family": c["family"], "pr_k": pr.get(c["name"])} for k, c in todo])


# ---- recipes that carry a whole bridge spec or VFX scenes (the 2026-10-13 gap recipes) ----
# the gap agent's priority; every other spec / scenes recipe follows in card order
GAP_ORDER = ["Ken Burns pan and zoom on a still", "Blur fill: 16:9 into 9:16",
             "Blur fill: portrait or 4:3 into 16:9 (pillarbox fill)", "Music ducks under VO (scripted, from VO ranges)",
             "Two-way (double box) split", "Three-up row", "Presenter plus screenshot picture-in-picture",
             "Bar chart grow (shape layers)", "Donut chart segments", "Pie chart slices pop",
             "Percentage ring with counter", "News ticker crawl (looping)", "Screenshot zoom with highlighter",
             "Rolling end credits (AE scroll)"]
FRACS = (("a", 0.1), ("b", 0.4), ("c", 0.7), ("d", 0.97))  # frames at these fractions of each render
# 30 px squares on a black 1080x1350 still: red at the centre, green 100 px above it, blue 100 px left of it (all
# three stay in frame through a 15% push and a +-75 px Transform pan on a cover-fitted 16:9 frame)
PROBE_DOTS = {"red": (540, 675), "green": (540, 575), "blue": (440, 675)}


def spec_recipes():
    return sorted((c for c in cards("recipe") if c.get("spec") or c.get("scenes")),
                  key=lambda c: GAP_ORDER.index(c["name"]) if c["name"] in GAP_ORDER else len(GAP_ORDER))


def probe_still(out):
    """The Ken Burns probe (PROBE_DOTS), made once: Premiere keeps the media of its open projects locked."""
    p = out / "probe2_1080x1350.png"  # a new name for a new layout (the first, probe_1080x1350.png, put dots off-frame)
    if not p.exists():
        box = ",".join(f"drawbox=x={x - 15}:y={y - 15}:w=30:h=30:color={'lime' if c == 'green' else c}:t=fill"
                       for c, (x, y) in PROBE_DOTS.items())
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s=1080x1350,{box}",
                        "-frames:v", "1", str(p)], check=True)
    return p


def spec_builds(c, probe):
    """[(tag, spec, ae)] to build for a spec recipe: its own spec, plus measurement variants: Ken Burns on the dot probe
    (where each app puts a Transform Position on an off-aspect still) and the ducking bed without the VO (the music
    alone, so its level can be read). The ducking recipe is Premiere only (ae_build ignores audio fades)."""
    s, ae = copy.deepcopy(c["spec"]), c.get("family") != "audio_mix"
    out = [("main", s)]
    if c["name"].startswith("Ken Burns"):
        p = copy.deepcopy(s)
        for x in p["clips"]:
            x["src"] = probe.as_posix()
        out.append(("probe", p))
    if c.get("family") == "audio_mix":
        m = copy.deepcopy(s)
        m["audio"] = [a for a in m.get("audio", []) if "test_vo" not in a["src"]]
        out.append(("music_only", m))
    return [(t, x, ae) for t, x in out]


def spec_scenes(c, k):
    """A recipe's VFX scenes, named G<k>_<i>, footage paths resolved against example/; for the green-screen recipe also
    its whole 'after_effects' stack on the green plate over clip_b (its spec pairs only two of the four effects)."""
    sc = copy.deepcopy(c.get("scenes") or [])
    for i, s in enumerate(sc):
        s["name"] = f"G{k:03d}_{i}"
        for L in s["layers"]:
            if L.get("src") and not Path(L["src"]).is_absolute():
                L["src"] = (EXAMPLE / L["src"]).as_posix()
    if c.get("family") == "compositing" and c.get("after_effects"):
        sc.append({"name": f"G{k:03d}_aefull", "dur": 6, "layers": [
            {"type": "footage", "name": "BG", "src": (EXAMPLE / "clip_b.mp4").as_posix()},
            {"type": "footage", "name": "FG", "src": (EXAMPLE / "greenscreen" / "presenter.mp4").as_posix(),
             "effects": copy.deepcopy(c["after_effects"])}]})
    return sc


def render_halving(scenes, aep, renders, failed):
    """render_scenes; a failing set is halved until the culprit is isolated (scene name -> failed)."""
    if not scenes:
        return {}
    try:
        ram_guard()
        ae_clean()
        return render_scenes(scenes, aep, renders, timeout=60 * len(scenes) + 600)
    except SystemExit as e:
        print("render failed:", str(e)[:200], flush=True)
        ab.restart_ae()
        if len(scenes) == 1:
            failed[scenes[0]["name"]] = str(e)[:300]
            return {}
        h = len(scenes) // 2
        return {**render_halving(scenes[:h], aep, renders, failed), **render_halving(scenes[h:], aep, renders, failed)}


def rgb_at(video, t, w=1920, h=1080):
    return subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v", "1", "-vf",
                           f"scale={w}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True,
                          check=True).stdout


def dots(raw, w):
    """Centroids {colour: [x, y]} of the probe squares in an rgb24 frame (None where a colour is absent)."""
    acc = {k: [0, 0, 0] for k in PROBE_DOTS}
    for i in range(0, len(raw) - 2, 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        k = ("red" if r >= 128 and g < 96 and b < 96 else "green" if g >= 128 and r < 96 and b < 96
             else "blue" if b >= 128 and r < 96 and g < 96 else None)
        if k:
            a = acc[k]
            a[0] += (i // 3) % w
            a[1] += (i // 3) // w
            a[2] += 1
    return {k: [round(a[0] / a[2], 1), round(a[1] / a[2], 1)] if a[2] else None for k, a in acc.items()}


def transform_reading(d, src=(1080, 1350), frame=(1920, 1080)):
    """From the probe dots in one frame: the layer's on-screen scale per axis (from the known dot spacing) and the
    Transform Position the app applied, in source pixels: the effect's anchor is the source centre, so the centre
    (red) dot lands on Position, and the layer's own anchor (the source centre) sits at the frame centre."""
    if None in d.values():
        return {"dots_frame_px": d, "error": "a probe square is not in the frame"}
    (rx, ry), (gx, gy), (bx, by) = (PROBE_DOTS[k] for k in ("red", "green", "blue"))
    r, g, b = d["red"], d["green"], d["blue"]
    sx, sy = (r[0] - b[0]) / (rx - bx), (r[1] - g[1]) / (ry - gy)
    return {"dots_frame_px": d, "scale_pct": [round(100 * sx, 2), round(100 * sy, 2)],
            "position_src_px": [round(src[0] / 2 + (r[0] - frame[0] / 2) / sx, 1),
                                round(src[1] / 2 + (r[1] - frame[1] / 2) / sy, 1)]}


def fraction_readings(fr, src=(1080, 1350), seq=(1920, 1080)):
    """Source-pixel Positions a Premiere fraction [fx, fy] would mean, by the two candidate readings."""
    return {"of_clip_frame": [round(fr[0] * src[0], 1), round(fr[1] * src[1], 1)],
            "of_sequence_frame": [round(fr[0] * seq[0], 1), round(fr[1] * seq[1], 1)]}


def levels(media, win=0.1, rate=8000):
    """Mono RMS level (dBFS) of each `win` s window of a file's audio."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le",
                          "-"], capture_output=True, check=True).stdout
    a, n = array("h"), int(win * rate)
    a.frombytes(raw[:len(raw) // 2 * 2])
    return [round(20 * math.log10(max(1e-9, math.sqrt(sum(x * x for x in a[i:i + n]) / n) / 32768)), 2)
            for i in range(0, len(a) - n + 1, n)]


def duck_regions(lv, vo, win=0.1, margin=0.7, edge=0.1):
    """Mean / min / max of a level curve under the VO ranges (edge s inside them) and in the clear (margin s away from
    any VO), plus each window's level for the ramps."""
    t = [(i + 0.5) * win for i in range(len(lv))]
    under = [v for x, v in zip(t, lv) if any(a + edge <= x <= b - edge for a, b in vo)]
    clear = [v for x, v in zip(t, lv) if all(x < a - margin or x > b + margin for a, b in vo)]
    st = lambda xs: {"mean_db": round(sum(xs) / len(xs), 2), "min_db": min(xs), "max_db": max(xs), "windows": len(xs)} if xs else None  # noqa: E731
    return {"under_vo": st(under), "clear": st(clear),
            "duck_db": round(st(clear)["mean_db"] - st(under)["mean_db"], 2) if under and clear else None}


def loudness(media):
    """Integrated loudness (LUFS) by ffmpeg ebur128."""
    import re
    err = subprocess.run(["ffmpeg", "-nostats", "-i", str(media), "-vn", "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    return float(m[-1]) if m else None


def profiles(video, crop, axis):
    """Per-frame mean luma along one axis of a crop (w, h, x, y): axis "y" = one value per row, "x" per column."""
    w, h, x, y = crop
    sc = f"scale=1:{h}" if axis == "y" else f"scale={w}:1"
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"crop={w}:{h}:{x}:{y},{sc}:flags=area,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    n = h if axis == "y" else w
    return [raw[i:i + n] for i in range(0, len(raw) - n + 1, n)]


def best_shift(a, b, lo, hi):
    """(d, error): the d in lo..hi for which b[i] ~ a[i + d] best (mean |difference| over the overlap)."""
    best = None
    for d in range(lo, hi + 1):
        idx = range(max(0, -d), min(len(b), len(a) - d))
        if len(idx) < len(b) // 2:
            continue
        e = sum(abs(b[i] - a[i + d]) for i in idx) / len(idx)
        if best is None or e < best[1]:
            best = (d, round(e, 3))
    return best


def motion_speed(prof, lo, hi, least=2.0):
    """Frame-to-frame shift (px / frame) of a scrolling profile, over the pairs that carry content (spread > least)."""
    sh = [(i, *best_shift(a, b, lo, hi)) for i, (a, b) in enumerate(zip(prof, prof[1:]))
          if max(a) - min(a) > least and max(b) - min(b) > least]
    if not sh:
        return None
    ds = sorted(d for _, d, _ in sh)
    med = ds[len(ds) // 2]
    return {"pairs": len(sh), "median_px_per_frame": med, "min": ds[0], "max": ds[-1],
            "off_median_pairs": [[i, d, e] for i, d, e in sh if d != med][:20],
            "worst_match_pair": list(max(sh, key=lambda x: x[2])), "pair_fields": "[frame, shift px, mean |diff|]"}


def luma_diff(a, b, times):
    """Mean |luma difference| (0-255, 192x108) between two renders at each time: how far the two apps disagree."""
    import flash_scan as fs
    res = []
    for t in times:
        fa, fb = fs.frames(a, t, t + 0.04), fs.frames(b, t, t + 0.04)
        res.append(round(sum(abs(x - y) for x, y in zip(fa[0], fb[0])) / len(fa[0]), 2) if fa and fb else None)
    return res


def spec_gallery(only=None):
    """Every recipe that carries a bridge spec or VFX scenes, built from them and rendered: specs in both apps (one
    bridge build each, base example/), scenes with aerender, the gap agent's order first. Frames at FRACS of each
    render; index.json rows hold the renders, frames, warnings, failures and measurements (Ken Burns probe readings,
    ducking levels, AE vs Premiere difference, credit / ticker speeds)."""
    out = HERE / "gallery" / "recipe_specs"
    out.mkdir(parents=True, exist_ok=True)
    probe = probe_still(out)
    todo = stable(out, spec_recipes(), "name")
    if only is not None:
        todo = [(k, c) for k, c in todo if c["name"] in only]
    p = out / "index.json"
    rows = {r["k"]: r for r in json.loads(p.read_text(encoding="utf-8"))} if p.exists() else {}

    def shots(video, k, tag, app, dur):
        got = []
        for s, f in FRACS:
            frame(video, f * dur, out / f"{k:03d}_{tag}_{app}_{s}.png")
            got.append(rel(out / f"{k:03d}_{tag}_{app}_{s}.png"))
        return got

    def done(k, tags):  # resumable: a full run skips what an earlier run already rendered without failures
        r = rows.get(k) or {}
        return only is None and not r.get("failed") and all(any(f.startswith(t + "_") for f in r.get("frames", {}))
                                                             for t in tags)
    for k, c in todo:
        if c.get("spec") is None or done(k, [t for t, _, _ in spec_builds(c, probe)]):
            continue
        row = rows.setdefault(k, {"k": k, "name": c["name"], "family": c["family"]})
        row.update(builds={}, frames={}, failed={}, measured={})
        for tag, spec, ae in spec_builds(c, probe):
            ram_guard()
            ae_clean()
            try:
                res = ab.build(dict(spec, name=f"gal_{k:03d}_{tag}", out_dir=str(out / "builds")), EXAMPLE, render=True,
                               premiere=True, ae=ae)
            except SystemExit as e:
                row["failed"][tag] = str(e)[:300]
                ab.restart_ae()
                continue
            b = row["builds"][tag] = {"warnings": res["warnings"], **{x: rel(res[x]) for x in ("ae_mp4", "pr_mp4") if x in res}}
            dur = ab.probe(res["pr_mp4"])[2]
            for app in ("ae", "pr"):
                if f"{app}_mp4" in res:
                    row["frames"][f"{tag}_{app}"] = shots(res[f"{app}_mp4"], k, tag, app, dur)
            if "ae_mp4" in res:
                b["ae_vs_pr_mean_abs_luma"] = dict(zip([s for s, _ in FRACS], luma_diff(res["ae_mp4"], res["pr_mp4"],
                                                                                         [f * dur for _, f in FRACS])))
            if tag == "probe":
                fps, d0 = spec.get("fps", 30), spec["clips"][0]
                last = d0["dur"] - 1.5 / fps  # inside the last frame (a seek to its exact start can return nothing)
                pos = (d0["effects"][0].get("pr_props") or {}).get("Position")
                row["measured"]["transform_position"] = {
                    "frames_s": [0, round(last, 3)],
                    **{app: [transform_reading(dots(rgb_at(res[f"{app}_mp4"], t), 1920), ab.probe(str(probe))[:2])
                             for t in (0, last)] for app in ("ae", "pr") if f"{app}_mp4" in res},
                    "spec_after_effects_px": (d0["effects"][0].get("props") or {}).get("Position"),
                    "spec_premiere_fractions": pos,
                    "premiere_fraction_readings": [fraction_readings(v) for _, v in pos] if pos else None}
            if tag == "music_only":
                vo = [(a["start"], a["start"] + a["dur"]) for a in c["spec"]["audio"] if "test_vo" in a["src"]]
                ref = levels(EXAMPLE / "music.mp3")
                ref_db = sorted(ref)[len(ref) // 2]
                lv = [round(v - ref_db, 2) for v in levels(res["pr_mp4"])]
                row["measured"]["ducking"] = {"relative_to": "example/music.mp3 at 0 dB (median 0.1 s RMS)",
                                              "vo_ranges_s": vo, **duck_regions(lv, vo), "curve_db_per_0.1s": lv}
            if tag == "main" and c.get("family") == "audio_mix":
                row["measured"]["mix_integrated_lufs"] = loudness(res["pr_mp4"])
        rows[k] = row
        p.write_text(json.dumps([rows[x] for x in sorted(rows)], indent=1, ensure_ascii=False), encoding="utf-8")
    stag = lambda s: "scene_" + s["name"].split("_", 1)[1]  # noqa: E731  G007_0 -> scene_0
    todo = [(k, c) for k, c in todo if spec_scenes(c, k) and not done(k, [stag(s) for s in spec_scenes(c, k)])]
    failed = {}
    files = render_halving([s for k, c in todo for s in spec_scenes(c, k)], out / "gal_specs.aep",
                           out / f"renders_{int(time.time())}", failed)
    for k, c in todo:
        sc = spec_scenes(c, k)
        row = rows.setdefault(k, {"k": k, "name": c["name"], "family": c["family"]})
        if c.get("spec") is None:
            row.update(builds={}, frames={}, failed={}, measured={})
        for s in sc:
            n, tag = s["name"], stag(s)
            if n in failed:
                row["failed"][tag] = failed[n]
                continue
            row["builds"][tag] = {"ae_render": rel(files[n])}
            row["frames"][f"{tag}_ae"] = shots(files[n], k, tag, "ae", s["dur"])
            if c["family"] == "credits" and "ticker" in c["name"].lower():
                prof = profiles(files[n], (1680, 80, 240, 960), "x")
                row["measured"]["crawl"] = {"band_crop": "x 240-1920, y 960-1040", "expect": "content moves left",
                                            **(motion_speed(prof, 0, 16) or {})}
            elif c["family"] == "credits":
                prof = profiles(files[n], (1400, 1080, 260, 0), "y")
                row["measured"]["scroll"] = {"crop": "x 260-1660", "expect": "content moves up",
                                             **(motion_speed(prof, 0, 12) or {})}
        rows[k] = row
    p.write_text(json.dumps([rows[x] for x in sorted(rows)], indent=1, ensure_ascii=False), encoding="utf-8")
    return rows


# ---- text recipes and templates, Latin and Devanagari ----
COPY = {"lat": ("ARCHIVE 1947", "SECOND LINE"), "dev": ("\u0905\u092d\u093f\u0932\u0947\u0916 \u0967\u096f\u096a\u096d",
                                                     "\u0926\u0942\u0938\u0930\u0940 \u092a\u0902\u0915\u094d\u0924\u093f")}
DEVA_FONT = "NirmalaUI-Bold"
HOST = ("(2) Text Presets", "Fade")  # plain fade in / out: hosts a text recipe's Premiere stack when it names no template
MOGRT_SAMPLE = [  # (folder, name): the text recipes' templates first, then other text families
    ("(2) Text Presets", "Position Characters Overshoot [Bottom]"), ("(2) Text Presets", "Decode & Fade Characters Separately"),
    ("(1) Text Boxes", "1-Line Fill Text Box 3 - Left"), ("(2) Text Presets", "Position & Mask Ease In [Bottom]"),
    ("(2) Text Presets", "Fade & Scale & Tracking Characters"), ("(2) Text Presets", "Fade"),
    ("(1) Text Boxes", "1-Line Fill Text Box 1 - Up"), ("(2) Text Presets", "Fade Characters Separately"),
    ("(2) Text Presets", "Scale From Center Ease In"), ("house", "HB Counter"),
    ("(5) Shape Elements", "Line Custom Duration [$Left To Right 1]"), ("(5) Shape Elements", "Rectangle Frame"),
    ("(2) Text Presets", "Position Ease In [Left]"), ("(2) Text Presets", "Position & Rotate Characters [Bottom]"),
    ("(2) Text Presets", "Fade Characters Together"), ("(1) Text Boxes", "Multiline Fill Text Box - Left"),
    ("(1) Universal", "Placeholder - Lower Third 1"), ("[AE] Sports Package", "Sports Lower Third Side"),
    ("[AE] Video Gaming Package", "Gaming Lower Third Left"), ("[AE] Sports Package", "Sports Intro"),
    ("Titles", "Classic Title"), ("Lower Thirds", "Bold Lower Third Left")]


def mogrt_files():
    """{(folder, name): absolute path} of every harvested template (%LOCALAPPDATA% expanded, house ones from the repo)."""
    fs_ = json.loads((HERE / "harvest" / "files.json").read_text(encoding="utf-8"))["mogrts"]
    return {(m["folder"], m["name"]): str(HERE.parent / os.path.expandvars(m["file"])) for m in fs_}


def copy_text(entries, lang):
    """A template's text cues with the test copy: the first free field gets the main line, the next the second line;
    a counter keeps its numbers (To = 1947); Devanagari copy also gets a Devanagari font."""
    main, second, out, free = *COPY[lang], [], 0
    for e in entries:
        e = dict(e)
        if e.get("param") in ("From", "To", "Prefix", "Suffix"):
            if e["param"] == "To":
                e["text"] = "1947"
        else:
            e["text"] = (main, second)[min(free, 1)]
            free += 1
            if lang == "dev":
                e["font"] = DEVA_FONT
        out.append(e)
    return out


def text_spec(recipes, lang, bg, files, slot=5.0, dur=4.0, lead=1.0):
    """One bridge spec per language: for each text recipe a Premiere slot (its templates, the 'premiere' stack on the
    first; HOST when it names none) and then an After Effects slot (a title with its presets and 'after_effects' stack),
    dur s each, slot s apart, over a dark plate. Returns (spec, slots)."""
    mgts, texts, slots, t = [], [], [], lead
    for k, c in recipes:
        ms = c.get("mogrts") or [{"role": "host", "file": files[HOST], "text": [{"text": "<TITLE>"}]}]
        for j, m in enumerate(ms):
            mgts.append({"file": str(HERE.parent / os.path.expandvars(m["file"])), "start": t, "dur": dur,
                         "track": 3 + j, "params": copy.deepcopy(m.get("params") or {}),
                         "text": copy_text(m.get("text") or [], lang),
                         "effects": copy.deepcopy(c.get("premiere") or []) if j == 0 else []})
        slots.append({"k": k, "name": c["name"], "half": "templates", "start": t,
                      "templates": [Path(m["file"]).stem for m in ms] + ([] if c.get("mogrts") else ["(host)"])})
        t += slot
        texts.append({"text": COPY[lang][0], "start": t, "dur": dur, "y": 0.5, "size": 150, "color": "#F2F2F2",
                      "anim": False, "presets": copy.deepcopy(c.get("presets") or []),
                      "effects": copy.deepcopy(c.get("after_effects") or []),
                      **({"font": "BebasNeueBold"} if lang == "lat" else {})})
        slots.append({"k": k, "name": c["name"], "half": "ae_title", "start": t,
                      "presets": [p["file"] for p in c.get("presets") or []]})
        t += slot
    return {"name": f"gal_text_{lang}", "width": 1920, "height": 1080, "fps": FPS, "clips": [{"src": str(bg), "dur": t}],
            "texts": texts, "mgts": mgts}, slots


SAMPLE_TEXT = {"HB Counter": [{"param": "From", "text": "0"}, {"param": "To", "text": "<N>"}, {"param": "Label", "text": "<L>"}]}


def mogrt_spec(sample, lang, bg, slot=6.0, dur=5.0, lead=1.0):
    """One spec per language: each sampled template alone for dur s with the test copy in its first two text fields."""
    mgts, slots, t = [], [], lead
    for k, m in sample:
        mgts.append({"file": m["path"], "start": t, "dur": dur, "track": 2,
                     "text": copy_text(SAMPLE_TEXT.get(m["name"], [{"text": "<A>"}, {"text": "<B>"}]), lang)})
        slots.append({"k": k, "name": m["name"], "folder": m["folder"], "half": "template", "start": t})
        t += slot
    return {"name": f"gal_mogrts_{lang}", "width": 1920, "height": 1080, "fps": FPS,
            "clips": [{"src": str(bg), "dur": t}], "mgts": mgts}, slots


def timing(fr, bg, fps, t0=0.0, delta=24, least=0.0005, still=0.0002, near=0.95):
    """In / out timing of text over a flat plate, from per-frame gray scans. Visible: more than `least` of the pixels
    differ from the plate by delta+ levels (ink). Level: the mean |frame - plate|; hold = its median over the visible
    span. The in-animation has settled at the first frame at `near` x hold or more after which 4 frames are quiet
    (under `still` of the pixels change by delta+ and the level moves under 1% of hold: a fade fails the second, a
    move the first); the out-animation starts after the last such frame, and a title that holds to its last visible
    frame ends on a cut. Seconds from the cue; t0 is the first scan frame's time relative to it."""
    n = len(bg)
    ink = [sum(1 for a, b in zip(f, bg) if abs(a - b) >= delta) / n for f in fr]
    lev = [sum(abs(a - b) for a, b in zip(f, bg)) / n for f in fr]
    mov = [0.0] + [sum(1 for a, b in zip(f, g) if abs(a - b) >= delta) / n for g, f in zip(fr, fr[1:])]
    T = lambda i: None if i is None else round(t0 + i / fps, 3)  # noqa: E731
    vis = [i for i, x in enumerate(ink) if x >= least]
    if not vis:
        return {"visible": False, "peak_ink": round(max(ink, default=0), 4)}
    a, b = vis[0], vis[-1]
    hold = sorted(lev[a:b + 1])[(b - a) // 2]
    quiet = lambda j: mov[j] < still and abs(lev[j] - lev[j - 1]) < 0.01 * hold  # noqa: E731
    full = [i for i in range(a, b + 1) if lev[i] >= near * hold]
    after = [i for i in full if i + 4 < len(fr) and all(quiet(j) for j in range(i + 1, i + 5))]
    before = [i for i in full if i >= 4 and all(quiet(j) for j in range(i - 3, i + 1))]
    last = before[-1] if before else None
    out = last + 1 if last is not None and last < b else None
    return {"visible": True, "first_visible_s": T(a), "in_settled_s": T(after[0] if after else None),
            "out_starts_s": T(out), "last_visible_s": T(b),
            "ends": "cut" if last == b else "animated" if out is not None else "never settles",
            "hold_ink": round(sorted(ink[a:b + 1])[(b - a) // 2], 4), "peak_ink": round(max(ink), 4)}


def slot_timing(video, start, dur, bg, pad=0.6, lead=0.2, w=320, h=180):
    import flash_scan as fs
    return timing(fs.frames(video, max(0.0, start - lead), start + dur + pad, w, h), bg, FPS, -min(lead, start))


def text_gallery(only=None, retime=False):
    """The 17 text recipes and MOGRT_SAMPLE built through the bridge (pr_build) in Latin and Devanagari over a dark
    plate, rendered in Premiere (and the recipes' title slots also by aerender); frames 0.5 s, 1.5 s and 0.3 s before
    the end of each slot; in / out timing per slot -> work/pve_live/wave2/text_timing.json. retime: measure the renders
    that file already names again (after a change to timing()) instead of building."""
    import flash_scan as fs
    old = json.loads((LIVE / "wave2" / "text_timing.json").read_text(encoding="utf-8")) if retime else None
    tm, mo = HERE / "gallery" / "text_motion", HERE / "gallery" / "mogrts"
    for d in (tm, mo):
        d.mkdir(parents=True, exist_ok=True)
    bg = tm / "plate_dark.png"
    if not bg.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x15181D:s=1920x1080", "-frames:v", "1",
                        str(bg)], check=True)
    files = mogrt_files()
    recipes = stable(tm, [c for c in cards("recipe") if c.get("family") == "text_motion"
                          and (only is None or c["name"] in only)], "name")
    sample = stable(mo, [{"folder": f, "name": n, "path": files[(f, n)]} for f, n in MOGRT_SAMPLE
                         if only is None or n in only], "name")
    res = {"method": f"Premiere export (Match Source, {FPS} fps) of a bridge build per language: each slot alone over a "
                     f"flat #15181D plate; per-frame gray scans at 320x180; text visible = more than 0.05% of the pixels "
                     f"24+ levels off the plate; hold = the median of that share over the visible span; settled = at 90% "
                     f"of the hold or more and the next 4 frames change under 0.02% of the pixels; times in s from the "
                     f"slot's cue", "copy": COPY, "devanagari_font": DEVA_FONT, "text_motion": [], "mogrts": [],
           "builds": {}}
    def shots(video, d, k, lang, half, dur, t):
        got = []
        for s, x in (("a", 0.5), ("b", 1.5), ("c", dur - 0.3)):
            frame(video, t + x, d / f"{k:03d}_{lang}_{half}_{s}.png")
            got.append(rel(d / f"{k:03d}_{lang}_{half}_{s}.png"))
        return got
    for lang in COPY:
        for what, d, (spec, slots), ae in (
                ("text_motion", tm, text_spec(recipes, lang, bg, files), True),
                ("mogrts", mo, mogrt_spec(sample, lang, bg), False)):
            if not slots:
                continue
            if retime:
                ob = old["builds"].get(f"{what}_{lang}") or {}
                if "pr_mp4" not in ob:
                    continue
                res["builds"][f"{what}_{lang}"] = ob
                b = {x: str(HERE.parent / ob[x]) for x in ("ae_mp4", "pr_mp4") if x in ob}
            else:
                ram_guard()
                ae_clean()
                try:
                    b = ab.build(dict(spec, out_dir=str(d / "builds")), d, render=True, premiere=True, ae=ae)
                except SystemExit as e:
                    res["builds"][f"{what}_{lang}"] = {"failed": str(e)[:400]}
                    ab.restart_ae()
                    continue
                res["builds"][f"{what}_{lang}"] = {"warnings": b["warnings"],
                                                   **{x: rel(b[x]) for x in ("ae_mp4", "pr_mp4") if x in b}}
            plate = fs.frames(b["pr_mp4"], 0.3, 0.34, 320, 180)[0]
            dur = 4.0 if what == "text_motion" else 5.0
            for s in slots:
                renders = [("pr", b["pr_mp4"])] + ([("ae", b["ae_mp4"])] if s["half"] == "ae_title" and "ae_mp4" in b else [])
                for app, video in renders:
                    half = f"{s['half']}_{app}" if what == "text_motion" else s["half"]
                    res[what].append({**s, "lang": lang, "render": app, "cue_s": s["start"],
                                      **slot_timing(video, s["start"], dur, plate),
                                      "frames": shots(video, d, s["k"], lang, half, dur, s["start"])})
    for d, rows in ((tm, [{"k": k, "name": c["name"]} for k, c in recipes]), (mo, [{"k": k, "name": m["name"],
                                                                                    "folder": m["folder"]} for k, m in sample])):
        write_index(d, rows)
    res["devanagari_vs_latin"] = devanagari_compare(res)
    report("wave2/text_timing.json", res)
    return res


def devanagari_compare(res):
    """Per slot: whether the Devanagari copy showed and how its timing / ink compare with the Latin copy's."""
    out = []
    for what in ("text_motion", "mogrts"):
        by = {}
        for r in res[what]:
            by.setdefault((r["k"], r["half"], r["render"]), {})[r["lang"]] = r
        for (k, half, app), v in sorted(by.items()):
            lat, dev = v.get("lat"), v.get("dev")
            if not (lat and dev):
                continue
            row = {"what": what, "k": k, "name": lat["name"], "half": half, "render": app,
                   "latin_visible": lat["visible"], "devanagari_visible": dev["visible"]}
            if lat["visible"] and dev["visible"]:
                row.update({f"d_{x}": round(dev[x] - lat[x], 3) for x in ("first_visible_s", "in_settled_s", "out_starts_s",
                                                                          "last_visible_s") if dev.get(x) is not None and lat.get(x) is not None})
                if lat.get("hold_ink") and dev.get("hold_ink"):
                    row["ink_ratio_dev_over_lat"] = round(dev["hold_ink"] / lat["hold_ink"], 2)
            out.append(row)
    return out


def pr_batches(todo, out, fx, frames, batch, only=None):
    """Premiere renders of todo[(k, card)] in batches (one sequence + render each); clip j carries fx(card). A batch
    whose export fails ("Error compiling movie": one item's settings break Premiere's renderer) is halved until the
    culprit is isolated; culprits go to out/failed.json with the error."""
    failed = json.loads((out / "failed.json").read_text()) if (out / "failed.json").exists() else {}
    warns = json.loads((out / "warnings.json").read_text()) if (out / "warnings.json").exists() else []
    todo = picked(todo, lambda c: c["name"], only, out, [s for s, _ in frames], failed)

    def run(items):
        clips = [{"src": str(MEDIA / "test_a.mp4"), "in": 1.0, "start": 0, "dur": 2.0, "track": 1}]  # untouched reference
        for j, (k, c) in enumerate(items):
            clips.append({"src": str(MEDIA / "test_a.mp4"), "in": 1.0, "start": (j + 1) * 2.0, "dur": 2.0, "track": 1,
                          "effects": json.loads(json.dumps(fx(c)))})  # a copy: build() normalizes in place
        ae_clean()  # AE relays the job: a dirty gallery project would leave it deaf
        try:
            res = ab.build({"name": f"pve_{out.name}_gallery_{items[0][0]:03d}", "width": 1920, "height": 1080, "fps": FPS,
                            "clips": clips, "out_dir": str(out / "builds")}, out, render=True, premiere=True, ae=False)
        except SystemExit as e:
            return str(e)
        warns.extend(res["warnings"])
        if not (out / "ref.png").exists():
            frame(res["pr_mp4"], 1.0, out / "ref.png")
        for j, (k, c) in enumerate(items):
            for suffix, t in frames:
                frame(res["pr_mp4"], (j + 1) * 2.0 + t, out / f"{k:03d}{suffix}.png")
        return None

    def go(items):
        if not items:
            return
        err = run(items)
        if err is None:
            return
        if len(items) == 1:
            failed[items[0][1]["name"]] = err[:300]
            (out / "failed.json").write_text(json.dumps(failed, indent=1), encoding="utf-8")
            return
        go(items[:len(items) // 2])
        go(items[len(items) // 2:])

    items = [(k, c) for k, c in todo if c["name"] not in failed and not (out / f"{k:03d}{frames[0][0]}.png").exists()]
    for i in range(0, len(items), batch):
        go(items[i:i + batch])
        print(f"{out.name} gallery {min(i + batch, len(items))}/{len(items)}", flush=True)
    (out / "warnings.json").write_text(json.dumps(warns, indent=1), encoding="utf-8")


def tr_gallery():
    """Every Premiere video transition on an A -> B cut (1 s, centred); frames before / at / after each cut."""
    out = HERE / "gallery" / "tr"
    out.mkdir(parents=True, exist_ok=True)
    lists = json.loads((HERE / "harvest" / "pr" / "pr_lists.json").read_text(encoding="utf-8"))
    names = lists["video_transitions"]
    clips = []
    for k, n in enumerate(names):
        clips.append({"src": str(MEDIA / "test_a.mp4"), "in": 1.0, "start": k * 4.0, "dur": 2.0, "track": 1})
        clips.append({"src": str(MEDIA / "test_b.mp4"), "in": 1.5, "start": k * 4.0 + 2.0, "dur": 2.0, "track": 1,
                      "transition": {"name": n, "dur": 1.0}})
    res = ab.build({"name": "pve_tr_gallery", "width": 1920, "height": 1080, "fps": FPS, "clips": clips, "out_dir": str(out)},
                   out, render=True, premiere=True, ae=False)
    (out / "warnings.json").write_text(json.dumps(res["warnings"], indent=1), encoding="utf-8")
    for k, n in enumerate(names):
        for tag, dt in (("a", -0.3), ("b", 0.0), ("c", 0.3)):
            frame(res["pr_mp4"], k * 4.0 + 2.0 + dt, out / f"{k:03d}{tag}.png")
    (out / "index.json").write_text(json.dumps([{"k": k, "name": n} for k, n in enumerate(names)], indent=1), encoding="utf-8")


def rel(p):
    """Repo-relative posix path for the reports (they may be tracked: no machine paths)."""
    return Path(p).resolve().relative_to(HERE.parent).as_posix()


def report(name, data):
    (LIVE / name).parent.mkdir(parents=True, exist_ok=True)
    (LIVE / name).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote", rel(LIVE / name), flush=True)


def scanned(fs, fr, ref, fps):
    """flash_scan facts of one test from its frames and its control's frames."""
    sig, area = fs.signal(fr, ref)
    ch = fs.changes(sig)
    return {"area": round(area, 3), "swing": round(max(sig) - min(sig), 1) if sig else 0,
            "changes": [[i, d] for i, d in ch], **fs.rate(ch, fps, len(fr)), **fs.local(fr, ref, fps),
            "signal": [round(v, 1) for v in sig]}


def coverage(fr, ref, color=255):
    """Per frame, how far it moved from the control toward the strobe colour: 0 untouched, 1 solid colour. Normalising
    by the room each pixel had keeps the plate's own near-white frames from hiding a flash or faking one."""
    return [sum(abs(a - b) for a, b in zip(f, r)) / max(1, sum(abs(color - b) for b in r)) for f, r in zip(fr, ref)]


def strobe_stats(on, fps, t_layer=0.0, t_seq=0.0, base=None):
    """For a per-frame strobe-coloured list `on`: a '#'/'.' strip, its runs (a run = one flash) with start times on the
    layer / clip clock and on the comp / sequence clock, flashes per second, and the frames that differ from `base`
    (the same test with Random Strobe Probablity 0)."""
    import flash_scan as fs
    rs = fs.runs([float(x) for x in on], 1)
    st = [i for i, _ in rs]
    win = max((sum(1 for x in st if a <= x < a + fps) for a in range(max(1, len(on) - fps + 1))), default=0)
    d = {"strip": "".join("#" if x else "." for x in on), "flash_runs": [[i, n] for i, n in rs],
         "starts_layer_s": [round(i / fps - t_layer, 2) for i in st],
         "starts_comp_s": [round(i / fps + t_seq, 2) for i in st],
         "flashes": len(rs), "max_flashes_in_1s": win, "mean_flashes_per_s": round(len(rs) / (len(on) / fps), 2)}
    if base is not None:
        diff = [i for i, (x, y) in enumerate(zip(on, base)) if x != y]
        d.update(frames_differing_from_p0=diff, differing_fraction=round(len(diff) / len(on), 3),
                 turned_on=sum(1 for i in diff if on[i]), turned_off=sum(1 for i in diff if not on[i]))
    return d


STROBE = {"Strobe Color": "#FFFFFF", "Blend With Original": 0, "Strobe Duration (secs)": 0.1, "Strobe Period (secs)": 0.5}


def strobe():
    """Settle Strobe Light (critic next step): Duration 0.1, Period 0.5, Random Strobe Probablity 0 and 10, the layer
    starting at comp / clip time 0, plus one test shifted in the source (in 0.8 s), in AE one starting later in the
    comp (0.6 s), and grey-solid / grey-still tests (P0 and P10 for 8 s, P50 and P100 for 2.8 s) whose frames are
    unambiguous. Footage tests are compared frame by frame with the same plate without the effect (the plate has its
    own exposure flashes). -> work/pve_live/strobe.json"""
    import flash_scan as fs
    out, D, LONG, src = HERE / "gallery" / "strobe", 2.8, 8.0, (MEDIA / "test_a.mp4").as_posix()
    out.mkdir(parents=True, exist_ok=True)
    grey = out / "grey.png"  # made once: Premiere keeps the media of its open projects locked
    if not grey.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x808080:s=1920x1080", "-frames:v",
                        "1", str(grey)], check=True)
    on = lambda fr, ref: [c >= 0.5 for c in coverage(fr, ref)]  # noqa: E731
    flat = lambda fr: [bytes([128]) * len(fr[0])] * len(fr)  # noqa: E731  (the grey host, untouched)
    fx = lambda p: [{"name": "ADBE Strobe", "props": {**STROBE, "Random Strobe Probablity": p}}]  # noqa: E731
    foot = lambda name, i, t0, e: {"name": name, "dur": D, "layers": [  # noqa: E731
        {"type": "footage", "name": "F", "src": src, "in": i, "t0": t0, "t1": D, **({"effects": e} if e else {})}]}
    ae = {"ae_p0": (0, 0, 0), "ae_p10": (0, 0, 10), "ae_p0_src0.8": (0.8, 0, 0), "ae_p0_layer_at_0.6": (0, 0.6, 0)}
    scenes = [foot("S_" + n, i, t0, fx(p)) for n, (i, t0, p) in ae.items()]
    scenes += [foot(f"S_ctrl_{i}_{t0}", i, t0, None) for i, t0 in sorted({(i, t0) for i, t0, _ in ae.values()})]
    solids = {0: LONG, 10: LONG, 50: D, 100: D}
    scenes += [{"name": f"S_ae_solid_p{p}", "dur": d, "layers": [{"type": "solid", "name": "G", "color": "#808080",
                                                                  "effects": fx(p)}]} for p, d in solids.items()]
    ae_clean()
    files = render_scenes(scenes, out / "gal_strobe.aep", out / f"renders_{int(time.time())}", w=960, h=540)
    res = {"settings": {**STROBE, "fps": FPS, "test_len_s": D, "grey_host_len_s": solids},
           "method": f"per-frame luma ({fs.W}x{fs.H}, area-averaged) of each render; a frame counts as strobe-coloured when it has "
                     "moved at least half-way from the same plate without the effect toward white (grey hosts: from "
                     "grey 128); a run of such frames is one flash", "after_effects": {}, "premiere": {}}
    A = res["after_effects"]
    for n, (i, t0, p) in ae.items():
        A[n] = {"probability": p, "source_in_s": i, "layer_start_comp_s": t0,
                **strobe_stats(on(fs.frames(files["S_" + n]), fs.frames(files[f"S_ctrl_{i}_{t0}"])), FPS, t0),
                "render": rel(files["S_" + n])}
    for p in solids:
        fr = fs.frames(files[f"S_ae_solid_p{p}"])
        A[f"ae_solid_p{p}"] = {"probability": p, "host": "grey solid #808080", **strobe_stats(on(fr, flat(fr)), FPS),
                               "render": rel(files[f"S_ae_solid_p{p}"])}
    # Premiere: one sequence, clips back to back; starts at multiples of 2.8 s sit off the 0.5 s grid, so clip time,
    # sequence time and source time give different flash phases
    pr = [("pr_p0", 0, 0, D), ("pr_p10", 0, 10, D), ("pr_p0_src0.8", 0.8, 0, D), ("ctrl_0", 0, None, D),
          ("ctrl_0.8", 0.8, None, D)] + [(f"pr_still_p{p}", None, p, d) for p, d in solids.items()]
    clips, t = [], 0.0
    for name, i, p, d in pr:
        clips.append({"src": str(grey) if i is None else str(MEDIA / "test_a.mp4"), "start": t, "dur": d, "track": 1,
                      **({"in": i} if i is not None else {}),
                      **({"effects": [{"name": "Strobe Light", "pr_props": {**STROBE, "Random Strobe Probablity": p}}]}
                         if p is not None else {})})
        t += d
    ae_clean()  # AE relays the Premiere job
    b = ab.build({"name": "pve_strobe_pr", "width": 1920, "height": 1080, "fps": FPS, "clips": clips,
                  "out_dir": str(out / "builds")}, out, render=True, premiere=True, ae=False)
    allf, seg, f0 = fs.frames(b["pr_mp4"]), {}, 0
    for name, i, p, d in pr:
        seg[name] = (f0, allf[f0:f0 + round(d * FPS)])
        f0 += round(d * FPS)
    for name, i, p, d in pr:
        if p is None:
            continue
        a, fr = seg[name]
        res["premiere"][name] = {"probability": p, "source_in_s": i, "clip_start_seq_s": round(a / FPS, 2),
                                 **({"host": "grey still"} if i is None else {}),
                                 **strobe_stats(on(fr, flat(fr) if i is None else seg[f"ctrl_{i:g}"][1]), FPS, 0, a / FPS),
                                 "render": rel(b["pr_mp4"]), "frames": [a, a + len(fr)]}
    pairs = [("after_effects", "ae_p0", "ae_p10"), ("premiere", "pr_p0", "pr_p10")]
    pairs += [(app, f"{k}_p0", f"{k}_p{p}") for app, k in (("after_effects", "ae_solid"), ("premiere", "pr_still"))
              for p in solids if p]
    for app, base, other in pairs:
        R = res[app]
        cmp = strobe_stats([c == "#" for c in R[other]["strip"]], FPS, base=[c == "#" for c in R[base]["strip"]])
        R[other].update({k: v for k, v in cmp.items() if k.startswith(("frames_diff", "differing", "turned"))})
    res["premiere_warnings"] = b["warnings"]
    report("strobe.json", res)
    return res


def which(f, a, b):
    """'A' when frame f is clearly nearer clip A's frame than clip B's (half the mean |difference| or less), 'B' the
    other way round, 'm' otherwise."""
    da, db = (sum(abs(x - y) for x, y in zip(f, r)) / len(f) for r in (a, b))
    return "A" if da <= db / 2 else "B" if db <= da / 2 else "m"


def by_eye():
    """Cards with a flash field from which pve.check_spec cannot compute a rate (its "check by eye" lines)."""
    import pve
    return [c for c in pve.cards() if isinstance(c.get("flash"), dict)
            and not any(k in c["flash"] for k in ("per_frame", "period", "rate", "per_use"))]


def flashes():
    """Measure every "check by eye" flasher at its gallery setup: AE presets applied at 0 s on their gallery host for
    3.9 s (marker-driven ones get layer markers at 0.5, 1.5 and 2.5 s), Premiere transitions on a 1 s A -> B cut;
    each scanned against the same host / cut without it. -> work/pve_live/flash_rates.json"""
    import flash_scan as fs
    out, D = HERE / "gallery" / "flashes", 3.9
    todo, presets = by_eye(), preset_files()
    rows, scenes, ctrl = [], [], {}
    for c in todo:
        if c["kind"] != "ae_preset":
            continue
        p = next(p for p in presets if p["name"] == c["name"] and p["category"] == c.get("category"))
        L = preset_layer(p, t1=D, src_in=0)
        if "marker" in c["name"].lower():
            L["markers"] = [0.5, 1.5, 2.5]
        bare = {k: v for k, v in L.items() if k not in ("presets", "markers")}
        cn = ctrl.setdefault(json.dumps(bare, sort_keys=True), f"C{len(ctrl):02d}")
        name = f"F{len(scenes):02d}"
        scenes.append(preset_scene(name, L, D))
        rows.append((c, name, cn, L.get("markers")))
    for key, cn in ctrl.items():
        scenes.append(preset_scene(cn, json.loads(key), D))
    ae_clean()
    files = render_scenes(scenes, out / "gal_flashes.aep", out / f"renders_{int(time.time())}", w=960, h=540)
    res = []
    for c, name, cn, markers in rows:
        s = scanned(fs, fs.frames(files[name]), fs.frames(files[cn]), FPS)
        res.append({"id": f"{c['kind']}:{c.get('category')}/{c['name']}", "kind": c["kind"], "name": c["name"],
                    "category": c.get("category"), "measured_max_per_second": s["max_per_second"],
                    "measured_mean_per_second": s["mean_per_second"], "flashes": s["flashes"], "area": s["area"],
                    "swing": s["swing"], "markers_s": markers, **{k: s[k] for k in s if k.startswith("local")},
                    "method": f"After Effects aerender, 960x540 {FPS} fps, preset at 0 s on its gallery host for {D} s; "
                              f"per-frame luma ({fs.W}x{fs.H}) against the host without the preset; a flash = a rise and a "
                              f"fall of {fs.DELTA}+ levels in the mean over the pixels that change (local: the same in the "
                              f"worst cell of a {fs.GRID[0]}x{fs.GRID[1]} grid)",
                    "changes": s["changes"], "signal": s["signal"], "render": rel(files[name])})
    trs = [c for c in todo if c["kind"] == "pr_transition"]
    if trs:  # pair j: A (0-2 s) -> B (2-4 s) with the transition; the last pair is the same cut, hard
        clips = []
        for j, c in enumerate(trs + [None]):
            clips.append({"src": str(MEDIA / "test_a.mp4"), "in": 1.0, "start": j * 4.0, "dur": 2.0, "track": 1})
            clips.append({"src": str(MEDIA / "test_b.mp4"), "in": 1.5, "start": j * 4.0 + 2.0, "dur": 2.0, "track": 1,
                          **({"transition": {"name": c["name"], "dur": 1.0}} if c else {})})
        ae_clean()
        b = ab.build({"name": "pve_flash_tr", "width": 1920, "height": 1080, "fps": FPS, "clips": clips,
                      "out_dir": str(out / "builds")}, out, render=True, premiere=True, ae=False)
        allf, n = fs.frames(b["pr_mp4"]), 4 * FPS
        hard = allf[len(trs) * n + FPS: len(trs) * n + 3 * FPS]  # 1-3 s of each pair: the 1 s transition and 0.5 s around
        # what each clip shows at 1-3 s of a pair (A from its in point 1.0, B from 1.5 at the 2 s cut), for the A / B strip
        fa, fb = fs.frames(MEDIA / "test_a.mp4", 2.0, 4.0), fs.frames(MEDIA / "test_b.mp4", 0.5, 2.5)
        for j, c in enumerate(trs):
            win = allf[j * n + FPS: j * n + 3 * FPS]
            s = scanned(fs, win, hard, FPS)
            strip = "".join(which(f, a, b_) for f, a, b_ in zip(win, fa, fb))
            sw = [i for i in range(1, len(strip)) if strip[i] != strip[i - 1]]
            s["ab"] = {"strip": strip, "switches": len(sw), "back_and_forth": len(sw) // 2,
                       "max_back_and_forth_in_1s": max((sum(1 for i in sw if x <= i < x + FPS)
                                                        for x in range(max(1, len(strip) - FPS + 1))), default=0) // 2,
                       "legend": "per frame from 1 s before the cut: A = clip A, B = clip B, m = neither (a mix or a "
                                 "changed picture), by mean |difference| to each clip's own frames"}
            res.append({"id": f"{c['kind']}:{c.get('category')}/{c['name']}", "kind": c["kind"], "name": c["name"],
                        "category": c.get("category"), "measured_max_per_second": s["max_per_second"],
                        "measured_mean_per_second": s["mean_per_second"], "flashes": s["flashes"], "area": s["area"],
                        "swing": s["swing"], **{k: s[k] for k in s if k.startswith("local")},
                        "method": f"Premiere render, 1920x1080 {FPS} fps, 1 s transition centred on an A -> B cut; frames "
                                  f"1-3 s of the pair against the same cut without a transition; a flash = a rise and a "
                                  f"fall of {fs.DELTA}+ levels in the mean |difference| over the pixels that change "
                                  f"(local: the same in the worst cell of a {fs.GRID[0]}x{fs.GRID[1]} grid)",
                        "ab": s["ab"], "changes": s["changes"], "signal": s["signal"], "render": rel(b["pr_mp4"]),
                        "frames": [j * n + FPS, j * n + 3 * FPS]})
        warns = b["warnings"]
    else:
        warns = []
    report("flash_rates.json", {"cards": len(todo), "rates": res, "premiere_warnings": warns})
    return res


def _cli(argv):
    kind, only, gpu = argv[0], None, "--gpu" in argv
    if kind == "text" and "--retime" in argv:
        return text_gallery(retime=True)
    if "--only" in argv:
        only = set(json.loads(Path(argv[argv.index("--only") + 1]).read_text(encoding="utf-8")))
    fn = {"ae": ae_gallery, "pr": pr_gallery, "tr": tr_gallery, "presets": preset_gallery, "recipes": recipe_gallery,
          "recipes_ae": recipe_ae_gallery, "specs": spec_gallery, "text": text_gallery,
          "strobe": strobe, "flashes": flashes}[kind]
    kw = {k: v for k, v in (("only", only), ("gpu", gpu)) if v} if kind in ("ae", "presets") else (
        {"only": only} if only is not None and kind in ("pr", "recipes", "recipes_ae", "specs", "text") else {})
    fn(**kw)


if __name__ == "__main__":
    _cli(sys.argv[1:])
