"""Answer Premiere's modal prompts: python pve/pr_dialog_watchdog.py [seconds] [--any]
Default: only the ~491x257 "Project Modified ... continue?" prompt (a project re-created at a path Premiere still has
open) gets Escape = Cancel. --any: every untitled #32770 dialog (harvests: "Audio Effects Replacement" -> No).
Never use --any during renders: it would also hit progress windows."""
import ctypes, ctypes.wintypes as wt, subprocess, sys, time


def dialogs():
    u = ctypes.windll.user32
    pids = {int(r.split('","')[1]) for r in subprocess.run(
        ["tasklist", "/fo", "csv", "/fi", "imagename eq Adobe Premiere Pro.exe"], capture_output=True, text=True).stdout.splitlines()[1:]}
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        pid, cls, r = wt.DWORD(), ctypes.create_unicode_buffer(64), wt.RECT()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        u.GetClassNameW(h, cls, 64)
        u.GetWindowRect(h, ctypes.byref(r))
        if pid.value in pids and u.IsWindowVisible(h) and cls.value == "#32770":
            out.append((h, r.right - r.left, r.bottom - r.top))
        return True

    u.EnumWindows(cb, 0)
    return out


def main(seconds=3600, any_dialog=False):
    u, t0 = ctypes.windll.user32, time.time()
    while time.time() - t0 < seconds:
        for h, w, hh in dialogs():
            if not any_dialog and not (480 <= w <= 505 and 245 <= hh <= 270):
                continue
            print(time.strftime("%X"), "escape", h, flush=True)
            for msg in (0x100, 0x101):
                u.PostMessageW(h, msg, 0x1B, 0)
        time.sleep(2)


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] != "--any" else 3600, "--any" in sys.argv)
