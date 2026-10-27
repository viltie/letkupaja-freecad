# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Liittimen yksinkertaistettu ulkomuoto ("envelope") reititystä ja törmäystarkastelua varten.

Ilmainen malli: oikeat ulkomitat (kuusiot, mutteri, kaulus, kulmarunko, holkki, banjon silmukka) geometry.json-
tiedostosta, mutta ei sisäkierteitä, kanavia, O-renkaita eikä muita yksityiskohtia. Tarkka STEP-malli on erillinen
maksullinen tuote (ks. api.py).

Logiikka on siirretty Letkupajan STEP-generaattorista (hosecad/model.py: build_end, head_solids, envelope,
ferrule_solid, bolt_parts) niin, että ulkomitat ja sijainnit ovat samat kuin tarkassa mallissa.

Tämä moduuli on puhdasta Pythonia: se tuottaa muotojen kuvaukset (Revolve, Hex, Cyl, Bend) liittimen paikallisessa
kehyksessä. fcgeom.py tekee niistä FreeCAD-kappaleet. Kehys (sama kuin letkuroute ja hosecad):
origo O = letkun pohja, x' = letkun suunta holkista poispäin, y' = kulman suunta (banjo: pultin akseli), z' = x' × y'.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence, Union

from .core import L

Vec3 = tuple[float, float, float]
DEG = math.pi / 180


@dataclass
class Revolve:
    """Pyörähdyskappale: suljettu profiili (u, r) akselin ympäri. Akseli kulkee pisteen origin kautta suuntaan axis;
    ref on kohtisuora suunta, jonka tasoon profiili piirretään."""

    origin: Vec3
    axis: Vec3
    ref: Vec3
    profile: list[tuple[float, float]]


@dataclass
class Hex:
    """Kuusiokanta akselilla u0..u1 (avainväli af). Lappeen normaali osoittaa ref-suuntaan. chamfer: 30° viisteet."""

    origin: Vec3
    axis: Vec3
    ref: Vec3
    u0: float
    u1: float
    af: float
    chamfer: bool = True


@dataclass
class Cyl:
    p0: Vec3
    direction: Vec3
    length: float
    radius: float


@dataclass
class Bend:
    """Taivutettu putki: ympyrä (säde radius) pisteessä start (taso kohtisuoraan tangenttia vastaan) kierretään akselin
    (center, axis) ympäri angle astetta oikean käden säännöllä."""

    start: Vec3
    tangent: Vec3
    center: Vec3
    axis: Vec3
    angle: float
    radius: float


Shape = Union[Revolve, Hex, Cyl, Bend]


@dataclass
class EndEnvelope:
    """Pään muodot. Liitin = (head − head_cuts) ∪ nose ∪ body. Naarasliittimessä kiertyvä mutteri jää omaksi
    kappaleekseen nipan ympärille (kuten todellisuudessa), muuten liitin on yksi umpikappale."""

    head: list[Shape]  # pää: mutteri/kuusio, kierre, laippa tai banjon silmukka
    head_cuts: list[Shape]  # mutterin ontelo (vastakappaleen urospää mahtuu sisään)
    nose: list[Shape]  # nokka mutterin sisällä
    body: list[Shape]  # kaulus, kaula, kulmarunko
    ferrule: Revolve  # puristusholkki (ontto, letku kulkee sisällä)
    extras: list[Shape] = field(default_factory=list)  # banjopultti ja tiivisteet
    info: dict[str, Any] = field(default_factory=dict)

    @property
    def fitting(self) -> list[Shape]:
        return self.head + self.nose + self.body


# ---------------------------------------------------------------------------------------------- apufunktiot


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a: Vec3, k: float) -> Vec3:
    return (a[0] * k, a[1] * k, a[2] * k)


def prim_radii(p: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    """Pyörähdysprimitiivin (u0, u1, r0, r1); sisäkierre, kuusio, O-rengas ym. -> None (hosecad.prim_radii)."""
    t = p.get("t")
    if t == "cyl":
        return (p["u0"], p["u1"], p["d"] / 2, p["d"] / 2)
    if t == "cone":
        return (p["u0"], p["u1"], p["d0"] / 2, p["d1"] / 2)
    if t == "thr" and p.get("ext"):
        d1 = p.get("d1", p["d"]) if p.get("taper") else p["d"]
        return (p["u0"], p["u1"], p["d"] / 2, d1 / 2)
    return None


def envelope(prims: Sequence[tuple[float, float, float, float]]) -> list[tuple[float, float]]:
    """Pyörähdysosien yhteinen ulkoprofiili (u, r); aukot r = 0 (hosecad.envelope)."""
    prims = [p for p in prims if p[1] - p[0] > 1e-4]
    if not prims:
        return []
    xs = sorted({p[0] for p in prims} | {p[1] for p in prims})

    def r_at(p, u):
        return p[2] + (p[3] - p[2]) * (u - p[0]) / (p[1] - p[0])

    out: list[tuple[float, float]] = []
    for a, b in zip(xs, xs[1:]):
        mid = (a + b) / 2
        act = [p for p in prims if p[0] <= mid <= p[1]]
        if not act:
            out += [(a, 0.0), (b, 0.0)]
            continue
        cuts = {a, b}
        for i in range(len(act)):
            for j in range(i + 1, len(act)):
                p, q = act[i], act[j]
                fa = r_at(p, a) - r_at(q, a)
                fb = r_at(p, b) - r_at(q, b)
                if fa * fb < 0:
                    cuts.add(a + (b - a) * fa / (fa - fb))
        for u in sorted(cuts):
            out.append((u, max(r_at(p, min(max(u, p[0]), p[1])) for p in act)))
    clean: list[tuple[float, float]] = []
    for u, r in out:
        if clean and abs(clean[-1][0] - u) < 1e-6 and abs(clean[-1][1] - r) < 1e-6:
            continue
        clean.append((u, r))
    return clean


def envelope_parts(env: Sequence[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Ulkoprofiili suljetuiksi pyörähdysprofiileiksi (r = 0 -kohdat jakavat osiin; hosecad.solid_from_envelope)."""
    parts: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    for u, r in env:
        if r <= 1e-6:
            if cur:
                parts.append(cur)
            cur = []
        else:
            cur.append((u, r))
    if cur:
        parts.append(cur)
    out = []
    for p in parts:
        if p[-1][0] - p[0][0] < 1e-3:
            continue
        out.append(_clean_profile([(p[0][0], 0.0)] + p + [(p[-1][0], 0.0)]))
    return [p for p in out if len(p) >= 3]


def _clean_profile(profile: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for x, r in profile:
        r = max(0.0, r)
        if pts and abs(pts[-1][0] - x) < 1e-6 and abs(pts[-1][1] - r) < 1e-6:
            continue
        pts.append((x, r))
    if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) < 1e-6 and abs(pts[0][1] - pts[-1][1]) < 1e-6:
        pts.pop()
    return pts


def ferrule_profile(fr: Mapping[str, Any], hose_id: float, hose_od: float) -> list[tuple[float, float]]:
    """Puristusholkki x' = −lip … len − lip (hosecad.ferrule_solid): etuhuuli, 30° viiste, runko, ura takapäässä.
    Sisäpinta: letkun ulkopinta letkun pohjasta taaksepäin ja nipan pinta huulen alla."""
    xf = -fr["lip"]
    xr = fr["len"] - fr["lip"]
    R = fr["od"] / 2
    rl = fr["lipOd"] / 2
    taper = max(0.6, (R - rl) / math.tan(30 * DEG))
    r_in_front = hose_id / 2
    r_in = hose_od / 2
    xb = min(0.0, xr - 6)
    return _clean_profile(
        [
            (xf, r_in_front),
            (xf, rl - 0.4),
            (xf + 0.4, rl),
            (xb, rl),
            (xb + taper, R),
            (xr - 1.7, R),
            (xr - 1.45, R - 0.45),
            (xr - 1.05, R - 0.45),
            (xr - 0.8, R),
            (xr - 0.45, R),
            (xr, R - 0.45),
            (xr, r_in),
            (0.0, r_in),
            (0.0, r_in_front),
        ]
    )


# ---------------------------------------------------------------------------------------------- pää


def end_envelope(end: Mapping[str, Any], size: str, hose: str, thread_od: float = 0.0) -> EndEnvelope:
    """Yhden pään yksinkertaistettu malli liittimen paikallisessa kehyksessä (O, x', y', z')."""
    res = L.resolve_end(end, size, thread_od)
    g = res.geo
    row = res.row
    sg = L.geometry.size_geo(size)
    hg = L.geometry.hose_geo(hose, size)
    fr = hg["ferrule"]
    a_deg = res.angle
    a = a_deg * DEG
    C = float(row["c"])
    H = float(row["h"]) if a_deg else 0.0
    xf = -float(fr["lip"])
    xr = float(fr["len"]) - float(fr["lip"])
    head_prims = list(g.get("head") or [])
    eye = next((p for p in head_prims if p.get("t") == "eye"), None)
    banjo = res.banjo and eye is not None
    u_neck = eye["bore"] / 2 + 0.5 if banjo else float(g.get("hb") or 0.0)
    neck_d = max(4.0, min(float(g.get("neck") or sg["stem"]), sg["collar"] - 1))

    # Pään kehys: origo P, u-akseli = liitännän normaali n, lape kohti y_head
    P: Vec3 = (-C, H, 0.0)
    n: Vec3 = (math.cos(a), -math.sin(a), 0.0)
    y_head: Vec3 = (math.sin(a), math.cos(a), 0.0)

    head: list[Shape] = []
    head_cuts: list[Shape] = []
    nose: list[Shape] = []
    body: list[Shape] = []
    extras: list[Shape] = []
    eye_w = 0.0
    if banjo:
        eye_w = max(float(eye["w"]), float(row.get("eyeW") or 0.0))
        # Silmukka: kiekko pultin akselilla (y'), keskipiste P
        head.append(Cyl(_add(P, (0.0, -eye_w / 2, 0.0)), (0.0, 1.0, 0.0), eye_w, eye["d"] / 2))
    else:
        int_thr = next((p for p in head_prims if p.get("t") == "thr" and not p.get("ext")), None)
        cav_d = float(int_thr["minor"]) if int_thr else 0.0

        def max_d(p: Mapping[str, Any]) -> float:
            rr = prim_radii(p)
            return 2 * max(rr[2], rr[3]) if rr else 0.0

        nose_p = [p for p in head_prims if int_thr and p.get("t") in ("cone", "cyl") and p["u0"] >= -0.01 and max_d(p) < cav_d - 0.2]
        body_p = [p for p in head_prims if p not in nose_p]
        for prof in envelope_parts(envelope([rr for p in body_p if (rr := prim_radii(p))])):
            head.append(Revolve(P, n, y_head, prof))
        af_over = row.get("af") if row.get("af") and g.get("af") and row.get("af") != g.get("af") else None
        hexes = [p for p in head_prims if p.get("t") == "hex" and p["u1"] - p["u0"] >= 0.1]
        for p in hexes:
            head.append(Hex(P, n, y_head, float(p["u0"]), float(p["u1"]), float(af_over or p["af"])))
        if int_thr is not None:
            u_front = min([float(int_thr["u0"])] + [float(p["u0"]) for p in hexes]) - 1.0
            cav_end = max([float(int_thr["u1"])] + [float(p["u1"]) for p in nose_p])
            head_cuts.append(Cyl(_add(P, _mul(n, u_front)), n, cav_end - u_front, cav_d / 2))
            nose_r = sorted((rr for p in nose_p if (rr := prim_radii(p))), key=lambda rr: rr[0])
            if nose_r:
                # Nippa yhtenäiseksi: nokan osien väliset raot täytetään ja viimeistä osaa jatketaan ontelon pohjan
                # yli kaulaan asti (muuten nokka jäisi irralliseksi kappaleeksi kiertyvän mutterin sisään)
                bridges = []
                for prev, nxt in zip(nose_r, nose_r[1:]):
                    if nxt[0] > prev[1]:
                        r = min(prev[3], nxt[2])
                        bridges.append((prev[1] - 1e-3, nxt[0] + 1e-3, r, r))
                last = max(nose_r, key=lambda rr: rr[1])
                nose_r += bridges + [(last[1] - 1e-3, cav_end + 0.3, last[3], last[3])]
            for prof in envelope_parts(envelope(nose_r)):
                nose.append(Revolve(P, n, y_head, prof))

    if not res.angle:
        avail = xf - (-C + u_neck)
        lk_c = min(sg["lk"], max(0.8, avail))
        body.append(Cyl((xf - lk_c, 0.0, 0.0), (1.0, 0.0, 0.0), lk_c, sg["collar"] / 2))
        x0 = -C + u_neck - (0.0 if banjo else 0.6)
        x1 = xf - lk_c + 0.2
        if x1 - x0 > 0.2:
            body.append(Cyl((x0, 0.0, 0.0), (1.0, 0.0, 0.0), x1 - x0, neck_d / 2))
    else:
        stem = max(3.0, min(float(row.get("stem") or sg["stem"]), neck_d + 2, sg["collar"] - 1))
        qx = C - H / math.tan(a)
        leg = H / math.sin(a)
        r = float(row.get("r") or stem * 1.6)
        t0 = math.tan(a / 2)
        max_t = max(0.5, min(leg - u_neck, qx + xf - 1))
        if r * t0 > max_t:
            r = max_t / t0
        t = r * t0
        ox, oy = -math.cos(a), math.sin(a)
        Qx = -qx
        b0x = Qx + t
        lk_c = min(sg["lk"], max(0.8, xf - b0x))
        body.append(Cyl((xf - lk_c, 0.0, 0.0), (1.0, 0.0, 0.0), lk_c, sg["collar"] / 2))
        x_start = xf - lk_c + 0.5
        if b0x < x_start - 0.05:
            body.append(Cyl((b0x, 0.0, 0.0), (1.0, 0.0, 0.0), x_start - b0x, stem / 2))
        # Mutka: alkaa letkun akselilta kohdasta b0x ja kääntyy kohti liitännän akselia
        body.append(Bend((b0x, 0.0, 0.0), (-1.0, 0.0, 0.0), (b0x, r, 0.0), (0.0, 0.0, -1.0), a_deg, stem / 2))
        s_end = leg - u_neck + 0.6
        if s_end > t + 0.05:
            p0 = (Qx + ox * t, oy * t, 0.0)
            body.append(Cyl(p0, (ox, oy, 0.0), s_end - t, stem / 2))

    if banjo and g.get("bolt") and end.get("bolt") in ("single", "double", "seals"):
        extras += _bolt_shapes(g["bolt"], eye_w, str(end.get("bolt")), end.get("seal"), P)

    ferrule = Revolve((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), ferrule_profile(fr, float(hg["id"]), float(hg["od"])))
    info = {
        "c": C,
        "h": H,
        "angle": a_deg,
        "banjo": banjo,
        "generic": res.generic,
        "ferrule_rear": xr,
        "ferrule_front": xf,
        "ferrule_od": float(fr["od"]),
        "seal": P,
        "normal": n,
    }
    return EndEnvelope(head=head, head_cuts=head_cuts, nose=nose, body=body, ferrule=ferrule, extras=extras, info=info)


def _bolt_shapes(bolt: Mapping[str, Any], eye_w: float, mode: str, seal_kind: Any, P: Vec3) -> list[Shape]:
    """Banjopultti ja tiivisteet pultin akselilla y' (kanta +y'), hosecad.bolt_parts yksinkertaistettuna."""
    usit = (bolt.get("seals") or {}).get("usit") or bolt["washer"]
    copper = (bolt.get("seals") or {}).get("copper") if seal_kind == "copper" else None
    seal = copper or usit
    t = float(seal["t"])
    w = eye_w
    double = mode == "double"
    head = max(2.0, float((bolt.get("head2") or bolt["head"]) if double else bolt["head"]))
    stack = 2 * w + 3 * t if double else w + 2 * t
    total = float((bolt.get("total2") or bolt["total"] + w + t) if double else bolt["total"])
    shank = max(total - head, stack + 0.4 * bolt["d"])
    top = w / 2 + t
    tip = top - shank
    y: Vec3 = (0.0, 1.0, 0.0)
    out: list[Shape] = []
    if mode in ("single", "double"):
        out.append(Cyl(_add(P, _mul(y, tip)), y, shank, bolt["d"] / 2))
        out.append(Hex(P, y, (1.0, 0.0, 0.0), top, top + head, float(bolt["af"])))
    at = [w / 2, -w / 2 - t, -w / 2 - t - w - t] if double else [w / 2, -w / 2 - t]
    for a0 in at:
        out.append(Cyl(_add(P, _mul(y, a0)), y, t, seal["od"] / 2))
    return out


def shape_extent(shapes: Sequence[Shape], axis: Vec3 = (1.0, 0.0, 0.0)) -> tuple[float, float]:
    """Muotojen likimääräinen ulottuma akselin suunnassa (testejä varten): (min, max)."""
    vals: list[float] = []

    def d(p: Vec3) -> float:
        return p[0] * axis[0] + p[1] * axis[1] + p[2] * axis[2]

    for s in shapes:
        if isinstance(s, Revolve):
            for u, _r in s.profile:
                vals.append(d(_add(s.origin, _mul(s.axis, u))))
        elif isinstance(s, Hex):
            vals += [d(_add(s.origin, _mul(s.axis, s.u0))), d(_add(s.origin, _mul(s.axis, s.u1)))]
        elif isinstance(s, Cyl):
            vals += [d(s.p0), d(_add(s.p0, _mul(s.direction, s.length)))]
        elif isinstance(s, Bend):
            vals.append(d(s.start))
    return (min(vals), max(vals)) if vals else (0.0, 0.0)
