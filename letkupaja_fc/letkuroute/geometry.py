# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Letkupajan mittadata (data/geometry.json) ja pituuslaskenta.

Haku- ja varamittalogiikka on siirretty Letkupajan tiedostoista cad/hosecad/geo.py ja src/lib/hose3d/data.ts
(sizeGeo, hoseGeo, resolveEnd: perhe -> kierre -> kulma -> koko -> "*"); pituudet kuten scene.ts lengths() ja
assembly.ts cutLength(). Pidä synkronissa, kun Letkupajan mittalogiikka muuttuu (tools/sync_geometry.py päivittää datan).

Mittakäsitteet (mm, letkun akselin suunnassa):
* C (c_dim): tiivistyspisteestä letkun pohjaan O. O = katkaistun letkun pää liittimen sisällä (kauluksen takapinta).
  Suora: tiivistyspinnasta. Kulma: tiivistyspisteen projektiosta letkun akselille (90°: mutterin tai laipan akseli).
  Banjo: pultin akselilta.
* H (h_dim): kulmaliittimen tiivistyspisteen etäisyys letkun akselista (suora ja banjo 0).
* katkaisumitta (cut) = letkun pituus = kokonaismitta - C_A - C_B.
* holkin takapää (rear) = len - lip letkun pohjasta O. Letkun vapaa osa alkaa tästä.
* työntösyvyys (insertion_depth) = letkun pohjasta O valittuun holkin vertailukohtaan (ref):
    ref="rear": holkin takapinta (len - lip). letkuroute-reitityksen Port.point on aina tämä.
    ref="edge": holkin täyden ulkohalkaisijan takareuna = takapään uran alku (rear - 1,7 mm). Tämän antaa
                SolidWorksin Measure, kun valitaan holkkien ulkopinnat (mittaus SolidWorksissa 2SC DN12: 930,0 - 886,4).
  Katkaisumitta = keskilinjan pituus vertailukohdasta toiseen + insertion_A + insertion_B (samalla ref:llä).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DEFAULT_GEOMETRY_PATH = os.path.join(DATA_DIR, "geometry.json")

# Holkin takapään uran alku takapinnasta (hosecad/model.py ferrule_solid: (xr - 1.7, R) ... (xr, R - 0.45))
FERRULE_EDGE_FROM_REAR = 1.7

INSERTION_REFS = ("rear", "edge")

_GEO: dict[str, Any] | None = None
_GEO_PATH: str | None = None


def geometry_path() -> str:
    """Käytössä olevan geometry.json-tiedoston polku (ympäristömuuttuja LETKUROUTE_GEOMETRY ohittaa paketin oman)."""
    return _GEO_PATH or os.environ.get("LETKUROUTE_GEOMETRY") or DEFAULT_GEOMETRY_PATH


def geo() -> dict[str, Any]:
    """Mittadata dict-muodossa (ladataan kerran)."""
    global _GEO
    if _GEO is None:
        with open(geometry_path(), encoding="utf-8") as f:
            _GEO = json.load(f)
    return _GEO


def set_geometry(data: Mapping[str, Any] | str | None) -> None:
    """Vaihda mittadata: dict, polku tai None (= takaisin oletukseen). Testejä ja CAD-lisäosien omaa dataa varten."""
    global _GEO, _GEO_PATH
    if data is None:
        _GEO, _GEO_PATH = None, None
    elif isinstance(data, str):
        _GEO, _GEO_PATH = None, data
    else:
        _GEO, _GEO_PATH = dict(data), None


def geometry_version() -> str:
    """geometry.json:n generated-päiväys (esim. "2026-10-25")."""
    return str(geo().get("generated", ""))


SIZE_ID = {"dn6": 6.4, "dn8": 7.9, "dn10": 9.5, "dn12": 12.7, "dn16": 15.9, "dn19": 19, "dn25": 25.4, "dn31": 31.8, "dn38": 38.1, "dn51": 50.8}


def num(v: Any, d: float) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else d


def size_geo(size: str) -> dict[str, float]:
    """Koon runkomitat (id, refOd, stem, collar, lk, lkb, bore, lip); tuntematon koko -> arvio (kuten sizeGeo)."""
    s = geo()["sizes"].get(size)
    if s:
        return s
    i = SIZE_ID.get(size, 12.7)
    od = i * 1.55 + 1
    return {"id": i, "refOd": od, "stem": i + 3, "collar": od + 1, "lk": 0.15 * (od + 1) + 2, "lkb": 0.55 * (od + 1) + 2, "bore": 0.75 * i, "lip": 0.25 * i + 2.5}


def hose_geo(hose_type: str, size: str) -> dict[str, Any]:
    """Letkun mitat kuten verkkosivun hoseGeo (puuttuva koko -> arvio, bendR lähimmän tyypin ensimmäisestä koosta)."""
    t = geo()["hoses"].get(hose_type)
    h = t.get(size) if t else None
    if h:
        return h
    s = size_geo(size)
    near = next(iter(t.values())) if t else None
    od = s["refOd"]
    return {
        "od": od,
        "id": s["id"],
        "color": (near or {}).get("color", "#1b1c1e"),
        "finish": (near or {}).get("finish", "rubber"),
        "bendR": (near or {}).get("bendR", od * 4),
        "ferrule": {"od": od * 1.04 + 6.5, "len": od * 0.9 + 15, "lip": (od * 0.9 + 15) * 0.16, "lipOd": (od * 1.04 + 6.5) * 0.95, "knurl": 0},
    }


@dataclass(frozen=True)
class FerruleInfo:
    """Puristusholkki letkun pohjasta O mitattuna (x' kohti letkua): etupää -lip, takapinta rear = len - lip."""

    od: float
    len: float
    lip: float
    lip_od: float
    knurl: bool
    rear: float  # takapinta letkun pohjasta O (= len - lip)
    edge: float  # täyden ulkohalkaisijan takareuna (= rear - 1.7)


@dataclass(frozen=True)
class HoseInfo:
    hose: str
    size: str
    od: float  # letkun ulkohalkaisija
    id: float  # sisähalkaisija
    bend_r: float  # pienin taivutussäde (keskilinjan säde)
    bend_r_estimated: bool  # True, jos taivutussäde on arvio (koko puuttuu datasta)
    estimated: bool  # True, jos mitat ovat arviota (q != "m" tai koko puuttuu)
    known: bool  # False, jos letkutyyppi/koko puuttuu datasta (kaikki mitat arvioita)
    color: str
    finish: str
    ferrule: FerruleInfo


def _bend_r_estimate(hose_type: str, od: float) -> float:
    """Taivutussäde koolle, jota ei ole datassa: lineaarinen interpolointi saman tyypin kokojen OD:n mukaan
    (ulkopuolella lähimmän koon suhde bendR/OD); tuntematon tyyppi -> 5 × OD (varovainen)."""
    t = geo()["hoses"].get(hose_type) or {}
    pts = sorted((float(v["od"]), float(v["bendR"])) for v in t.values() if isinstance(v.get("bendR"), (int, float)))
    if not pts:
        return 5.0 * od
    if od <= pts[0][0]:
        return pts[0][1] / pts[0][0] * od
    if od >= pts[-1][0]:
        return pts[-1][1] / pts[-1][0] * od
    for (o0, r0), (o1, r1) in zip(pts, pts[1:]):
        if o0 <= od <= o1:
            return r0 + (r1 - r0) * (od - o0) / max(1e-9, o1 - o0)
    return 5.0 * od


def hose_info(hose: str, size: str) -> HoseInfo:
    """Letkun ja holkin mitat reititystä varten. Puuttuvalle koolle mitat arvioidaan (estimated/known)."""
    t = geo()["hoses"].get(hose) or {}
    raw = t.get(size)
    h = hose_geo(hose, size)
    fr = h["ferrule"]
    rear = float(fr["len"]) - float(fr["lip"])
    od = float(h["od"])
    if raw and isinstance(raw.get("bendR"), (int, float)):
        bend_r, bend_est = float(raw["bendR"]), False
    else:
        bend_r, bend_est = _bend_r_estimate(hose, od), True
    est = (raw is None) or raw.get("q", "m") != "m" or fr.get("q", "m") != "m"
    return HoseInfo(
        hose=hose,
        size=size,
        od=od,
        id=float(h["id"]),
        bend_r=bend_r,
        bend_r_estimated=bend_est,
        estimated=bool(est),
        known=raw is not None,
        color=str(h.get("color", "#1b1c1e")),
        finish=str(h.get("finish", "rubber")),
        ferrule=FerruleInfo(
            od=float(fr["od"]),
            len=float(fr["len"]),
            lip=float(fr["lip"]),
            lip_od=float(fr["lipOd"]),
            knurl=bool(fr.get("knurl")),
            rear=rear,
            edge=rear - FERRULE_EDGE_FROM_REAR,
        ),
    )


def prot_geo(hose_type: str, size: str, hose_od: float, ferrule_od: float) -> dict[str, Any]:
    """Suojien mitat (spiral, sleeve, fire, alu) kuten protGeo; puuttuva -> arvio letkun OD:sta."""
    p = geo()["protections"].get(hose_type, {}).get(size, {})
    od = hose_od
    t = max(1.2, od * 0.08)
    return {
        "spiral": p.get("spiral") or {"od": od, "id": od - 2, "t": t, "pitch": od, "band": od * 0.88, "vis": [od, od + 2 * t], "color": "#202124"},
        "sleeve": p.get("sleeve") or {"id": od + 2, "t": 1, "vis": [od + 1.5, od + 3.5], "color": "#1e1f22"},
        "fire": p.get("fire") or {"id": od + 1, "od": od + 6, "vis": [od + 1, od + 6], "color": "#b4432c"},
        "alu": p.get("alu") or {"id": ferrule_od + 2, "od": ferrule_od + 5, "len": 25, "t": 1.5, "vis": ferrule_od + 5},
    }


def protection_od(config: Mapping[str, Any]) -> float:
    """Suojatun letkun ulkohalkaisija (näkyvä) törmäystarkasteluun; ilman suojaa letkun OD."""
    hi = hose_info(config["hose"], config["size"])
    prot = config.get("protection") or "none"
    if prot == "none":
        return hi.od
    pg = prot_geo(config["hose"], config["size"], hi.od, hi.ferrule.od)
    vis = pg.get(prot, {}).get("vis")
    if isinstance(vis, (list, tuple)) and len(vis) == 2:
        return max(hi.od, float(vis[1]))
    return hi.od


@dataclass
class ResolvedEnd:
    """Liitinpään mitat: geo = kierteen data (head, hb, eye, bolt ...), row = ends[kulma][koko] (c, h, r, stem ...)."""

    family: str
    kind: str
    angle: int
    geo: dict[str, Any]
    row: dict[str, Any]
    generic: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def c(self) -> float:
        return float(self.row["c"])

    @property
    def h(self) -> float:
        return float(self.row["h"])

    @property
    def banjo(self) -> bool:
        return self.kind == "banjo" or bool(self.geo.get("eye"))


def generic_thread(thread_od: float, size: str) -> dict[str, Any]:
    s = size_geo(size)
    d = thread_od if thread_od > 0 else s["refOd"] * 0.95
    af = round(d * 1.2 + 2)
    minor = d * 0.92
    return {
        "thread": {"d": d, "p": 1.5, "type": "metric", "minor": minor},
        "seal": "generic",
        "af": af,
        "front": 0.3 * d,
        "neck": min(s["stem"], minor * 0.8),
        "hb": 0.55 * af,
        "head": [
            {"t": "thr", "u0": -0.3 * d, "u1": 0, "d": d, "p": 1.5, "ext": 0, "minor": minor},
            {"t": "hex", "u0": -0.3 * d, "u1": 0.45 * af, "af": af, "bore": minor},
            {"t": "cone", "u0": 0, "u1": 0.25 * d, "d0": 0.55 * d, "d1": 0.75 * d, "bore": 0.4 * d},
            {"t": "cyl", "u0": 0.45 * af, "u1": 0.55 * af, "d": min(s["stem"], minor * 0.8)},
        ],
        "ends": {},
    }


def resolve_end(end: Mapping[str, Any], size: str, thread_od: float = 0) -> ResolvedEnd:
    """Pään mitat: perhe -> kierre -> kulma -> koko (puuttuva koko -> "*"); puuttuva data -> arvio (generic=True).
    end = {"family", "thread", "angle"} kuten Letkupajan konfiguraatiossa. Ei koskaan poikkeusta."""
    fam = geo()["fittings"].get(end.get("family", ""))
    a = end.get("angle")
    angle = 45 if a == 45 else 90 if a == 90 else 0
    g = fam["threads"].get(end.get("thread", "")) if fam else None
    generic = False
    if not g:
        g = generic_thread(thread_od, size)
        generic = True
    rows = g["ends"].get(str(angle))
    row = (rows.get(size) or rows.get("*")) if rows else None
    s = size_geo(size)
    if not row:
        generic = True
        e0 = g["ends"].get("0") or {}
        straight = e0.get(size) or e0.get("*")
        c0 = num((straight or {}).get("c"), s["lip"] + s["lk"] + g["hb"] + 6)
        if angle == 0:
            row = {"c": c0}
        else:
            stem = s["stem"]
            h = max(g["hb"] + stem * 1.6, c0 * 0.9)
            c = c0 * 0.6 + stem if angle == 90 else c0 * 0.6 + h / math.tan(math.radians(45))
            row = {"c": c, "h": h, "r": stem * 1.6, "stem": stem, "lk": s["lk"]}
    row = dict(row)
    row["c"] = num(row.get("c"), 25)
    row["h"] = 0.0 if angle == 0 else num(row.get("h"), 30)
    return ResolvedEnd(family=end.get("family", ""), kind=(fam or {}).get("kind", "female"), angle=angle, geo=g, row=row, generic=generic)


def is_directional(end: Mapping[str, Any]) -> bool:
    """Suunnattava pää (kulmaliitin tai banjo): kiertokulma merkitsee, jos molemmat päät ovat suunnattavia."""
    return end.get("angle", 0) != 0 or end.get("family") == "banjo"


def needs_orientation(config: Mapping[str, Any]) -> bool:
    return is_directional(config["a"]) and is_directional(config["b"])


def c_dim(end: Mapping[str, Any], size: str, thread_od: float = 0) -> float:
    """Pään C-mitta (mm): tiivistyspisteestä (kulma: sen projektiosta, banjo: pultin akselilta) letkun pohjaan."""
    return resolve_end(end, size, thread_od).c


def h_dim(end: Mapping[str, Any], size: str, thread_od: float = 0) -> float:
    """Kulmaliittimen H-mitta (mm): tiivistyspisteen etäisyys letkun akselista; suora ja banjo 0."""
    return resolve_end(end, size, thread_od).h


@dataclass(frozen=True)
class Lengths:
    cut: float  # katkaisumitta (letkun pituus)
    total: float  # kokonaismitta (tiivistyspisteestä tiivistyspisteeseen letkun suunnassa)
    c_a: float
    c_b: float


def lengths(config: Mapping[str, Any]) -> Lengths:
    """(katkaisumitta, kokonaismitta) konfiguraation lengthMm/lengthMode mukaan kuten verkkosivulla."""
    c_a = c_dim(config["a"], config["size"])
    c_b = c_dim(config["b"], config["size"])
    L = max(0.0, num(config.get("lengthMm"), 0))
    if config.get("lengthMode") == "hose":
        return Lengths(L, L + c_a + c_b, c_a, c_b)
    return Lengths(L - c_a - c_b, L, c_a, c_b)


def cut_from_total(config: Mapping[str, Any], total_mm: float | None = None) -> float:
    """Katkaisumitta kokonaismitasta: total - C_A - C_B. total_mm puuttuu -> konfiguraation kokonaismitta."""
    ln = lengths(config)
    total = ln.total if total_mm is None else float(total_mm)
    return total - ln.c_a - ln.c_b


def total_from_cut(config: Mapping[str, Any], cut_mm: float | None = None) -> float:
    """Kokonaismitta katkaisumitasta: cut + C_A + C_B. cut_mm puuttuu -> konfiguraation katkaisumitta."""
    ln = lengths(config)
    cut = ln.cut if cut_mm is None else float(cut_mm)
    return cut + ln.c_a + ln.c_b


def insertion_depth(hose: str, size: str, ref: str = "rear") -> float:
    """Letkun pituus holkin sisällä: letkun pohjasta O holkin vertailukohtaan (mm). ref="rear" (takapinta,
    oletus, letkuroute-porttien kohta) tai "edge" (täyden halkaisijan takareuna, rear - 1.7)."""
    fr = hose_info(hose, size).ferrule
    if ref == "rear":
        return fr.rear
    if ref == "edge":
        return fr.edge
    raise ValueError(f"Tuntematon vertailukohta {ref!r} (sallitut: {', '.join(INSERTION_REFS)})")


def insertion_total(config: Mapping[str, Any], ref: str = "rear") -> float:
    """Molempien päiden työntösyvyys yhteensä: katkaisumitta = keskilinja vertailukohdasta toiseen + tämä."""
    return 2.0 * insertion_depth(config["hose"], config["size"], ref)


def cut_from_centerline(config: Mapping[str, Any] | None, centerline_mm: float, ref: str = "rear", *, insertion_a: float | None = None, insertion_b: float | None = None) -> float:
    """Katkaisumitta CAD:ssä mitatusta keskilinjasta, joka kulkee holkkien vertailukohtien (ref) keskipisteiden välillä."""
    if insertion_a is None or insertion_b is None:
        if config is None:
            raise ValueError("Anna config tai insertion_a ja insertion_b")
        d = insertion_depth(config["hose"], config["size"], ref)
        insertion_a = d if insertion_a is None else insertion_a
        insertion_b = d if insertion_b is None else insertion_b
    return float(centerline_mm) + insertion_a + insertion_b


def straight_ref_distance(config: Mapping[str, Any], ref: str = "rear") -> float:
    """Suoran mallin (STEP) holkkien vertailukohtien välinen etäisyys: katkaisumitta - työntösyvyydet."""
    return lengths(config).cut - insertion_total(config, ref)


def round_mm(x: float) -> int:
    """Pyöristys kokonaisiksi millimetreiksi kuten JavaScriptin Math.round (puolikkaat ylöspäin)."""
    return int(math.floor(x + 0.5))
