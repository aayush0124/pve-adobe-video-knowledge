"""PVE: generate pve/PVE.md (the readable guide) from the cards, the harvest and the verification results.

    python pve/build_docs.py
"""
import json, os
from collections import defaultdict
from pathlib import Path

try:
    from pve import FLASH_LIMIT, UNTESTED, VERDICTS
except ImportError:  # `pve` is the namespace package here (a caller did `from pve import pve`)
    from pve.pve import FLASH_LIMIT, UNTESTED, VERDICTS

HERE = Path(os.environ.get("PVE_HOME") or Path(__file__).resolve().parent)
KIND_TITLES = [("ae_effect", "After Effects effects"), ("pr_effect", "Premiere Pro video effects"),
               ("pr_transition", "Premiere Pro transitions"), ("pr_audio", "Premiere Pro audio effects"),
               ("ae_preset", "After Effects animation presets"), ("pr_preset", "Premiere Pro effect presets (incl. Lumetri looks)"),
               ("lut", "Lumetri LUTs and looks"), ("mogrt", "Motion graphics templates"), ("tool", "Tools, features and scripting surfaces"),
               ("recipe", "Recipes (verified looks built from the cards)")]

HEAD = """# PVE: professional video editor knowledge base

PVE is what the AdobeBridge edit system knows about **After Effects 2026** and **Premiere Pro 2026**. Every item below
was harvested from the installed apps (names, match names, every property with its type, range and default), written
up as a knowledge card, checked by a validator against that harvest, reviewed by an adversarial agent, and (for
effects and transitions) rendered on test footage and checked against the frame.

## Using it

```
python pve/pve.py find "film grain"          # search by look / use
python pve/pve.py card "Camera Shake" PR      # one full card: params, demo settings, recipe, scripting notes
python pve/pve.py check spec.json             # pre-flight an AdobeBridge spec (also runs inside every build):
                                              #   names, ranges, transitions, flash rate (see Photosensitivity)
python pve/pve.py scenes scenes.json          # pre-flight After Effects VFX scenes (also runs inside vfx.py):
                                              #   names, ranges, preset on the wrong layer type / shape origin,
                                              #   flash rate
```

`pve.py card` warns (on stderr) when more than one card matches: distinct effects can share a display name (a current
and an obsolete Transform), so ask by match name, by app, or as "Category/Name" (`"Text/Rotation/Spin In"`).

In code: `sys.path.insert(0, "pve"); import pve` then `pve.find(...)`, `pve.card(...)`, `pve.check_spec(spec)`.

Spec conventions (both apps): After Effects effects are `{"name": <match name>, "props": {"Group/Property": value}}`
(dropdowns 1-based, points in pixels); Premiere effects are `{"name": <display name>, "pr_props": {"Property": value}}`
(repeated names `Name#2`, dropdowns 0-based, points as frame fractions). Colours `"#RRGGBB"`, keyframes `[[s, v], ...]`.

Preset timing (render-verified): applying an animation preset puts its first keyframe at the current time, so any
lead-in hold stored in the .ffx is lost; and preset keyframes keep their 30 fps frame counts, so in a 25 fps comp every
preset runs about 1.2x longer than the seconds quoted on its card. Shape presets draw around the layer's origin (put the
layer where the graphic should appear); text-animator presets do nothing on footage.

Status legend: **current**, obsolete (use the equivalent), hidden (AE internal), needs_input (needs a layer, file,
mask, audio or camera), unusable.

## Render verdicts

Each card's `verified.verdict`, shown in brackets after its name. A verdict describes what the test render showed
about the CARD; **pass = matched its render, not "works"**. Blank = no render check (Premiere presets, LUTs,
templates, audio, tools, and items never rendered).

{legend}

When a verdict changes, the old one stays in `verified.previous` with the date and the reason; details are in each
card's `verified.note`; cards whose values were fixed and then rendered again carry `verified.recheck`. A recipe
that carries both stacks has a second verdict for its After Effects half in `verified.ae` (shown as "AE ..."); the
first one is then the Premiere half.

## Photosensitivity

`check` and `scenes` count flashes for every effect, transition and preset whose card has a `flash` field (Strobe
Light, the Flash / Dip to White / Glow / Light Leak transitions, the flicker and flash presets...). The rule is the
Harding test / ITU-R BT.1702 one: no more than {limit} flashes in any one second where the flashing area is a quarter
of the screen or more, and a flash to or from saturated red counts even when it is not bright. A rate PVE can compute
(a strobe's Period and Duration, a preset's measured flash rate, flash transitions placed on the timeline) is an issue
when it passes {limit}. Strobe Light (both apps, measured): each Period starts in the strobe colour, which holds for
Period minus Duration, and the picture shows for the last Duration, on the layer's own clock (After Effects: from the
source's first frame; Premiere: from the clip's start). Random Strobe Probablity 0 flashes once per Period, 100 is the
exact inverse (again once per Period), and any value in between inverts about that share of frames in random bursts
(10 reached 6-7 flashes in one second), so it is always an issue. A flasher whose rate PVE cannot compute gives an
advisory line starting "check by eye". Advisory lines never fail `check` / `scenes`; the build treats every PVE line as
a warning anyway.

Files: `harvest/` raw ground truth; `cards/` knowledge cards (verdicts in each card's `verified`); `gallery/` render
test frames; `cards/recipe_*.json` combined looks/moves; `cards/zz_critic.json` known gaps.
"""


def legend():
    rows = [f"- **{v}**: {m}." for v, m in VERDICTS.items()]
    rows += [f"- **untested: {k}** (`untested:{k}`): the harness could not test it; {m}." for k, m in UNTESTED.items()]
    return "\n".join(rows)


def load():
    cards = []
    for f in sorted((HERE / "cards").glob("*.json")):
        try:
            cards += [c for c in json.loads(f.read_text(encoding="utf-8")).get("cards", []) if not c.get("merged_into")]
        except Exception:  # noqa: BLE001
            print("unreadable:", f.name)
    return cards


def line(c):
    vd = (c.get("verified") or {}).get("verdict")
    ae = ((c.get("verified") or {}).get("ae") or {}).get("verdict")  # a recipe's After Effects half
    vs = [x for x in (vd, ae and f"AE {ae}") if x and not x.endswith("not_rendered")]
    v = f" [{'; '.join(x.replace(':', ': ') for x in vs)}]" if vs else ""
    st = "" if c.get("status") in (None, "current") else f" *({c['status']})*"
    eq = f" -> {c['equivalent']}" if c.get("status") == "obsolete" and c.get("equivalent") else ""
    return f"- **{c.get('name')}**{st}{v}: {(c.get('summary') or '').strip()}{eq}"


def render():
    """PVE.md text from the cards (the test suite checks the committed PVE.md equals it) -> (text, cards, lines)."""
    cards = load()
    by = defaultdict(list)
    for c in cards:
        by[c.get("kind")].append(c)
    out = [HEAD.replace("{legend}", legend()).replace("{limit}", str(FLASH_LIMIT)), "## Contents\n"]
    for k, title in KIND_TITLES:
        if by.get(k):
            out.append(f"- [{title}](#{title.lower().replace(' ', '-').replace('(', '').replace(')', '').replace(',', '').replace('.', '')}) ({len(by[k])})")
    for k, title in KIND_TITLES:
        cs = by.get(k)
        if not cs:
            continue
        out.append(f"\n## {title}\n")
        groups = defaultdict(list)
        for c in cs:
            g = c.get("topic") or c.get("family") or c.get("category") or c.get("bin") or c.get("folder") or "Other"
            groups[g or "(no category)"].append(c)
        for g in sorted(groups, key=lambda s: (s.startswith("_") or s in ("(no category)", "Obsolete"), s.lower())):
            out.append(f"\n### {g}\n")
            out += [line(c) for c in sorted(groups[g], key=lambda c: (c.get("status") != "current", c.get("name") or ""))]
    critic = HERE / "cards" / "zz_critic.json"
    if critic.exists():
        z = json.loads(critic.read_text(encoding="utf-8"))
        out.append("\n## Known gaps (completeness critic)\n")
        for key in ("resolved", "gaps", "weak_cards", "doubtful", "next_steps"):
            if z.get(key):
                out.append(f"\n### {key.replace('_', ' ').capitalize()}\n")
                out += [f"- {x}" if isinstance(x, str) else f"- {x.get('step') or x.get('gap')}" + "".join(
                    f" **{k.capitalize()} {x[k]}**" for k in ("done", "progress", "covered") if x.get(k))
                    if "step" in x or "gap" in x else f"- {json.dumps(x, ensure_ascii=False)}" for x in z[key]]
    return "\n".join(out) + "\n", len(cards), len(out)


def main():
    text, n_cards, n_lines = render()
    (HERE / "PVE.md").write_text(text, encoding="utf-8")
    print(f"PVE.md: {n_cards} cards, {n_lines} lines")


if __name__ == "__main__":
    main()
