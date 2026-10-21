export const meta = {
  name: 'pve-verify',
  description: 'PVE wave 2: check every effect / transition card against its rendered test frame, record a verdict in the card, fix wrong cards, list demos to re-render',
  phases: [
    { title: 'Verify', detail: 'one visual verifier per card batch' },
    { title: 'Second opinion', detail: 'independent check of every non-pass verdict' },
  ],
}

// args.root: absolute path of the pve/ folder (pipeline.py prints it); args.clip_a / args.clip_b: what the test plates
// in pve/media show (defaults describe the footage the published gallery was rendered on; say so if you swap them)
const ROOT = (args && args.root) || 'pve'
const CLIP_A = (args && args.clip_a) || 'a photographic test plate (a live-action clip with people, faces and saturated colour areas)'
const CLIP_B = (args && args.clip_b) || 'a second live-action test plate with a cool, low-contrast outdoor scene'
const KIND_GALLERY = {ae_effect: 'ae', pr_effect: 'pr', pr_transition: 'tr', ae_preset: 'presets'}

// recipe_<family> files have no harvest batch: they are validated with pve.py check instead of validate_cards.py
// Verdict bookkeeping (pve/pve.py VERDICTS / UNTESTED): a card changed after its render is no longer verified by it
const BOOKKEEPING = `Verdict bookkeeping: if you changed the card text or the values after judging it, write the verdict
"corrected_unverified" and keep the render's verdict as "previous": [{"verdict": "wrong" | "weak" | ..., "changed":
"<date>", "why": "<what you changed>"}]; if the test could not exercise it at all (GPU-only slate, analysis not
finished, footage without what it acts on, a setup the harness lacks), write "untested:<cause>" with a cause listed in
pve/pve.py UNTESTED. In your returned counts put corrected_unverified under "wrong" and untested under "broken".`
const isRecipe = b => b.startsWith('recipe_')
const validate = b => isRecipe(b)
  ? `for each recipe build a temp spec {"clips": [{"src": "x", "effects": <its premiere list>}]} and run
python ${ROOT}/pve.py check --no-ae <spec>  (ignore "missing source" and advisory "check by eye" lines) until nothing else is reported`
  : `python ${ROOT}/validate_cards.py ${ROOT}/work/batches/${b}.json ${ROOT}/cards/${b}.json  until 0 issues`

function recipePrompt(b) {
  return `You verify RECIPE cards of PVE (an automated video-editing system's knowledge of After Effects 2026 / Premiere
Pro 2026) against real renders. Card file: ${ROOT}/cards/${b}.json. Frames: ${ROOT}/gallery/recipes/index.json maps
k -> recipe (name, family). For recipe k, ${ROOT}/gallery/recipes/<kkk>a.png is 0.2 s and <kkk>b.png 1.0 s into a 2 s
clip carrying the recipe's whole "premiere" effect stack (keyframe times are relative to the clip start, negative = from
its end; read "keyframes_note"). The untouched reference is ${ROOT}/gallery/recipes/ref.png (${CLIP_A}). Recipes in ${ROOT}/gallery/recipes/failed.json broke Premiere's render (verdict
"broken"); recipes with no "premiere" list, or of family text_motion, were not rendered (verdict "not_rendered").
A recipe's transition and sfx are not in the frames: judge only the effect stack.

For EVERY recipe: open its frames and the reference with the Read tool and compare them with its summary. Decide
 "pass"   the frames clearly show the look / move the summary describes,
 "weak"   it is there but too subtle to read at normal viewing size,
 "wrong"  the frames show a different look, or the stack ruins the picture (crushed to black, blown out, unreadable),
 "broken" / "not_rendered" as above.
For weak or wrong: fix the "premiere" values (stay inside the ranges in the effect's card: python ${ROOT}/pve.py card
"<name>" PR), or rewrite the summary to what the frames really show. Write into each recipe
"verified": {"verdict": ..., "frame": "<path of the frame you judged>", "note": "<what you saw, one sentence>",
"rerender": true if you changed premiere values else false}. ${BOOKKEEPING}
Keep everything else valid. Then ${validate(b)}.
Return the summary.`
}

function prompt(b) {
  if (isRecipe(b)) return recipePrompt(b)
  const kind = b.replace(/_\d+$/, '')
  const g = KIND_GALLERY[kind]
  const how = kind === 'ae_preset'
    ? `Frames: ${ROOT}/gallery/presets/index.json maps k -> preset (name, category). For preset k the frames are
${ROOT}/gallery/presets/<kkk>a.png (0.5 s after the preset was applied at 0 s) and <kkk>b.png (1.4 s), rendered at 960x540.
Layer used: Text / Adobe Express / Behaviors presets were applied to a white text layer "ARCHIVE 1947" on a dark
background; Shapes presets to a small orange square shape layer on a dark background; Backgrounds / Synthetics to a grey
solid; everything else (Image, Transitions, Legacy) to live footage (${CLIP_A}). Sound Effects presets were not rendered (verdict "not_rendered"); presets in ${ROOT}/gallery/presets/failed.json
broke the render (verdict "broken"). Judge whether the frames show what the card says the preset does (an animate-in
preset may be mid-move at 0.5 s and settled at 1.4 s). There are no demo_settings for presets: for "weak" / "broken",
fix the card text (summary, look, duration_note, applies_to, status) to match what you see.`
    : kind === 'pr_transition'
    ? `Frames: ${ROOT}/gallery/tr/index.json maps k -> transition name. For transition k the frames are
${ROOT}/gallery/tr/<kkk>a.png (0.3 s before the cut), <kkk>b.png (at the cut, mid-transition) and <kkk>c.png
(0.3 s after). Clip A is ${CLIP_A}, clip B is ${CLIP_B}.
A 1-second transition centred on the cut was applied. Judge whether the frames show the transition the card describes
(family, feel). Audio transitions (card "family": "audio") have no frames: verdict "not_rendered".`
    : `Frames: ${ROOT}/gallery/${g}/index.json maps k -> effect (name${g === 'ae' ? ' and match' : ''}). Frame for item k:
${ROOT}/gallery/${g}/<kkk>.png (k zero-padded to 3 digits). The untouched reference frame is ${ROOT}/gallery/${g}/ref.png
(${CLIP_A}). Each frame is the reference with ONLY this card's
demo_settings applied (After Effects frames come from aerender, Premiere frames from a Premiere render). Effects whose
card is not "current" were not rendered: verdict "not_rendered". ${g === 'ae' ? `Effects AE refused to render are in
${ROOT}/gallery/ae/failed.json (verdict "broken", note it).` : `Premiere's warnings from that render are in
${ROOT}/gallery/pr/warnings.json (a property it rejected means the demo did not apply).`}`
  return `You verify knowledge cards of PVE (an automated video-editing system's knowledge of After Effects 2026 /
Premiere Pro 2026) against real renders. Card file: ${ROOT}/cards/${b}.json. Batch (ground truth names, properties,
ranges): ${ROOT}/work/batches/${b}.json.
${how}

For EVERY card: open its frame(s) and the reference with the Read tool and compare. Decide:
 "pass"   the frame clearly shows what the summary / look describe,
 "weak"   the effect is there but too subtle to judge or to be useful: raise demo_settings (stay inside the ranges in
          the batch file),
 "wrong"  something visible happened but it is not what the card says: rewrite summary / look / use_for to match what
          the frame shows (and what you know of the effect), or fix demo_settings if they caused it,
 "broken" the frame is unchanged, black, transparent or garbage: fix demo_settings if they are the cause (a no-op value,
          an off-frame point, a dropdown mode that hides the image), otherwise set status "needs_input" and explain,
 "not_rendered" no frame exists for it.
Write into each card: "verified": {"verdict": ..., "frame": "<path of the frame you judged>", "note": "<what you saw, one
sentence>", "rerender": true if you changed demo_settings else false}. ${BOOKKEEPING}
Keep everything else valid and names exact.
Then run:  ${validate(b)}.
Return the summary.`
}

function secondPrompt(b, items) {
  return `Independent second check for PVE card batch ${b} (${ROOT}/cards/${b}.json; ground truth
${isRecipe(b) ? 'the effect cards via python ' + ROOT + '/pve.py card "<name>" PR' : ROOT + '/work/batches/' + b + '.json'}).
The first verifier gave these cards a non-pass verdict and may have changed them:
${items.join('; ')}.
For each of those cards, look at the frame named in its "verified.frame" (and the reference ${ROOT}/gallery/*/ref.png or
the a/b/c transition frames) yourself, and check the first verifier's conclusion AND any rewrite it made. If the
rewrite is wrong or the new ${isRecipe(b) ? 'premiere values' : 'demo_settings'} are still a no-op / out of range /
destructive, fix the card. Add "second_opinion": "agree" | "corrected: <what>" inside its "verified" object. Then
${validate(b)}. Return the summary.`
}

const OUT = {type: 'object', properties: {batch: {type: 'string'}, pass: {type: 'integer'}, weak: {type: 'integer'},
  wrong: {type: 'integer'}, broken: {type: 'integer'}, not_rendered: {type: 'integer'},
  non_pass: {type: 'array', items: {type: 'string'}}, rerender: {type: 'array', items: {type: 'string'}}},
  required: ['batch', 'pass', 'weak', 'wrong', 'broken', 'not_rendered', 'non_pass', 'rerender']}
const SECOND = {type: 'object', properties: {batch: {type: 'string'}, agreed: {type: 'integer'}, corrected: {type: 'integer'},
  rerender: {type: 'array', items: {type: 'string'}}}, required: ['batch', 'agreed', 'corrected', 'rerender']}

const res = await pipeline(args.batches,
  (b) => agent(prompt(b), {label: `verify:${b}`, phase: 'Verify', schema: OUT}),
  (v, b) => (v && v.non_pass && v.non_pass.length)
    ? agent(secondPrompt(b, v.non_pass), {label: `second:${b}`, phase: 'Second opinion', schema: SECOND}).then(s => ({v, s}))
    : {v, s: null})
const rows = res.filter(Boolean)
const tot = k => rows.reduce((a, r) => a + ((r.v && r.v[k]) || 0), 0)
log(`pass ${tot('pass')}, weak ${tot('weak')}, wrong ${tot('wrong')}, broken ${tot('broken')}, not rendered ${tot('not_rendered')}`)
return {totals: {pass: tot('pass'), weak: tot('weak'), wrong: tot('wrong'), broken: tot('broken'), not_rendered: tot('not_rendered')},
  rerender: rows.flatMap(r => [].concat((r.v && r.v.rerender) || [], (r.s && r.s.rerender) || [])),
  per_batch: rows.map(r => ({batch: r.v && r.v.batch, pass: r.v && r.v.pass, non_pass: r.v && r.v.non_pass,
    corrected: r.s && r.s.corrected}))}
