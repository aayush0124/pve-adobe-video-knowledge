"""Loudness master for a delivered render: python master_audio.py in.mp4 [out.mp4] [--lufs -14] [--tp -1.5]

Two-pass EBU R128 loudnorm (measure, then apply with the measured values, linear when the range allows) to the
YouTube target (-14 LUFS integrated, true peak <= -1 dBTP; -1.5 leaves headroom for the platform's AAC re-encode).
The video stream is copied untouched; audio is re-encoded to AAC 320k 48 kHz. Prints before/after and exits 1 if the
result misses the target by more than 1 LU or peaks above the ceiling.
"""
import json, re, subprocess, sys
from pathlib import Path


def measure(src, lufs, tp, lra=11):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-af",
                        f"loudnorm=I={lufs}:TP={tp}:LRA={lra}:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    return json.loads(re.findall(r"\{[^{}]+\}", r.stderr)[-1])


def main(argv):
    args, opt, it = [], {}, iter(argv)
    for a in it:
        if a.startswith("--"):
            opt[a] = next(it)
        else:
            args.append(a)
    lufs, tp = float(opt.get("--lufs", -14)), float(opt.get("--tp", -1.5))
    src = Path(args[0])
    out = Path(args[1]) if len(args) > 1 else src.with_name(src.stem + "_master.mp4")
    m = measure(src, lufs, tp)
    lra = max(11, float(m["input_lra"]) + 1)  # a target LRA below the input's forces dynamic (non-linear) mode
    af = (f"loudnorm=I={lufs}:TP={tp}:LRA={lra}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,"
          f"aresample=48000")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v", "-map", "0:a", "-c:v", "copy",
                    "-af", af, "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-movflags", "+faststart", str(out)], check=True)
    a = measure(out, lufs, tp)
    print(f"before: {m['input_i']} LUFS, TP {m['input_tp']} dBTP, LRA {m['input_lra']}")
    print(f"after:  {a['input_i']} LUFS, TP {a['input_tp']} dBTP, LRA {a['input_lra']}  -> {out}")
    ok = abs(float(a["input_i"]) - lufs) <= 1.0 and float(a["input_tp"]) <= tp + 0.5
    if not ok:
        print("MISSED the target: check the mix (a few very loud hits can force the limiter)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
