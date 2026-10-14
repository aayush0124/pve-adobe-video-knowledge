"""Build the house .mogrt templates in After Effects: python make_house_mogrts.py -> mgt/house/*.mogrt"""
import json, sys
from pathlib import Path
from adobe_bridge import HERE, ae_notes, restart_ae, run_job

out = HERE / "mgt" / "house"
out.mkdir(parents=True, exist_ok=True)
aep, n = out / "house_mogrts.aep", 1
while aep.exists():
    n += 1
    aep = out / f"house_mogrts_v{n}.aep"
H = {"out_dir": out.as_posix(), "aep": aep.as_posix(), "pre_aep": aep.with_name(aep.stem + "_pre.aep").as_posix()}
import ctypes, threading, time
from adobe_bridge import _ae_dialogs


def click_ok():  # the export stops on "fonts were not synced from Adobe ... OK to continue" (local fonts)
    t0 = time.time()
    while time.time() - t0 < 280 and not aep.exists():
        for h, w, hh in _ae_dialogs():
            if w > 450 and hh > 300:
                for m in (0x100, 0x101):
                    ctypes.windll.user32.PostMessageW(h, m, 0x0D, 0)
        time.sleep(2)


threading.Thread(target=click_ok, daemon=True).start()
run_job("var H = " + json.dumps(H) + ";\n" + (HERE / "ae_lib.jsx").read_text(encoding="ascii")
        + (HERE / "house_mogrts.jsx").read_text(encoding="ascii"), aep.with_name(aep.stem + "_job.jsx"), aep, 300)
for n in ae_notes(aep.read_bytes()):
    print(n)
print([p.name for p in out.glob("*.mogrt")])
restart_ae()  # after a template export AE ignores every later -r script (the Premiere relay too)
