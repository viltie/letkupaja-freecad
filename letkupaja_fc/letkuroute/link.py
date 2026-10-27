# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Letkupajan Letkukone-linkit: https://letkupaja.fi/?asennelma=<base64url(JSON) ilman täytettä>#rakenna

JSON on kuten Letkupajan src/lib/schemas.ts assemblySchema ja src/components/Configurator.tsx DEFAULT_CONFIG
(avainten järjestys: hose, size, a, b, lengthMm, lengthMode, orientation, protection, sitten valinnaiset).
Sivu lukee linkin parseSharedConfig-funktiolla (atob + assemblySchema.safeParse) ja tarkistaa valinnat uudelleen
hinnoittelussa; enintään 2000 merkkiä. JSON koodataan ASCII-muodossa (ä -> \\u00e4), koska atob palauttaa tavuja.
"""
from __future__ import annotations

import base64
import json
import math
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from .geometry import is_directional, round_mm

BASE_URL = "https://letkupaja.fi/"
ANCHOR = "rakenna"
MAX_TOKEN = 2000

HOSES = ("1sc", "2sc", "gates2sc", "pp250", "pp400", "tp_r7", "tp_r8", "tp_r8e", "tp_mt1", "ptfe")
SIZES = ("dn6", "dn8", "dn10", "dn12", "dn16", "dn19", "dn25", "dn31", "dn38", "dn51")
PROTECTIONS = ("none", "spiral", "sleeve", "fire")
LENGTH_MODES = ("total", "hose")

_TOP_ORDER = ("hose", "size", "a", "b", "lengthMm", "lengthMode", "orientation", "protection", "orientationConfirmed", "sleeveCrimp", "caps", "rstFerrules", "tag")
_END_ORDER = ("family", "thread", "angle", "bolt", "seal", "rst")


def _end(e: Mapping[str, Any]) -> dict[str, Any]:
    out = {k: e[k] for k in _END_ORDER if k in e and e[k] is not None}
    out["angle"] = int(out.get("angle", 0))
    return out


def normalize_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Konfiguraatio verkkosivun avainjärjestyksessä; tuntemattomat avaimet pudotetaan, puuttuvat oletuksilla
    (lengthMode "total", orientation 0, protection "none"). orientation on 0, jos kumpikaan pää ei ole suunnattava
    (kuten Configurator)."""
    c = dict(config)
    c.setdefault("lengthMode", "total")
    c.setdefault("orientation", 0)
    c.setdefault("protection", "none")
    c["a"] = _end(c["a"])
    c["b"] = _end(c["b"])
    if not (is_directional(c["a"]) and is_directional(c["b"])):
        c["orientation"] = 0
    out: dict[str, Any] = {}
    for k in _TOP_ORDER:
        if k in c and c[k] is not None:
            out[k] = c[k]
    return out


def validate_config(config: Mapping[str, Any]) -> list[str]:
    """assemblySchema-tarkistus (rakenne ja arvoalueet). Palauttaa virheet; tyhjä = kelpaa.
    Saatavuutta (onko liitintä tähän kokoon) ei tarkisteta: sen tekee verkkosivun hinnoittelu."""
    err: list[str] = []
    c = config
    if c.get("hose") not in HOSES:
        err.append(f"hose: tuntematon letkutyyppi {c.get('hose')!r}")
    if c.get("size") not in SIZES:
        err.append(f"size: tuntematon koko {c.get('size')!r}")
    for k in ("a", "b"):
        e = c.get(k)
        if not isinstance(e, Mapping):
            err.append(f"{k}: puuttuu")
            continue
        for f in ("family", "thread"):
            v = e.get(f)
            if not isinstance(v, str) or len(v) > 20:
                err.append(f"{k}.{f}: merkkijono (enintään 20 merkkiä)")
        if e.get("angle") not in (0, 45, 90) or isinstance(e.get("angle"), bool):
            err.append(f"{k}.angle: 0, 45 tai 90")
        if "bolt" in e and e["bolt"] not in ("single", "double", "seals"):
            err.append(f"{k}.bolt: single, double tai seals")
        if "seal" in e and e["seal"] not in ("usit", "copper"):
            err.append(f"{k}.seal: usit tai copper")
        if "rst" in e and not isinstance(e["rst"], bool):
            err.append(f"{k}.rst: true/false")
    L = c.get("lengthMm")
    if not isinstance(L, int) or isinstance(L, bool) or not (1 <= L <= 100000):
        err.append("lengthMm: kokonaisluku 1...100000")
    if c.get("lengthMode", "total") not in LENGTH_MODES:
        err.append("lengthMode: total tai hose")
    o = c.get("orientation", 0)
    if not isinstance(o, int) or isinstance(o, bool) or not (0 <= o <= 359):
        err.append("orientation: kokonaisluku 0...359")
    if c.get("protection", "none") not in PROTECTIONS:
        err.append("protection: none, spiral, sleeve tai fire")
    for b in ("orientationConfirmed", "sleeveCrimp", "caps", "rstFerrules"):
        if b in c and not isinstance(c[b], bool):
            err.append(f"{b}: true/false")
    if "tag" in c and (not isinstance(c["tag"], str) or len(c["tag"].strip()) > 60):
        err.append("tag: enintään 60 merkkiä")
    return err


def encode_config(config: Mapping[str, Any]) -> str:
    """base64url(JSON) ilman '='-täytettä, JSON tiiviinä (kuten JSON.stringify) ja ASCII-muodossa."""
    js = json.dumps(normalize_config(config), separators=(",", ":"), ensure_ascii=True)
    return base64.urlsafe_b64encode(js.encode("ascii")).decode("ascii").rstrip("=")


def decode_config(token: str) -> dict[str, Any]:
    """Purkaa ?asennelma=-arvon (base64url, täyte valinnainen) JSON-konfiguraatioksi."""
    t = token.strip()
    if len(t) > MAX_TOKEN:
        raise ValueError(f"Linkin asennelma on liian pitkä (yli {MAX_TOKEN} merkkiä)")
    raw = base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))
    return json.loads(raw.decode("utf-8"))


def build_link(config: Mapping[str, Any], base_url: str = BASE_URL, anchor: str = ANCHOR, check: bool = True) -> str:
    """Letkukone-linkki konfiguraatiolle. check=True: virheellinen konfiguraatio -> ValueError."""
    cfg = normalize_config(config)
    if check:
        err = validate_config(cfg)
        if err:
            raise ValueError("Virheellinen asennelma: " + "; ".join(err))
    token = encode_config(cfg)
    if len(token) > MAX_TOKEN:
        raise ValueError("Asennelma on liian pitkä linkkiin")
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}asennelma={token}" + (f"#{anchor}" if anchor else "")


def parse_link(link_or_token: str) -> dict[str, Any]:
    """Konfiguraatio Letkukone-linkistä, pelkästä asennelma-arvosta tai JSON-tekstistä."""
    s = link_or_token.strip()
    if s.startswith("{"):
        return json.loads(s)
    if "asennelma=" in s or s.startswith("http"):
        q = parse_qs(urlparse(s).query)
        vals = q.get("asennelma")
        if not vals:
            raise ValueError("Linkissä ei ole asennelma-parametria")
        return decode_config(vals[0])
    return decode_config(s)


def make_config(hose: str, size: str, a: Mapping[str, Any], b: Mapping[str, Any], length_mm: float, length_mode: str = "hose", orientation: float = 0, protection: str = "none", **extra: Any) -> dict[str, Any]:
    """Uusi konfiguraatio. length_mm pyöristetään kokonaisluvuksi, orientation 0..359."""
    cfg: dict[str, Any] = {
        "hose": hose,
        "size": size,
        "a": dict(a),
        "b": dict(b),
        "lengthMm": round_mm(float(length_mm)),
        "lengthMode": length_mode,
        "orientation": round_mm(float(orientation)) % 360,
        "protection": protection,
        **extra,
    }
    return normalize_config(cfg)


def routed_config(config: Mapping[str, Any], cut_mm: float, orientation_deg: float | None = None) -> dict[str, Any]:
    """Reititetyn letkun tilauskonfiguraatio: lengthMode "hose", lengthMm = katkaisumitta pyöristettynä,
    orientation = reitistä laskettu kiertokulma (jos molemmat päät suunnattavia). orientationConfirmed pudotetaan:
    asiakas vahvistaa kiertokulman Letkukoneessa."""
    c = {k: v for k, v in dict(config).items() if k != "orientationConfirmed"}
    c["lengthMode"] = "hose"
    c["lengthMm"] = round_mm(float(cut_mm))
    if orientation_deg is not None and math.isfinite(orientation_deg):
        c["orientation"] = round_mm(float(orientation_deg)) % 360
    return normalize_config(c)
