# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Valikoiden nimet: letkutyypit, koot, liitinperheet ja kierteet (asiakkaalle näkyvät tekstit, suomi ja englanti).

Tekstit vastaavat letkupaja.fi:n Letkukoneen julkisia nimiä. Mitat tulevat ytimen geometry.json-tiedostosta;
tässä on vain nimet ja järjestys. Saatavuus (onko liitintä juuri tähän kokoon) tarkistetaan verkkokaupassa.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .core import L
from .i18n import lang

SIZES = ("dn6", "dn8", "dn10", "dn12", "dn16", "dn19", "dn25", "dn31", "dn38", "dn51")
SIZE_INFO = {
    "dn6": ("1/4″", "-04"),
    "dn8": ("5/16″", "-05"),
    "dn10": ("3/8″", "-06"),
    "dn12": ("1/2″", "-08"),
    "dn16": ("5/8″", "-10"),
    "dn19": ("3/4″", "-12"),
    "dn25": ("1″", "-16"),
    "dn31": ("1 1/4″", "-20"),
    "dn38": ("1 1/2″", "-24"),
    "dn51": ("2″", "-32"),
}

# id: (suomi, englanti)
HOSES: dict[str, tuple[str, str]] = {
    "2sc": ("2SC / 2SN", "2SC / 2SN"),
    "gates2sc": ("Gates MegaFlex 2SC", "Gates MegaFlex 2SC"),
    "1sc": ("1SC / 1SN", "1SC / 1SN"),
    "pp250": ("Painepesuriletku 250 bar", "Pressure washer hose 250 bar"),
    "pp400": ("Painepesuriletku 400 bar", "Pressure washer hose 400 bar"),
    "tp_r8": ("Termoplastinen R8", "Thermoplastic R8"),
    "tp_r8e": ("R8 sähköä johtamaton", "R8 non-conductive"),
    "tp_r7": ("Termoplastinen R7", "Thermoplastic R7"),
    "tp_mt1": ("Termoplastinen MT1", "Thermoplastic MT1"),
    "ptfe": ("PTFE-teflonletku", "PTFE hose"),
}

FAMILIES: dict[str, tuple[str, str]] = {
    "dkol": ("DKOL – metrinen naaras, kevyt sarja (L), 24° kartio", "DKOL – metric female, light series (L), 24° cone"),
    "dkos": ("DKOS – metrinen naaras, raskas sarja (S), 24° kartio", "DKOS – metric female, heavy series (S), 24° cone"),
    "dkr": ("BSP naaras (DKR), 60° kartio", "BSP female (DKR), 60° cone"),
    "agr": ("BSP uros (AGR), 60° kartio", "BSP male (AGR), 60° cone"),
    "jic": ("JIC naaras (DKJ), 37° kartio", "JIC female (DKJ), 37° flare"),
    "jicm": ("JIC uros (AGJ), 37° kartio", "JIC male (AGJ), 37° flare"),
    "orfs": ("ORFS naaras, tasotiiviste", "ORFS female, face seal"),
    "orfsm": ("ORFS uros, tasotiiviste", "ORFS male, face seal"),
    "cel": ("CEL-putkitartunta (L-sarja)", "CEL tube stub (L series)"),
    "ces": ("CES-putkitartunta (S-sarja)", "CES tube stub (S series)"),
    "sae61": ("SAE-laippa 3000 psi (code 61)", "SAE flange 3000 psi (code 61)"),
    "sae62": ("SAE-laippa 6000 psi (code 62)", "SAE flange 6000 psi (code 62)"),
    "banjo": ("Silmukka (banjo)", "Banjo"),
    "ppm22": ("Painepesuri M22×1,5", "Pressure washer M22×1.5"),
    "ppkahva": ("Painepesurin kahvaliitin", "Pressure washer gun fitting"),
    "npt": ("NPTF uros (AGN)", "NPTF male (AGN)"),
}

_METRIC_L = [("6l", "6L – M12×1,5", "dn6"), ("8l", "8L – M14×1,5", "dn6"), ("10l", "10L – M16×1,5", "dn8"), ("12l", "12L – M18×1,5", "dn10"), ("15l", "15L – M22×1,5", "dn12"), ("18l", "18L – M26×1,5", "dn16"), ("22l", "22L – M30×2", "dn19"), ("28l", "28L – M36×2", "dn25"), ("35l", "35L – M45×2", "dn31"), ("42l", "42L – M52×2", "dn38")]
_METRIC_S = [("6s", "6S – M14×1,5", "dn6"), ("8s", "8S – M16×1,5", "dn6"), ("10s", "10S – M18×1,5", "dn6"), ("12s", "12S – M20×1,5", "dn8"), ("14s", "14S – M22×1,5", "dn10"), ("16s", "16S – M24×1,5", "dn12"), ("20s", "20S – M30×2", "dn16"), ("25s", "25S – M36×2", "dn19"), ("30s", "30S – M42×2", "dn25"), ("38s", "38S – M52×2", "dn31")]
_BSP = [("g14", "G 1/4″ (BSP)", "dn6"), ("g38", "G 3/8″ (BSP)", "dn10"), ("g12", "G 1/2″ (BSP)", "dn12"), ("g58", "G 5/8″ (BSP)", "dn16"), ("g34", "G 3/4″ (BSP)", "dn19"), ("g1", "G 1″ (BSP)", "dn25"), ("g114", "G 1 1/4″ (BSP)", "dn31"), ("g112", "G 1 1/2″ (BSP)", "dn38"), ("g2", "G 2″ (BSP)", "dn51")]
_JIC = [("04", "-04 – 7/16″-20 UNF", "dn6"), ("05", "-05 – 1/2″-20 UNF", "dn8"), ("06", "-06 – 9/16″-18 UNF", "dn10"), ("07", "-07 – 5/8″-18 UNF", "dn10"), ("08", "-08 – 3/4″-16 UNF", "dn12"), ("10", "-10 – 7/8″-14 UNF", "dn16"), ("12", "-12 – 1 1/16″-12 UN", "dn19"), ("14", "-14 – 1 3/16″-12 UN", "dn19"), ("16", "-16 – 1 5/16″-12 UN", "dn25"), ("20", "-20 – 1 5/8″-12 UN", "dn31"), ("24", "-24 – 1 7/8″-12 UN", "dn38"), ("32", "-32 – 2 1/2″-12 UN", "dn51")]
_ORFS = [("04", "-04 – 9/16″-18 UNF", "dn6"), ("06", "-06 – 11/16″-16 UN", "dn10"), ("08", "-08 – 13/16″-16 UN", "dn12"), ("10", "-10 – 1″-14 UNS", "dn16"), ("12", "-12 – 1 3/16″-12 UN", "dn19"), ("16", "-16 – 1 7/16″-12 UN", "dn25"), ("20", "-20 – 1 11/16″-12 UN", "dn31"), ("24", "-24 – 2″-12 UN", "dn38")]
_SAE61 = [("08", "1/2″ (-08), laippa Ø 30,2 mm", "dn12"), ("10", "5/8″ (-10), laippa Ø 34,0 mm", "dn16"), ("12", "3/4″ (-12), laippa Ø 38,1 mm", "dn19"), ("16", "1″ (-16), laippa Ø 44,5 mm", "dn25"), ("20", "1 1/4″ (-20), laippa Ø 50,8 mm", "dn31"), ("24", "1 1/2″ (-24), laippa Ø 60,3 mm", "dn38"), ("32", "2″ (-32), laippa Ø 71,4 mm", "dn51"), ("40", "2 1/2″ (-40), laippa Ø 84,1 mm", "dn51")]
_SAE62 = [("08", "1/2″ (-08), laippa Ø 31,8 mm", "dn12"), ("12", "3/4″ (-12), laippa Ø 41,3 mm", "dn19"), ("16", "1″ (-16), laippa Ø 47,6 mm", "dn25"), ("20", "1 1/4″ (-20), laippa Ø 54,0 mm", "dn31"), ("24", "1 1/2″ (-24), laippa Ø 63,5 mm", "dn38"), ("32", "2″ (-32), laippa Ø 79,4 mm", "dn51")]
_BANJO = [("m10", "Ø 10 mm (M10-pultille)", "dn6"), ("m12", "Ø 12 mm (M12-pultille)", "dn6"), ("m14", "Ø 14 mm (M14-pultille)", "dn8"), ("m16", "Ø 16 mm (M16-pultille)", "dn10"), ("m18", "Ø 18 mm (M18-pultille)", "dn10"), ("m22", "Ø 22 mm (M22-pultille)", "dn12"), ("m26", "Ø 26 mm (M26-pultille)", "dn19"), ("g14", "G 1/4″ (BSP-pultille)", "dn6"), ("g38", "G 3/8″ (BSP-pultille)", "dn10"), ("g12", "G 1/2″ (BSP-pultille)", "dn12"), ("g34", "G 3/4″ (BSP-pultille)", "dn19"), ("g1", "G 1″ (BSP-pultille)", "dn25")]

THREADS: dict[str, list[tuple[str, str, str]]] = {
    "dkol": _METRIC_L,
    "dkos": _METRIC_S,
    "dkr": _BSP,
    "agr": _BSP,
    "jic": _JIC,
    "jicm": _JIC,
    "orfs": _ORFS,
    "orfsm": _ORFS,
    "cel": _METRIC_L,
    "ces": _METRIC_S,
    "sae61": _SAE61,
    "sae62": _SAE62,
    "banjo": _BANJO,
    "ppm22": [("m22s", "M22×1,5, matala mutteri", "dn10"), ("m22u", "M22×1,5, korkea mutteri", "dn10")],
    "ppkahva": [("d10", "Ø10 mm kara", "dn6"), ("d11", "Ø11 mm kara", "dn6")],
    "npt": [("14", "1/4″ NPTF", "dn6"), ("38", "3/8″ NPTF", "dn10"), ("12", "1/2″ NPTF", "dn12"), ("34", "3/4″ NPTF", "dn19"), ("1", "1″ NPTF", "dn25")],
}

PROTECTIONS: dict[str, tuple[str, str]] = {
    "none": ("Ei suojaa", "No protection"),
    "spiral": ("Muovispiraali", "Plastic spiral guard"),
    "sleeve": ("Tekstiilisuojasukka", "Textile sleeve"),
    "fire": ("Lämpösuojasukka", "Fire sleeve"),
}

ANGLES: dict[int, tuple[str, str]] = {0: ("Suora", "Straight"), 45: ("45° kulma", "45° elbow"), 90: ("90° kulma", "90° elbow")}

BOLTS: dict[str, tuple[str, str]] = {
    "": ("Ei pulttia", "No bolt"),
    "single": ("Pultti ja tiivisteet", "Bolt and seals"),
    "double": ("Kaksoispultti ja tiivisteet", "Double bolt and seals"),
    "seals": ("Vain tiivisteet", "Seals only"),
}
SEALS: dict[str, tuple[str, str]] = {"usit": ("Usit-tiiviste", "Bonded seal"), "copper": ("Kuparitiiviste", "Copper washer")}

_EN_WORDS = (("-pultille", " bolt"), ("pultille", "bolt"), ("laippa", "flange"), ("matala mutteri", "low nut"), ("korkea mutteri", "high nut"), ("kara", "stem"))


def _pick(pair: tuple[str, str]) -> str:
    return pair[1] if lang() == "en" else pair[0]


def _en_label(label: str) -> str:
    out = label
    m = re.match(r"^Ø (\d+) mm \((M\d+|BSP)-pultille\)$", out)
    if m:
        return f"Ø {m.group(1)} mm (for {m.group(2)} bolt)"
    m = re.match(r"^(G [^(]+)\(BSP-pultille\)$", out)
    if m:
        return f"{m.group(1)}(for BSP bolt)"
    for fi, en in _EN_WORDS:
        out = out.replace(fi, en)
    return re.sub(r"(\d),(\d)", r"\1.\2", out)  # desimaalipilkku -> piste


def hose_label(hose: str) -> str:
    return _pick(HOSES[hose]) if hose in HOSES else hose


def size_label(size: str) -> str:
    inch, dash = SIZE_INFO.get(size, ("", ""))
    return f"{size.upper()} – {inch} ({dash})" if inch else size.upper()


def family_label(family: str) -> str:
    return _pick(FAMILIES[family]) if family in FAMILIES else family.upper()


def thread_label(family: str, thread: str) -> str:
    for tid, label, _nom in THREADS.get(family, []):
        if tid == thread:
            return _en_label(label) if lang() == "en" else label
    return thread.upper()


def angle_label(angle: int) -> str:
    return _pick(ANGLES.get(int(angle), (f"{angle}°", f"{angle}°")))


def protection_label(prot: str) -> str:
    return _pick(PROTECTIONS.get(prot, (prot, prot)))


def bolt_label(bolt: str) -> str:
    return _pick(BOLTS.get(bolt or "", (bolt, bolt)))


def seal_label(seal: str) -> str:
    return _pick(SEALS.get(seal, (seal, seal)))


def _geo() -> dict:
    from .core import L as _L

    return _L.geometry.geo()


def hose_types() -> list[str]:
    """Letkutyypit, joille on mittadataa (valikon järjestyksessä)."""
    hoses = _geo().get("hoses", {})
    return [h for h in HOSES if h in hoses] + [h for h in hoses if h not in HOSES]


def sizes_for(hose: str) -> list[str]:
    """Letkutyypin koot mittadatasta (pienimmästä suurimpaan)."""
    have = _geo().get("hoses", {}).get(hose, {})
    return [s for s in SIZES if s in have]


def families() -> list[str]:
    fams = _geo().get("fittings", {})
    return [f for f in FAMILIES if f in fams]


def angles_for(family: str) -> list[int]:
    fam = _geo().get("fittings", {}).get(family) or {}
    return [int(a) for a in fam.get("angles", [0])] or [0]


def threads_for(family: str) -> list[str]:
    """Perheen kierteet: luettelon järjestyksessä ne, joille on mittadataa (puuttuvat arvioidaan)."""
    have = (_geo().get("fittings", {}).get(family) or {}).get("threads", {})
    listed = [tid for tid, _l, _n in THREADS.get(family, [])]
    return [t for t in listed if t in have] + [t for t in have if t not in listed]


def default_thread(family: str, size: str) -> str:
    """Koon nimelliskierre (esim. DN12 -> 15L), muuten lähin nimelliskoko."""
    opts = threads_for(family)
    if not opts:
        return ""
    nominal = {tid: nom for tid, _l, nom in THREADS.get(family, [])}
    idx = SIZES.index(size) if size in SIZES else 3

    def score(t: str) -> tuple[int, int]:
        n = nominal.get(t)
        return (abs(SIZES.index(n) - idx) if n in SIZES else 99, opts.index(t))

    return min(opts, key=score)


# Perheet, joiden kierteen nimelliskoko ei kerro letkun kokoa (painepesurin liittimet sopivat useaan kokoon)
_NO_SIZE_HINT = {"ppm22", "ppkahva"}


def thread_nominal(family: str, thread: str) -> str | None:
    """Kierteen tavallinen letkukoko (esim. DKOL 15L -> dn12) tai None."""
    for tid, _l, nom in THREADS.get(family, []):
        if tid == thread:
            return nom
    return None


def size_mismatch(end: dict, size: str) -> tuple[str, str] | None:
    """Varoitus, jos pään kierre on tavallisesti selvästi eri kokoiselle letkulle (yli yhden koon ero), esim. DN6-letku
    ja 42L-liitin: (kierteen nimi, nimelliskoko). Saatavuustaulukkoa ei ole (hinnaton taulukko vaatisi verkkokaupan
    rajapinnan), joten tämä on vain vihje; Letkukone ja tarkan mallin lataus tarkistavat saatavuuden."""
    fam, thread = str(end.get("family", "")), str(end.get("thread", ""))
    nom = thread_nominal(fam, thread)
    if fam in _NO_SIZE_HINT or nom not in SIZES or size not in SIZES:
        return None
    if abs(SIZES.index(nom) - SIZES.index(size)) <= 1:
        return None
    return thread_label(fam, thread).split(" – ")[0], nom


@dataclass
class EndChoice:
    family: str = "dkol"
    thread: str = "15l"
    angle: int = 0
    bolt: str = ""
    seal: str = "usit"

    def to_end(self) -> dict:
        e: dict = {"family": self.family, "thread": self.thread, "angle": int(self.angle)}
        if self.family == "banjo" and self.bolt:
            e["bolt"] = self.bolt
            e["seal"] = self.seal or "usit"
        return e

    @staticmethod
    def from_end(e: dict) -> "EndChoice":
        return EndChoice(str(e.get("family", "dkol")), str(e.get("thread", "")), int(e.get("angle", 0) or 0), str(e.get("bolt", "") or ""), str(e.get("seal", "usit") or "usit"))


def end_title(e: dict) -> str:
    """Lyhyt kuvaus päästä, esim. "DKOL 15L – M22×1,5, 90° kulma"."""
    fam = family_label(e.get("family", "")).split(" – ")[0].split(",")[0]
    return f"{fam} {thread_label(e.get('family', ''), e.get('thread', ''))}, {angle_label(int(e.get('angle', 0) or 0))}"


def assembly_title(cfg: dict) -> str:
    return f"{hose_label(cfg['hose'])} {size_label(cfg['size']).split(' – ')[0]}"


def validate_choice(cfg: dict) -> list[str]:
    """Rakenteen tarkistus ytimellä (suomenkieliset viestit käännetään i18n.core_message-funktiolla)."""
    return L.validate_config(cfg)
