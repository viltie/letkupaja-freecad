# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""FreeCAD-geometria: yksinkertaistetut liittimet (envelope.py -> Part-kappaleet), letkun keskilinja ja pyyhkäisy,
liitännän tunnistus valitusta reunasta tai pinnasta sekä Letkupaja-STEP-mallin holkkien tunnistus.

Toimii ilman käyttöliittymää (freecadcmd), joten tämä testataan oikealla FreeCADilla.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Sequence

import FreeCAD as App  # type: ignore
import Part  # type: ignore

from . import envelope as E
from .core import L
from .routing import PlacedEnd

Vec3 = tuple[float, float, float]


def V(p: Sequence[float]) -> App.Vector:
    return App.Vector(float(p[0]), float(p[1]), float(p[2]))


def T(v: App.Vector) -> Vec3:
    return (float(v.x), float(v.y), float(v.z))


def placement_of(end: PlacedEnd) -> App.Placement:
    m = end.matrix()
    return App.Placement(App.Matrix(*[m[r][c] for r in range(4) for c in range(4)]))


def placement_from_frame(origin: Sequence[float], x: Sequence[float], y: Sequence[float]) -> App.Placement:
    xo = L.vec.unit(x)
    yo = L.vec.perpendicular(xo, y)
    zo = L.vec.cross(xo, yo)
    vals = [xo[0], yo[0], zo[0], origin[0], xo[1], yo[1], zo[1], origin[1], xo[2], yo[2], zo[2], origin[2], 0.0, 0.0, 0.0, 1.0]
    return App.Placement(App.Matrix(*[float(v) for v in vals]))


# ---------------------------------------------------------------------------------------------- muodot


def _basis(axis: Sequence[float], ref: Sequence[float]) -> tuple[Vec3, Vec3, Vec3]:
    u = L.vec.unit(axis)
    v = L.vec.perpendicular(u, ref)
    return u, v, L.vec.cross(u, v)


def _pt(o: Sequence[float], u: Vec3, v: Vec3, a: float, r: float) -> App.Vector:
    return V((o[0] + u[0] * a + v[0] * r, o[1] + u[1] * a + v[1] * r, o[2] + u[2] * a + v[2] * r))


def solid_revolve(s: E.Revolve) -> Part.Shape | None:
    if len(s.profile) < 3:
        return None
    u, v, _w = _basis(s.axis, s.ref)
    pts = [_pt(s.origin, u, v, a, r) for a, r in s.profile]
    pts.append(pts[0])
    face = Part.Face(Part.makePolygon(pts))
    return face.revolve(V(s.origin), V(u), 360)


def solid_hex(s: E.Hex) -> Part.Shape | None:
    length = s.u1 - s.u0
    if length < 0.1 or s.af < 1:
        return None
    u, v, w = _basis(s.axis, s.ref)
    rc = s.af / 2 / math.cos(math.radians(30))
    base = L.vec.madd(s.origin, u, s.u0)
    pts = []
    for k in range(6):
        th = math.radians(30 + 60 * k)
        pts.append(V(L.vec.add(base, L.vec.add(L.vec.mul(v, rc * math.cos(th)), L.vec.mul(w, rc * math.sin(th))))))
    pts.append(pts[0])
    prism = Part.Face(Part.makePolygon(pts)).extrude(V(L.vec.mul(u, length)))
    if not s.chamfer:
        return prism
    # 30° viisteet kuten STEP-mallin kuusiossa: päätypinnan säde 0,95·af/2
    r_face = 0.95 * s.af / 2
    kc = 1 / math.tan(math.radians(30))
    dd = min(length / 2, (rc * 1.02 - r_face) / kc)
    r_mid = r_face + dd * kc
    prof = [(s.u0, 0.0), (s.u0, r_face), (s.u0 + dd, r_mid)]
    if length - 2 * dd > 1e-3:
        prof.append((s.u1 - dd, r_mid))
    prof += [(s.u1, r_face), (s.u1, 0.0)]
    try:
        cham = solid_revolve(E.Revolve(s.origin, s.axis, s.ref, prof))
        out = prism.common(cham) if cham is not None else prism
        return out if out.Volume > 0.5 * prism.Volume else prism
    except Part.OCCError:
        return prism


def solid_cyl(s: E.Cyl) -> Part.Shape | None:
    if s.length < 0.02 or s.radius < 0.02:
        return None
    return Part.makeCylinder(float(s.radius), float(s.length), V(s.p0), V(L.vec.unit(s.direction)))


def solid_bend(s: E.Bend) -> Part.Shape | None:
    if s.radius < 0.02 or abs(s.angle) < 0.01:
        return None
    circle = Part.makeCircle(float(s.radius), V(s.start), V(L.vec.unit(s.tangent)))
    face = Part.Face(Part.Wire(circle))
    return face.revolve(V(s.center), V(L.vec.unit(s.axis)), float(s.angle))


def solid(s: E.Shape) -> Part.Shape | None:
    if isinstance(s, E.Revolve):
        return solid_revolve(s)
    if isinstance(s, E.Hex):
        return solid_hex(s)
    if isinstance(s, E.Cyl):
        return solid_cyl(s)
    if isinstance(s, E.Bend):
        return solid_bend(s)
    raise TypeError(type(s))


def fuse(shapes: Iterable[Part.Shape | None]) -> Part.Shape | None:
    items = [s for s in shapes if s is not None and not s.isNull()]
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    out = items[0].fuse(items[1:])
    try:
        out = out.removeSplitter()
    except Part.OCCError:
        pass
    return _single_solid(out)


def _single_solid(sh: Part.Shape) -> Part.Shape:
    sols = sh.Solids
    if len(sols) == 1:
        return sols[0]
    return sh


def end_shapes(env: E.EndEnvelope) -> dict[str, Part.Shape | None]:
    """Pään kappaleet paikallisessa kehyksessä: fitting (liitin), ferrule (holkki), extras (pultti ja tiivisteet)."""
    head = fuse(solid(s) for s in env.head)
    if head is not None and env.head_cuts:
        cut = fuse(solid(s) for s in env.head_cuts)
        if cut is not None:
            try:
                head = _single_solid(head.cut(cut))
            except Part.OCCError:
                pass
    fitting = fuse([head] + [solid(s) for s in env.nose] + [solid(s) for s in env.body])
    ferrule = solid(env.ferrule)
    extras = fuse(solid(s) for s in env.extras) if env.extras else None
    return {"fitting": fitting, "ferrule": ferrule, "extras": extras}


def placed_end_shapes(end: PlacedEnd, cfg: dict[str, Any], thread_od: float = 0.0) -> dict[str, Part.Shape | None]:
    """Pään kappaleet maailman koordinaateissa."""
    env = E.end_envelope(cfg[end.key], cfg["size"], cfg["hose"], thread_od)
    shapes = end_shapes(env)
    pl = placement_of(end)
    out: dict[str, Part.Shape | None] = {}
    for k, sh in shapes.items():
        if sh is None:
            out[k] = None
            continue
        c = sh.copy()
        c.Placement = pl.multiply(c.Placement)
        out[k] = c
    return out


# ---------------------------------------------------------------------------------------------- letku


def centerline_edges(points: Sequence[Sequence[float]], tan_a: Sequence[float], tan_b: Sequence[float], bezier: Sequence[Sequence[Sequence[float]]] | None = None) -> Part.Edge:
    """Reitin keskilinja A -> B yhtenä BSpline-reunana.

    bezier annettuna (ytimen RouteResult.bezier): täsmälleen sama käyrä kuin ytimellä, jonka säde ja pituus
    raportoidaan. Bézier-segmentit kootaan B-splineksi jänteen pituuksilla parametrisoituna (ytimen C2-splinin oma
    parametrointi), jolloin sisäsolmujen kertaluku voidaan laskea yhteen ilman muodon muutosta (sileä pyyhkäisy).
    Muuten (vanhat tiedot) interpoloidaan pisteiden läpi."""
    if bezier:
        try:
            return _bezier_edge(bezier)
        except Exception:  # noqa: BLE001 - varalla interpolointi
            pass
    pts = [V(p) for p in points]
    # Parametrina kaaren pituus (pisteet ovat tasavälein kaaren pituuden mukaan), jolloin päätytangentit ovat
    # yksikkövektoreita eikä skaalausta tarvita (skaalaus tekisi päihin turhan jyrkän mutkan).
    params = [0.0]
    for i in range(1, len(pts)):
        params.append(params[-1] + max(1e-9, (pts[i] - pts[i - 1]).Length))
    bs = Part.BSplineCurve()
    bs.interpolate(Points=pts, Parameters=params, InitialTangent=V(L.vec.unit(tan_a)), FinalTangent=V(L.vec.unit(tan_b)), Scale=False)
    return bs.toShape()


def _bezier_edge(segs: Sequence[Sequence[Sequence[float]]]) -> Part.Edge:
    segs = [s for s in segs if L.vec.dist(s[0], s[3]) > 1e-9]
    if not segs:
        raise ValueError("empty curve")
    poles = [V(segs[0][0])]
    for s in segs:
        poles += [V(s[1]), V(s[2]), V(s[3])]
    knots = [0.0]
    for s in segs:
        knots.append(knots[-1] + L.vec.dist(s[0], s[3]))
    mults = [4] + [3] * (len(segs) - 1) + [4]
    bs = Part.BSplineCurve()
    bs.buildFromPolesMultsKnots(poles, mults, knots, False, 3)
    # Ytimen splini on C2 tällä parametroinnilla: kertaluvut 3 -> 1 (toleranssi 1 µm; jos ei onnistu, jää C0/C1)
    for i in range(2, len(knots)):
        for m in (1, 2):
            try:
                if bs.removeKnot(i, m, 1e-6):
                    break
            except Part.OCCError:
                pass
    return bs.toShape()


def hose_wire(o_a: Sequence[float], points: Sequence[Sequence[float]], o_b: Sequence[float], tan_a: Sequence[float], tan_b: Sequence[float], bezier: Any = None) -> tuple[Part.Wire, Part.Edge]:
    """Koko letkun keskilinja: suora letkun pohjasta A holkin takapintaan, BSpline, suora takapinnasta pohjaan B.
    Pituus = katkaisumitta. Palauttaa (lanka, reitin BSpline-reuna)."""
    curve = centerline_edges(points, tan_a, tan_b, bezier)
    edges = []
    if L.vec.dist(o_a, points[0]) > 1e-6:
        edges.append(Part.LineSegment(V(o_a), V(points[0])).toShape())
    edges.append(curve)
    if L.vec.dist(points[-1], o_b) > 1e-6:
        edges.append(Part.LineSegment(V(points[-1]), V(o_b)).toShape())
    return Part.Wire(edges), curve


def sweep_circle(wire: Part.Wire, start: Sequence[float], tangent: Sequence[float], radius: float) -> Part.Shape:
    """Ympyräprofiilin pyyhkäisy langan pitkin umpikappaleeksi. Ensin CorrectedFrenet; jos se epäonnistuu (OpenCascade
    ei pysty siihen tasossa olevalla U-mutkalla: BRepFill_Sweep::BuildEdge), Frenet. Ympyräprofiililla kierto ei
    muuta muotoa, joten tulos on sama; tilavuus tarkistetaan (π r² · pituus)."""
    profile = Part.Wire(Part.makeCircle(float(radius), V(start), V(L.vec.unit(tangent))))
    expect = math.pi * radius * radius * wire.Length
    err: Exception | None = None
    for frenet in (False, True):
        try:
            sh = _single_solid(wire.makePipeShell([profile], True, frenet))
        except Part.OCCError as e:
            err = e
            continue
        if sh.isValid() and sh.Solids and abs(sh.Volume / expect - 1) < 0.05:
            return sh
        err = Part.OCCError("invalid sweep")
    raise err if err is not None else Part.OCCError("sweep failed")


def curve_min_radius(edge: Part.Edge, n: int = 2000) -> tuple[float, float]:
    """Käyrän pienin kaarevuussäde näytteistämällä: (säde, kohta kaaren pituutena alusta)."""
    c = edge.Curve
    u0, u1 = edge.ParameterRange
    best, best_u = math.inf, u0
    for i in range(n + 1):
        u = u0 + (u1 - u0) * i / n
        try:
            k = c.curvature(u)
        except Part.OCCError:
            continue
        if k > 1e-12 and 1.0 / k < best:
            best, best_u = 1.0 / k, u
    at = Part.Edge(c, u0, best_u).Length if best_u > u0 else 0.0
    return best, at


# ---------------------------------------------------------------------------------------------- valinnat


def _circles(shape: Part.Shape) -> list[tuple[App.Vector, App.Vector, float]]:
    out = []
    for e in shape.Edges:
        c = e.Curve
        if isinstance(c, Part.Circle):
            out.append((c.Center, c.Axis, float(c.Radius)))
    return out


def _inside_count(owner: Part.Shape | None, pts: Sequence[App.Vector]) -> int:
    if owner is None:
        return 0
    sols = owner.Solids
    if not sols:
        return 0
    n = 0
    for p in pts:
        for s in sols:
            try:
                if s.isInside(p, 1e-4, True):
                    n += 1
                    break
            except Part.OCCError:
                pass
    return n


def _outward(center: App.Vector, axis: App.Vector, radius: float, owner: Part.Shape | None) -> tuple[App.Vector, int, int]:
    """Suunta (±axis), johon liitännästä pääsee ulos koneesta, sen "sisällä"-pisteiden määrä (0 = vapaa) ja toisen
    suunnan määrä (sama luku = suuntaa ei voi päätellä, esim. läpireikä). Testipisteet akselin molemmin puolin:
    kiekko 0,5·r ja rengas 1,3·r."""
    ax = App.Vector(axis)
    ax.normalize()
    ref = V(L.vec.perpendicular(T(ax), None))
    other = ax.cross(ref)
    eps = max(0.5, 0.15 * radius)
    scores = []
    for sgn in (1.0, -1.0):
        d = ax * sgn
        pts = []
        for rr in (0.0, 0.5 * radius, 1.3 * radius):
            for k in range(4 if rr else 1):
                th = math.pi / 2 * k + math.pi / 4
                pts.append(center + d * eps + (ref * math.cos(th) + other * math.sin(th)) * rr)
        scores.append((d, _inside_count(owner, pts)))
    (d0, s0), (d1, s1) = scores
    return (d0, s0, s1) if s0 <= s1 else (d1, s1, s0)


def port_from_subshape(sub: Part.Shape, owner: Part.Shape | None = None, picked: App.Vector | None = None) -> dict[str, Any] | None:
    """Liitäntä valitusta alielementistä (maailman koordinaateissa): ympyräreuna, lieriöpinta tai pyöreä tasopinta.
    Palauttaa {"point", "direction", "radius", "kind"} tai None. Suunta osoittaa koneesta ulos (owner-kappaleen
    perusteella); jos sitä ei voi päätellä, käytetään ympyrän akselia (käyttäjä voi kääntää)."""
    cands: list[tuple[App.Vector, App.Vector, float]] = []
    kind = ""
    if isinstance(sub, Part.Edge):
        if isinstance(sub.Curve, Part.Circle):
            c = sub.Curve
            cands = [(c.Center, c.Axis, float(c.Radius))]
            kind = "edge"
    elif isinstance(sub, Part.Face):
        surf = sub.Surface
        if isinstance(surf, Part.Cylinder):
            kind = "cylinder"
            ax = App.Vector(surf.Axis)
            ax.normalize()
            cands = [(c, ax, r) for c, a, r in _circles(sub) if abs(abs(App.Vector(a).normalize().dot(ax)) - 1) < 1e-6]
            if not cands:
                v0, v1 = sub.ParameterRange[2], sub.ParameterRange[3]
                cands = [(surf.Center + ax * v0, ax, float(surf.Radius)), (surf.Center + ax * v1, ax, float(surf.Radius))]
        elif isinstance(surf, Part.Plane):
            kind = "plane"
            normal = sub.normalAt(*_mid_uv(sub))
            outer = _circles(sub.OuterWire)
            if len(sub.OuterWire.Edges) == 1 and outer:
                cands = [(outer[0][0], normal, outer[0][2])]
            else:
                inner = [w for w in sub.Wires if not w.isSame(sub.OuterWire)]
                circ = [_circles(w)[0] for w in inner if len(w.Edges) == 1 and _circles(w)]
                if len(circ) == 1:
                    cands = [(circ[0][0], normal, circ[0][2])]
    if not cands:
        return None
    best = None
    for center, axis, radius in cands:
        d, score, other = _outward(center, axis, radius, owner)
        dist = (center - picked).Length if picked is not None else 0.0
        key = (score, dist)
        if best is None or key < best[0]:
            best = (key, center, d, radius, score == other)
    assert best is not None
    _key, center, d, radius, ambiguous = best
    if kind == "plane" and owner is None:
        d = App.Vector(cands[0][1])
    d.normalize()
    return {"point": T(center), "direction": T(d), "radius": radius, "kind": kind, "ambiguous": bool(ambiguous or owner is None)}


def _mid_uv(face: Part.Face) -> tuple[float, float]:
    u0, u1, v0, v1 = face.ParameterRange
    return (u0 + u1) / 2, (v0 + v1) / 2


def global_shape(obj: Any, sub: str = "") -> Part.Shape | None:
    """Objektin (tai sen aliobjektin polulla sub, esim. "Kone." -> "Runko.") muoto maailman koordinaateissa: kaikki
    säiliöiden (App::Part) ja linkkien (App::Link, kokoonpanot) sijoitukset huomioituina."""
    try:
        sh = Part.getShape(obj, sub) if sub else Part.getShape(obj)
    except Exception:  # noqa: BLE001
        sh = None
    if sh is None or sh.isNull():
        if sub:
            return None
        sh0 = getattr(obj, "Shape", None)
        if sh0 is None or sh0.isNull():
            return None
        sh = sh0.copy()
        try:
            sh.Placement = obj.getGlobalPlacement()
        except AttributeError:
            pass
    return sh


def sub_path(sub: str) -> str:
    """Alielementin nimestä aliobjektin polku: "Runko.Face3" -> "Runko.", "Face3" -> "", "Runko." -> "Runko."."""
    return sub[: sub.rfind(".") + 1] if "." in sub else ""


def sub_object(obj: Any, path: str) -> Any:
    """Aliobjekti polun päässä (itse objekti, jos polku on tyhjä)."""
    if not path:
        return obj
    try:
        o = obj.getSubObject(path, retType=1)
    except Exception:  # noqa: BLE001
        o = None
    return o if o is not None else obj


def shape_rotation(obj: Any, sub: str) -> list[float] | None:
    """Valitun elementin omistajan kierto maailmassa (kvaternio), jotta suunta voidaan siirtää kappaleen mukana."""
    try:
        sh = Part.getShape(obj, sub_path(sub)) if sub_path(sub) else Part.getShape(obj)
        q = sh.Placement.Rotation.Q
        return [float(v) for v in q]
    except Exception:  # noqa: BLE001
        return None


def rotate_by_change(direction: Sequence[float], q_old: Sequence[float] | None, q_new: Sequence[float] | None) -> Vec3:
    """Suunta siirrettynä omistajan kierron muutoksella (q_new · q_old⁻¹)."""
    if not q_old or not q_new:
        return L.vec.unit(direction)
    r_old = App.Rotation(*q_old)
    r_new = App.Rotation(*q_new)
    v = r_new.multVec(r_old.inverted().multVec(V(direction)))
    return L.vec.unit(T(v))


def bbox_of(obj: Any, sub: str = "") -> tuple[float, float, float, float, float, float] | None:
    sh = global_shape(obj, sub)
    if sh is None:
        return None
    try:
        b = sh.optimalBoundingBox()  # tiukka laatikko (FreeCAD 0.20+)
    except Exception:  # noqa: BLE001
        b = sh.BoundBox
    if not b.isValid():
        return None
    return (b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)


# ---------------------------------------------------------------------------------------------- Letkupaja-STEP

_END_TOKEN = re.compile(r"^([AB])\d{0,3}$")


def end_of_label(label: str) -> str | None:
    """Letkupaja-STEP-osan pää nimestä: "A-liitin", "Holkki A", "A O-rengas", "Alumiiniholkki B" -> "A"/"B"."""
    for tok in re.split(r"[\s_\-]+", label.strip()):
        m = _END_TOKEN.match(tok)
        if m:
            return m.group(1)
    return None


def part_role(label: str) -> str:
    """"ferrule" (Holkki A/B), "fitting" (A-liitin), "hose" (Letku), "protection" (Suoja) tai "other"."""
    low = label.strip().lower()
    if re.match(r"^holkki[\s_]+[ab]\d{0,3}$", low):
        return "ferrule"
    if re.match(r"^[ab][\s_]*-[\s_]*liitin\d{0,3}$", low) or re.match(r"^[ab]_liitin\d{0,3}$", low):
        return "fitting"
    if re.match(r"^letku\d{0,3}$", low):
        return "hose"
    if re.match(r"^suoja\d{0,3}$", low):
        return "protection"
    if re.match(r"^[ab][\s_]+banjopultti\d{0,3}$", low):
        return "bolt"
    return "other"


def ferrule_port(shape: Part.Shape, fitting: Part.Shape | None = None, bolt: Part.Shape | None = None) -> dict[str, Any] | None:
    """Holkin takapinnan keskipiste ja letkun lähtösuunta holkin muodosta (ympyräreunojen perusteella: takapään
    sisäreuna on letkun ulkohalkaisija, etupään sisäreuna letkun sisähalkaisija). up = kulman suunta liittimestä."""
    circ = _circles(shape)
    if len(circ) < 4:
        return None
    big = max(circ, key=lambda c: c[2])
    ax = App.Vector(big[1])
    ax.normalize()
    c0 = big[0]
    on_axis = [(c, r) for c, a, r in circ if abs(abs(App.Vector(a).normalize().dot(ax)) - 1) < 1e-4 and ((c - c0) - ax * (c - c0).dot(ax)).Length < 1e-3]
    if len(on_axis) < 4:
        return None
    ts = [((c - c0).dot(ax), r, c) for c, r in on_axis]
    t_min = min(t for t, _r, _c in ts)
    t_max = max(t for t, _r, _c in ts)
    inner_min = min(r for t, r, _c in ts if abs(t - t_min) < 1e-3)
    inner_max = min(r for t, r, _c in ts if abs(t - t_max) < 1e-3)
    if inner_max >= inner_min:
        t_rear, t_front = t_max, t_min
    else:
        t_rear, t_front = t_min, t_max
    d = ax * (1.0 if t_rear > t_front else -1.0)
    point = c0 + ax * t_rear
    up = None
    if fitting is not None and not fitting.isNull():
        up = _fitting_up(fitting, point, d, bolt)
    return {"point": T(point), "direction": T(d), "up": None if up is None else T(up), "length": abs(t_rear - t_front)}


def _center_of_mass(sh: Part.Shape) -> App.Vector | None:
    sols = sh.Solids
    if not sols:
        return None
    tot = sum(s.Volume for s in sols)
    if tot <= 0:
        return None
    acc = App.Vector(0, 0, 0)
    for s in sols:
        acc += s.CenterOfMass * (s.Volume / tot)
    return acc


def _fitting_up(fitting: Part.Shape, rear: App.Vector, d: App.Vector, bolt: Part.Shape | None = None) -> App.Vector | None:
    """Kulmaliittimen kulman suunta (painopisteen sivuttaissiirtymä) tai banjon pultin akseli (silmukan ympyrät).
    Banjon silmukka on symmetrinen, joten akselin etumerkki otetaan banjopultin kannan puolelta (Letkupajan
    mallissa kanta on +y'-puolella); ilman pulttia etumerkkiä ei voi päätellä geometriasta."""
    com = _center_of_mass(fitting)
    if com is not None:
        v = com - rear
        side = v - d * v.dot(d)
        if side.Length > 3.0:
            side.normalize()
            return side
    for c, a, r in sorted(_circles(fitting), key=lambda x: -x[2]):
        av = App.Vector(a)
        av.normalize()
        if abs(av.dot(d)) < 1e-3:
            sgn = _bolt_head_side(bolt, c, av) if bolt is not None else 0.0
            return av * (-1.0 if sgn < 0 else 1.0)
    return None


def _bolt_head_side(bolt: Part.Shape, eye_center: App.Vector, axis: App.Vector) -> float:
    """Banjopultin kuusiokannan puoli silmukan keskeltä akselin suunnassa (+1 / −1, 0 = ei tunnistettu). Kanta
    tunnistetaan tasopinnoista, joiden normaali on kohtisuorassa akseliin nähden (kuusion sivut)."""
    acc = 0.0
    n = 0
    for f in bolt.Faces:
        if not isinstance(f.Surface, Part.Plane):
            continue
        try:
            nrm = f.normalAt(*_mid_uv(f))
        except Exception:  # noqa: BLE001
            continue
        if abs(nrm.dot(axis)) < 1e-3:
            acc += (f.CenterOfMass - eye_center).dot(axis)
            n += 1
    if n < 3:
        return 0.0
    return 1.0 if acc > 0 else -1.0


def iter_leaves(roots: Iterable[Any]) -> list[dict[str, Any]]:
    """Kokoonpanopuun lehdet (kappaleet) juuriobjekteista: [{"root", "path", "obj", "label"}]. Toimii sekä
    Part::Feature-, App::Part- että App::Link-rakenteille (STEP-tuonnin eri tilat)."""
    out: list[dict[str, Any]] = []

    def rec(root: Any, obj: Any, path: str, depth: int) -> None:
        subs = []
        if depth < 12 and obj.TypeId != "Part::Feature":
            try:
                subs = list(obj.getSubObjects())
            except Exception:  # noqa: BLE001
                subs = []
        if not subs:
            label = str(getattr(obj, "Label", "") or obj.Name)
            linked = getattr(obj, "LinkedObject", None)
            if obj.TypeId.startswith("App::Link") and linked is not None and re.match(r"^Link\d*$", label):
                label = str(linked.Label)
            out.append({"root": root, "path": path, "obj": obj, "label": label})
            return
        for sname in subs:
            try:
                child = obj.getSubObject(sname, retType=1)
            except Exception:  # noqa: BLE001
                child = None
            if child is None:
                continue
            rec(root, child, path + sname, depth + 1)

    for r in roots:
        rec(r, r, "", 0)
    return out


def leaf_shape(root: Any, path: str) -> Part.Shape | None:
    """Lehden muoto maailman koordinaateissa (kaikki säiliöiden ja linkkien sijoitukset huomioituina)."""
    try:
        sh = Part.getShape(root, path) if path else Part.getShape(root)
    except Exception:  # noqa: BLE001
        return None
    if sh is None or sh.isNull() or not sh.Solids:
        return None
    return sh


def find_step_parts(roots: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Letkupaja-STEP-mallin osat nimien perusteella:
    {"A": {"ferrule": lehti, "fitting": lehti, "parts": [lehdet]}, "B": ..., "_hose": {"parts": [...]}}."""
    out: dict[str, dict[str, Any]] = {}
    for leaf in iter_leaves(roots):
        label = leaf["label"]
        role = part_role(label)
        k = end_of_label(label)
        if k is None:
            if role in ("hose", "protection"):
                out.setdefault("_hose", {"parts": []})["parts"].append(leaf)
            continue
        ent = out.setdefault(k, {"ferrule": None, "fitting": None, "bolt": None, "parts": []})
        ent["parts"].append(leaf)
        if role in ("ferrule", "fitting", "bolt") and ent[role] is None:
            ent[role] = leaf
    return out


def detect_step_ports(roots: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Holkki A ja Holkki B -> reitityksen portit {"A": {"point", "direction", "up", "source"}, ...}.
    source = {"root", "path", "fitting_path", "step": True} päivitystä varten."""
    parts = find_step_parts(roots)
    out = {}
    for k in ("A", "B"):
        ent = parts.get(k)
        if not ent or ent.get("ferrule") is None:
            continue
        fl = ent["ferrule"]
        fsh = leaf_shape(fl["root"], fl["path"])
        fit_leaf = ent.get("fitting")
        fit = leaf_shape(fit_leaf["root"], fit_leaf["path"]) if fit_leaf else None
        bolt_leaf = ent.get("bolt")
        bolt = leaf_shape(bolt_leaf["root"], bolt_leaf["path"]) if bolt_leaf else None
        p = ferrule_port(fsh, fit, bolt) if fsh is not None else None
        if p:
            p["source"] = {
                "root": fl["root"].Name,
                "doc": fl["root"].Document.Name,
                "path": fl["path"],
                "fitting_path": fit_leaf["path"] if fit_leaf else None,
                "fitting_root": fit_leaf["root"].Name if fit_leaf else None,
                "bolt_path": bolt_leaf["path"] if bolt_leaf else None,
                "bolt_root": bolt_leaf["root"].Name if bolt_leaf else None,
                "step": True,
            }
            out[k] = p
    return out


def port_from_step_source(doc: Any, source: dict[str, Any]) -> dict[str, Any] | None:
    """Tallennetun STEP-holkin portti uudelleen (malli on voinut liikkua)."""
    root = doc.getObject(str(source.get("root", "")))
    if root is None:
        return None
    fsh = leaf_shape(root, str(source.get("path") or ""))
    if fsh is None:
        return None
    fit = None
    if source.get("fitting_root"):
        froot = doc.getObject(str(source["fitting_root"]))
        if froot is not None:
            fit = leaf_shape(froot, str(source.get("fitting_path") or ""))
    bolt = None
    if source.get("bolt_root"):
        broot = doc.getObject(str(source["bolt_root"]))
        if broot is not None:
            bolt = leaf_shape(broot, str(source.get("bolt_path") or ""))
    return ferrule_port(fsh, fit, bolt)
