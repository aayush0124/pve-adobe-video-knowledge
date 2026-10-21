# AdobeBridge + PVE

**AdobeBridge** turns a timeline JSON into an After Effects `.aep` and a Premiere Pro `.prproj` (plus optional mp4
renders) and leaves both open in the apps. **PVE** ("professional video editor") is the knowledge base it checks every
spec against: every effect, transition, preset, LUT and motion graphics template that After Effects 2026 and Premiere
Pro 2026 ship, harvested from the installed apps, written up as about 2,600 knowledge cards, validated against the
harvest and (for effects, transitions, presets and recipes) render-tested and judged frame by frame.

Not affiliated with or endorsed by Adobe. After Effects, Premiere Pro and Media Encoder are trademarks of Adobe; other
product names are trademarks of their owners.

## Quick start

Offline (any OS, Python 3.10+, standard library only):

```bash
python -m unittest discover -s tests -v       # the whole offline suite
python pve/pve.py find "film grain"           # search the cards
python pve/pve.py check example/fx_test.json  # pre-flight a spec (exit 1 = issues found, 2 = could not check)
python pipeline.py status                     # where every stage stands
```

Live (Windows with After Effects 2026 and Premiere Pro 2026 installed, ffmpeg on PATH):

```bash
python make_test_media.py                          # synthetic test clips (ffmpeg lavfi sources)
python adobe_bridge.py example/spec.json           # build both projects
python adobe_bridge.py example/spec.json --render  # + <name>_ae.mp4 and <name>_pr.mp4
python adobe_bridge.py example/spec.json --no-pr   # After Effects only
python adobe_bridge.py example/spec.json --no-ae   # Premiere only (AE still relays the job)
```

From another pipeline:

```python
import sys; sys.path.insert(0, "/path/to/AdobeBridge")
from adobe_bridge import build
paths = build(spec_dict, base_dir, render=True)   # {"aep":..., "prproj":..., "ae_mp4":..., "pr_mp4":..., "warnings": [...]}
```

`sfx_synth.py` and `grunge.py` (synthesised SFX beds, a film-damage overlay) need numpy and scipy; nothing else does.

### Environment

| variable | default | what |
|---|---|---|
| `PVE_HOME` | `pve/` next to the code | folder holding `cards/`, `harvest/`, `work/` (a relative path is resolved once, against the current folder, and `pipeline.py` hands that to its stage scripts) |
| `PVE_ADOBE_DIR` | `C:\Program Files\Adobe` | Adobe install root (Media Encoder preset, default app paths) |
| `PVE_AFTERFX` | `<root>\Adobe After Effects 2026\Support Files\AfterFX.exe` | also locates `aerender.exe` and `Presets` |
| `PVE_PREMIERE` | `<root>\Adobe Premiere Pro 2026\Adobe Premiere Pro.exe` | |
| `PVE_LIVE` | unset | `1` lets `pipeline.py` run live stages and enables `tests/test_live.py` |

## PVE command line

```bash
python pve/pve.py find "film grain"             # rank cards by name / look / summary / use_for / kind / category
python pve/pve.py card "Camera Shake" PR        # one card in full (name or match name, optional app)
python pve/pve.py check spec.json [--no-ae|--no-pr]   # pre-flight an AdobeBridge spec
python pve/pve.py scenes scenes.json            # pre-flight After Effects VFX scenes (vfx.py input)
python pve/pve.py stats                         # what PVE knows
```

Exit codes: 0 ok (a `find` or `card` with no match too: "no cards matched" / the closest names go to stderr), 1 =
`check` / `scenes` found issues, 2 = could not check (usage error, unknown command or option value, a file that is
missing, not JSON or the wrong shape, or a knowledge base that is missing or partly unreadable, which `check` /
`scenes` name in their first line and which also reaches every build as a `PVE knowledge base incomplete ...` warning).
`find` matches whole words (stemmed; "a", "to", "the" and the like carry no weight), ranks a card whose name is the
query first, and scales obsolete, hidden, unusable and broken cards down; a line reads `[app kind] name: summary`, with
`[status, verdict]` after the colon for a card that is not current or whose render (either half) did not pass.

`check` runs inside every `adobe_bridge.build()` and `scenes` inside every `vfx.render_scenes()`; both are advisory
(the issues come back as `PVE ...` warnings and never block a build). A clip's effects are checked for every app the
build drives (`build(ae=False)` / `--no-ae`: Premiere only): Premiere gets `pr_name` / `pr_props`, After Effects
`name` / `props` (an effect After Effects lacks is skipped there while Premiere's half names it, as the build only warns
about it; an After Effects-only build, `--no-pr`, flags it); text effects and
presets run in After Effects only and are checked against it (`pve.check_spec(spec)` in code keeps the clip check
Premiere-only unless you pass `apps=("AE", "PR")`). They flag unknown effect, property and transition names,
out-of-range values (both apps clamp them, After Effects with a warning), values of the wrong type (a string, NaN, a
malformed colour, point or keyframe list), properties that cannot be set from a spec, text-animator presets on
non-text layers, shape presets on the wrong layer type or without an origin, and flashing: every effect, transition
and preset whose card has a `flash` field is held to the photosensitivity rule (Harding / ITU-R BT.1702: no more than
3 flashes in any one second; saturated red flashes count), from a strobe's Period and Duration (Strobe Light's Random Strobe Probablity 0 or 100; any value in between makes random bursts and
is always flagged), a preset's measured flash rate, or flash transitions placed on the timeline. Where no rate can be
computed the line starts "check by eye" and is advisory: `check` / `scenes` exit 1 only on the other lines. After
Effects runs a strobe on the layer's source clock, so a layer that becomes visible mid-period (a trimmed footage
layer) opens with a partial flash: there Strobe Period needs 0.5 s or more. One malformed entry gives an issue of its
own and the rest of the spec is still checked.
`card` warns on stderr when several cards match (ask by match name, app, or "Category/Name"; a current card comes
before hidden, obsolete and unusable ones). The app is `AE`, `PR` or `both` in any case, and another app's card is
never returned for it.
In code: `sys.path.insert(0, "pve"); import pve; pve.check_spec(spec)`.

## Spec format (seconds; paths relative to the spec file)

```json
{
  "name": "demo", "width": 1080, "height": 1920, "fps": 30,
  "clips":  [{"src": "a.mp4", "in": 1, "dur": 2.5, "zoom": 1.1, "track": 1, "start": 0}],
  "audio":  [{"src": "music.mp3", "gain_db": -6}],
  "texts":  [{"text": "HELLO", "start": 0.2, "dur": 2, "y": 0.2, "size": 110,
              "color": "#FFFFFF", "stroke": "#000000", "font": "Arial-BoldMT"}]
}
```

Defaults: clips run back-to-back per track, stills last 3 s, videos run to their end, every clip is cover-fitted to
the frame, `zoom` is a scale ramp over the clip. Music stops with the picture. Texts are centred, fade/pop in and out
(unless they have presets), and use `NirmalaUI-Bold` automatically for Indic text. All times are snapped to the frame
grid. At least one clip is required. Also accepted: `mgts` (Premiere motion graphics templates: `file`, `start`, `dur`,
`track`, `text`, `params`, `effects`) and `aecomps` (Dynamic-Linked After Effects comps: `aep`, `comp`, `start`, `dur`,
`track`), both Premiere-only, and `out_dir`.

### Effects and animation presets (clips and texts)

```json
"effects": ["Black & White",
            {"name": "Gaussian Blur", "props": {"Blurriness": [[0, 60], [0.8, 0]]},
             "pr_props": {"Amount": [[0, 60], [0.8, 0]]}},
            {"name": "Tint", "props": {"Map Black To": "#1a0033", "Map White To": "#ffd400"}}],
"presets": ["Text/Animate In/Typewriter.ffx",
            {"file": "Text/Animate Out/Fade Out By Character.ffx", "at": -1.0}]
```

- `effects`: any effect by its display name. A prop is a value, a `#rrggbb` colour, or keyframes `[[t, value], ...]`
  with `t` from the layer start (negative = from its end). Premiere gets the same effect natively; when its name or
  parameter names differ (Premiere 26's Gaussian Blur uses `Amount`, not `Blurriness`), set `pr_name` / `pr_props`.
- `presets`: any AE `.ffx`, as a path inside `...\After Effects 2026\Support Files\Presets` or relative to the spec,
  applied at `at` seconds (default 0, negative = from the end). Text presets reach Premiere through the linked title
  comps. Presets on **clips** are AE-only (Premiere has no equivalent), and the build warns about each one.
- AE's `Transitions - *` presets are transitions **in**: the layer holds the start state (often black or hidden)
  until `at`, then reveals. Don't use them as outs.
- Unknown effects/params never fail the build; they print `WARNING ...` lines listing the valid parameter names.
- PVE conventions: After Effects effects in VFX scenes are `{"name": <match name>, "props": {"Group/Property": value}}`
  (dropdowns 1-based, points in pixels, repeated names `Name#2`); Premiere effects are
  `{"name": <display name>, "pr_props": {"Property": value}}` (dropdowns 0-based, points as frame fractions).

### Transitions (clips)

```json
{"src": "b.mp4", "transition": "Cross Dissolve"},
{"src": "c.mp4", "transition": {"name": "Push", "dur": 0.5, "ae_preset": "Transitions - Movement/Slide - straight.ffx"}},
{"src": "d.png", "transition_out": {"name": "Dip to Black", "dur": 0.8}}
```

- `transition` = into this clip: from the previous clip on the same track (centred on the cut), or from black
  if nothing is directly before it. `transition_out` = to black at the end of a clip with nothing directly after
  it. Default 0.5 s. Any of Premiere's transition names works (`(Legacy)` versions too).
- Premiere applies them natively. AE builds the same frame window: Cross/Film Dissolve as a crossfade, Dip to
  Black/White as a colour solid, both with Premiere 26's measured curves (dissolves = smoothstep = AE Easy Ease,
  `(Legacy)` = linear). Other transitions need an `ae_preset` (transitions in only) or AE shows a cut, with a warning.
- A centred transition borrows media beyond the cut. When there isn't enough, Premiere drops that part and the
  build does the same in AE, with a `only N of M frames fit` warning.
- After building, Premiere reads back where each transition landed and warns if it differs from the plan.
- Measured AE vs Premiere: dissolves, legacy dissolves, one-sided dips within 1-3 luma levels per frame; two-sided
  dips run about a frame behind Premiere mid-dip; `ae_preset` stand-ins keep the preset's own timing.

### VFX scenes (`vfx.py`)

`render_scenes(scenes, aep_path, out_dir)` builds one After Effects project of native comps from scene dicts (layer
types are documented in `vfx_build.jsx`), queues every comp and renders them with aerender. The `.aep` stays the
editable source.

## Output (`<name>_adobe/`, never overwritten: `_v2`, `_v3`, ...)

| file | what |
|---|---|
| `<name>.aep` | AE master: comp `<name>` + `TXT_xx` title comps, render-queue item ready |
| `<name>.prproj` | Premiere sequence: clips V1.., titles on the next track, music on the next audio track |
| `<name>_pr_titles.aep` | the copy Premiere Dynamic-Links its titles to (double-click a title in Premiere to edit it in AE) |

## Not in the repo

Git ignores these folders; each is rebuilt by a script here. Other pipelines that read them need them rebuilt after a
fresh clone.

| folder | rebuilt by |
|---|---|
| `example/*.mp4`, `*.mp3`, `*.png`, `pve/media/` | `python make_test_media.py` (ffmpeg; makes only what is missing, and a file appears only once complete) |
| `sfx/` | `python sfx_synth.py --all` (numpy, scipy; without `--all` only the missing ambience beds) |
| `pve/gallery/` | `pve/gallery.py` (live, After Effects and Premiere) |
| `mgt/house/` | `python make_house_mogrts.py` (live, After Effects) |

## Pipeline

`python pipeline.py status | run <stage> | doctor | all`. Exit codes: 0 ok, 1 failure, 10 needs attention.
Deterministic stages run anywhere; live stages need the apps (and `PVE_LIVE=1`); agent stages are LLM workflows that
`pipeline.py run <stage>` only describes. None of the live or agent stages run in CI.

| stage | kind | runs |
|---|---|---|
| harvest | live | `pve/harvest_files.py`, `pve/harvest_ae.py`, `pve/harvest_pr.py` -> `pve/harvest/` |
| batches | deterministic | `pve/prepare_batches.py` -> `pve/work/batches/` (kept once cards exist; `--force` re-cuts all but the frozen Premiere batches, which were patched by hand after their cut) |
| cards | agent | one agent per batch writes `pve/cards/<batch>.json` (workflow not shipped here) |
| validate | deterministic | every card and harvest file loads, `pve/validate_cards.py` on every batch, `pve.py` pre-flight on every recipe stack (`pve.RECIPE_STACKS`: `premiere`, `after_effects` and their `_out`, `_alt`, `background` and `premiere_audio` variants), spec and scenes |
| gallery | live | `pve/gallery.py ae / pr / tr / presets / recipes / recipes_ae / specs / text` -> `pve/gallery/` (not in the repo); `strobe` / `flashes` -> `work/pve_live/*.json` |
| verify | agent | Workflow `pve/verify_wave.js` (args `root`, `batches`, optional `clip_a` / `clip_b`) |
| recipes | agent | Workflow `pve/recipes_wave.js` (args `root`, optional `skip_critic` / `only_critic`) |
| docs | deterministic | `pve/build_docs.py` -> `pve/PVE.md` |
| build | live | `make_test_media.py` + `adobe_bridge.py example/spec.json` |

Re-render chosen items with `pve/gallery.py ae|presets|recipes|pr --only keys.json` (a JSON list of match names,
preset files or names, any status): their old frames and renders move to `pve/work/gallery_pre_fix/<gallery>/` first,
so the frames the old verdicts were judged on survive. `--gpu` (ae / presets) renders with GPU acceleration.
`recipes_ae` renders each recipe's `after_effects` stack on the same clip and frame times as the Premiere `recipes`
gallery, plus a check that a stack holding one effect two or three times keeps every instance's own values. `specs`
builds every recipe that carries a bridge `spec` or VFX `scenes` from them, in both apps, and measures what can be
measured (where each app puts a Transform Position on an off-aspect still, ducking levels, credit and ticker speeds).
`text` builds the text recipes and a sample of templates in Latin and Devanagari copy and times their in / out
animations (`work/pve_live/wave2/text_timing.json`).
`strobe` settles Strobe Light's timing in both apps and `flashes` measures every card whose flash rate `check` cannot
compute; both count flashes with `pve/flash_scan.py` (per-frame luma against a render without the effect: a flash is a
rise and a fall of 25+ levels, also counted per cell of a grid so a chase across letters is not averaged away; run on
its own it counts at the clip's own frame rate unless `--fps` says otherwise).
`pve/harvest_enums.py` harvests every After Effects popup's labels (`pve/harvest/ae_enum_labels.json`): offline from
the plug-ins' own string tables, and with `--readback` from the running app, which in 26.0 lists a popup's menu
(`Property.propertyParameters`) and the current item's text (`Property.valueText`); the string-table list stays as its
cross-check. `pve/live_tests.py two_track | audio | footage` runs the tests that need a second track or a real edit
(Track Matte Key, Morph Cut, Camera Shake jitter, After Effects depth of field per 3D renderer), Premiere audio
measurements (every audio effect's 0..1 values mapped to dB / Hz / s / LUFS, its parameters dumped from a stereo clip
into `pve/harvest/pr_audio_params_w3.json`) and re-renders on the second test-footage set (`make_test_media.py`,
`pve/media/set2/`: shake, rolling-shutter skew, interlaced, 4:3 archive, VFR, locked-off, noisy, green screen with
fine strands and spill, dust and a wire, a jump-cut face); reports go to `work/pve_live/wave3/`.

Coverage now (`python pipeline.py status`, without the two machine-specific rows; a test keeps this block in sync):

<!-- status:begin -->
```
harvest   live  ae_effects 445, pr_video_effects 143, pr_transitions 154, pr_audio_effects 53, ae_presets 621, pr_presets 379, luts 55, mogrts 214
batches   det   97 batch files, 2067 items
cards     agent 2683 cards: ae_effect 445, ae_preset 621, lut 55, mogrt 214, pr_audio 53, pr_effect 141, pr_preset 379, pr_transition 154, recipe 171, tool 450
validate  det   ok, 0 issues
verify    agent broken 7, corrected_unverified 12, not_rendered 230, pass 1245, unverified 1129, weak 38, untested 22 (audio_only 1, needs_analysis 2, needs_second_layer 2, needs_setup 17); recipe After Effects halves: broken 1, corrected_unverified 27, pass 97, weak 3, untested 18 (audio_only 5, needs_analysis 1, needs_second_layer 2, needs_setup 10)
recipes   agent 171 recipes
docs      det   pve/PVE.md fresh
```
<!-- status:end -->

Verdicts say what the test render showed about the card, never more: `pass` = matched its render (not "works");
`weak` = visible but too faint at the tested values; `corrected_unverified` = the card disagreed with its render and
was then rewritten, so neither the old verdict nor the new text is render-checked; `untested:<cause>` = the harness
could not test it (`gpu_only`, `needs_analysis`, `needs_second_layer`, `needs_second_track`, `no_suitable_footage`,
`needs_setup`, `audio_only`); `broken` = nothing usable rendered and the setup was not shown to be the cause; `wrong`
= disagreed and not yet rewritten; `not_rendered` = no frame; unverified = never render-checked (tools, LUTs,
presets, templates). The legend in `pve/PVE.md` is generated from `pve/pve.py` (`VERDICTS`, `UNTESTED`).

## Data model

- `pve/harvest/`: raw ground truth read from the live apps: `ae_effects.json` (every AE effect, its property tree
  with types, ranges, defaults), `pr/pr_video_effects.json`, `pr/pr_audio_effects.json`, `pr/pr_transitions.json`,
  `pr/pr_lists.json`, and `files.json` (shipped `.ffx` presets with the match names inside each, Premiere effect
  presets, Lumetri LUTs, `.mogrt` parameters). Per-user paths are stored as `%LOCALAPPDATA%\...`.
  `ae_enum_labels.json`: per AE effect and popup (by display path, `Name#2` for repeats) the index -> label map, its
  source (the API's menu, or `<AE Support Files>/<plug-in> $$$/AE/<plugin>/LStr/<n>`), how the list was matched and
  what the readback showed (popup / integer slider); `pr_audio_params_w3.json`: every Premiere audio effect's
  component dumped on a stereo clip with probed ranges (the mono harvest gave stereo-only effects the Volume row; the
  `pr_audio` batch rows of those seven now come from this dump, marked `source`).
- `pve/work/batches/<kind>_<n>.json`: the harvest cut into agent-sized batches; each card file was written from, and
  is validated against, the batch of the same name.
- `pve/cards/*.json`: `{"cards": [...]}`. Every card has `name`, `app` (AE / PR / both), `kind`, `status` (current,
  obsolete, hidden, needs_input, unusable; beta for tools), `summary`, `use_for`, `confidence`; effect cards add
  `match`, `params`, `demo_settings`; render-tested cards carry `verified: {verdict, frame, note, rerender}` (a
  changed verdict keeps the old one in `verified.previous`); cards that flash carry `flash` (how to count their
  flashes, and the basis of those numbers). A duplicate harvest row is a stub `{name, kind, app, match,
  merged_into}` pointing at the one card for that item. AE effect cards list every popup's labels in `enum_labels`
  (1-based, `(separator)` for a menu separator, from `ae_enum_labels.json`); Premiere audio cards keep measured
  mappings in `params[].measured` (figure, test signal, `[value, result]` points, a straight-line fit) and the
  effect's level / distortion change at its defaults in `measured.defaults_on_tone`.
  `recipe_*.json` combine verified cards into looks and moves (`premiere` / `after_effects` effect stacks; the
  gap recipes also carry a whole bridge `spec` or VFX `scenes`, which `pipeline.py validate` pre-flights); a recipe
  with both stacks keeps its After Effects half's verdict in `verified.ae` (the top-level one is then Premiere's),
  `tools_*.json` cover features and scripting surfaces, `zz_critic.json` is the completeness critic.
- `pve/PVE.md`: the readable guide, generated from the cards.

## How it works / traps (all hit and verified 2026-10-01 .. 10-13)

- AE runs the generated JSX via `AfterFX.exe -r`. Premiere has no script flag, so AE relays the Premiere JSX over
  **BridgeTalk**, URL-encoded (BridgeTalk mangles backslashes).
- AE's "Allow scripts to write files" pref is off, so AE errors come back as an `AB_ERROR ...` comp inside the saved .aep.
- Premiere can't render Dynamic-Linked comps from an .aep that is open in the AE window, hence the `_pr_titles.aep` copy.
- **Never close/reopen a project from a script**: AE then silently ignores every later `-r` script.
- New AE text inherits the Character panel; Optical kerning breaks Devanagari spacing, so every layer is reset to Metrics.
- Premiere export needs native `\` paths. Its Volume "Level" is linear with 0 dB = 10^(-15/20); mono clips get a further -3 dB pan law.
- If AE was killed, its next start shows **Crash Repair Options**: choose Continue, or the build times out.
- If AE has unsaved changes (`*` in its title bar), the build refuses before touching anything.
- Premiere's QE `getVideoEffectByName` returns an object even for unknown names, so effects are checked against
  `getVideoEffectList()`.
- AE sometimes goes deaf to `-r` (idle, no dialog; seen after `app.open`, after its open .aep was deleted, after a
  template export): if nothing happens within 2 minutes the build restarts AE and sends the job once more.
- A cold, memory-starved Premiere can take longer than BridgeTalk's pickup window to start, so the build starts
  Premiere itself and waits for its window before relaying.
- Only one pipeline may drive AE at a time: builds take a cross-process lock (`.ae.lock`), re-entrant within a process.
- AE resolves a relative `-r` path against its own working directory and silently ignores it; ExtendScript garbles
  UTF-8, so every generated JSX is ASCII (`json.dumps(ensure_ascii=True)`). The other way round, AE's own error text
  carries curly quotes ("Can not “set value” ..."), and the AB_WARN / AB_ERROR comp names are stored as UTF-8 in the
  .aep: read them with `adobe_bridge.ae_notes` (an ASCII-only pattern dropped them, so an AB_ERROR did not fail the build).
- A plain `import pve` from the repo root can pick up the `pve/` folder as a namespace package; `adobe_bridge` loads
  `pve/pve.py` by path for the pre-flight, and scripts put `pve/` on `sys.path` first.
- A project's renderer (`Project.gpuAccelType`, set by `render_scenes(gpu=True)` / `gallery.py --gpu`) also becomes
  AE's renderer for every later new project. The galleries render on software (the VR effects then draw only a red
  "requires GPU acceleration" slate), so a `--gpu` gallery run ends by setting software again. The 12 VR (Mettle
  SkyBox) effects need GPU acceleration: their cards say `"gpu": "required"`; render them with `gpu=True` / `--gpu`.
- Effects keep time on the layer's own clock: in AE from the layer's source frame 0 (`startTime`, so a trimmed layer keeps
  the source's phase), in Premiere from the clip's start on the timeline, whatever its in point or sequence position.
- Premiere keeps every file its open projects use locked: write new media under new names (overwriting a still it had
  imported failed with "Permission denied").
- After Effects refuses keyframes on a property it cannot animate (Hue/Saturation's Master Saturation and about 930
  more that the harvest marks `animatable: false`), and the error ends the rest of that layer's effect stack in
  `ae_lib.jsx`; `pve.py check` / `scenes` flag such keys.
- Premiere snaps an audio-only clip's in point to the item's media timebase (25 fps in the project file): in 4.7 s
  was stored as 4.666667 s, one 30 fps frame early. Two copies of one file that must stay sample-aligned (the
  ducking recipe's lift copies) need in points on a multiple of 0.2 s.
- Premiere's Transform effect reads Position as fractions of the clip's own frame, not the sequence's (measured on
  a 1080x1350 still in a 1920x1080 sequence); Brightness & Contrast renders 10-27 levels darker in Premiere than in
  After Effects at the same values.
- Applying an animation preset puts its first keyframe at the current time (any lead-in hold in the .ffx is lost), and
  preset keyframes keep their 30 fps frame counts, so in a 25 fps comp a preset runs about 1.2x longer than its card says.
- After Effects 26.0 scripting lists a popup's menu (`Property.propertyParameters`; it throws "does not have dropdown
  menu options" on a slider) and the current item's text (`Property.valueText`); `isInterpolationTypeValid(LINEAR)`
  is false only for Hold-only properties. Never probe past `maxValue`: an out-of-range value kills the script.
- Apply Color LUT, Lumetri Color and Color Profile Converter open a file dialog (on apply, or when a popup reaches
  its Browse item), which blocks a scripted batch until After Effects is restarted.
- `comp.renderer` names: `ADBE Advanced 3d` is the Classic 3D renderer (an old internal name), `ADBE Calder` the newer
  Advanced 3D, `ADBE Ernst` Cinema 4D. Under `ADBE Calder` on 26.0 the camera's Depth of Field, Focus Distance,
  Aperture and Blur Level are hidden and refuse values from a script.
- Premiere's Track Matte Key `Matte:` is 1-based (1 = Video 1; 0 and the default 4294967295 = none), and with
  `Composite Using:` 0 = Matte Alpha, 1 = Matte Luma. Premiere asks "Audio Effects Replacement" (a modal Yes / No)
  when a script adds an obsolete audio effect; Escape (= No) keeps the obsolete one. A Morph Cut exported before
  Premiere's background analysis ends carries an "Analyzing in background" banner.
- Premiere snaps clip starts to the sequence's frame grid (3.5 s in a 25 fps timebase landed 0.02 s off): find
  placed clips by order, not by an exact start time.

## Tests

`python -m unittest discover -s tests -v` (standard library only; no app, network or LLM). It covers the spec logic
with a stubbed ffprobe, every `pve.py` subcommand, golden queries, bad specs that must be flagged, every card against
its batch and the batches against the harvest, docs and this README's coverage block, `pipeline.py`, and
`tests/test_public_scrub.py` (no machine paths, private names, credentials, vendor files or files over 5 MB in the
tracked set). The original scripts still run on their own: `python test_adobe_bridge.py` (needs ffprobe, the example
media and the AE presets) and `node test_ae_lib.js`. Live tests are opt-in: `PVE_LIVE=1 python -m unittest tests.test_live`.
Verified 2026-10-02: AE vs Premiere PSNR 37-39 dB where both apply the same effects, and builds are bit-identical
day to day for the same spec.

Test media is synthetic (`make_test_media.py`: ffmpeg `testsrc2`, `mandelbrot`, a gradient and generated tones). The
published gallery verdicts were judged on other footage, which is why the verify workflow takes `clip_a` / `clip_b`
descriptions of whatever plates you render on.

## Known gaps

The completeness critic's 20 technique gaps (`pve/cards/zz_critic.json`, listed in full at the end of
`pve/PVE.md`) now have tool cards and recipes (2026-10-13, ten `tools_*.json` and seven `recipe_*.json` files: editing
grammar, sync and multicam, camera moves and motion paths, the text-animator set, expressions, charts, credits and
tickers, safe areas, delivery and audio-stem specs, scripted ducking, frame rates, export colour tags, blur-fill and
multi-box layouts, document treatments, keying finish, the flash rule and media management). Each new recipe carries a
bridge `spec` or VFX `scenes` that passes `pve.py check` / `scenes`. Live wave 2 (rendered 2026-10-13) built all 24 in
both apps where they apply and judged them: 31 of 33 app halves pass; the news ticker's loop (a 13 px jump at every
wrap) and the ducking recipe's middle gap (-9.6 dB instead of -6) were fixed by value afterwards. The same wave judged
the After Effects half of every recipe (`verified.ae`), the 17 text recipes and 22 sampled templates in Latin and
Devanagari copy (Devanagari keeps time with the Latin, but per-character animators split matras mid-animation and
three bundled sports / gaming templates draw it unshaped even with a Devanagari font; the cards say so). 39 cards and After Effects halves are now changed after their render (`corrected_unverified`, listed in the
critic; the latest, 2026-10-20, is Grungy film reel's scratch layer, whose Fractal Noise Blending Mode 5 is Multiply in
the harvested labels and is now 6 = Screen) and wait for the next live wave. AE
dropdown labels are harvested from the app's own menus (`pve/harvest/ae_enum_labels.json`, live wave 3) and the AE
effect cards carry them in `enum_labels`; no card hedges on a label any more, but a mode known only by its label (CC
Radial Blur's zoom Types, most blend modes) has not been rendered. Live wave 3 also settled the two-track questions
(Track Matte Key's `Matte:` is 1-based, Camera Shake's mode-to-set mapping, Morph Cut on a jump inside one shot,
depth of field per 3D renderer), measured every Premiere audio effect (`params[].measured` on the audio cards; the
seven stereo-only effects now have their real rows) and re-rendered the 15 cleanup / keying cards on a synthetic
second footage set: 9 pass, 3 weak, 1 broken (Frame-rate judder conform: Timewarp at 100 % blends nothing), 1
untested (both apps field-separate a flagged interlaced file on import) and 1 corrected after its render (Advanced
Spill Suppressor). Some display
names are shared by distinct items (a current and an obsolete AE effect, the two 'Spin In' presets, the two 'Stretch'
transitions): `pve.card` warns and takes a match name or "Category/Name".

## License

Code: MIT (see `LICENSE`). Cards, harvest summaries and docs (`pve/cards/`, `pve/PVE.md`, this README): CC BY 4.0.
