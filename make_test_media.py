"""Synthetic test media (ffmpeg lavfi sources only, nothing downloaded): python make_test_media.py [root]

Writes whatever is missing, never overwrites (put your own footage at these paths to test on real pictures):

    example/clip_a.mp4    testsrc2, 1920x1080 30 fps 6 s + mono 440 Hz tone   (test_adobe_bridge.py, example specs)
    example/clip_b.mp4    mandelbrot, 1280x720 30 fps 6 s, no audio
    example/music.mp3     12 s stereo two-tone bed
    example/still.png     1080x1350 orange -> blue gradient
    pve/media/test_a.mp4  testsrc2, 1920x1080 25 fps 3.96 s     (harvest_pr.py, gallery.py)
    pve/media/test_b.mp4  mandelbrot, 1920x1080 25 fps 5.64 s
    pve/media/test_vo.wav 29.3 s, 32 kHz mono, a pitch- and level-modulated tone standing in for speech
    example/parallax/bg_clean_plate.png   1920x1080 blue gradient + grid (the '2.5D photo parallax' background)
    example/parallax/subject_cutout.png   1920x1080 RGBA, a gridded orange-purple disc on transparency (straight alpha)
    example/greenscreen/presenter.mp4     1920x1080 30 fps 6 s: that disc drifting over chroma green #00B140, with a
                                          24 px feathered edge where the green mixes in (the green-screen recipe)

Second set, pve/media/set2/ (25 fps, 5 s, 1920x1080 unless noted): footage the first set cannot test
(pve/live_tests.py footage):

    handheld_shake.mp4      per-frame random shake (crop jitter) with directional motion blur on frames n%4==1 and
                            n%7==5, sharp in between (Camera-Shake Deblur, stabilizers)
    rolling_shutter_pan.mp4 300 px/s pan over vertical bars, rows sheared 48 +-18 px top to bottom (rolling-shutter
                            skew with jello wobble: Rolling Shutter Repair, phone-pan recipes)
    interlaced_25i.mp4      1080i25 top field first, a 600 px/s pan made from 50p: combing on every edge in motion
    archive_4x3.mp4         640x480 square pixels, 4:1:1 chroma and MPEG-2 8x8 blocks baked in (archive / DV cleanup,
                            upscale recipes)
    vfr_phone.mp4           nominal 30 fps with frames dropped irregularly (n%9==4, n%13==7): variable frame rate
    locked_off_motion.mp4   static gradient + grid plate, a disc and a box moving across it; no camera move, no cut
    noisy_lowlight.mp4      that clip darkened, with heavy temporal grain (denoise / grain tools)
    presenter_hair.mp4      a disc on chroma green #00B140 with fine semi-transparent strands at its top edge and
                            green spill on its rim (keyers, spill, matte choke / refine)
    dust_wire.mp4           the static plate with a dark 4 px 'wire' from (700, 0) to (760, 1080) and single-pixel
                            white / black specks that change every frame (dust & scratches, wire-removal recipes)
    talking_head.mp4        6 s: a drawn face-like oval swaying on a plate, its mouth box opening and closing (a jump
                            cut out of it is the Morph Cut test)

test_adobe_bridge.py depends on these sizes and durations (cover-fit of the 1080x1350 still, clip_a's 6 s, the
12 s music bed), so keep them if you swap the pictures.
"""
import os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VO = "0.25*sin(2*PI*(130+25*sin(2*PI*0.7*t))*t)*(0.55+0.45*sin(2*PI*3.1*t))*abs(sin(2*PI*0.4*t))"  # no commas: lavfi
# a 700 px gridded orange-purple disc (no green in it, so a keyer leaves it alone) with a 24 px feathered alpha edge
DISC = (r"gradients=s=700x700:r=30:c0=0xE07A3A:c1=0x6A2C91:speed=0.02,drawgrid=w=35:h=35:t=1:c=0xF0E0D0@0.8,format=rgba,"
        r"geq=r='r(X\,Y)':g='g(X\,Y)':b='b(X\,Y)':a='255*clip((330-hypot(X-350\,Y-350))/24\,0\,1)'")
MEDIA = {
    "example/clip_a.mp4": ["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30", "-f", "lavfi",
                           "-i", "sine=frequency=440:sample_rate=44100", "-t", "6", "-ac", "1",
                           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"],
    "example/clip_b.mp4": ["-f", "lavfi", "-i", "mandelbrot=size=1280x720:rate=30", "-t", "6",
                           "-c:v", "libx264", "-pix_fmt", "yuv420p"],
    "example/music.mp3": ["-f", "lavfi", "-i", "aevalsrc=0.2*sin(2*PI*220*t)+0.12*sin(2*PI*330*t):s=44100:d=12",
                          "-ac", "2", "-c:a", "libmp3lame", "-q:a", "4"],
    "example/still.png": ["-f", "lavfi", "-i", "gradients=s=1080x1350:c0=0xFF7A1A:c1=0x2050E0:x0=1080:y0=0:x1=0:y1=1350:"
                          "nb_colors=2", "-frames:v", "1"],
    "pve/media/test_a.mp4": ["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=25", "-t", "3.96",
                             "-c:v", "libx264", "-pix_fmt", "yuv420p"],
    "pve/media/test_b.mp4": ["-f", "lavfi", "-i", "mandelbrot=size=1920x1080:rate=25", "-t", "5.64",
                             "-c:v", "libx264", "-pix_fmt", "yuv420p"],
    "pve/media/test_vo.wav": ["-f", "lavfi", "-i", f"aevalsrc={VO}:s=32000:d=29.30125", "-ac", "1", "-c:a", "pcm_s16le"],
    "example/parallax/bg_clean_plate.png": ["-f", "lavfi", "-i", "gradients=s=1920x1080:c0=0x1B3A5C:c1=0xC9D6E3:x0=0:y0=0:"
                                            "x1=0:y1=1080:nb_colors=2,drawgrid=w=120:h=120:t=2:c=white@0.35,format=rgb24", "-frames:v", "1"],
    "example/parallax/subject_cutout.png": ["-f", "lavfi", "-i", f"color=c=black@0:s=1920x1080,format=rgba[c];{DISC}[d];"
                                            "[c][d]overlay=x=610:y=250", "-frames:v", "1"],
    "example/greenscreen/presenter.mp4": ["-f", "lavfi", "-i", f"color=c=0x00B140:s=1920x1080:r=30:d=6[c];{DISC}[d];"
                                          "[c][d]overlay=x='610+120*sin(t)':y=190:shortest=1,format=yuv420p",
                                          "-t", "6", "-c:v", "libx264"],
}
X264 = ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p"]
WIDE = "testsrc2=size=3600x1080:rate={r},noise=alls=8:all_seed=5,drawgrid=w=160:h=1080:t=8:c=white"  # fixed grain
SKEW = "(48+18*sin(on*0.35))"  # rolling-shutter shear in px (bottom row vs top), wobbling frame to frame
PLATE = ("gradients=s=1920x1080:c0=0x1B3A5C:c1=0xC9D6E3:x0=0:y0=0:x1=0:y1=1080:nb_colors=2,"
         "drawgrid=w=120:h=120:t=2:c=white@0.35,format=rgb24")
BALL = (r"gradients=s=300x300:r=25:c0=0xE07A3A:c1=0x6A2C91:speed=0.02,format=rgba,"
        r"geq=r='r(X\,Y)':g='g(X\,Y)':b='b(X\,Y)':a='255*clip((140-hypot(X-150\,Y-150))/6\,0\,1)'")
_R = r"hypot(X-400\,Y-430)"
HAIR = (r"color=c=black@0:s=800x800,format=rgba,geq="
        rf"r='if(lt({_R}\,330)\,224-120*Y/800\,58)':g='if(lt({_R}\,330)\,122-80*Y/800+90*clip(({_R}-300)/30\,0\,1)\,42)':"
        rf"b='if(lt({_R}\,330)\,58+90*Y/800\,26)':a='255*max(clip((330-{_R})/3\,0\,1)\,lt(Y\,440)*gt({_R}\,328)*"
        rf"lt({_R}\,400)*gt(sin(atan2(Y-430\,X-400)*180+4*sin({_R}/9))\,0.75)*clip((400-{_R})/60\,0\,1)*0.9)'")
_EYES = r"lt(hypot(X-120\,Y-170)\,18)+lt(hypot(X-240\,Y-170)\,18)"
FACE = (rf"color=c=black@0:s=360x420,format=rgba,geq=r='if({_EYES}\,30\,200)':g='if({_EYES}\,30\,160)':"
        rf"b='if({_EYES}\,30\,125)':a='255*clip((1-hypot((X-180)/170\,(Y-210)/200))*60\,0\,1)'")
HX, HY = "780+40*sin(2*PI*0.3*t)", "300+15*sin(2*PI*0.5*t)"
# A tuple = steps run in order; "{tmp.ext}" is a scratch file next to the output (removed after), "{root}" the root.
MEDIA.update({
    "pve/media/set2/handheld_shake.mp4": [
        "-f", "lavfi", "-i", "testsrc2=size=2048x1152:rate=25,drawgrid=w=128:h=128:t=3:c=white@0.6,crop=1920:1080:"
        "x='64+40*sin(2*PI*1.3*t)+24*(random(1)-0.5)':y='36+22*sin(2*PI*0.9*t+1)+16*(random(2)-0.5)',"
        r"dblur=angle=20:radius=12:enable='eq(mod(n\,4)\,1)+eq(mod(n\,7)\,5)'", "-t", "5", *X264],
    "pve/media/set2/rolling_shutter_pan.mp4": [
        "-f", "lavfi", "-i", WIDE.format(r=25) + ",crop=2040:1080:x='200+300*t':y=0,perspective="
        f"x0='-{SKEW}/2':y0=0:x1='W-{SKEW}/2':y1=0:x2='{SKEW}/2':y2=H:x3='W+{SKEW}/2':y3=H:eval=frame,"
        "crop=1920:1080:60:0", "-t", "5", *X264],
    "pve/media/set2/interlaced_25i.mp4": [
        "-f", "lavfi", "-i", WIDE.format(r=50) + ",crop=1920:1080:x='200+600*t':y=0,tinterlace=mode=interleave_top,"
        "setfield=tff", "-t", "5", "-c:v", "libx264", "-flags", "+ilme+ildct", "-x264-params", "tff=1", "-crf", "14",
        "-pix_fmt", "yuv420p", "-field_order", "tt"],
    "pve/media/set2/archive_4x3.mp4": (
        ["-f", "lavfi", "-i", "testsrc2=size=640x480:rate=25,format=yuv411p,format=yuv420p", "-t", "5",
         "-c:v", "mpeg2video", "-q:v", "24", "-g", "12", "{tmp.mpg}"],
        ["-i", "{tmp.mpg}", "-vf", "setsar=1", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p"]),
    "pve/media/set2/vfr_phone.mp4": [
        "-f", "lavfi", "-i", "testsrc2=size=3200x1080:rate=30,crop=1920:1080:x='200+360*t':y=0,"
        r"select='not(eq(mod(n\,9)\,4))*not(eq(mod(n\,13)\,7))'", "-fps_mode", "vfr", "-t", "5", *X264],
    "pve/media/set2/locked_off_motion.mp4": (
        ["-f", "lavfi", "-i", PLATE, "-frames:v", "1", "{tmp.png}"],
        ["-loop", "1", "-framerate", "25", "-i", "{tmp.png}", "-f", "lavfi", "-i", BALL, "-filter_complex",
         "[0:v]format=yuv420p[b];[b][1:v]overlay=x='200+1300*(0.5+0.5*sin(2*PI*0.25*t))':y='600+120*sin(2*PI*0.5*t)',"
         "drawbox=x=1560:y='100+600*(0.5+0.5*sin(2*PI*0.4*t))':w=120:h=120:color=0xF2C14E:t=fill",
         "-t", "5", "-r", "25", *X264]),
    "pve/media/set2/noisy_lowlight.mp4": [
        "-i", "{root}/pve/media/set2/locked_off_motion.mp4", "-vf",
        "eq=brightness=-0.18:contrast=0.75,noise=alls=34:allf=t:all_seed=7", *X264],
    "pve/media/set2/presenter_hair.mp4": (
        ["-f", "lavfi", "-i", HAIR, "-frames:v", "1", "{tmp.png}"],
        ["-f", "lavfi", "-i", "color=c=0x00B140:s=1920x1080:r=25", "-loop", "1", "-framerate", "25", "-i", "{tmp.png}",
         "-filter_complex", "[0:v][1:v]overlay=x='560+100*sin(t)':y=140,format=yuv420p", "-t", "5",
         "-c:v", "libx264", "-crf", "14"]),
    "pve/media/set2/dust_wire.mp4": (
        ["-f", "lavfi", "-i", PLATE + r",geq=r='if(lt(abs(X-700-Y*60/1080)\,2)\,35\,r(X\,Y))':"
         r"g='if(lt(abs(X-700-Y*60/1080)\,2)\,35\,g(X\,Y))':b='if(lt(abs(X-700-Y*60/1080)\,2)\,40\,b(X\,Y))'",
         "-frames:v", "1", "{tmp.png}"],
        ["-loop", "1", "-framerate", "25", "-i", "{tmp.png}", "-vf",
         r"format=yuv420p,geq=lum='if(gt(random(1)\,0.9996)\,250\,if(gt(random(2)\,0.9996)\,8\,lum(X\,Y)))':"
         r"cb='cb(X\,Y)':cr='cr(X\,Y)'", "-t", "5", "-r", "25", *X264]),
    "pve/media/set2/talking_head.mp4": (
        ["-f", "lavfi", "-i", FACE, "-frames:v", "1", "{tmp.png}"],
        ["-f", "lavfi", "-i", "color=c=0x2B3440:s=1920x1080:r=25", "-loop", "1", "-framerate", "25", "-i", "{tmp.png}",
         "-filter_complex", f"[0:v][1:v]overlay=x='{HX}':y='{HY}',drawbox=x='{HX}+130':y='{HY}+290':w=100:"
         "h='8+16*abs(sin(2*PI*2.2*t))':color=0x5A1E1E:t=fill,format=yuv420p", "-t", "6", *X264]),
})


def part(out):
    """Where the last step writes before the file is moved into place: same folder and extension (ffmpeg picks its muxer
    by extension), so an interrupted run never leaves a truncated clip under the real name."""
    return out.with_name(f"{out.stem}.part{out.suffix}")


def commands(rel, root=ROOT):
    """The ffmpeg argument lists that make MEDIA[rel] under root, in order (the last one writes the file)."""
    out, args = Path(root, rel), MEDIA[rel]
    steps, cmds = (args if isinstance(args, tuple) else (args,)), []
    for i, st in enumerate(steps):
        cmd = [re.sub(r"\{tmp(\.\w+)\}", lambda m: str(out.with_name(f"_{out.stem}_tmp{m.group(1)}")), a)
               .replace("{root}", Path(root).as_posix()) for a in st]
        cmds.append(["ffmpeg", "-v", "error", "-y", *cmd] + ([str(out)] if i == len(steps) - 1 else []))
    return cmds


def main(root=ROOT, only=None):
    """Make every missing file (only: just these keys); returns the keys made."""
    made = []
    for rel in MEDIA:
        out = Path(root, rel)
        if out.exists() or (only is not None and rel not in only):
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        part(out).unlink(missing_ok=True)  # left by an interrupted run
        cmds = commands(rel, root)
        cmds[-1][-1] = str(part(out))  # the last step writes beside the file, which appears only once it is whole
        for cmd in cmds:
            subprocess.run(cmd, check=True)
        os.replace(part(out), out)
        for t in out.parent.glob(f"_{out.stem}_tmp.*"):
            t.unlink()
        made.append(rel)
    return made


if __name__ == "__main__":
    print("made:", main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT) or "nothing (all present)")
