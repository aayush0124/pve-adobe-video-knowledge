"""Docs rebuild reproducibly, the README coverage block is the live `pipeline.py status` output, and pipeline.py
keeps its contract (exit codes 0 / 1 / 10, live and agent stages never run here)."""
import os, re, subprocess, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pve"))
import build_docs  # noqa: E402

MACHINE_ROWS = ("gallery", "build")  # depend on what is rendered / downloaded on this machine


def pipeline(*args, **env):
    e = {k: v for k, v in os.environ.items() if k != "PVE_LIVE"}
    e.update(env)
    r = subprocess.run([sys.executable, "pipeline.py", *args], cwd=ROOT, capture_output=True, text=True, env=e,
                       encoding="utf-8")
    return r.returncode, r.stdout


class Docs(unittest.TestCase):
    def test_pve_md_is_fresh(self):
        text, n, _ = build_docs.render()
        self.assertEqual((ROOT / "pve" / "PVE.md").read_text(encoding="utf-8"), text,
                         "pve/PVE.md is stale: python pipeline.py run docs")
        self.assertGreater(n, 2000)

    def test_legend_defines_every_verdict(self):
        md = (ROOT / "pve" / "PVE.md").read_text(encoding="utf-8")
        legend = md.split("## Render verdicts", 1)[1].split("\n## ", 1)[0]
        self.assertIn('pass = matched its render, not "works"', legend)
        for v in build_docs.VERDICTS:
            self.assertIn(f"- **{v}**: ", legend)
        for cause in build_docs.UNTESTED:
            self.assertIn(f"`untested:{cause}`", legend)
        self.assertIn("## Photosensitivity", md)

    def test_readme_coverage_is_live(self):
        m = re.search(r"<!-- status:begin -->\n```\n(.*?)```\n<!-- status:end -->", (ROOT / "README.md").read_text(encoding="utf-8"), re.S)
        self.assertTrue(m, "README.md lost its status block")
        _, out = pipeline("status")
        live = [l for l in out.splitlines() if not l.startswith(MACHINE_ROWS)]
        self.assertEqual(m.group(1).splitlines(), live, "README coverage differs from `python pipeline.py status`")


class Pipeline(unittest.TestCase):
    def test_status(self):
        rc, out = pipeline("status")
        self.assertEqual(rc, 0, out)
        stages = [l.split()[0] for l in out.splitlines()]
        self.assertEqual(stages, ["harvest", "batches", "cards", "validate", "gallery", "verify", "recipes", "docs", "build"])
        self.assertIn("validate  det   ok, 0 issues", out)

    def test_deterministic_stages(self):
        for stage in ("batches", "validate", "docs"):
            rc, out = pipeline("run", stage)
            self.assertEqual(rc, 0, (stage, out))
        self.assertIn("kept", pipeline("run", "batches")[1])  # never re-cuts batches the cards were written against
        self.assertEqual(pipeline("all")[0], 0)

    def test_live_and_agent_stages_only_explain(self):
        for stage, needle in (("harvest", "harvest_ae.py"), ("gallery", "gallery.py"), ("build", "adobe_bridge.py"),
                              ("verify", "verify_wave.js"), ("recipes", "recipes_wave.js"), ("cards", "validate_cards.py")):
            rc, out = pipeline("run", stage)
            self.assertEqual(rc, 10, stage)
            self.assertIn(needle, out)

    def test_bad_usage_and_doctor(self):
        self.assertEqual(pipeline("run", "nope")[0], 1)
        self.assertEqual(pipeline()[0], 0)
        rc, out = pipeline("doctor")
        self.assertIn(rc, (0, 10))
        self.assertIn("python", out)


if __name__ == "__main__":
    unittest.main()
