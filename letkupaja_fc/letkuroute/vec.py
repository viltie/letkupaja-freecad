# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Vektorit, kehykset ja yksiköt (puhdas Python, ei riippuvuuksia).

Kaikki pituudet ovat millimetrejä ja kulmat asteita, ellei toisin mainita. Vektori on tavallinen tuple (x, y, z).
CAD-ohjelmien sisäiset yksiköt: Fusion 360 API = cm, SolidWorks API = m (ks. to_mm / from_mm).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

Vec3 = tuple[float, float, float]

EPS = 1e-12

# Yksiköt: montako millimetriä yksi yksikkö on
UNIT_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4}


def to_mm(value: float, unit: str) -> float:
    """Arvo yksiköstä unit (mm, cm, m, in) millimetreiksi. Esim. Fusion API:n cm -> mm: to_mm(v, "cm")."""
    return float(value) * UNIT_MM[unit]


def from_mm(value: float, unit: str) -> float:
    """Millimetrit yksikköön unit (mm, cm, m, in). Esim. SolidWorks API:n metrit: from_mm(v, "m")."""
    return float(value) / UNIT_MM[unit]


def vec(v: Sequence[float] | str) -> Vec3:
    """Vec3 listasta/tuplesta tai merkkijonosta "x,y,z" (myös välilyönnit tai ; erottimena)."""
    if isinstance(v, str):
        parts = [p for p in v.replace(";", ",").replace(" ", ",").split(",") if p.strip()]
        v = [float(p) for p in parts]
    if len(v) != 3:
        raise ValueError(f"Odotettiin 3 koordinaattia, saatiin {len(v)}")
    x, y, z = (float(c) for c in v)
    if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
        raise ValueError("Koordinaatin on oltava äärellinen luku")
    return (x, y, z)


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a: Vec3, k: float) -> Vec3:
    return (a[0] * k, a[1] * k, a[2] * k)


def madd(a: Vec3, b: Vec3, k: float) -> Vec3:
    """a + k·b"""
    return (a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def dist(a: Vec3, b: Vec3) -> float:
    return norm(sub(a, b))


def unit(a: Sequence[float]) -> Vec3:
    a = (float(a[0]), float(a[1]), float(a[2]))
    n = norm(a)
    if n < EPS:
        raise ValueError("Nollavektoria ei voi normeerata (suunta puuttuu)")
    return (a[0] / n, a[1] / n, a[2] / n)


def lerp(a: Vec3, b: Vec3, t: float) -> Vec3:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def reject(v: Vec3, n: Vec3) -> Vec3:
    """v:n komponentti kohtisuoraan yksikkövektoria n vastaan."""
    return madd(v, n, -dot(v, n))


def perpendicular(n: Sequence[float], hint: Sequence[float] | None = None) -> Vec3:
    """Yksikkövektori kohtisuoraan n:ää vastaan. Ensisijaisesti hint projisoituna; jos hint puuttuu tai on n:n
    suuntainen, deterministinen valinta (maailman Z, X tai Y projisoituna, tässä järjestyksessä)."""
    n = unit(n)
    cands: list[Vec3] = [vec(hint)] if hint is not None else []
    cands += [(0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    for cand in cands:
        r = reject(cand, n)
        if norm(r) > 1e-6 * max(EPS, norm(cand)):
            return unit(r)
    raise ValueError("Kohtisuoraa suuntaa ei löytynyt")


def rotate(v: Vec3, axis: Vec3, deg: float) -> Vec3:
    """v kierrettynä akselin axis (yksikkövektori) ympäri oikean käden säännöllä deg astetta (Rodrigues)."""
    k = unit(axis)
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    kv = cross(k, v)
    kd = dot(k, v) * (1 - c)
    return (v[0] * c + kv[0] * s + k[0] * kd, v[1] * c + kv[1] * s + k[1] * kd, v[2] * c + kv[2] * s + k[2] * kd)


def signed_angle(a: Vec3, b: Vec3, axis: Vec3) -> float:
    """Kulma a:sta b:hen akselin axis ympäri (oikean käden sääntö), asteina välillä [0, 360).
    a ja b projisoidaan ensin kohtisuoraan akselia vastaan."""
    k = unit(axis)
    a2, b2 = reject(a, k), reject(b, k)
    if norm(a2) < EPS or norm(b2) < EPS:
        raise ValueError("Kulmaa ei voi laskea akselin suuntaisille vektoreille")
    ang = math.degrees(math.atan2(dot(cross(a2, b2), k), dot(a2, b2)))
    return ang % 360.0


def angle_between(a: Vec3, b: Vec3) -> float:
    """Kulma kahden vektorin välillä asteina [0, 180]."""
    c = dot(a, b) / max(EPS, norm(a) * norm(b))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


@dataclass(frozen=True)
class Frame:
    """Ortonormaali oikeakätinen kehys: origo ja akselit x, y, z maailman koordinaateissa."""

    origin: Vec3
    x: Vec3
    y: Vec3
    z: Vec3

    @staticmethod
    def from_xy(origin: Sequence[float], x: Sequence[float], y_hint: Sequence[float]) -> "Frame":
        """Kehys, jonka x-akseli on x ja y-akseli y_hint ortogonalisoituna; z = x × y."""
        xo = unit(vec(x))
        yo = perpendicular(xo, y_hint)
        return Frame(vec(origin), xo, yo, cross(xo, yo))

    def to_world(self, p: Sequence[float]) -> Vec3:
        """Paikallinen piste -> maailma."""
        return add(self.origin, self.dir_to_world(p))

    def dir_to_world(self, d: Sequence[float]) -> Vec3:
        return (
            self.x[0] * d[0] + self.y[0] * d[1] + self.z[0] * d[2],
            self.x[1] * d[0] + self.y[1] * d[1] + self.z[1] * d[2],
            self.x[2] * d[0] + self.y[2] * d[1] + self.z[2] * d[2],
        )

    def to_local(self, p: Sequence[float]) -> Vec3:
        """Maailman piste -> paikallinen."""
        return self.dir_to_local(sub(vec(p), self.origin))

    def dir_to_local(self, d: Sequence[float]) -> Vec3:
        d = vec(d)
        return (dot(d, self.x), dot(d, self.y), dot(d, self.z))


def polyline_length(points: Iterable[Vec3]) -> float:
    pts = list(points)
    return sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def transport_frame(points: Sequence[Vec3], tangents: Sequence[Vec3], r0: Vec3) -> list[Vec3]:
    """Kiertymätön (rotation minimizing) siirto: vektori r0 (kohtisuoraan ensimmäistä tangenttia vastaan) kuljetetaan
    käyrää pitkin ilman kiertymää (double reflection, Wang ym. 2008). Palauttaa vektorin jokaisessa pisteessä.
    Tällä lasketaan letkun päiden välinen kiertokulma (letku asennetaan ilman kierrettä)."""
    r = unit(reject(r0, unit(tangents[0])))
    out = [r]
    for i in range(len(points) - 1):
        v1 = sub(points[i + 1], points[i])
        c1 = dot(v1, v1)
        if c1 < EPS:
            out.append(r)
            continue
        rl = madd(r, v1, -2.0 / c1 * dot(v1, r))
        tl = madd(tangents[i], v1, -2.0 / c1 * dot(v1, tangents[i]))
        v2 = sub(tangents[i + 1], tl)
        c2 = dot(v2, v2)
        r = rl if c2 < EPS else madd(rl, v2, -2.0 / c2 * dot(v2, rl))
        r = unit(reject(r, unit(tangents[i + 1])))
        out.append(r)
    return out
