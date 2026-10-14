// Runs inside After Effects. vfx.py prepends `var V = {...};` (scenes) + ae_lib.jsx. Pure ASCII.
// One comp per scene, layers listed bottom-to-top. Every scene gets a render-queue item; vfx.py runs aerender.
//
// Layer types: solid | shape | marker | text | counter | footage   (+ "matte": {layer, type: luma|alpha, invert})
// Shared keys: name, t0/t1 (in/out, comp seconds), cam (parent to the CAM null; marker/text keep constant size),
//   segs [{p, seg: [t0, v0, t1, v1, ease]}] with p in opacity|scale|position|rotation|anchor|trim|trim_start|
//   fill_op|stroke_op|fill_color|stroke_color (colour segs: ease "lin"), expr {p: "expression"}, effects/presets (ae_lib dress, times relative to t0).
//   markers [comp seconds] (layer markers, for marker-driven presets). V.gpu: true = GPU acceleration, "cpu" = software.
(function () {
  var proj = null;
  function prop(l, p, refs) {
    var tr = l.property("ADBE Transform Group");
    switch (p) {
      case "opacity": return tr.property("ADBE Opacity");
      case "scale": return tr.property("ADBE Scale");
      case "position": return tr.property("ADBE Position");
      case "rotation": return tr.property("ADBE Rotate Z");
      case "anchor": return tr.property("ADBE Anchor Point");
      case "trim": return refs.trim.property("ADBE Vector Trim End");
      case "trim_start": return refs.trim.property("ADBE Vector Trim Start");
      case "fill_op": return refs.fill.property("ADBE Vector Fill Opacity");
      case "fill_color": return refs.fill.property("ADBE Vector Fill Color");
      case "stroke_color": return refs.stroke.property("ADBE Vector Stroke Color");  // colour segs must use "lin" ease
      case "stroke_op": return refs.stroke.property("ADBE Vector Stroke Opacity");
      case "source_text": return l.property("Source Text");
    }
    throw "unknown property " + p;
  }
  function vec(cont, L, refs, camExpr) {  // stroke/fill/trim added after the paths so they apply to all of them
    // Adding a property to a group INVALIDATES references to properties added before it, and touching an
    // invalid reference kills the script with no catchable error. So add all three first, then re-fetch.
    if (L.trim) cont.addProperty("ADBE Vector Filter - Trim");
    if (L.stroke) cont.addProperty("ADBE Vector Graphic - Stroke");
    if (L.fill) cont.addProperty("ADBE Vector Graphic - Fill");
    if (L.trim) {
      refs.trim = cont.property("ADBE Vector Filter - Trim");
      refs.trim.property("ADBE Vector Trim Type").setValue(L.trim_each ? 2 : 1);
    }
    if (L.stroke) {
      refs.stroke = cont.property("ADBE Vector Graphic - Stroke");
      refs.stroke.property("ADBE Vector Stroke Color").setValue(hex(L.stroke));
      var sw = refs.stroke.property("ADBE Vector Stroke Width");
      sw.setValue(L.width || 2);
      if (camExpr) sw.expression = "value / (thisComp.layer(\"CAM\").transform.scale[0] / 100)";
      refs.stroke.property("ADBE Vector Stroke Line Join").setValue(2);
      refs.stroke.property("ADBE Vector Stroke Line Cap").setValue(2);
      if (L.dash) {
        var ds = refs.stroke.property("ADBE Vector Stroke Dashes");
        ds.addProperty("ADBE Vector Stroke Dash 1").setValue(L.dash[0]);
        ds.addProperty("ADBE Vector Stroke Gap 1").setValue(L.dash[1]);
      }
    }
    if (L.fill) {
      refs.fill = cont.property("ADBE Vector Graphic - Fill");
      refs.fill.property("ADBE Vector Fill Color").setValue(hex(L.fill));
      refs.fill.property("ADBE Vector Fill Opacity").setValue(L.fill_op === undefined ? 100 : L.fill_op);
    }
  }
  function shapeLayer(comp, L, refs, camOn) {
    var sl = comp.layers.addShape();
    // vertices are comp pixels, or relative to L.origin (shape presets draw around the layer origin, so centre it)
    sl.property("ADBE Transform Group").property("ADBE Position").setValue(L.origin || [0, 0]);
    var cont = sl.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group").property("ADBE Vectors Group");
    for (var i = 0; i < L.paths.length; i++) {
      var sh = new Shape();
      sh.vertices = L.paths[i].pts;
      sh.closed = L.paths[i].closed;
      cont.addProperty("ADBE Vector Shape - Group").property("ADBE Vector Shape").setValue(sh);
    }
    vec(cont, L, refs, camOn);
    return sl;
  }
  function markerLayer(comp, L, refs) {  // dot + a ring that keeps pulsing outwards
    var sl = comp.layers.addShape();
    sl.property("ADBE Transform Group").property("ADBE Position").setValue(L.pos);
    var root = sl.property("ADBE Root Vectors Group"), sz = L.size || 18;
    var ring = root.addProperty("ADBE Vector Group"), rc = ring.property("ADBE Vectors Group");
    rc.addProperty("ADBE Vector Shape - Ellipse").property("ADBE Vector Ellipse Size").setValue([sz, sz]);
    var rs = rc.addProperty("ADBE Vector Graphic - Stroke");
    rs.property("ADBE Vector Stroke Color").setValue(hex(L.color));
    rs.property("ADBE Vector Stroke Width").setValue(3);
    var rt = ring.property("ADBE Vector Transform Group");
    rt.property("ADBE Vector Scale").expression = "var t = (time - inPoint) % 1.4; var s = linear(t, 0, 1.4, 100, 420); [s, s]";
    rt.property("ADBE Vector Group Opacity").expression = "linear((time - inPoint) % 1.4, 0, 1.4, 90, 0)";
    var dot = root.addProperty("ADBE Vector Group"), dc = dot.property("ADBE Vectors Group");
    dc.addProperty("ADBE Vector Shape - Ellipse").property("ADBE Vector Ellipse Size").setValue([sz, sz]);
    refs.fill = dc.addProperty("ADBE Vector Graphic - Fill");
    refs.fill.property("ADBE Vector Fill Color").setValue(hex(L.color));
    return sl;
  }
  function textLayer(comp, L) {
    var tl = L.box ? comp.layers.addBoxText(L.box, L.text) : comp.layers.addText(L.text);
    styleText(tl, L, L.name);
    var r = tl.sourceRectAtTime(0, false), ax = L.just == "left" ? r.left : r.left + r.width / 2;
    tl.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([ax, r.top + r.height / 2]);
    tl.property("ADBE Transform Group").property("ADBE Position").setValue(L.pos);
    return tl;
  }
  function footageLayer(comp, L) {
    var item = proj.importFile(new ImportOptions(new File(L.src)));
    if (L.alpha_mode && item.mainSource && item.mainSource.hasAlpha) {  // "straight" | "premultiplied" (matted with black)
      item.mainSource.alphaMode = L.alpha_mode == "straight" ? AlphaMode.STRAIGHT : AlphaMode.PREMULTIPLIED;
      if (L.alpha_mode != "straight") item.mainSource.premulColor = [0, 0, 0];
    }
    var l = comp.layers.add(item), t0 = L.t0 || 0, t1 = L.t1 === undefined ? comp.duration : L.t1;
    var s = 100 * Math.max(comp.width / item.width, comp.height / item.height) * (L.zoom || 1);
    l.property("ADBE Transform Group").property("ADBE Scale").setValue([s, s]);
    // No time remap: rebuilding AE's time-remap keys from a script crashed AE (2026-10-02). Reverse or
    // speed-change footage with ffmpeg before it gets here.
    l.startTime = t0 - (L["in"] || 0);
    l.inPoint = t0;
    l.outPoint = t1;
    return l;
  }
  function buildScene(S) {
    var comp = proj.items.addComp(S.name, V.w, V.h, 1, S.dur, V.fps), cam = null, i, j;
    comp.bgColor = hex(S.bg || "#000000");
    for (i = 0; i < S.layers.length; i++) {
      var L = S.layers[i], refs = {}, l;
      try {
      var camOn = !!(L.cam && S.cam);
      if (L.type == "solid") l = comp.layers.addSolid(hex(L.color), L.name || "solid", V.w, V.h, 1, S.dur);
      else if (L.type == "shape") l = shapeLayer(comp, L, refs, camOn);
      else if (L.type == "marker") l = markerLayer(comp, L, refs);
      else if (L.type == "text") l = textLayer(comp, L);
      else if (L.type == "counter") {
        l = textLayer(comp, L);
        l.property("Source Text").expression = "var v = Math.round(easeOut(time, " + L.c0 + ", " + L.c1 + ", " + L.from + ", " + L.to + "));" +
          "\"" + (L.prefix || "") + "\" + String(v)" + (L.comma === false ? "" : ".replace(/\\B(?=(\\d{3})+(?!\\d))/g, \",\")") +
          " + \"" + (L.suffix || "") + "\"";
      }
      else if (L.type == "footage") l = footageLayer(comp, L);
      else throw "unknown layer type " + L.type;
      if (L.name) l.name = L.name;
      if (L.adjust) l.adjustmentLayer = true;
      if (L.pivot) {  // scale/rotate around this comp point instead of the layer origin
        prop(l, "anchor").setValue(L.pivot); prop(l, "position").setValue(L.pivot);
      }
      if (L.blend) l.blendingMode = BlendingMode[L.blend];
      if (L.opacity !== undefined) l.property("ADBE Transform Group").property("ADBE Opacity").setValue(L.opacity);
      if (L.t0 !== undefined && L.type != "footage") l.inPoint = L.t0;
      if (L.t1 !== undefined && L.type != "footage") l.outPoint = L.t1;
      for (j = 0; j < (L.markers || []).length; j++) l.property("ADBE Marker").setValueAtTime(L.markers[j], new MarkerValue(""));
      for (j = 0; j < (L.segs || []).length; j++) seg(prop(l, L.segs[j].p, refs), L.segs[j].seg);
      for (var p in (L.expr || {})) prop(l, p, refs).expression = L.expr[p];
      dress(l, comp, L, S.name + "/" + (L.name || L.type), L.t0 || 0, (L.t1 === undefined ? S.dur : L.t1) - (L.t0 || 0));
      if (camOn) {
        if (!cam) throw S.name + ": CAM must be created before layers that use it";
        l.parent = cam;
        if (L.type == "marker" || L.type == "text" || L.type == "counter")  // stay the same size while the map zooms
          prop(l, "scale").expression = "var c = thisComp.layer(\"CAM\").transform.scale[0] / 100; [value[0] / c, value[1] / c]";
      }
      if (i == 0 && S.cam) {  // CAM sits on top of the first (background) layer; identity at parenting time
        cam = comp.layers.addNull(S.dur);
        cam.name = "CAM";
        cam.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([V.w / 2, V.h / 2]);
        cam.property("ADBE Transform Group").property("ADBE Position").setValue([V.w / 2, V.h / 2]);
        cam.moveToEnd();
      }
      } catch (le) { warn(S.name + "/" + (L.name || L.type) + ": " + String(le).replace(/\s+/g, " ") + " line " + le.line); }
    }
    for (i = 0; i < S.layers.length; i++) {  // "matte": {layer, type: luma|alpha, invert}; the matte layer is hidden
      var M = S.layers[i].matte;
      if (!M) continue;
      try {
        var ml = comp.layer(M.layer), TM = TrackMatteType;
        comp.layer(S.layers[i].name).setTrackMatte(ml, M.type == "alpha" ? (M.invert ? TM.ALPHA_INVERTED : TM.ALPHA)
                                                                         : (M.invert ? TM.LUMA_INVERTED : TM.LUMA));
        ml.enabled = false;
      } catch (me) { warn(S.name + "/" + S.layers[i].name + " matte: " + String(me).replace(/\s+/g, " ") + " line " + me.line); }
    }
    if (cam) {  // after parenting, so children were converted at identity
      for (j = 0; j < S.cam.length; j++) {
        var c = S.cam[j];  // [t0, [ax, ay, s], t1, [ax, ay, s], ease]
        seg(prop(cam, "anchor"), [c[0], [c[1][0], c[1][1]], c[2], [c[3][0], c[3][1]], c[4]]);
        seg(prop(cam, "scale"), [c[0], [c[1][2], c[1][2]], c[2], [c[3][2], c[3][2]], c[4]]);
      }
    }
    var rq = proj.renderQueue.items.add(comp), om = rq.outputModule(1);
    rq.applyTemplate("Best Settings");
    om.applyTemplate(S.alpha ? "High Quality with Alpha" : "H.264 - Match Render Settings - 40 Mbps");
    om.file = new File(V.out_dir + "/" + S.name + (S.alpha ? ".mov" : ".mp4"));
  }
  try {
    if (app.project && app.project.dirty) {
      alert("AdobeBridge VFX: save or close your current After Effects project, then run again.");
      return;
    }
    proj = app.newProject();
    if (V.gpu) {  // true: first non-software engine AE offers (CUDA / OpenCL / Metal); "cpu": Mercury Software Only.
      var ga = app.availableGPUAccelTypes, gi;  // the project keeps it, and aerender follows the project
      if (V.gpu == "cpu") proj.gpuAccelType = GpuAccelType.SOFTWARE;
      else for (gi = 0; gi < ga.length; gi++) if (ga[gi] != GpuAccelType.SOFTWARE) { proj.gpuAccelType = ga[gi]; break; }
      var gn = String(proj.gpuAccelType), names = ["CUDA", "METAL", "OPENCL", "VULKAN", "SOFTWARE"];
      for (gi = 0; gi < names.length; gi++) if (GpuAccelType[names[gi]] !== undefined && proj.gpuAccelType == GpuAccelType[names[gi]]) gn = names[gi];
      warn("GPU " + (proj.gpuAccelType == GpuAccelType.SOFTWARE ? "off, rendering on the CPU" : "acceleration " + gn));
    }
    for (var s = 0; s < V.scenes.length; s++) {
      try { buildScene(V.scenes[s]); }
      catch (e) { warn("scene " + V.scenes[s].name + " failed: " + String(e).replace(/\s+/g, " ") + " line " + e.line); }
    }
  // String(e), never "..." + e: some AE errors throw on + coercion ("Object of type Error found where a Number,
  // Array, or Property is needed"), which killed whole jobs from inside these handlers (applyPreset, 2026-10-04).
  } catch (e) {
    if (!proj) { alert("AdobeBridge VFX: " + String(e)); return; }
    proj.items.addComp(("AB_ERROR " + String(e).replace(/\s+/g, " ") + " line " + e.line).substr(0, 190) + " ~", 16, 16, 1, 1, 30);
  }
  flushWarns(proj);
  proj.save(new File(V.aep));
})();
