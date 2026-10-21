"""PVE / AdobeBridge pipeline: one entry point for every stage.

    python pipeline.py status            counts per stage; exit 0 ok, 10 needs attention
    python pipeline.py run <stage>       one stage (live / agent stages print how to run them; live ones run with PVE_LIVE=1)
    python pipeline.py run batches --force   rebuild the agent batches (only before new cards are written: see below)
    python pipeline.py doctor            Python, tools, app paths, free RAM
    python pipeline.py all               every deterministic stage in order: batches, validate, docs

Stages (kind): harvest (live), batches, cards (agent), validate, gallery (live), verify (agent), recipes (agent),
docs, build (live bridge smoke test). Exit codes: 0 ok, 1 failure, 10 needs attention.
"""
import contextlib, io, json, os, platform, re, shutil, subprocess, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pve"))
import adobe_bridge as ab  # noqa: E402
import build_docs  # noqa: E402
import pve  # noqa: E402  (pve/pve.py: PVE_HOME moves the knowledge base)
import validate_cards  # noqa: E402

PVE = pve.HERE
BATCHES = PVE / "work" / "batches"
LIVE = os.environ.get("PVE_LIVE") == "1"
PY = sys.executable

LIVE_STAGES = {
    "harvest": [[PY, "pve/harvest_files.py"], [PY, "pve/harvest_ae.py"], [PY, "pve/harvest_pr.py"]],
    "gallery": [[PY, "pve/gallery.py", k] for k in ("ae", "pr", "tr", "presets", "recipes")],
    "build": [[PY, "make_test_media.py"], [PY, "adobe_bridge.py", "example/spec.json"]],
}
AGENT_STAGES = {
    "cards": "one agent per pve/work/batches/<b>.json writes pve/cards/<b>.json from that batch only, then runs "
             "`python pve/validate_cards.py pve/work/batches/<b>.json pve/cards/<b>.json` until 0 issues "
             "(the original card-writing workflow is not in this repo)",
    "verify": "Workflow pve/verify_wave.js, args {\"root\": \"%s\", \"batches\": [<batch names>]} (needs the gallery)",
    "recipes": "Workflow pve/recipes_wave.js, args {\"root\": \"%s\"} (add \"skip_critic\" / \"only_critic\")",
}
STAGES = ["harvest", "batches", "cards", "validate", "gallery", "verify", "recipes", "docs", "build"]


def child_env():
    """The stage scripts run with cwd=ROOT: hand them the PVE_HOME this process resolved (a relative one would
    otherwise resolve against ROOT instead of the caller's folder)."""
    return dict(os.environ, PVE_HOME=str(PVE))


def pairs():
    """(batch file, card file) for every agent batch."""
    return [(b, PVE / "cards" / b.name) for b in sorted(BATCHES.glob("*.json"))]


def validate():
    """validate_cards over every batch, every card / harvest file loads, and the pve.py pre-flight over every recipe
    (each RECIPE_STACKS stack, its spec and its scenes) -> list of issue lines."""
    kb = pve.kb_problems()  # every card and harvest file loads; reported once here, not once per recipe spec
    issues = list(kb) + ([] if pairs() else [f"no batch files in {BATCHES.as_posix()} (python pipeline.py run batches)"])
    for b, c in pairs():
        if not c.exists():
            issues.append(f"{c.name}: no card file for batch {b.name}")
            continue
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = validate_cards.main(b, c)
        if rc:  # every line but validate_cards' "N cards, M issues" summary (an unreadable file prints no summary)
            lines = [x for x in buf.getvalue().splitlines() if not re.fullmatch(r"\d+ cards, \d+ issues", x)]
            issues += [f"{c.name}: {x}" for x in lines or [f"validate_cards exit {rc}"]]
    for r in (x for x in pve.cards() if x.get("kind") == "recipe"):  # every RECIPE_STACKS stack, its spec and scenes
        issues += [f"recipe {r['name']!r}: {x}" for x in pve.check_recipe(r)
                   if not pve.advisory(x) and x not in kb]  # "check by eye" is for builds
    return issues


def verdict_tally(cs):
    """'pass 1100, ..., untested 43 (gpu_only 12, ...)': untested:<cause> grouped so the row stays readable."""
    v = Counter((c.get("verified") or {}).get("verdict") or "unverified" for c in cs)
    un = sorted((k.split(":", 1)[1], n) for k, n in v.items() if k.startswith("untested:"))
    row = ", ".join(f"{k} {n}" for k, n in sorted(v.items()) if not k.startswith("untested:"))
    return row + (f", untested {sum(n for _, n in un)} ({', '.join(f'{k} {n}' for k, n in un)})" if un else "")


def docs_fresh():
    p = PVE / "PVE.md"
    return p.exists() and p.read_text(encoding="utf-8") == build_docs.render()[0]


def status():
    cs = pve.cards()
    files = pve._harvest("harvest/files.json") or {}  # a missing or corrupt file counts 0 here; validate names it
    lists = pve._harvest("harvest/pr/pr_lists.json") or {}
    harvested = {"ae_effects": len(pve.ae_effects()), "pr_video_effects": len(lists.get("video_effects", [])),
                 "pr_transitions": len(lists.get("video_transitions", [])), "pr_audio_effects": len(lists.get("audio_effects", [])),
                 **{k: len(v) for k, v in files.items() if isinstance(v, list)}}
    batch_items = sum(len(json.loads(b.read_text(encoding="utf-8"))["items"]) for b, _ in pairs())
    issues = validate()
    gal = PVE / "gallery"
    frames = {d.name: sum(1 for _ in d.glob("*.png")) for d in sorted(gal.iterdir()) if d.is_dir()} if gal.exists() else {}
    media = [p for p in ("example/clip_a.mp4", "example/clip_b.mp4", "example/music.mp3", "example/still.png",
                         "pve/media/test_a.mp4", "pve/media/test_b.mp4", "pve/media/test_vo.wav") if not (ROOT / p).exists()]
    fresh = docs_fresh()
    ae = [{"verified": c["verified"]["ae"]} for c in cs if (c.get("verified") or {}).get("ae")]  # verified.ae
    rows = [
        ("harvest", "live", ", ".join(f"{k} {v}" for k, v in harvested.items())),
        ("batches", "det", f"{len(pairs())} batch files, {batch_items} items"),
        ("cards", "agent", f"{len(cs)} cards: " + ", ".join(f"{k} {v}" for k, v in sorted(Counter(c.get('kind') for c in cs).items()))),
        ("validate", "det", "ok, 0 issues" if not issues else f"{len(issues)} ISSUES (python pipeline.py run validate)"),
        ("gallery", "live", ", ".join(f"{k} {v}" for k, v in frames.items()) + " frames" if frames else "no frames rendered here"),
        ("verify", "agent", verdict_tally(cs) + (f"; recipe After Effects halves: {verdict_tally(ae)}" if ae else "")),
        ("recipes", "agent", f"{sum(1 for c in cs if c.get('kind') == 'recipe')} recipes"),
        ("docs", "det", "pve/PVE.md fresh" if fresh else "pve/PVE.md STALE (python pipeline.py run docs)"),
        ("build", "live", "test media present" if not media else f"missing test media {media} (python make_test_media.py)"),
    ]
    for name, kind, what in rows:
        print(f"{name:9} {kind:5} {what}")
    return 10 if issues or not fresh or not pairs() else 0


def run(stage, force=False):
    if stage in LIVE_STAGES:
        cmds = LIVE_STAGES[stage]
        if not LIVE:
            print(f"{stage} needs After Effects / Premiere Pro running on this machine. Set PVE_LIVE=1 to run:")
            print("\n".join("  " + " ".join(c[1:]) for c in cmds))
            return 10
        for c in cmds:
            if subprocess.run(c, cwd=ROOT).returncode:
                return 1
        return 0
    if stage in AGENT_STAGES:
        print(f"{stage} is an LLM-agent stage, run outside this script:\n  " + AGENT_STAGES[stage].replace("%s", PVE.as_posix()))
        return 10
    if stage == "batches":
        if pairs() and not force:  # the cards were written against these exact batches (chunking included)
            print(f"batches: {len(pairs())} batch files already exist and cards were written against them; kept "
                  "(--force re-cuts them from the harvest, keeping the frozen Premiere ones; then re-validate every card file)")
            return 0
        return subprocess.run([PY, "pve/prepare_batches.py"] + ["--force"] * force, cwd=ROOT, env=child_env()).returncode and 1
    if stage == "validate":
        issues = validate()
        print("\n".join(issues) or f"validate: {len(pairs())} card files and every recipe clean")
        return 1 if issues else 0
    if stage == "docs":
        if docs_fresh():
            print("docs: pve/PVE.md already fresh")
            return 0
        return subprocess.run([PY, "pve/build_docs.py"], cwd=ROOT, env=child_env()).returncode and 1
    print(f"unknown stage {stage!r}; stages: {', '.join(STAGES)}")
    return 1


def free_ram_gb():
    if platform.system() == "Windows":
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in (
                "total", "avail", "tpage", "apage", "tvirt", "avirt", "aext")]
        m = MS()
        m.len = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.avail / 2 ** 30
    with contextlib.suppress(OSError, ValueError, IndexError):
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable"):
                return int(line.split()[1]) / 2 ** 20
    return None


def doctor():
    ok = sys.version_info >= (3, 10)
    ram = free_ram_gb()
    rows = [("python", f"{platform.python_version()} ({'ok' if ok else 'need 3.10+'})")]
    rows += [(t, shutil.which(t) or "MISSING") for t in ("ffmpeg", "ffprobe", "node", "git")]
    apps = {"After Effects (PVE_AFTERFX)": ab.AFTERFX, "aerender": ab.AERENDER, "AE presets": ab.PRESETS,
            "Premiere Pro (PVE_PREMIERE)": ab.PREMIERE, "Media Encoder preset": ab.EPR}
    rows += [(k, f"{v} ({'found' if Path(v).exists() else 'not found'})") for k, v in apps.items()]
    rows += [("PVE_HOME", f"{PVE}{' (env)' if os.environ.get('PVE_HOME') else ''}"),
             ("free RAM", f"{ram:.1f} GB" if ram is not None else "unknown"), ("PVE_LIVE", "1" if LIVE else "off")]
    for k, v in rows:
        print(f"{k:28} {v}")
    if not ok:
        return 1
    return 0 if all(Path(v).exists() for v in apps.values()) and shutil.which("ffprobe") else 10


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "status":
        return status()
    if cmd == "doctor":
        return doctor()
    if cmd == "run" and len(argv) > 1:
        return run(argv[1], "--force" in argv)
    if cmd == "all":
        worst = 0
        for s in ("batches", "validate", "docs"):
            rc = run(s)
            worst = 1 if rc == 1 or worst == 1 else max(worst, rc)
        return worst
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
