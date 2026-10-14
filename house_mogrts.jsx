// Runs inside After Effects (prepended: `var H = {...}` + ae_lib.jsx). Builds the house .mogrt templates that the
// Premiere Composer pack does not have, exports them, saves H.aep. Pure ASCII.
(function () {
  var proj = null;
  function expose(prop, comp, name) { prop.addToMotionGraphicsTemplateAs(comp, name); }
  function slider(layer, name, v) {
    var e = layer.property("ADBE Effect Parade").addProperty("ADBE Slider Control");
    e.name = name;
    e.property(1).setValue(v);
    return layer.property("ADBE Effect Parade").property(name).property(1);  // re-fetch: adding invalidates refs
  }
  function counter() {  // animated number + label; Premiere sets From/To/Count Seconds/Prefix/Suffix/Label/colours
    var comp = proj.items.addComp("HB Counter", 1920, 1080, 1, 4, 25);
    comp.motionGraphicsTemplateName = "HB Counter";
    var ctl = comp.layers.addNull(4);
    ctl.name = "CTRL";
    var lbl = comp.layers.addText("LIVES LOST");
    styleText(lbl, {font: "Montserrat-Bold", size: 44, color: "#E0242B", tracking: 700}, "label");
    var num = comp.layers.addText("0");
    styleText(num, {font: "BebasNeueBold", size: 260, color: "#FFFFFF"}, "number");
    var r = num.sourceRectAtTime(0, false);
    num.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([r.left + r.width / 2, r.top + r.height / 2]);
    num.property("ADBE Transform Group").property("ADBE Position").setValue([960, 490]);
    var r2 = lbl.sourceRectAtTime(0, false);
    lbl.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([r2.left + r2.width / 2, r2.top + r2.height / 2]);
    lbl.property("ADBE Transform Group").property("ADBE Position").setValue([960, 660]);
    // From/To are hidden text layers, not sliders: Premiere clamps template sliders to the 0-100 slider range.
    var fr = comp.layers.addText("0"); fr.name = "FROM"; fr.enabled = false;
    var to = comp.layers.addText("166"); to.name = "TO"; to.enabled = false;
    var d = slider(ctl, "Count Seconds", 1.5);
    var cm = ctl.property("ADBE Effect Parade").addProperty("ADBE Checkbox Control");
    cm.name = "Thousands Comma";
    cm = ctl.property("ADBE Effect Parade").property("Thousands Comma").property(1);
    cm.setValue(1);
    var pre = comp.layers.addText(" "); pre.name = "PREFIX"; styleText(pre, {font: "BebasNeueBold", size: 40, color: "#FFFFFF"}, "prefix"); pre.enabled = false;
    var suf = comp.layers.addText(" "); suf.name = "SUFFIX"; styleText(suf, {font: "BebasNeueBold", size: 40, color: "#FFFFFF"}, "suffix"); suf.enabled = false;
    // Call effect() on the layer: a stored `layer.effect` loses its `this` and the expression silently fails ("0").
    num.property("Source Text").expression =
      "var L = thisComp.layer('CTRL'); function n(x) { return parseFloat(String(thisComp.layer(x).text.sourceText).replace(/[^0-9.\-]/g, '')) || 0; }" +
      "var a = n('FROM'), b = n('TO'), s = L.effect('Count Seconds')('Slider');" +
      "var v = Math.round(easeOut(time, 0.1, 0.1 + s, a, b)); var t = String(v);" +
      "if (L.effect('Thousands Comma')('Checkbox') == 1) t = t.replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',');" +
      "(String(thisComp.layer('PREFIX').text.sourceText) + t + String(thisComp.layer('SUFFIX').text.sourceText)).replace(/^ | $/g, '')";
    var op = num.property("ADBE Transform Group").property("ADBE Opacity");
    op.setValueAtTime(0, 0); op.setValueAtTime(0.15, 100);
    var lo = lbl.property("ADBE Transform Group").property("ADBE Opacity");
    lo.expression = "linear(time, thisComp.layer('CTRL').effect('Count Seconds')('Slider') * 0.6, thisComp.layer('CTRL').effect('Count Seconds')('Slider') * 0.6 + 0.3, 0, 100)";
    var nf = num.property("ADBE Effect Parade").addProperty("ADBE Fill"); nf.property("Color").setValue([1, 1, 1, 1]);
    var lf = lbl.property("ADBE Effect Parade").addProperty("ADBE Fill"); lf.property("Color").setValue([0.878, 0.141, 0.169, 1]);
    num.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");
    expose(fr.property("Source Text"), comp, "From");
    expose(to.property("Source Text"), comp, "To");
    expose(ctl.property("ADBE Effect Parade").property("Count Seconds").property(1), comp, "Count Seconds");
    expose(ctl.property("ADBE Effect Parade").property("Thousands Comma").property(1), comp, "Thousands Comma");
    expose(pre.property("Source Text"), comp, "Prefix");
    expose(suf.property("Source Text"), comp, "Suffix");
    expose(lbl.property("Source Text"), comp, "Label");
    expose(num.property("ADBE Effect Parade").property("ADBE Fill").property("Color"), comp, "Number Colour");
    expose(lbl.property("ADBE Effect Parade").property("ADBE Fill").property("Color"), comp, "Label Colour");
    proj.save(new File(H.pre_aep));  // export refuses an unsaved project; the runner waits for the FINAL save
    var ok = comp.exportAsMotionGraphicsTemplate(true, H.out_dir);  // a FOLDER; file = template name
    if (!ok) warn("export of HB Counter returned false");
    proj = app.project;  // the export invalidates the old project object ("Object is invalid", uncatchable)
  }
  try {
    if (app.project && app.project.dirty) { alert("AdobeBridge: save or close your current After Effects project, then run again."); return; }
    proj = app.newProject();
    counter();
  } catch (e) {
    if (!proj) { alert("AdobeBridge: " + String(e)); return; }
    proj.items.addComp(("AB_ERROR " + String(e).replace(/\s+/g, " ") + " line " + e.line).substr(0, 190) + " ~", 16, 16, 1, 1, 25);
  }
  flushWarns(proj);
  proj.save(new File(H.aep));
})();
