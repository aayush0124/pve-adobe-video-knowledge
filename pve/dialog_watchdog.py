"""Cancel the modal dialogs some effects open when applied during a harvest (Apply Color LUT asks for a LUT file,
the legacy Basic Text / Path Text / Numbers effects ask for text): python pve/dialog_watchdog.py [seconds].
Escapes every TITLED After Effects dialog except the "Executing Script" progress window; untitled ones (splash,
Crash Repair, script errors) are left to the harvest driver."""
import ctypes, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adobe_bridge as ab  # noqa: E402

def main(seconds=3600):
    u, t0 = ctypes.windll.user32, time.time()
    while time.time() - t0 < seconds:
        for h, w, hh in ab._ae_dialogs():
            buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(h, buf, 256)
            if buf.value and not buf.value.startswith("Executing Script"):
                print(time.strftime("%X"), "cancel:", buf.value, flush=True)
                for msg in (0x100, 0x101):
                    u.PostMessageW(h, msg, 0x1B, 0)  # Escape = Cancel
        time.sleep(2)


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 3600)
