"""AdobeBridge: timeline JSON -> After Effects .aep + Premiere Pro .prproj, left open in both apps.

    python adobe_bridge.py spec.json [--render] [--no-pr]

or from another pipeline:  from adobe_bridge import build; build(spec_dict, base_dir)

After Effects runs the generated JSX via `AfterFX.exe -r`; Premiere has no script flag, so AE relays the
Premiere JSX over BridgeTalk. Texts are AE comps (TXT_01..) Dynamic-Linked into Premiere.
"""
import argparse, contextlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
# Install locations; override with env vars for another drive or version (PVE_AFTERFX also moves aerender + Presets).
ADOBE = Path(os.environ.get("PVE_ADOBE_DIR", r"C:\Program Files\Adobe"))
AFTERFX = Path(os.environ.get("PVE_AFTERFX", ADOBE / "Adobe After Effects 2026/Support Files/AfterFX.exe"))
AERENDER = AFTERFX.with_name("aerender.exe")
PRESETS = AFTERFX.parent / "Presets"
EPR = ADOBE /"Adobe Media Encoder 2026/MediaIO/systempresets/4E49434B_48323634/00 - Match Source - High bitrate.epr"
STILLS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".psd"}


def fwd(p):
    return str(Path(p).resolve()).replace("\\", "/")


def probe(src):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:format=duration", "-of", "json", src],
                       capture_output=True, text=True, check=True)
    j = json.loads(r.stdout)
    s = (j.get("streams") or [{}])[0]
    return s.get("width"), s.get("height"), float(j.get("format", {}).get("duration") or 0)


def normalize(spec, base):
    """Fill defaults, resolve paths, compute cover-fit scale. Returns a new dict; input is not mutated."""
    S = json.loads(json.dumps(spec))
    S.setdefault("name", "adobe_bridge")
    S.setdefault("fps", 30)
    W, H = S["width"], S["height"]
    if not S.get("clips"):
        sys.exit("spec needs at least one clip (Premiere builds its sequence from the first one)")
    if isinstance(S["fps"], bool) or not isinstance(S["fps"], (int, float)) or not 0 < S["fps"] < float("inf"):
        sys.exit(f"spec fps {S['fps']!r}: needs a number above 0")
    for k, keys in (("texts", ()), ("mgts", ("file",)), ("aecomps", ("aep", "comp"))):
        for i, x in enumerate(S.get(k) or []):
            miss = [n for n in keys + ("start", "dur") if not isinstance(x, dict) or n not in x]
            if miss:
                sys.exit(f"{k} {i + 1}: needs " + " and ".join(f'"{n}"' for n in miss) + " (start / dur in seconds)")
    ends, files, fps = {}, [], S["fps"]

    def snap(x):
        """Both apps put clips on whole frames; give them identical frame-exact times (2.5 s at 25 fps is not one)."""
        for k in ("in", "start", "dur"):
            x[k] = round(round(x[k] * fps) / fps, 6)

    def dress(x, label):
        """effects: "Name" or {name, props, pr_name, pr_props}; presets: "Text/Animate In/X.ffx" or {file, at}."""
        fx = [e if isinstance(e, dict) else {"name": e} for e in x.get("effects", [])]
        for e in fx:
            e.setdefault("props", {})
            e.setdefault("pr_name", e["name"])
            e.setdefault("pr_props", e["props"])
        x["effects"] = fx
        ps = [p if isinstance(p, dict) else {"file": p} for p in x.get("presets", [])]
        for p in ps:
            f = next((c for c in (Path(base, p["file"]), PRESETS / p["file"]) if c.is_file()), None)
            if not f:
                sys.exit(f"{label}: preset not found: {p['file']} (looked in {base} and {PRESETS})")
            p["file"], p["at"] = fwd(f), p.get("at", 0)
        x["presets"] = ps

    def src(x):
        p = Path(base, x["src"])
        if not p.is_file():
            sys.exit(f"missing source: {p}")
        x["src"] = fwd(p)
        if x["src"] not in files:
            files.append(x["src"])
        x.setdefault("in", 0)

    for i, c in enumerate(S["clips"]):
        src(c)
        dress(c, f"clip {i + 1}")
        w, h, d = probe(c["src"])
        c["still"] = Path(c["src"]).suffix.lower() in STILLS
        c.setdefault("track", 1)
        c.setdefault("dur", 3 if c["still"] else int((d - c["in"]) * fps) / fps)  # whole frames, never past the end
        c.setdefault("start", ends.get(c["track"], 0))
        snap(c)
        ends[c["track"]] = c["start"] + c["dur"]
        c["scale"] = round(100 * max(W / w, H / h), 3)  # cover the frame
        c.setdefault("zoom", 1)
        c["media_dur"] = None if c["still"] else d
    plan_transitions(S, base)
    top = max(c["track"] for c in S["clips"])
    vend = max(c["start"] + c["dur"] for c in S["clips"])  # the picture's end, whatever order the clips are listed in
    for a in S.setdefault("audio", []):
        src(a)
        a["still"] = False
        a.setdefault("start", 0)
        a.setdefault("dur", min(int((probe(a["src"])[2] - a["in"]) * fps) / fps, vend - a["start"]))  # stop with the picture
        a.setdefault("track", top + 1)  # clip audio sits on A1..A{top}
        a.setdefault("gain_db", 0)
        snap(a)
    for i, t in enumerate(S.setdefault("texts", [])):
        t["comp"] = f"TXT_{i + 1:02d}"
        t.setdefault("in", 0)
        snap(t)
        t.setdefault("size", 90)
        t.setdefault("color", "#FFFFFF")
        t.setdefault("y", 0.5)
        t.setdefault("stroke", None)
        t.setdefault("stroke_width", 6)
        t["indic"] = any("\u0900" <= ch <= "\u0dff" for ch in t["text"])  # Devanagari..Sinhala
        t.setdefault("font", "NirmalaUI-Bold" if t["indic"] else "Arial-BoldMT")
        dress(t, t["comp"])
        t.setdefault("anim", not t["presets"])  # built-in fade/pop only when no preset animates the text
    for g in S.setdefault("mgts", []):  # motion graphics templates (Premiere only)
        f = Path(base, g["file"])
        if not f.is_file():
            sys.exit(f"missing .mogrt: {f}")
        g["file"], g["in"] = fwd(f), 0
        g.setdefault("name", f.stem)
        g.setdefault("params", {})
        dress(g, f"mgt {g['name']}")
        tx = g.get("text", [])
        g["text"] = [x if isinstance(x, dict) else {"text": x} for x in (tx if isinstance(tx, list) else [tx])]
        snap(g)
    for a in S.setdefault("aecomps", []):  # Dynamic-Linked After Effects comps (Premiere only)
        f = Path(base, a["aep"])
        if not f.is_file():
            sys.exit(f"missing .aep: {f}")
        a["aep"] = fwd(f)
        a.setdefault("in", 0)
        dress(a, f"AE comp {a['comp']}")
        snap(a)
    S["aecomp_names"] = {p: sorted({a["comp"] for a in S["aecomps"] if a["aep"] == p}) for p in {a["aep"] for a in S["aecomps"]}}
    S["text_track"] = top + 1
    S["video_tracks"] = max([3, top + (1 if S["texts"] else 0)] + [x["track"] for x in S["mgts"] + S["aecomps"]])
    S["audio_tracks"] = max([3] + [a["track"] for a in S["audio"]])
    S["clip_order"] = sorted(range(len(S["clips"])), key=lambda i: (S["clips"][i]["track"], S["clips"][i]["start"]))
    S["files"] = files
    ends_all = [x["start"] + x["dur"] for x in S["clips"] + S["audio"] + S["texts"] + S["mgts"] + S["aecomps"]]
    S["duration"] = round(max(ends_all), 3)
    return S


XFADE = {"Cross Dissolve", "Cross Dissolve (Legacy)", "Film Dissolve", "Film Dissolve (Legacy)"}
DIP = {"Dip to Black": "#000000", "Dip to Black (Legacy)": "#000000",
       "Dip to White": "#FFFFFF", "Dip to White (Legacy)": "#FFFFFF"}


def plan_transitions(S, base):
    """Premiere transitions (`transition` into a clip, `transition_out` at a clip's end with nothing after it).

    Premiere applies them natively. For AE this works out the same frame window and turns it into plain layer
    instructions: dissolves = crossfade using handles, dips = colour solid, anything else = the spec's `ae_preset`.
    """
    fps, clips, warns = S["fps"], S["clips"], S.setdefault("pywarn", [])
    near = lambda a, b: abs(a - b) < 0.5 / fps
    for c in clips:
        c.update(ae_in=c["start"], ae_out=c["start"] + c["dur"], opacity=[], solids=[], tpresets=[], trans=[])

    def spare(c, side):  # whole frames of media beyond a clip's in point ("pre") or out point ("post")
        if c["still"]:
            return 10 ** 6
        return int(c["in"] * fps + 1e-6) if side == "pre" else int((c["media_dur"] - c["in"] - c["dur"]) * fps + 1e-6)

    for i, c in enumerate(clips):
        for key, side in (("transition", "in"), ("transition_out", "out")):
            t = c.get(key)
            if not t:
                continue
            t = {"name": t} if isinstance(t, str) else dict(t)
            t.setdefault("dur", 0.5)
            label, name = f"clip {i + 1} {key}", t["name"]
            frames = max(1, round(t["dur"] * fps))
            cut = c["start"] if side == "in" else c["start"] + c["dur"]
            other = next((o for o in clips if o is not c and o["track"] == c["track"]
                          and near(o["start"] + o["dur"] if side == "in" else o["start"], cut)), None)
            if side == "out" and other:
                sys.exit(f"{label}: clip {clips.index(other) + 1} follows directly; put the transition on that clip")
            if other:  # centred on the cut, odd frame after it; Premiere drops whatever the handles can't cover
                a = min(frames // 2, spare(c, "pre"))
                b = min(frames - frames // 2, spare(other, "post"))
                if a + b < frames:
                    warns.append(f"{label}: only {a + b} of {frames} frames fit in the media beyond the cut, "
                                 "both apps use the shorter transition")
                if not a + b:
                    continue
            else:  # one-sided: inside the clip, from its start (in) or up to its end (out)
                a, b = (0, frames) if side == "in" else (frames, 0)
            t0, t1 = cut - a / fps, cut + b / fps
            c["trans"].append({"name": name, "side": side, "frames": frames, "t0": t0, "t1": t1, "label": label,
                               "single": other is None})  # nothing clip-like beside it (e.g. a DL comp)
            # Measured on Premiere 26: dissolves follow smoothstep (= AE Easy Ease); dips ease out into the colour
            # and ease in out of it; "(Legacy)" transitions are linear.
            lin = "Legacy" in name
            if name in XFADE:
                ease = "lin" if lin else "smooth"
                if other:
                    c["ae_in"], other["ae_out"] = t0, t1
                c["opacity"].append([t0, 100, t1, 0, ease] if side == "out" else [t0, 0, t1, 100, ease])
            elif name in DIP:
                col = DIP[name]
                if side == "out":
                    c["solids"].append({"color": col, "above": True, "in": t0, "out": t1,
                                        "segs": [[t0, 0, t1, 100, "lin" if lin else "in"]]})
                elif other:
                    c["solids"].append({"color": col, "above": False, "in": t0, "out": t1,
                                        "segs": [[t0, 0, cut, 100, "lin" if lin else "out"]]})
                    c["opacity"].append([cut, 0, t1, 100, "lin" if lin else "in"])
                else:
                    c["solids"].append({"color": col, "above": False, "in": t0, "out": t1, "segs": [[t0, 100, t0, 100, "lin"]]})
                    c["opacity"].append([t0, 0, t1, 100, "lin" if lin else "out"])
            elif t.get("ae_preset") and side == "in":
                f = next((p for p in (Path(base, t["ae_preset"]), PRESETS / t["ae_preset"]) if p.is_file()), None)
                if not f:
                    sys.exit(f"{label}: preset not found: {t['ae_preset']}")
                if other:
                    c["ae_in"], other["ae_out"] = t0, t1
                c["tpresets"].append({"file": fwd(f), "t": t0})
            else:
                warns.append(f"AE {label}: no AE version of '{name}' (use a dissolve/dip, or give an "
                             f"\"ae_preset\" for a transition in); AE shows a cut")


def outputs(S, base):
    out = Path(base, S.get("out_dir", f"{S['name']}_adobe"))
    out.mkdir(parents=True, exist_ok=True)
    stem, n = S["name"], 1
    while (out / f"{stem}.aep").exists() or (out / f"{stem}.prproj").exists():  # never overwrite an open project
        n += 1
        stem = f"{S['name']}_v{n}"
    S.update(aep=fwd(out / f"{stem}.aep"), titles_aep=fwd(out / f"{stem}_pr_titles.aep"),
             prproj=fwd(out / f"{stem}.prproj"),
             ae_mp4=fwd(out / f"{stem}_ae.mp4"), pr_mp4=fwd(out / f"{stem}_pr.mp4"),
             pr_result=fwd(out / f"{stem}_pr_result.txt"), epr=fwd(EPR))
    return out


def ae_titles():
    """Window titles of every AfterFX.exe (the GUI plus any headless Dynamic Link instances)."""
    r = subprocess.run(["tasklist", "/v", "/fo", "csv", "/fi", "imagename eq AfterFX.exe"], capture_output=True, text=True)
    return [row.split('","')[-1].rstrip('"') for row in r.stdout.splitlines()[1:]]


def _launch(*args):
    """Start AfterFX.exe through PowerShell Start-Process: launched directly from a hidden build process it ran the
    job and quit, or never showed a window; Start-Process gives it a normal desktop window every time."""
    arg = (" -ArgumentList " + ",".join("'" + a.replace("'", "''") + "'" for a in args)) if args else ""
    subprocess.run(["powershell", "-NoProfile", "-Command", f"Start-Process -FilePath '{AFTERFX}'{arg}"], check=True)


PREMIERE = Path(os.environ.get("PVE_PREMIERE", ADOBE / "Adobe Premiere Pro 2026/Adobe Premiere Pro.exe"))


def ensure_premiere(timeout=900):
    """Start Premiere ourselves and wait for its window. BridgeTalk.launch from AE on a cold, memory-starved machine took
    longer than the relay's 60 s pickup window, so a healthy job was reported as never received (2026-10-07)."""
    def up():
        r = subprocess.run(["tasklist", "/v", "/fo", "csv", "/fi", "imagename eq Adobe Premiere Pro.exe"],
                           capture_output=True, text=True)
        return any(not row.rstrip('"').endswith("N/A") for row in r.stdout.splitlines()[1:])  # titled = UI is up
    if up():
        return
    print("Starting Premiere Pro and waiting for its window", flush=True)
    subprocess.run(["powershell", "-NoProfile", "-Command", f"Start-Process -FilePath '{PREMIERE}'"], check=True)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if up():
            time.sleep(30)  # the window comes before the scripting engine is ready to take BridgeTalk messages
            return
        time.sleep(5)
    sys.exit(f"Premiere Pro did not open within {timeout} s")


def run_in_ae(code, path):
    path = Path(path).resolve()  # AE resolves a relative -r path against its own cwd and silently ignores it
    path.write_text(code, encoding="ascii")  # ExtendScript garbles UTF-8; json.dumps(ensure_ascii) keeps it ASCII
    _launch("-r", _short(path))


def _short(path):
    """8.3 path: Start-Process joins arguments unquoted, and quoting them made AE ignore the script."""
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
    return buf.value or str(path)


def _ae_dialogs():
    """Visible '#32770' windows of AfterFX.exe as (hwnd, width, height): the splash is tall, Crash Repair is wide."""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    pids = {int(r.split('","')[1]) for r in subprocess.run(["tasklist", "/fo", "csv", "/fi", "imagename eq AfterFX.exe"],
                                                           capture_output=True, text=True).stdout.splitlines()[1:]}
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        pid, cls, r = wt.DWORD(), ctypes.create_unicode_buffer(64), wt.RECT()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        u.GetClassNameW(h, cls, 64)
        if pid.value in pids and u.IsWindowVisible(h) and cls.value == "#32770":
            u.GetWindowRect(h, ctypes.byref(r))
            out.append((h, r.right - r.left, r.bottom - r.top))
        return True
    u.EnumWindows(cb, 0)
    return out


def _dismiss_warning(wait=20):
    """AE opens a modal 'Warning' (disk cache folder short of space for the size set in Preferences) once per session,
    a few seconds after start; it blocks every -r job. OK it (the preference itself is the user's to change)."""
    import ctypes
    u, t0 = ctypes.windll.user32, time.time()
    while time.time() - t0 < wait:
        for h, w, hh in _ae_dialogs():
            buf = ctypes.create_unicode_buffer(64)
            u.GetWindowTextW(h, buf, 64)
            if buf.value == "Warning":
                for msg in (0x100, 0x101):
                    u.PostMessageW(h, msg, 0x0D, 0)
                return
        time.sleep(1)


_LOCK = {"depth": 0, "fh": None}


@contextlib.contextmanager
def ae_lock(timeout=3600):
    """One pipeline at a time drives After Effects. Two builds sharing AE collided: one's relay timed out behind the
    other's job and restarted AE under it. Jobs, Premiere relays and restarts hold this cross-process file lock
    (re-entrant within a process); a second pipeline waits for its turn instead."""
    import msvcrt
    if _LOCK["depth"]:
        _LOCK["depth"] += 1
        try:
            yield
        finally:
            _LOCK["depth"] -= 1
        return
    fh, t0, said = open(HERE / ".ae.lock", "a+"), time.time(), False
    while True:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            break
        except OSError:
            if not said:
                print("After Effects is busy with another pipeline; waiting for it", flush=True)
                said = True
            if time.time() - t0 > timeout:
                fh.close()
                sys.exit(f"After Effects stayed busy with another pipeline for over {timeout} s")
            time.sleep(2)
    _LOCK.update(depth=1, fh=fh)
    try:
        yield
    finally:
        _LOCK["depth"] = 0
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        fh.close()


def restart_ae(timeout=420):
    with ae_lock():
        _restart_ae(timeout)


def _restart_ae(timeout=420):
    """Force-restart AE (used when it has gone deaf to scripts), answering Crash Repair with its default Continue."""
    import ctypes
    subprocess.run(["taskkill", "/F", "/IM", "AfterFX.exe"], capture_output=True)
    time.sleep(3)
    _launch()
    t0 = time.time()
    while time.time() - t0 < timeout:
        if [t for t in ae_titles() if t.startswith("Adobe After Effects 20")]:
            _dismiss_warning()
            return
        for h, w, hh in _ae_dialogs():
            if w > hh:  # Crash Repair Options (wide; Enter = Continue). The splash's art window is wide too, so keep
                for msg in (0x100, 0x101):  # answering every poll: Enter on the splash is harmless
                    ctypes.windll.user32.PostMessageW(h, msg, 0x0D, 0)
        time.sleep(3)
    sys.exit("After Effects did not come back after a restart")


def wait_for(path, timeout, what):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if path.exists():
            size = path.stat().st_size
            time.sleep(1.5)
            if path.stat().st_size == size:
                return
        time.sleep(1)
    sys.exit(f"timed out after {timeout}s waiting for {what} ({path}). Check the app for a dialog.")


def run_job(code, path, target, timeout, retry=True):
    with ae_lock():
        return _run_job(code, path, target, timeout, retry)


def _run_job(code, path, target, timeout, retry=True):
    """Send a JSX job to AE and wait for `target` (the .aep it saves). AE sometimes goes deaf to `-r` (idle, no
    dialog, ignores scripts and even WM_CLOSE; seen after app.open, after its open .aep was deleted, after a
    template export), so if AE is up and nothing happens within 2 minutes, restart AE and send the job once more."""
    gui = lambda: [t for t in ae_titles() if t.startswith("Adobe After Effects 20")]
    before = gui()
    if not before:  # start AE the way that answers Crash Repair / the cache warning; a cold -r start answers nothing
        _restart_ae()
        before = gui()
    run_in_ae(code, path)
    if before:
        t0 = time.time()
        while time.time() - t0 < 120 and not Path(target).exists() and gui() == before:
            time.sleep(2)
        if not Path(target).exists() and gui() == before:
            if not retry:
                sys.exit("After Effects ignored the job twice, even after a restart.")
            print("After Effects ignored the job; restarting it and sending the job again")
            restart_ae()
            return run_job(code, path, target, timeout, retry=False)
    t0 = time.time()
    while time.time() - t0 < timeout and not Path(target).exists():
        if _script_error_dialog():  # "Unable to execute script at line N": the job is dead, fail now, not at the timeout
            sys.exit("After Effects script error (the job died; AE needs a restart before the next job)")
        time.sleep(1)
    wait_for(Path(target), 30, "After Effects")


def _script_error_dialog():
    """An untitled ~491x258 After Effects dialog is the 'Unable to execute script' error (Crash Repair is 521x328,
    the cache notice is titled 'Warning'). OK it and report True."""
    import ctypes
    u = ctypes.windll.user32
    for h, w, hh in _ae_dialogs():
        buf = ctypes.create_unicode_buffer(64)
        u.GetWindowTextW(h, buf, 64)
        if not buf.value and 470 <= w <= 515 and 240 <= hh <= 280:
            for msg in (0x100, 0x101):
                u.PostMessageW(h, msg, 0x0D, 0)
            return True
    return False


# "AB_ERROR|AB_WARN <text> ~" comp names inside a saved .aep (stored as UTF-8). <text> is printable ASCII ("~" too:
# ExtendScript writes home paths as "~/", 8.3 names carry "~1"; the lazy match still ends at the first " ~") or any
# well-formed UTF-8 multi-byte character: After Effects' own messages carry curly quotes ("Can not “set value”"),
# which an ASCII-only pattern dropped silently (an AB_ERROR then did not fail the build); stray binary does not match.
_AE_NOTE = re.compile(rb"AB_(ERROR|WARN) ((?:[ -~]|[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}"
                      rb"|[\xf0-\xf4][\x80-\xbf]{3}){1,250}?) ~")


def ae_notes(data):
    """The AB_ERROR / AB_WARN notes in .aep bytes, in order, once each: ["ERROR <text>", "WARN <text>", ...]."""
    return list(dict.fromkeys(f"{m.group(1).decode()} {m.group(2).decode('utf-8', 'replace')}"
                              for m in _AE_NOTE.finditer(data)))


def _pve_check(fn, data, *args):
    """PVE pre-flight (pve/pve.py): effect / property / transition names and value ranges against what the installed
    apps actually have. Advisory: returns issues, never blocks a build. args go to pve.<fn> after data."""
    try:
        # load pve/pve.py by path: a plain `import pve` returned the pve PACKAGE whenever a caller had already done
        # `from pve import pve`, so every pre-flight silently printed "unavailable" (2026-10-09)
        import importlib.util
        spec_ = importlib.util.spec_from_file_location("pve_checker", HERE / "pve" / "pve.py")
        pve = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(pve)
        return getattr(pve, fn)(data, *args)
    except Exception as e:  # noqa: BLE001
        return [f"pre-flight unavailable ({e})"]


def build(spec, base, render=False, premiere=True, ae=True):
    """ae=False: Premiere-only edit (e.g. when AE renders the VFX shots separately); AE still relays the job."""
    S = normalize(spec, base)
    pve_issues = _pve_check("check_spec", spec, tuple(a for a, on in (("AE", ae), ("PR", premiere)) if on))
    if not ae and S["texts"]:
        sys.exit("texts are After Effects comps; build with ae=True or render them as clips")
    out = outputs(S, base)
    S["render"] = render
    head = "var S = " + json.dumps(S, ensure_ascii=True) + ";\n"
    stem = Path(S["aep"]).stem
    warns = [w for w in S["pywarn"] if ae or not w.startswith("AE ")]  # AE-only notes are noise without AE
    warns += ["PVE " + w for w in pve_issues]
    result = {"warnings": warns}

    if ae:
        # Only an AE build replaces AE's open project, and another pipeline's half-built one also shows "*": judge
        # the unsaved marker once AE is ours (the lock is re-entrant, run_job takes it again).
        with ae_lock():
            if any(t.rstrip().endswith("*") for t in ae_titles()):
                sys.exit("After Effects has unsaved changes. Save or close that project, then run again.")
            run_job(head + (HERE / "ae_lib.jsx").read_text(encoding="ascii") + (HERE / "ae_build.jsx").read_text(encoding="ascii"),
                    out / f"{stem}_ae_job.jsx", S["aep"], 480)  # a cold AE start here takes 3-5 min
        # AE can't write files from scripts here, so it reports through comp names ending in " ~".
        notes = ae_notes(Path(S["aep"]).read_bytes())
        errs = [n for n in notes if n.startswith("ERROR")]
        if errs:
            sys.exit("After Effects " + errs[0])
        warns += ["AE " + n[5:] for n in notes]
        result["aep"] = S["aep"]
        if render:
            subprocess.run([str(AERENDER), "-project", S["aep"]], check=True, capture_output=True)
            result["ae_mp4"] = S["ae_mp4"]
    if premiere:
        warns += [f"PR clip {i + 1}: AE presets have no Premiere equivalent, skipped"
                  for i, c in enumerate(S["clips"]) if c["presets"]]

    if premiere:
        # BridgeTalk mangles backslashes in message bodies, so ship the script URL-encoded.
        body = quote(head + (HERE / "pr_build.jsx").read_text(encoding="ascii"), safe="")
        relay = ('if (!BridgeTalk.isRunning("premierepro")) BridgeTalk.launch("premierepro");\n'
                 'var bt = new BridgeTalk(); bt.target = "premierepro";\n'
                 "bt.body = 'eval(decodeURIComponent(\"%s\"))'; bt.send();\n" % body)
        res, started = Path(S["pr_result"]), Path(S["pr_result"] + ".start")
        # AE can go deaf to -r (after a template export, after its open .aep was deleted): if Premiere hasn't
        # started the job within a minute, restart AE and send it once more.
        ensure_premiere()
        with ae_lock():  # AE must be ours while it relays, or a busy AE looks deaf and gets restarted
            for attempt in range(2):
                if attempt:
                    print("Premiere did not pick up the job; restarting After Effects and sending it again", flush=True)
                    restart_ae()
                run_in_ae(relay, out / f"{stem}_pr_relay.jsx")
                t0 = time.time()
                while time.time() - t0 < 60 and not started.exists() and not res.exists():
                    time.sleep(1)
                if started.exists() or res.exists():
                    break
            else:
                sys.exit("Premiere never received the job from After Effects (BridgeTalk).")
        # A heavy edit renders at ~15x real time (a 6:07 documentary cut, 142 .mogrts + 20 DL comps: 90 min), so the wait
        # scales with the timeline; a fixed 30 min gave up on a render that then finished fine.
        length = max([c["start"] + c["dur"] for k in ("clips", "mgts", "aecomps") for c in S.get(k, [])] or [60])
        wait_for(res, max(1800, 25 * length) if render else max(600, 3 * length), "Premiere Pro")
        msg, *pw = res.read_text(encoding="utf-8").split("\n")
        if msg != "OK":
            sys.exit("Premiere error: " + msg)
        warns += ["PR " + w for w in pw]
        result["prproj"] = S["prproj"]
        if render:
            result["pr_mp4"] = S["pr_mp4"]
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("spec")
    ap.add_argument("--render", action="store_true", help="also render mp4s (aerender + Premiere export)")
    ap.add_argument("--no-pr", action="store_true", help="After Effects only")
    ap.add_argument("--no-ae", action="store_true", help="Premiere only (AE still relays the job)")
    a = ap.parse_args()
    spec_path = Path(a.spec).resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))  # utf-8-sig: Windows PowerShell 5.1 saves with a BOM
    res = build(spec, spec_path.parent, a.render, not a.no_pr, not a.no_ae)
    for w in res.pop("warnings"):
        print("WARNING", w)
    for k, v in res.items():
        print(f"{k:7} {v}")
