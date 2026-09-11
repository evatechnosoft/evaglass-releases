"""
evaglass 30 sn lansman spotu - urun planlari (SH010, SH050, SH060)

Calistirma:
  blender -b -P evaglass_spot.py -- --shot SH010 --device OPTIX
  python -c "import bpy" ortaminda:  python evaglass_spot.py --shot SH050 --frame 72 --res 25

Argumanlar (hepsi istege bagli):
  --shot SH010|SH050|SH060|all     hangi plan (varsayilan: all)
  --frame N                        tek kare test render
  --res 25                         cozunurluk yuzdesi (varsayilan 100)
  --samples 64                     Cycles ornek sayisi (varsayilan 64)
  --device CPU|CUDA|OPTIX          render cihazi (varsayilan CPU)
  --out DIR                        cikti klasoru (varsayilan ./out)
  --glasses PATH.glb|.obj|.fbx     gercek gozluk modeli; verilmezse yer tutucu cizilir
  --screen-phone PATH              telefon ekran kaydi: mp4/mov dosyasi ya da png dizisi klasoru
  --screen-watch PATH              saat ekran kaydi: mp4/mov dosyasi ya da png dizisi klasoru
  --save PATH.blend                sahneyi .blend olarak kaydet (yerelde acip bakmak icin)
"""
import bpy, sys, os, math, argparse
from mathutils import Vector

# ---------------------------------------------------------------- args
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
ap = argparse.ArgumentParser()
ap.add_argument("--shot", default="all")
ap.add_argument("--frame", type=int, default=None)
ap.add_argument("--res", type=int, default=100)
ap.add_argument("--samples", type=int, default=64)
ap.add_argument("--device", default="CPU")
ap.add_argument("--out", default="out")
ap.add_argument("--glasses", default=None)
ap.add_argument("--screen-phone", dest="screen_phone", default=None)
ap.add_argument("--screen-watch", dest="screen_watch", default=None)
ap.add_argument("--save", default=None)
args = ap.parse_args(argv)

FPS = 24
SHOTS = {  # kare sayilari: 24 fps
    "SH010": dict(frames=72,  desc="Makro, slow dolly-in: camda sehir isiklari"),
    "SH050": dict(frames=144, desc="Bullet time: gozluk + telefon + saat havada, kamera orbit"),
    "SH060": dict(frames=96,  desc="Genis plan, pull-out: logo ve indirme cagrisi"),
}

# ---------------------------------------------------------------- helpers
def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)

def mat_principled(name, color=(0.02, 0.02, 0.02, 1), rough=0.35, metal=0.0, transmission=0.0, ior=1.5, emission=None, emission_strength=0.0):
    m = bpy.data.materials.new(name); m.use_nodes = True
    bsdf = m.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    bsdf.inputs["IOR"].default_value = ior
    # Blender 4.x: "Transmission Weight"; eski surumler: "Transmission"
    for key in ("Transmission Weight", "Transmission"):
        if key in bsdf.inputs: bsdf.inputs[key].default_value = transmission; break
    if emission is not None:
        bsdf.inputs["Emission Color"].default_value = emission
        bsdf.inputs["Emission Strength"].default_value = emission_strength
    return m

def add_obj(name, mesh_op, mat=None, loc=(0,0,0), rot=(0,0,0), scale=(1,1,1), **kw):
    mesh_op(location=loc, rotation=rot, **kw)
    o = bpy.context.active_object; o.name = name; o.scale = scale
    if mat: o.data.materials.append(mat)
    return o

def smooth(o):
    for p in o.data.polygons: p.use_smooth = True

# ---------------------------------------------------------------- products
def _apply_bool(target, cutter):
    m = target.modifiers.new("cut", "BOOLEAN"); m.operation = "DIFFERENCE"; m.object = cutter; m.solver = "EXACT"
    bpy.ops.object.select_all(action="DESELECT"); target.select_set(True); bpy.context.view_layer.objects.active = target
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.data.objects.remove(cutter, do_unlink=True)

def build_glasses(mat_frame, mat_lens):
    """evaglass yer tutucu: fotoğraflara göre kalın mat siyah Wayfarer gövde, dikdörtgene yakın camlar,
    elektronikli kalın saplar, sol ön köşede kamera, sapta kırmızı anahtar ve mikrofon delikleri.
    Gerçek 3D model --glasses ile gelirse bu çizilmez."""
    root = bpy.data.objects.new("glasses", None); bpy.context.collection.objects.link(root)
    parts = []
    mat_cam = mat_principled("cam_lens", (0.02, 0.02, 0.03, 1), rough=0.05, transmission=0.6, ior=1.7)
    mat_red = mat_principled("switch_red", (0.75, 0.05, 0.03, 1), rough=0.5)
    mat_dark = mat_principled("grille", (0.004, 0.004, 0.004, 1), rough=0.9)

    # --- ön gövde: yuvarlatılmış dikdörtgen, üstte biraz daha geniş (Wayfarer)
    front = add_obj("front", bpy.ops.mesh.primitive_cube_add, mat_frame, loc=(0, 0, 0), size=1)
    front.scale = (0.144, 0.0065, 0.048)
    bpy.ops.object.select_all(action="DESELECT"); front.select_set(True); bpy.context.view_layer.objects.active = front
    bpy.ops.object.transform_apply(scale=True)
    # alt kenarı hafif daralt (trapez): alt köşe vertexlerini içeri çek
    for v in front.data.vertices:
        if v.co.z < 0: v.co.x *= 0.95
    bev = front.modifiers.new("bevel", "BEVEL"); bev.width = 0.0045; bev.segments = 8; bev.limit_method = "ANGLE"
    bpy.ops.object.modifier_apply(modifier=bev.name)
    # cam boşlukları: yuvarlatılmış dikdörtgen kesici (küp + bevel), camlar aynı şekille kesişim
    def rounded_cutter(name, x, depth):
        """Wayfarer cam açıklığı: köşeler 4.5 mm yarıçaplı, alt kenar üstten %10 dar, dış alt köşe biraz daha kısa."""
        c = add_obj(name, bpy.ops.mesh.primitive_cube_add, None, loc=(x, 0, -0.002), size=1)
        c.scale = (0.054, depth, 0.040)
        bpy.ops.object.select_all(action="DESELECT"); c.select_set(True); bpy.context.view_layer.objects.active = c
        bpy.ops.object.transform_apply(scale=True)
        outer = 1 if x > 0 else -1
        for v in c.data.vertices:
            if v.co.z < 0:
                v.co.x *= 0.90                       # alt kenar daralır
                if (v.co.x > 0) == (outer > 0): v.co.z += 0.003  # dış alt köşe yukarı: Wayfarer eğimi
        b = c.modifiers.new("bevel", "BEVEL"); b.width = 0.0045; b.segments = 8; b.limit_method = "NONE"
        bpy.ops.object.modifier_apply(modifier=b.name)
        return c
    for side, x in (("L", -0.0345), ("R", 0.0345)):
        _apply_bool(front, rounded_cutter(f"cut_{side}", x, 0.03))
    # burun boşluğu (keyhole)
    nose = add_obj("cut_nose", bpy.ops.mesh.primitive_cylinder_add, None, loc=(0, 0, -0.033), rot=(math.radians(90), 0, 0), radius=0.014, depth=0.03, vertices=32)
    _apply_bool(front, nose)
    smooth(front); parts.append(front)
    # camlar: ince levha ∩ kesici şekli
    for side, x in (("L", -0.0345), ("R", 0.0345)):
        lens = add_obj(f"lens_{side}", bpy.ops.mesh.primitive_cube_add, mat_lens, loc=(x, 0.0005, -0.002), size=1)
        lens.scale = (0.2, 0.0018, 0.2)
        bpy.ops.object.select_all(action="DESELECT"); lens.select_set(True); bpy.context.view_layer.objects.active = lens
        bpy.ops.object.transform_apply(scale=True)
        shape = rounded_cutter(f"lensshape_{side}", x, 0.03)
        # kesici %97 küçük: cam çerçeve içinde otursun
        shape.scale = (0.97, 1, 0.97)
        m = lens.modifiers.new("fit", "BOOLEAN"); m.operation = "INTERSECT"; m.object = shape; m.solver = "EXACT"
        bpy.ops.object.select_all(action="DESELECT"); lens.select_set(True); bpy.context.view_layer.objects.active = lens
        bpy.ops.object.modifier_apply(modifier=m.name)
        bpy.data.objects.remove(shape, do_unlink=True)
        parts.append(lens)  # düz levha: flat shading doğru, smooth buruşuk gösterir
    # --- saplar: kalın, elektronikli; uçta kulak kıvrımı
    for side, x, sgn in (("L", -0.070, -1), ("R", 0.070, 1)):
        hinge = add_obj(f"hinge_{side}", bpy.ops.mesh.primitive_cube_add, mat_frame, loc=(x, 0.006, 0.006), size=1)
        hinge.scale = (0.0075, 0.012, 0.014); hb = hinge.modifiers.new("bevel", "BEVEL"); hb.width = 0.0015; hb.segments = 4
        parts.append(hinge)
        temple = add_obj(f"temple_{side}", bpy.ops.mesh.primitive_cube_add, mat_frame, loc=(x + sgn*0.0005, 0.066, 0.006), size=1)
        temple.scale = (0.0055, 0.112, 0.011); tb = temple.modifiers.new("bevel", "BEVEL"); tb.width = 0.0018; tb.segments = 5
        parts.append(temple)
        tip = add_obj(f"tip_{side}", bpy.ops.mesh.primitive_cube_add, mat_frame, loc=(x + sgn*0.0005, 0.134, -0.004), rot=(math.radians(-28), 0, 0), size=1)
        tip.scale = (0.0052, 0.034, 0.0095); tpb = tip.modifiers.new("bevel", "BEVEL"); tpb.width = 0.0022; tpb.segments = 5
        parts.append(tip)
        # mikrofon delikleri (sap dışı, iki nokta)
        for dy in (0.040, 0.046):
            mic = add_obj(f"mic_{side}", bpy.ops.mesh.primitive_cylinder_add, mat_dark, loc=(x + sgn*0.0033, dy, 0.006), rot=(0, math.radians(90), 0), radius=0.0006, depth=0.001, vertices=12)
            parts.append(mic)
        # hoparlör ızgarası (sap içi, menteşeye yakın üç yarık)
        for dy in (0.014, 0.017, 0.020):
            slit = add_obj(f"slit_{side}", bpy.ops.mesh.primitive_cube_add, mat_dark, loc=(x - sgn*0.0031, dy, 0.004), size=1)
            slit.scale = (0.0006, 0.0008, 0.005); parts.append(slit)
    # --- kamera modülü: sol ön köşe (izleyiciye göre sol), hafif öne çıkık halka + cam
    ring = add_obj("cam_ring", bpy.ops.mesh.primitive_cylinder_add, mat_frame, loc=(-0.061, -0.0068, 0.012), rot=(math.radians(90), 0, 0), radius=0.0042, depth=0.0012, vertices=32)
    smooth(ring); parts.append(ring)
    cam = add_obj("cam_lens", bpy.ops.mesh.primitive_cylinder_add, mat_cam, loc=(-0.061, -0.0072, 0.012), rot=(math.radians(90), 0, 0), radius=0.0028, depth=0.0008, vertices=32)
    smooth(cam); parts.append(cam)
    # --- kırmızı anahtar: sol sap üstü, menteşeye yakın
    sw = add_obj("switch", bpy.ops.mesh.primitive_cube_add, mat_red, loc=(-0.0705, 0.024, 0.0122), size=1)
    sw.scale = (0.0035, 0.006, 0.0014); parts.append(sw)
    for p in parts: p.parent = root
    return root

def import_glasses(path):
    ext = os.path.splitext(path)[1].lower()
    before = set(bpy.data.objects)
    if ext in (".glb", ".gltf"): bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".obj": bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".fbx": bpy.ops.import_scene.fbx(filepath=path)
    else: raise SystemExit(f"desteklenmeyen model formati: {ext}")
    new = [o for o in bpy.data.objects if o not in before]
    root = bpy.data.objects.new("glasses", None); bpy.context.collection.objects.link(root)
    for o in new:
        if o.parent is None: o.parent = root
    # gozluk genisligini ~14 cm'e normalize et
    xs = [ (o.matrix_world @ Vector(c)).x for o in new if o.type == "MESH" for c in o.bound_box ]
    if xs:
        w = max(xs) - min(xs)
        if w > 0: root.scale = (0.14 / w,) * 3
    return root

def screen_material(name, frames_dir, fallback_color):
    """Ekran kaydi kare dizisi varsa image sequence, yoksa emissive degrade."""
    m = bpy.data.materials.new(name); m.use_nodes = True
    nt = m.node_tree; bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.15
    img = None; n_frames = 0
    if frames_dir and os.path.isfile(frames_dir) and frames_dir.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
        img = bpy.data.images.load(frames_dir); img.source = "MOVIE"; n_frames = img.frame_duration
    elif frames_dir and os.path.isdir(frames_dir):
        files = sorted(f for f in os.listdir(frames_dir) if f.lower().endswith((".png", ".jpg", ".jpeg")))
        if files:
            img = bpy.data.images.load(os.path.join(frames_dir, files[0])); img.source = "SEQUENCE"; n_frames = len(files)
    if img is not None:
        if True:
            tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = img
            tex.image_user.frame_duration = n_frames; tex.image_user.use_auto_refresh = True; tex.image_user.use_cyclic = True
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            bsdf.inputs["Emission Strength"].default_value = 3.0
            return m
    bsdf.inputs["Base Color"].default_value = fallback_color
    bsdf.inputs["Emission Color"].default_value = fallback_color
    bsdf.inputs["Emission Strength"].default_value = 2.5
    return m

def build_phone(mat_body, mat_screen):
    root = bpy.data.objects.new("phone", None); bpy.context.collection.objects.link(root)
    body = add_obj("phone_body", bpy.ops.mesh.primitive_cube_add, mat_body, size=1); body.scale = (0.036, 0.004, 0.075)
    bev = body.modifiers.new("bevel", "BEVEL"); bev.width = 0.003; bev.segments = 6
    scr = add_obj("phone_screen", bpy.ops.mesh.primitive_plane_add, mat_screen, loc=(0, -0.0041, 0), rot=(math.radians(90), 0, 0), size=1); scr.scale = (0.066, 0.142, 1)
    body.parent = root; scr.parent = root
    return root

def build_watch(mat_body, mat_screen):
    root = bpy.data.objects.new("watch", None); bpy.context.collection.objects.link(root)
    body = add_obj("watch_body", bpy.ops.mesh.primitive_cylinder_add, mat_body, rot=(math.radians(90), 0, 0), radius=0.022, depth=0.011, vertices=64); smooth(body)
    scr = add_obj("watch_screen", bpy.ops.mesh.primitive_circle_add, mat_screen, loc=(0, -0.0056, 0), rot=(math.radians(90), 0, 0), radius=0.019, vertices=64, fill_type="NGON")
    band = add_obj("watch_band", bpy.ops.mesh.primitive_cube_add, mat_body, size=1); band.scale = (0.011, 0.002, 0.09)
    body.parent = root; scr.parent = root; band.parent = root
    return root

# ---------------------------------------------------------------- environment
def build_city_lights():
    """Kameranin arkasinda, camda yansiyacak 'sehir isiklari' bokeh duvari."""
    m = bpy.data.materials.new("city_lights"); m.use_nodes = True
    nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial"); em = nt.nodes.new("ShaderNodeEmission")
    vor = nt.nodes.new("ShaderNodeTexVoronoi"); vor.inputs["Scale"].default_value = 18.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.06; ramp.color_ramp.elements[0].color = (1, 1, 1, 1)
    ramp.color_ramp.elements[1].position = 0.10; ramp.color_ramp.elements[1].color = (0, 0, 0, 1)
    tint = nt.nodes.new("ShaderNodeTexNoise"); tint.inputs["Scale"].default_value = 6.0
    mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.inputs[0].default_value = 0.5
    mix.inputs[6].default_value = (1.0, 0.62, 0.25, 1)   # tungsten
    mix.inputs[7].default_value = (0.35, 0.75, 1.0, 1)   # soguk neon
    nt.links.new(vor.outputs["Distance"], ramp.inputs["Fac"])
    nt.links.new(tint.outputs["Fac"], mix.inputs[0])
    nt.links.new(mix.outputs[2], em.inputs["Color"])
    nt.links.new(ramp.outputs["Color"], em.inputs["Strength"])
    em.inputs["Strength"].default_value = 40.0
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    wall = add_obj("city_wall", bpy.ops.mesh.primitive_plane_add, m, loc=(0, -2.5, 0.3), rot=(math.radians(90), 0, 0), size=6)
    # emission strength'i ramp * 40 yapmak icin math node
    mul = nt.nodes.new("ShaderNodeMath"); mul.operation = "MULTIPLY"; mul.inputs[1].default_value = 40.0
    nt.links.new(ramp.outputs["Color"], mul.inputs[0]); nt.links.new(mul.outputs[0], em.inputs["Strength"])
    wall.visible_camera = False  # kamera gormesin, sadece yansisin
    return wall

def build_lights():
    def area(name, loc, rot, energy, size, color=(1, 1, 1)):
        bpy.ops.object.light_add(type="AREA", location=loc, rotation=rot)
        l = bpy.context.active_object; l.name = name
        l.data.energy = energy; l.data.size = size; l.data.color = color
        return l
    area("key", (0.6, -0.5, 0.6), (math.radians(50), 0, math.radians(50)), 60, 0.5, (1.0, 0.93, 0.85))
    area("rim", (-0.5, 0.6, 0.4), (math.radians(-60), 0, math.radians(-140)), 90, 0.3, (0.88, 0.94, 1.0))
    area("fill", (-0.7, -0.6, 0.1), (math.radians(80), 0, math.radians(-50)), 15, 1.2)
    w = bpy.context.scene.world or bpy.data.worlds.new("World"); bpy.context.scene.world = w
    w.use_nodes = True; bg = w.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.01, 0.011, 0.015, 1); bg.inputs["Strength"].default_value = 1.0

def build_floor():
    m = mat_principled("floor", (0.03, 0.032, 0.04, 1), rough=0.25)
    add_obj("floor", bpy.ops.mesh.primitive_plane_add, m, loc=(0, 0, -0.06), size=8)

# ---------------------------------------------------------------- camera
def make_camera(focal=50, fstop=2.8, focus=None):
    bpy.ops.object.camera_add(location=(0, -1, 0), rotation=(math.radians(90), 0, 0))
    cam = bpy.context.active_object; cam.name = "cam"
    cam.data.lens = focal; cam.data.dof.use_dof = True; cam.data.dof.aperture_fstop = fstop
    if focus is not None: cam.data.dof.focus_object = focus
    bpy.context.scene.camera = cam
    return cam

def look_at(cam, target):
    c = cam.constraints.new("TRACK_TO"); c.target = target; c.track_axis = "TRACK_NEGATIVE_Z"; c.up_axis = "UP_Y"

def key_loc(o, frame, loc):
    o.location = loc; o.keyframe_insert("location", frame=frame)

def _fcurves(action):
    """Blender 4.x: action.fcurves. Blender 5.x (katmanli action): layers/strips/channelbags."""
    if hasattr(action, "fcurves"): return list(action.fcurves)
    out = []
    for layer in action.layers:
        for strip in layer.strips:
            for cb in strip.channelbags: out.extend(cb.fcurves)
    return out

def ease_all(o):
    if o.animation_data and o.animation_data.action:
        for fc in _fcurves(o.animation_data.action):
            for kp in fc.keyframe_points: kp.interpolation = "BEZIER"; kp.easing = "EASE_IN_OUT"

# ---------------------------------------------------------------- shots
def shot_SH010(glasses):
    """Makro, cam uzerine yavas dolly-in. Sehir isiklari camda yansir."""
    n = SHOTS["SH010"]["frames"]
    build_city_lights()
    tgt = bpy.data.objects.new("focus_010", None); bpy.context.collection.objects.link(tgt); tgt.location = (0.036, 0, 0)
    cam = make_camera(focal=85, fstop=2.8, focus=tgt)
    key_loc(cam, 1, (0.18, -0.42, 0.06)); key_loc(cam, n, (0.06, -0.19, 0.02))
    ease_all(cam); look_at(cam, tgt)
    glasses.rotation_euler = (0, 0, math.radians(-12))
    return n

def shot_SH050(glasses, phone, watch):
    """Bullet time: uc urun havada donmus, kamera 180 derece orbit + hafif yukselme."""
    n = SHOTS["SH050"]["frames"]
    glasses.location = (0, 0, 0.02); glasses.rotation_euler = (math.radians(12), 0, math.radians(25))
    phone.location = (-0.16, 0.05, 0.0); phone.rotation_euler = (math.radians(-10), math.radians(15), math.radians(-30))
    watch.location = (0.15, 0.04, -0.01); watch.rotation_euler = (math.radians(15), math.radians(-20), math.radians(40))
    pivot = bpy.data.objects.new("orbit_pivot", None); bpy.context.collection.objects.link(pivot)
    cam = make_camera(focal=50, fstop=4.0, focus=glasses)
    cam.parent = pivot; cam.location = (0, -0.75, 0.12)
    look_at(cam, glasses)
    pivot.rotation_euler = (0, 0, math.radians(-90)); pivot.keyframe_insert("rotation_euler", frame=1)
    pivot.rotation_euler = (0, 0, math.radians(90));  pivot.keyframe_insert("rotation_euler", frame=n)
    key_loc(pivot, 1, (0, 0, -0.03)); key_loc(pivot, n, (0, 0, 0.06))
    ease_all(pivot)
    # havada asili his: her urun cok hafif sallanir
    for o, amp in ((glasses, 2), (phone, 3), (watch, 3)):
        r = o.rotation_euler.copy()
        o.keyframe_insert("rotation_euler", frame=1)
        o.rotation_euler = (r[0] + math.radians(amp), r[1], r[2] - math.radians(amp)); o.keyframe_insert("rotation_euler", frame=n)
        ease_all(o)
    return n

def shot_SH060(glasses, phone, watch):
    """Genis plan, pull-out. Logo ve cagri metni belirir."""
    n = SHOTS["SH060"]["frames"]
    glasses.location = (0, 0, 0.0); glasses.rotation_euler = (0, 0, math.radians(-20))
    phone.location = (-0.14, 0.06, 0.0); phone.rotation_euler = (math.radians(-6), 0, math.radians(-15))
    watch.location = (0.13, 0.05, -0.02); watch.rotation_euler = (math.radians(10), 0, math.radians(20))
    tgt = bpy.data.objects.new("focus_060", None); bpy.context.collection.objects.link(tgt); tgt.location = (0, 0.02, 0.02)
    cam = make_camera(focal=40, fstop=5.6, focus=tgt)
    key_loc(cam, 1, (0.1, -0.7, 0.12)); key_loc(cam, n, (0.05, -1.5, 0.28))
    ease_all(cam); look_at(cam, tgt)
    mat_txt = mat_principled("text", (0.9, 0.9, 0.92, 1), rough=0.4, emission=(1, 1, 1, 1), emission_strength=0.6)
    for name, body, size, z, f_in in (("logo", "evaglass", 0.09, 0.16, int(n*0.35)), ("cta", "Uygulamayi indir", 0.032, 0.11, int(n*0.55))):
        bpy.ops.object.text_add(location=(0, 0.25, z), rotation=(math.radians(90), 0, 0))
        t = bpy.context.active_object; t.name = name; t.data.body = body; t.data.size = size; t.data.align_x = "CENTER"
        t.data.extrude = 0.001; t.data.materials.append(mat_txt)
        t.scale = (0, 0, 0); t.keyframe_insert("scale", frame=f_in)
        t.scale = (1, 1, 1); t.keyframe_insert("scale", frame=f_in + 14)
        ease_all(t)
    return n

# ---------------------------------------------------------------- render
def setup_render(n_frames, out_dir, shot):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = args.samples; sc.cycles.use_denoising = True
    sc.render.fps = FPS; sc.frame_start = 1; sc.frame_end = n_frames
    sc.render.resolution_x = 1920; sc.render.resolution_y = 1080; sc.render.resolution_percentage = args.res
    sc.render.image_settings.file_format = "PNG"; sc.render.image_settings.color_mode = "RGB"
    sc.render.film_transparent = False
    sc.view_settings.view_transform = "AgX" if "AgX" in [i.identifier for i in sc.view_settings.bl_rna.properties["view_transform"].enum_items] else "Filmic"
    sc.render.filepath = os.path.join(os.path.abspath(out_dir), shot, "frame_")
    if args.device.upper() != "CPU":
        try:
            prefs = bpy.context.preferences.addons["cycles"].preferences
            prefs.compute_device_type = args.device.upper()
            prefs.get_devices()
            for d in prefs.devices: d.use = True
            sc.cycles.device = "GPU"
            print("GPU cihazlari:", [(d.name, d.type, d.use) for d in prefs.devices])
        except Exception as e:
            print("GPU secilemedi, CPU ile devam:", e); sc.cycles.device = "CPU"

def build_common():
    clear_scene()
    mat_frame = mat_principled("frame", (0.010, 0.010, 0.011, 1), rough=0.48)  # mat siyah plastik
    mat_lens = mat_principled("lens", (1.0, 1.0, 1.0, 1), rough=0.01, transmission=1.0, ior=1.5)
    mat_body = mat_principled("device_body", (0.05, 0.05, 0.055, 1), rough=0.28, metal=0.6)
    mat_phone_scr = screen_material("phone_screen", args.screen_phone, (0.05, 0.35, 0.55, 1))
    mat_watch_scr = screen_material("watch_screen", args.screen_watch, (0.08, 0.5, 0.45, 1))
    glasses = import_glasses(args.glasses) if args.glasses else build_glasses(mat_frame, mat_lens)
    phone = build_phone(mat_body, mat_phone_scr); watch = build_watch(mat_body, mat_watch_scr)
    build_floor(); build_lights()
    return glasses, phone, watch

def render_shot(shot):
    glasses, phone, watch = build_common()
    if shot == "SH010":
        for root in (phone, watch):   # bos parent'i gizlemek cocuklari gizlemez; hepsini gez
            for o in [root] + list(root.children_recursive): o.hide_render = True; o.hide_viewport = True
        n = shot_SH010(glasses)
    elif shot == "SH050": n = shot_SH050(glasses, phone, watch)
    elif shot == "SH060": n = shot_SH060(glasses, phone, watch)
    else: raise SystemExit(f"bilinmeyen plan: {shot}")
    setup_render(n, args.out, shot)
    if args.save:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.save.replace(".blend", f"_{shot}.blend")))
    print(f"[{shot}] {SHOTS[shot]['desc']} - {n} kare @ {FPS} fps, {args.res}%, {args.samples} sample, {args.device}")
    if args.frame is not None:
        bpy.context.scene.frame_set(args.frame)
        bpy.context.scene.render.filepath = os.path.join(os.path.abspath(args.out), shot, f"test_{args.frame:04d}.png")
        bpy.ops.render.render(write_still=True)
    else:
        bpy.ops.render.render(animation=True)

shots = list(SHOTS) if args.shot == "all" else [args.shot]
for s in shots: render_shot(s)
print("bitti:", shots)
