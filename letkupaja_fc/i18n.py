# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Kieli (suomi/englanti) ja tekstit. Kieli valitaan automaattisesti FreeCADin kieliasetuksesta tai järjestelmän
kielestä; asetuksen "Language" (fi/en/auto) voi pakottaa.

Ytimen (letkuroute) varoitukset ja virheet ovat suomeksi; core_message() kääntää ne englanniksi tunnettujen
muotojen perusteella (tuntematon teksti näytetään sellaisenaan).
"""
from __future__ import annotations

import locale
import os
import re

_LANG: str | None = None


_FI_NAMES = ("finnish", "suomi")


def _is_finnish_locale(code: str) -> bool:
    """Kielikoodi tai -nimi: "fi", "fi_FI", "fi-FI.UTF-8", "Finnish", "Finnish_Finland.1252", "suomi"."""
    c = (code or "").strip().lower()
    if not c:
        return False
    head = c.replace("-", "_").split(".")[0].split("@")[0]
    if head == "fi" or head.startswith("fi_"):
        return True
    return head.split("_")[0] in _FI_NAMES


def detect_language(pref: str = "auto", freecad_language: str = "", system_locale: str | None = None) -> str:
    """fi tai en. pref: "fi" | "en" | "auto". freecad_language: FreeCADin asetus (esim. "Finnish", "English", tyhjä =
    järjestelmän kieli). system_locale: esim. "fi_FI" (None = luetaan ympäristöstä). "Filipino" ei ole suomi."""
    if pref in ("fi", "en"):
        return pref
    fl = (freecad_language or "").strip()
    if fl:
        return "fi" if _is_finnish_locale(fl) else "en"
    if system_locale is None:
        system_locale = ""
        try:
            from PySide import QtCore  # type: ignore

            system_locale = QtCore.QLocale.system().name()
        except Exception:  # noqa: BLE001 - ei Qt:ta (testit, freecadcmd)
            for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
                if os.environ.get(var):
                    system_locale = os.environ[var]
                    break
            if not system_locale:
                try:
                    system_locale = locale.getlocale()[0] or ""
                except (ValueError, TypeError):
                    system_locale = ""
    return "fi" if _is_finnish_locale(system_locale) else "en"


def lang() -> str:
    global _LANG
    if _LANG is None:
        try:
            from . import settings

            s = settings.Settings()
            _LANG = detect_language(s.get_str("Language", "auto"), s.freecad_language())
        except Exception:  # noqa: BLE001
            _LANG = detect_language()
    return _LANG


def set_lang(value: str | None) -> None:
    """Pakota kieli ("fi"/"en") tai None = tunnista uudelleen."""
    global _LANG
    _LANG = value


def fmt_num(x: float, decimals: int = 1) -> str:
    """Luku kielen mukaisella desimaalierottimella (fi: pilkku)."""
    s = f"{x:.{decimals}f}"
    return s.replace(".", ",") if lang() == "fi" else s


def fmt_vec(p, decimals: int = 1) -> str:
    """Piste tai suunta: suomeksi "12,0; 30,5; 7,0" (desimaalipilkku), englanniksi "12.0, 30.5, 7.0"."""
    sep = "; " if lang() == "fi" else ", "
    return sep.join(fmt_num(float(c), decimals) for c in p)


def api_error_text(e: Exception) -> str:
    """letkupaja.fi:n virhe käyttöliittymän kielellä (palvelimen error/error_en, lisätiedot, uudelleenyritys)."""
    from .api import ApiError, NetworkError

    if isinstance(e, NetworkError):
        if getattr(e, "certificate", False):
            return tr("err.cert", e=e.message)
        return tr("err.network", e=e.message)
    if isinstance(e, ApiError):
        msg = e.text(lang())
        if e.retry_after and e.status in (429, 503):
            msg += tr("err.retry", s=e.retry_after)
        return tr("err.server", status=e.status, e=msg)
    return str(e)


def fmt_mm(x: float | None, decimals: int = 1) -> str:
    if x is None:
        return "–"
    if x == float("inf"):
        return "∞ (" + ("suora" if lang() == "fi" else "straight") + ")"
    return f"{fmt_num(x, decimals)} mm"


# avain: (suomi, englanti)
STRINGS: dict[str, tuple[str, str]] = {
    # työpöytä ja komennot
    "wb.menu": ("Letkupaja", "Letkupaja"),
    "wb.tooltip": ("Hydrauliikkaletkujen reititys ja tilaus Letkupajasta", "Route hydraulic hoses and order them from Letkupaja"),
    "cmd.route": ("Reititä letku", "Route hose"),
    "cmd.route.tip": ("Reititä hydrauliikkaletku kahden liitännän välille: katkaisumitta, taivutussäde ja tilauslinkki", "Route a hydraulic hose between two ports: cut length, bend radius and order link"),
    "cmd.update": ("Päivitä letkut", "Update hoses"),
    "cmd.update.tip": ("Laske valitut (tai kaikki) Letkupaja-letkut uudelleen, esim. kun koneen osia on siirretty", "Re-route the selected (or all) Letkupaja hoses, e.g. after moving machine parts"),
    "cmd.import": ("Tuo Letkupaja-STEP", "Import Letkupaja STEP"),
    "cmd.import.tip": ("Tuo Letkupajasta ladattu STEP-malli dokumenttiin", "Import a STEP model downloaded from Letkupaja"),
    "cmd.detailed": ("Lataa tarkka malli", "Download detailed model"),
    "cmd.detailed.tip": ("Lataa valitun letkun tarkka liitinmalli (STEP) Letkupajasta ja sijoita se reititettyyn letkuun", "Download the detailed fitting model (STEP) for the selected hose and place it on the routed hose"),
    "cmd.order": ("Avaa tilaus Letkupajassa", "Open order in Letkupaja"),
    "cmd.order.tip": ("Avaa valitun letkun tilauslinkki selaimessa", "Open the order link of the selected hose in the browser"),
    "cmd.login": ("Kirjaudu Letkupajaan", "Sign in to Letkupaja"),
    "cmd.login.tip": ("Yhdistä lisäosa Letkupaja-tiliisi tarkkojen mallien lataamista varten", "Connect the add-on to your Letkupaja account to download detailed models"),
    "cmd.logout": ("Kirjaudu ulos", "Sign out"),
    "cmd.logout.tip": ("Peru Letkupaja-tilin tunniste palvelimella ja poista se tältä koneelta", "Revoke the Letkupaja account token on the server and remove it from this computer"),
    "cmd.updates": ("Tarkista päivitykset", "Check for updates"),
    "cmd.updates.tip": ("Tarkista lisäosan ja mittadatan uusin versio letkupaja.fi:stä", "Check letkupaja.fi for a newer add-on and dimension data"),
    "cmd.about": ("Tietoja", "About"),
    "cmd.about.tip": ("Versio, käyttöehdot ja tietosuoja", "Version, terms and privacy"),
    "cmd.help": ("Ohje", "Help"),
    "cmd.help.tip": ("Avaa lisäosan ohje letkupaja.fi:ssä (asennus, käyttö, vianetsintä)", "Open the add-on help on letkupaja.fi (installation, use, troubleshooting)"),
    # tehtäväpaneeli
    "task.title": ("Reititä letku", "Route hose"),
    "grp.hose": ("Letku ja liittimet", "Hose and fittings"),
    "grp.ends": ("Liitännät", "Ports"),
    "grp.route": ("Reititys", "Routing"),
    "grp.result": ("Tulos", "Result"),
    "link.placeholder": ("Liitä Letkupaja-linkki (valinnainen)", "Paste a Letkupaja link (optional)"),
    "link.read": ("Lue linkki", "Read link"),
    "link.read.ok": ("Linkin asennelma luettu.", "Assembly read from the link."),
    "hint.size": ("Huom: {thread} on tavallisesti {nom}-letkulle (valittu {size}). Saatavuus tarkistetaan Letkukoneessa ja tarkkaa mallia ladattaessa.", "Note: {thread} is normally used with a {nom} hose (selected {size}). Availability is checked in the configurator and when downloading the detailed model."),
    "link.extras": ("Linkistä säilyvät myös: {items}", "Also kept from the link: {items}"),
    "extra.sleeveCrimp": ("sukan puristus", "sleeve crimp"),
    "extra.caps": ("suojatulpat", "protective caps"),
    "extra.rstFerrules": ("RST-holkit", "stainless ferrules"),
    "extra.tag": ("merkintä", "tag"),
    "extra.rst_a": ("A-pään RST-liitin", "stainless fitting at end A"),
    "extra.rst_b": ("B-pään RST-liitin", "stainless fitting at end B"),
    "lbl.hose": ("Letkutyyppi", "Hose type"),
    "lbl.size": ("Koko", "Size"),
    "lbl.protection": ("Suoja", "Protection"),
    "lbl.end": ("Pää {k}", "End {k}"),
    "lbl.family": ("Liitin", "Fitting"),
    "lbl.thread": ("Kierre", "Thread"),
    "lbl.angle": ("Kulma", "Angle"),
    "lbl.bolt": ("Banjopultti", "Banjo bolt"),
    "lbl.seal": ("Tiiviste", "Seal"),
    "end.mode.port": ("Koneen liitäntä (tiivistyspinta)", "Machine port (sealing face)"),
    "end.mode.ferrule": ("Holkin takapää (valmis liitinmalli)", "Ferrule rear face (existing fitting model)"),
    "end.pick": ("Valitse {k}", "Pick {k}"),
    "end.pick.tip": ("Valitse 3D-näkymästä liitännän ympyräreuna tai lieriöpinta ja paina tätä (tai paina ensin ja valitse sitten)", "Select the port's circular edge or cylindrical face in the 3D view and press this (or press first, then select)"),
    "end.picking": ("Valitse liitäntä {k} 3D-näkymästä…", "Select port {k} in the 3D view…"),
    "end.none": ("ei valittu", "not selected"),
    "end.flip": ("Käännä suunta", "Flip direction"),
    "end.offset": ("Siirto", "Offset"),
    "end.offset.tip": ("Siirrä tiivistyspistettä liitännän akselin suunnassa (mm), esim. jos valittu reuna ei ole tiivistyspinnassa", "Move the sealing point along the port axis (mm), e.g. if the picked edge is not on the sealing face"),
    "end.swing": ("Kääntökulma", "Swing angle"),
    "end.swing.auto": ("automaattinen", "automatic"),
    "end.swing.tip": ("Kulmaliittimen ja banjon kääntö liitännän akselin ympäri. Automaattinen etsii parhaan.", "Rotation of an elbow or banjo about the port axis. Automatic finds the best."),
    "end.status": ("Ø {d} mm, piste ({p}), suunta ({n})", "Ø {d} mm, point ({p}), direction ({n})"),
    "end.status.ferrule": ("holkki: piste ({p}), suunta ({n})", "ferrule: point ({p}), direction ({n})"),
    "detect.step": ("Tunnista Letkupaja-STEP (Holkki A / Holkki B)", "Detect Letkupaja STEP (ferrules \u201cHolkki A\u201d / \u201cHolkki B\u201d)"),
    "detect.none": ("Dokumentista ei löytynyt Letkupaja-STEP-mallin holkkeja (Holkki A ja Holkki B).", "No Letkupaja STEP ferrules (Holkki A and Holkki B) were found in the document."),
    "detect.ok": ("Holkit tunnistettu: {n} kpl.", "Ferrules detected: {n}."),
    "obst.add": ("Lisää valitut esteiksi", "Add selected as obstacles"),
    "obst.clear": ("Tyhjennä esteet", "Clear obstacles"),
    "obst.count": ("Esteet: {n} (rajauslaatikot)", "Obstacles: {n} (bounding boxes)"),
    "obst.tip": ("Letku pidetään valittujen kappaleiden rajauslaatikoiden ulkopuolella. Valitse pienet osat, ei koko konetta.", "The hose is kept outside the bounding boxes of the selected bodies. Select small parts, not the whole machine."),
    "lbl.safety": ("Taivutussäteen kerroin", "Bend radius factor"),
    "lbl.safety.tip": ("Vaadittu pienin taivutussäde = letkun pienin taivutussäde × kerroin", "Required minimum bend radius = hose minimum bend radius × factor"),
    "lbl.margin": ("Vähimmäisvara", "Minimum clearance"),
    "lbl.target": ("Tavoitekatkaisumitta", "Target cut length"),
    "lbl.target.tip": ("Jos valittu, letkun pituus ratkaistaan tähän katkaisumittaan. Muuten etsitään luonteva reitti.", "If checked, the hose is solved to this cut length. Otherwise a natural route is used."),
    "lbl.avoid": ("Väistä muita Letkupaja-letkuja", "Avoid other Letkupaja hoses"),
    "lbl.auto": ("Päivitä automaattisesti", "Update automatically"),
    "btn.update": ("Päivitä", "Update"),
    "btn.copy": ("Kopioi tilauslinkki", "Copy order link"),
    "btn.open": ("Avaa Letkupajassa", "Open in Letkupaja"),
    "btn.detailed": ("Lataa tarkka malli", "Download detailed model"),
    "btn.detailed.tip": ("Hyväksyy paneelin (reititys tallennetaan) ja lataa tarkan liitinmallin Letkupajasta", "Accepts the panel (the routing is saved) and downloads the detailed fitting model from Letkupaja"),
    "btn.cancel": ("Peruuta", "Cancel"),
    "busy": ("Reititetään…", "Routing…"),
    "res.cut": ("Katkaisumitta", "Cut length"),
    "res.total": ("Kokonaismitta", "Overall length"),
    "res.radius": ("Pienin taivutussäde", "Minimum bend radius"),
    "res.radius.req": ("vaatimus ≥ {r}", "required ≥ {r}"),
    "res.clear": ("Pienin vara esteisiin", "Minimum obstacle clearance"),
    "res.clear.hoses": ("Pienin vara muihin letkuihin", "Minimum clearance to other hoses"),
    "res.orient": ("Kiertokulma", "Orientation"),
    "res.orient.tip": ("B-pään kulman suunta myötäpäivään A-päästä katsottuna. Vahvista Letkupajassa ennen tilausta.", "Direction of end B clockwise as seen from end A. Confirm it in Letkupaja before ordering."),
    "res.orient.unknown": ("ei laskettu – määritä Letkukoneessa", "not computed – set it in the configurator"),
    "res.ok": ("Reitti kunnossa.", "Route OK."),
    "res.notok": ("Reitissä on ongelmia – katso varoitukset.", "The route has problems – see warnings."),
    "res.none": ("Valitse liitännät A ja B ja paina Päivitä.", "Pick ports A and B and press Update."),
    "res.link.copied": ("Tilauslinkki kopioitu leikepöydälle.", "Order link copied to the clipboard."),
    "res.note": ("Linkki avaa Letkukoneen valmiilla asennelmalla (katkaisumitta). Hinta ja saatavuus tarkistetaan verkkokaupassa.", "The link opens the Letkupaja configurator with this assembly (cut length). Price and availability are checked in the web shop."),
    "terms": ("Käyttöehdot", "Terms of use"),
    "order.confirm": ("Reitissä on ongelmia:\n{w}\n\nTarkista asennelma Letkukoneessa ennen tilausta. Letkua ei saa asentaa pienemmälle taivutussäteelle kuin sen pienin sallittu säde.", "The route has problems:\n{w}\n\nCheck the assembly in the configurator before ordering. Never install a hose tighter than its minimum bend radius."),
    "order.anyway.open": ("Avaa tilaus silti", "Open the order anyway"),
    "order.anyway.copy": ("Kopioi linkki silti", "Copy the link anyway"),
    "prop.result": ("Tulos", "Result"),
    "terms.line": ("Käyttämällä lisäosaa hyväksyt käyttöehdot: {url}", "By using the add-on you accept the terms: {url}"),
    # objektit
    "obj.group": ("Letkupaja-letku", "Letkupaja hose"),
    "obj.hose": ("Letku", "Hose"),
    "obj.centerline": ("Keskilinja", "Centerline"),
    "obj.protection": ("Suoja", "Protection"),
    "obj.fitting": ("Liitin {k} (yksinkertaistettu)", "Fitting {k} (simplified)"),
    "obj.ferrule": ("Holkki {k} (yksinkertaistettu)", "Ferrule {k} (simplified)"),
    "obj.extra": ("{k} pultti (yksinkertaistettu)", "{k} bolt (simplified)"),
    "obj.detailed": ("Tarkka malli", "Detailed model"),
    "obj.imported": ("Letkupaja-STEP", "Letkupaja STEP"),
    # virheet ja ilmoitukset
    "err.title": ("Letkupaja", "Letkupaja"),
    "err.no_doc": ("Avaa tai luo ensin dokumentti.", "Open or create a document first."),
    "err.need_ports": ("Valitse molemmat liitännät (A ja B).", "Pick both ports (A and B)."),
    "err.bad_pick": ("Valinta ei kelpaa liitännäksi. Valitse ympyrän muotoinen reuna, lieriöpinta tai pyöreä tasopinta.", "The selection is not a valid port. Select a circular edge, a cylindrical face or a round planar face."),
    "err.no_selection": ("Mitään ei ole valittu.", "Nothing is selected."),
    "err.route": ("Reititys epäonnistui: {e}", "Routing failed: {e}"),
    "err.config": ("Asennelma ei kelpaa: {e}", "Invalid assembly: {e}"),
    "err.link": ("Linkkiä ei voitu lukea: {e}", "Could not read the link: {e}"),
    "err.cert": ("Yhteys letkupaja.fi:hin estyi, koska varmennetta ei voitu tarkistaa ({e}). Yrityksen verkossa välityspalvelin tai palomuuri voi tarkastaa HTTPS-liikenteen: pyydä IT-tukea lisäämään sen varmenne tai sallimaan letkupaja.fi. Ohje: https://letkupaja.fi/cad-lisaosat#ongelmia. Reititys ja tilauslinkki toimivat ilman yhteyttä.", "Could not connect to letkupaja.fi because the certificate could not be verified ({e}). On a company network a proxy or firewall may inspect HTTPS traffic: ask your IT support to add its certificate or to allow letkupaja.fi. Help: https://letkupaja.fi/cad-lisaosat#ongelmia. Routing and the order link work without a connection."),
    "err.network": ("Yhteys letkupaja.fi:hin epäonnistui: {e}", "Could not connect to letkupaja.fi: {e}"),
    "err.retry": (" Yritä uudelleen {s} s kuluttua.", " Try again in {s} s."),
    "err.unsafe_url": ("Osoitetta ei avattu, koska se ei ole letkupaja.fi:n https-osoite:\n{url}", "The address was not opened because it is not an https address on letkupaja.fi:\n{url}"),
    "err.panel_busy": ("Sulje ensin avoin tehtävä (esim. luonnoksen muokkaus) ja yritä uudelleen.", "Close the open task (e.g. a sketch edit) first and try again."),
    "err.detailed_parts": ("Ladatusta mallista ei tunnistettu kaikkia liittimen osia ({missing}). Mallia ei sijoitettu eikä yksinkertaistettuja liittimiä piilotettu. Tiedosto on tallessa: {path}\nOta yhteyttä Letkupajaan, jos virhe toistuu.", "Not all fitting parts were recognised in the downloaded model ({missing}). The model was not placed and the simplified fittings were kept. The file is saved: {path}\nPlease contact Letkupaja if this happens again."),
    "err.server": ("letkupaja.fi vastasi virheellä {status}: {e}", "letkupaja.fi returned error {status}: {e}"),
    "err.no_hose": ("Valitse ensin Letkupaja-letku (ryhmä mallipuussa).", "Select a Letkupaja hose (group in the tree) first."),
    "err.no_link": ("Letkulla ei ole vielä tilauslinkkiä. Reititä letku ensin.", "The hose has no order link yet. Route it first."),
    "err.step_import": ("STEP-tiedoston tuonti epäonnistui: {e}", "STEP import failed: {e}"),
    "err.python": ("Letkupaja-lisäosa vaatii FreeCAD 1.0:n tai uudemman (Python 3.10+). Tämän FreeCADin Python on {v}.", "The Letkupaja add-on requires FreeCAD 1.0 or newer (Python 3.10+). This FreeCAD has Python {v}."),
    "warn.source_lost": ("Liitännän {k} alkuperäistä valintaa ei löytynyt; käytetään tallennettua sijaintia.", "The original selection of port {k} was not found; using the stored position."),
    "warn.sweep": ("Letkun pintamallin teko epäonnistui; tarkista reitti (keskilinja ja mitat ovat oikein).", "Building the hose surface failed; check the route (the centerline and lengths are correct)."),
    "warn.estimated": ("Huom: osa mitoista on arvioita.", "Note: some dimensions are estimates."),
    "warn.protection": ("Suojan pintamallin teko epäonnistui; suoja näytetään yksinkertaistettuna.", "Building the protection surface failed; it is shown simplified."),
    "warn.orientation_unknown": ("Kiertokulmaa ei voitu laskea (holkin suunta puuttuu). Määritä kiertokulma Letkukoneessa; tilauslinkin {deg}° ei ole laskettu reitistä.", "The orientation could not be computed (ferrule direction unknown). Set the orientation in the configurator; the {deg}° in the order link is not computed from the route."),
    "warn.detailed_removed": ("Tarkka malli poistettiin, koska asennelma muuttui – lataa se uudelleen.", "The detailed model was removed because the assembly changed – download it again."),
    "imported.found": ("Tuotu {n} osaa. Holkit tunnistettu: {k}.", "Imported {n} parts. Ferrules detected: {k}."),
    # kirjautuminen ja lataus
    "login.title": ("Kirjaudu Letkupajaan", "Sign in to Letkupaja"),
    "login.intro": ("Lisäosa yhdistetään Letkupaja-tiliisi selaimessa. Tarkista, että selaimessa näkyy sama koodi. Tunniste tallennetaan vain tälle koneelle omaan tiedostoonsa (vain sinun luettavissasi).", "The add-on is connected to your Letkupaja account in the browser. Check that the browser shows the same code. The token is stored only on this computer in its own file (readable only by you)."),
    "login.code": ("Avaa {url} ja syötä koodi:", "Open {url} and enter the code:"),
    "login.open": ("Avaa selain", "Open browser"),
    "login.waiting": ("Odotetaan vahvistusta selaimessa…", "Waiting for confirmation in the browser…"),
    "login.ok": ("Kirjautuminen onnistui.", "Signed in."),
    "login.expired": ("Koodi vanheni tai kirjautuminen peruttiin. Yritä uudelleen.", "The code expired or the sign-in was cancelled. Please try again."),
    "login.required": ("Tarkan mallin lataaminen vaatii kirjautumisen Letkupaja-tilillesi.", "Downloading the detailed model requires signing in to your Letkupaja account."),
    "login.denied": ("Kirjautuminen hylättiin selaimessa.", "The sign-in was declined in the browser."),
    "login.cancelled": ("Kirjautuminen peruttiin; tarkkaa mallia ei ladattu.", "Sign-in cancelled; the detailed model was not downloaded."),
    "login.failed": ("Kirjautuminen ei onnistunut: palvelin ei hyväksynyt tunnistetta. Kirjaudu uudelleen.", "Sign-in failed: the server did not accept the token. Please sign in again."),
    "login.free": ("Tarkat mallit ovat tilillesi maksuttomia.", "Detailed models are free for your account."),
    "login.expires": ("Kirjautuminen voimassa {d} asti.", "Signed in until {d}."),
    "login.manage": ("Laitteet ja kirjautumiset: {url}", "Devices and sign-ins: {url}"),
    "login.token_where": ("Tunniste: {path}", "Token file: {path}"),
    "logout.ok": ("Kirjauduttu ulos: tunniste peruttiin palvelimella ja poistettiin tältä koneelta.", "Signed out: the token was revoked on the server and removed from this computer."),
    "logout.partial": ("Tunniste poistettiin tältä koneelta, mutta sen peruminen palvelimella ei onnistunut ({e}). Peru laite tililläsi: {url}", "The token was removed from this computer, but revoking it on the server failed ({e}). Revoke the device in your account: {url}"),
    "login.signed_in": ("Kirjautunut: {who}", "Signed in: {who}"),
    "login.not_signed_in": ("Ei kirjautunut.", "Not signed in."),
    "buy.title": ("Tarkka malli", "Detailed model"),
    "buy.text": ("Tarkka liitinmalli ei sisälly tiliisi tälle asennelmalle. Yksinkertaistetut liittimet (ulkomitat) ovat aina ilmaisia.", "The detailed fitting model is not included in your account for this assembly. Simplified fittings (outer dimensions) are always free."),
    "buy.button": ("Osta tarkka malli ({price}, hyvitetään letkutilauksessa)", "Buy detailed model ({price}, credited on hose order)"),
    "buy.price": ("3,77 € sis. alv", "€3.77 incl. VAT"),
    "buy.informative": ("Malli ostetaan Letkukoneessa, ja hinta hyvitetään letkutilauksessa. Oston jälkeen palaa FreeCADiin ja paina uudelleen Lataa tarkka malli.", "The model is bought in the configurator and the price is credited on your hose order. After buying, return to FreeCAD and press Download detailed model again."),
    "buy.ordered_note": ("Tilaamasi tai ostamasi asennelman tarkka malli on maksuton millä tahansa pituudella ja kiertokulmalla, kun letkutyyppi, koko, liittimet (kierre, kulma, banjon pultti ja tiivisteet) ja suoja ovat samat; päät saavat olla toisin päin. Jos olet tilannut tämän letkun, tarkista, että lisäosa on kirjautunut samalla sähköpostilla kuin tilaus ja että tilaus on maksettu tai vahvistettu.", "The detailed model of an assembly you have ordered or bought is free at any length and orientation, as long as the hose type, size, fittings (thread, angle, banjo bolt and seals) and protection are the same; the ends may be the other way round. If you have ordered this hose, check that the add-on is signed in with the same email as the order and that the order has been paid or confirmed."),
    "buy.unpaid": ("Tilauksesi {ref} verkkomaksu jäi kesken. Jos jatkat sen maksamista tilaussivulla (Jatka maksamiseen), tilauksen tarkka malli on maksuton, eikä sitä tarvitse ostaa erikseen.", "The online payment of your order {ref} was not completed. If you continue the payment on the order page (Continue to payment), the order's detailed model is free and you do not need to buy it separately."),
    "buy.unpaid_noref": ("Tämän letkun tilauksen verkkomaksu jäi kesken. Jos jatkat sen maksamista tilaussivulla (Jatka maksamiseen), tilauksen tarkka malli on maksuton, eikä sitä tarvitse ostaa erikseen.", "The online payment of your order for this hose was not completed. If you continue the payment on the order page (Continue to payment), the order's detailed model is free and you do not need to buy it separately."),
    "pay.button": ("Jatka maksamiseen", "Continue to payment"),
    "pending.title": ("Tilaus odottaa", "Order pending"),
    "dl.ok": ("Tarkka malli ladattu ja sijoitettu: {path}", "Detailed model downloaded and placed: {path}"),
    "dl.saved": ("Tallennettu: {path}", "Saved: {path}"),
    "dl.busy": ("Ladataan tarkkaa mallia letkupaja.fi:stä…", "Downloading the detailed model from letkupaja.fi…"),
    "dl.cancelled": ("Peruttu.", "Cancelled."),
    "net.busy": ("Yhdistetään letkupaja.fi:hin…", "Connecting to letkupaja.fi…"),
    # päivitykset
    "upd.title": ("Päivitykset", "Updates"),
    "upd.current": ("Lisäosa on ajan tasalla (versio {v}).", "The add-on is up to date (version {v})."),
    "upd.available": ("Uusi versio {new} on saatavilla (nykyinen {v}).", "A new version {new} is available (current {v})."),
    "upd.download": ("Lataa päivitys", "Download update"),
    "upd.addon_manager": (
        "Lisäosa on asennettu FreeCADin Addon Managerilla: päivitä se kohdassa Työkalut › Addon Manager (Letkupaja › Päivitä) ja käynnistä FreeCAD uudelleen.",
        "The add-on was installed with FreeCAD's Addon Manager: update it in Tools › Addon Manager (Letkupaja › Update) and restart FreeCAD.",
    ),
    "upd.open_addon_manager": ("Avaa Addon Manager", "Open Addon Manager"),
    "upd.zip_or_addon_manager": (
        "Lataa uusi zip-paketti letkupaja.fi:stä ja asenna se samalla asennusmakrolla. Jos asensit lisäosan FreeCADin Addon Managerilla, päivitä se siellä (Työkalut › Addon Manager).",
        "Download the new zip package from letkupaja.fi and install it with the same installer macro. If you installed the add-on with FreeCAD's Addon Manager, update it there (Tools › Addon Manager).",
    ),
    "upd.geometry_new": ("Mittadata päivitetty: {d} (oli {old}).", "Dimension data updated: {d} (was {old})."),
    "upd.geometry_same": ("Mittadata on ajan tasalla ({d}).", "Dimension data is up to date ({d})."),
    "upd.unknown": ("Palvelimen versiotietoa ei voitu tulkita.", "Could not interpret the server's version information."),
    "upd.unavailable": ("Päivitystietoja ei ole juuri nyt saatavilla letkupaja.fi:stä (HTTP {status}). Lisäosa toimii normaalisti omalla mittadatallaan.", "Update information is not available from letkupaja.fi right now (HTTP {status}). The add-on works normally with its own dimension data."),
    "upd.minimum": ("Tämä versio ({v}) on vanhempi kuin palvelimen vaatima {min}: tarkkojen mallien lataus ei ehkä toimi. Päivitä lisäosa.", "This version ({v}) is older than the minimum {min} required by the server: detailed model downloads may not work. Please update the add-on."),
    "upd.geometry_fix": ("Mittadataan on tullut korjaus ({d}); se on nyt käytössä.", "A correction to the dimension data ({d}) is now in use."),
    "upd.routing": ("Reititetään letkua {i}/{n}: {label}", "Routing hose {i}/{n}: {label}"),
    "upd.cancelled": ("Päivitys peruttiin; loput letkut jäivät ennalleen.", "Update cancelled; the remaining hoses were not changed."),
    # tietoja
    "about.title": ("Tietoja Letkupaja-lisäosasta", "About the Letkupaja add-on"),
    "about.text": (
        "Letkupaja FreeCAD -lisäosa {v}\nMittadata {g}\n\nLaitehuolto Tiensuu Oy – letkupaja.fi\n\nTietosuoja: lisäosa ei lähetä käyttötietoja. Verkkoyhteys avataan vain, kun itse painat painiketta (päivitystarkistus, kirjautuminen, uloskirjautuminen tai tarkan mallin lataus). Pyynnöissä on lisäosan versio (User-Agent) ja palvelin näkee IP-osoitteesi; kirjautuessa laitteen nimeksi tallentuu FreeCADin versio ja käyttöjärjestelmä (ei koneen eikä käyttäjän nimeä). Tarkkaa mallia pyydettäessä lähetetään vain asennelman valinnat, ei mallisi geometriaa. Tilauslinkki sisältää vain valitsemasi asennelman (letku, liittimet, pituus, kiertokulma).\n\nKäyttöehdot: {terms}\nTietosuoja: {privacy}\nLaitteet ja kirjautumiset: {manage}",
        "Letkupaja FreeCAD add-on {v}\nDimension data {g}\n\nLaitehuolto Tiensuu Oy – letkupaja.fi\n\nPrivacy: the add-on sends no usage data. A network connection is made only when you press a button (update check, sign-in, sign-out or detailed model download). Requests include the add-on version (User-Agent) and the server sees your IP address; at sign-in the device name is your FreeCAD version and operating system (no computer or user name). A detailed model request sends only the assembly choices, never your model geometry. The order link contains only the assembly you chose (hose, fittings, length, orientation).\n\nTerms of use: {terms}\nPrivacy: {privacy}\nDevices and sign-ins: {manage}",
    ),
}


def tr(key: str, **kw: object) -> str:
    """Teksti nykyisellä kielellä; {nimi}-kentät täytetään."""
    pair = STRINGS.get(key)
    text = key if pair is None else (pair[1] if lang() == "en" else pair[0])
    return text.format(**kw) if kw else text


# Ytimen (letkuroute) suomenkieliset varoitukset ja virheet -> englanti
_CORE_EN: list[tuple[str, str]] = [
    (r"^Pienin taivutussäde (\S+) mm on alle sallitun (\S+) mm \(kohta (\S+) mm A-päästä\)\.$", r"Minimum bend radius \1 mm is below the allowed \2 mm (at \3 mm from end A)."),
    (r"^Letku on liian lähellä estettä: pienin vara (\S+) mm, vaadittu (\S+) mm \(kohta (\S+) mm A-päästä\)\.$", r"The hose is too close to an obstacle: minimum clearance \1 mm, required \2 mm (at \3 mm from end A)."),
    (r"^Letku on liian lähellä toista letkua: pienin vara (\S+) mm, vaadittu (\S+) mm\.$", r"The hose is too close to another hose: minimum clearance \1 mm, required \2 mm."),
    (r"^Tavoitepituus on lyhyempi kuin suora etäisyys holkkien välillä \((\S+) mm liian lyhyt\)\.$", r"The target length is shorter than the straight distance between the ferrules (\1 mm too short)."),
    (r"^Tavoitepituutta ei saavutettu\.$", "The target length was not reached."),
    (r"^Letkun mitat tai taivutussäde ovat osin arvioita; tarkista ennen tilausta\.$", "Some hose dimensions or the bend radius are estimates; check before ordering."),
    (r"^([AB])-pään mitat ovat arvioita \(liitintä ei löydy mittadatasta\)\.$", r"End \1 dimensions are estimates (fitting not found in the dimension data)."),
    (r"^Katkaisumitta on liian lyhyt\.$", "The cut length is too short."),
    (r"^Katkaisumitta (\S+) mm on liian lyhyt holkeille \(vähintään (\S+) mm\)\.$", r"Cut length \1 mm is too short for the ferrules (at least \2 mm)."),
    (r"^Tavoitepituus on lyhyempi kuin letkun osuus holkkien sisällä$", "The target length is shorter than the hose length inside the ferrules"),
    (r"^Anna vain yksi tavoite.*$", "Give only one target length."),
    (r"^Nollavektoria ei voi normeerata \(suunta puuttuu\)$", "A direction vector is zero (direction missing)."),
    (r"^Virheellinen asennelma: (.*)$", r"Invalid assembly: \1"),
    (r"^hose: tuntematon letkutyyppi (.*)$", r"hose: unknown hose type \1"),
    (r"^size: tuntematon koko (.*)$", r"size: unknown size \1"),
    (r"^lengthMm: kokonaisluku 1\.\.\.100000$", "lengthMm: integer 1...100000"),
    (r"^orientation: kokonaisluku 0\.\.\.359$", "orientation: integer 0...359"),
    (r"^([ab])\.angle: 0, 45 tai 90$", r"\1.angle: 0, 45 or 90"),
    (r"^([ab]): puuttuu$", r"\1: missing"),
    (r"^Linkissä ei ole asennelma-parametria$", "The link has no asennelma parameter"),
    (r"^Linkin asennelma on liian pitkä.*$", "The assembly in the link is too long"),
]


def core_message(text: str) -> str:
    """Ytimen suomenkielinen viesti nykyisellä kielellä."""
    if lang() != "en":
        return text
    for part_sep in ("; ",):
        if part_sep in text and text.startswith("Virheellinen asennelma: "):
            head, rest = text.split(": ", 1)
            return "Invalid assembly: " + "; ".join(core_message(p) for p in rest.split(part_sep))
    for pat, rep in _CORE_EN:
        if re.match(pat, text):
            return re.sub(pat, rep, text)
    return text
