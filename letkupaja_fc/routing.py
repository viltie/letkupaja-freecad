# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Reitityksen syötteet ja tulokset ilman FreeCADia: päiden määrittely (EndSpec), reititys ytimellä ja liitinten
sijainnit (paikallinen kehys -> maailma).

Pään tila (EndSpec.mode):
* "port"    koneen liitäntä: valittu piste on tiivistyspiste (banjo: koneen tiivistyspinta pultin akselilla) ja
            suunta on liitännän akseli koneesta ulospäin. Liitin sijoitetaan liitäntään, kulmaliittimen ja banjon
            kääntökulma optimoidaan (tai annetaan).
* "ferrule" holkin takapää: valittu piste on holkin takapinnan keskipiste ja suunta on letkun lähtösuunta
            (esim. Letkupaja-STEP-mallin "Holkki A"). Liitin on jo mallissa.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .core import L

Vec3 = tuple[float, float, float]


@dataclass
class EndSpec:
    mode: str  # "port" | "ferrule"
    point: Vec3
    direction: Vec3
    up: Vec3 | None = None  # ferrule-tila: kulman suunta (kulmaliitin/banjo)
    swing: float | None = None  # port-tila: kääntökulma (None = automaattinen)
    offset: float = 0.0  # siirto suunnan mukaan (mm)
    flip: bool = False  # käännä suunta
    radius: float = 0.0  # valitun ympyrän säde (tieto käyttäjälle)
    draw_fitting: bool = True  # piirretäänkö yksinkertaistettu liitin
    source: dict[str, Any] | None = None  # {"object": nimi, "sub": alielementti, "doc": dokumentti}

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["point"] = list(self.point)
        d["direction"] = list(self.direction)
        d["up"] = None if self.up is None else list(self.up)
        return d

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "EndSpec":
        return EndSpec(
            mode=str(d.get("mode", "port")),
            point=L.vec.vec(d["point"]),
            direction=L.vec.vec(d["direction"]),
            up=None if d.get("up") is None else L.vec.vec(d["up"]),
            swing=None if d.get("swing") is None else float(d["swing"]),
            offset=float(d.get("offset") or 0.0),
            flip=bool(d.get("flip")),
            radius=float(d.get("radius") or 0.0),
            draw_fitting=bool(d.get("draw_fitting", True)),
            source=dict(d["source"]) if isinstance(d.get("source"), Mapping) else None,
        )

    @property
    def axis(self) -> Vec3:
        """Käytettävä suunta (flip huomioitu)."""
        d = L.vec.unit(self.direction)
        return L.vec.mul(d, -1.0) if self.flip else d

    @property
    def effective_point(self) -> Vec3:
        return L.vec.madd(L.vec.vec(self.point), self.axis, self.offset)


def to_core_port(spec: EndSpec, key: str, config: Mapping[str, Any]):
    """EndSpec -> letkuroute.FittingPort (koneen liitäntä) tai letkuroute.Port (holkin takapää)."""
    end = config[key]
    if spec.mode == "ferrule":
        up = spec.up if (spec.up is not None and L.is_directional(end)) else None
        return L.Port(spec.effective_point, spec.axis, up)
    seal = spec.effective_point
    res = L.resolve_end(end, config["size"])
    if res.banjo:
        try:
            seal = L.banjo_seal_point(end, config["size"], seal, spec.axis)
        except ValueError:
            pass
    swing = spec.swing if L.is_directional(end) else 0.0
    return L.FittingPort(seal, spec.axis, swing, None)


EXTRA_KEYS = ("sleeveCrimp", "caps", "rstFerrules", "tag")


def panel_config(base: Mapping[str, Any] | None, hose: str, size: str, protection: str, a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    """Paneelin valinnat Letkupaja-linkin (tai tallennetun asennelman) päälle: paneeli muokkaa letkua, kokoa, suojaa
    ja päiden perhettä, kierrettä, kulmaa, pulttia ja tiivistettä; muut linkin valinnat (sukan puristus, suojatulpat,
    RST-holkit, merkintä, päiden RST-liitin ja kiertokulma, jos sitä ei voi laskea) säilyvät. Päiden RST-valinta
    säilyy vain, jos pään liitin ja kierre ovat samat kuin linkissä."""
    base = dict(base or {})
    ends = []
    for key, new in (("a", a), ("b", b)):
        e = dict(new)
        old = base.get(key) if isinstance(base.get(key), Mapping) else {}
        if "rst" in old and old.get("family") == e.get("family") and old.get("thread") == e.get("thread"):
            e["rst"] = old["rst"]
        ends.append(e)
    extra = {k: base[k] for k in EXTRA_KEYS if k in base}
    orientation = base.get("orientation", 0)
    if not isinstance(orientation, int) or isinstance(orientation, bool):
        orientation = 0
    return L.make_config(hose, size, ends[0], ends[1], 1000, protection=protection, orientation=orientation, **extra)


def config_extras(cfg: Mapping[str, Any]) -> list[str]:
    """Paneelissa näkymättömät valinnat, jotka säilyvät tilauksessa: avaimet (i18n "extra.<avain>")."""
    out = [k for k in EXTRA_KEYS if cfg.get(k) not in (None, False, "")]
    for key in ("a", "b"):
        e = cfg.get(key)
        if isinstance(e, Mapping) and e.get("rst"):
            out.append(f"rst_{key}")
    return out


def obstacles_from_boxes(boxes: Iterable[Sequence[float]]) -> list[Any]:
    """Rajauslaatikot (xmin, ymin, zmin, xmax, ymax, zmax) -> letkuroute.Box-esteet."""
    out = []
    for b in boxes:
        x0, y0, z0, x1, y1, z1 = (float(v) for v in b)
        if x1 - x0 <= 0 or y1 - y0 <= 0 or z1 - z0 <= 0:
            continue
        out.append(L.Box((x0, y0, z0), (x1, y1, z1)))
    return out


@dataclass
class RouteRequest:
    config: dict[str, Any]
    a: EndSpec
    b: EndSpec
    obstacles: list[Any] = field(default_factory=list)  # letkuroute-esteet
    avoid: list[Any] = field(default_factory=list)  # [(pisteet, od)]
    safety: float = 1.0
    margin: float = 2.0
    target_cut: float | None = None


def run_route(req: RouteRequest):
    """Reititä (kestää 0,05–10 s puhtaalla Pythonilla). Palauttaa letkuroute.RouteResult."""
    cfg = L.normalize_config(req.config)
    err = L.validate_config({**cfg, "lengthMm": max(1, int(cfg.get("lengthMm") or 1000))})
    if err:
        raise ValueError("Virheellinen asennelma: " + "; ".join(err))
    pa = to_core_port(req.a, "a", cfg)
    pb = to_core_port(req.b, "b", cfg)
    kw: dict[str, Any] = {"config": {**cfg, "lengthMm": max(1, int(cfg.get("lengthMm") or 1000))}, "safety": float(req.safety), "margin": float(req.margin), "obstacles": list(req.obstacles), "avoid": list(req.avoid)}
    if req.target_cut:
        kw["target_cut"] = float(req.target_cut)
    return L.route(pa, pb, **kw)


@dataclass
class PlacedEnd:
    """Pään sijainti maailmassa: liittimen paikallinen kehys (O, x', y') ja holkin takapinta."""

    key: str
    origin: Vec3
    x: Vec3
    y: Vec3
    rear: Vec3
    draw_fitting: bool
    swing: float | None = None

    @property
    def z(self) -> Vec3:
        return L.vec.cross(self.x, self.y)

    def to_world(self, p: Sequence[float]) -> Vec3:
        return (
            self.origin[0] + self.x[0] * p[0] + self.y[0] * p[1] + self.z[0] * p[2],
            self.origin[1] + self.x[1] * p[0] + self.y[1] * p[1] + self.z[1] * p[2],
            self.origin[2] + self.x[2] * p[0] + self.y[2] * p[1] + self.z[2] * p[2],
        )

    def dir_to_world(self, d: Sequence[float]) -> Vec3:
        return L.vec.sub(self.to_world(d), self.origin)

    def matrix(self) -> list[list[float]]:
        """4×4-muunnosmatriisi (rivit) paikallisesta kehyksestä maailmaan."""
        x, y, z, o = self.x, self.y, self.z, self.origin
        return [[x[0], y[0], z[0], o[0]], [x[1], y[1], z[1], o[1]], [x[2], y[2], z[2], o[2]], [0.0, 0.0, 0.0, 1.0]]


def placed_ends(result: Any, req: RouteRequest) -> tuple[PlacedEnd, PlacedEnd]:
    """Liitinten kehykset reititetystä tuloksesta. FittingPort-päille ytimen EndFrame; holkkipäille kehys portista
    (O = takapinta − työntösyvyys · suunta)."""
    out = []
    for key, spec, port, ins, sw in (("a", req.a, result.port_a, result.insertion_a, result.swing_a_deg), ("b", req.b, result.port_b, result.insertion_b, result.swing_b_deg)):
        fr = (result.frames or {}).get(key) if result.frames else None
        if fr:
            o, x, y = tuple(fr["origin"]), tuple(fr["direction"]), tuple(fr["up"])
            rear = tuple(fr["rear"])
        else:
            x = port.direction
            ins = float(ins if ins is not None else 0.0)
            o = L.vec.madd(port.point, x, -ins)
            y = port.up if port.up is not None else L.vec.perpendicular(x, None)
            rear = port.point
        out.append(PlacedEnd(key, L.vec.vec(o), L.vec.unit(x), L.vec.perpendicular(x, y), L.vec.vec(rear), spec.draw_fitting, sw))
    return out[0], out[1]


def hose_path(result: Any, ends: tuple[PlacedEnd, PlacedEnd]) -> tuple[Vec3, list[Vec3], Vec3]:
    """Letkun keskilinja letkun pohjasta toiseen: (O_A, reitin pisteet takapinnasta takapintaan, O_B).
    Koko viivan pituus = katkaisumitta (letku alkaa liittimen sisältä letkun pohjasta)."""
    return ends[0].origin, list(result.points), ends[1].origin


def summary(result: Any) -> dict[str, Any]:
    """Tuloksen tärkeimmät luvut (JSON-yhteensopiva)."""
    def fx(v: Any) -> Any:
        return None if isinstance(v, float) and not math.isfinite(v) else v

    return {
        "ok": bool(result.ok),
        "mode": result.mode,
        "cut_length": result.cut_length,
        "cut_length_mm": result.cut_length_mm,
        "total_length": result.total_length,
        "length": result.length,
        "min_radius": fx(result.min_radius),
        "required_radius": result.required_radius,
        "min_clearance": result.min_clearance,
        "min_clearance_hoses": result.min_clearance_hoses,
        "orientation_deg": result.orientation_deg,
        "swing_a_deg": result.swing_a_deg,
        "swing_b_deg": result.swing_b_deg,
        "warnings": list(result.warnings),
        "link": result.link,
        "config": result.config,
        "od": result.od,
    }
