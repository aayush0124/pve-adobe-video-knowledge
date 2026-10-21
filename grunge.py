"""Film-damage overlay with alpha (dust, scratches, hair) for laying over an edit: python grunge.py out.mov [seconds]
ProRes 4444 + alpha, 1920x1080 @ 25 fps, loops cleanly enough to repeat (damage is random, nothing persists)."""
import subprocess, sys

import numpy as np

W, H, FPS = 1920, 1080, 25


def frames(n, seed=3):
    rng = np.random.default_rng(seed)
    scratches = []  # [x, life, width, alpha, shade, drift, y0, y1]
    yy, xx = np.mgrid[0:9, 0:9]
    for f in range(n):
        img = np.zeros((H, W, 4), np.uint8)
        for _ in range(rng.poisson(11)):  # dust: soft specks, black or white, one frame each
            x, y, r = rng.integers(10, W - 10), rng.integers(10, H - 10), rng.uniform(1.5, 4.8)
            m = np.clip(1.6 - np.hypot(xx - 4, yy - 4) / r, 0, 1)
            shade = 255 if rng.random() < 0.35 else 0
            sl = img[y - 4:y + 5, x - 4:x + 5]
            sl[..., :3] = shade
            sl[..., 3] = np.maximum(sl[..., 3], (m * rng.uniform(150, 235)).astype(np.uint8))
        if rng.random() < 0.06:  # a fibre / hair for a few frames
            x0, y0 = rng.integers(200, W - 200), rng.integers(150, H - 150)
            t = np.linspace(0, 1, 400)
            px = (x0 + 140 * t + 30 * np.sin(t * rng.uniform(4, 9))).astype(int)
            py = (y0 + 90 * np.sin(t * rng.uniform(2, 5)) + 60 * t).astype(int)
            ok = (px >= 0) & (px < W) & (py >= 0) & (py < H)
            img[py[ok], px[ok], :3] = 10
            img[py[ok], px[ok], 3] = 200
        if rng.random() < 0.18 and len(scratches) < 3:  # vertical scratches that drift and live several frames
            y0 = 0 if rng.random() < 0.6 else rng.integers(0, H // 2)
            scratches.append([float(rng.integers(40, W - 40)), int(rng.integers(5, 26)), int(rng.integers(1, 3)),
                              rng.uniform(70, 150), 255 if rng.random() < 0.7 else 0, rng.uniform(-1.5, 1.5),
                              y0, H if y0 == 0 else rng.integers(H // 2, H)])
        for s in scratches:
            x = int(s[0])
            img[s[6]:s[7], x:x + s[2], :3] = s[4]
            img[s[6]:s[7], x:x + s[2], 3] = int(s[3] * rng.uniform(0.6, 1.0))
            s[0] += s[5] + rng.uniform(-0.6, 0.6)
            s[1] -= 1
        scratches = [s for s in scratches if s[1] > 0 and 0 < s[0] < W - 3]
        yield img


def main(out, seconds=12.0):
    n = int(seconds * FPS)
    if n < 1:  # ffmpeg exits 0 on zero frames and leaves an empty .mov
        sys.exit(f"seconds {seconds:g} is too short: need at least one frame (1/{FPS} s)")
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(FPS),
                          "-i", "-", "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", out], stdin=subprocess.PIPE)
    for img in frames(n):
        p.stdin.write(img.tobytes())
    p.stdin.close()
    if p.wait():
        sys.exit(f"ffmpeg failed (exit {p.returncode}) writing {out}")
    return out


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 12.0)
