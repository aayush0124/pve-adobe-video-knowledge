"""Synthesised SFX for AdobeBridge edits (0 credits): python sfx_synth.py [--all] -> sfx/<name>.wav (48 kHz, mono).
(Without --all only the missing beds2() beds are written: Premiere locks the files an open project uses.)

Fills what the Premiere Composer pack lacks: risers, sub drops, ambience beds (fire, sirens, projector, artillery,
drone), tape rewind, glitch, shutter, UI blips, ticks, heartbeat, jet pass. tick_roll() builds a counter's ticks.
"""
import sys
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.io import wavfile

SR = 48000
OUT = Path(__file__).resolve().parent / "sfx"
rng = np.random.default_rng(7)


def t_(d):
    return np.arange(int(d * SR)) / SR


def lp(x, f, o=4):
    return signal.sosfilt(signal.butter(o, f, "low", fs=SR, output="sos"), x)


def hp(x, f, o=4):
    return signal.sosfilt(signal.butter(o, f, "high", fs=SR, output="sos"), x)


def bp(x, f0, f1, o=4):
    return signal.sosfilt(signal.butter(o, [f0, f1], "band", fs=SR, output="sos"), x)


def noise(d):
    return rng.standard_normal(int(d * SR))


def brown(d):
    x = np.cumsum(noise(d))
    return hp(x - x.mean(), 20)


def chirp(d, f0, f1, method="logarithmic"):
    return signal.chirp(t_(d), f0, d, f1, method=method)


def env(d, a, r, curve=4.0):
    """attack a s, exponential-ish release over the rest."""
    t = t_(d)
    e = np.minimum(1, t / max(a, 1e-4))
    rel = np.clip((t - a) / max(d - a, 1e-4), 0, 1)
    return e * (1 - rel) ** curve if r else e


def reverb(x, tail=1.8, mix=0.35):
    ir = noise(tail) * np.exp(-t_(tail) * 5 / tail)
    ir = lp(ir, 5000)
    wet = signal.fftconvolve(x, ir)[: len(x) + int(tail * SR)]
    dry = np.pad(x, (0, len(wet) - len(x)))
    return dry * (1 - mix) + wet / (np.abs(wet).max() + 1e-9) * np.abs(x).max() * mix


def save(name, x, peak_db=-1.0):
    OUT.mkdir(exist_ok=True)
    x = np.asarray(x, dtype=np.float64)
    x = x / (np.abs(x).max() + 1e-12) * 10 ** (peak_db / 20)
    wavfile.write(OUT / f"{name}.wav", SR, (x * (2 ** 31 - 1)).astype(np.int32))
    return OUT / f"{name}.wav"


def add(buf, i, x):
    """Mix x into buf at sample i, clipped to buf's bounds."""
    i = int(i)
    a, b = max(0, i), min(len(buf), i + len(x))
    if b > a:
        buf[a:b] += x[a - i:b - i]


def loop_fade(x, f=0.05):
    n = int(f * SR)
    x[:n] *= np.linspace(0, 1, n)
    x[-n:] *= np.linspace(1, 0, n)
    return x


def build():
    d = 2.5  # riser: noise sweep + rising tone, swelling into a cut
    sw = np.concatenate([bp(seg, 300 + 3000 * k / 40, 900 + 9000 * k / 40, 2)
                         for k, seg in enumerate(np.array_split(noise(d), 40))])
    save("riser", (sw * 0.7 + 0.3 * chirp(d, 120, 1600)) * env(d, d * 0.97, 0) ** 2.2)
    d = 2.0  # sub drop: falling sine + click
    save("sub_drop", np.sin(2 * np.pi * np.cumsum(np.geomspace(90, 28, int(d * SR))) / SR) * env(d, 0.005, 1, 2.5)
         + np.pad(hp(noise(0.02), 2000) * 0.4, (0, int(d * SR) - int(0.02 * SR))))
    d = 1.6  # tape rewind: squealing chirp + gritty band noise with tremolo
    trem = 0.6 + 0.4 * np.sin(2 * np.pi * 18 * t_(d))
    save("tape_rewind", (0.5 * chirp(d, 2600, 900) + bp(noise(d), 1500, 6000)) * trem * env(d, 0.05, 1, 0.6))
    d = 0.45  # glitch: bitcrushed blocks of noise and square tones
    g = np.zeros(int(d * SR))
    for s in range(0, len(g), int(0.03 * SR)):
        n = min(int(0.03 * SR), len(g) - s)
        g[s:s + n] = rng.choice([1, 0.6, 0]) * (np.sign(np.sin(2 * np.pi * rng.uniform(80, 2000) * t_(n / SR)))
                                                if rng.random() < 0.5 else rng.standard_normal(n))
    save("glitch", np.round(g * 6) / 6 * env(d, 0.002, 1, 1))
    d = 20.0  # fire crackle bed: low rumble + random crackles (loops)
    fire = lp(brown(d), 500) * 0.5
    for s in rng.uniform(0, d - 0.05, int(d * 14)):
        i = int(s * SR)
        c = hp(noise(0.012), 1500) * np.exp(-t_(0.012) * 300) * rng.uniform(0.2, 1.0)
        add(fire, i, c * 3)
    save("amb_fire", loop_fade(fire), -6)
    d = 12.0  # distant sirens: hi-lo two-tone, low-passed and reverberant
    f = np.where((t_(d) // 0.65) % 2 == 0, 680, 920)
    sir = np.sign(np.sin(2 * np.pi * np.cumsum(f) / SR)) * 0.5
    sir = lp(sir, 1800) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.07 * t_(d)))
    save("amb_sirens", loop_fade(reverb(sir, 2.5, 0.6)[: int(d * SR)]), -6)
    d = 10.0  # projector rattle: 24 Hz clicks + hum + flutter
    pr = np.zeros(int(d * SR))
    for k in range(int(d * 24)):
        i = int(k / 24 * SR + rng.uniform(-40, 40))
        c = bp(noise(0.006), 800, 5000, 2) * np.exp(-t_(0.006) * 600)
        add(pr, i, c * rng.uniform(0.5, 1))
    pr += 0.15 * np.sin(2 * np.pi * 50 * t_(d)) + 0.05 * bp(noise(d), 200, 3000, 2)
    save("amb_projector", loop_fade(pr), -6)
    d = 14.0  # distant artillery: sparse low booms with long rumble
    art = 0.08 * lp(brown(d), 120)
    for s in (0.8, 3.9, 5.1, 8.6, 11.2):
        i = int(s * SR)
        b = lp(noise(2.5), 140) * np.exp(-t_(2.5) * 2.2) * rng.uniform(0.6, 1)
        add(art, i, b * 4)
    save("amb_artillery", loop_fade(reverb(art, 3, 0.4)[: int(d * SR)]), -6)
    d = 20.0  # dark drone: detuned low saws, slowly breathing filter
    saw = sum(signal.sawtooth(2 * np.pi * fr * t_(d)) for fr in (55, 55.4, 82.4, 110.3))
    drone = np.concatenate([lp(seg, 200 + 250 * (1 + np.sin(k / 6))) for k, seg in enumerate(np.array_split(saw, 60))])
    save("amb_drone", loop_fade(drone), -6)
    d = 1.6  # heartbeat: lub-dub sub thumps
    hb = np.zeros(int(d * SR))
    for s, a in ((0.0, 1), (0.28, 0.7)):
        i = int(s * SR)
        th = np.sin(2 * np.pi * 52 * t_(0.25)) * np.exp(-t_(0.25) * 16) * a
        add(hb, i, th)
    save("heartbeat", hb)
    d = 0.35  # camera shutter + flash pop
    sh = np.zeros(int(d * SR))
    for s in (0.0, 0.06):
        i = int(s * SR)
        c = bp(noise(0.02), 1500, 9000) * np.exp(-t_(0.02) * 220)
        add(sh, i, c)
    sh += 0.4 * hp(noise(d), 4000) * np.exp(-t_(d) * 14)
    save("shutter", sh)
    save("blip", np.sin(2 * np.pi * 1760 * t_(0.09)) * np.exp(-t_(0.09) * 45)
         + 0.3 * np.sin(2 * np.pi * 3520 * t_(0.09)) * np.exp(-t_(0.09) * 70))
    save("tick", bp(noise(0.01), 2500, 9000, 2) * np.exp(-t_(0.01) * 500) + 0.5 * np.sin(2 * np.pi * 3000 * t_(0.01)))
    d = 2.6  # jet pass: rising then falling filtered roar
    roar = np.concatenate([bp(seg, 150 + 900 * np.sin(np.pi * k / 30), 900 + 4000 * np.sin(np.pi * k / 30), 2)
                           for k, seg in enumerate(np.array_split(noise(d), 30))])
    save("jet_pass", roar * np.sin(np.pi * t_(d) / d) ** 1.5)
    tick = np.zeros(int(0.8 * SR))  # typing burst for decode / typewriter text
    for s in np.sort(rng.uniform(0, 0.7, 14)):
        add(tick, s * SR, bp(noise(0.012), 1800, 8000, 2) * np.exp(-t_(0.012) * 400) * rng.uniform(0.5, 1))
    save("type_burst", tick)
    d = 15.0  # film / vinyl crackle bed: faint hiss + sparse pops (loops)
    cr = 0.05 * hp(noise(d), 3000)
    for s in rng.uniform(0, d, int(d * 7)):
        add(cr, s * SR, hp(noise(0.004), 1200) * np.exp(-t_(0.004) * 900) * rng.uniform(0.3, 1.0) * 2)
    save("amb_crackle", loop_fade(cr), -6)
    d = 0.6  # air whoosh (soft, for plain cuts)
    wh = np.concatenate([bp(seg, 200 + 2500 * np.sin(np.pi * k / 20), 1200 + 6000 * np.sin(np.pi * k / 20), 2)
                         for k, seg in enumerate(np.array_split(noise(d), 20))])
    save("air", wh * np.sin(np.pi * t_(d) / d) ** 2)


def beds2():
    """More looping ambience beds. Only missing files are written: an open Premiere project locks the ones it uses.
    The noise stream restarts at seed 7, so a bed comes out the same with or without --all (build() first)."""
    global rng
    rng = np.random.default_rng(7)
    def keep(name, x):
        if not (OUT / f"{name}.wav").exists():
            save(name, loop_fade(x), -6)
    d = 16.0  # high-altitude wind: band noise with slow gusts and a whistle
    gust = 0.35 + 0.65 * np.clip(lp(np.abs(noise(d)), 0.25, 2) * 9, 0, 1)
    keep("amb_wind", (lp(brown(d), 700) * 0.6 + bp(noise(d), 600, 1800, 2) * 0.25) * gust
         + 0.03 * np.sin(2 * np.pi * np.cumsum(900 + 300 * gust) / SR) * gust)
    d = 16.0  # sea: waves swelling every ~4 s over a low wash
    swell = (0.5 + 0.5 * np.sin(2 * np.pi * t_(d) / 4.0 - np.pi / 2)) ** 2
    keep("amb_sea", lp(noise(d), 1200) * (0.25 + swell) + 0.3 * lp(brown(d), 200))
    d = 14.0  # river: steady broadband water with burbles
    wat = bp(noise(d), 300, 4000, 2) * 0.6
    for s in rng.uniform(0, d - 0.1, int(d * 30)):
        add(wat, s * SR, np.sin(2 * np.pi * rng.uniform(300, 900) * t_(0.05)) * np.exp(-t_(0.05) * 70) * 0.4)
    keep("amb_water", wat)
    d = 12.0  # teleprinter / typewriter room: irregular mechanical clatter with carriage returns
    tp = 0.04 * bp(noise(d), 100, 1500, 2)
    t = 0.0
    while t < d - 0.5:
        for _ in range(rng.integers(8, 30)):
            add(tp, t * SR, bp(noise(0.015), 1000, 6000, 2) * np.exp(-t_(0.015) * 300) * rng.uniform(0.4, 1))
            t += rng.uniform(0.06, 0.13)
        add(tp, t * SR, bp(noise(0.25), 300, 2500, 2) * np.exp(-t_(0.25) * 12) * 0.5)
        t += rng.uniform(0.4, 1.0)
    keep("amb_teleprinter", tp)
    d = 12.0  # radio static: hiss, crackle and a faint carrier
    rs = 0.25 * bp(noise(d), 800, 7000, 2) * (0.7 + 0.3 * np.sin(2 * np.pi * 0.3 * t_(d)))
    for s in rng.uniform(0, d, int(d * 10)):
        add(rs, s * SR, hp(noise(0.006), 1500) * rng.uniform(0.3, 1))
    keep("amb_radio", rs + 0.03 * np.sin(2 * np.pi * 1000 * t_(d)))


def tick_roll(name, frm, to, count, dur, max_rate=28):
    """Ticks following AE's easeOut counter (fast start, slow landing): one per change, at most max_rate per second."""
    if (OUT / f"{name}.wav").exists():  # deterministic, and an open Premiere project keeps the file locked
        return OUT / f"{name}.wav"
    tick = wavfile.read(OUT / "tick.wav")[1].astype(np.float64) / 2 ** 31
    out = np.zeros(int(dur * SR) + len(tick))
    last, step = -1.0, max(1, abs(to - frm) / (max_rate * count))
    for i in range(int(count * SR / 200)):
        u = i * 200 / SR / count
        v = frm + (to - frm) * (1 - (1 - u) ** 2)
        if abs(v - frm) // step != last:
            last = abs(v - frm) // step
            s = int(0.1 * SR + i * 200)
            add(out, s, tick * (0.5 + 0.5 * (1 - u)))
    return save(name, out[: int(dur * SR)], -3)


if __name__ == "__main__":
    build() if "--all" in sys.argv else None
    beds2()
    print(sorted(p.name for p in OUT.glob("*.wav")))
