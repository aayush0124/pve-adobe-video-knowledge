"""PVE: harvest Premiere Pro's effects, transitions and audio effects from the live app -> pve/harvest/pr/*.json
plus fx_gallery.mp4 (every video effect at defaults, 2 s each, clip 0 untouched) and tr_gallery.mp4 (every transition
on a cut every 4 s).

    python pve/harvest_pr.py [--no-render] [--video-only]

The job goes to Premiere over BridgeTalk (relayed by After Effects, as in adobe_bridge). Premiere writes its results
after every item, so a crash leaves a breadcrumb: the last record in pr_video_effects.json etc.

Test media: pve/media/test_a.mp4, test_b.mp4 and test_vo.wav. Missing ones are generated synthetically
(make_test_media.py); drop your own clips at those paths to harvest and render on real footage.
"""
import json, sys, time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adobe_bridge as ab  # noqa: E402
import make_test_media  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "harvest" / "pr"
MEDIA = HERE / "media"


def main(render=True, video_only=False):
    OUT.mkdir(parents=True, exist_ok=True)
    MEDIA.mkdir(exist_ok=True)
    make_test_media.main()  # writes only what is missing
    for f in OUT.glob("pr_*.json"):
        if not video_only or f.name in ("pr_video_effects.json", "pr_done.json"):
            f.unlink()
    n = 1
    while (OUT / f"pve_harvest_{n}.prproj").exists():
        n += 1
    pv = {"out_dir": OUT.as_posix(), "prproj": (OUT / f"pve_harvest_{n}.prproj").as_posix(), "render": render,
          "a": (MEDIA / "test_a.mp4").as_posix(), "b": (MEDIA / "test_b.mp4").as_posix(), "wav": (MEDIA / "test_vo.wav").as_posix(),
          "a_name": "test_a.mp4", "b_name": "test_b.mp4", "wav_name": "test_vo.wav", "epr": ab.fwd(ab.EPR),
          "video_only": video_only}
    body = quote("var PV = " + json.dumps(pv) + ";\n" + (HERE / "harvest_pr.jsx").read_text(encoding="ascii"), safe="")
    relay = ('if (!BridgeTalk.isRunning("premierepro")) BridgeTalk.launch("premierepro");\n'
             'var bt = new BridgeTalk(); bt.target = "premierepro";\n'
             "bt.body = 'eval(decodeURIComponent(\"%s\"))'; bt.send();\n" % body)
    for attempt in range(2):
        ab.run_in_ae(relay, OUT / "_relay.jsx")
        t0 = time.time()
        fresh = lambda: (OUT / "pr_lists.json").exists() and (OUT / "pr_lists.json").stat().st_mtime > t0 - 1  # noqa: E731
        while time.time() - t0 < 90 and not fresh():  # this job rewrites the lists first thing
            time.sleep(1)
        if fresh():
            break
        print("Premiere did not pick up the job; restarting AE and resending", flush=True)
        ab.restart_ae()
    else:
        sys.exit("Premiere never received the harvest job")
    last, t0 = None, time.time()
    while not (OUT / "pr_done.json").exists():
        crumbs = {f.name: f.stat().st_mtime for f in OUT.glob("pr_*.json")}
        if crumbs != last:
            last, t0 = crumbs, time.time()
        elif time.time() - t0 > 900:
            sys.exit(f"no progress for 15 min; breadcrumbs: {sorted(crumbs)}")
        time.sleep(5)
    done = json.loads((OUT / "pr_done.json").read_text(encoding="utf-8"))
    for name in ("pr_lists", "pr_video_effects", "pr_audio_effects", "pr_transitions"):
        p = OUT / f"{name}.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            print(name, {k: len(v) for k, v in d.items()} if isinstance(d, dict) else len(d),
                  "errors:", sum(1 for r in d if isinstance(r, dict) and r.get("error")) if isinstance(d, list) else "-")
    print("done:", done)


if __name__ == "__main__":
    main("--no-render" not in sys.argv, "--video-only" in sys.argv)
