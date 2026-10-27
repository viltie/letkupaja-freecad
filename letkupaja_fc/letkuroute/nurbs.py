# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Kuutiolliset B-splinit CAD-ohjelmille: Bézier-ketju -> sileä B-splini (yksinkertaiset sisäsolmut) ja evaluointi.

bezier_to_nurbs (route.py) antaa reitin Bézier-muodossa: sisäsolmut kolminkertaisia, eli käyrä on solmukohdissa
vain C0-jatkuva parametrinsa suhteen, vaikka se on geometrisesti sileä. Osa CAD-ohjelmista (SolidWorks
CreateSplinesByEqnParams2) voi pilkkoa sellaisen käyrän osiin tai hylätä sen. bezier_to_bspline
1. parametrisoi segmentit uudelleen niin, että derivaatta on jatkuva jokaisessa G1-liitoksessa (C1),
2. poistaa ylimääräiset solmut (Piegl & Tiller, The NURBS Book, A5.8) toleranssin rajoissa: reitin C2-splinistä
   jää yksinkertaiset sisäsolmut (n + 3 ohjauspistettä), suoran ja kaaren liitokseen kaksinkertainen solmu (C1),
3. tarkistaa tuloksen alkuperäistä käyrää vasten ja palaa Bézier-muotoon, jos poikkeama on liian suuri.

Solmut skaalataan käyrän kaaren pituuteen (mm), kuten bezier_to_nurbs. Puhdas Python, käy myös Fusionin
upotettuun Pythoniin.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

from .vec import Vec3, dist, dot, norm, sub

Bezier = tuple[Vec3, Vec3, Vec3, Vec3]


# ============================================================================================== evaluointi


def find_span(knots: Sequence[float], degree: int, n_ctrl: int, u: float) -> int:
    """Solmuväli, jolla u on (Piegl & Tiller A2.1). n_ctrl = ohjauspisteiden määrä."""
    n = n_ctrl - 1
    if u >= knots[n + 1]:
        return n
    if u <= knots[degree]:
        return degree
    lo, hi = degree, n + 1
    mid = (lo + hi) // 2
    while u < knots[mid] or u >= knots[mid + 1]:
        if u < knots[mid]:
            hi = mid
        else:
            lo = mid
        mid = (lo + hi) // 2
    return mid


def nurbs_point(nurbs: dict[str, Any], u: float) -> Vec3:
    """Ei-rationaalisen B-splinin piste parametrilla u (de Boor)."""
    p = int(nurbs.get("degree", 3))
    U = [float(k) for k in nurbs["knots"]]
    P = [tuple(float(c) for c in q) for q in nurbs["control_points"]]
    k = find_span(U, p, len(P), u)
    d = [list(P[j + k - p]) for j in range(p + 1)]
    for r in range(1, p + 1):
        for j in range(p, r - 1, -1):
            den = U[j + 1 + k - r] - U[j + k - p]
            a = 0.0 if den == 0 else (u - U[j + k - p]) / den
            d[j] = [(1 - a) * d[j - 1][i] + a * d[j][i] for i in range(3)]
    return (d[p][0], d[p][1], d[p][2])


def nurbs_derivative(nurbs: dict[str, Any], u: float) -> Vec3:
    """Ensimmäinen derivaatta parametrin suhteen (derivaattakäyrän kautta)."""
    p = int(nurbs.get("degree", 3))
    U = [float(k) for k in nurbs["knots"]]
    P = [tuple(float(c) for c in q) for q in nurbs["control_points"]]
    Q = []
    for i in range(len(P) - 1):
        den = U[i + p + 1] - U[i + 1]
        f = 0.0 if den == 0 else p / den
        Q.append(tuple(f * (P[i + 1][j] - P[i][j]) for j in range(3)))
    if p == 1:
        k = find_span(U, p, len(P), u)
        return Q[k - 1]  # type: ignore[return-value]
    return nurbs_point({"degree": p - 1, "knots": U[1:-1], "control_points": Q}, u)


def nurbs_sample(nurbs: dict[str, Any], n: int) -> list[Vec3]:
    """n pistettä tasavälein parametrissa."""
    U = nurbs["knots"]
    u0, u1 = float(U[0]), float(U[-1])
    return [nurbs_point(nurbs, u0 + (u1 - u0) * i / (n - 1)) for i in range(n)]


def interior_knot_multiplicities(nurbs: dict[str, Any]) -> list[int]:
    """Sisäsolmujen kertaluvut järjestyksessä (esim. [1, 1, 2, 1])."""
    p = int(nurbs.get("degree", 3))
    U = [float(k) for k in nurbs["knots"]]
    inner = U[p + 1 : len(U) - p - 1]
    out: list[int] = []
    prev = None
    for k in inner:
        if prev is not None and abs(k - prev) <= 1e-12 * max(1.0, abs(k)):
            out[-1] += 1
        else:
            out.append(1)
        prev = k
    return out


# ============================================================================================== muunnos


def _bez_point(s: Sequence[Sequence[float]], t: float) -> Vec3:
    u = 1 - t
    b = (u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t)
    return (sum(b[k] * s[k][0] for k in range(4)), sum(b[k] * s[k][1] for k in range(4)), sum(b[k] * s[k][2] for k in range(4)))


def _bez_len(s: Sequence[Sequence[float]]) -> float:
    from .route import bezier_length

    return bezier_length(s)  # type: ignore[arg-type]


def _intervals(segs: Sequence[Bezier], lengths: Sequence[float], g1_tol_deg: float) -> list[float]:
    """Segmenttien parametrivälit: G1-liitoksessa nopeudet yhtä suuriksi (C1), muuten kaaren pituuden suhteessa."""
    cos_tol = math.cos(math.radians(g1_tol_deg))
    iv = [max(lengths[0], 1e-9)]
    for i in range(1, len(segs)):
        left = sub(segs[i - 1][3], segs[i - 1][2])
        right = sub(segs[i][1], segs[i][0])
        nl, nr = norm(left), norm(right)
        if nl > 1e-12 and nr > 1e-12 and dot(left, right) / (nl * nr) >= cos_tol:
            iv.append(iv[-1] * nr / nl)
        else:
            iv.append(iv[-1] * max(lengths[i], 1e-9) / max(lengths[i - 1], 1e-9))
    return iv


def _remove_knot(n: int, p: int, U: list[float], P: list[list[float]], u: float, r: int, s: int, num: int, tol: float) -> int:
    """Piegl & Tiller A5.8 RemoveCurveKnot. n = viimeisen ohjauspisteen indeksi, r = solmun u viimeinen indeksi,
    s = kertaluku. Muuttaa U:ta ja P:tä paikallaan (lopusta jää käyttämättömiä alkioita, kutsuja lyhentää).
    Palauttaa poistojen määrän."""
    m = n + p + 1
    order = p + 1
    fout = (2 * r - s - p) // 2
    last = r - s
    first = r - p
    temp: list[list[float]] = [[0.0, 0.0, 0.0] for _ in range(2 * p + 1)]
    t = 0
    while t < num:
        off = first - 1
        temp[0] = list(P[off])
        temp[last + 1 - off] = list(P[last + 1])
        i, j = first, last
        ii, jj = 1, last - off
        while j - i > t:
            alfi = (u - U[i]) / (U[i + order + t] - U[i])
            alfj = (u - U[j - t]) / (U[j + order] - U[j - t])
            temp[ii] = [(P[i][k] - (1 - alfi) * temp[ii - 1][k]) / alfi for k in range(3)]
            temp[jj] = [(P[j][k] - alfj * temp[jj + 1][k]) / (1 - alfj) for k in range(3)]
            i += 1
            ii += 1
            j -= 1
            jj -= 1
        if j - i < t:
            ok = dist(tuple(temp[ii - 1]), tuple(temp[jj + 1])) <= tol  # type: ignore[arg-type]
        else:
            alfi = (u - U[i]) / (U[i + order + t] - U[i])
            q = [alfi * temp[ii + t + 1][k] + (1 - alfi) * temp[ii - 1][k] for k in range(3)]
            ok = dist(tuple(P[i]), tuple(q)) <= tol  # type: ignore[arg-type]
        if not ok:
            break
        i, j = first, last
        while j - i > t:
            P[i] = list(temp[i - off])
            P[j] = list(temp[j - off])
            i += 1
            j -= 1
        first -= 1
        last += 1
        t += 1
    if t == 0:
        return 0
    for k in range(r + 1, m + 1):
        U[k - t] = U[k]
    j = fout
    i = j
    for k in range(1, t):
        if k % 2 == 1:
            i += 1
        else:
            j -= 1
    for k in range(i + 1, n + 1):
        P[j] = P[k]
        j += 1
    return t


def bezier_to_bspline(segs: Sequence[Sequence[Sequence[float]]], *, tol: float = 1e-4, g1_tol_deg: float = 0.05, check_samples: int = 24) -> dict[str, Any]:
    """Bézier-ketju (kuutiolliset segmentit A -> B, mm) sileäksi B-splineksi. tol = sallittu poikkeama (mm)
    solmunpoistossa. Palauttaa {"degree": 3, "knots", "control_points", "rational": False, "form": "bspline"|
    "bezier", "multiplicities": [...], "max_error": mm}. Solmut 0 … käyrän pituus (mm)."""
    sg: list[Bezier] = [tuple(tuple(float(c) for c in q) for q in s) for s in segs]  # type: ignore[misc]
    if not sg:
        raise ValueError("Tyhjä käyrä")
    lengths = [_bez_len(s) for s in sg]
    total = sum(lengths)
    iv = _intervals(sg, lengths, g1_tol_deg)
    breaks = [0.0]
    for d in iv:
        breaks.append(breaks[-1] + d)
    p = 3
    U = [breaks[0]] * 4
    for b in breaks[1:-1]:
        U += [b] * 3
    U += [breaks[-1]] * 4
    P: list[list[float]] = [list(sg[0][0])]
    for s in sg:
        P += [list(s[1]), list(s[2]), list(s[3])]
    n = len(P) - 1
    # sisäsolmut lopusta alkuun, jotta aiempien indeksit eivät muutu
    for bi in range(len(breaks) - 2, 0, -1):
        u = breaks[bi]
        r = 3 + 3 * bi  # solmun viimeinen indeksi Bézier-muodossa
        # indeksit siirtyvät edellisten (myöhempien) poistojen verran vain solmun jälkeen, joten r pysyy
        removed = _remove_knot(n, p, U, P, u, r, 3, 2, tol)
        if removed:
            n -= removed
            del U[len(U) - removed :]
            del P[n + 1 :]
    scale = total / breaks[-1] if breaks[-1] > 0 else 1.0
    knots = [k * scale for k in U]
    out = {"degree": 3, "knots": knots, "control_points": [list(q) for q in P], "rational": False}
    # tarkistus alkuperäistä käyrää vasten samoissa parametrikohdissa
    err = 0.0
    for si, s in enumerate(sg):
        a, b = breaks[si] * scale, breaks[si + 1] * scale
        for k in range(check_samples + 1):
            t = k / check_samples
            err = max(err, dist(nurbs_point(out, a + (b - a) * t), _bez_point(s, t)))
    if err > max(10 * tol, 1e-3):
        from .route import bezier_to_nurbs

        bz = bezier_to_nurbs(sg)
        bz.update(form="bezier", multiplicities=interior_knot_multiplicities(bz), max_error=0.0, note=f"solmunpoisto epäonnistui (poikkeama {err:.3g} mm)")
        return bz
    out.update(form="bspline", multiplicities=interior_knot_multiplicities(out), max_error=err)
    return out
