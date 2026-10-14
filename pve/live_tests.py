"""PVE live tests that need a second track, a real edit, audio measurement or other footage (live wave 3).

    python pve/live_tests.py two_track   Premiere Track Matte Key (is 'Matte:' 0- or 1-based?), Morph Cut on a synthetic
                                         jump cut, Film Impact Camera Shake mode by mode (frame-to-frame jitter), and
                                         After Effects depth of field per 3D renderer -> work/pve_live/wave3/two_track.json
    python pve/live_tests.py audio       every Premiere audio effect on a stereo clip (parameter dump with ranges ->
                                         pve/harvest/pr_audio_params_w3.json), then 3-4 settings per effect through test
                                         signals, exported and measured -> work/pve_live/wave3/audio_map.json
    python pve/live_tests.py footage     the cards the critic marked untested:no_suitable_footage, re-rendered on the
                                         second test-footage set (make_test_media.py, pve/media/set2/) in their apps
                                         -> work/pve_live/wave3/footage_rerender.json, frames in pve/gallery/set2/

Measurements only (no verdicts). Renders and frames go under pve/gallery/ (not tracked); reports carry repo-relative
paths. Every measuring helper here is plain Python (wave, array, cmath) and has an offline test.
"""
import cmath, json, math, subprocess, sys, time, wave
from array import array
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import adobe_bridge as ab  # noqa: E402
import gallery as G  # noqa: E402

LIVE = HERE.parent / "work" / "pve_live" / "wave3"
SET2 = HERE / "media" / "set2"
FPS = 25
WAV_EPR = ab.ADOBE / "Adobe Media Encoder 2026/MediaIO/systempresets/3F3F3F3F_57415645/Waveform Audio 48kHz 16-bit.epr"


# ---------------- measuring helpers (offline-tested) ----------------
def luma_frames(video, t0, t1, w=192, h=108):
    """Gray frames (bytes) of video between t0 and t1 at w x h (area-averaged)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-i", str(video), "-t", f"{t1 - t0:.3f}", "-vf",
                          f"scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"], capture_output=True,
                         check=True).stdout
    n = w * h
    return [raw[i:i + n] for i in range(0, len(raw) - n + 1, n)]


def profile(f, w, h, axis):
    """Mean of each column (axis 'x') or row ('y') of a w x h gray frame."""
    if axis == "x":
        return [sum(f[r * w + c] for r in range(h)) / h for c in range(w)]
    return [sum(f[r * w:(r + 1) * w]) / w for r in range(h)]


def shift(a, b, lim):
    """Sub-sample d (|d| <= lim) where b[i] ~ a[i + d]: integer best match, refined by a parabola through its errors."""
    errs = {}
    for d in range(-lim, lim + 1):
        idx = range(max(0, -d), min(len(b), len(a) - d))
        if len(idx) >= len(b) // 2:
            errs[d] = sum(abs(b[i] - a[i + d]) for i in idx) / len(idx)
    d = min(errs, key=errs.get)
    if d - 1 in errs and d + 1 in errs:
        e0, e1, e2 = errs[d - 1], errs[d], errs[d + 1]
        den = e0 - 2 * e1 + e2
        if den > 0:
            return d + 0.5 * (e0 - e2) / den, errs[d]
    return float(d), errs[d]


def jitter(frames, w, h, lim=24):
    """Frame-to-frame motion of a picture: per pair the (dx, dy) shift of the column / row profiles (px at w x h),
    their RMS, the RMS of the second difference (jerk: shake, not a smooth drift) and the mean match error."""
    px = [profile(f, w, h, "x") for f in frames]
    py = [profile(f, w, h, "y") for f in frames]
    dx = [shift(a, b, lim) for a, b in zip(px, px[1:])]
    dy = [shift(a, b, lim) for a, b in zip(py, py[1:])]
    sx, sy = [d for d, _ in dx], [d for d, _ in dy]
    rms = lambda xs: math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else 0.0  # noqa: E731
    jerk = lambda xs: [b - a for a, b in zip(xs, xs[1:])]  # noqa: E731
    return {"pairs": len(sx), "dx": [round(x, 2) for x in sx], "dy": [round(y, 2) for y in sy],
            "rms_px": round(math.hypot(rms(sx), rms(sy)), 3), "jerk_rms_px": round(math.hypot(rms(jerk(sx)), rms(jerk(sy))), 3),
            "max_px": round(max([abs(x) for x in sx + sy] or [0]), 2),
            "match_error": round(sum(e for _, e in dx + dy) / max(1, len(dx) + len(dy)), 3)}


def region_mean_diff(f, ref, w, box):
    """Mean |f - ref| over box (x0, y0, x1, y1) of two w-wide gray frames."""
    x0, y0, x1, y1 = box
    s = n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            s += abs(f[y * w + x] - ref[y * w + x])
            n += 1
    return s / max(1, n)


def which_layer(f, fg, bg, w, box):
    """'fg' / 'bg' / 'mix' for a region of a composite: which reference it is clearly nearer (half the distance)."""
    a, b = region_mean_diff(f, fg, w, box), region_mean_diff(f, bg, w, box)
    return "fg" if a <= b / 2 else "bg" if b <= a / 2 else "mix"


def sharpness(f, w, box):
    """Mean |horizontal + vertical gradient| inside box of a gray frame: drops when a region is blurred."""
    x0, y0, x1, y1 = box
    s = n = 0
    for y in range(y0, y1 - 1):
        for x in range(x0, x1 - 1):
            p = f[y * w + x]
            s += abs(f[y * w + x + 1] - p) + abs(f[(y + 1) * w + x] - p)
            n += 1
    return round(s / max(1, n), 3)


def read_wav(path):
    """(rate, [channel arrays of floats -1..1]) of a 16-bit PCM WAV."""
    with wave.open(str(path), "rb") as wf:
        rate, ch, n = wf.getframerate(), wf.getnchannels(), wf.getnframes()
        a = array("h")
        a.frombytes(wf.readframes(n))
    return rate, [[x / 32768 for x in a[c::ch]] for c in range(ch)]


def db(x, floor=-150.0):
    return round(20 * math.log10(x), 2) if x > 0 else floor


def rms_db(x):
    return db(math.sqrt(sum(v * v for v in x) / len(x))) if x else -150.0


def peak_db(x):
    return db(max((abs(v) for v in x), default=0))


def fft(x):
    """Iterative radix-2 FFT of a list whose length is a power of two."""
    n = len(x)
    a = [complex(v) for v in x]
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    size = 2
    while size <= n:
        wn = cmath.exp(-2j * math.pi / size)
        half = size // 2
        tw = [wn ** k for k in range(half)]
        for start in range(0, n, size):
            for k in range(half):
                u, v = a[start + k], a[start + k + half] * tw[k]
                a[start + k], a[start + k + half] = u + v, u - v
        size *= 2
    return a


THIRD = [round(1000 * 2 ** (k / 3), 1) for k in range(-16, 14)]  # 25 Hz .. 20 kHz third-octave centres


def bands(x, rate, n=4096, frames=6):
    """Power (dB) in each third-octave band, Hann-windowed FFTs of `frames` frames spread over x, averaged."""
    hop = max(1, (len(x) - n) // max(1, frames - 1))
    win = [0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1)) for i in range(n)]
    pw = [0.0] * (n // 2)
    used = 0
    for s in range(0, len(x) - n + 1, hop):
        X = fft([x[s + i] * win[i] for i in range(n)])
        for k in range(n // 2):
            pw[k] += abs(X[k]) ** 2
        used += 1
        if used == frames:
            break
    out = []
    for fc in THIRD:
        lo, hi = fc / 2 ** (1 / 6), fc * 2 ** (1 / 6)
        ks = range(max(1, int(lo * n / rate)), min(n // 2, int(math.ceil(hi * n / rate)) + 1))
        p = sum(pw[k] for k in ks) / max(1, used)
        out.append(db(math.sqrt(p), -200.0) if p > 0 else -200.0)
    return out


def transfer(out_bands, dry_bands):
    return [round(a - b, 2) for a, b in zip(out_bands, dry_bands)]


def cutoff(tf, kind):
    """-3 dB point (Hz, log-interpolated) of a high- or low-pass transfer: where it crosses its passband level - 3."""
    ref = max(tf)
    lvl = ref - 3
    idx = range(len(tf)) if kind == "highpass" else range(len(tf) - 1, -1, -1)
    prev = None
    for i in idx:
        if tf[i] >= lvl:
            if prev is None:
                return THIRD[i]
            a, b = tf[prev], tf[i]
            fr = (lvl - a) / (b - a) if b != a else 0
            return round(math.exp(math.log(THIRD[prev]) + fr * (math.log(THIRD[i]) - math.log(THIRD[prev]))), 1)
        prev = i
    return None


def band_at(tf, f):
    """The transfer value of the third-octave band nearest f Hz."""
    return tf[min(range(len(THIRD)), key=lambda i: abs(math.log(THIRD[i] / f)))]


def extreme(tf, kind):
    """(Hz, dB) of the largest boost ('peak') or deepest cut ('notch') in a transfer."""
    i = max(range(len(tf)), key=lambda k: tf[k]) if kind == "peak" else min(range(len(tf)), key=lambda k: tf[k])
    return THIRD[i], tf[i]


def envelope(x, rate, win=0.005):
    n = max(1, int(win * rate))
    return [math.sqrt(sum(v * v for v in x[i:i + n]) / n) for i in range(0, len(x) - n + 1, n)]


def echoes(x, rate, floor_db=-40, win=0.005, gap=0.02):
    """Impulse response reading: (s after the direct sound, dB re the direct) of each envelope peak above floor_db
    that is at least `gap` s after the previous one; and the time the envelope takes to fall 30 dB below its peak."""
    env = envelope(x, rate, win)
    if not env or max(env) <= 0:
        return {"direct_s": None, "echoes": [], "t30_s": None}
    top = max(env)
    i0 = env.index(top)
    peaks, last = [], i0
    for i in range(i0 + 1, len(env) - 1):
        v = env[i]
        if v > 0 and 20 * math.log10(v / top) >= floor_db and v >= env[i - 1] and v >= env[i + 1] and \
                (i - last) * win >= gap:
            peaks.append([round((i - i0) * win, 3), round(20 * math.log10(v / top), 1)])
            last = i
    below = next((i for i in range(i0, len(env)) if env[i] < top * 10 ** (-30 / 20) and
                  all(e < top * 10 ** (-30 / 20) for e in env[i:i + 4])), None)
    return {"direct_s": round(i0 * win, 3), "echoes": peaks[:8], "t30_s": round((below - i0) * win, 3) if below else None}


def tone_freq(x, rate):
    """Frequency (Hz) of a steady tone from its rising zero crossings, interpolated."""
    zs = [i + x[i] / (x[i] - x[i + 1]) for i in range(len(x) - 1) if x[i] <= 0 < x[i + 1]]
    return round((len(zs) - 1) * rate / (zs[-1] - zs[0]), 2) if len(zs) > 2 else None


def goertzel(x, rate, f):
    w = 2 * math.pi * f / rate
    c, s1, s2 = 2 * math.cos(w), 0.0, 0.0
    for v in x:
        s1, s2 = v + c * s1 - s2, s1
    return math.sqrt(max(0.0, s1 * s1 + s2 * s2 - c * s1 * s2)) / len(x)


def thd(x, rate, f0, n=5):
    """Harmonic distortion (%): power of harmonics 2..n over the fundamental's."""
    h1 = goertzel(x, rate, f0)
    hs = math.sqrt(sum(goertzel(x, rate, k * f0) ** 2 for k in range(2, n + 1)))
    return round(100 * hs / h1, 3) if h1 > 0 else None


def corr(a, b):
    n = min(len(a), len(b))
    sa = math.sqrt(sum(v * v for v in a[:n])) * math.sqrt(sum(v * v for v in b[:n]))
    return round(sum(a[i] * b[i] for i in range(n)) / sa, 4) if sa else None


def stereo(out, dry):
    """L/R levels of a stereo result and where each output channel came from: its correlation with the dry L and R."""
    (L, R), (dL, dR) = out, dry
    return {"L_db": rms_db(L), "R_db": rms_db(R), "L_vs_dryL": corr(L, dL), "L_vs_dryR": corr(L, dR),
            "R_vs_dryL": corr(R, dL), "R_vs_dryR": corr(R, dR), "L_vs_R": corr(L, R)}


def fit_line(xs, ys):
    """Least squares y = a x + b: (a, b, max |residual|) or None for fewer than 2 points."""
    pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
    if len(pts) < 2:
        return None
    n = len(pts)
    mx, my = sum(x for x, _ in pts) / n, sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    if not sxx:
        return None
    a = sum((x - mx) * (y - my) for x, y in pts) / sxx
    b = my - a * mx
    return round(a, 4), round(b, 4), round(max(abs(y - (a * x + b)) for x, y in pts), 3)


def blockiness(f, w, h, period):
    """Mean |step| across the `period`-px block grid over the mean |step| between grid lines (1 = no blocks)."""
    on = off = n_on = n_off = 0
    for y in range(h):
        row = f[y * w:(y + 1) * w]
        for x in range(1, w):
            d = abs(row[x] - row[x - 1])
            if x % period == 0:
                on, n_on = on + d, n_on + 1
            else:
                off, n_off = off + d, n_off + 1
    return round((on / max(1, n_on)) / max(1e-6, off / max(1, n_off)), 3)


def skew(f, w, h, band=0.2, lim=40):
    """Horizontal offset (px) of the bottom band's column profile against the top band's: a rolling-shutter shear."""
    k = max(1, int(h * band))
    top = [sum(f[r * w + c] for r in range(k)) / k for c in range(w)]
    bot = [sum(f[r * w + c] for r in range(h - k, h)) / k for c in range(w)]
    return round(shift(top, bot, lim)[0], 2)


def specks(f, w, h, delta=60):
    """Single-pixel specks: pixels with at most one of their 8 neighbours within `delta` levels of them (a line or an
    edge always has a few like neighbours, an isolated speck none)."""
    n = 0
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            p = f[y * w + x]
            like = sum(1 for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dx or dy) and abs(f[(y + dy) * w + x + dx] - p) <= delta)
            if like <= 1:
                n += 1
    return n


def wire_contrast(f, w, h, x0=700, x1=760, side=8):
    """Mean luma 8 px either side of the dust_wire plate's line (x0 at the top, x1 at the bottom) minus on it."""
    on = off = n = 0
    for y in range(0, h, 4):
        x = round(x0 + (x1 - x0) * y / h)
        on += f[y * w + x]
        off += (f[y * w + x - side] + f[y * w + x + side]) / 2
        n += 1
    return round((off - on) / n, 2)


def combing(f, w, h):
    """Mean |line - average of its neighbours| over mean |vertical step two lines apart|: high when fields disagree."""
    a = b = 0
    for y in range(2, h - 2):
        for x in range(0, w, 2):
            p = f[y * w + x]
            a += abs(p - (f[(y - 1) * w + x] + f[(y + 1) * w + x]) / 2)
            b += abs(f[(y + 2) * w + x] - p) / 2
    return round(a / max(1e-6, b), 3)


# ---------------- Premiere relay ----------------
def pr_run(code, tag, timeout=3600):
    """Run a JSX job in Premiere (relayed by After Effects over BridgeTalk, URL-encoded). The job writes JSON to
    PV.result (and PV.result + '.start' when it begins); returns the parsed result."""
    LIVE.mkdir(parents=True, exist_ok=True)
    res = LIVE / f"{tag}_result.json"
    start = Path(str(res) + ".start")
    for p in (res, start):
        p.unlink(missing_ok=True)
    relay = ('if (!BridgeTalk.isRunning("premierepro")) BridgeTalk.launch("premierepro");\n'
             'var bt = new BridgeTalk(); bt.target = "premierepro";\n'
             "bt.body = 'eval(decodeURIComponent(\"%s\"))'; bt.send();\n" % quote(code, safe=""))
    ab.ensure_premiere()
    with ab.ae_lock():
        for attempt in range(2):
            if attempt:
                ab.restart_ae()
            ab.run_in_ae(relay, LIVE / f"_{tag}_relay.jsx")
            t0 = time.time()
            while time.time() - t0 < 90 and not start.exists() and not res.exists():
                time.sleep(1)
            if start.exists() or res.exists():
                break
        else:
            sys.exit(f"Premiere never received the {tag} job")
    t0 = time.time()
    while not res.exists():
        if time.time() - t0 > timeout:
            sys.exit(f"Premiere {tag} job: no result after {timeout} s")
        time.sleep(2)
    time.sleep(1)
    return json.loads(res.read_text(encoding="utf-8"))


def still(path, lavfi):
    """A still made once (Premiere locks the media of its open projects, so never rewrite one)."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", lavfi, "-frames:v", "1", str(path)], check=True)
    return path


def rel(p):
    return G.rel(p)


def report(name, data):
    p = LIVE / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote", rel(p), flush=True)


# ---------------- task: two-track tests ----------------
TMK_BOXES = {"left_disc": (40, 40, 64, 68), "right_disc": (128, 40, 152, 68), "corner": (4, 4, 24, 20)}  # 192x108


def tmk_reading(regions):
    """Which matte a Track Matte Key segment used, from where the foreground shows: left disc only = the V1 matte,
    right disc only = V2, everywhere = no matte (or an opaque one read by alpha), nowhere = an empty matte."""
    l, r, c = regions["left_disc"], regions["right_disc"], regions["corner"]
    if l == "fg" and r == "bg" and c == "bg":
        return "V1 matte (left disc)"
    if r == "fg" and l == "bg" and c == "bg":
        return "V2 matte (right disc)"
    if l == r == c == "fg":
        return "foreground everywhere (no matte, or the matte is opaque)"
    if l == r == c == "bg":
        return "foreground nowhere (empty matte)"
    return "other: " + json.dumps(regions)


def track_matte(out):
    """Premiere: V1 left-disc matte, V2 right-disc matte (white on black, opaque), V3 background (test_b) hiding both,
    V4 foreground (test_a) with Track Matte Key; one 2 s segment per 'Matte:' value, Composite Using 1 (and 0 once)."""
    ml = still(out / "matte_left.png", "color=c=black:s=1920x1080,format=gray,geq=lum='255*lt(hypot(X-500\\,Y-540)\\,260)'")
    mr = still(out / "matte_right.png", "color=c=black:s=1920x1080,format=gray,geq=lum='255*lt(hypot(X-1420\\,Y-540)\\,260)'")
    a, b = G.MEDIA / "test_a.mp4", G.MEDIA / "test_b.mp4"
    segs = [("fg_only", None), ("bg_only", None)] + [(f"matte_{v}", {"Matte:": v, "Composite Using:": 1}) for v in range(5)]
    segs += [("matte_0_alpha", {"Matte:": 0, "Composite Using:": 0}), ("matte_1_alpha", {"Matte:": 1, "Composite Using:": 0}),
             ("default", {})]
    clips = []
    for i, (name, props) in enumerate(segs):
        t = i * 2.0
        clips += [{"src": str(ml), "start": t, "dur": 2.0, "track": 1}, {"src": str(mr), "start": t, "dur": 2.0, "track": 2}]
        if name != "fg_only":
            clips.append({"src": str(b), "in": 1.0, "start": t, "dur": 2.0, "track": 3})
        if name != "bg_only":
            clips.append({"src": str(a), "in": 1.0, "start": t, "dur": 2.0, "track": 4, **(
                {"effects": [{"name": "Track Matte Key", "pr_props": props}]} if props is not None else {}),
                **({"dump": True} if name == "default" else {})})
    G.ae_clean()
    res = ab.build({"name": "pve_w3_trackmatte", "width": 1920, "height": 1080, "fps": FPS, "clips": clips,
                    "out_dir": str(out / "builds")}, out, render=True, premiere=True, ae=False)
    fr = {name: luma_frames(res["pr_mp4"], i * 2.0 + 1.0, i * 2.0 + 1.04)[0] for i, (name, _) in enumerate(segs)}
    rows = {}
    for i, (name, props) in enumerate(segs):
        if name in ("fg_only", "bg_only"):
            continue
        reg = {k: which_layer(fr[name], fr["fg_only"], fr["bg_only"], 192, box) for k, box in TMK_BOXES.items()}
        png = out / f"tmk_{name}.png"
        G.frame(res["pr_mp4"], i * 2.0 + 1.0, png)
        rows[name] = {"set": props, "regions": reg, "reading": tmk_reading(reg), "frame": rel(png),
                      "seq_s": [i * 2.0, i * 2.0 + 2.0]}
    dump = [w for w in res["warnings"] if "Track Matte Key >" in w]
    m0, m1 = rows["matte_0"]["reading"], rows["matte_1"]["reading"]
    answer = ("0-based: 'Matte:' 0 = Video 1" if m0.startswith("V1") else
              "1-based: 'Matte:' 1 = Video 1" if m1.startswith("V1") else "undetermined (see segments)")
    return {"setup": "V1 white disc left (x 500) on black, V2 white disc right (x 1420), both opaque stills; V3 test_b "
                     "(hides V1/V2); V4 test_a with Track Matte Key. Frame 1.0 s into each 2 s segment, regions on a "
                     "192x108 luma frame compared with the fg-only and bg-only segments",
            "answer": answer, "segments": rows, "default_dump": dump,
            "warnings": [w for w in res["warnings"] if "Track Matte" in w and ">" not in w],
            "render": rel(res["pr_mp4"])}


def camera_shake(out):
    """Film Impact Camera Shake on a static still, Camera Mode 0..4 at defaults, then each mode with every other
    mode's Strafe / Stride / Roll triplet zeroed (which triplet a mode reads); frame-to-frame jitter per segment."""
    st = still(out / "shake_still.png", "testsrc2=size=1920x1080,drawgrid=w=96:h=96:t=2:c=white@0.5")
    segs = [("control", None)] + [(f"mode_{m}", {"Camera Mode": m}) for m in range(5)]
    for m in range(5):  # keep only triplet m
        p = {"Camera Mode": m}
        for k in range(5):
            if k != m:
                sfx = f"#{k + 1}" if k else ""
                p.update({f"Strafe{sfx}": 0, f"Stride{sfx}": 0, f"Roll{sfx}": 0})
        segs.append((f"mode_{m}_only_triplet_{m + 1}", p))
    for m in range(5):  # the converse: only triplet m zeroed
        sfx = f"#{m + 1}" if m else ""
        segs.append((f"mode_{m}_triplet_{m + 1}_zeroed", {"Camera Mode": m, f"Strafe{sfx}": 0, f"Stride{sfx}": 0,
                                                            f"Roll{sfx}": 0}))
    clips = [{"src": str(st), "start": i * 2.0, "dur": 2.0, "track": 1,
              **({"effects": [{"name": "Camera Shake", "pr_props": p}]} if p is not None else {})}
             for i, (_, p) in enumerate(segs)]
    G.ae_clean()
    res = ab.build({"name": "pve_w3_camshake", "width": 1920, "height": 1080, "fps": FPS, "clips": clips,
                    "out_dir": str(out / "builds")}, out, render=True, premiere=True, ae=False)
    rows = {}
    for i, (name, p) in enumerate(segs):
        fr = luma_frames(res["pr_mp4"], i * 2.0 + 0.2, i * 2.0 + 1.84, 192, 108)
        j = jitter(fr, 192, 108, lim=20)
        rows[name] = {"set": p, "frames_measured": len(fr), **{k: j[k] for k in ("rms_px", "jerk_rms_px", "max_px",
                                                                                    "match_error", "pairs")},
                      "dx_192": j["dx"], "dy_192": j["dy"]}
        png = out / f"shake_{name}.png"
        G.frame(res["pr_mp4"], i * 2.0 + 1.0, png)
        rows[name]["frame"] = rel(png)
    return {"setup": "a static 1920x1080 test still per 2 s segment, Camera Shake at its defaults (Master 100) per Camera "
                     "Mode, each mode with the other four Strafe / Stride / Roll triplets at 0, and each mode with only "
                     "its own triplet (k = mode + 1) at 0. Jitter: shift of the "
                     "column / row luma profiles between consecutive frames at 192x108 (x10 for 1920 px), 0.2-1.84 s",
            "scale_note": "px values are at 192x108: multiply by 10 for 1920x1080", "segments": rows,
            "warnings": [w for w in res["warnings"] if "Camera Shake" in w], "render": rel(res["pr_mp4"])}


MORPH_CUTS = (("morph_cut_face", 2.0), ("hard_cut_face", 8.0), ("morph_cut_plate", 14.0))


def banner(rgb, w, h):
    """Share of a frame's middle band (rows 41-45 % of the height) in the red 'Analyzing in background' stripes."""
    y0, y1 = int(0.41 * h), int(0.45 * h)
    px = [(rgb[i], rgb[i + 1], rgb[i + 2]) for y in range(y0, y1) for i in range(y * w * 3, (y + 1) * w * 3, 3)]
    return round(sum(1 for r, g, b in px if r > 150 and r > g + 50 and r > b + 50) / max(1, len(px)), 3)


def morph_export(out, mp4, tag):
    """Export the active Premiere sequence to mp4; per cut: per-frame change, the analysis banner, frames."""
    pv = {"result": ab.fwd(LIVE / f"{tag}_result.json"), "out": str(mp4), "epr": ab.fwd(ab.EPR)}
    got = pr_run("var PV = " + json.dumps(pv) + ";" + EXPORT_JSX, tag)
    rows = {}
    for name, cut in MORPH_CUTS:
        fr = luma_frames(mp4, cut - 0.6, cut + 0.6)
        diffs = [round(sum(abs(a - b) for a, b in zip(f, g)) / len(f), 2) for f, g in zip(fr, fr[1:])]
        rows[name] = {"cut_s": cut, "per_frame_change": diffs, "max_change": max(diffs),
                      "frames_over_2": sum(1 for d in diffs if d > 2), "frames": [],
                      "banner_share_at_cut": banner(G.rgb_at(mp4, cut, 384, 216), 384, 216)}
        for dt in (-0.3, -0.12, 0.0, 0.12, 0.3):
            png = out / f"morph_{mp4.stem}_{name}_{dt:+.2f}.png"
            G.frame(mp4, cut + dt, png)
            rows[name]["frames"].append(rel(png))
    return {"export": got, "render": rel(mp4) if mp4.exists() else None, "cuts": rows}


def morph_cut(out, wait=150, tries=4):
    """A jump cut (talking_head.mp4 0-2 s, then from 3.2 s) with Morph Cut, the same cut hard, and a non-face jump
    (locked_off_motion) with Morph Cut. Built without a render; Premiere analyses Morph Cuts in the background and an
    export before that ends carries its 'Analyzing in background' banner, so export every `wait` s until no cut shows
    the banner (or `tries` exports)."""
    th, lo = SET2 / "talking_head.mp4", SET2 / "locked_off_motion.mp4"
    clips = [{"src": str(th), "in": 0, "start": 0, "dur": 2.0}, {"src": str(th), "in": 3.2, "start": 2.0, "dur": 2.0,
                                                                   "transition": {"name": "Morph Cut", "dur": 0.6}},
             {"src": str(th), "in": 0, "start": 6.0, "dur": 2.0}, {"src": str(th), "in": 3.2, "start": 8.0, "dur": 2.0},
             {"src": str(lo), "in": 0, "start": 12.0, "dur": 2.0},
             {"src": str(lo), "in": 2.4, "start": 14.0, "dur": 2.0, "transition": {"name": "Morph Cut", "dur": 0.6}}]
    for c in clips:
        c["track"] = 1
    G.ae_clean()
    res = ab.build({"name": "pve_w3_morphcut", "width": 1920, "height": 1080, "fps": FPS, "clips": clips,
                    "out_dir": str(out / "builds")}, out, render=False, premiere=True, ae=False)
    stem, t0, exports = Path(res["prproj"]).stem, time.time(), []
    for k in range(tries):
        print(f"Morph Cut: waiting {wait} s for Premiere's background analysis", flush=True)
        time.sleep(wait)
        e = morph_export(out, Path(res["prproj"]).with_name(f"{stem}_pr_{k + 1}.mp4"), f"morphcut_export_{k + 1}")
        e["seconds_after_build"] = round(time.time() - t0)
        exports.append(e)
        if all(c["banner_share_at_cut"] < 0.02 for c in e["cuts"].values()):
            break
    return {"setup": "talking_head.mp4 0-2 s then 3.2-5.2 s (a 1.2 s jump), Morph Cut 0.6 s centred, the same cut hard at "
                     "8 s, and a locked-off plate jump (2.4 s) with Morph Cut at 14 s; built, then exported every "
                     f"{wait} s until no cut shows the analysis banner. per_frame_change = mean |luma diff| between "
                     "consecutive 192x108 frames from 0.6 s before to 0.6 s after each cut; banner_share_at_cut = share of "
                     "the middle band in the banner's red stripes", "exports": exports, "final": exports[-1],
            "warnings": res["warnings"]}


EXPORT_JSX = """(function () {
  function w(s) { var f = new File(PV.result); f.encoding = "UTF-8"; f.open("w"); f.write(s); f.close(); }
  var st = new File(PV.result + ".start"); st.open("w"); st.write("1"); st.close();
  try {
    var q = app.project.activeSequence;
    var r = q.exportAsMediaDirect(new File(PV.out).fsName, new File(PV.epr).fsName, app.encoder.ENCODE_ENTIRE);
    w('{"ok": ' + (new File(PV.out).exists ? "true" : "false") + ', "sequence": "' + String(q.name).replace(/"/g, "") +
      '", "returned": "' + String(r).replace(/"/g, "") + '"}');
  } catch (e) { w('{"ok": false, "error": "' + String(e).replace(/"/g, "") + '"}'); }
})();
"""

DOF_JSX = r"""
(function () {
  var proj = app.newProject(), out = [];
  if (V.gpu) {  // the first non-software engine (the project keeps it; aerender follows the project)
    var ga = app.availableGPUAccelTypes;
    for (var gi = 0; gi < ga.length; gi++) if (ga[gi] != GpuAccelType.SOFTWARE) { proj.gpuAccelType = ga[gi]; break; }
    warn("GPU acceleration " + (proj.gpuAccelType == GpuAccelType.SOFTWARE ? "not available" : "on"));
  }
  function L(comp, item, x, z) {
    var l = comp.layers.add(item); l.threeDLayer = true;
    l.property("ADBE Transform Group").property("ADBE Position").setValue([x, 540, z]);
    l.property("ADBE Transform Group").property("ADBE Scale").setValue([22, 22, 22]);
    return l;
  }
  var item = proj.importFile(new ImportOptions(new File(V.still)));
  var rs = [];
  for (var s = 0; s < V.scenes.length; s++) {
    var S = V.scenes[s], comp = proj.items.addComp(S.name, 1920, 1080, 1, 1, 25);
    try {
    var avail = comp.renderers, pick = null;
    for (var r = 0; r < avail.length; r++) { rs.push(avail[r]); if (avail[r].toLowerCase().indexOf(S.want) >= 0) pick = avail[r]; }
    if (!pick) warn(S.name + ": no renderer like " + S.want + " (" + avail.join(", ") + ")");
    else try { comp.renderer = pick; } catch (re) { warn(S.name + ": renderer " + pick + " refused: " + String(re)); }
    L(comp, item, 576, -900); L(comp, item, 960, 0); L(comp, item, 2084, 2500);
    var cam = comp.layers.addCamera("CAM", [960, 540]);
    var co = cam.property("ADBE Camera Options Group");
    var zoom = co.property("ADBE Camera Zoom").value;
    cam.property("ADBE Transform Group").property("ADBE Position").setValue([960, 540, -zoom]);
    var cs = [["ADBE Camera Depth of Field", S.dof ? 1 : 0], ["ADBE Camera Focus Distance", zoom],
              ["ADBE Camera Aperture", 300], ["ADBE Camera Blur Level", 100]], hidden = [];
    for (var c2 = 0; c2 < cs.length; c2++) {  // a renderer hides the camera options it does not use
      try { co.property(cs[c2][0]).setValue(cs[c2][1]); } catch (ce) { hidden.push(cs[c2][0]); }
    }
    if (hidden.length) warn(S.name + ": not settable (hidden): " + hidden.join(", "));
    warn(S.name + ": renderer now " + comp.renderer + ", zoom " + Math.round(zoom) + ", AE " + app.version);
    var rq = proj.renderQueue.items.add(comp), om = rq.outputModule(1);
    rq.applyTemplate("Best Settings");
    om.applyTemplate("H.264 - Match Render Settings - 40 Mbps");
    om.file = new File(V.out_dir + "/" + S.name + ".mp4");
    } catch (se) { warn(S.name + " failed: " + String(se).replace(/\s+/g, " ") + " line " + se.line); }
  }
  warn("renderers offered: " + rs.slice(0, 3).join(", "));
  flushWarns(proj);
  proj.save(new File(V.aep));
})();
"""


def dof_project(out, tag, want, gpu):
    """One After Effects project: DOF off / on comps on the renderer whose name contains `want`; rendered with
    aerender; sharpness per still copy."""
    from vfx import _aerender
    st = still(out / "dof_still.png", "testsrc2=size=1920x1080,drawgrid=w=48:h=48:t=2:c=white@0.6")
    scenes = [{"name": f"DOF_{tag}_{'on' if d else 'off'}", "want": want, "dof": d} for d in (False, True)]
    aep = out / f"dof_{tag}_{int(time.time())}.aep"
    V = {"still": ab.fwd(st), "scenes": scenes, "out_dir": ab.fwd(out / "renders"), "aep": ab.fwd(aep), "gpu": gpu}
    (out / "renders").mkdir(parents=True, exist_ok=True)
    G.ae_clean()
    with ab.ae_lock():
        ab.run_job("var V = " + json.dumps(V) + ";" + (HERE.parent / "ae_lib.jsx").read_text(encoding="ascii") + DOF_JSX,
                   out / (aep.stem + "_job.jsx"), aep, 600)
    # AE's own messages carry curly quotes ("Can not “set value”"): ab.ae_notes keeps them
    notes = sorted(n[5:] for n in ab.ae_notes(aep.read_bytes()) if n.startswith("WARN "))
    built = [sc["name"] for sc in scenes if any(n.startswith(sc["name"] + ": renderer now") for n in notes)]
    files = {n: out / "renders" / f"{n}.mp4" for n in built}
    t0 = time.time()
    rc = _aerender(aep, files, t0, timeout=900) if files else None
    boxes = {"near_z-900": (16, 40, 60, 68), "focus_z0": (82, 44, 110, 64), "far_z2500": (148, 49, 160, 59)}
    rows = {}
    for sc in scenes:
        f = files.get(sc["name"])
        if not f or not f.exists() or f.stat().st_mtime < t0:
            rows[sc["name"]] = {"error": "not built" if sc["name"] not in built else "not rendered"}
            continue
        fr = luma_frames(f, 0.4, 0.44)[0]
        png = out / f"{sc['name']}.png"
        G.frame(f, 0.4, png)
        rows[sc["name"]] = {"renderer_wanted": want, "dof": sc["dof"], "frame": rel(png), "render": rel(f),
                            "sharpness_192": {k: sharpness(fr, 192, b) for k, b in boxes.items()}}
    off, on = rows.get(f"DOF_{tag}_off", {}), rows.get(f"DOF_{tag}_on", {})
    if "sharpness_192" in off and "sharpness_192" in on:
        on["sharpness_ratio_on_over_off"] = {k: round(on["sharpness_192"][k] / max(1e-6, off["sharpness_192"][k]), 3)
                                             for k in boxes}
    return {"aerender_exit": rc, "ae_notes": notes, "scenes": rows, "project_gpu": bool(gpu)}


def ae_dof(out):
    """After Effects 26.0 depth of field per 3D renderer. comp.renderer names: 'ADBE Advanced 3d' is the CLASSIC 3D
    renderer (its old internal name), 'ADBE Calder' the newer Advanced 3D, 'ADBE Ernst' Cinema 4D. Classic on the
    software renderer, Advanced 3D with GPU acceleration (then AE's default for new projects is set back to software,
    as after a --gpu gallery run). Three copies of a test still at z -900 / 0 / +2500, the camera focused at z 0."""
    res = {"setup": "comp 1920x1080, camera at its zoom distance looking at z 0, Focus Distance = zoom, Aperture 300 px, "
                    "Blur Level 100; copies of a test still (scale 22%) at x 576 / 960 / 2084, z -900 / 0 / +2500 (on "
                    "screen near x 380, centre, far x 1540); frame at 0.4 s, mean gradient per copy at 192x108 (lower = "
                    "blurrier)", "renderer_names": {"ADBE Advanced 3d": "Classic 3D", "ADBE Calder": "Advanced 3D",
                                                   "ADBE Ernst": "Cinema 4D"}}
    res["classic_3d"] = dof_project(out, "classic", "advanced 3d", False)
    try:
        res["advanced_3d"] = dof_project(out, "adv3d", "calder", True)
    except SystemExit as e:
        res["advanced_3d"] = {"failed": str(e)[:300]}
        ab.restart_ae()
    G.ae_clean()  # the GPU project set AE's default renderer for new projects: put software back
    from vfx import render_scenes
    render_scenes([{"name": "CPU", "dur": 0.04, "layers": [{"type": "solid", "name": "S", "color": "#000000"}]}],
                  out / "dof_cpu_default.aep", out / "renders", render=False, gpu="cpu")
    res["renderer_default_restored"] = "software (a project saved with gpu='cpu')"
    return res


def two_track(only=None):
    """All four tests (only: a list of their names, the others kept from the last report)."""
    out = HERE / "gallery" / "two_track"
    out.mkdir(parents=True, exist_ok=True)
    p = LIVE / "two_track.json"
    res = json.loads(p.read_text(encoding="utf-8")) if only and p.exists() else {}
    res.update(note="measurements only, no verdicts; paths repo-relative", date=time.strftime("%Y-%m-%d"))
    for name, fn in (("track_matte_key", track_matte), ("camera_shake", camera_shake), ("morph_cut", morph_cut),
                     ("ae_advanced_3d_dof", ae_dof)):
        if only and name not in only:
            continue
        G.ram_guard()
        try:
            res[name] = fn(out)
        except SystemExit as e:
            res[name] = {"failed": str(e)[:500]}
            ab.restart_ae()
        report("two_track.json", res)
    return res


# ---------------- task: audio ----------------
SIGNALS = {  # stereo 48 kHz 16-bit, 3 s each
    "tone": "aevalsrc=0.1259*sin(2*PI*1000*t)|0.1259*sin(2*PI*1000*t):s=48000:d=3",  # 1 kHz, -18 dBFS peak
    "hot": "aevalsrc=0.5*sin(2*PI*1000*t)|0.5*sin(2*PI*1000*t):s=48000:d=3",  # 1 kHz, -6 dBFS peak
    "pink": "anoisesrc=color=pink:amplitude=0.25:seed=42:r=48000:d=3,pan=stereo|c0=c0|c1=c0",
    "vo": "aevalsrc=" + "|".join(["0.25*sin(2*PI*(130+25*sin(2*PI*0.7*t))*t)*(0.55+0.45*sin(2*PI*3.1*t))*abs(sin(2*PI*0.4*t+0.6))"] * 2)
          + ":s=48000:d=3",
    "click": "aevalsrc=0.9*lt(abs(t-0.25)\\,0.00001)|0.9*lt(abs(t-0.25)\\,0.00001):s=48000:d=3",  # one impulse at 0.25 s
    "split": "aevalsrc=0.25*sin(2*PI*440*t)|0.0625*sin(2*PI*1000*t):s=48000:d=3",  # L 440 Hz -12, R 1 kHz -24 dBFS peak
    "hum": "aevalsrc=" + "|".join(["0.1*sin(2*PI*50*t)+0.05*sin(2*PI*100*t)+0.03*sin(2*PI*150*t)+0.02*sin(2*PI*1000*t)"] * 2)
           + ":s=48000:d=3",
}
SLOT, DUR = 3.6, 3.0  # a test every 3.6 s (a multiple of 0.2 s, on both frame grids): 3 s of signal, then silence


def tests_table(dump):
    """[(effect, settings dict, signal, measure)] for the measurement run. `dump`: {effect: [prop names]} from the
    stereo dump (stereo-only effects have no mono harvest row)."""
    def sweep(fx, param, values, sig, measure, fixed=None):
        return [(fx, {**(fixed or {}), param: v}, sig, measure) for v in values]
    T = []
    T += sweep("Amplify", "Gain", [0, 0.333, 0.6667, 1], "tone", "gain")
    T += sweep("Volume", "Level", [0.0562, 0.1778, 0.3162, 1.0], "tone", "gain")
    T += sweep("Bass", "Boost", [0, 0.25, 0.75, 1], "pink", "shelf_low")
    T += sweep("Treble", "Boost", [0, 0.25, 0.75, 1], "pink", "shelf_high")
    for fx in ("Highpass", "Lowpass"):
        T += sweep(fx, "Cutoff", [0.05, 0.25, 0.5, 0.75], "pink", "cutoff_" + fx.lower())
    T += sweep("Bandpass", "Cutoff", [0.1, 0.25, 0.5, 0.75], "pink", "peak")
    T += sweep("Simple Notch Filter", "Cutoff", [0.1, 0.25, 0.5, 0.75], "pink", "notch")
    T += sweep("Simple Parametric EQ", "Center", [0.1, 0.25, 0.5, 0.75], "pink", "peak", {"Boost": 1})
    T += sweep("Simple Parametric EQ", "Boost", [0, 0.25, 0.75, 1], "pink", "peak", {"Center": 0.25})
    T += sweep("Parametric Equalizer", "EQ Band 3 Gain", [0, 0.25, 0.75, 1], "pink", "peak")
    T += sweep("Parametric Equalizer", "EQ Band 3 Center Frequency", [0.01, 0.05, 0.2, 0.5], "pink", "peak",
               {"EQ Band 3 Gain": 1})
    for fx, bandset in (("Graphic Equalizer (10 Bands)", (1, 4, 7, 10)), ("Graphic Equalizer (20 Bands)", (1, 7, 14, 20)),
                        ("Graphic Equalizer (30 Bands)", (1, 10, 20, 30))):
        T += [(fx, {f"EQ Band {b}": 1}, "pink", "peak") for b in bandset] + [(fx, {f"EQ Band {bandset[1]}": 0}, "pink", "notch")]
    T += sweep("Delay", "Delay", [0.1, 0.3, 0.6], "click", "delay", {"Feedback": 0, "Mix": 0.5})
    T += sweep("Multitap Delay", "Delay 1", [0.1, 0.3, 0.6], "click", "delay", {"Level 2": 0, "Level 3": 0, "Level 4": 0})
    T += sweep("Analog Delay", "Delay", [0.01, 0.0238, 0.1, 0.3], "click", "delay", {"Feedback": 0})
    T += sweep("Pitch Shifter", "Transpose Ratio", [0.25, 0.3333, 0.5, 0.75], "tone", "pitch")
    T += sweep("Hard Limiter", "Maximum Amplitude", [0.25, 0.5, 0.75, 0.999], "hot", "peak_out")
    T += sweep("Hard Limiter", "Input Boost", [0.5, 0.6667, 0.8333, 1], "tone", "gain")
    for fx in ("Single-band Compressor", "Tube-modeled Compressor"):
        T += sweep(fx, "Threshold", [0.25, 0.5, 0.75, 1], "hot", "gain", {"Ratio": 0.5})
        T += sweep(fx, "Gain", [0, 0.25, 0.5, 0.75], "tone", "gain")
    T += sweep("Multiband Compressor", "Output Gain", [0.25, 0.5, 0.75], "tone", "gain")
    T += sweep("Dynamics", "Compressor Threshold", [0.25, 0.5, 0.6667], "hot", "gain",
               {"Compressor Enabled": 1, "Compressor Ratio": 0.5})
    T += sweep("Dynamics Processing", "Output Gain", [0.25, 0.5, 0.75], "tone", "gain")
    T += sweep("Mastering", "Output Gain", [0.3333, 0.6667, 1], "tone", "gain")
    T += sweep("Mastering", "Loudness Maximizer Amount", [0, 0.5, 1], "vo", "lufs")
    T += sweep("Vocal Enhancer", "Mode", [0, 0.5, 1], "vo", "spectrum")
    T += sweep("DeNoise", "Amount", [0, 0.4, 1], "pink", "gain")
    T += sweep("DeReverb", "Amount", [0, 0.4, 1], "vo", "gain")
    T += sweep("Adaptive Noise Reduction", "Reduce Noise By", [0, 0.5, 1], "pink", "gain")
    T += sweep("Automatic Click Remover", "Threshold", [0.1, 0.2929, 0.9], "click", "peak_out")
    T += sweep("DeHummer", "Harmonic Base Gain", [0, 0.2353, 1], "hum", "spectrum")
    T += sweep("DeEsser", "Threshold", [0.25, 0.625, 1], "pink", "spectrum")
    T += sweep("Distortion", "dB Range", [0.3333, 0.6667, 1], "tone", "thd")
    T += sweep("GuitarSuite", "Dist Amount", [0, 0.722, 1], "tone", "thd")
    T += sweep("Chorus/Flanger", "Intensity", [0, 0.5, 1], "pink", "spectrum")
    T += sweep("Flanger", "Mix", [0, 0.4, 1], "pink", "spectrum")
    T += sweep("Phaser", "Mix", [0, 0.5, 1], "pink", "spectrum")
    T += sweep("Convolution Reverb", "Mix", [0, 0.5, 1], "click", "decay")
    T += sweep("Studio Reverb", "Decay", [0.1, 0.2347, 0.6], "click", "decay")
    T += sweep("Studio Reverb", "Wet Output Level", [0, 0.25, 1], "click", "decay")
    T += sweep("Surround Reverb", "Mix", [0, 0.5, 1], "click", "decay")
    T += sweep("FFT Filter", "Y 1", [0.25, 0.75, 1], "pink", "spectrum", None)
    T += sweep("Scientific Filter", "Filter Type", [0, 0.3333, 0.6667, 1], "pink", "spectrum")
    T += [("Notch Filter", {}, "pink", "notch"), ("Loudness Meter", {}, "vo", "gain"), ("Loudness Radar", {}, "vo", "gain")]
    T += sweep("Loudness Meter", "Target Level", [0, 1], "vo", "gain")
    T += sweep("Mute", "Mute All", [0, 1], "tone", "gain")
    for fx in ("Invert", "Swap Channels", "Fill Left with Right", "Fill Right with Left"):
        T.append((fx, {}, "split", "stereo"))
    for fx in ("Balance", "Channel Volume", "Stereo Expander", "Channel Mixer"):  # stereo-only: names from the dump
        names = [n for n in dump.get(fx, []) if n]
        T.append((fx, {}, "split", "stereo"))
        for n in names[:2]:
            T += sweep(fx, n, [0, 0.5, 1], "split", "stereo")
    return T


def interpret(measure, m, dry):
    """One number (and its unit) per test from its measurements against the dry signal's."""
    if measure == "gain":
        return {"gain_db": round(m["rms_db"] - dry["rms_db"], 2)}
    if measure == "lufs":
        return {"lufs": m.get("lufs"), "lufs_change": round(m["lufs"] - dry["lufs"], 2) if m.get("lufs") is not None and
                dry.get("lufs") is not None else None}
    if measure == "peak_out":
        return {"peak_dbfs": m["peak_db"], "peak_change_db": round(m["peak_db"] - dry["peak_db"], 2)}
    if measure in ("cutoff_highpass", "cutoff_lowpass"):
        return {"cutoff_hz": cutoff(m["transfer_db"], measure.split("_")[1])}
    if measure in ("peak", "notch"):
        f, g = extreme(m["transfer_db"], measure)
        return {f"{measure}_hz": f, f"{measure}_db": g}
    if measure == "shelf_low":
        return {"gain_63hz_db": band_at(m["transfer_db"], 63), "gain_1khz_db": band_at(m["transfer_db"], 1000)}
    if measure == "shelf_high":
        return {"gain_10khz_db": band_at(m["transfer_db"], 10000), "gain_1khz_db": band_at(m["transfer_db"], 1000)}
    if measure == "delay":
        e = m["echoes"]["echoes"]
        return {"first_echo_s": e[0][0] if e else None, "first_echo_db": e[0][1] if e else None}
    if measure == "decay":
        return {"edc_t30_s": m.get("edc_t30_s"), "dry_edc_t30_s": dry.get("edc_t30_s"), "envelope_t30_s": m["echoes"]["t30_s"]}
    if measure == "pitch":
        f = m.get("freq_hz")
        return {"freq_hz": f, "semitones": round(12 * math.log2(f / 1000), 2) if f else None}
    if measure == "thd":
        return {"thd_pct": m.get("thd_pct"), "dry_thd_pct": dry.get("thd_pct")}
    if measure == "stereo":
        return m["stereo"]
    if measure == "spectrum":
        tf = m["transfer_db"]
        return {"gain_db": round(m["rms_db"] - dry["rms_db"], 2), "transfer_min_db": min(tf), "transfer_max_db": max(tf),
                "at_hz": [THIRD[tf.index(min(tf))], THIRD[tf.index(max(tf))]]}
    return {}


def edc_t30(x, rate, start=None):
    """Schroeder energy-decay curve from the direct sound (the peak, or `start` s): seconds until the energy still to
    come is 30 dB below the total (None if it never is). A gap of silence before a late tail cannot end it early."""
    i0 = int(start * rate) if start is not None else max(range(len(x)), key=lambda i: abs(x[i]))
    e, acc = [v * v for v in x[i0:]], 0.0
    tail = [0.0] * len(e)
    for i in range(len(e) - 1, -1, -1):
        acc += e[i]
        tail[i] = acc
    if not acc:
        return None
    lim = acc * 10 ** (-30 / 10)
    j = next((i for i, v in enumerate(tail) if v < lim), None)
    return round(j / rate, 3) if j is not None else None


def measure(chs, rate, sig, wav=None, t0=None):
    """Measurements of one test segment (chs = [L, R] float lists) by signal kind."""
    L = chs[0]
    steady = [c[int(0.3 * rate):int(2.8 * rate)] for c in chs]
    whole = sig == "click"  # the click sits at 0.25 s and its echoes run into the gap: measure the whole slot
    base = chs if whole else steady
    m = {"rms_db": rms_db(base[0]), "peak_db": peak_db(base[0]), "rms_db_R": rms_db(base[1]) if len(base) > 1 else None}
    if sig in ("pink", "vo", "hum"):
        m["bands_db"] = bands(steady[0], rate)
    if sig in ("tone", "hot"):
        m["freq_hz"] = tone_freq(steady[0][:rate], rate)
        m["thd_pct"] = thd(steady[0][:rate], rate, m["freq_hz"] or 1000)
    if sig == "click":
        m["echoes"] = echoes(L, rate)
        m["edc_t30_s"] = edc_t30(L, rate, 0.25)
    if wav is not None and sig in ("vo", "tone"):
        m["lufs"] = lufs(wav, t0 + 0.3, 2.5)
    return m


def lufs(wav, t0, dur):
    import re
    err = subprocess.run(["ffmpeg", "-nostats", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", str(wav), "-af", "ebur128",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    mm = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    return float(mm[-1]) if mm else None


def audio_job(tests, files, out, tag, probe=False):
    """Lay the tests out on A1 (SLOT s apart), add / set their effects, dump the components, export a WAV."""
    wav = out / f"{tag}_{int(time.time())}.wav"
    pv = {"result": ab.fwd(LIVE / f"{tag}_result.json"), "prproj": ab.fwd(out / f"{tag}_{int(time.time())}.prproj"),
          "files": [ab.fwd(f) for f in files.values()], "probe": probe, "export": ab.fwd(wav), "epr": ab.fwd(WAV_EPR),
          "tests": [{"k": i, "src": ab.fwd(files[t["signal"]]), "start": i * SLOT, "in": 0, "dur": DUR,
                     "effect": t.get("effect"), "set": t.get("set") or {}} for i, t in enumerate(tests)]}
    got = pr_run("var PV = " + json.dumps(pv) + ";\n" + (HERE / "audio_map.jsx").read_text(encoding="ascii"), tag)
    if not got.get("ok"):
        sys.exit(f"audio job {tag} failed: {got}")
    return got, wav


def signal_files(out):
    files = {}
    for k, lav in SIGNALS.items():
        p = out / f"sig_{k}.wav"
        if not p.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", lav, "-ac", "2", "-ar", "48000",
                            "-c:a", "pcm_s16le", str(p)], check=True)
        files[k] = p
    return files


def dump_tests():
    names = json.loads((HERE / "harvest" / "pr" / "pr_lists.json").read_text(encoding="utf-8"))["audio_effects"]
    return [{"signal": "tone"}] + [{"signal": "tone", "effect": n} for n in names]


def write_audio_harvest(dtests, dres):
    harvest = [{"name": t["effect"], "component": r.get("component"), "match": r.get("match"), "added": r.get("added"),
                "props": r.get("props"), **({"error": r["error"]} if "error" in r else {})}
               for t, r in zip(dtests[1:], dres["tests"][1:])]
    (HERE / "harvest" / "pr_audio_params_w3.json").write_text(json.dumps({
        "about": "Premiere Pro 26 audio effects applied to a STEREO 48 kHz clip (the mono harvest gave seven stereo-only "
                 "effects the clip Volume component); each effect's component dumped at its defaults, numeric "
                 "properties range-probed (set to +-1e9, the clamp read back, default restored)",
        "effects": harvest}, indent=1), encoding="utf-8", newline="\n")
    print("wrote pve/harvest/pr_audio_params_w3.json", flush=True)
    return {h["name"]: [p["name"] for p in (h.get("props") or []) if isinstance(p.get("value"), (int, float))
                        and not isinstance(p.get("value"), bool)] for h in harvest}


def audio(reanalyse=False):
    """Run the dump and the measurement jobs (or, reanalyse, read their saved results and exports again)."""
    out = HERE / "gallery" / "audio_map"
    out.mkdir(parents=True, exist_ok=True)
    files, dtests = signal_files(out), dump_tests()
    prev = json.loads((LIVE / "audio_map.json").read_text(encoding="utf-8")) if reanalyse else None
    if reanalyse:
        dres = json.loads((LIVE / "audio_dump_result.json").read_text(encoding="utf-8"))
        dwav = HERE.parent / prev["renders"]["dump"]
    else:
        G.ram_guard()
        dres, dwav = audio_job(dtests, files, out, "audio_dump", probe=True)
    dump = write_audio_harvest(dtests, dres)
    rate, dch = read_wav(dwav)
    dry0 = measure([c[0:int(DUR * rate)] for c in dch], rate, "tone")
    defaults = {}
    for i, t in enumerate(dtests[1:], 1):
        m = measure([c[int(i * SLOT * rate):int((i * SLOT + DUR) * rate)] for c in dch], rate, "tone")
        defaults[t["effect"]] = {"gain_db_at_defaults": round(m["rms_db"] - dry0["rms_db"], 2), "thd_pct": m["thd_pct"],
                                 "freq_hz": m["freq_hz"], "R_db": m["rms_db_R"]}
    mtests = [{"signal": s} for s in SIGNALS] + [{"signal": sig, "effect": fx, "set": st, "measure": me}
                                                 for fx, st, sig, me in tests_table(dump)]
    if reanalyse:
        mres = json.loads((LIVE / "audio_measure_result.json").read_text(encoding="utf-8"))
        mwav = HERE.parent / prev["renders"]["measure"]
    else:
        G.ram_guard()
        mres, mwav = audio_job(mtests, files, out, "audio_measure")
    res = analyse_audio(mtests, mres, mwav)
    res.update(defaults_on_tone=defaults, renders={"dump": rel(dwav), "measure": rel(mwav)})
    report("audio_map.json", res)
    return res


def analyse_audio(mtests, mres, mwav):
    rate, ch = read_wav(mwav)
    seg = lambda i: [c[int(i * SLOT * rate):int((i * SLOT + SLOT) * rate)] for c in ch]  # noqa: E731
    dry = {}
    for i, s in enumerate(SIGNALS):
        dry[s] = measure(seg(i), rate, s, mwav, i * SLOT)
        dry[s]["stereo"] = stereo([c[:int(DUR * rate)] for c in seg(i)], [c[:int(DUR * rate)] for c in seg(i)])
    rows = []
    for i, (t, r) in enumerate(zip(mtests, mres["tests"])):
        if not t.get("effect"):
            continue
        s = seg(i)
        m = measure(s, rate, t["signal"], mwav, i * SLOT)
        if "bands_db" in m:
            m["transfer_db"] = transfer(m["bands_db"], dry[t["signal"]]["bands_db"])
        if t["signal"] == "split":
            m["stereo"] = stereo([c[:int(DUR * rate)] for c in s], [c[:int(DUR * rate)] for c in seg(list(SIGNALS).index("split"))])
        rows.append({"effect": t["effect"], "set": t["set"], "read_back": r.get("set"), "signal": t["signal"],
                     "measure": t["measure"], "result": interpret(t["measure"], m, dry[t["signal"]]),
                     "rms_db": m["rms_db"], "peak_db": m["peak_db"],
                     **({"transfer_db": m["transfer_db"]} if "transfer_db" in m else {}),
                     **({"error": r["error"]} if r.get("error") else {}), "seq_s": round(i * SLOT, 2)})
    return {"about": "Premiere Pro 26 audio effects: 0..1 parameter values mapped to dB / Hz / s / LUFS by measurement. "
                     "Measurements only (no verdicts)",
            "method": {"signals": dict(SIGNALS), "layout": f"one test per {SLOT} s on A1 ({DUR} s of signal, then silence "
                       "for tails), one sequence, exported with the Media Encoder 'Waveform Audio 48kHz 16-bit' preset",
                       "steady": "levels over 0.3-2.8 s of each test (left channel; R as rms_db_R); the click test over "
                       "its whole slot", "spectrum": "third-octave band power (Hann FFT 4096, 6 frames) minus the dry "
                       f"signal's = transfer_db, bands at {THIRD[0]}..{THIRD[-1]} Hz",
                       "cutoff": "-3 dB from the transfer's maximum, log-interpolated",
                       "delay": "5 ms RMS envelope of the click test: peaks above -40 dB re the direct sound",
                       "decay": "edc_t30: Schroeder energy-decay curve of the click test, time from the direct sound until "
                       "it is 30 dB down", "pitch": "zero-crossing frequency of the 1 kHz tone",
                       "lufs": "ffmpeg ebur128 integrated over 2.5 s",
                       "mapping": "per effect | parameter | measure: [value, number]; the number is the result's dB for a "
                       "gain-like parameter (Gain, Boost, Level, Threshold, Amount, Mix ...) and its first figure "
                       "otherwise; line = least-squares slope / intercept / worst residual"},
            "dry": {k: {kk: v for kk, v in d.items() if kk in ("rms_db", "peak_db", "lufs", "freq_hz", "thd_pct", "echoes")}
                    for k, d in dry.items()},
            "tests": rows, "mapping": mapping(rows)}


GAINLIKE = ("gain", "boost", "level", "threshold", "amount", "mix", "intensity", "maximum amplitude", "input boost")


def mapping(rows):
    """Per effect + parameter + measure: [[value, number], ...] and, where a straight line fits, (slope, intercept,
    worst residual). The number is the result's dB figure for a gain-like parameter, else its first figure."""
    out = {}
    for r in rows:
        if not r["set"]:
            continue
        p = list(r["set"])[-1]  # the swept parameter is the last key (fixed ones come first)
        nums = {k: v for k, v in r["result"].items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        dbk = [k for k in nums if k.endswith("_db") or k == "gain_db"]
        k = dbk[0] if dbk and any(g in p.lower() for g in GAINLIKE) else next(iter(nums), None)
        if r["measure"] == "stereo":
            k = "R_db" if "right" in p.lower() else "L_db"
        v = out.setdefault(f"{r['effect']} | {p} | {r['measure']}", {"points": [], "figure": k})
        v["points"].append([r["set"][p], nums.get(k)])
    for v in out.values():
        v["line"] = fit_line([x for x, _ in v["points"]], [y for _, y in v["points"]])
    return out



# ---------------- task: second footage set ----------------
def blurred_frames(n):
    """Frames of handheld_shake.mp4 that carry the directional blur (make_test_media.py)."""
    return [i for i in range(n) if i % 4 == 1 or i % 7 == 5]


# the 13 cards the critic marked untested:no_suitable_footage (id = kind:match, or kind:name without a match) -> plate
FOOT = {
    "ae_effect:ADBE CameraShakeDeblur": "handheld_shake.mp4",
    "ae_effect:ADBE Rolling Shutter": "rolling_shutter_pan.mp4",
    "pr_effect:AE.ADBE Rolling Shutter": "rolling_shutter_pan.mp4",
    "ae_effect:ADBE Spill2": "presenter_hair.mp4",
    "ae_effect:ADBE Matte Choker": "presenter_hair.mp4 keyed, and subject_cutout.png (alpha still)",
    "ae_effect:ADBE RefineMatte": "presenter_hair.mp4 keyed",
    "ae_preset:Keylight + Key Cleaner + Advanced Spill Suppressor": "presenter_hair.mp4",
    "ae_preset:Reduce DV blockiness": "archive_4x3.mp4",
    "recipe:Low-res archive upscale cleanup": "archive_4x3.mp4",
    "recipe:Phone pan jello fix": "rolling_shutter_pan.mp4",
    "recipe:Frame-rate judder conform": "vfr_phone.mp4",
    "recipe:Interlace combing and line twitter fix": "interlaced_25i.mp4",
    "recipe:Green screen key": "presenter_hair.mp4 over test_b.mp4",
    # marked untested:no_suitable_footage by the wave-2 verifier after the critic's list (After Effects only)
    "recipe:Wire, boom and scratch line removal": "dust_wire.mp4",
    "recipe:Archive dust and speck cleanup": "dust_wire.mp4",
}
KEY = {"name": "Keylight 906", "props": {"Screen Colour": "#00B140"}}
RECIPE_PLATES = {"Low-res archive upscale cleanup": "archive_4x3.mp4", "Phone pan jello fix": "rolling_shutter_pan.mp4",
                 "Frame-rate judder conform": "vfr_phone.mp4", "Interlace combing and line twitter fix": "interlaced_25i.mp4",
                 "Wire, boom and scratch line removal": "dust_wire.mp4", "Archive dust and speck cleanup": "dust_wire.mp4"}


def card_index():
    """{kind:match-or-name: card} over every card (read only)."""
    import pve
    return {f"{c['kind']}:{c.get('match') or c['name']}": c for c in pve.cards() if c.get("kind")}


def ae_footage_scenes(cards, presets, cut):
    """{scene name: (card id or None for a control, variant, plate file, layers)} for the After Effects renders."""
    P = lambda n: (SET2 / n).as_posix()  # noqa: E731
    demo = lambda cid: dict(cards[cid].get("demo_settings") or {})  # noqa: E731

    def foot(src, effects=None, presets_=None, **kw):
        return {"type": "footage", "name": "F", "src": src, "in": 0, "t0": 0, "t1": 4.0, **kw,
                **({"effects": effects} if effects else {}), **({"presets": presets_} if presets_ else {})}
    grey = {"type": "solid", "name": "BG", "color": "#808080"}
    hair, ks = "presenter_hair.mp4", "Keylight + Key Cleaner + Advanced Spill Suppressor"
    sc = {
        "S_deblur": ("ae_effect:ADBE CameraShakeDeblur", "card demo", "handheld_shake.mp4", [foot(P("handheld_shake.mp4"), [
            {"name": "ADBE CameraShakeDeblur", "props": demo("ae_effect:ADBE CameraShakeDeblur")}])]),
        "S_deblur_ctrl": (None, "control", "handheld_shake.mp4", [foot(P("handheld_shake.mp4"))]),
        "S_rsr": ("ae_effect:ADBE Rolling Shutter", "card demo", "rolling_shutter_pan.mp4", [foot(P("rolling_shutter_pan.mp4"), [
            {"name": "ADBE Rolling Shutter", "props": demo("ae_effect:ADBE Rolling Shutter")}])]),
        "S_rsr_ctrl": (None, "control", "rolling_shutter_pan.mp4", [foot(P("rolling_shutter_pan.mp4"))]),
        "S_spill": ("ae_effect:ADBE Spill2", "card demo (Key Color #0047BB)", hair, [grey, foot(P(hair), [
            {"name": "ADBE Spill2", "props": demo("ae_effect:ADBE Spill2")}])]),
        "S_spill_green": ("ae_effect:ADBE Spill2", "card demo, Key Color set to the plate green #00B140", hair, [grey, foot(
            P(hair), [{"name": "ADBE Spill2", "props": {**demo("ae_effect:ADBE Spill2"), "Key Color": "#00B140"}}])]),
        "S_hair_ctrl": (None, "control (plate untouched)", hair, [grey, foot(P(hair))]),
        "S_key_only": (None, "control (Keylight only, Screen Colour #00B140)", hair, [grey, foot(P(hair), [KEY])]),
        "S_choke_keyed": ("ae_effect:ADBE Matte Choker", "card demo after Keylight (Screen Colour #00B140)", hair, [grey, foot(
            P(hair), [KEY, {"name": "ADBE Matte Choker", "props": demo("ae_effect:ADBE Matte Choker")}])]),
        "S_choke_cutout": ("ae_effect:ADBE Matte Choker", "card demo on an alpha still", "subject_cutout.png", [grey, foot(
            cut, [{"name": "ADBE Matte Choker", "props": demo("ae_effect:ADBE Matte Choker")}], alpha_mode="straight")]),
        "S_cutout_ctrl": (None, "control (alpha still untouched)", "subject_cutout.png", [grey, foot(cut, alpha_mode="straight")]),
        "S_refine_keyed": ("ae_effect:ADBE RefineMatte", "defaults after Keylight (Screen Colour #00B140)", hair, [grey, foot(
            P(hair), [KEY, {"name": "ADBE RefineMatte", "props": {}}])]),
        "S_keypreset": (f"ae_preset:{ks}", "preset as shipped (Screen Colour unset)", hair, [grey, foot(
            P(hair), presets_=[{"file": presets[ks]["file"], "at": 0}])]),
        "S_keypreset_stack": (f"ae_preset:{ks}", "its three effects added one by one, Screen Colour #00B140 (a preset's "
                              "values cannot be set once it is applied)", hair, [grey, foot(P(hair), [
                                  KEY, {"name": "ADBE KeyCleaner", "props": {}}, {"name": "ADBE Spill2", "props": {}}])]),
        "S_dv": ("ae_preset:Reduce DV blockiness", "preset at 0 s", "archive_4x3.mp4", [foot(
            P("archive_4x3.mp4"), presets_=[{"file": presets["Reduce DV blockiness"]["file"], "at": 0}])]),
        "S_dv_ctrl": (None, "control", "archive_4x3.mp4", [foot(P("archive_4x3.mp4"))]),
    }
    for i, (name, plate) in enumerate(RECIPE_PLATES.items()):
        sc[f"S_recipe_{i}"] = (f"recipe:{name}", "recipe after_effects stack", plate, [foot(
            P(plate), json.loads(json.dumps(cards[f"recipe:{name}"]["after_effects"])))])
        if plate not in ("archive_4x3.mp4", "rolling_shutter_pan.mp4") and not any(
                v[2] == plate and v[0] is None for v in sc.values()):  # one control per plate
            sc[f"S_recipe_{i}_ctrl"] = (None, "control", plate, [foot(P(plate))])
    sc["S_recipe_green"] = ("recipe:Green screen key", "recipe after_effects stack over test_b", hair, [
        foot((G.MEDIA / "test_b.mp4").as_posix()), foot(P(hair), json.loads(json.dumps(
            cards["recipe:Green screen key"]["after_effects"])))])
    sc["S_recipe_green_ctrl"] = (None, "control (plate over test_b)", hair, [foot((G.MEDIA / "test_b.mp4").as_posix()),
                                                                          foot(P(hair))])
    return sc


def footage():
    out = HERE / "gallery" / "set2"
    out.mkdir(parents=True, exist_ok=True)
    import make_test_media
    make_test_media.main(only=[k for k in make_test_media.MEDIA if k.startswith("pve/media/set2/")])
    allc = card_index()
    cards = {cid: allc[cid] for cid in FOOT if cid in allc}
    cut = (HERE.parent / "example" / "parallax" / "subject_cutout.png").as_posix()
    ae_sc = ae_footage_scenes(cards, {p["name"]: p for p in G.preset_files()}, cut)
    scenes = [{"name": n, "dur": 4.0, "layers": L} for n, (_, _, _, L) in ae_sc.items()]
    G.ram_guard()
    G.ae_clean()
    failed = {}
    files = G.render_halving(scenes, out / "gal_set2.aep", out / f"renders_{int(time.time())}", failed)
    rows = []
    for n, (cid, variant, plate, _) in ae_sc.items():
        row = {"id": cid or "control", "scene": n, "app": "AE", "variant": variant, "plate": plate, "frames": []}
        if n in failed or n not in files:
            row["failed"] = failed.get(n, "not rendered")
            rows.append(row)
            continue
        row["render"] = rel(files[n])
        for t in (0.92, 1.04, 2.0, 3.0):
            png = out / f"{n}_{t:.2f}.png"
            G.frame(files[n], t, png)
            row["frames"].append(rel(png))
        row["measured"] = measure_plate(files[n], plate)
        rows.append(row)
    # Premiere: the card's demo and the recipes' premiere stacks, one build; each 3 s from source 0.5 s on V2
    pr = [("pr_effect:AE.ADBE Rolling Shutter", "card demo", "rolling_shutter_pan.mp4",
           [{"name": "Rolling Shutter Repair", "pr_props": dict(cards["pr_effect:AE.ADBE Rolling Shutter"]["demo_settings"])}]),
          (None, "control", "rolling_shutter_pan.mp4", None)]
    for name, plate in RECIPE_PLATES.items():
        if not cards[f"recipe:{name}"].get("premiere"):
            continue  # an After Effects-only recipe
        pr.append((f"recipe:{name}", "recipe premiere stack", plate, json.loads(json.dumps(cards[f"recipe:{name}"]["premiere"]))))
        if plate != "rolling_shutter_pan.mp4":
            pr.append((None, "control", plate, None))
    clips, pslots = [], []
    for i, (cid, variant, plate, fx) in enumerate(pr):
        clips.append({"src": str(SET2 / plate), "in": 0.5, "start": i * 3.0, "dur": 3.0, "track": 2,
                      **({"effects": fx} if fx else {})})
        pslots.append((cid, variant, plate, i * 3.0))
    gk = len(pr) * 3.0  # green screen: the plate on V2 over test_b on V1, keyed, then not
    for j, fx in enumerate((json.loads(json.dumps(cards["recipe:Green screen key"]["premiere"])), None)):
        clips += [{"src": str(G.MEDIA / "test_b.mp4"), "in": 0, "start": gk + 3 * j, "dur": 3.0, "track": 1},
                  {"src": str(SET2 / "presenter_hair.mp4"), "in": 0.5, "start": gk + 3 * j, "dur": 3.0, "track": 2,
                   **({"effects": fx} if fx else {})}]
    pslots += [("recipe:Green screen key", "recipe premiere stack over test_b", "presenter_hair.mp4", gk),
               (None, "control (plate over test_b)", "presenter_hair.mp4", gk + 3)]
    G.ram_guard()
    G.ae_clean()
    try:
        b = ab.build({"name": "pve_w3_set2_pr", "width": 1920, "height": 1080, "fps": FPS, "clips": clips,
                      "out_dir": str(out / "builds")}, out, render=True, premiere=True, ae=False)
        for cid, variant, plate, t in pslots:
            row = {"id": cid or "control", "app": "PR", "variant": variant, "plate": plate, "seq_s": [t, t + 3.0], "frames": []}
            for dt in (0.42, 0.54, 1.5, 2.5):
                png = out / f"pr_{t:05.1f}_{dt:.2f}.png"
                G.frame(b["pr_mp4"], t + dt, png)
                row["frames"].append(rel(png))
            row["measured"] = measure_plate(b["pr_mp4"], plate, t0=t, dur=3.0, src0=0.5)
            rows.append(row)
        prw, prr = b["warnings"], rel(b["pr_mp4"])
    except SystemExit as e:
        prw, prr = [f"build failed: {str(e)[:300]}"], None
        ab.restart_ae()
    ids = sorted({r["id"] for r in rows if r["id"] != "control" and "failed" not in r})
    res = {"about": "the 13 cards marked untested:no_suitable_footage re-rendered on the second test-footage set "
                    "(make_test_media.py, pve/media/set2/); measurements only, no verdicts. Frames: AE scenes at 0.92 / "
                    "1.04 / 2.0 / 3.0 s (source time = comp time, in 0); Premiere slots at +0.42 / 0.54 / 1.5 / 2.5 s "
                    "(source = 0.5 s + that)",
           "cards": {cid: {"name": c["name"], "kind": c["kind"], "plate": FOOT[cid]} for cid, c in cards.items()},
           "missing_cards": sorted(set(FOOT) - set(cards)), "rendered_ids": ids, "not_rendered": sorted(set(FOOT) - set(ids)),
           "renders": rows, "premiere_render": prr, "premiere_warnings": prw, "ae_failed": failed}
    report("footage_rerender.json", res)
    return res


def measure_plate(video, plate, t0=0.0, dur=4.0, src0=0.0):
    """The plate's own problem, measured after the effect: blur per frame (shake plate), rolling-shutter skew,
    combing, 8x8-block (24 px at 3x) blockiness, frame-to-frame change (VFR), green left / on the rim (green screen),
    the alpha edge (cutout)."""
    m = {}
    if plate == "handheld_shake.mp4":
        full = luma_frames(video, t0, t0 + dur, 384, 216)
        m["sharpness_per_frame_384"] = [sharpness(f, 384, (8, 8, 376, 208)) for f in full]
        m["blurred_source_frames"] = [i for i in blurred_frames(len(full) + int(src0 * FPS)) if i >= int(src0 * FPS)]
    elif plate == "rolling_shutter_pan.mp4":
        fr = luma_frames(video, t0 + 0.4, t0 + 2.0, 192, 108)
        m["skew_px_192"] = [skew(f, 192, 108) for f in fr]
    elif plate == "interlaced_25i.mp4":
        f = luma_frames(video, t0 + 1.0, t0 + 1.04, 1920, 1080)
        m["combing_1080"] = combing(f[0], 1920, 1080) if f else None
    elif plate == "archive_4x3.mp4":
        f = luma_frames(video, t0 + 1.0, t0 + 1.04, 1920, 1080)
        if f:
            m["blockiness_24px_grid"] = blockiness(f[0], 1920, 1080, 24)
            m["sharpness_1080"] = sharpness(f[0], 1920, (0, 0, 1920, 1080))
    elif plate == "vfr_phone.mp4":
        fr = luma_frames(video, t0 + 0.4, t0 + 2.4, 192, 108)
        ch = [round(sum(abs(a - b) for a, b in zip(f, g)) / len(f), 2) for f, g in zip(fr, fr[1:])]
        m["frame_change"], m["repeated_frames"] = ch, sum(1 for c in ch if c < 0.3)
    elif plate == "presenter_hair.mp4":
        m.update(green_left(video, t0 + 1.0, src0 + 1.0))
    elif plate == "dust_wire.mp4":
        f = luma_frames(video, t0 + 1.0, t0 + 1.04, 1920, 1080)
        if f:
            m["specks_1080"] = specks(f[0], 1920, 1080)
            m["wire_contrast_1080"] = wire_contrast(f[0], 1920, 1080)
    elif plate == "subject_cutout.png":
        raw = G.rgb_at(video, t0 + 1.0, 1920, 1080)
        row = 600 * 1920 * 3
        m["edge_luma_x1220_1320_y600"] = [round(sum(raw[row + x * 3:row + x * 3 + 3]) / 3) for x in range(1220, 1321, 4)]
    return m


def green_left(video, t, src_t):
    """Share of pixels still screen green, and the mean green excess (G - max(R, B)) on the disc's rim (radius
    300-340 px around the disc centre at that source time: x = 960 + 100 sin(t), y = 570)."""
    raw = G.rgb_at(video, t, 384, 216)
    px = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw) - 2, 3)]
    green = sum(1 for r, g, b in px if g > 120 and g > r + 50 and g > b + 50) / len(px)
    cx, cy = (960 + 100 * math.sin(src_t)) / 5, 570 / 5
    rim = [g - max(r, b) for i, (r, g, b) in enumerate(px) if 60 <= math.hypot(i % 384 - cx, i // 384 - cy) <= 68]
    return {"green_pixels_share": round(green, 4), "rim_green_excess": round(sum(rim) / len(rim), 2) if rim else None}


if __name__ == "__main__":
    if sys.argv[1] == "two_track" and len(sys.argv) > 2:
        two_track(sys.argv[2:])
    elif sys.argv[1] == "audio" and "--reanalyse" in sys.argv:
        audio(reanalyse=True)
    else:
        {"two_track": two_track, "audio": audio, "footage": footage}[sys.argv[1]]()
