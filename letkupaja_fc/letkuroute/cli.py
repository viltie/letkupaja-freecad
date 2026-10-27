# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Komentorivi: python -m letkuroute <komento> ... (tulostaa JSONin, mitat mm).

  route   reititä letku kahden portin välille
  info    konfiguraation mitat: letku, holkki, C-mitat, pituudet, työntösyvyydet, suoran mallin kehykset
  link    Letkukone-linkki konfiguraatiosta (valinnaisesti uusi katkaisu-/kokonaismitta ja kiertokulma)
  decode  pura Letkukone-linkki konfiguraatioksi

Esimerkki:
  python -m letkuroute route --config 'https://letkupaja.fi/?asennelma=...#rakenna' \\
      --a "0,0,0 1,0,0" --b "300,300,0 0,-1,0" --target-cut 520 --obstacle sphere:150,80,0:30
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any, Sequence

from . import geometry as G
from .link import build_link, normalize_config, parse_link, routed_config, validate_config
from .ports import Port, assembly_frames
from .route import FittingPort, parse_obstacle, route
from .vec import vec


def load_config(text: str | None) -> dict[str, Any] | None:
    """Konfiguraatio linkistä, asennelma-arvosta, JSON-tekstistä tai tiedostosta (@polku)."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("@"):
        with open(t[1:], encoding="utf-8") as f:
            t = f.read().strip()
    return parse_link(t)


def _port(args: argparse.Namespace, key: str, cfg: dict[str, Any] | None) -> Port | FittingPort:
    p = getattr(args, key)
    seal = getattr(args, f"{key}_seal")
    if bool(p) == bool(seal):
        raise SystemExit(f"Anna joko --{key} tai --{key}-seal")
    if seal:
        parts = seal.replace("|", " ").replace("/", " ").split()
        if len(parts) != 2:
            raise SystemExit(f"--{key}-seal muodossa 'x,y,z nx,ny,nz'")
        sw = getattr(args, f"{key}_swing")
        swing = None if sw in (None, "free", "vapaa") else float(sw)
        ref = getattr(args, f"{key}_swing_ref")
        return FittingPort(vec(parts[0]), vec(parts[1]), swing, vec(ref) if ref else None)
    up = getattr(args, f"{key}_up")
    ins = getattr(args, f"{key}_insertion")
    if ins is None and cfg is not None:
        ins = G.insertion_depth(cfg["hose"], cfg["size"], args.port_ref)
    return Port.parse(p, up=up, insertion=ins)


def _clean(o: Any) -> Any:
    if isinstance(o, float):
        return None if not math.isfinite(o) else round(o, 6)
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def cmd_route(args: argparse.Namespace) -> dict[str, Any]:
    cfg = load_config(args.config)
    a = _port(args, "a", cfg)
    b = _port(args, "b", cfg)
    obstacles = [parse_obstacle(o) for o in args.obstacle or []]
    avoid = []
    for path in args.avoid or []:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        avoid.append({"points": d["points"], "od": d.get("od") or args.od})
    target_total = args.target_total
    if args.target_config:
        if cfg is None:
            raise SystemExit("--target-config vaatii --config")
        target_total = G.lengths(cfg).total
    lead: float | tuple[float, float] = args.lead
    res = route(
        a,
        b,
        config=cfg,
        od=args.od,
        bend_r=args.bend_r,
        safety=args.safety,
        margin=args.margin,
        obstacles=obstacles,
        avoid=avoid,
        target_cut=args.target_cut,
        target_total=target_total,
        target_length=args.target_length,
        lead=lead,
        n_via=args.via if args.via == "auto" else int(args.via),
        n_points=args.points,
        n_through=args.through,
        max_evals=args.max_evals,
    )
    out = res.to_dict()
    if args.no_points:
        out.pop("points", None)
    return out


def cmd_info(args: argparse.Namespace) -> dict[str, Any]:
    cfg = load_config(args.config)
    if cfg is None:
        raise SystemExit("--config puuttuu")
    hi = G.hose_info(cfg["hose"], cfg["size"])
    ln = G.lengths(cfg)
    fa, fb = assembly_frames(cfg)
    ra, rb = G.resolve_end(cfg["a"], cfg["size"]), G.resolve_end(cfg["b"], cfg["size"])
    return {
        "config": normalize_config(cfg),
        "errors": validate_config(normalize_config(cfg)),
        "geometry_version": G.geometry_version(),
        "hose": {**{k: v for k, v in hi.__dict__.items() if k != "ferrule"}, "ferrule": hi.ferrule.__dict__},
        "od_with_protection": G.protection_od(cfg),
        "cut": ln.cut,
        "total": ln.total,
        "c_a": ln.c_a,
        "c_b": ln.c_b,
        "h_a": ra.h,
        "h_b": rb.h,
        "generic_a": ra.generic,
        "generic_b": rb.generic,
        "insertion": {"rear": hi.ferrule.rear, "edge": hi.ferrule.edge},
        "straight_distance": {"rear": G.straight_ref_distance(cfg, "rear"), "edge": G.straight_ref_distance(cfg, "edge")},
        "needs_orientation": G.needs_orientation(cfg),
        "frames": {"a": fa.to_dict(), "b": fb.to_dict()},
        "link": build_link(cfg, check=False),
    }


def cmd_link(args: argparse.Namespace) -> dict[str, Any]:
    cfg = load_config(args.config)
    if cfg is None:
        raise SystemExit("--config puuttuu")
    if args.cut is not None:
        cfg = routed_config(cfg, args.cut, args.orientation)
    elif args.total is not None:
        cfg = routed_config(cfg, G.cut_from_total(cfg, args.total), args.orientation)
    elif args.orientation is not None:
        cfg = dict(cfg, orientation=G.round_mm(args.orientation) % 360)
    cfg = normalize_config(cfg)
    return {"link": build_link(cfg), "config": cfg, "cut": G.lengths(cfg).cut, "total": G.lengths(cfg).total}


def cmd_decode(args: argparse.Namespace) -> dict[str, Any]:
    cfg = parse_link(args.link)
    return {"config": cfg, "errors": validate_config(cfg)}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m letkuroute", description="Letkupajan letkureititys ja Letkukone-linkit (mm)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("route", help="reititä letku")
    r.add_argument("--config", help="Letkukone-linkki, asennelma-arvo, JSON tai @tiedosto")
    for k in ("a", "b"):
        K = k.upper()
        r.add_argument(f"--{k}", help=f"{K}-portti 'x,y,z dx,dy,dz': holkin takapinnan keskipiste ja letkun lähtösuunta")
        r.add_argument(f"--{k}-up", help=f"{K}-liittimen kulman suunta (kiertokulmaa varten) 'x,y,z'")
        r.add_argument(f"--{k}-insertion", type=float, help="letkun pituus holkin sisällä porttiin asti (mm)")
        r.add_argument(f"--{k}-seal", help=f"{K}-pää koneen liitännästä: tiivistyspiste ja normaali 'x,y,z nx,ny,nz' (vaatii --config)")
        r.add_argument(f"--{k}-swing", help="kulmaliittimen kääntökulma (astetta) tai 'free' (oletus free)")
        r.add_argument(f"--{k}-swing-ref", help="kääntökulman nollasuunta 'x,y,z'")
    r.add_argument("--port-ref", choices=G.INSERTION_REFS, default="rear", help="mihin holkin kohtaan --a/--b osoittavat (oletus rear = takapinta)")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--target-cut", type=float, help="tavoitekatkaisumitta (mm)")
    g.add_argument("--target-total", type=float, help="tavoitekokonaismitta (mm, vaatii --config)")
    g.add_argument("--target-length", type=float, help="tavoitekeskilinja porttien välillä (mm)")
    g.add_argument("--target-config", action="store_true", help="tavoitteena konfiguraation pituus")
    r.add_argument("--obstacle", action="append", help="este: sphere:x,y,z:r | box:x0,y0,z0:x1,y1,z1 | capsule:x,y,z:x,y,z:r | cylinder:x,y,z:x,y,z:r | JSON")
    r.add_argument("--avoid", action="append", help="toisen letkun reitti JSON-tiedostona (route-tuloste: points, od)")
    r.add_argument("--od", type=float, help="letkun ulkohalkaisija (ohittaa configin)")
    r.add_argument("--bend-r", type=float, help="pienin taivutussäde (ohittaa configin)")
    r.add_argument("--safety", type=float, default=1.0, help="taivutussäteen varmuuskerroin (oletus 1.0)")
    r.add_argument("--margin", type=float, default=2.0, help="vähimmäisvara esteisiin ja muihin letkuihin (mm)")
    r.add_argument("--lead", type=float, default=0.0, help="suora osuus holkin jälkeen (mm)")
    r.add_argument("--via", default="auto", help="välipisteiden määrä 0-3 tai auto")
    r.add_argument("--points", type=int, default=101, help="keskilinjan pisteiden määrä")
    r.add_argument("--through", type=int, default=8, help="CAD-splinin läpikulkupisteiden määrä (6-12)")
    r.add_argument("--max-evals", type=int, default=2500, help="optimoinnin laskentabudjetti vaihetta kohden")
    r.add_argument("--no-points", action="store_true", help="jätä tiheä pistejono pois tulosteesta")
    r.set_defaults(func=cmd_route)

    i = sub.add_parser("info", help="konfiguraation mitat")
    i.add_argument("--config", required=True)
    i.set_defaults(func=cmd_info)

    lk = sub.add_parser("link", help="Letkukone-linkki")
    lk.add_argument("--config", required=True)
    lk.add_argument("--cut", type=float, help="uusi katkaisumitta (lengthMode hose)")
    lk.add_argument("--total", type=float, help="uusi kokonaismitta (muunnetaan katkaisumitaksi)")
    lk.add_argument("--orientation", type=float, help="kiertokulma (astetta)")
    lk.set_defaults(func=cmd_link)

    d = sub.add_parser("decode", help="pura linkki")
    d.add_argument("link")
    d.set_defaults(func=cmd_decode)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        out = args.func(args)
    except ValueError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(_clean(out), ensure_ascii=False, indent=None if getattr(args, "compact", False) else 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
