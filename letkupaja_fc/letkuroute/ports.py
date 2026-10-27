# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Liitäntäkehykset: reitityksen portti (holkin takapää) ja liittimen kinematiikka tiivistyspisteestä holkkiin.

Liittimen paikallinen kehys (sama kuin hosecad/model.py ja verkkosivun 3D-katselin):
* origo O = letkun pohja (katkaistun letkun pää liittimen sisällä)
* x' = letkun suunta holkista poispäin (kohti letkun vapaata osaa)
* y' = kulmaliittimen kulman suunta (tiivistyspiste on +y'-puolella), banjossa pultin suunta (kanta +y')
* tiivistyspiste P = (-C, H, 0); banjossa pultin akselin piste silmukan keskitasossa (-C, 0, 0)
* liitännän normaali n (koneen portista ulospäin, liittimen u-akseli): kulma a -> (cos a, -sin a, 0); banjo (0, 1, 0)
* holkin takapinnan keskipiste R = (rear, 0, 0), rear = ferrule.len - ferrule.lip
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from . import geometry as G
from .vec import Vec3, add, cross, madd, mul, perpendicular, unit, vec


@dataclass(frozen=True)
class Port:
    """Reitityksen pää: holkin takapinnan keskipiste ja letkun lähtösuunta.

    point: holkin takapinnan keskipiste (mm, maailman koordinaatit)
    direction: yksikkövektori, johon letku lähtee holkista (A:ssa kohti letkua, B:ssä myös holkista ulos kohti letkua)
    up: liittimen kulman suunta y' (kulmaliitin, banjo: pultin suunta); tarvitaan kiertokulman laskemiseen
    insertion: letkun pituus holkin sisällä letkun pohjasta tähän pisteeseen (mm); None = lasketaan konfiguraatiosta
    """

    point: Vec3
    direction: Vec3
    up: Vec3 | None = None
    insertion: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "point", vec(self.point))
        object.__setattr__(self, "direction", unit(vec(self.direction)))
        if self.up is not None:
            object.__setattr__(self, "up", perpendicular(self.direction, vec(self.up)))

    def shifted(self, d: float) -> "Port":
        """Portti siirrettynä d mm letkun suuntaan (työntösyvyys kasvaa d:llä). Esim. holkin uran reunalta
        (ref="edge") takapintaan: port.shifted(1.7)."""
        return replace(self, point=madd(self.point, self.direction, d), insertion=None if self.insertion is None else self.insertion + d)

    @staticmethod
    def parse(text: str, up: str | Sequence[float] | None = None, insertion: float | None = None) -> "Port":
        """Merkkijonosta "x,y,z dx,dy,dz" (erottimena välilyönti, / tai |)."""
        parts = [p for p in text.replace("|", " ").replace("/", " ").split() if p.strip()]
        if len(parts) != 2:
            raise ValueError(f"Portti muodossa 'x,y,z dx,dy,dz', saatiin {text!r}")
        return Port(vec(parts[0]), vec(parts[1]), None if up is None else vec(up), insertion)

    def to_dict(self) -> dict[str, Any]:
        return {"point": list(self.point), "direction": list(self.direction), "up": None if self.up is None else list(self.up), "insertion": self.insertion}


@dataclass(frozen=True)
class EndFrame:
    """Liitinpään sijainti maailmassa (mm). port() antaa reitityksen portin (holkin takapinta)."""

    seal: Vec3  # tiivistyspiste P (banjo: pultin akseli silmukan keskitasossa)
    normal: Vec3  # liitännän akseli koneen portista ulospäin (liittimen u-akseli)
    origin: Vec3  # letkun pohja O
    rear: Vec3  # holkin takapinnan keskipiste
    edge: Vec3  # holkin täyden halkaisijan takareunan keskipiste (rear - 1.7 letkun suunnassa)
    direction: Vec3  # letkun suunta x'
    up: Vec3  # kulman suunta y'
    c: float
    h: float
    angle: int
    banjo: bool
    directional: bool
    insertion: float  # letkun pohjasta takapintaan (= ferrule.rear)
    swing_deg: float | None = None

    def port(self, ref: str = "rear") -> Port:
        """Reitityksen portti: ref="rear" (takapinta, oletus) tai "edge"."""
        p = self.rear if ref == "rear" else self.edge if ref == "edge" else None
        if p is None:
            raise ValueError(f"Tuntematon vertailukohta {ref!r}")
        ins = self.insertion if ref == "rear" else self.insertion - G.FERRULE_EDGE_FROM_REAR
        return Port(p, self.direction, self.up if self.directional else None, ins)

    def to_dict(self) -> dict[str, Any]:
        d = {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}
        return d


def local_seal_and_normal(res: G.ResolvedEnd) -> tuple[Vec3, Vec3]:
    """Tiivistyspiste ja liitännän normaali liittimen paikallisessa kehyksessä (O, x', y')."""
    if res.banjo:
        return (-res.c, 0.0, 0.0), (0.0, 1.0, 0.0)
    a = math.radians(res.angle)
    return (-res.c, res.h, 0.0), (math.cos(a), -math.sin(a), 0.0)


def end_frame(end: Mapping[str, Any], size: str, hose: str, origin: Sequence[float], direction: Sequence[float], up: Sequence[float], thread_od: float = 0) -> EndFrame:
    """EndFrame letkun pohjasta O, letkun suunnasta x' ja kulman suunnasta y' (maailman koordinaatit)."""
    res = G.resolve_end(end, size, thread_od)
    hi = G.hose_info(hose, size)
    o = vec(origin)
    x = unit(vec(direction))
    y = perpendicular(x, vec(up))
    z = cross(x, y)
    pl, nl = local_seal_and_normal(res)

    def w(p: Vec3) -> Vec3:
        return (x[0] * p[0] + y[0] * p[1] + z[0] * p[2], x[1] * p[0] + y[1] * p[1] + z[1] * p[2], x[2] * p[0] + y[2] * p[1] + z[2] * p[2])

    return EndFrame(
        seal=add(o, w(pl)),
        normal=w(nl),
        origin=o,
        rear=madd(o, x, hi.ferrule.rear),
        edge=madd(o, x, hi.ferrule.edge),
        direction=x,
        up=y,
        c=res.c,
        h=res.h,
        angle=res.angle,
        banjo=res.banjo,
        directional=G.is_directional(end),
        insertion=hi.ferrule.rear,
    )


def swing_dir(normal: Sequence[float], swing_deg: float, swing_ref: Sequence[float] | None = None) -> Vec3:
    """Kääntösuunta w liitännän akselin n ympäri: w(0) = swing_ref projisoituna (oletus: maailman Z, X tai Y),
    kasvava kulma oikean käden säännöllä n:n ympäri."""
    n = unit(vec(normal))
    e0 = perpendicular(n, swing_ref)
    e1 = cross(n, e0)
    a = math.radians(swing_deg)
    return add(mul(e0, math.cos(a)), mul(e1, math.sin(a)))


def fitting_frame(end: Mapping[str, Any], size: str, hose: str, seal: Sequence[float], normal: Sequence[float], swing_deg: float = 0.0, swing_ref: Sequence[float] | None = None, thread_od: float = 0) -> EndFrame:
    """Liitinpää koneen liitännästä: tiivistyspiste seal, liitännän normaali (koneesta ulospäin) ja kääntökulma.

    Kulmaliitin ja banjo kääntyvät liitännän akselin ympäri: letkun suunnan projektio liitännän tasossa on
    w = swing_dir(normal, swing_deg, swing_ref). Suora liitin: letku lähtee normaalin suuntaan (kääntö vain y').
    Banjo: seal = pultin akselin piste silmukan keskitasossa (ks. banjo_seal_point)."""
    res = G.resolve_end(end, size, thread_od)
    n = unit(vec(normal))
    w = swing_dir(n, swing_deg, swing_ref)
    if res.banjo:
        x, y = w, n
    else:
        a = math.radians(res.angle)
        x = add(mul(n, math.cos(a)), mul(w, math.sin(a)))
        y = add(mul(n, -math.sin(a)), mul(w, math.cos(a)))
    p = vec(seal)
    # P = O - C x' + H y'  ->  O = P + C x' - H y'
    o = madd(madd(p, x, res.c), y, -res.h)
    f = end_frame(end, size, hose, o, x, y, thread_od)
    return replace(f, swing_deg=float(swing_deg))


def banjo_seal_point(end: Mapping[str, Any], size: str, face_point: Sequence[float], normal: Sequence[float]) -> Vec3:
    """Banjon tiivistyspiste (silmukan keskitaso pultin akselilla) koneen tiivistyspinnan pisteestä:
    face_point + (silmukan leveys / 2 + tiivisteen paksuus) · normal (hosecad bolt_parts: tiiviste -w/2 - t ... -w/2)."""
    res = G.resolve_end(end, size)
    eye = next((p for p in res.geo.get("head", []) if p.get("t") == "eye"), None)
    if not res.banjo or not eye:
        raise ValueError("Pää ei ole banjo")
    w = max(float(eye["w"]), float(res.row.get("eyeW") or 0))
    bolt = res.geo.get("bolt") or {}
    seal = ((bolt.get("seals") or {}).get("usit")) or bolt.get("washer") or {"t": 0}
    t = float(seal.get("t") or 0)
    if end.get("seal") == "copper":
        cu = (bolt.get("seals") or {}).get("copper")
        if cu:
            t = float(cu.get("t") or t)
    return madd(vec(face_point), unit(vec(normal)), w / 2 + t)


def norm_orientation(config: Mapping[str, Any]) -> float:
    """Kiertokulma 0..360 (0, jos kumpikaan pää ei ole suunnattava), kuten hosecad.norm_orientation."""
    if not G.needs_orientation(config):
        return 0.0
    try:
        n = float(config.get("orientation") or 0)
    except (TypeError, ValueError):
        n = 0.0
    return ((n % 360) + 360) % 360


def assembly_frames(config: Mapping[str, Any]) -> tuple[EndFrame, EndFrame]:
    """Suoran asennelman päät samassa kehyksessä kuin Letkupajan STEP-malli (hosecad):
    letkun akseli = +X A:sta B:hen, P_A:n projektio origossa, A:n kulma +Y, B:n kulma (0, cos N, sin N)."""
    ln = G.lengths(config)
    n = math.radians(norm_orientation(config))
    fa = end_frame(config["a"], config["size"], config["hose"], (ln.c_a, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    fb = end_frame(config["b"], config["size"], config["hose"], (ln.c_a + ln.cut, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, math.cos(n), math.sin(n)))
    return fa, fb
