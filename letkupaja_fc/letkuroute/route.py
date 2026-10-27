# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Letkun keskilinjan reititys kahden portin välille (puhdas Python, ei riippuvuuksia).

Käyrä on C2-jatkuva kuutiollinen splini (yhdistetyt Bézier-segmentit) porttien välillä: alku A.point suuntaan
A.direction, loppu B.point saapuen suunnasta -B.direction. Vapausasteet: päätytangenttien painot (λ, suhteessa
jänteen pituuteen parametrisoituun derivaattaan), välipisteet (esteiden kiertämiseen) ja vapaasti kääntyvien
kulmaliittimien kääntökulmat. Optimointi: Nelder-Mead (ilman derivaattoja).

Tilat:
* target: tavoitepituus (katkaisumitta tai kokonaismitta) -> keskilinjan pituus ratkaistaan tarkasti juurenhaulla
  yhteisestä skaalasta s (tangenttien painot ja välipisteiden poikkeama suorasta), muoto optimoidaan.
* natural: vapaa pituus, "luonnollinen" reitti: mahdollisimman tasainen kaarevuus ja suuri pienin säde,
  lievä pituussakko.
Rajoitteet: pienin kaarevuussäde >= bendR · safety, etäisyys esteisiin >= od/2 + margin, etäisyys muihin letkuihin.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import geometry as G
from .ports import EndFrame, Port, fitting_frame
from .vec import Vec3, add, cross, dist, dot, lerp, madd, mul, norm, perpendicular, reject, signed_angle, sub, transport_frame, unit, vec

Bezier = tuple[Vec3, Vec3, Vec3, Vec3]

INF = float("inf")


# ============================================================================================== esteet


@dataclass(frozen=True)
class Sphere:
    center: Vec3
    radius: float
    name: str = ""

    def distance(self, p: Vec3) -> float:
        """Etumerkillinen etäisyys pinnasta (negatiivinen sisällä)."""
        c = self.center
        return math.sqrt((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2) - self.radius

    def to_dict(self) -> dict[str, Any]:
        return {"type": "sphere", "center": list(self.center), "radius": self.radius, "name": self.name}


@dataclass(frozen=True)
class Box:
    """Akselien suuntainen laatikko (min- ja max-kulmat)."""

    min: Vec3
    max: Vec3
    name: str = ""

    def distance(self, p: Vec3) -> float:
        qx = abs(p[0] - (self.min[0] + self.max[0]) * 0.5) - (self.max[0] - self.min[0]) * 0.5
        qy = abs(p[1] - (self.min[1] + self.max[1]) * 0.5) - (self.max[1] - self.min[1]) * 0.5
        qz = abs(p[2] - (self.min[2] + self.max[2]) * 0.5) - (self.max[2] - self.min[2]) * 0.5
        out = math.sqrt(max(qx, 0.0) ** 2 + max(qy, 0.0) ** 2 + max(qz, 0.0) ** 2)
        return out + min(max(qx, qy, qz), 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "box", "min": list(self.min), "max": list(self.max), "name": self.name}


@dataclass(frozen=True)
class Capsule:
    """Kapseli: jana a-b, säde radius (pyöreät päät). Sopii putkille, sylintereille ja muille letkuille."""

    a: Vec3
    b: Vec3
    radius: float
    name: str = ""

    def distance(self, p: Vec3) -> float:
        return _seg_dist(p, self.a, self.b) - self.radius

    def to_dict(self) -> dict[str, Any]:
        return {"type": "capsule", "a": list(self.a), "b": list(self.b), "radius": self.radius, "name": self.name}


@dataclass(frozen=True)
class Cylinder:
    """Suljettu sylinteri: akseli a-b, säde radius (tasaiset päädyt)."""

    a: Vec3
    b: Vec3
    radius: float
    name: str = ""

    def distance(self, p: Vec3) -> float:
        ba = sub(self.b, self.a)
        L = norm(ba)
        if L < 1e-12:
            return Sphere(self.a, self.radius).distance(p)
        u = mul(ba, 1.0 / L)
        ap = sub(p, self.a)
        t = dot(ap, u)
        radial = norm(madd(ap, u, -t))
        dx = radial - self.radius
        dy = abs(t - L / 2) - L / 2
        return min(max(dx, dy), 0.0) + math.sqrt(max(dx, 0.0) ** 2 + max(dy, 0.0) ** 2)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "cylinder", "a": list(self.a), "b": list(self.b), "radius": self.radius, "name": self.name}


Obstacle = Sphere | Box | Capsule | Cylinder


def obstacle_from_dict(d: Mapping[str, Any]) -> Obstacle:
    """Este dictistä: {"type": "sphere", "center", "radius"} | {"type": "box", "min", "max"} |
    {"type": "capsule"|"cylinder", "a", "b", "radius"} (+ valinnainen "name")."""
    t = d.get("type")
    name = str(d.get("name", ""))
    if t == "sphere":
        return Sphere(vec(d["center"]), float(d["radius"]), name)
    if t == "box":
        lo, hi = vec(d["min"]), vec(d["max"])
        return Box(tuple(min(lo[i], hi[i]) for i in range(3)), tuple(max(lo[i], hi[i]) for i in range(3)), name)  # type: ignore[arg-type]
    if t == "capsule":
        return Capsule(vec(d["a"]), vec(d["b"]), float(d["radius"]), name)
    if t == "cylinder":
        return Cylinder(vec(d["a"]), vec(d["b"]), float(d["radius"]), name)
    raise ValueError(f"Tuntematon estetyyppi {t!r}")


def parse_obstacle(text: str) -> Obstacle:
    """Este merkkijonosta: "sphere:x,y,z:r", "box:x0,y0,z0:x1,y1,z1", "capsule:x,y,z:x,y,z:r",
    "cylinder:x,y,z:x,y,z:r" tai JSON-objekti."""
    text = text.strip()
    if text.startswith("{"):
        import json

        return obstacle_from_dict(json.loads(text))
    parts = [p.strip() for p in text.split(":")]
    t = parts[0].lower()
    try:
        if t == "sphere" and len(parts) == 3:
            return Sphere(vec(parts[1]), float(parts[2]))
        if t == "box" and len(parts) == 3:
            return obstacle_from_dict({"type": "box", "min": vec(parts[1]), "max": vec(parts[2])})
        if t in ("capsule", "cylinder") and len(parts) == 4:
            return obstacle_from_dict({"type": t, "a": vec(parts[1]), "b": vec(parts[2]), "radius": float(parts[3])})
    except ValueError as e:
        raise ValueError(f"Virheellinen este {text!r}: {e}") from None
    raise ValueError(f"Virheellinen este {text!r} (esim. sphere:0,0,0:50 tai box:0,0,0:100,50,20)")


def _seg_dist(p: Vec3, a: Vec3, b: Vec3) -> float:
    abx, aby, abz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    apx, apy, apz = p[0] - a[0], p[1] - a[1], p[2] - a[2]
    L2 = abx * abx + aby * aby + abz * abz
    t = 0.0 if L2 < 1e-24 else max(0.0, min(1.0, (apx * abx + apy * aby + apz * abz) / L2))
    dx, dy, dz = apx - abx * t, apy - aby * t, apz - abz * t
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _seg_seg(p1: Vec3, q1: Vec3, p2: Vec3, q2: Vec3) -> tuple[float, float, float]:
    """Janojen p1q1 ja p2q2 lyhin etäisyys ja parametrit (s, t) ∈ [0, 1] (Ericson, Real-Time Collision Detection)."""
    d1, d2, r = sub(q1, p1), sub(q2, p2), sub(p1, p2)
    a, e, f = dot(d1, d1), dot(d2, d2), dot(d2, r)
    if a < 1e-18 and e < 1e-18:
        return dist(p1, p2), 0.0, 0.0
    if a < 1e-18:
        s, t = 0.0, max(0.0, min(1.0, f / e))
    else:
        c = dot(d1, r)
        if e < 1e-18:
            t, s = 0.0, max(0.0, min(1.0, -c / a))
        else:
            b = dot(d1, d2)
            den = a * e - b * b
            s = max(0.0, min(1.0, (b * f - c * e) / den)) if den > 1e-18 else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t, s = 0.0, max(0.0, min(1.0, -c / a))
            elif t > 1.0:
                t, s = 1.0, max(0.0, min(1.0, (b - c) / a))
    c1, c2 = madd(p1, d1, s), madd(p2, d2, t)
    return dist(c1, c2), s, t


# ============================================================================================== muut letkut


@dataclass
class _AvoidIndex:
    """Muiden letkujen keskilinjat ruudukossa nopeaa etäisyyshakua varten."""

    segs: list[tuple[Vec3, Vec3, float]]  # (p, q, toisen letkun säde)
    cell: float
    grid: dict[tuple[int, int, int], list[int]]

    @staticmethod
    def build(items: Iterable[tuple[Sequence[Vec3], float]], reach: float) -> "_AvoidIndex | None":
        segs: list[tuple[Vec3, Vec3, float]] = []
        for pts, od in items:
            pts = [vec(p) for p in pts]
            for i in range(len(pts) - 1):
                segs.append((pts[i], pts[i + 1], float(od) / 2))
        if not segs:
            return None
        cell = max(5.0, reach)
        grid: dict[tuple[int, int, int], list[int]] = {}
        for k, (p, q, r) in enumerate(segs):
            pad = reach + r
            lo = [int(math.floor((min(p[i], q[i]) - pad) / cell)) for i in range(3)]
            hi = [int(math.floor((max(p[i], q[i]) + pad) / cell)) for i in range(3)]
            for ix in range(lo[0], hi[0] + 1):
                for iy in range(lo[1], hi[1] + 1):
                    for iz in range(lo[2], hi[2] + 1):
                        grid.setdefault((ix, iy, iz), []).append(k)
        return _AvoidIndex(segs, cell, grid)

    def clearance(self, p: Vec3, own_r: float, cap: float) -> float:
        """Pinta-pinta-etäisyys lähimpään muuhun letkuun (enintään cap)."""
        key = (int(math.floor(p[0] / self.cell)), int(math.floor(p[1] / self.cell)), int(math.floor(p[2] / self.cell)))
        best = cap
        for k in self.grid.get(key, ()):
            a, b, r = self.segs[k]
            d = _seg_dist(p, a, b) - r - own_r
            if d < best:
                best = d
        return best


def _avoid_items(avoid: Iterable[Any]) -> list[tuple[list[Vec3], float]]:
    out = []
    for it in avoid or ():
        if isinstance(it, RouteResult):
            out.append((it.points, it.od))
        elif isinstance(it, Mapping):
            out.append(([vec(p) for p in it["points"]], float(it["od"])))
        else:
            pts, od = it
            out.append(([vec(p) for p in pts], float(od)))
    return out


# ============================================================================================== Bézier-apurit


def bezier_point(seg: Bezier, t: float) -> Vec3:
    u = 1 - t
    b0, b1, b2, b3 = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    p0, p1, p2, p3 = seg
    return (b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0], b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1], b0 * p0[2] + b1 * p1[2] + b2 * p2[2] + b3 * p3[2])


def bezier_d1(seg: Bezier, t: float) -> Vec3:
    u = 1 - t
    p0, p1, p2, p3 = seg
    a, b, c = 3 * u * u, 6 * u * t, 3 * t * t
    return tuple(a * (p1[i] - p0[i]) + b * (p2[i] - p1[i]) + c * (p3[i] - p2[i]) for i in range(3))  # type: ignore[return-value]


def bezier_d2(seg: Bezier, t: float) -> Vec3:
    p0, p1, p2, p3 = seg
    u = 1 - t
    return tuple(6 * u * (p2[i] - 2 * p1[i] + p0[i]) + 6 * t * (p3[i] - 2 * p2[i] + p1[i]) for i in range(3))  # type: ignore[return-value]


def bezier_curvature(seg: Bezier, t: float) -> float:
    d1, d2 = bezier_d1(seg, t), bezier_d2(seg, t)
    s = norm(d1)
    if s < 1e-12:
        return 0.0 if norm(cross(bezier_d1(seg, min(1.0, t + 1e-6)), d2)) < 1e-12 else INF
    return norm(cross(d1, d2)) / (s * s * s)


_GL5 = (
    (-0.9061798459386640, 0.2369268850561891),
    (-0.5384693101056831, 0.4786286704993665),
    (0.0, 0.5688888888888889),
    (0.5384693101056831, 0.4786286704993665),
    (0.9061798459386640, 0.2369268850561891),
)


def bezier_length(seg: Bezier, t0: float = 0.0, t1: float = 1.0, n_sub: int = 8) -> float:
    """Kaaren pituus Gauss-Legendre-kvadratuurilla (n_sub osaväliä × 5 pistettä)."""
    p0, p1, p2, p3 = seg
    c = [3 * (p1[i] - p0[i]) for i in range(3)]
    b = [3 * (p2[i] - 2 * p1[i] + p0[i]) for i in range(3)]
    a = [p3[i] - 3 * p2[i] + 3 * p1[i] - p0[i] for i in range(3)]
    h = (t1 - t0) / n_sub
    total = 0.0
    for k in range(n_sub):
        mid = t0 + (k + 0.5) * h
        for x, w in _GL5:
            t = mid + 0.5 * h * x
            dx = (3 * a[0] * t + 2 * b[0]) * t + c[0]
            dy = (3 * a[1] * t + 2 * b[1]) * t + c[1]
            dz = (3 * a[2] * t + 2 * b[2]) * t + c[2]
            total += w * math.sqrt(dx * dx + dy * dy + dz * dz)
    return total * 0.5 * h


def curve_length(segs: Sequence[Bezier], n_sub: int = 8) -> float:
    """Yhdistetyn käyrän pituus."""
    return sum(bezier_length(s, n_sub=n_sub) for s in segs)


def line_bezier(p: Vec3, q: Vec3) -> Bezier:
    return (p, lerp(p, q, 1 / 3), lerp(p, q, 2 / 3), q)


def bezier_to_nurbs(segs: Sequence[Bezier], arc_length_knots: bool = True) -> dict[str, Any]:
    """Yhdistetty Bézier-käyrä tarkkana B-splinenä: aste 3, ohjauspisteet 3n+1, solmuvektori 3n+5
    (sisäsolmut kolminkertaisia). Solmut segmenttien kaaren pituuksista (mm) tai 0..n.
    Fusion: NurbsCurve3D.createNonRational(pisteet (cm!), 3, knots, False);
    SolidWorks: SketchManager.CreateSplinesByEqnParams2 tai Modeler.CreateBsplineCurve (metreinä)."""
    if not segs:
        raise ValueError("Tyhjä käyrä")
    cps: list[Vec3] = [segs[0][0]]
    for s in segs:
        cps += [s[1], s[2], s[3]]
    breaks = [0.0]
    for s in segs:
        breaks.append(breaks[-1] + (bezier_length(s) if arc_length_knots else 1.0))
    knots = [0.0] * 4
    for b in breaks[1:-1]:
        knots += [b] * 3
    knots += [breaks[-1]] * 4
    return {"degree": 3, "knots": knots, "control_points": [list(p) for p in cps], "rational": False}


# ============================================================================================== splini


def _clamped_spline(knots: list[Vec3], d0: Vec3, dn: Vec3) -> list[Bezier]:
    """C2-kuutiosplini pisteiden läpi jänteen pituuteen perustuvalla parametroinnilla, päätyderivaatat d0 ja dn
    (derivaatta parametrin suhteen, |d| = 1 vastaa luonnollista nopeutta). Palauttaa Bézier-segmentit."""
    n = len(knots) - 1
    h = [max(1e-6, dist(knots[i], knots[i + 1])) for i in range(n)]
    m: list[Vec3] = [d0] + [(0.0, 0.0, 0.0)] * (n - 1) + [dn]
    if n >= 2:
        # Thomas-algoritmi sisäsolmujen derivaatoille: h_i m_{i-1} + 2(h_{i-1}+h_i) m_i + h_{i-1} m_{i+1} = rhs
        N = n - 1
        lo = [0.0] * N
        di = [0.0] * N
        up = [0.0] * N
        rhs: list[list[float]] = []
        for k in range(N):
            i = k + 1
            lo[k] = h[i]
            di[k] = 2 * (h[i - 1] + h[i])
            up[k] = h[i - 1]
            r = [3 * (h[i] / h[i - 1] * (knots[i][c] - knots[i - 1][c]) + h[i - 1] / h[i] * (knots[i + 1][c] - knots[i][c])) for c in range(3)]
            rhs.append(r)
        rhs[0] = [rhs[0][c] - lo[0] * d0[c] for c in range(3)]
        rhs[-1] = [rhs[-1][c] - up[-1] * dn[c] for c in range(3)]
        cp = [0.0] * N
        dp = [[0.0, 0.0, 0.0] for _ in range(N)]
        for k in range(N):
            den = di[k] - (lo[k] * cp[k - 1] if k else 0.0)
            cp[k] = up[k] / den if k < N - 1 else 0.0
            dp[k] = [(rhs[k][c] - (lo[k] * dp[k - 1][c] if k else 0.0)) / den for c in range(3)]
        sol = [[0.0, 0.0, 0.0] for _ in range(N)]
        for k in range(N - 1, -1, -1):
            sol[k] = [dp[k][c] - (cp[k] * sol[k + 1][c] if k < N - 1 else 0.0) for c in range(3)]
        for k in range(N):
            m[k + 1] = (sol[k][0], sol[k][1], sol[k][2])
    segs: list[Bezier] = []
    for i in range(n):
        p, q = knots[i], knots[i + 1]
        segs.append((p, madd(p, m[i], h[i] / 3), madd(q, m[i + 1], -h[i] / 3), q))
    return segs


# ============================================================================================== näytteistys


@dataclass
class _Samples:
    pts: list[Vec3]
    kappa: list[float]
    s: list[float]  # likimääräinen kaaren pituus A:sta
    length: float  # Simpson
    energy: float  # ∫ κ² ds
    kmax: float
    kmax_i: int
    seg_of: list[int]
    t_of: list[float]


def _sample(segs: Sequence[Bezier], spacing: float, min_k: int = 8, max_k: int = 400) -> _Samples:
    pts: list[Vec3] = []
    kap: list[float] = []
    sacc: list[float] = []
    seg_of: list[int] = []
    t_of: list[float] = []
    length = 0.0
    energy = 0.0
    kmax = 0.0
    kmax_i = 0
    run = 0.0
    for si, (p0, p1, p2, p3) in enumerate(segs):
        cx, cy, cz = 3 * (p1[0] - p0[0]), 3 * (p1[1] - p0[1]), 3 * (p1[2] - p0[2])
        bx, by, bz = 3 * (p2[0] - 2 * p1[0] + p0[0]), 3 * (p2[1] - 2 * p1[1] + p0[1]), 3 * (p2[2] - 2 * p1[2] + p0[2])
        ax, ay, az = p3[0] - 3 * p2[0] + 3 * p1[0] - p0[0], p3[1] - 3 * p2[1] + 3 * p1[1] - p0[1], p3[2] - 3 * p2[2] + 3 * p1[2] - p0[2]
        est = dist(p0, p1) + dist(p1, p2) + dist(p2, p3)
        if est < 1e-9:
            continue
        k = int(math.ceil(est / spacing))
        k = max(min_k, min(max_k, k + (k & 1)))
        dt = 1.0 / k
        prev = None
        pdx = pdy = pdz = 0.0
        for j in range(k + 1):
            t = j * dt
            px = ((ax * t + bx) * t + cx) * t + p0[0]
            py = ((ay * t + by) * t + cy) * t + p0[1]
            pz = ((az * t + bz) * t + cz) * t + p0[2]
            d1x = (3 * ax * t + 2 * bx) * t + cx
            d1y = (3 * ay * t + 2 * by) * t + cy
            d1z = (3 * az * t + 2 * bz) * t + cz
            sp2 = d1x * d1x + d1y * d1y + d1z * d1z
            sp = math.sqrt(sp2)
            d2x, d2y, d2z = 6 * ax * t + 2 * bx, 6 * ay * t + 2 * by, 6 * az * t + 2 * bz
            crx, cry, crz = d1y * d2z - d1z * d2y, d1z * d2x - d1x * d2z, d1x * d2y - d1y * d2x
            cr = math.sqrt(crx * crx + cry * cry + crz * crz)
            kk = cr / (sp2 * sp) if sp2 > 1e-18 else 1e9
            if j and pdx * d1x + pdy * d1y + pdz * d1z <= 0.0:
                kk = 1e9
            pdx, pdy, pdz = d1x, d1y, d1z
            wj = (1.0 if j == 0 or j == k else (4.0 if j & 1 else 2.0)) * dt / 3.0
            length += wj * sp
            energy += wj * kk * kk * sp
            p = (px, py, pz)
            if prev is not None:
                run += math.sqrt((px - prev[0]) ** 2 + (py - prev[1]) ** 2 + (pz - prev[2]) ** 2)
            prev = p
            pts.append(p)
            kap.append(kk)
            sacc.append(run)
            seg_of.append(si)
            t_of.append(t)
            if kk > kmax:
                kmax, kmax_i = kk, len(kap) - 1
    return _Samples(pts, kap, sacc, length, energy, kmax, kmax_i, seg_of, t_of)


def _sample_fast(segs: Sequence[Bezier], k: int = 24) -> tuple[float, float, float]:
    """(pituus, ∫κ²ds, κmax) Simpsonilla, k väliä segmenttiä kohden; ei pisteitä (esteetön optimointi)."""
    length = 0.0
    energy = 0.0
    kmax = 0.0
    dt = 1.0 / k
    w_end, w_odd, w_even = dt / 3.0, 4.0 * dt / 3.0, 2.0 * dt / 3.0
    for p0, p1, p2, p3 in segs:
        cx, cy, cz = 3 * (p1[0] - p0[0]), 3 * (p1[1] - p0[1]), 3 * (p1[2] - p0[2])
        bx, by, bz = 3 * (p2[0] - 2 * p1[0] + p0[0]), 3 * (p2[1] - 2 * p1[1] + p0[1]), 3 * (p2[2] - 2 * p1[2] + p0[2])
        ax, ay, az = p3[0] - 3 * p2[0] + 3 * p1[0] - p0[0], p3[1] - 3 * p2[1] + 3 * p1[1] - p0[1], p3[2] - 3 * p2[2] + 3 * p1[2] - p0[2]
        a3x, a3y, a3z, b2x, b2y, b2z = 3 * ax, 3 * ay, 3 * az, 2 * bx, 2 * by, 2 * bz
        a6x, a6y, a6z = 6 * ax, 6 * ay, 6 * az
        px = py = pz = 0.0
        for j in range(k + 1):
            t = j * dt
            d1x = (a3x * t + b2x) * t + cx
            d1y = (a3y * t + b2y) * t + cy
            d1z = (a3z * t + b2z) * t + cz
            d2x, d2y, d2z = a6x * t + b2x, a6y * t + b2y, a6z * t + b2z
            sp2 = d1x * d1x + d1y * d1y + d1z * d1z
            sp = math.sqrt(sp2)
            crx, cry, crz = d1y * d2z - d1z * d2y, d1z * d2x - d1x * d2z, d1x * d2y - d1y * d2x
            cr = math.sqrt(crx * crx + cry * cry + crz * crz)
            if sp2 > 1e-18:
                kk = cr / (sp2 * sp)
            else:
                kk = 1e9
            # Kärki tai taittuminen näytteiden välissä: derivaatan suunta kääntyy yli 90°
            if j and px * d1x + py * d1y + pz * d1z <= 0.0:
                kk = 1e9
            px, py, pz = d1x, d1y, d1z
            w = w_end if (j == 0 or j == k) else (w_odd if j & 1 else w_even)
            length += w * sp
            energy += w * kk * kk * sp
            if kk > kmax:
                kmax = kk
    return length, energy, kmax


# ============================================================================================== optimointi


def nelder_mead(f: Callable[[list[float]], float], x0: Sequence[float], steps: Sequence[float], max_evals: int = 1000, ftol: float = 1e-9, xtol: float = 1e-7) -> tuple[list[float], float, int]:
    """Nelder-Mead adaptiivisilla kertoimilla (Gao & Han 2012). Palauttaa (x, f(x), arviointeja)."""
    n = len(x0)
    if n == 0:
        return [], f([]), 1
    alpha, beta, gamma, delta = 1.0, 1.0 + 2.0 / n, 0.75 - 1.0 / (2 * n), 1.0 - 1.0 / n
    simplex = [list(x0)]
    for i in range(n):
        x = list(x0)
        x[i] += steps[i]
        simplex.append(x)
    fs = [f(x) for x in simplex]
    evals = n + 1
    while evals < max_evals:
        order = sorted(range(n + 1), key=lambda i: fs[i])
        simplex = [simplex[i] for i in order]
        fs = [fs[i] for i in order]
        if abs(fs[-1] - fs[0]) <= ftol * (abs(fs[0]) + 1e-12) and max(abs(simplex[i][j] - simplex[0][j]) for i in range(1, n + 1) for j in range(n)) <= xtol:
            break
        cen = [sum(simplex[i][j] for i in range(n)) / n for j in range(n)]
        xr = [cen[j] + alpha * (cen[j] - simplex[-1][j]) for j in range(n)]
        fr = f(xr)
        evals += 1
        if fr < fs[0]:
            xe = [cen[j] + beta * (xr[j] - cen[j]) for j in range(n)]
            fe = f(xe)
            evals += 1
            if fe < fr:
                simplex[-1], fs[-1] = xe, fe
            else:
                simplex[-1], fs[-1] = xr, fr
        elif fr < fs[-2]:
            simplex[-1], fs[-1] = xr, fr
        else:
            if fr < fs[-1]:
                xc = [cen[j] + gamma * (xr[j] - cen[j]) for j in range(n)]
            else:
                xc = [cen[j] + gamma * (simplex[-1][j] - cen[j]) for j in range(n)]
            fc = f(xc)
            evals += 1
            if fc < min(fr, fs[-1]):
                simplex[-1], fs[-1] = xc, fc
            else:
                for i in range(1, n + 1):
                    simplex[i] = [simplex[0][j] + delta * (simplex[i][j] - simplex[0][j]) for j in range(n)]
                    fs[i] = f(simplex[i])
                    evals += 1
    i = min(range(n + 1), key=lambda i: fs[i])
    return simplex[i], fs[i], evals


def _solve_scale(length_of: Callable[[float], float], target: float, s_guess: float, tol: float, len0: float | None = None) -> tuple[float, float]:
    """Ratkaise s >= 0 siten, että length_of(s) = target (pituus kasvaa s:n mukana). len0 = length_of(0), jos tiedossa
    (suora jana). Sekantti lämpimästä alusta ja Illinois-haarukointi. Palauttaa (s, virhe)."""
    f0 = (length_of(0.0) if len0 is None else len0) - target
    if f0 >= -tol:
        # Suora jana riittää (tai tavoite on lyhyempi). Samansuuntaiset kahvat pitävät käyrän suorana: käytä
        # luonnollista skaalaa, jos pituus pysyy samana (ei nolladerivaattaa päissä).
        if f0 <= tol:
            f1 = length_of(1.0) - target
            if abs(f1) <= tol:
                return 1.0, f1
        return 0.0, f0
    lo, flo = 0.0, f0
    g = max(1e-3, s_guess)
    fg = length_of(g) - target
    if abs(fg) <= tol:
        return g, fg
    if fg < 0:
        lo, flo = g, fg
        hi, fhi = None, None
        # sekantti (0, f0) - (g, fg) eteenpäin, kunnes ylitetään
        step = g
        for _ in range(60):
            nxt = lo + max(step * 0.25, (-flo) * (lo - 0.0) / max(1e-12, flo - f0)) if lo > 0 else lo + step
            nxt = min(nxt, lo * 4 + 1.0)
            fn = length_of(nxt) - target
            if abs(fn) <= tol:
                return nxt, fn
            if fn > 0:
                hi, fhi = nxt, fn
                break
            step = nxt - lo
            lo, flo = nxt, fn
        if hi is None:
            return lo, flo
    else:
        hi, fhi = g, fg
        # sekantti (0, f0) - (g, fg)
        nxt = g - fg * g / (fg - f0)
        fn = length_of(nxt) - target
        if abs(fn) <= tol:
            return nxt, fn
        if fn < 0:
            lo, flo = nxt, fn
        else:
            hi, fhi = nxt, fn
    side = 0
    s, fs = hi, fhi
    for _ in range(80):
        s = hi - fhi * (hi - lo) / (fhi - flo) if fhi != flo else 0.5 * (lo + hi)
        fs = length_of(s) - target
        if abs(fs) <= tol:
            return s, fs
        if fs > 0:
            hi, fhi = s, fs
            if side == 1:
                flo *= 0.5
            side = 1
        else:
            lo, flo = s, fs
            if side == -1:
                fhi *= 0.5
            side = -1
        if hi - lo < 1e-14:
            break
    return s, fs


# ============================================================================================== tulos


@dataclass
class SplineHint:
    """CAD-splinin ohjeet: läpikulkupisteet ja päätytangentit (kulkusuunta A -> B), kahvat ja painot."""

    through_points: list[Vec3]
    tangent_a: Vec3  # kulkusuunta A:ssa (= A.direction)
    tangent_b: Vec3  # kulkusuunta B:ssä (= -B.direction)
    handle_a: float  # ensimmäisen kaarisegmentin Bézier-kahvan pituus |P1 - P0| (mm)
    handle_b: float  # viimeisen kaarisegmentin kahvan pituus |P3 - P2| (mm)
    weight_a: float  # päätyderivaatan suuruus suhteessa jänteen pituudella parametrisoituun (1 = luonnollinen)
    weight_b: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "through_points": [list(p) for p in self.through_points],
            "tangent_a": list(self.tangent_a),
            "tangent_b": list(self.tangent_b),
            "handle_a": self.handle_a,
            "handle_b": self.handle_b,
            "weight_a": self.weight_a,
            "weight_b": self.weight_b,
        }


@dataclass
class RouteResult:
    ok: bool
    warnings: list[str]
    mode: str  # "target" | "natural"
    points: list[Vec3]  # keskilinja A -> B tasavälein kaaren pituuden mukaan
    length: float  # keskilinjan pituus porttien välillä (mm)
    min_radius: float  # pienin kaarevuussäde (mm), inf = suora
    min_radius_at: float  # kohta kaaren pituutena A:sta (mm)
    required_radius: float  # bendR · safety
    od: float  # törmäystarkastelun letkun ulkohalkaisija
    min_clearance: float | None  # pienin pinta-pinta-etäisyys esteisiin (mm); None = ei esteitä
    min_clearance_at: float | None
    min_clearance_hoses: float | None  # pienin etäisyys muihin letkuihin (pinnasta pintaan)
    insertion_a: float | None
    insertion_b: float | None
    cut_length: float | None  # katkaisumitta = length + insertion_a + insertion_b
    cut_length_mm: int | None  # pyöristetty tilausta varten
    total_length: float | None  # kokonaismitta = cut + C_A + C_B (vain konfiguraation kanssa)
    orientation_deg: float | None  # kiertokulma (B myötäpäivään A:sta katsottuna), jos molemmilla päillä up
    port_a: Port
    port_b: Port
    swing_a_deg: float | None
    swing_b_deg: float | None
    spline: SplineHint
    bezier: list[Bezier]
    nurbs: dict[str, Any]
    config: dict[str, Any] | None = None  # Letkupajan konfiguraatio (lengthMode "hose", lengthMm = cut_length_mm)
    link: str | None = None
    frames: dict[str, Any] | None = None  # FittingPort-päiden EndFrame-tiedot
    stats: dict[str, Any] = field(default_factory=dict)

    def xyz_text(self, unit: str = "mm", which: str = "points") -> str:
        """Pisteet tekstinä "x y z" riveittäin halutussa yksikössä (SolidWorks Curve Through XYZ Points, Fusion CSV).
        which: "points" (tiheä keskilinja) tai "through" (splinin läpikulkupisteet)."""
        from .vec import from_mm

        pts = self.points if which == "points" else self.spline.through_points
        return "\n".join(" ".join(f"{from_mm(c, unit):.6f}" for c in p) for p in pts) + "\n"

    def to_dict(self) -> dict[str, Any]:
        def fx(v: Any) -> Any:
            if isinstance(v, float) and not math.isfinite(v):
                return None
            return v

        return {
            "ok": self.ok,
            "warnings": list(self.warnings),
            "mode": self.mode,
            "length": self.length,
            "min_radius": fx(self.min_radius),
            "min_radius_at": self.min_radius_at,
            "required_radius": self.required_radius,
            "od": self.od,
            "min_clearance": self.min_clearance,
            "min_clearance_at": self.min_clearance_at,
            "min_clearance_hoses": self.min_clearance_hoses,
            "insertion_a": self.insertion_a,
            "insertion_b": self.insertion_b,
            "cut_length": self.cut_length,
            "cut_length_mm": self.cut_length_mm,
            "total_length": self.total_length,
            "orientation_deg": self.orientation_deg,
            "port_a": self.port_a.to_dict(),
            "port_b": self.port_b.to_dict(),
            "swing_a_deg": self.swing_a_deg,
            "swing_b_deg": self.swing_b_deg,
            "spline": self.spline.to_dict(),
            "bezier": [[list(p) for p in s] for s in self.bezier],
            "nurbs": self.nurbs,
            "points": [list(p) for p in self.points],
            "config": self.config,
            "link": self.link,
            "frames": self.frames,
            "stats": self.stats,
        }


# ============================================================================================== ongelma


@dataclass(frozen=True)
class FittingPort:
    """Pää koneen liitännästä (tiivistyspiste ja normaali); liitin luetaan konfiguraatiosta (config["a"/"b"]).

    seal: tiivistyspiste (kulma: liitännän akselilla; banjo: pultin akselin piste silmukan keskitasossa,
          ks. ports.banjo_seal_point)
    normal: liitännän akseli koneesta ulospäin
    swing_deg: kulmaliittimen/banjon kääntökulma liitännän akselin ympäri (ports.swing_dir); None = optimoidaan
    swing_ref: kääntökulman nollasuunta (projisoidaan kohtisuoraan normaalia vastaan)
    """

    seal: Vec3
    normal: Vec3
    swing_deg: float | None = None
    swing_ref: Vec3 | None = None


PortLike = Port | FittingPort | EndFrame | tuple


def _as_port(p: Any) -> Port | FittingPort:
    if isinstance(p, (Port, FittingPort)):
        return p
    if isinstance(p, EndFrame):
        return p.port()
    if isinstance(p, str):
        return Port.parse(p)
    if isinstance(p, Mapping):
        if "seal" in p:
            return FittingPort(vec(p["seal"]), vec(p["normal"]), p.get("swing_deg"), None if p.get("swing_ref") is None else vec(p["swing_ref"]))
        return Port(vec(p["point"]), vec(p["direction"]), None if p.get("up") is None else vec(p["up"]), p.get("insertion"))
    if isinstance(p, (tuple, list)) and len(p) in (2, 3):
        return Port(vec(p[0]), vec(p[1]), None if len(p) < 3 or p[2] is None else vec(p[2]))
    raise TypeError(f"Tuntematon portti: {p!r}")


class _EndModel:
    """Yksi pää: kiinteä Port tai FittingPort (vapaa tai kiinteä kääntökulma)."""

    def __init__(self, spec: Port | FittingPort, key: str, config: Mapping[str, Any] | None):
        self.spec = spec
        self.key = key
        self.config = config
        self.free = False
        if isinstance(spec, FittingPort):
            if config is None:
                raise ValueError("FittingPort vaatii konfiguraation (liitin luetaan config['a'/'b'])")
            self.end = config[key]
            self.free = spec.swing_deg is None and G.is_directional(self.end)

    def frame(self, swing: float) -> tuple[Port, EndFrame | None, float | None]:
        if isinstance(self.spec, Port):
            return self.spec, None, None
        cfg = self.config
        assert cfg is not None
        sw = swing if self.free else float(self.spec.swing_deg or 0.0)
        f = fitting_frame(self.end, cfg["size"], cfg["hose"], self.spec.seal, self.spec.normal, sw, self.spec.swing_ref)
        return f.port(), f, sw

    def initial_swings(self, other_point: Vec3) -> list[float]:
        """Kääntökulman alkuarvaukset: letku kohti toista päätä sekä ±70°."""
        if not self.free:
            return [0.0]
        assert isinstance(self.spec, FittingPort)
        n = unit(self.spec.normal)
        e0 = perpendicular(n, self.spec.swing_ref)
        to = reject(sub(other_point, self.spec.seal), n)
        base = 0.0 if norm(to) < 1e-9 else signed_angle(e0, to, n)
        return [base, base + 70.0, base - 70.0]


@dataclass
class _Shape:
    segs: list[Bezier]
    port_a: Port
    port_b: Port
    frame_a: EndFrame | None
    frame_b: EndFrame | None
    swing_a: float | None
    swing_b: float | None
    s: float
    lam_a: float
    lam_b: float
    vias: list[Vec3]
    q0: Vec3
    q1: Vec3


class _Problem:
    def __init__(self, ea: _EndModel, eb: _EndModel, *, mode: str, target_len: float | None, req_r: float, od: float, margin: float, obstacles: Sequence[Obstacle], avoid: _AvoidIndex | None, lead_a: float, lead_b: float, length_weight: float, n_via: int):
        self.ea, self.eb = ea, eb
        self.mode = mode
        self.target_len = target_len
        self.req_r = req_r
        self.od = od
        self.margin = margin
        self.obstacles = list(obstacles)
        self.avoid = avoid
        self.lead_a, self.lead_b = lead_a, lead_b
        self.length_weight = length_weight
        self.n_via = n_via
        self.n_sw = int(ea.free) + int(eb.free)
        self.s_guess = 1.0
        self.spacing = max(1.0, min(0.5 * (od / 2 + max(margin, 0.0)), 25.0)) if (obstacles or avoid) else 15.0
        self.spacing = min(self.spacing, max(1.0, req_r / 6))
        self.ramp = max(3.0 * od, 30.0)
        self.buffer = max(0.5, 0.05 * od)
        self.evals = 0

    # Parametrit: [swing_a?, swing_b?] + (natural: [qa, qb] | target: [q]) + 3·n_via
    def unpack(self, x: Sequence[float]) -> tuple[float, float, list[float], list[float]]:
        i = 0
        sa = sb = 0.0
        if self.ea.free:
            sa, i = x[i], i + 1
        if self.eb.free:
            sb, i = x[i], i + 1
        nq = 2 if self.mode == "natural" else 1
        q = list(x[i:i + nq])
        off = list(x[i + nq:])
        return sa, sb, q, off

    def ends(self, sa: float, sb: float) -> tuple[Port, Port, EndFrame | None, EndFrame | None, float | None, float | None]:
        pa, fa, swa = self.ea.frame(sa)
        pb, fb, swb = self.eb.frame(sb)
        return pa, pb, fa, fb, swa, swb

    def shape(self, x: Sequence[float], s: float | None = None, tol: float = 1e-2, ends: tuple | None = None) -> _Shape:
        sa, sb, q, off = self.unpack(x)
        pa, pb, fa, fb, swa, swb = ends if ends is not None else self.ends(sa, sb)
        q0 = madd(pa.point, pa.direction, self.lead_a)
        q1 = madd(pb.point, pb.direction, self.lead_b)
        chord = sub(q1, q0)
        D = max(1.0, norm(chord))
        bases = [lerp(q0, q1, (k + 1) / (self.n_via + 1)) for k in range(self.n_via)]

        def build(sv: float) -> tuple[list[Bezier], float, float, list[Vec3]]:
            if self.mode == "natural":
                la, lb = math.exp(q[0]), math.exp(q[1])
                vias = [madd(bases[k], (off[3 * k], off[3 * k + 1], off[3 * k + 2]), D) for k in range(self.n_via)]
            else:
                la, lb = sv * math.exp(q[0]), sv * math.exp(-q[0])
                vias = [madd(bases[k], (off[3 * k], off[3 * k + 1], off[3 * k + 2]), D * sv) for k in range(self.n_via)]
            knots = [q0] + vias + [q1]
            segs = _clamped_spline(knots, mul(pa.direction, la), mul(pb.direction, -lb))
            if self.lead_a > 1e-9:
                segs.insert(0, line_bezier(pa.point, q0))
            if self.lead_b > 1e-9:
                segs.append(line_bezier(q1, pb.point))
            return segs, la, lb, vias

        if self.mode == "natural":
            segs, la, lb, vias = build(1.0)
            sv = 1.0
        else:
            assert self.target_len is not None
            len0 = self.lead_a + norm(chord) + self.lead_b
            sv, _ = _solve_scale(lambda v: curve_length(build(v)[0], n_sub=2 if tol > 1e-3 else 16), self.target_len, s if s is not None else self.s_guess, tol, len0)
            segs, la, lb, vias = build(sv)
        return _Shape(segs, pa, pb, fa, fb, swa, swb, sv, la, lb, vias, q0, q1)

    def measure(self, sh: _Shape, spacing: float | None = None, buffer: float | None = None) -> tuple[_Samples, float, float, float, float, int]:
        """(näytteet, säderikkomus, esterikkomus, letkurikkomus, pienin estevara, sen indeksi).
        buffer: lisävara optimoinnin aikana (harva näytteistys), lopullisessa tarkistuksessa 0."""
        smp = _sample(sh.segs, spacing or self.spacing)
        margin = self.margin + (self.buffer if buffer is None else buffer)
        v_r = max(0.0, self.req_r * smp.kmax - 1.0)
        v_o = 0.0
        v_h = 0.0
        cmin = INF
        cmin_i = -1
        r = self.od / 2
        L = smp.s[-1] if smp.s else 0.0
        if self.obstacles:
            for ob in self.obstacles:
                ca = ob.distance(sh.port_a.point) - r
                cb = ob.distance(sh.port_b.point) - r
                for j, p in enumerate(smp.pts):
                    c = ob.distance(p) - r
                    if c < cmin:
                        cmin, cmin_i = c, j
                    req = margin
                    if c < req:
                        sj = smp.s[j]
                        if ca < margin and sj < self.ramp:
                            req = min(req, ca + (margin - ca) * sj / self.ramp)
                        if cb < margin and L - sj < self.ramp:
                            req = min(req, cb + (margin - cb) * (L - sj) / self.ramp)
                        if c < req:
                            v_o += ((req - c) / self.od) ** 2
        if self.avoid is not None:
            cap = margin + self.od
            ca = self.avoid.clearance(sh.port_a.point, r, cap)
            cb = self.avoid.clearance(sh.port_b.point, r, cap)
            for j, p in enumerate(smp.pts):
                c = self.avoid.clearance(p, r, cap)
                if c < margin:
                    req = margin
                    sj = smp.s[j]
                    if ca < margin and sj < self.ramp:
                        req = min(req, ca + (margin - ca) * sj / self.ramp)
                    if cb < margin and L - sj < self.ramp:
                        req = min(req, cb + (margin - cb) * (L - sj) / self.ramp)
                    if c < req:
                        v_h += ((req - c) / self.od) ** 2
        return smp, v_r, v_o, v_h, cmin, cmin_i

    def objective(self, x: Sequence[float]) -> float:
        self.evals += 1
        try:
            sh = self.shape(x)
        except (ValueError, ZeroDivisionError, OverflowError):
            return 1e12
        if self.mode == "target":
            self.s_guess = sh.s if sh.s > 0 else self.s_guess
        if self.obstacles or self.avoid is not None:
            smp, v_r, v_o, v_h, _, _ = self.measure(sh)
            length, energy, kmax = smp.length, smp.energy, smp.kmax
        else:
            length, energy, kmax = _sample_fast(sh.segs, 24)
            v_r = max(0.0, self.req_r * kmax - 1.0)
            v_o = v_h = 0.0
        _, _, q, off = self.unpack(x)
        reg = 1e-3 * sum(v * v for v in q) + 1e-4 * sum(v * v for v in off)
        if any(abs(v) > 6 for v in q):
            reg += 10 * sum(max(0.0, abs(v) - 6) for v in q)
        viol = v_r + v_o + v_h
        if self.mode == "target":
            assert self.target_len is not None
            dl = abs(length - self.target_len)
            if dl > 0.05:
                viol += dl / max(1.0, self.target_len)
        rr = self.req_r * kmax
        J = self.req_r * energy + rr * rr + reg
        if self.mode == "natural":
            J += self.length_weight * length / self.req_r
        else:
            assert self.target_len is not None
            J += 10.0 * abs(length - self.target_len) / max(1.0, self.target_len)
        if viol > 0:
            return 1e6 * (1.0 + viol) + J * 1e-3
        return J


# ============================================================================================== julkinen API


def route(
    a: PortLike,
    b: PortLike,
    *,
    config: Mapping[str, Any] | None = None,
    od: float | None = None,
    bend_r: float | None = None,
    safety: float = 1.0,
    margin: float = 2.0,
    obstacles: Iterable[Obstacle | Mapping[str, Any] | str] = (),
    avoid: Iterable[Any] = (),
    target_cut: float | None = None,
    target_total: float | None = None,
    target_length: float | None = None,
    insertion_a: float | None = None,
    insertion_b: float | None = None,
    lead: float | tuple[float, float] = 0.0,
    length_weight: float = 0.3,
    n_via: int | str = "auto",
    n_points: int = 101,
    n_through: int = 8,
    max_evals: int = 2500,
    base_url: str | None = None,
) -> RouteResult:
    """Reititä letku porttien a ja b välille (kaikki mitat mm, maailman koordinaatit).

    a, b: Port (holkin takapinnan keskipiste + letkun lähtösuunta), FittingPort (koneen liitäntä, vaatii configin),
          EndFrame, "x,y,z dx,dy,dz" tai (piste, suunta[, up]).
    config: Letkupajan konfiguraatio (hose, size, a, b, ...). Antaa od:n, bendR:n, työntösyvyydet, C-mitat ja linkin.
    od, bend_r: letkun ulkohalkaisija ja pienin taivutussäde, jos configia ei ole (ohittavat configin arvot).
    safety: taivutussäteen kerroin (vaatimus bend_r · safety).
    margin: vähimmäisvara letkun pinnasta esteisiin ja muihin letkuihin (mm).
    obstacles: Sphere/Box/Capsule/Cylinder, dict tai merkkijono (ks. parse_obstacle).
    avoid: muut letkut: RouteResult, {"points", "od"} tai (pisteet, od).
    target_cut | target_total | target_length: tavoitepituus (katkaisumitta | kokonaismitta | keskilinja);
          puuttuu -> luonnollinen vapaa reitti.
    insertion_a/b: letkun pituus holkin sisällä porttiin asti (oletus Port.insertion tai configin holkin rear).
    lead: suora osuus holkin jälkeen (mm), yksi luku tai (A, B).
    length_weight: natural-tilan pituussakko (pienempi -> väljempi kaari).
    n_via: välipisteiden määrä (0-3) tai "auto".
    """
    pa_spec, pb_spec = _as_port(a), _as_port(b)
    hi = G.hose_info(config["hose"], config["size"]) if config is not None else None
    if od is None:
        od = G.protection_od(config) if config is not None else None
    if bend_r is None and hi is not None:
        bend_r = hi.bend_r
    if od is None or bend_r is None:
        raise ValueError("Anna config tai sekä od että bend_r")
    req_r = float(bend_r) * float(safety)
    obs = [o if isinstance(o, (Sphere, Box, Capsule, Cylinder)) else parse_obstacle(o) if isinstance(o, str) else obstacle_from_dict(o) for o in obstacles]
    avoid_items = _avoid_items(avoid)
    avoid_idx = _AvoidIndex.build(avoid_items, reach=float(od) / 2 + max(margin, 0.0) + max([o for _, o in avoid_items], default=0.0) / 2 + 1.0) if avoid_items else None
    lead_a, lead_b = (float(lead), float(lead)) if isinstance(lead, (int, float)) else (float(lead[0]), float(lead[1]))
    warnings: list[str] = []

    ea = _EndModel(pa_spec, "a", config)
    eb = _EndModel(pb_spec, "b", config)

    def insertions(pa: Port, pb: Port) -> tuple[float | None, float | None]:
        ia = insertion_a if insertion_a is not None else pa.insertion if pa.insertion is not None else (hi.ferrule.rear if hi else None)
        ib = insertion_b if insertion_b is not None else pb.insertion if pb.insertion is not None else (hi.ferrule.rear if hi else None)
        return ia, ib

    # Tavoitepituus keskilinjalle (riippuu vain työntösyvyyksistä, ei kääntökulmista)
    mode = "natural"
    target_len: float | None = None
    if sum(v is not None for v in (target_cut, target_total, target_length)) > 1:
        raise ValueError("Anna vain yksi tavoite: target_cut, target_total tai target_length")
    pa0, _, _ = ea.frame(0.0)
    pb0, _, _ = eb.frame(0.0)
    ia0, ib0 = insertions(pa0, pb0)
    if target_length is not None:
        mode, target_len = "target", float(target_length)
    elif target_cut is not None or target_total is not None:
        if ia0 is None or ib0 is None:
            raise ValueError("Katkaisu- tai kokonaismittaan tarvitaan työntösyvyydet (config tai insertion_a/insertion_b)")
        cut = float(target_cut) if target_cut is not None else None
        if target_total is not None:
            if config is None:
                raise ValueError("Kokonaismittaan tarvitaan config (C-mitat)")
            cut = G.cut_from_total(config, float(target_total))
        assert cut is not None
        mode, target_len = "target", cut - ia0 - ib0
    if target_len is not None and target_len <= 0:
        raise ValueError("Tavoitepituus on lyhyempi kuin letkun osuus holkkien sisällä")

    if lead_a < 0 or lead_b < 0:
        raise ValueError("lead ei voi olla negatiivinen")

    def problem(m: int, md: str = mode, rr: float = req_r) -> _Problem:
        return _Problem(ea, eb, mode=md, target_len=target_len, req_r=rr, od=float(od), margin=float(margin), obstacles=obs, avoid=avoid_idx, lead_a=lead_a, lead_b=lead_b, length_weight=length_weight, n_via=m)

    def refined_kmax(prob: _Problem, x: list[float]) -> float:
        """Pienin kaarevuussäde tiheällä näytteistyksellä (kuten lopullinen mittaus): optimoinnin harva näytteistys voi
        hyväksyä mutkan, joka on tarkasti mitattuna liian jyrkkä."""
        try:
            sh = prob.shape(x, tol=1e-7)
        except (ValueError, ZeroDivisionError, OverflowError):
            return INF
        return _refine_kmax(sh.segs, _sample(sh.segs, max(0.5, min(prob.spacing, 4.0))))[0]

    def feasible(fx: float, prob: _Problem, x: list[float]) -> bool:
        if fx >= 1e6:
            return False
        k = refined_kmax(prob, x)
        return k <= 1e-9 or 1.0 / k >= req_r * (1 - 1e-3)

    budget = max(200, int(max_evals))
    total_evals = 0
    refs: list[_Shape] = []
    nat_len = None
    if mode == "target":
        # Luonnollinen reitti muodon lähtökohdaksi ja vertailupituudeksi
        pn = problem(0, "natural")
        xn, fn, ne = _optimize(pn, _initial_points(pn, []), min(budget, 600))
        total_evals += ne
        nat = pn.shape(xn)
        refs.append(nat)
        nat_len = curve_length(nat.segs)

    # Välipisteiden määrät. Luonnollinen reitti ilman välipisteitä jää joskus paikalliseen minimiin, jossa säde ei
    # riitä, vaikka yhdellä välipisteellä löytyy väljä reitti: välipiste kokeillaan vain silloin.
    if n_via == "auto":
        if obs or avoid_idx is not None:
            via_list = [0, 1, 2, 3]
        elif mode == "target":
            via_list = [0, 2, 3]
        else:
            via_list = [0, 1]
    else:
        via_list = [int(n_via)]

    # Paras: ensin kelvollinen (rajoitteet ja tarkasti mitattu säde), sitten pienin kohdefunktio
    best: tuple[float, _Problem, list[float]] | None = None
    best_ok = False
    for m in via_list:
        prob = problem(m)
        x, fx, ne = _optimize(prob, _initial_points(prob, refs), budget)
        total_evals += ne
        ok_m = feasible(fx, prob, x)
        if best is None or (ok_m and not best_ok) or (ok_m == best_ok and fx < best[0] - 1e-9):
            best = (fx, prob, x)
            best_ok = ok_m
        refs.insert(0, prob.shape(x))
        if best_ok:
            if mode == "natural" or m >= 1:
                break
            if mode == "target" and nat_len is not None and target_len is not None and target_len <= 1.1 * nat_len:
                break

    assert best is not None
    if not best_ok and best[0] < 1e6:
        # Harva näytteistys hyväksyi liian jyrkän mutkan: optimoidaan kerran uudelleen säteen vaatimusta
        # korottaen havaitulla suhteella (lähtö parhaasta ratkaisusta).
        fx0, prob0, x0 = best
        k0 = refined_kmax(prob0, x0)
        prob = problem(prob0.n_via, rr=req_r * min(2.0, max(1.0, req_r * k0)) * 1.02)
        x, fx, ne = _optimize(prob, [list(x0)] + _initial_points(prob, refs), budget)
        total_evals += ne
        if feasible(fx, prob, x):
            best = (fx, prob, x)
            best_ok = True
    fx, prob, x = best
    sh = prob.shape(x, tol=1e-7)
    # Lopullinen mittaus tiheällä näytteistyksellä
    fine = max(0.5, min(prob.spacing, 4.0))
    smp, v_r, v_o, v_h, cmin, cmin_i = prob.measure(sh, spacing=fine, buffer=0.0)
    length = curve_length(sh.segs, n_sub=32)
    kmax, kmax_s = _refine_kmax(sh.segs, smp)
    min_radius = INF if kmax <= 1e-9 else 1.0 / kmax

    # Tasavälinen polyline ja läpikulkupisteet
    table = _arc_table(sh.segs)
    pts = [_point_at(sh.segs, table, length * i / max(1, n_points - 1)) for i in range(max(2, n_points))]
    thr = [_point_at(sh.segs, table, length * i / max(1, n_through - 1)) for i in range(max(2, n_through))]

    pa, pb = sh.port_a, sh.port_b
    ia, ib = insertions(pa, pb)
    cut = length + ia + ib if ia is not None and ib is not None else None
    cut_mm = G.round_mm(cut) if cut is not None else None
    total = None
    if cut is not None and config is not None:
        total = G.total_from_cut(config, cut)

    # Kiertokulma: A:n up kuljetetaan kiertymättä B:hen
    orientation = None
    if pa.up is not None and pb.up is not None:
        tans = [unit(_tangent_at(sh.segs, table, length * i / max(1, len(pts) - 1))) for i in range(len(pts))]
        tr = transport_frame(pts, tans, pa.up)
        try:
            orientation = signed_angle(tr[-1], pb.up, tans[-1])
            if orientation > 359.5:
                orientation -= 360.0
        except ValueError:
            orientation = None

    # Varoitukset
    ok = True
    if kmax > 0 and min_radius < req_r * (1 - 1e-3):
        ok = False
        warnings.append(f"Pienin taivutussäde {min_radius:.0f} mm on alle sallitun {req_r:.0f} mm (kohta {kmax_s:.0f} mm A-päästä).")
    if obs and v_o > 0:
        ok = False
        warnings.append(f"Letku on liian lähellä estettä: pienin vara {cmin:.1f} mm, vaadittu {margin:.1f} mm (kohta {smp.s[cmin_i]:.0f} mm A-päästä).")
    hose_clear = None
    if avoid_idx is not None:
        # Raportoitava väli lasketaan tarkasti keskilinjoista (janat janoja vasten): ruudukkohaku (_AvoidIndex) näkee
        # vain hakusäteen (od/2 + margin + …) sisällä olevat letkut, joten kauempana oleva letku antoi äärettömän
        # tai liian suuren välin (CAD-apurin testi 26.10.2026).
        hose_clear = min((min_distance(smp.pts, pts)[0] - float(od) / 2 - float(o) / 2 for pts, o in avoid_items), default=INF)
        if v_h > 0:
            ok = False
            warnings.append(f"Letku on liian lähellä toista letkua: pienin vara {hose_clear:.1f} mm, vaadittu {margin:.1f} mm.")
    if mode == "target" and target_len is not None and abs(length - target_len) > 0.05:
        ok = False
        if length > target_len:
            warnings.append(f"Tavoitepituus on lyhyempi kuin suora etäisyys holkkien välillä ({length - target_len:.0f} mm liian lyhyt).")
        else:
            warnings.append("Tavoitepituutta ei saavutettu.")
    if hi is not None and (hi.estimated or hi.bend_r_estimated):
        warnings.append("Letkun mitat tai taivutussäde ovat osin arvioita; tarkista ennen tilausta.")
    if config is not None:
        for k in ("a", "b"):
            if G.resolve_end(config[k], config["size"]).generic:
                warnings.append(f"{k.upper()}-pään mitat ovat arvioita (liitintä ei löydy mittadatasta).")

    out_cfg = None
    link = None
    if config is not None and cut_mm is not None:
        from .link import build_link, routed_config

        out_cfg = routed_config(config, cut, orientation)
        if cut_mm < 1:
            warnings.append("Katkaisumitta on liian lyhyt.")
            ok = False
        else:
            link = build_link(out_cfg, base_url=base_url) if base_url else build_link(out_cfg)
        rear = hi.ferrule.rear if hi else 0
        if cut is not None and cut < 2 * rear:
            ok = False
            warnings.append(f"Katkaisumitta {cut:.0f} mm on liian lyhyt holkeille (vähintään {2 * rear:.0f} mm).")

    curve = [s for s in sh.segs]
    first_c = next((s for s in curve if dist(s[0], s[1]) > 0 and not _is_line(s)), curve[0])
    last_c = next((s for s in reversed(curve) if not _is_line(s)), curve[-1])
    spline = SplineHint(
        through_points=thr,
        tangent_a=pa.direction,
        tangent_b=mul(pb.direction, -1.0),
        handle_a=dist(first_c[0], first_c[1]),
        handle_b=dist(last_c[2], last_c[3]),
        weight_a=sh.lam_a,
        weight_b=sh.lam_b,
    )
    frames = None
    if sh.frame_a is not None or sh.frame_b is not None:
        frames = {"a": sh.frame_a.to_dict() if sh.frame_a else None, "b": sh.frame_b.to_dict() if sh.frame_b else None}
    return RouteResult(
        ok=ok,
        warnings=warnings,
        mode=mode,
        points=pts,
        length=length,
        min_radius=min_radius,
        min_radius_at=kmax_s,
        required_radius=req_r,
        od=float(od),
        min_clearance=(cmin if obs else None),
        min_clearance_at=(smp.s[cmin_i] if obs and cmin_i >= 0 else None),
        min_clearance_hoses=hose_clear,
        insertion_a=ia,
        insertion_b=ib,
        cut_length=cut,
        cut_length_mm=cut_mm,
        total_length=total,
        orientation_deg=orientation,
        port_a=pa,
        port_b=pb,
        swing_a_deg=sh.swing_a,
        swing_b_deg=sh.swing_b,
        spline=spline,
        bezier=curve,
        nurbs=bezier_to_nurbs(curve),
        config=out_cfg,
        link=link,
        frames=frames,
        stats={"evals": total_evals, "n_via": prob.n_via, "scale": sh.s, "objective": fx},
    )


def _is_line(seg: Bezier) -> bool:
    p0, p1, p2, p3 = seg
    d = sub(p3, p0)
    L = norm(d)
    if L < 1e-12:
        return True
    return norm(cross(sub(p1, p0), d)) < 1e-9 * L * L and norm(cross(sub(p2, p0), d)) < 1e-9 * L * L


def _steps(prob: _Problem) -> list[float]:
    st: list[float] = []
    st += [60.0] * prob.n_sw
    st += [0.5] * (2 if prob.mode == "natural" else 1)
    st += [0.25] * (3 * prob.n_via)
    return st


def _optimize(prob: _Problem, starts: list[list[float]], budget: int) -> tuple[list[float], float, int]:
    """Monikäynnistys: lyhyt Nelder-Mead jokaisesta alkupisteestä, sitten tarkennus parhaasta."""
    total = 0
    per = max(60, int(budget * 0.5) // max(1, len(starts)))
    cand: list[tuple[float, list[float]]] = []
    for x0 in starts:
        x, fx, ne = nelder_mead(prob.objective, x0, _steps(prob), max_evals=per)
        total += ne
        cand.append((fx, x))
    cand.sort(key=lambda c: c[0])
    fx, x = cand[0]
    x, fx, ne = nelder_mead(prob.objective, x, [v * 0.3 for v in _steps(prob)], max_evals=max(150, int(budget * 0.3)))
    total += ne
    x, fx, ne = nelder_mead(prob.objective, x, [v * 0.05 for v in _steps(prob)], max_evals=max(100, int(budget * 0.2)))
    total += ne
    return x, fx, total


def _initial_points(prob: _Problem, refs: Sequence[_Shape]) -> list[list[float]]:
    """Alkupisteet: kääntökulmat kohti toista päätä (±70°), muoto aiemmista ratkaisuista (refs: välipisteet
    näytteistetään niiden käyrältä) ja esteiden ohitus (välipisteet sivuun jänteestä)."""
    pa0, _, _ = prob.ea.frame(0.0)
    pb0, _, _ = prob.eb.frame(0.0)
    sw_a = prob.ea.initial_swings(pb0.point if not isinstance(prob.eb.spec, FittingPort) else prob.eb.spec.seal)
    sw_b = prob.eb.initial_swings(pa0.point if not isinstance(prob.ea.spec, FittingPort) else prob.ea.spec.seal)
    sw_combos: list[list[float]] = []
    for x in sw_a if prob.ea.free else [None]:
        for y in sw_b if prob.eb.free else [None]:
            sw_combos.append([v for v in (x, y) if v is not None])
    m = prob.n_via
    starts: list[list[float]] = []

    def q_of(ref: _Shape | None) -> list[float]:
        if ref is None:
            return [0.0, 0.0] if prob.mode == "natural" else [0.0]
        la, lb = max(1e-6, ref.lam_a), max(1e-6, ref.lam_b)
        if prob.mode == "natural":
            return [math.log(la), math.log(lb)]
        return [0.5 * math.log(la / lb)]

    def offs_of(ref: _Shape) -> list[float]:
        pa, pb = ref.port_a, ref.port_b
        q0p = madd(pa.point, pa.direction, prob.lead_a)
        q1p = madd(pb.point, pb.direction, prob.lead_b)
        D = max(1.0, dist(q0p, q1p))
        sc = 1.0 if prob.mode == "natural" else max(1e-3, ref.s if ref.s > 0 else 1.0)
        table = _arc_table(ref.segs, 16)
        L = table[-1][-1][1]
        o: list[float] = []
        for k in range(m):
            base = lerp(q0p, q1p, (k + 1) / (m + 1))
            d = sub(_point_at(ref.segs, table, L * (k + 1) / (m + 1)), base)
            o += [d[0] / (D * sc), d[1] / (D * sc), d[2] / (D * sc)]
        return o

    def sw_of(ref: _Shape) -> list[float]:
        return [v for v, free in ((ref.swing_a, prob.ea.free), (ref.swing_b, prob.eb.free)) if free and v is not None]

    for ref in refs[:3]:
        starts.append(sw_of(ref) + q_of(ref) + offs_of(ref))
    if m == 0 or not refs:
        for sw in sw_combos[:4]:
            starts.append(list(sw) + q_of(None) + [0.0] * (3 * m))
    if m > 0:
        chord = sub(pb0.point, pa0.point)
        cdir = unit(chord) if norm(chord) > 1e-9 else (1.0, 0.0, 0.0)
        e1 = perpendicular(cdir)
        e2 = cross(cdir, e1)
        sw0 = sw_of(refs[0]) if refs else (sw_combos[0] if sw_combos else [])
        q0 = q_of(refs[0]) if refs else q_of(None)
        for dirv in (e1, mul(e1, -1), e2, mul(e2, -1)):
            starts.append(list(sw0) + list(q0) + [c * 0.35 for _ in range(m) for c in dirv])
    # Poista kaksoiskappaleet
    uniq: list[list[float]] = []
    for s0 in starts:
        if all(max((abs(a - b) for a, b in zip(s0, u)), default=0.0) > 1e-9 for u in uniq):
            uniq.append(s0)
    return uniq[:10]


def _arc_table(segs: Sequence[Bezier], n: int = 48) -> list[list[tuple[float, float]]]:
    """Jokaiselle segmentille [(t, kumulatiivinen kaaren pituus koko käyrällä)]."""
    out: list[list[tuple[float, float]]] = []
    acc = 0.0
    for seg in segs:
        rows = [(0.0, acc)]
        for i in range(1, n + 1):
            t0, t1 = (i - 1) / n, i / n
            acc += bezier_length(seg, t0, t1, n_sub=1)
            rows.append((t1, acc))
        out.append(rows)
    return out


def _locate(segs: Sequence[Bezier], table: list[list[tuple[float, float]]], s: float) -> tuple[int, float]:
    for si, rows in enumerate(table):
        if s <= rows[-1][1] or si == len(table) - 1:
            for i in range(1, len(rows)):
                if s <= rows[i][1] or i == len(rows) - 1:
                    (t0, s0), (t1, s1) = rows[i - 1], rows[i]
                    t = t0 if s1 - s0 < 1e-12 else t0 + (t1 - t0) * max(0.0, min(1.0, (s - s0) / (s1 - s0)))
                    # Newton-askel
                    sp = norm(bezier_d1(segs[si], t))
                    if sp > 1e-9:
                        cur = s0 + bezier_length(segs[si], t0, t, n_sub=1)
                        t = max(t0, min(t1, t + (s - cur) / sp))
                    return si, t
    return len(segs) - 1, 1.0


def _point_at(segs: Sequence[Bezier], table: list[list[tuple[float, float]]], s: float) -> Vec3:
    si, t = _locate(segs, table, s)
    return bezier_point(segs[si], t)


def _tangent_at(segs: Sequence[Bezier], table: list[list[tuple[float, float]]], s: float) -> Vec3:
    si, t = _locate(segs, table, s)
    d = bezier_d1(segs[si], t)
    if norm(d) < 1e-12:
        d = bezier_d1(segs[si], min(1.0, t + 1e-5)) if t < 0.5 else bezier_d1(segs[si], max(0.0, t - 1e-5))
    return d


def _refine_kmax(segs: Sequence[Bezier], smp: _Samples) -> tuple[float, float]:
    """Suurin kaarevuus tarkennettuna kultaisella leikkauksella näytteen ympäriltä. Palauttaa (κmax, s A:sta)."""
    if not smp.kappa or smp.kmax <= 0:
        return 0.0, 0.0
    i = smp.kmax_i
    si = smp.seg_of[i]
    t = smp.t_of[i]
    # naapurinäytteiden t-väli samassa segmentissä
    lo = smp.t_of[i - 1] if i > 0 and smp.seg_of[i - 1] == si else 0.0
    hi = smp.t_of[i + 1] if i + 1 < len(smp.t_of) and smp.seg_of[i + 1] == si else 1.0
    if lo > t:
        lo = 0.0
    if hi < t:
        hi = 1.0
    seg = segs[si]
    gr = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - gr * (b - a), a + gr * (b - a)
    fc, fd = -bezier_curvature(seg, c), -bezier_curvature(seg, d)
    for _ in range(40):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - gr * (b - a)
            fc = -bezier_curvature(seg, c)
        else:
            a, c, fc = c, d, fd
            d = a + gr * (b - a)
            fd = -bezier_curvature(seg, d)
    tb = (a + b) / 2
    kb = bezier_curvature(seg, tb)
    cands = [(kb, tb), (smp.kappa[i], t), (bezier_curvature(seg, lo), lo), (bezier_curvature(seg, hi), hi)]
    k, tt = max(cands, key=lambda c: c[0])
    s = sum(bezier_length(segs[j]) for j in range(si)) + bezier_length(seg, 0.0, tt, n_sub=8)
    return k, s


# ============================================================================================== analyysi


def analyze_points(points: Sequence[Sequence[float]], *, od: float | None = None, bend_r: float | None = None, config: Mapping[str, Any] | None = None, safety: float = 1.0, obstacles: Iterable[Any] = (), margin: float = 2.0, insertion_a: float | None = None, insertion_b: float | None = None) -> dict[str, Any]:
    """Tarkista valmis keskilinja (esim. CAD:n splini näytteistettynä tiheästi, A -> B): pituus, pienin säde
    (kolmen pisteen ympyrä), estevara ja katkaisumitta. Näytteistä vähintään ~2 mm välein luotettavaan säteeseen."""
    pts = [vec(p) for p in points]
    if len(pts) < 2:
        raise ValueError("Vähintään kaksi pistettä")
    if config is not None:
        hi = G.hose_info(config["hose"], config["size"])
        od = od if od is not None else G.protection_od(config)
        bend_r = bend_r if bend_r is not None else hi.bend_r
        insertion_a = insertion_a if insertion_a is not None else hi.ferrule.rear
        insertion_b = insertion_b if insertion_b is not None else hi.ferrule.rear
    s = [0.0]
    for i in range(len(pts) - 1):
        s.append(s[-1] + dist(pts[i], pts[i + 1]))
    rmin, rmin_at = INF, 0.0
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        ab, bc, ca = dist(a, b), dist(b, c), dist(c, a)
        area2 = norm(cross(sub(b, a), sub(c, a)))
        if area2 > 1e-12:
            r = ab * bc * ca / (2 * area2)
            if r < rmin:
                rmin, rmin_at = r, s[i]
    out: dict[str, Any] = {"length": s[-1], "min_radius": None if rmin == INF else rmin, "min_radius_at": rmin_at}
    if bend_r is not None:
        req = bend_r * safety
        out["required_radius"] = req
        out["radius_ok"] = rmin >= req * (1 - 1e-3)
    obs = [o if isinstance(o, (Sphere, Box, Capsule, Cylinder)) else parse_obstacle(o) if isinstance(o, str) else obstacle_from_dict(o) for o in obstacles]
    if obs:
        if od is None:
            raise ValueError("Estetarkasteluun tarvitaan od tai config")
        cmin, cat = INF, 0.0
        for i, p in enumerate(pts):
            for ob in obs:
                c = ob.distance(p) - od / 2
                if c < cmin:
                    cmin, cat = c, s[i]
        out["min_clearance"] = cmin
        out["min_clearance_at"] = cat
        out["clearance_ok"] = cmin >= margin
    if insertion_a is not None and insertion_b is not None:
        cut = s[-1] + insertion_a + insertion_b
        out["cut_length"] = cut
        out["cut_length_mm"] = G.round_mm(cut)
        if config is not None:
            out["total_length"] = G.total_from_cut(config, cut)
    return out


def min_distance(points_a: Sequence[Sequence[float]], points_b: Sequence[Sequence[float]]) -> tuple[float, float, float]:
    """Kahden keskilinjan (polyline) lyhin etäisyys ja kohdat kaaren pituutena kummankin alusta: (d, s_a, s_b).
    Pinta-pinta-etäisyys = d - od_a/2 - od_b/2."""
    A = [vec(p) for p in points_a]
    B = [vec(p) for p in points_b]
    sa = [0.0]
    for i in range(len(A) - 1):
        sa.append(sa[-1] + dist(A[i], A[i + 1]))
    sb = [0.0]
    for i in range(len(B) - 1):
        sb.append(sb[-1] + dist(B[i], B[i + 1]))
    best = (INF, 0.0, 0.0)
    # karkea karsinta: segmenttien rajauslaatikot
    boxes_b = [(tuple(min(B[j][k], B[j + 1][k]) for k in range(3)), tuple(max(B[j][k], B[j + 1][k]) for k in range(3))) for j in range(len(B) - 1)]
    for i in range(len(A) - 1):
        lo_a = tuple(min(A[i][k], A[i + 1][k]) for k in range(3))
        hi_a = tuple(max(A[i][k], A[i + 1][k]) for k in range(3))
        for j in range(len(B) - 1):
            lo_b, hi_b = boxes_b[j]
            gap = math.sqrt(sum(max(0.0, lo_b[k] - hi_a[k], lo_a[k] - hi_b[k]) ** 2 for k in range(3)))
            if gap >= best[0]:
                continue
            d, s, t = _seg_seg(A[i], A[i + 1], B[j], B[j + 1])
            if d < best[0]:
                best = (d, sa[i] + s * (sa[i + 1] - sa[i]), sb[j] + t * (sb[j + 1] - sb[j]))
    return best
