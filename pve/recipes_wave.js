export const meta = {
  name: 'pve-recipes',
  description: 'PVE wave 3: build verified recipes (looks, moves, transitions, sound) from the verified cards, then a completeness critic',
  phases: [
    { title: 'Recipes', detail: 'one agent per recipe family, only from verified cards' },
    { title: 'Critic', detail: 'what PVE still does not know' },
  ],
}

const ROOT = (args && args.root) || 'pve'  // absolute path of the pve/ folder (pipeline.py prints it)
const FAMILIES = [
  {key: 'archive_looks', ask: 'archival / historical footage looks: grungy film (grain, scratches, flicker, gate weave), faded print, sepia, black-and-white newsreel, damaged VHS, CRT/broadcast, projector light'},
  {key: 'grades', ask: 'colour grades for documentary and news: clean newsroom, cinematic teal-orange, warm nostalgic, cold tense, noir high-contrast, desaturated war, saffron/brand accent, day-for-night, using Lumetri / Color Balance / Curves / LUTs'},
  {key: 'impacts', ask: 'impact and emphasis moves on a cut or a hit word: camera shake, punch-in zoom with motion blur, RGB split, flash frame, glitch burst, chromatic pulse, speed ramp feel, shockwave, light flash'},
  {key: 'transitions', ask: 'transition recipes by purpose: hard energetic cut-aways, time jump, era change (colour to black-and-white), chapter change, reveal, glitch / digital, light leak / warm, whip pan, match cut helpers, dip to colour; each with the right sound from our SFX set'},
  {key: 'text_motion', ask: 'title and text motion: kinetic typography, typewriter, decode / scramble, mask reveal, glow title, 3D text, lower thirds, counters, quote cards, documentary chapter cards (After Effects text animators / presets and Premiere motion graphics templates)'},
  {key: 'vfx_overlays', ask: 'VFX overlays and atmospherics: embers/sparks/snow/rain/dust particles, smoke/fog, fire glow, lens flares, light rays, volumetric beams, heat haze, film burns, bokeh, particle text, map and data-viz highlights (glows, strokes, pulses)'},
  {key: 'stylized', ask: 'stylized and graphic treatments: posterize/comic/halftone, duotone, newspaper print, cutout/paper, blueprint, thermal/infrared, night vision, CCTV / bodycam, drone HUD, mosaic/pixelation for anonymising faces, blur-to-reveal'},
  {key: 'cleanup', ask: 'repair and cleanup: denoise, sharpen, deflicker, stabilize, upscale, interlace/judder fixes, crop-zoom logo removal, skin smoothing, blemish/object removal, keying green screen, VO cleanup audio chains and loudness mastering'},
]

function recipePrompt(f) {
  return `You are designing RECIPES for PVE, the knowledge base of an automated editing system (AdobeBridge) that drives
After Effects 2026 and Premiere Pro 2026 by script and edits documentaries, political explainers, news reels, social
reels and trailers. Cards (one per effect / transition / preset / LUT / template / tool, written from the apps' own
harvested data) are in ${ROOT}/cards/*.json; cards with "verified": {"verdict": "pass"} were render-tested on footage.
You can search them with:  python ${ROOT}/pve.py find "<words>"   and read one with  python ${ROOT}/pve.py card "<name>" [AE|PR]
(After Effects effect cards are being render-verified while you work: a card file mid-rewrite is skipped by those tools,
so if an effect you expect is missing, try again a few seconds later. Premiere effects and transitions are verified.)

Family: ${f.ask}.
Write 8-20 recipes to ${ROOT}/cards/recipe_${f.key}.json as {"cards": [ ... ]}, each:
{"kind": "recipe", "family": "${f.key}", "name": "<short name>", "summary": "<the look/move in one sentence>",
 "use_for": [...], "avoid": [...], "app": "PR" | "AE" | "both",
 "premiere": [effect specs exactly as the system applies them: {"name": <effect display name>, "pr_props": {...}}],
 "after_effects": [effect specs: {"name": <match name>, "props": {"Group/Prop": value}}],
 "transition": optional {"name": <Premiere transition>, "dur": seconds}, "sfx": optional [names from: whoosh, swoosh_tr,
 air, glitch, shutter, hit, boom, sub_drop, riser, tape_rewind, type_burst, heartbeat, blip, tick, jet_pass, amb_*],
 "keyframes_note": how to animate it (times relative to the clip / hit), "built_from": [card names used],
 "status": "current", "confidence": "high" | "medium" | "low"}.
Rules: use ONLY effects/transitions that have cards, prefer cards whose verified verdict is "pass", use exact property
names and values inside the ranges in those cards (read the card's params/demo_settings/scripting before using it),
and give BOTH a Premiere and an After Effects version when the look is achievable in both. Premiere-first: if
Premiere can do it, the Premiere version must be complete. Then check every recipe with
  python ${ROOT}/pve.py check <a temp spec file containing {"clips": [{"src": "x", "effects": <premiere list>}]}>
(ignore "missing source"), fixing any property / range problem it reports. Return the summary.`
}

const CRITIC = `You are the completeness critic for PVE (${ROOT}): an automated video editor's knowledge of After Effects
2026 and Premiere Pro 2026. Read ${ROOT}/PVE.md, sample the cards in ${ROOT}/cards/, and the harvest summaries
(${ROOT}/harvest/ae_effects.json, ${ROOT}/harvest/pr/pr_lists.json, ${ROOT}/harvest/files.json). Answer:
1. Which capabilities a professional documentary / news / motion-graphics editor relies on are NOT covered by any card or
   recipe (e.g. AE text animator properties, shape layer operators, expressions library, 3D camera moves, track mattes,
   masks and mask tracking, time remapping, Essential Graphics/Properties panel, audio mixing/ducking, export settings)?
2. Which cards look thin, wrong or contradictory (spot-check 30 at random, check against your knowledge)?
3. Which verified verdicts look doubtful?
Write ${ROOT}/cards/zz_critic.json as {"gaps": [...], "weak_cards": [...], "doubtful": [...], "next_steps": [...]} and
return a short summary.`

const SUM = {type: 'object', properties: {batch: {type: 'string'}, recipes: {type: 'integer'}, notes: {type: 'string'}},
  required: ['batch', 'recipes', 'notes']}
// args.skip_critic: recipes only (the critic should read the final cards); args.only_critic: the critic alone
const A = args || {}
let made = []
if (!A.only_critic) {
  phase('Recipes')
  made = await parallel(FAMILIES.map(f => () => agent(recipePrompt(f), {label: `recipes:${f.key}`, phase: 'Recipes', schema: SUM})))
}
if (A.skip_critic) return {recipes: made.filter(Boolean)}
phase('Critic')
const critic = await agent(CRITIC, {label: 'critic', phase: 'Critic'})
return {recipes: made.filter(Boolean), critic}
