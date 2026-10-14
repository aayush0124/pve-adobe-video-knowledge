"""Live tests: drive the real After Effects 2026 + Premiere Pro 2026. Opt-in only: PVE_LIVE=1 python -m unittest tests.test_live

Builds example/spec.json in both apps (several minutes, opens both apps, writes example/demo_adobe/). Never in CI.
"""
import json, os, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@unittest.skipUnless(os.environ.get("PVE_LIVE") == "1", "live app test: set PVE_LIVE=1")
class LiveBuild(unittest.TestCase):
    def test_example_spec_builds_in_both_apps(self):
        import adobe_bridge
        import make_test_media
        make_test_media.main()
        spec = ROOT / "example" / "spec.json"
        res = adobe_bridge.build(json.loads(spec.read_text(encoding="utf-8")), spec.parent)
        self.assertTrue(Path(res["aep"]).exists() and Path(res["prproj"]).exists(), res)
        self.assertFalse([w for w in res["warnings"] if w.startswith("PVE ")], res["warnings"])


if __name__ == "__main__":
    unittest.main()
