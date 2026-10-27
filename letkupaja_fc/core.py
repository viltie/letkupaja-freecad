# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""letkuroute-ydin lisäosalle ja ladatun mittadatan hallinta.

letkuroute on lisäosan oma alipaketti letkupaja_fc/letkuroute (reititys, mitat ja mittadata data/geometry.json), sama
kirjasto kuin Letkupajan muissa CAD-lisäosissa. Julkaistussa lisäosassa se on aina mukana. Kehitysversion
yksikkötesteissä kirjasto voi olla Pythonin polussa omana pakettinaan. Lisäosa ei muuta sys.path-polkua.

Mittadata: lisäosan mukana tulee geometry.json (paketoitu). "Tarkista päivitykset" voi ladata uudemman datan
letkupaja.fi:stä välimuistiin (geometry.json + geometry.json.meta.json). Välimuistia käytetään vain, jos se on
uudempi kuin paketoitu data tai samana päivänä julkaistu korjaus juuri tähän paketoituun dataan (meta.bundled_sha).
Jos välimuisti on viallinen tai ei toimi ytimen kanssa, palataan paketoituun dataan ja välimuisti poistetaan.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from typing import Any

if importlib.util.find_spec(__package__ + ".letkuroute") is not None:  # julkaistu lisäosa
    from . import letkuroute as L  # type: ignore[attr-defined]
else:  # kehitysversion yksikkötestit: letkuroute omana pakettinaan polussa
    import letkuroute as L  # type: ignore[no-redef]  # noqa: E402

__all__ = ["L", "apply_geometry_override", "bundled_geometry_version", "valid_geometry", "should_accept_geometry", "store_geometry_cache", "cached_geometry_etag"]

REQUIRED_KEYS = ("sizes", "fittings", "hoses", "protections")


def valid_geometry(data: object) -> bool:
    """Kelpaako ladattu mittadata (rakenne kuten Letkupajan geometry.json)."""
    return isinstance(data, dict) and all(isinstance(data.get(k), dict) and data.get(k) for k in REQUIRED_KEYS)


def _bundled_path() -> str:
    return os.path.join(os.path.dirname(L.__file__), "data", "geometry.json")


def _bundled() -> dict[str, Any]:
    with open(_bundled_path(), encoding="utf-8") as f:
        return json.load(f)


def bundled_geometry_version() -> str:
    """Lisäosan mukana tulevan mittadatan päiväys (ei muuta käytössä olevaa dataa pysyvästi)."""
    return str(_bundled().get("generated", ""))


def bundled_geometry_sha() -> str:
    with open(_bundled_path(), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _content(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k not in ("snapshot", "_letkupaja")}


def should_accept_geometry(server: Any, active: dict[str, Any]) -> bool:
    """Otetaanko palvelimen mittadata käyttöön: kelvollinen ja uudempi kuin käytössä oleva, tai sama päiväys ja eri
    sisältö (saman päivän korjaus). Vanhempaa ei koskaan oteta."""
    if not valid_geometry(server):
        return False
    new, cur = str(server.get("generated", "")), str(active.get("generated", ""))
    if new > cur:
        return True
    return new == cur and _content(server) != _content(active)


def _meta_path(path: str) -> str:
    return path + ".meta.json"


def _read_meta(path: str) -> dict[str, Any]:
    try:
        with open(_meta_path(path), encoding="utf-8") as f:
            js = json.load(f)
        return js if isinstance(js, dict) else {}
    except (OSError, ValueError):
        return {}


def cached_geometry_etag(path: str | None) -> str | None:
    """Välimuistin ETag (If-None-Match), jos välimuisti on käytössä."""
    if not path or not os.path.isfile(path):
        return None
    e = _read_meta(path).get("etag")
    return e if isinstance(e, str) and e else None


def store_geometry_cache(path: str, data: dict[str, Any], etag: str | None) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
    with open(_meta_path(path), "w", encoding="utf-8") as f:
        json.dump({"etag": etag or "", "bundled_sha": bundled_geometry_sha(), "generated": str(data.get("generated", ""))}, f)


def _drop_cache(path: str) -> None:
    for p in (path, _meta_path(path)):
        try:
            os.remove(p)
        except OSError:
            pass


def _smoke_test() -> None:
    """Ytimen perusoperaatiot ladatulla datalla (viallinen data -> poikkeus)."""
    if not L.hose_info("2sc", "dn12").known:
        raise ValueError("2SC DN12 puuttuu mittadatasta")
    if L.resolve_end({"family": "dkol", "thread": "15l", "angle": 90}, "dn12").generic:
        raise ValueError("DKOL 15L puuttuu mittadatasta")
    L.lengths(L.make_config("2sc", "dn12", {"family": "dkol", "thread": "15l", "angle": 90}, {"family": "dkol", "thread": "15l", "angle": 0}, 500))


def apply_geometry_override(path: str | None) -> str:
    """Käytä letkupaja.fi:stä ladattua mittadataa, jos se on kelvollinen ja uudempi kuin lisäosan mukana tullut (tai
    saman päivän korjaus juuri tähän paketoituun dataan). Muuten käytetään lisäosan omaa dataa. Palauttaa käytössä
    olevan mittadatan päiväyksen."""
    L.set_geometry(None)
    bundled = L.geometry_version()
    if not path or not os.path.isfile(path):
        return bundled
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):  # viallinen välimuisti: lisäosan oma data
        _drop_cache(path)
        return L.geometry_version()
    if not valid_geometry(data):
        _drop_cache(path)
        return L.geometry_version()
    gen = str(data.get("generated", ""))
    use = gen > bundled or (gen == bundled and _read_meta(path).get("bundled_sha") == bundled_geometry_sha())
    if not use:
        return bundled  # paketoitu data on uudempi (tai välimuisti oli tehty vanhemmalle paketille)
    try:
        L.set_geometry(data)
        _smoke_test()
    except Exception:  # noqa: BLE001 - data ei toimi ytimen kanssa: paketoitu data, välimuisti pois
        L.set_geometry(None)
        _drop_cache(path)
    return L.geometry_version()
