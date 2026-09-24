"""Procedural Blender model + Cycles render of the red/white/blue "rocket blaster".

Everything (geometry, worn-paint materials, studio lighting, camera) is built
from code, so the scene can be regenerated or tweaked from this one file.

Run with the Blender executable:
    blender -b -P blender/build_blaster.py -- --out renders/blaster.png
or with the `bpy` module from PyPI (pip install bpy):
    python3 blender/build_blaster.py --out renders/blaster.png

Useful flags: --samples N, --percent 50 (quick preview), --blend path.blend
Targets Blender 4.2+ (tested on 5.0).
"""

import argparse
import math
import os
import sys

import bpy  # must precede bmesh/mathutils when running as the pip `bpy` module
import bmesh
from mathutils import Matrix, Vector

TAU = 2.0 * math.pi
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def rad(d):
    return math.radians(d)


def parse_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else argv[1:]
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", default=os.path.join(ROOT, "renders", "blaster.png"))
    p.add_argument("--blend", default=None, help="also save the scene to this .blend")
    p.add_argument("--width", type=int, default=1448)
    p.add_argument("--height", type=int, default=1086)
    p.add_argument("--percent", type=int, default=100)
    p.add_argument("--samples", type=int, default=256)
    p.add_argument("--no-render", action="store_true")
    p.add_argument("--border", type=float, nargs=4, metavar=("X0", "X1", "Y0", "Y1"),
                   help="render only this region (0-1 fractions, y from bottom)")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Scene reset
# ---------------------------------------------------------------------------

def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    coll = bpy.data.collections.new("Blaster")
    scene.collection.children.link(coll)
    return scene, coll


# ---------------------------------------------------------------------------
# Geometry helpers (all meshes are built in world space, objects sit at origin
# so the procedural "Object" texture coordinates are continuous across parts)
# ---------------------------------------------------------------------------

def straight_frame(s):
    """Gun axis frame: point, tangent, normal (theta=0 -> +Y), binormal (theta=90 -> +Z)."""
    return Vector((s, 0.0, 0.0)), Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))


def make_bent_frame(p0, l1, bend_r, bend_deg, l2):
    """Straight run, downward arc, straight run - the curved rear grip tube."""
    p0 = Vector(p0)
    a = rad(bend_deg)
    arc = bend_r * a
    arc_c = p0 + Vector((l1, 0, -bend_r))
    p_end = arc_c + bend_r * Vector((math.sin(a), 0, math.cos(a)))
    t_end = Vector((math.cos(a), 0, -math.sin(a)))
    n = Vector((0, 1, 0))

    def frame(s):
        if s <= l1:
            p, t = p0 + Vector((s, 0, 0)), Vector((1, 0, 0))
        elif s <= l1 + arc:
            phi = (s - l1) / bend_r
            p = arc_c + bend_r * Vector((math.sin(phi), 0, math.cos(phi)))
            t = Vector((math.cos(phi), 0, -math.sin(phi)))
        else:
            p, t = p_end + t_end * (s - l1 - arc), t_end
        return p, t, n, t.cross(n)

    frame.length = l1 + arc + l2
    return frame


def ring_point(frame, s, th, r):
    p, _t, n, b = frame(s)
    return p + r * (math.cos(th) * n + math.sin(th) * b)


def frame_matrix(frame, s, th_deg, r):
    """Local frame on a swept surface: X along the axis, Z = surface normal."""
    p, t, n, b = frame(s)
    th = rad(th_deg)
    z = (math.cos(th) * n + math.sin(th) * b).normalized()
    x = t.normalized()
    y = z.cross(x)
    m = Matrix((x, y, z)).transposed().to_4x4()
    m.translation = p + z * r
    return m


def _lerp_r(r, t):
    return r[0] + (r[1] - r[0]) * t if isinstance(r, tuple) else r


def swept_panel(frame, s0, s1, th0, th1, r_in, r_out, ns=2, seg_per_turn=160):
    """Curved slab around a path (angles in degrees). r_in/r_out may be (start, end) tapers.

    Outer faces get a UV map: u runs along the path, v around it.
    """
    full = (th1 - th0) >= 359.999
    if full:
        n_th = seg_per_turn
    else:
        n_th = max(2, int(math.ceil(abs(th1 - th0) / 360.0 * seg_per_turn)) + 1)
    vden = n_th if full else n_th - 1
    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new("UVMap")
    O, I = [], []
    for i in range(ns):
        t = i / (ns - 1)
        s = s0 + (s1 - s0) * t
        ro, ri = _lerp_r(r_out, t), _lerp_r(r_in, t)
        orow, irow = [], []
        for j in range(n_th):
            th = rad(th0 + (th1 - th0) * j / vden)
            orow.append(bm.verts.new(ring_point(frame, s, th, ro)))
            irow.append(bm.verts.new(ring_point(frame, s, th, ri)))
        O.append(orow)
        I.append(irow)

    def face(vs, uvs=None):
        f = bm.faces.new(vs)
        if uvs:
            for loop, uv in zip(f.loops, uvs):
                loop[uvl].uv = uv
        return f

    jr = range(n_th) if full else range(n_th - 1)
    for i in range(ns - 1):
        u0, u1 = i / (ns - 1), (i + 1) / (ns - 1)
        for j in jr:
            j2 = (j + 1) % n_th
            v0, v1 = j / vden, (j + 1) / vden
            face([O[i][j], O[i + 1][j], O[i + 1][j2], O[i][j2]],
                 [(u0, v0), (u1, v0), (u1, v1), (u0, v1)])
            face([I[i][j2], I[i + 1][j2], I[i + 1][j], I[i][j]])
    for j in jr:
        j2 = (j + 1) % n_th
        face([O[0][j2], O[0][j], I[0][j], I[0][j2]])
        face([O[-1][j], O[-1][j2], I[-1][j2], I[-1][j]])
    if not full:
        for i in range(ns - 1):
            face([O[i][0], I[i][0], I[i + 1][0], O[i + 1][0]])
            face([O[i + 1][-1], I[i + 1][-1], I[i][-1], O[i][-1]])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def lathe(profile, n=64, closed=False):
    """Revolve a (h, r) profile around local +Z. r == 0 points become poles."""
    bm = bmesh.new()
    rings = []
    for h, r in profile:
        if r <= 1e-6:
            rings.append([bm.verts.new((0, 0, h))])
        else:
            rings.append([bm.verts.new((r * math.cos(TAU * k / n), r * math.sin(TAU * k / n), h))
                          for k in range(n)])
    pairs = list(zip(rings[:-1], rings[1:]))
    if closed:
        pairs.append((rings[-1], rings[0]))
    for a, b in pairs:
        if len(a) == 1 and len(b) == 1:
            continue
        if len(a) == 1:
            for k in range(n):
                bm.faces.new([a[0], b[k], b[(k + 1) % n]])
        elif len(b) == 1:
            for k in range(n):
                bm.faces.new([a[(k + 1) % n], a[k], b[0]])
        else:
            for k in range(n):
                bm.faces.new([a[k], a[(k + 1) % n], b[(k + 1) % n], b[k]])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def prism(outline, y0, y1):
    """Extrude a 2D outline given in the local XZ plane between y0 and y1."""
    bm = bmesh.new()
    a = [bm.verts.new((p[0], y0, p[1])) for p in outline]
    b = [bm.verts.new((p[0], y1, p[1])) for p in outline]
    bm.faces.new(a)
    bm.faces.new(b[::-1])
    n = len(outline)
    for k in range(n):
        bm.faces.new([a[k], a[(k + 1) % n], b[(k + 1) % n], b[k]])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def box(x0, x1, y0, y1, z0, z1):
    return prism([(x0, z0), (x1, z0), (x1, z1), (x0, z1)], y0, y1)


ROT_Z_TO_X = Matrix.Rotation(rad(90), 4, "Y")  # lathe axis (local Z) -> gun axis (+X)


class Builder:
    def __init__(self, coll):
        self.coll = coll
        self.count = 0

    def add(self, name, bm, mat, matrix=None, bevel=0.005, segs=2, smooth=True):
        if matrix is not None:
            bmesh.ops.transform(bm, matrix=matrix, verts=bm.verts)
        me = bpy.data.meshes.new(name)
        bm.to_mesh(me)
        bm.free()
        me.materials.append(mat)
        obj = bpy.data.objects.new(name, me)
        self.coll.objects.link(obj)
        if smooth:
            me.shade_smooth()
            me.set_sharp_from_angle(angle=rad(35))
        if bevel:
            mod = obj.modifiers.new("Bevel", "BEVEL")
            mod.width = bevel
            mod.segments = segs
            mod.limit_method = "ANGLE"
            mod.angle_limit = rad(35)
            mod.harden_normals = True
        self.count += 1
        return obj

    def panel_ring(self, name, frame, s0, s1, bounds, r_in, r_out, mat, gap=0.014, **kw):
        """A ring of curved panels separated by seams. bounds = seam angles (deg)."""
        if not bounds:
            return [self.add(name, swept_panel(frame, s0 + gap / 2, s1 - gap / 2, 0, 360,
                                               r_in, r_out, **kw),
                             mat if not callable(mat) else mat(0, 0, 360))]
        bounds = sorted(bounds)
        r_ref = max(r_out) if isinstance(r_out, tuple) else r_out
        half = math.degrees(gap / 2 / r_ref)
        objs = []
        for k, a in enumerate(bounds):
            b = bounds[(k + 1) % len(bounds)]
            if b <= a:
                b += 360
            m = mat(k, a, b) if callable(mat) else mat
            bm = swept_panel(frame, s0 + gap / 2, s1 - gap / 2, a + half, b - half, r_in, r_out, **kw)
            objs.append(self.add(f"{name}_{k}", bm, m))
        return objs


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def _sock(sockets, key):
    for s in sockets:
        if s.identifier == key:
            return s
    return sockets[key]


class Nodes:
    """Tiny helper to wire shader graphs without boilerplate."""

    def __init__(self, tree):
        self.tree = tree
        self.nodes = tree.nodes

    def new(self, kind, **props):
        n = self.nodes.new(kind)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def feed(self, sock, val):
        if isinstance(val, bpy.types.NodeSocket):
            self.tree.links.new(val, sock)
        else:
            sock.default_value = val

    def math(self, op, a, b=0.0, clamp=False):
        n = self.new("ShaderNodeMath", operation=op, use_clamp=clamp)
        self.feed(n.inputs[0], a)
        self.feed(n.inputs[1], b)
        return n.outputs[0]

    def maprange(self, v, f0, f1, t0=0.0, t1=1.0, smooth=False):
        n = self.new("ShaderNodeMapRange", clamp=True)
        if smooth:
            n.interpolation_type = "SMOOTHSTEP"
        self.feed(n.inputs["Value"], v)
        n.inputs["From Min"].default_value = f0
        n.inputs["From Max"].default_value = f1
        n.inputs["To Min"].default_value = t0
        n.inputs["To Max"].default_value = t1
        return n.outputs["Result"]

    def mix(self, fac, a, b):
        n = self.new("ShaderNodeMix", data_type="RGBA")
        self.feed(_sock(n.inputs, "Factor_Float"), fac)
        self.feed(_sock(n.inputs, "A_Color"), a)
        self.feed(_sock(n.inputs, "B_Color"), b)
        return _sock(n.outputs, "Result_Color")

    def noise(self, vec, scale, detail=4.0, rough=0.5, distortion=0.0):
        n = self.new("ShaderNodeTexNoise")
        self.feed(n.inputs["Vector"], vec)
        n.inputs["Scale"].default_value = scale
        n.inputs["Detail"].default_value = detail
        n.inputs["Roughness"].default_value = rough
        n.inputs["Distortion"].default_value = distortion
        return n

    def crackle(self, vec, scale, width):
        v = self.new("ShaderNodeTexVoronoi", feature="DISTANCE_TO_EDGE")
        self.feed(v.inputs["Vector"], vec)
        v.inputs["Scale"].default_value = scale
        return self.maprange(v.outputs["Distance"], 0.0, width, 1.0, 0.0)


def srgb(hexstr):
    """'#rrggbb' -> linear RGBA."""
    hexstr = hexstr.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(hexstr[i:i + 2], 16) / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return (*out, 1.0)


def new_material(name):
    mat = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):
        mat.use_nodes = True
    mat.node_tree.nodes.clear()
    return mat


def make_paint(name, base, chip_col, crack_col, grime_col, rough=0.42, chip_metal=0.6,
               crack_amt=1.0, chip_amt=1.0, coat=0.0, groove=None, seed=0.0):
    """Worn painted metal: grime, crackle, edge chips and flecks, all procedural.

    groove: None | "x" | "frame" - engraved lines driven by the panel UVs.
    """
    mat = new_material(name)
    N = Nodes(mat.node_tree)
    out = N.new("ShaderNodeOutputMaterial")
    bsdf = N.new("ShaderNodeBsdfPrincipled")
    N.feed(out.inputs["Surface"], bsdf.outputs["BSDF"])

    tc = N.new("ShaderNodeTexCoord")
    co = tc.outputs["Object"]
    if seed:
        off = N.new("ShaderNodeVectorMath", operation="ADD")
        N.feed(off.inputs[0], co)
        off.inputs[1].default_value = (seed, seed * 0.7, seed * 1.3)
        co = off.outputs[0]

    # large scale grime / discolouration
    g = N.noise(co, 2.2, 6.0, 0.55)
    grime = N.maprange(g.outputs["Fac"], 0.38, 0.78)
    col = N.mix(N.math("MULTIPLY", grime, 0.55), base, grime_col)

    # dirt collecting in seams and corners (ambient occlusion)
    ao = N.new("ShaderNodeAmbientOcclusion", samples=6)
    ao.inputs["Distance"].default_value = 0.06
    cavity = N.maprange(ao.outputs["AO"], 0.35, 0.95, 0.75, 0.0)
    col = N.mix(cavity, col, grime_col)

    # slightly wobbly coordinates for organic cracks
    dn = N.noise(co, 5.0, 3.0, 0.5)
    warp = N.new("ShaderNodeVectorMath", operation="MULTIPLY_ADD")
    N.feed(warp.inputs[0], dn.outputs["Color"])
    warp.inputs[1].default_value = (0.06, 0.06, 0.06)
    N.feed(warp.inputs[2], co)
    wco = warp.outputs[0]

    # craquelure in patches
    c1 = N.crackle(wco, 7.0, 0.010)
    c2 = N.crackle(wco, 17.0, 0.008)
    patch = N.maprange(N.noise(co, 1.4, 2.0, 0.5).outputs["Fac"], 0.42, 0.56)
    crack = N.math("MULTIPLY", N.math("MAXIMUM", c1, N.math("MULTIPLY", c2, 0.8)), patch)
    crack = N.math("MULTIPLY", crack, crack_amt)

    if groove:
        crack = N.math("MAXIMUM", crack, _groove(N, groove))

    # edge wear: bevel normal vs true normal finds the rounded edges
    bev = N.new("ShaderNodeBevel", samples=6)
    bev.inputs["Radius"].default_value = 0.012
    geo = N.new("ShaderNodeNewGeometry")
    dot = N.new("ShaderNodeVectorMath", operation="DOT_PRODUCT")
    N.feed(dot.inputs[0], bev.outputs["Normal"])
    N.feed(dot.inputs[1], geo.outputs["Normal"])
    edge = N.maprange(dot.outputs["Value"], 0.985, 0.88)

    chipn = N.noise(co, 24.0, 8.0, 0.62, distortion=0.5)
    chipv = N.math("ADD", chipn.outputs["Fac"], N.math("MULTIPLY", edge, 0.22))
    chip = N.maprange(chipv, 0.655, 0.685)
    fleck = N.maprange(N.noise(co, 85.0, 3.0, 0.5, distortion=0.8).outputs["Fac"], 0.70, 0.72)
    chip = N.math("MULTIPLY", N.math("MAXIMUM", chip, fleck), chip_amt)

    col = N.mix(N.math("MULTIPLY", crack, 0.85), col, crack_col)
    col = N.mix(chip, col, chip_col)
    N.feed(bsdf.inputs["Base Color"], col)

    rvar = N.maprange(N.noise(co, 9.0, 4.0, 0.5).outputs["Fac"], 0.3, 0.7, -0.08, 0.08)
    r = N.math("ADD", rough, rvar)
    r = N.math("ADD", r, N.math("MULTIPLY", chip, 0.25))
    N.feed(bsdf.inputs["Roughness"], r)
    N.feed(bsdf.inputs["Metallic"], N.math("MULTIPLY", chip, chip_metal))

    if coat:
        N.feed(bsdf.inputs["Coat Weight"], N.math("MULTIPLY", N.math("SUBTRACT", 1.0, chip), coat))
        bsdf.inputs["Coat Roughness"].default_value = 0.12

    height = N.math("ADD", N.math("MULTIPLY", crack, -0.6), N.math("MULTIPLY", chip, -1.0))
    bump = N.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.45
    bump.inputs["Distance"].default_value = 0.004
    N.feed(bump.inputs["Height"], height)
    N.feed(bsdf.inputs["Normal"], bump.outputs["Normal"])
    return mat


def _groove(N, kind):
    """Engraved line mask from panel UVs: an inner frame, optionally with an X to the centre."""
    uv = N.new("ShaderNodeUVMap", uv_map="UVMap")
    sep = N.new("ShaderNodeSeparateXYZ")
    N.feed(sep.inputs[0], uv.outputs["UV"])
    u, v = sep.outputs["X"], sep.outputs["Y"]
    e = N.math("MINIMUM", N.math("MINIMUM", u, N.math("SUBTRACT", 1.0, u)),
               N.math("MINIMUM", v, N.math("SUBTRACT", 1.0, v)))
    inset = 0.09
    g = N.math("ABSOLUTE", N.math("SUBTRACT", e, inset))
    if kind == "x":
        d1 = N.math("ABSOLUTE", N.math("SUBTRACT", u, v))
        d2 = N.math("ABSOLUTE", N.math("SUBTRACT", N.math("ADD", u, v), 1.0))
        dx = N.math("MINIMUM", d1, d2)
        dx = N.math("ADD", dx, N.math("LESS_THAN", e, inset))  # X lines only inside the frame
        g = N.math("MINIMUM", g, dx)
    return N.maprange(g, 0.004, 0.009, 1.0, 0.0, smooth=True)


def make_simple(name, color, metallic=0.0, rough=0.5):
    mat = new_material(name)
    N = Nodes(mat.node_tree)
    out = N.new("ShaderNodeOutputMaterial")
    bsdf = N.new("ShaderNodeBsdfPrincipled")
    N.feed(out.inputs["Surface"], bsdf.outputs["BSDF"])
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Metallic"].default_value = metallic
    rn = N.noise(N.new("ShaderNodeTexCoord").outputs["Object"], 30.0, 6.0, 0.6)
    N.feed(bsdf.inputs["Roughness"], N.maprange(rn.outputs["Fac"], 0.3, 0.7, rough - 0.1, rough + 0.12))
    return mat


def build_materials():
    white_kw = dict(base=srgb("#b8b4ae"), chip_col=srgb("#4e3f33"), crack_col=srgb("#645d55"),
                    grime_col=srgb("#7f776c"), rough=0.42, chip_metal=0.35)
    M = {
        "white": make_paint("PaintWhite", **white_kw),
        "white_x": make_paint("PaintWhiteXPanel", groove="x", **white_kw),
        "white_frame": make_paint("PaintWhiteFramePanel", groove="frame", **white_kw),
        "blue": make_paint("PaintBlue", base=srgb("#1d3874"), chip_col=srgb("#8a8e96"),
                           crack_col=srgb("#1a2c55"), grime_col=srgb("#15254a"), rough=0.38,
                           chip_metal=0.9, crack_amt=0.5, seed=3.1),
        "red": make_paint("PaintRed", base=srgb("#a31b1e"), chip_col=srgb("#5a1a14"),
                          crack_col=srgb("#7a0c0e"), grime_col=srgb("#7a1012"), rough=0.3,
                          chip_metal=0.3, crack_amt=0.35, chip_amt=0.6, coat=0.7, seed=7.7),
        "recess": make_paint("PaintRecess", base=srgb("#b5b0a8"), chip_col=srgb("#3a2a1e"),
                             crack_col=srgb("#6f6860"), grime_col=srgb("#6e675e"), rough=0.5,
                             crack_amt=0.6, seed=1.9),
        "metal": make_simple("DarkSteel", srgb("#5c5d60"), metallic=1.0, rough=0.38),
        "gap": make_simple("SeamShadow", srgb("#1b1a19"), metallic=0.0, rough=0.7),
    }
    return M


# ---------------------------------------------------------------------------
# The blaster
# ---------------------------------------------------------------------------

def equal_bounds(n, offset=0.0):
    return [offset + 360.0 * k / n for k in range(n)]


BUTTON = [(0, 0), (0, 0.066), (0.022, 0.066), (0.033, 0.056), (0.038, 0.034), (0.040, 0)]
WASHER = [(0, 0), (0, 0.077), (0.010, 0.077), (0.012, 0.070), (0.012, 0)]
BOLT = [(0, 0), (0, 0.019), (0.006, 0.019), (0.011, 0.014), (0.013, 0)]


def build_blaster(B, M):
    F = straight_frame

    def bolt(frame, s, th, r, scale=1.0):
        m = frame_matrix(frame, s, th, r - 0.002) @ Matrix.Scale(scale, 4)
        B.add("bolt", lathe(BOLT, 20), M["metal"], m, bevel=0)

    def button(frame, s, th, r, scale=1.0):
        m = frame_matrix(frame, s, th, r - 0.004) @ Matrix.Scale(scale, 4)
        B.add("button_washer", lathe(WASHER, 48), M["metal"], m, bevel=0)
        B.add("button", lathe(BUTTON, 48), M["red"], m @ Matrix.Translation((0, 0, 0.008)), bevel=0)

    # ---- muzzle -----------------------------------------------------------
    B.add("muzzle_rim", lathe([(-2.02, 0.455), (-2.20, 0.455), (-2.20, 0.585), (-2.02, 0.585)],
                              128, closed=True), M["red"], ROT_Z_TO_X, bevel=0.014, segs=3)
    for k in range(6):
        th = 60 * k
        rim_front = Matrix.Translation(ring_point(F, -2.20, rad(th), 0.52)) @ Matrix.Rotation(rad(-90), 4, "Y")
        B.add("rim_bolt", lathe(BOLT, 20), M["metal"], rim_front @ Matrix.Translation((0, 0, -0.002)), bevel=0)
    B.add("muzzle_floor", lathe([(-2.03, 0), (-2.03, 0.46), (-2.135, 0.46), (-2.135, 0)], 96),
          M["recess"], ROT_Z_TO_X, bevel=0.004)
    B.add("wheel_ring", lathe([(-2.13, 0.37), (-2.165, 0.37), (-2.165, 0.40), (-2.13, 0.40)],
                              96, closed=True), M["blue"], ROT_Z_TO_X, bevel=0.006)
    for k in range(8):
        rot = Matrix.Rotation(rad(22.5 + 45 * k), 4, "X")
        B.add("spoke", box(-2.16, -2.13, -0.014, 0.014, 0.05, 0.38), M["blue"], rot, bevel=0.005)
    B.add("hub", lathe([(-2.13, 0), (-2.13, 0.07), (-2.175, 0.07), (-2.185, 0.058), (-2.185, 0)], 48),
          M["metal"], ROT_Z_TO_X, bevel=0.004)
    B.add("hub_bolt", lathe(BOLT, 20), M["metal"],
          Matrix.Translation((-2.183, 0, 0)) @ Matrix.Rotation(rad(-90), 4, "Y") @ Matrix.Scale(1.4, 4), bevel=0)

    B.panel_ring("muzzle_shell_a", F, -2.03, -1.58, [20, 100, 165, 245, 305], 0.47, 0.56, M["white"])
    B.panel_ring("muzzle_shell_b", F, -1.58, -1.22, [50, 130, 200, 270, 330], 0.47, 0.56, M["white"])
    B.add("core_muzzle", swept_panel(F, -2.03, -1.21, 0, 360, 0.455, 0.535), M["gap"], bevel=0)
    for s, th in [(-1.95, 118), (-1.95, 238), (-1.66, 150), (-1.66, 212), (-1.30, 140), (-1.30, 222),
                  (-1.30, 60), (-1.95, 300), (-1.66, 20), (-1.95, 30)]:
        bolt(F, s, th, 0.56)

    # ---- front blue band --------------------------------------------------
    B.panel_ring("band1_a", F, -1.22, -1.07, equal_bounds(14), 0.50, 0.585, M["blue"])
    B.panel_ring("band1_b", F, -1.07, -0.92, equal_bounds(14, 12.9), 0.50, 0.57, M["blue"])
    B.add("core_band1", swept_panel(F, -1.22, -0.92, 0, 360, 0.30, 0.54), M["gap"], bevel=0)
    for th in (158, 202, 112, 248):
        bolt(F, -1.145, th, 0.585, 0.8)

    # ---- centre body ------------------------------------------------------
    B.panel_ring("body_front", F, -0.92, -0.78, [45, 135, 225, 315], 0.44, 0.52, M["white"])

    def body_mat(k, a, b):
        return M["blue"] if 230 <= a <= 240 else M["white"]

    B.panel_ring("body_mid", F, -0.78, 0.55, [0, 80, 140, 235, 290], 0.44, 0.52, body_mat)
    B.add("core_body", swept_panel(F, -0.92, 0.55, 0, 360, 0.30, 0.495), M["gap"], bevel=0)

    for side in (180, 0):  # camera side and its mirror
        B.add("square_panel", swept_panel(F, -0.42, 0.14, side - 24, side + 24, 0.50, 0.537),
              M["white_x"], bevel=0.006)
        bolt(F, -0.14, side, 0.537, 1.3)
        for s0, s1 in ((-0.60, -0.49), (0.25, 0.39)):
            B.add("slot_panel", swept_panel(F, s0, s1, side - 14, side + 14, 0.50, 0.531),
                  M["white_frame"], bevel=0.005)
        for s in (-0.64, 0.33):
            for d in (-32, 32):
                button(F, s, side + d, 0.52)

    # top hatch (red pill) on its steel base plate
    top = frame_matrix(F, -0.14, 90, 0.505)
    B.add("hatch_base", lathe([(0, 0), (0, 0.135), (0.02, 0.135), (0.02, 0)], 64), M["metal"],
          top @ Matrix.Diagonal((2.35, 1, 1, 1)), bevel=0.003)
    B.add("hatch", box(-0.27, 0.27, -0.115, 0.115, 0.0, 0.15), M["red"],
          top @ Matrix.Translation((0, 0, 0.01)), bevel=0.045, segs=5)
    for dx in (-0.33, 0.33):
        B.add("hatch_clip", box(-0.03, 0.03, -0.05, 0.05, 0.0, 0.035), M["metal"],
              top @ Matrix.Translation((dx, 0, 0)), bevel=0.006)

    # ---- rear blue band (tapering) ------------------------------------------
    B.panel_ring("band2_a", F, 0.55, 0.72, equal_bounds(16, 5), 0.42, 0.53, M["blue"])
    B.panel_ring("band2_b", F, 0.72, 0.88, equal_bounds(16, 16), 0.38, (0.50, 0.465), M["blue"])
    B.panel_ring("band2_c", F, 0.88, 1.05, equal_bounds(12, 3), 0.30, (0.45, 0.395), M["blue"])
    B.add("core_band2", swept_panel(F, 0.55, 1.05, 0, 360, 0.25, (0.50, 0.37)), M["gap"], bevel=0)
    for th in (150, 210, 105):
        bolt(F, 0.635, th, 0.53, 0.8)

    # neck collar + spine block over it
    B.add("neck_collar", lathe([(1.03, 0.12), (1.03, 0.335), (1.17, 0.335), (1.17, 0.12)], 96, closed=True),
          M["blue"], ROT_Z_TO_X, bevel=0.01, segs=3)
    B.add("neck_flange", lathe([(1.15, 0.1), (1.15, 0.29), (1.22, 0.29), (1.22, 0.1)], 96, closed=True),
          M["white"], ROT_Z_TO_X, bevel=0.008)
    B.add("spine", box(0.87, 1.23, -0.075, 0.075, 0.32, 0.42), M["white"], bevel=0.012, segs=3)
    B.add("belly_bracket", box(0.99, 1.25, -0.09, 0.09, -0.36, -0.26), M["blue"], bevel=0.012, segs=3)

    # ---- bent grip tube -----------------------------------------------------
    T = make_bent_frame((1.17, 0, -0.03), l1=0.2, bend_r=0.7, bend_deg=46, l2=1.45)
    L = T.length
    B.add("core_tube", swept_panel(T, 0.0, L - 0.2, 0, 360, 0.10, 0.232, ns=48), M["gap"], bevel=0)
    B.panel_ring("tube_a", T, 0.00, 0.45, [70, 200, 320], 0.19, 0.252, M["white"], ns=16)
    B.panel_ring("tube_b", T, 0.45, 1.15, [25, 160, 280], 0.19, 0.252, M["white"], ns=30)
    B.panel_ring("tube_c", T, 1.15, 1.72, [95, 215, 335], 0.19, (0.252, 0.262), M["white"], ns=12)
    B.panel_ring("tube_d", T, 1.72, L - 0.23, [40, 170, 290], 0.19, (0.262, 0.27), M["white"], ns=6)
    B.add("tube_cap", swept_panel(T, L - 0.235, L - 0.02, 0, 360, 0.12, 0.305, ns=4), M["blue"],
          bevel=0.014, segs=3)
    B.add("tube_cap_end", swept_panel(T, L - 0.03, L, 0, 360, 0.001, 0.23, ns=2), M["blue"],
          bevel=0.01, segs=2)
    for th in (135, 225, 90, 270, 180):
        bolt(T, L - 0.13, th, 0.305, 0.9)
    button(T, 0.22, 180, 0.252, 0.95)
    button(T, 0.22, 0, 0.252, 0.95)
    for s, th in ((0.75, 200), (0.75, 160), (1.4, 185), (1.4, 150)):
        bolt(T, s, th, 0.252, 0.8)

    # ---- fins ---------------------------------------------------------------
    fin(B, M, x_root=-1.00, th=104, r_surf=0.57, rake=32, length=0.44, width=0.48, lift=0.12)
    fin(B, M, x_root=-0.62, th=250, r_surf=0.515, rake=42, length=0.45, width=0.44, lift=0.11)
    fin(B, M, x_root=0.80, th=100, r_surf=0.49, rake=14, length=0.44, width=0.40, lift=0.10)


def fin(B, M, x_root, th, r_surf, rake, length, width, lift, chamfer=0.07, t=0.042, border=0.034):
    """Blue-edged white fin plate on a stem, raked forward, two red studs near the tip."""
    base = frame_matrix(straight_frame, x_root, th, r_surf)
    rk = rad(rake)
    u = Vector((math.cos(rk), math.sin(rk)))    # across the plate (local x,z)
    v = Vector((-math.sin(rk), math.cos(rk)))   # along the plate, leaning towards the muzzle
    c = Vector((0.0, lift))

    def outline(w, ln, ch, shift):
        c0 = c + v * shift
        bl, bt = c0 - u * (w / 2), c0 + u * (w / 2)
        tt, tl = bt + v * ln, bl + v * ln
        return [bl, bt, tt, tl + u * ch, tl - v * ch]

    B.add("fin_plate", prism(outline(width, length, chamfer, 0.0), -t / 2, t / 2), M["blue"], base,
          bevel=0.008, segs=2)
    B.add("fin_face", prism(outline(width - 2 * border, length - 2 * border, chamfer * 0.6, border),
                            -t / 2 - 0.006, t / 2 + 0.006), M["white"], base, bevel=0.004)
    stem = [(-0.13, -0.07), (0.13, -0.07), (0.10, lift + 0.07), (-0.09, lift + 0.07)]
    B.add("fin_stem", prism(stem, -0.032, 0.032), M["white"], base, bevel=0.01, segs=2)
    B.add("fin_foot", box(-0.19, 0.19, -0.075, 0.075, -0.06, 0.035), M["blue"], base, bevel=0.012, segs=2)
    for side in (-1, 1):
        y = side * (t / 2 + 0.006)
        rot = Matrix.Rotation(rad(-90 if side > 0 else 90), 4, "X")
        for off in (-0.2, 0.2):
            p = c + v * (length - 0.15) + u * (width * off)
            m = base @ Matrix.Translation((p.x, y - side * 0.004, p.y)) @ rot @ Matrix.Scale(0.8, 4)
            B.add("fin_stud", lathe(BUTTON, 40), M["red"], m, bevel=0)
        for bx in (-0.06, 0.06):
            m = base @ Matrix.Translation((bx, side * 0.032, lift * 0.45)) @ rot @ Matrix.Scale(0.8, 4)
            B.add("fin_bolt", lathe(BOLT, 16), M["metal"], m, bevel=0)


# ---------------------------------------------------------------------------
# Studio: world, lights, camera, render settings
# ---------------------------------------------------------------------------

def build_world(scene):
    world = bpy.data.worlds.new("Studio")
    if bpy.app.version < (5, 0, 0):
        world.use_nodes = True
    scene.world = world
    t = world.node_tree
    t.nodes.clear()
    N = Nodes(t)
    out = N.new("ShaderNodeOutputWorld")
    # what the camera sees: a flat grey backdrop, darker at the top like the reference
    win = N.new("ShaderNodeTexCoord").outputs["Window"]
    sep = N.new("ShaderNodeSeparateXYZ")
    N.feed(sep.inputs[0], win)
    grad = N.maprange(sep.outputs["Y"], 0.0, 1.0, 0.0, 1.0, smooth=True)
    backdrop = N.mix(grad, srgb("#a8a8ab"), srgb("#929295"))
    bg_cam = N.new("ShaderNodeBackground")
    N.feed(bg_cam.inputs["Color"], backdrop)
    # what the object is lit by: soft neutral studio fill, brighter overhead
    gen = N.new("ShaderNodeTexCoord").outputs["Generated"]
    sep2 = N.new("ShaderNodeSeparateXYZ")
    N.feed(sep2.inputs[0], gen)
    sky = N.mix(N.maprange(sep2.outputs["Z"], 0.3, 0.8), srgb("#6a6a6c"), srgb("#c8c8cc"))
    bg_env = N.new("ShaderNodeBackground")
    N.feed(bg_env.inputs["Color"], sky)
    bg_env.inputs["Strength"].default_value = 0.42
    lp = N.new("ShaderNodeLightPath")
    mix = N.new("ShaderNodeMixShader")
    N.feed(mix.inputs[0], lp.outputs["Is Camera Ray"])
    N.feed(mix.inputs[1], bg_env.outputs["Background"])
    N.feed(mix.inputs[2], bg_cam.outputs["Background"])
    N.feed(out.inputs["Surface"], mix.outputs["Shader"])


def area_light(coll, name, pos, target, energy, size, size_y=None, color=(1, 1, 1)):
    data = bpy.data.lights.new(name, "AREA")
    data.energy = energy
    data.color = color
    if size_y:
        data.shape = "RECTANGLE"
        data.size, data.size_y = size, size_y
    else:
        data.size = size
    obj = bpy.data.objects.new(name, data)
    coll.objects.link(obj)
    obj.location = pos
    d = Vector(target) - Vector(pos)
    obj.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    # keep the lights themselves out of reflections of the camera ray backdrop
    obj.visible_camera = False
    return obj


def build_lights(coll, target):
    t = Vector(target)
    area_light(coll, "Key", t + Vector((-2.6, -3.4, 3.6)), t, 270, 3.5, 2.5, (1.0, 0.99, 0.975))
    area_light(coll, "Fill", t + Vector((2.8, -4.2, 0.4)), t, 170, 4.5, 3.0, (0.97, 0.98, 1.0))
    area_light(coll, "Rim", t + Vector((1.5, 3.8, 2.6)), t, 260, 3.0, 1.2)
    area_light(coll, "Top", t + Vector((0.0, -0.6, 4.2)), t, 130, 5.0, 1.0)
    area_light(coll, "Under", t + Vector((-1.0, -2.5, -3.0)), t, 110, 4.5, 2.5)


def build_camera(scene, coll, target, azimuth=36.0, elevation=6.0, distance=6.3, lens=50.0, shift=(0.0, 0.0)):
    cam = bpy.data.cameras.new("Camera")
    cam.lens = lens
    cam.sensor_width = 36.0
    cam.shift_x, cam.shift_y = shift
    obj = bpy.data.objects.new("Camera", cam)
    coll.objects.link(obj)
    az, el = rad(azimuth), rad(elevation)
    d = Vector((-math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
    obj.location = Vector(target) + d * distance
    obj.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    scene.camera = obj
    return obj


def setup_render(scene, args):
    r = scene.render
    r.engine = "CYCLES"
    r.resolution_x, r.resolution_y = args.width, args.height
    r.resolution_percentage = args.percent
    r.film_transparent = False
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGB"
    r.image_settings.color_depth = "8"
    c = scene.cycles
    c.device = "CPU"
    c.samples = args.samples
    c.use_adaptive_sampling = True
    c.adaptive_threshold = 0.015
    c.use_denoising = True
    c.denoiser = "OPENIMAGEDENOISE"
    c.max_bounces = 8
    c.diffuse_bounces = 3
    c.glossy_bounces = 3
    c.transparent_max_bounces = 4
    c.transmission_bounces = 2
    c.caustics_reflective = False
    c.caustics_refractive = False
    c.sample_clamp_indirect = 8.0
    if args.border:
        r.use_border = True
        r.use_crop_to_border = True
        r.border_min_x, r.border_max_x, r.border_min_y, r.border_max_y = args.border
    scene.view_settings.view_transform = "Khronos PBR Neutral"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0


def main():
    args = parse_args()
    scene, coll = reset_scene()
    M = build_materials()
    B = Builder(coll)
    build_blaster(B, M)
    target = (-0.38, 0.0, 0.02)
    build_world(scene)
    build_lights(coll, target)
    build_camera(scene, coll, target, azimuth=39.0, elevation=1.5, distance=6.05, lens=50.0)
    setup_render(scene, args)
    print(f"built {B.count} parts")

    if args.blend:
        os.makedirs(os.path.dirname(os.path.abspath(args.blend)), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.blend))
    if not args.no_render:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        scene.render.filepath = os.path.abspath(args.out)
        bpy.ops.render.render(write_still=True)
        print("wrote", scene.render.filepath)


if __name__ == "__main__":
    main()
