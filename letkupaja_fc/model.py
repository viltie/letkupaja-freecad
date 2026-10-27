# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Letkupaja-letku FreeCAD-dokumentissa.

Letku on tavallinen ryhmä (App::DocumentObjectGroup), jonka ominaisuuksiin syötteet ja tulokset tallennetaan, ja
jonka sisällä on tavallisia Part::Feature-kappaleita (letku, keskilinja, yksinkertaistetut liittimet ja holkit).
Dokumentti aukeaa siis myös ilman lisäosaa (muodot säilyvät); lisäosalla letkun voi laskea uudelleen ("Päivitä").

Kulku: prepare_request (lukee dokumentin) -> routing.run_route (raskas laskenta, voi olla taustasäikeessä) ->
apply_result (kirjoittaa dokumenttiin pääsäikeessä).

Dokumentin tiedot ovat luottamattomia (FCStd-tiedostoja vaihdetaan suunnittelijoiden kesken): tilauslinkki
kootaan aina uudelleen tarkistetusta asennelmasta (safe_order_link), ei koskaan tallennetusta OrderLink-tekstistä.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Iterable

import FreeCAD as App  # type: ignore
import Part  # type: ignore

from . import fcgeom as F
from . import routing as R
from .core import L
from .i18n import core_message, tr
from .version import VERSION

TYPE_VALUE = "LetkupajaHose"
STEEL = (0.79, 0.80, 0.82)
FERRULE = (0.77, 0.78, 0.80)
DETAILED_GROUP_ROLE = "detailed"
SITE_BASE = "https://letkupaja.fi/"


def _hex_rgb(h: str, default: tuple[float, float, float] = (0.11, 0.11, 0.12)) -> tuple[float, float, float]:
    try:
        h = h.lstrip("#")
        return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except (ValueError, AttributeError):
        return default


# ---------------------------------------------------------------------------------------------- ominaisuudet

# (tyyppi, nimi, ryhmä ("in" = syötteet, "out" = tulos), ohje, vain luku)
_PROPS: list[tuple[str, str, str, str, bool]] = [
    ("App::PropertyString", "LetkupajaType", "in", "Letkupaja hose marker", True),
    ("App::PropertyString", "LetkupajaVersion", "in", "Add-on version that last routed this hose", True),
    ("App::PropertyString", "AssemblyConfig", "in", "Valittu asennelma (JSON) / chosen assembly", False),
    ("App::PropertyString", "EndA", "in", "Liitäntä A (JSON) / port A", False),
    ("App::PropertyString", "EndB", "in", "Liitäntä B (JSON) / port B", False),
    ("App::PropertyLinkListGlobal", "Obstacles", "in", "Esteiden objektit / obstacle objects", False),
    ("App::PropertyString", "ObstacleRefs", "in", "Esteet (JSON: objekti ja aliobjektin polku) / obstacles (object and sub-object path)", False),
    ("App::PropertyFloat", "BendFactor", "in", "Taivutussäteen kerroin / bend radius factor", False),
    ("App::PropertyFloat", "Clearance", "in", "Vähimmäisvara mm / minimum clearance mm", False),
    ("App::PropertyFloat", "TargetCut", "in", "Tavoitekatkaisumitta mm (0 = luonteva reitti) / target cut length mm (0 = natural)", False),
    ("App::PropertyBool", "AvoidOtherHoses", "in", "Väistä muita letkuja / avoid other hoses", False),
    ("App::PropertyLength", "CutLength", "out", "Katkaisumitta / cut length", True),
    ("App::PropertyLength", "TotalLength", "out", "Kokonaismitta / overall length", True),
    ("App::PropertyLength", "MinBendRadius", "out", "Pienin taivutussäde / minimum bend radius", True),
    ("App::PropertyLength", "RequiredBendRadius", "out", "Vaadittu taivutussäde / required bend radius", True),
    ("App::PropertyFloat", "Orientation", "out", "Kiertokulma astetta / orientation degrees", True),
    ("App::PropertyBool", "RouteOk", "out", "Reitti kunnossa / route OK", True),
    ("App::PropertyStringList", "Warnings", "out", "Varoitukset / warnings", True),
    ("App::PropertyString", "OrderLink", "out", "Tilauslinkki (tieto; avataan aina OrderConfigista koottuna) / order link (information only)", True),
    ("App::PropertyString", "OrderConfig", "out", "Tilattava asennelma (JSON) / assembly to order", True),
    ("App::PropertyVectorList", "CenterlinePoints", "out", "Keskilinja holkista holkkiin / centerline", True),
    ("App::PropertyLength", "HoseOD", "out", "Letkun ulkohalkaisija törmäystarkasteluun / hose OD for clearance", True),
    ("App::PropertyString", "EndFrames", "out", "Liitinten kehykset (JSON) / fitting frames", True),
]


def is_hose_group(obj: Any) -> bool:
    return obj is not None and hasattr(obj, "LetkupajaType") and obj.LetkupajaType == TYPE_VALUE


def hose_groups(doc: Any) -> list[Any]:
    return [o for o in doc.Objects if is_hose_group(o)]


def _contains(group: Any, obj: Any) -> bool:
    try:
        return obj in (getattr(group, "Group", None) or [])
    except Exception:  # noqa: BLE001
        return False


def owning_hose_group(obj: Any) -> Any:
    """Valitun objektin Letkupaja-letku: ryhmä itse, sen sisältämä kappale tai tarkan mallin osa (ryhmän sisällä).
    Jäsenyys päätellään sisällyksestä (Group), ei linkeistä: esteeksi valittu koneen osa on ryhmän InListissä
    (Obstacles-linkki), mutta se ei kuulu letkuun."""
    if is_hose_group(obj):
        return obj
    for p in getattr(obj, "InList", []) or []:
        if not _contains(p, obj):
            continue
        if is_hose_group(p):
            return p
        if getattr(p, "LetkupajaRole", None) == DETAILED_GROUP_ROLE:
            for q in getattr(p, "InList", []) or []:
                if is_hose_group(q) and _contains(q, p):
                    return q
    return None


def _group_name(kind: str) -> str:
    return "Letkupaja" if kind == "in" else tr("prop.result")


def ensure_props(g: Any) -> None:
    """Lisää puuttuvat ominaisuudet (vanhemmalla versiolla tehty letku)."""
    for typ, name, grp, doc_, ro in _PROPS:
        if not hasattr(g, name):
            g.addProperty(typ, name, _group_name(grp), doc_)
            if ro:
                try:
                    g.setEditorMode(name, 1)
                except Exception:  # noqa: BLE001
                    pass
    try:
        g.setEditorMode("ObstacleRefs", 2)
    except Exception:  # noqa: BLE001
        pass


def create_group(doc: Any) -> Any:
    g = doc.addObject("App::DocumentObjectGroup", "LetkupajaHose")
    g.Label = tr("obj.group")
    ensure_props(g)
    g.LetkupajaType = TYPE_VALUE
    g.BendFactor = 1.0
    g.Clearance = 2.0
    g.TargetCut = 0.0
    g.AvoidOtherHoses = True
    return g


ObstacleRef = tuple[Any, str]  # (juuriobjekti, aliobjektin polku, esim. "Runko." tai "")


def _norm_obstacles(obstacles: Iterable[Any]) -> list[ObstacleRef]:
    out: list[ObstacleRef] = []
    seen = set()
    for it in obstacles:
        obj, sub = (it if isinstance(it, tuple) else (it, ""))
        if obj is None:
            continue
        key = (obj.Name, sub)
        if key not in seen:
            seen.add(key)
            out.append((obj, sub))
    return out


def save_inputs(group: Any, config: dict[str, Any], a: R.EndSpec | None, b: R.EndSpec | None, obstacles: Iterable[Any], safety: float, margin: float, target_cut: float | None, avoid: bool) -> None:
    ensure_props(group)
    obs = _norm_obstacles(obstacles)
    group.AssemblyConfig = json.dumps(config, ensure_ascii=False)
    group.EndA = json.dumps(a.to_dict()) if a else ""
    group.EndB = json.dumps(b.to_dict()) if b else ""
    roots: list[Any] = []
    for o, _s in obs:
        if o not in roots:
            roots.append(o)
    group.Obstacles = roots
    group.ObstacleRefs = json.dumps([{"object": o.Name, "sub": s} for o, s in obs])
    group.BendFactor = float(safety)
    group.Clearance = float(margin)
    group.TargetCut = float(target_cut or 0.0)
    group.AvoidOtherHoses = bool(avoid)


def load_obstacles(group: Any) -> list[ObstacleRef]:
    doc = group.Document
    refs = []
    raw = getattr(group, "ObstacleRefs", "") or ""
    if raw:
        try:
            for r in json.loads(raw):
                o = doc.getObject(str(r.get("object", "")))
                if o is not None:
                    refs.append((o, str(r.get("sub") or "")))
        except (ValueError, AttributeError, TypeError):
            refs = []
    if not refs:
        refs = [(o, "") for o in (group.Obstacles or []) if o is not None]
    return _norm_obstacles(refs)


def load_inputs(group: Any) -> dict[str, Any]:
    cfg = json.loads(group.AssemblyConfig) if group.AssemblyConfig else None
    a = R.EndSpec.from_dict(json.loads(group.EndA)) if group.EndA else None
    b = R.EndSpec.from_dict(json.loads(group.EndB)) if group.EndB else None
    return {"config": cfg, "a": a, "b": b, "obstacles": load_obstacles(group), "safety": float(group.BendFactor or 1.0), "margin": float(group.Clearance), "target_cut": float(group.TargetCut) or None, "avoid": bool(group.AvoidOtherHoses)}


# ---------------------------------------------------------------------------------------------- tilaus


def order_config(group: Any) -> dict[str, Any] | None:
    """Tilattava asennelma tarkistettuna (None, jos puuttuu tai ei kelpaa)."""
    try:
        cfg = json.loads(group.OrderConfig) if group.OrderConfig else None
    except (ValueError, TypeError):
        return None
    if not isinstance(cfg, dict):
        return None
    try:
        cfg = L.normalize_config(cfg)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return cfg if not L.validate_config(cfg) else None


def safe_order_link(group: Any) -> str:
    """Tilauslinkki koottuna tarkistetusta asennelmasta kiinteään osoitteeseen https://letkupaja.fi/. Dokumentin
    OrderLink-tekstiä ei koskaan avata sellaisenaan."""
    cfg = order_config(group)
    if cfg is None:
        return ""
    try:
        return L.build_link(cfg, base_url=SITE_BASE)
    except ValueError:
        return ""


def route_problems(group: Any) -> list[str]:
    """Varoitukset, jotka kysytään ennen tilauksen avaamista (reitti ei kunnossa, arvioidut mitat, kiertokulma)."""
    if not bool(getattr(group, "RouteOk", False)):
        return list(group.Warnings) or [tr("res.notok")]
    return list(group.Warnings or [])


# ---------------------------------------------------------------------------------------------- valinnat


def resolve_source(doc: Any, source: dict[str, Any] | None) -> tuple[Any, Any, list[float] | None] | None:
    """Tallennettu valinta -> (alielementin muoto maailmassa, omistajan muoto, omistajan kierto) tai None.
    source = {"object": juuriobjekti, "sub": koko polku alielementtiin (esim. "Runko.Edge10")}: säiliöiden ja
    linkkien sijoitukset huomioidaan (Part.getShape kulkee polun)."""
    if not source:
        return None
    try:
        d = App.getDocument(source.get("doc")) if source.get("doc") else doc
    except Exception:  # noqa: BLE001
        d = doc
    obj = d.getObject(str(source.get("object", ""))) if d else None
    if obj is None:
        return None
    sub = str(source.get("sub", ""))
    try:
        sub_shape = Part.getShape(obj, sub, needSubElement=True)
        owner_sub = F.sub_path(sub)
        owner = Part.getShape(obj, owner_sub) if owner_sub else Part.getShape(obj)
    except Exception:  # noqa: BLE001 - poistettu tai uudelleennimetty elementti
        return None
    if sub_shape is None or sub_shape.isNull():
        return None
    return sub_shape, owner, F.shape_rotation(obj, sub)


def refresh_spec(doc: Any, spec: R.EndSpec) -> tuple[R.EndSpec, bool]:
    """Päivitä liitännän sijainti valitusta geometriasta (kone on voinut liikkua tai kääntyä). (spec, löytyikö).

    Suunta tunnistetaan uudelleen (koneesta ulos). Vain jos tunnistus on epävarma (esim. läpireikä: kumpikin suunta
    on yhtä vapaa), säilytetään aiempi suunta siirrettynä kappaleen kierron muutoksella. Käyttäjän kääntö on erikseen
    (spec.flip), joten sitä ei tarvitse arvata tästä."""
    if spec.mode != "port" or not spec.source:
        return spec, True
    got = resolve_source(doc, spec.source)
    if got is None:
        return spec, False
    sub_shape, owner, q_new = got
    p = F.port_from_subshape(sub_shape, owner)
    if p is None:
        return spec, False
    new_dir = p["direction"]
    if p.get("ambiguous"):
        ref = F.rotate_by_change(spec.direction, spec.source.get("rot"), q_new)
        if L.vec.dot(ref, new_dir) < 0:
            new_dir = L.vec.mul(new_dir, -1.0)
    spec.point, spec.direction, spec.radius = p["point"], new_dir, p["radius"]
    if q_new is not None:
        spec.source = dict(spec.source, rot=q_new)
    return spec, True


def ferrule_spec_from_step(p: dict[str, Any]) -> R.EndSpec:
    return R.EndSpec("ferrule", p["point"], p["direction"], up=p.get("up"), draw_fitting=False, source=p.get("source"))


def refresh_ferrule_spec(doc: Any, spec: R.EndSpec) -> tuple[R.EndSpec, bool]:
    if spec.mode != "ferrule" or not spec.source or not spec.source.get("step"):
        return spec, True
    p = F.port_from_step_source(doc, spec.source)
    if p is None:
        return spec, False
    spec.point, spec.direction, spec.up = p["point"], p["direction"], p.get("up")
    return spec, True


def step_roots(doc: Any) -> list[Any]:
    """Dokumentin juuriobjektit ilman Letkupaja-letkuja (niiden tarkat mallit eivät ole reitityksen päitä)."""
    return [o for o in doc.RootObjects if not is_hose_group(o)]


def obstacle_boxes(refs: Iterable[ObstacleRef]) -> list[tuple[float, float, float, float, float, float]]:
    """Esteiden rajauslaatikot maailmassa (myös App::Part-säiliöissä ja App::Link-kopioissa)."""
    out = []
    for o, sub in refs:
        b = F.bbox_of(o, sub)
        if b:
            out.append(b)
    return out


# ---------------------------------------------------------------------------------------------- reititys


def prepare_request(group: Any) -> tuple[R.RouteRequest, list[str]]:
    """Lue ryhmän syötteet ja dokumentin geometria reitityspyynnöksi. Palauttaa (pyyntö, huomautukset)."""
    doc = group.Document
    ensure_props(group)
    inp = load_inputs(group)
    notes: list[str] = []
    if not inp["config"]:
        raise ValueError(tr("err.config", e="—"))
    if inp["a"] is None or inp["b"] is None:
        raise ValueError(tr("err.need_ports"))
    specs = []
    for k, spec in (("A", inp["a"]), ("B", inp["b"])):
        spec, found = refresh_spec(doc, spec) if spec.mode == "port" else refresh_ferrule_spec(doc, spec)
        if not found:
            notes.append(tr("warn.source_lost", k=k))
        specs.append(spec)
    boxes = obstacle_boxes(inp["obstacles"])
    avoid = []
    if inp["avoid"]:
        for g in hose_groups(doc):
            if g is group or not g.CenterlinePoints:
                continue
            avoid.append(([F.T(v) for v in g.CenterlinePoints], float(g.HoseOD.Value if hasattr(g.HoseOD, "Value") else g.HoseOD)))
    req = R.RouteRequest(config=dict(inp["config"]), a=specs[0], b=specs[1], obstacles=R.obstacles_from_boxes(boxes), avoid=avoid, safety=inp["safety"], margin=inp["margin"], target_cut=inp["target_cut"])
    # Päivitetyt sijainnit talteen
    group.EndA = json.dumps(specs[0].to_dict())
    group.EndB = json.dumps(specs[1].to_dict())
    return req, notes


def _child(group: Any, role: str) -> Any:
    for o in group.Group:
        if getattr(o, "LetkupajaRole", None) == role:
            return o
    return None


def _set_child(group: Any, role: str, name: str, label: str, shape: Any, color: tuple[float, float, float] | None, transparency: int = 0) -> Any:
    obj = _child(group, role)
    created = obj is None
    if created:
        obj = group.Document.addObject("Part::Feature", name)
        obj.addProperty("App::PropertyString", "LetkupajaRole", "Letkupaja", "Letkupaja part role")
        obj.LetkupajaRole = role
        try:
            obj.setEditorMode("LetkupajaRole", 2)
        except Exception:  # noqa: BLE001
            pass
        group.addObject(obj)
    obj.Shape = shape
    obj.Label = label
    if created and App.GuiUp and color is not None:
        try:
            obj.ViewObject.ShapeColor = color
            obj.ViewObject.Transparency = transparency
            if role == "centerline":
                obj.ViewObject.LineColor = (0.85, 0.33, 0.10)
                obj.ViewObject.LineWidth = 2.0
        except Exception:  # noqa: BLE001
            pass
    return obj


def _remove_child(group: Any, role: str) -> None:
    obj = _child(group, role)
    if obj is not None:
        group.Document.removeObject(obj.Name)


def _set_visible(obj: Any, on: bool) -> None:
    if obj is not None and App.GuiUp:
        try:
            obj.ViewObject.Visibility = on
        except Exception:  # noqa: BLE001
            pass


def _sweep(wire: Any, start: Any, tangent: Any, radius: float) -> Any:
    return F.sweep_circle(wire, start, tangent, radius)


def apply_result(group: Any, req: R.RouteRequest, result: Any, notes: Iterable[str] = ()) -> dict[str, Any]:
    """Kirjoita reitityksen tulos dokumenttiin: kappaleet ja tulosominaisuudet. Palauttaa yhteenvedon."""
    ensure_props(group)
    cfg = L.normalize_config({**req.config, "lengthMm": max(1, int(req.config.get("lengthMm") or 1000))})
    ends = R.placed_ends(result, req)
    o_a, pts, o_b = R.hose_path(result, ends)
    wire, curve = F.hose_wire(o_a, pts, o_b, ends[0].x, L.vec.mul(ends[1].x, -1.0), getattr(result, "bezier", None))
    hi = L.hose_info(cfg["hose"], cfg["size"])
    notes = list(notes)
    try:
        hose_solid = _sweep(wire, o_a, ends[0].x, hi.od / 2)
    except Exception:  # noqa: BLE001 - poikkeuksellisen jyrkkä mutka: letku putkena tai pelkkä keskilinja
        try:
            hose_solid = wire.makePipe(Part.Wire(Part.makeCircle(hi.od / 2, F.V(o_a), F.V(ends[0].x))))
        except Exception:  # noqa: BLE001
            hose_solid = wire.copy()
        notes.append(tr("warn.sweep"))
    cut_mm = result.cut_length_mm
    _set_child(group, "hose", "Letku", f"{tr('obj.hose')} {cut_mm} mm" if cut_mm else tr("obj.hose"), hose_solid, _hex_rgb(hi.color))
    _set_child(group, "centerline", "Keskilinja", tr("obj.centerline"), wire, None)
    prot = cfg.get("protection") or "none"
    if prot != "none":
        pod = L.protection_od(cfg)
        pg = L.geometry.prot_geo(cfg["hose"], cfg["size"], hi.od, hi.ferrule.od).get(prot, {})
        if pod > hi.od + 0.05:
            try:
                sh = _sweep(Part.Wire([curve]), pts[0], ends[0].x, pod / 2)
            except Exception:  # noqa: BLE001
                try:
                    sh = Part.Wire([curve]).makePipe(Part.Wire(Part.makeCircle(pod / 2, F.V(pts[0]), F.V(ends[0].x))))
                except Exception:  # noqa: BLE001
                    sh = None
                notes.append(tr("warn.protection"))
            if sh is not None:
                _set_child(group, "protection", "Suoja", tr("obj.protection"), sh, _hex_rgb(str(pg.get("color", "#202124"))), 40)
            else:
                _remove_child(group, "protection")
    else:
        _remove_child(group, "protection")
    for end in ends:
        k = end.key.upper()
        if end.draw_fitting:
            shapes = F.placed_end_shapes(end, cfg)
            for role, nm, lab, col in (("fitting", f"Liitin{k}", tr("obj.fitting", k=k), STEEL), ("ferrule", f"Holkki{k}", tr("obj.ferrule", k=k), FERRULE), ("extras", f"Pultti{k}", tr("obj.extra", k=k), STEEL)):
                if shapes.get(role) is not None:
                    _set_child(group, f"{role}_{end.key}", nm, lab, shapes[role], col)
                else:
                    _remove_child(group, f"{role}_{end.key}")
        else:
            for role in ("fitting", "ferrule", "extras"):
                _remove_child(group, f"{role}_{end.key}")

    frames = {e.key: {"origin": list(e.origin), "x": list(e.x), "y": list(e.y), "rear": list(e.rear), "draw": e.draw_fitting, "swing": e.swing} for e in ends}
    notes += _update_detailed(group, cfg, frames)
    if L.is_directional(cfg["a"]) and L.is_directional(cfg["b"]) and result.orientation_deg is None:
        deg = int((result.config or cfg).get("orientation", 0) or 0)
        notes.append(tr("warn.orientation_unknown", deg=deg))
    warnings = [core_message(w) for w in list(notes) + list(result.warnings)]
    group.CutLength = float(result.cut_length or 0.0)
    group.TotalLength = float(result.total_length or 0.0)
    group.MinBendRadius = float(result.min_radius) if result.min_radius != float("inf") else 0.0
    group.RequiredBendRadius = float(result.required_radius)
    group.Orientation = float(result.orientation_deg or 0.0)
    group.RouteOk = bool(result.ok)
    group.Warnings = warnings
    group.OrderLink = result.link or ""
    group.OrderConfig = json.dumps(result.config) if result.config else ""
    group.CenterlinePoints = [F.V(p) for p in pts]
    group.HoseOD = float(result.od)
    group.EndFrames = json.dumps(frames)
    group.LetkupajaVersion = VERSION
    group.Document.recompute()
    out = R.summary(result)
    out["warnings"] = warnings
    out["hose_object"] = _child(group, "hose").Name
    out["wire_length"] = wire.Length
    return out


def update_group(group: Any) -> dict[str, Any]:
    """Laske letku uudelleen (synkronisesti)."""
    req, notes = prepare_request(group)
    result = R.run_route(req)
    return apply_result(group, req, result, notes)


# ---------------------------------------------------------------------------------------------- tarkka malli


def fitting_key(cfg: dict[str, Any]) -> str:
    """Tarkan mallin liitinosien muotoon vaikuttavat valinnat (ei pituutta eikä kiertokulmaa: ne vain siirtävät
    päitä, jolloin osat voidaan siirtää uusiin kehyksiin)."""
    keep = {k: cfg.get(k) for k in ("hose", "size", "a", "b", "protection", "sleeveCrimp", "rstFerrules")}
    return json.dumps(keep, sort_keys=True)


def _frame_placement(fr: dict[str, Any]) -> Any:
    return F.placement_from_frame(fr["origin"], fr["x"], fr["y"])


def _frames_equal(f1: dict[str, Any], f2: dict[str, Any]) -> bool:
    for k in ("origin", "x", "y"):
        if L.vec.dist(f1[k], f2[k]) > 1e-9:
            return False
    return True


def _update_detailed(group: Any, cfg: dict[str, Any], frames: dict[str, Any]) -> list[str]:
    """Pidä ladattu tarkka malli ajan tasalla uudelleenreitityksessä: jos liittimet ovat samat, osat siirretään uusiin
    liitinkehyksiin; jos asennelma muuttui (liitin, koko, letku, suoja), tarkka malli poistetaan ja yksinkertaistetut
    liittimet näytetään taas. Palauttaa huomautukset."""
    det = _child(group, DETAILED_GROUP_ROLE)
    if det is None:
        return []
    try:
        old_frames = json.loads(getattr(det, "LetkupajaFrames", "") or "{}")
        old_key = getattr(det, "LetkupajaKey", "")
    except ValueError:
        old_frames, old_key = {}, ""
    if old_key and old_key == fitting_key(cfg) and old_frames.get("a") and old_frames.get("b"):
        for o in det.Group:
            k = str(getattr(o, "LetkupajaEnd", "")).lower()
            if k not in ("a", "b") or _frames_equal(old_frames[k], frames[k]):
                continue
            delta = _frame_placement(frames[k]).multiply(_frame_placement(old_frames[k]).inverse())
            o.Placement = delta.multiply(o.Placement)
        det.LetkupajaFrames = json.dumps({k: frames[k] for k in ("a", "b")})
        _hide_simplified(group)
        return []
    doc = group.Document
    for o in list(det.Group):
        doc.removeObject(o.Name)
    doc.removeObject(det.Name)
    for k in ("a", "b"):
        for role in ("fitting", "ferrule", "extras"):
            _set_visible(_child(group, f"{role}_{k}"), True)
    return [tr("warn.detailed_removed")]


def _hide_simplified(group: Any) -> None:
    for k in ("a", "b"):
        for role in ("fitting", "ferrule", "extras"):
            _set_visible(_child(group, f"{role}_{k}"), False)


def _insert_step(doc: Any, path: str) -> tuple[list[Any], list[Any]]:
    """STEP dokumenttiin (käyttöliittymässä värien kanssa). Palauttaa (uudet juuriobjektit, kaikki tuonnin luomat)."""
    before = {o.Name for o in doc.Objects}
    if App.GuiUp:
        import ImportGui  # type: ignore

        ImportGui.insert(path, doc.Name)
    else:
        import Import  # type: ignore

        Import.insert(path, doc.Name)
    new = [o for o in doc.Objects if o.Name not in before]
    names = {o.Name for o in new}
    roots = [o for o in new if not any(p.Name in names for p in (o.InList or []))]
    return roots, new


def _remove_objects(doc: Any, names: Iterable[str]) -> None:
    """Poista nimetyt objektit (lapset ensin)."""
    objs = [doc.getObject(n) for n in names]
    for o in sorted([o for o in objs if o is not None], key=_depth, reverse=True):
        try:
            if doc.getObject(o.Name) is not None:
                doc.removeObject(o.Name)
        except Exception:  # noqa: BLE001
            pass


def import_step(doc: Any, path: str, group_label: str | None = None) -> tuple[list[Any], dict[str, dict[str, Any]]]:
    """Tuo STEP-tiedosto dokumenttiin omaan ryhmäänsä. Palauttaa (uudet juuriobjektit, tunnistetut holkit)."""
    roots, _new = _insert_step(doc, path)
    ports = F.detect_step_ports(roots)
    if group_label is not None and roots:
        grp = doc.addObject("App::DocumentObjectGroup", "LetkupajaStep")
        grp.Label = group_label
        for r in roots:
            try:
                grp.addObject(r)
            except Exception:  # noqa: BLE001
                pass
        doc.recompute()
        ports = F.detect_step_ports([grp])
    return roots, ports


class DetailedPlacementError(ValueError):
    pass


def place_detailed_step(group: Any, step_path: str) -> list[Any]:
    """Tuo tarkka STEP-malli (suora asennelma Letkupajan kehyksessä) ja siirrä kummankin pään osat reititetyn letkun
    liitinten kohdalle. Suora letku ja suoja jätetään pois (reititetty letku korvaa ne). Yksinkertaistetut liittimet
    piilotetaan vasta, kun molempien päiden holkki ja liitin on tunnistettu ja sijoitettu; muuten nostetaan
    DetailedPlacementError eikä dokumenttiin jää mitään tuonnista. Palauttaa luodut kappaleet."""
    doc = group.Document
    cfg = order_config(group)
    if not cfg:
        raise ValueError(tr("err.no_link"))
    inp = load_inputs(group)
    req = R.RouteRequest(config=cfg, a=inp["a"], b=inp["b"])
    # Liitinten kehykset reititetystä letkusta: keskilinjan päistä ja tallennetuista portin tiedoista
    ends = _ends_from_group(group, req)
    step_a, step_b = L.assembly_frames(cfg)
    transforms = {}
    for end, sf in ((ends[0], step_a), (ends[1], step_b)):
        s_pl = F.placement_from_frame(sf.origin, sf.direction, sf.up)
        w_pl = F.placement_from_frame(end.origin, end.x, end.y)
        transforms[end.key.upper()] = w_pl.multiply(s_pl.inverse())
    roots, imported = _insert_step(doc, step_path)
    imported_names = [o.Name for o in imported]
    try:
        picked = []
        found = {"A": set(), "B": set()}
        for leaf in F.iter_leaves(roots):
            label = leaf["label"]
            role = F.part_role(label)
            k = F.end_of_label(label)
            if k is None or role in ("hose", "protection"):
                continue
            gs = F.leaf_shape(leaf["root"], leaf["path"])
            if gs is None:
                continue
            picked.append((leaf, label, k, gs))
            found[k].add(role)
        missing = [f"{role} {k}" for k in ("A", "B") for role in ("ferrule", "fitting") if role not in found[k]]
        if missing:
            raise DetailedPlacementError(", ".join(missing))
        container = _child(group, DETAILED_GROUP_ROLE)
        if container is None:
            container = doc.addObject("App::DocumentObjectGroup", "TarkkaMalli")
            container.addProperty("App::PropertyString", "LetkupajaRole", "Letkupaja", "Letkupaja part role")
            container.LetkupajaRole = DETAILED_GROUP_ROLE
            group.addObject(container)
        else:
            for o in list(container.Group):
                doc.removeObject(o.Name)
        for prop in ("LetkupajaKey", "LetkupajaFrames"):
            if not hasattr(container, prop):
                container.addProperty("App::PropertyString", prop, "Letkupaja", "Detailed model state")
                try:
                    container.setEditorMode(prop, 2)
                except Exception:  # noqa: BLE001
                    pass
        container.Label = tr("obj.detailed")
        created = []
        for leaf, label, k, gs in picked:
            gs = gs.copy()
            gs.Placement = transforms[k].multiply(gs.Placement)
            nf = doc.addObject("Part::Feature", "Tarkka")
            nf.Shape = gs
            nf.Label = label
            nf.addProperty("App::PropertyString", "LetkupajaEnd", "Letkupaja", "End A/B")
            nf.LetkupajaEnd = k
            try:
                nf.setEditorMode("LetkupajaEnd", 2)
            except Exception:  # noqa: BLE001
                pass
            if App.GuiUp:
                color = None
                try:
                    color = tuple(leaf["obj"].ViewObject.ShapeColor[:3])
                except Exception:  # noqa: BLE001
                    color = None
                try:
                    nf.ViewObject.ShapeColor = color or STEEL
                except Exception:  # noqa: BLE001
                    pass
            container.addObject(nf)
            created.append(nf)
        frames = json.loads(group.EndFrames)
        container.LetkupajaKey = fitting_key(cfg)
        container.LetkupajaFrames = json.dumps({k: frames[k] for k in ("a", "b")})
    finally:
        # Vain tämän tuonnin luomat objektit pois (ei koskaan dokumentin aiempia objekteja)
        _remove_objects(doc, imported_names)
    _hide_simplified(group)
    doc.recompute()
    return created


def _depth(o: Any) -> int:
    d = 0
    cur = o
    seen = set()
    while getattr(cur, "InList", None) and cur.Name not in seen:
        seen.add(cur.Name)
        cur = cur.InList[0]
        d += 1
    return d


def _ends_from_group(group: Any, req: R.RouteRequest) -> tuple[R.PlacedEnd, R.PlacedEnd]:
    """Liitinkehykset viimeisimmästä reitityksestä (EndFrames)."""
    data = json.loads(group.EndFrames) if getattr(group, "EndFrames", "") else {}
    if not data.get("a") or not data.get("b"):
        raise ValueError(tr("err.no_link"))
    out = []
    for key in ("a", "b"):
        d = data[key]
        out.append(R.PlacedEnd(key, L.vec.vec(d["origin"]), L.vec.vec(d["x"]), L.vec.vec(d["y"]), L.vec.vec(d["rear"]), bool(d.get("draw", True)), d.get("swing")))
    return out[0], out[1]


def default_step_path(group: Any, cfg: dict[str, Any] | None = None) -> str:
    """Tarkan mallin tiedosto: dokumentin nimi + letkun sisäinen nimi + asennelman tiiviste (ei päällekirjoitusta
    eri projektien tai asennelmien välillä)."""
    from .settings import Settings

    def safe(s: str, n: int) -> str:
        return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in s)[:n]

    cfg = cfg if cfg is not None else (order_config(group) or {})
    h = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:10]
    name = f"{safe(str(group.Document.Name), 40) or 'doc'}-{safe(str(group.Name), 30) or 'letku'}-{h}.step"
    return os.path.join(Settings().models_dir(), name)
