"""Count flashes in a rendered clip from its per-frame luma (ffmpeg decodes, the counting is plain Python).

    python pve/flash_scan.py clip.mp4 [--ref control.mp4] [--fps N] [--start S] [--end S]   -> JSON on stdout

--fps defaults to the clip's own frame rate (ffprobe); the JSON says which rate it used. Exit 2 when nothing could be
measured (no frames in the window, a reference whose frame count differs by more than one, no frame rate).

A flash is a pair of opposing luma changes of at least DELTA levels (Harding / ITU-R BT.1702 count a flash as a
pair of opposing changes). The signal is the mean over the pixels that change at all: with a reference render
(the same plate without the effect) it is the mean of (clip - reference) over the pixels where the two ever differ
by DELTA, so the plate's own cuts and exposure flashes cancel and a flicker above and below the plate still swings;
without one it is the mean luma of the pixels whose luma ever swings by DELTA. `area` says how much of the frame that is (the Harding count applies from 25%). The same
count in every cell of a grid gives the worst local rate (a chase across letters averages out in the whole-area mean).
"""
import json, subprocess, sys
from fractions import Fraction

W, H = 192, 108
GRID = (24, 12)  # cells of 8 x 9 scan pixels: about one letter of a 75 px title at 960 x 540
DELTA = 25  # ponytail: ~10% of 8-bit luma code values, not cd/m2; a real PSE analyser converts to luminance first


def frames(video, start=None, end=None, w=W, h=H):
    """Every frame of video as a bytes row of w*h gray values (area-averaged downscale)."""
    cmd = ["ffmpeg", "-v", "error"] + (["-ss", f"{start:.3f}"] if start else []) + ["-i", str(video)]
    cmd += (["-t", f"{end - (start or 0):.3f}"] if end else []) + [
        "-vf", f"scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = w * h
    return [raw[i:i + n] for i in range(0, len(raw) - n + 1, n)]


def _masked(fr, ref, delta):
    """(frames, mask): frame - reference (signed: brighter and darker than the plate must not cancel) when there is a
    reference, and the pixels that ever change by delta."""
    if ref is not None:
        fr = [[a - b for a, b in zip(f, r)] for f, r in zip(fr, ref)]
        mask = [i for i in range(len(fr[0])) if max(abs(f[i]) for f in fr) >= delta] if fr else []
    else:
        mask = [i for i in range(len(fr[0])) if max(f[i] for f in fr) - min(f[i] for f in fr) >= delta] if fr else []
    return fr, mask


def signal(fr, ref=None, delta=DELTA):
    """(per-frame signal, area): see the module docstring. ref frames are matched by index (shorter list wins)."""
    fr, mask = _masked(fr, ref, delta)
    if not mask:
        return [0.0] * len(fr), 0.0
    return [sum(f[i] for i in mask) / len(mask) for f in fr], len(mask) / len(fr[0])


def cells(fr, ref=None, delta=DELTA, w=W, h=H, grid=GRID, least=4):
    """signal() inside each cell of a grid: {(cx, cy): (signal, share of the frame)} for every cell holding at least
    `least` changing pixels. A chase or a per-letter flicker cancels out in the whole-area mean; a cell sees it."""
    fr, mask = _masked(fr, ref, delta)
    by = {}
    for i in mask:
        by.setdefault(((i % w) * grid[0] // w, (i // w) * grid[1] // h), []).append(i)
    return {c: ([sum(f[i] for i in px) / len(px) for f in fr], len(px) / (w * h)) for c, px in by.items() if len(px) >= least}


def changes(sig, delta=DELTA):
    """Zig-zag of sig: [(frame, +1 | -1)], one entry per rise or fall of at least delta, at the frame it peaks or
    bottoms out. Smaller wiggles (compression noise, a gentle drift) never register."""
    if not sig:
        return []
    piv, d = [], 0
    lo = hi = (sig[0], 0)
    ext = None
    for i, v in enumerate(sig):
        if d == 0:
            lo, hi = min(lo, (v, i)), max(hi, (v, i), key=lambda x: x[0])
            if v - lo[0] >= delta:
                piv, d, ext = [lo], 1, (v, i)
            elif hi[0] - v >= delta:
                piv, d, ext = [hi], -1, (v, i)
        elif d * (v - ext[0]) > 0:  # still moving the same way: a new extreme
            ext = (v, i)
        elif d * (ext[0] - v) >= delta:  # turned by delta: the extreme was a pivot
            piv.append(ext)
            d, ext = -d, (v, i)
    if d:
        piv.append(ext)
    return [(b[1], 1 if b[0] > a[0] else -1) for a, b in zip(piv, piv[1:])]


def rate(ch, fps, n_frames):
    """{"flashes", "max_per_second", "mean_per_second"}: flashes = opposing-change pairs; the max counts the pairs
    among the changes inside any 1-second window."""
    ends = [i for i, _ in ch]
    win = max((sum(1 for e in ends if s <= e < s + fps) for s in range(max(1, n_frames - fps + 1))), default=0)
    secs = n_frames / fps if n_frames else 1
    return {"flashes": len(ch) // 2, "max_per_second": win // 2, "mean_per_second": round(len(ch) / 2 / secs, 2)}


def runs(sig, level):
    """[(first frame, length)] of the stretches where sig >= level (e.g. the white frames of a strobe)."""
    out, start = [], None
    for i, v in enumerate(list(sig) + [float("-inf")]):
        if v >= level and start is None:
            start = i
        elif v < level and start is not None:
            out.append((start, i - start))
            start = None
    return out


def local(fr, ref, fps, delta=DELTA):
    """The worst cell: {"local_max_per_second", "local_cell" [x, y] of the grid, "local_cell_area" share of the frame}."""
    best = {"local_max_per_second": 0, "local_cell": None, "local_cell_area": 0.0}
    for c, (sig, area) in sorted(cells(fr, ref, delta).items()):
        m = rate(changes(sig, delta), fps, len(sig))["max_per_second"]
        if m > best["local_max_per_second"]:
            best = {"local_max_per_second": m, "local_cell": list(c), "local_cell_area": round(area, 4)}
    return best


def scan(video, fps, ref=None, start=None, end=None, delta=DELTA):
    """Everything the card writer needs about one clip's flashing, from its own frames (and a control render)."""
    fr = frames(video, start, end)
    if not fr:
        raise ValueError("no frames decoded (check --start / --end)")
    rf = frames(ref, start, end) if ref else None
    if rf is not None and abs(len(rf) - len(fr)) > 1:  # one frame either way is normal between two exports
        raise ValueError(f"the reference has {len(rf)} frames, the clip {len(fr)}: render both over the same span")
    sig, area = signal(fr, rf, delta)
    ch = changes(sig, delta)
    return {"frames": len(fr), "frames_compared": len(sig), "fps": fps, "area": round(area, 3), "swing": round(max(sig) - min(sig), 1) if sig else 0,
            "changes": [[i, s] for i, s in ch], **rate(ch, fps, len(fr)), **local(fr, rf, fps, delta), "delta": delta,
            "signal": [round(v, 1) for v in sig]}


def clip_fps(video):
    """The first video stream's average frame rate (ffprobe), or None when it cannot be read."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate",
                            "-of", "default=nw=1:nk=1", str(video)], capture_output=True, text=True, check=True)
        f = float(Fraction(r.stdout.strip().splitlines()[0]))
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError, ZeroDivisionError):
        return None
    return f if f > 0 else None


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    ap.add_argument("--ref")
    ap.add_argument("--fps", type=float, help="frame rate to count with (default: the clip's own, from ffprobe)")
    ap.add_argument("--start", type=float)
    ap.add_argument("--end", type=float)
    a = ap.parse_args(argv)
    fps = a.fps or clip_fps(a.video)
    if not fps:
        print("flash_scan: cannot read the clip's frame rate (is ffprobe installed?): pass --fps", file=sys.stderr)
        return 2
    try:
        res = scan(a.video, round(fps), a.ref, a.start, a.end)
    except ValueError as e:
        print(f"flash_scan: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
