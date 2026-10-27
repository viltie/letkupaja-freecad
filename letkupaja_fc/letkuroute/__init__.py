# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""letkuroute: Letkupajan CAD-työkalujen yhteinen ydin (puhdas Python >= 3.10, ei pakollisia riippuvuuksia).

Moduulit:
* geometry: mittadata (geometry.json), letkun ja holkin mitat, C-mitat, katkaisu- ja kokonaismitta, työntösyvyys
* ports: Port (holkin takapää + suunta), EndFrame, liittimen kinematiikka tiivistyspisteestä holkkiin
* route: keskilinjan reititys (tavoitepituus tai luonnollinen), esteet, tarkistukset, CAD-splinin tiedot
* link: Letkukone-linkit (?asennelma=base64url-JSON#rakenna)
* vec: vektorit, kehykset ja yksikkömuunnokset (mm, cm, m, in)

Kaikki pituudet millimetreinä, kulmat asteina.
"""
from .geometry import (
    FERRULE_EDGE_FROM_REAR,
    FerruleInfo,
    HoseInfo,
    Lengths,
    ResolvedEnd,
    c_dim,
    cut_from_centerline,
    cut_from_total,
    geometry_version,
    h_dim,
    hose_info,
    insertion_depth,
    insertion_total,
    is_directional,
    lengths,
    needs_orientation,
    protection_od,
    resolve_end,
    round_mm,
    set_geometry,
    straight_ref_distance,
    total_from_cut,
)
from .nurbs import bezier_to_bspline, nurbs_derivative, nurbs_point, nurbs_sample
from .link import build_link, decode_config, encode_config, make_config, normalize_config, parse_link, routed_config, validate_config
from .ports import EndFrame, Port, assembly_frames, banjo_seal_point, end_frame, fitting_frame, norm_orientation, swing_dir
from .route import Box, Capsule, Cylinder, FittingPort, RouteResult, Sphere, SplineHint, analyze_points, bezier_to_nurbs, min_distance, obstacle_from_dict, parse_obstacle, route
from .vec import Frame, from_mm, to_mm

__version__ = "0.1.0"

__all__ = [
    "FERRULE_EDGE_FROM_REAR",
    "Box",
    "Capsule",
    "Cylinder",
    "EndFrame",
    "FerruleInfo",
    "FittingPort",
    "Frame",
    "HoseInfo",
    "Lengths",
    "Port",
    "ResolvedEnd",
    "RouteResult",
    "Sphere",
    "SplineHint",
    "analyze_points",
    "assembly_frames",
    "banjo_seal_point",
    "bezier_to_bspline",
    "bezier_to_nurbs",
    "build_link",
    "c_dim",
    "cut_from_centerline",
    "cut_from_total",
    "decode_config",
    "encode_config",
    "end_frame",
    "fitting_frame",
    "from_mm",
    "geometry_version",
    "h_dim",
    "hose_info",
    "insertion_depth",
    "insertion_total",
    "is_directional",
    "lengths",
    "make_config",
    "min_distance",
    "needs_orientation",
    "norm_orientation",
    "normalize_config",
    "nurbs_derivative",
    "nurbs_point",
    "nurbs_sample",
    "obstacle_from_dict",
    "parse_link",
    "parse_obstacle",
    "protection_od",
    "resolve_end",
    "round_mm",
    "route",
    "routed_config",
    "set_geometry",
    "straight_ref_distance",
    "swing_dir",
    "to_mm",
    "total_from_cut",
    "validate_config",
]
