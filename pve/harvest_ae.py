"""PVE: harvest every After Effects effect's properties from the live app -> pve/harvest/ae_effects.json

    python pve/harvest_ae.py [batch_size]

AE scripts may not write files here, so each batch ships its JSON to Premiere over BridgeTalk and Premiere writes it
(Premiere must be running). A batch file that never arrives means AE crashed or went deaf: restart AE, then walk that
batch one effect at a time so the culprit is recorded as skipped instead of stalling the whole harvest.
"""
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adobe_bridge as ab  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "harvest" / "ae"
JSX = (HERE / "harvest_ae.jsx").read_text(encoding="ascii")


def run(i0, i1, skip, timeout=120):
    out = OUT / f"batch_{i0:04d}_{i1:04d}.json"
    out.unlink(missing_ok=True)
    hv = {"i0": i0, "i1": i1, "out": out.as_posix(), "scratch": (OUT / f"_scratch_{i0:04d}.aep").as_posix(),
          "skip": {str(m): True for m in skip}}
    ab.run_in_ae("var HV = " + json.dumps(hv) + ";\n" + JSX, OUT / f"_job_{i0:04d}.jsx")
    t0 = time.time()
    while time.time() - t0 < timeout:
        if out.exists() and out.stat().st_size:
            time.sleep(1)
            return json.loads(out.read_text(encoding="utf-8"))
        time.sleep(1)
    return None


def main(batch=15):
    OUT.mkdir(parents=True, exist_ok=True)
    skip_file = OUT / "skip.json"
    skip = set(json.loads(skip_file.read_text())) if skip_file.exists() else set()  # effect indices
    first = run(0, 1, skip)
    if first is None:
        ab.restart_ae()
        first = run(0, 1, skip)
    total = first["count"]
    print(f"After Effects has {total} effects", flush=True)
    effects = {}
    i = 0
    while i < total:
        j = min(total, i + batch)
        got = run(i, j, skip)
        if got is None:  # walk the batch one by one to find what kills AE
            print(f"batch {i}-{j} did not come back; restarting AE and walking it", flush=True)
            ab.restart_ae()
            for k in range(i, j):
                one = run(k, k + 1, skip, timeout=90)
                if one is None:
                    print(f"  effect {k} kills AE; skipping it", flush=True)
                    skip.add(k)
                    skip_file.write_text(json.dumps(sorted(skip)))
                    ab.restart_ae()
                    one = run(k, k + 1, skip, timeout=90)  # records its name with the skip note
                    effects[k] = one["effects"][0] if one else {"i": k, "error": "crashed AE during harvest"}
                else:
                    for e in one["effects"]:
                        effects[e["i"]] = e
        else:
            for e in got["effects"]:
                effects[e["i"]] = e
        print(f"{min(j, total)}/{total}", flush=True)
        i = j
    res = [effects[k] for k in sorted(effects)]
    (HERE / "harvest" / "ae_effects.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    bad = [e for e in res if e.get("error")]
    print(f"done: {len(res)} effects, {len(bad)} with errors")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 15)
