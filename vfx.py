"""AdobeBridge VFX: scene dicts -> one After Effects project of native comps -> rendered shot files.

    from vfx import render_scenes
    files = render_scenes(scenes, aep_path, out_dir)        # {scene name: rendered file}

Scenes are built by vfx_build.jsx (layer types documented there); every comp gets a render-queue item and
aerender renders the whole queue. The .aep stays the editable source: re-render it to replace the files.
"""
import json, subprocess, sys, time
from pathlib import Path

from adobe_bridge import AERENDER, HERE, ae_lock, ae_notes, ae_titles, run_job

W, H, FPS = 1920, 1080, 25


def _plays(f):
    """True when ffprobe can open the file (a render cut short has no moov atom and fails). True when ffprobe itself is
    missing: then the output cannot be judged and the launcher's exit code is not trusted (the lingering-worker case)."""
    try:
        return subprocess.run(["ffprobe", "-v", "error", "-i", str(f)], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return True


def _aerender(aep, files, t0, settle=20, timeout=None, grace=600):
    """Run aerender on a project and return its exit code. Its output goes to a log file, not a pipe: the render
    engine (AfterFX.com -noui) sometimes lingers after the last frame and would hold a pipe open forever. Once every
    expected file exists and has stopped growing for `settle` s, a still-running render is killed and counted done."""
    log = Path(aep).with_suffix(".render.log")
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        p = subprocess.Popen([str(AERENDER), "-project", str(aep)], stdout=fh, stderr=subprocess.STDOUT)
        sizes, stable_since, exited_at = None, None, None
        # The launcher can exit while a detached AfterFX.com worker keeps rendering (seen 2026-10-08 when a previous
        # worker was still alive): judge by the OUTPUT FILES, not by the launcher's exit, with a grace period after it.
        while True:
            now = [f.stat().st_size if f.exists() and f.stat().st_mtime >= t0 else -1 for f in files.values()]
            if -1 not in now and now == sizes:
                stable_since = stable_since or time.time()
                if time.time() - stable_since > settle:
                    rc = p.poll()  # an error exit with an output that does not decode is a failed render, not a linger
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
                    return rc if rc and not all(_plays(f) for f in files.values()) else 0
            else:
                stable_since = None
            sizes = now
            if timeout and time.time() - t0 > timeout:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
                return -1
            if p.poll() is not None:
                exited_at = exited_at or time.time()
                if time.time() - exited_at > grace and -1 in now:
                    return p.returncode if p.returncode else -1
            time.sleep(2)


def render_scenes(scenes, aep, out_dir, render=True, w=W, h=H, timeout=None, fps=FPS, gpu=False):
    """gpu=True: the project renders with GPU acceleration, gpu="cpu": software only, False: the app's default for new
    projects (Project.gpuAccelType; aerender follows the project)."""
    aep, out_dir = Path(aep).resolve(), Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    base, n = aep.stem, 1
    while aep.exists():  # never delete or overwrite: AE may have the previous one open (deleting it left AE deaf)
        n += 1
        aep = aep.with_name(f"{base}_v{n}.aep")
    from adobe_bridge import _pve_check
    for issue in _pve_check("check_scenes", scenes):  # names / ranges against the AE harvest, before AE sees them
        print("PVE", issue)
    V = {"w": w, "h": h, "fps": fps, "aep": aep.as_posix(), "out_dir": out_dir.as_posix(), "scenes": scenes, "gpu": gpu}
    # Another pipeline's half-built project also shows "*": judge the unsaved marker once AE is ours (re-entrant lock).
    with ae_lock():
        if any(t.rstrip().endswith("*") for t in ae_titles()):
            sys.exit("After Effects has unsaved changes. Save or close that project, then run again.")
        run_job("var V = " + json.dumps(V, ensure_ascii=True) + ";\n" + (HERE / "ae_lib.jsx").read_text(encoding="ascii")
                + (HERE / "vfx_build.jsx").read_text(encoding="ascii"), aep.with_name(aep.stem + "_job.jsx"), aep, 600)
    notes = ae_notes(aep.read_bytes())
    for n in notes:
        print(n)
    if any(n.startswith("ERROR") for n in notes):
        sys.exit("VFX build failed")
    files = {s["name"]: out_dir / (s["name"] + (".mov" if s.get("alpha") else ".mp4")) for s in scenes}
    if render:
        t0 = time.time()
        rc = _aerender(aep, files, t0, timeout=timeout)
        missing = [n for n, f in files.items() if not f.exists() or f.stat().st_mtime < t0]
        if rc or missing:
            log = aep.with_suffix(".render.log").read_text(encoding="utf-8", errors="replace")
            sys.exit(f"aerender failed (exit {rc}); not rendered: {missing}\n{log[-2000:]}")
        print(f"rendered {len(files)} shots in {time.time() - t0:.0f}s")
    return files
