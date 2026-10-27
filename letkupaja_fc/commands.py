# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""FreeCAD-komennot (työkalurivi ja Letkupaja-valikko)."""
from __future__ import annotations

import os
import traceback
from typing import Any

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore

from . import api, channel
from . import model as M
from . import routing as R
from .core import L, apply_geometry_override, cached_geometry_etag, should_accept_geometry, store_geometry_cache
from .gui_util import Cancelled, QtWidgets, ask, choose, copy_text, icon, log, main_window, message, open_url, run_async, status, wait_cursor
from .i18n import api_error_text, core_message, lang, tr
from .settings import Settings
from .version import VERSION

TERMS_URL = "https://letkupaja.fi/ehdot#cad-lisaosat"
PRIVACY_URL = "https://letkupaja.fi/tietosuoja#cad-lisaosat"
HELP_URL = "https://letkupaja.fi/cad-lisaosat#freecad"
MANAGE_URL = "https://letkupaja.fi/tili#cad-lisaosat"
PLUGINS_URL = "https://letkupaja.fi/cad-lisaosat"


def _doc() -> Any:
    return App.ActiveDocument


def selected_hose_groups() -> list[Any]:
    out = []
    for o in Gui.Selection.getSelection():
        g = M.owning_hose_group(o)
        if g is not None and g not in out:
            out.append(g)
    return out


def _single_hose_group() -> Any:
    sel = selected_hose_groups()
    if sel:
        return sel[0]
    doc = _doc()
    groups = M.hose_groups(doc) if doc else []
    return groups[0] if len(groups) == 1 else None


# ---------------------------------------------------------------------------------------------- tilaus


def confirm_order(problems: list[str], yes_key: str) -> bool:
    """Reitissä ongelmia (säde, estevara, tavoite, arvioidut mitat, kiertokulma): kysy ennen tilauksen avaamista."""
    if not problems:
        return True
    text = tr("order.confirm", w="\n".join("• " + p for p in problems[:8]))
    return ask(text, tr(yes_key), title=tr("cmd.order"))


# ---------------------------------------------------------------------------------------------- tarkka malli


def offer_purchase(group: Any, err: api.PaymentRequired | None = None) -> None:
    """402: tarjoa tarkan mallin ostoa Letkukoneessa (palvelimen teksti ja hinta, jos saatavilla) ja kerro sääntö:
    tilattu tai ostettu malli kelpaa millä tahansa pituudella ja kiertokulmalla, kun letku, koko, liittimet ja suoja
    ovat samat (palvelin antaa sen silloin suoraan, joten 402 tarkoittaa eri liittimiä, eri tiliä tai maksamatta jäänyttä
    tilausta). Maksamatta jäänyt tilaus (unpaid.payUrl): ostopainikkeen lisäksi Jatka maksamiseen (tilaussivu), koska
    maksetun tilauksen malli on maksuton. Rajapinnan owned-lista on aina tyhjä, eikä sitä tarvita."""
    buy_url = (err.buy_url if err is not None else None) or M.safe_order_link(group) or PLUGINS_URL
    label = (err.buy_label(lang()) if err is not None else None) or tr("buy.button", price=tr("buy.price"))
    text = err.text(lang()) if err is not None and err.message and err.message != "payment required" else tr("buy.text")
    info = [tr("buy.informative")]
    if group.OrderConfig:
        info.append(tr("buy.ordered_note"))
    pay_url = err.unpaid_url if err is not None else None
    if pay_url:
        ref = err.unpaid_ref()
        info.insert(0, tr("buy.unpaid", ref=ref) if ref else tr("buy.unpaid_noref"))
        pick = choose(text, [label, err.unpaid_label(lang()) or tr("pay.button")], title=tr("buy.title"), informative="\n\n".join(info))
        if pick == 0:
            open_url(buy_url)
        elif pick == 1:
            open_url(pay_url)
        return
    if ask(text, label, title=tr("buy.title"), informative="\n\n".join(info)):
        open_url(buy_url)


def show_pending(err: api.OrderPending) -> None:
    """409 order_pending: tilattu ja tilaus kesken (verkkomaksu juuri aloitettu tai laskutilaus odottaa tarkistusta).
    Ei ostoa (sama malli maksettaisiin kahdesti); verkkomaksulle Jatka maksamiseen (payUrl, tilaussivu)."""
    text = err.text(lang())
    if err.pay_url:
        if ask(text, err.pay_label(lang()) or tr("pay.button"), title=tr("pending.title")):
            open_url(err.pay_url)
    else:
        message(text, kind="info", title=tr("pending.title"))


def download_detailed(group: Any) -> bool:
    """Lataa tarkka STEP-malli letkupaja.fi:stä (kirjautuneena) ja sijoita se reititettyyn letkuun."""
    from .login import ensure_login

    cfg = M.order_config(group)
    if not cfg:
        message(tr("err.no_link"), kind="warning")
        return False
    s = Settings()
    if not s.token and not ensure_login(s):
        status(tr("login.cancelled"), 8000)
        return False
    data = None
    for attempt in range(2):
        client = api.Client(s.api_base, s.token, timeout=120)
        try:
            data = run_async(lambda c=client: c.step(cfg), tr("dl.busy"))  # 402 -> PaymentRequired, 409 -> OrderPending
            break
        except Cancelled:
            status(tr("dl.cancelled"), 8000)
            return False
        except api.AuthRequired:
            s.clear_token()
            if attempt == 0:
                message(tr("login.required"), kind="info")
                if ensure_login(s):
                    continue
                status(tr("login.cancelled"), 8000)
            else:
                message(tr("login.failed"), kind="warning")
            return False
        except api.PaymentRequired as e:
            offer_purchase(group, e)
            return False
        except api.OrderPending as e:
            show_pending(e)  # tilattu, tilaus kesken: ei ostoa, verkkomaksulle Jatka maksamiseen
            return False
        except api.ApiError as e:
            message(api_error_text(e), kind="warning")
            return False
    if data is None:
        return False
    path = M.default_step_path(group, cfg)
    with open(path, "wb") as f:
        f.write(data)
    doc = group.Document
    try:
        with wait_cursor():
            doc.openTransaction(tr("cmd.detailed"))
            M.place_detailed_step(group, path)
            doc.commitTransaction()
    except M.DetailedPlacementError as e:
        doc.abortTransaction()
        log(traceback.format_exc())
        message(tr("err.detailed_parts", missing=str(e), path=path), kind="warning")
        return False
    except Exception as e:  # noqa: BLE001
        doc.abortTransaction()
        log(traceback.format_exc())
        message(tr("err.step_import", e=str(e)) + "\n" + tr("dl.saved", path=path), kind="warning")
        return False
    status(tr("dl.ok", path=path), 10000)
    log(tr("dl.ok", path=path))
    return True


# ---------------------------------------------------------------------------------------------- komennot


class _Cmd:
    key = ""
    icon_file = "Letkupaja.svg"

    def GetResources(self) -> dict[str, str]:  # noqa: N802
        return {"MenuText": tr(f"cmd.{self.key}"), "ToolTip": tr(f"cmd.{self.key}.tip"), "Pixmap": icon(self.icon_file)}

    def IsActive(self) -> bool:  # noqa: N802
        return True


class RouteCmd(_Cmd):
    key = "route"
    icon_file = "Route.svg"

    def IsActive(self) -> bool:  # noqa: N802
        return _doc() is not None

    def Activated(self) -> None:  # noqa: N802
        from .task_route import open_panel

        sel = selected_hose_groups()
        open_panel(sel[0] if sel else None)


def update_groups(groups: list[Any]) -> list[str]:
    """Laske letkut uudelleen: dokumentti luetaan ja kirjoitetaan pääsäikeessä, reititys taustasäikeessä
    (edistymisikkuna, Peruuta lopettaa nykyisen letkun jälkeen). Palauttaa ongelmat."""
    problems = []
    for i, g in enumerate(groups, 1):
        try:
            req, notes = M.prepare_request(g)
            result = run_async(lambda r=req: R.run_route(r), tr("upd.routing", i=i, n=len(groups), label=g.Label))
            s = M.apply_result(g, req, result, notes)
            if not s["ok"]:
                problems.append(f"{g.Label}: " + "; ".join(s["warnings"]))
        except Cancelled:
            problems.append(tr("upd.cancelled"))
            break
        except Exception as e:  # noqa: BLE001
            problems.append(f"{g.Label}: {core_message(str(e))}")
            log(traceback.format_exc())
    return problems


class UpdateCmd(_Cmd):
    key = "update"
    icon_file = "Refresh.svg"

    def IsActive(self) -> bool:  # noqa: N802
        d = _doc()
        return d is not None and bool(M.hose_groups(d))

    def Activated(self) -> None:  # noqa: N802
        doc = _doc()
        groups = selected_hose_groups() or M.hose_groups(doc)
        doc.openTransaction(tr("cmd.update"))
        try:
            problems = update_groups(groups)
        finally:
            doc.commitTransaction()
        if problems:
            message("\n".join(problems), kind="warning")
        else:
            status(tr("res.ok"))


class ImportStepCmd(_Cmd):
    key = "import"
    icon_file = "ImportStep.svg"

    def Activated(self) -> None:  # noqa: N802
        path, _f = QtWidgets.QFileDialog.getOpenFileName(main_window(), tr("cmd.import"), "", "STEP (*.step *.stp *.STEP *.STP)")
        if not path:
            return
        doc = _doc() or App.newDocument()
        try:
            with wait_cursor():
                doc.openTransaction(tr("cmd.import"))
                roots, ports = M.import_step(doc, path, f"{tr('obj.imported')} {os.path.basename(path)}")
                doc.commitTransaction()
        except Exception as e:  # noqa: BLE001
            doc.abortTransaction()
            log(traceback.format_exc())
            message(tr("err.step_import", e=str(e)), kind="warning")
            return
        Gui.SendMsgToActiveView("ViewFit")
        message(tr("imported.found", n=len(roots), k=", ".join(sorted(ports)) or "–"))


class DetailedCmd(_Cmd):
    key = "detailed"
    icon_file = "Download.svg"

    def IsActive(self) -> bool:  # noqa: N802
        d = _doc()
        return d is not None and bool(M.hose_groups(d))

    def Activated(self) -> None:  # noqa: N802
        g = _single_hose_group()
        if g is None:
            message(tr("err.no_hose"), kind="warning")
            return
        download_detailed(g)


class OrderCmd(_Cmd):
    key = "order"
    icon_file = "Order.svg"

    def IsActive(self) -> bool:  # noqa: N802
        d = _doc()
        return d is not None and bool(M.hose_groups(d))

    def Activated(self) -> None:  # noqa: N802
        g = _single_hose_group()
        if g is None:
            message(tr("err.no_hose"), kind="warning")
            return
        link = M.safe_order_link(g)
        if not link:
            message(tr("err.no_link"), kind="warning")
            return
        if not confirm_order(M.route_problems(g), "order.anyway.open"):
            return
        copy_text(link)
        open_url(link)
        status(tr("res.link.copied"))


class LoginCmd(_Cmd):
    key = "login"
    icon_file = "Account.svg"

    def Activated(self) -> None:  # noqa: N802
        from .gui_util import exec_dialog
        from .login import DeviceLoginDialog

        s = Settings()
        if not s.token and not exec_dialog(DeviceLoginDialog(s)):
            return
        message(_account_text(s), title=tr("cmd.login"))


def _account_text(s: Settings) -> str:
    from .login import account_text

    if not s.token:
        return account_text(s)
    client = api.Client(s.api_base, s.token, timeout=10)
    try:
        info = run_async(client.entitlements, tr("net.busy"))
        return account_text(s, info=info)
    except Cancelled:
        return account_text(s, error=api.NetworkError("cancelled"))
    except api.ApiError as e:
        return account_text(s, error=e)


def logout(s: Settings | None = None) -> tuple[bool, str]:
    """Peru tunniste palvelimella (paras yritys) ja poista se tältä koneelta. (peruttiinko palvelimella, virhe)."""
    s = s or Settings()
    token = s.token
    revoked, err = False, ""
    if token:
        client = api.Client(s.api_base, token, timeout=10)
        try:
            revoked = bool(run_async(client.logout, tr("net.busy")))
        except Cancelled:
            err = tr("dl.cancelled")
        except api.ApiError as e:
            err = api_error_text(e)
    s.clear_token()
    return revoked, err


class LogoutCmd(_Cmd):
    key = "logout"
    icon_file = "Account.svg"

    def IsActive(self) -> bool:  # noqa: N802
        return bool(Settings().token)

    def Activated(self) -> None:  # noqa: N802
        revoked, err = logout()
        if revoked:
            message(tr("logout.ok"))
        else:
            message(tr("logout.partial", e=err or "–", url=MANAGE_URL), kind="warning")


def check_updates(settings: Settings | None = None, client: api.Client | None = None) -> dict[str, Any]:
    """Versiotarkistus ja mittadatan päivitys (käyttöliittymästä riippumaton osa; ajetaan taustasäikeessä).
    Palauttaa tulokset; välimuisti kirjoitetaan, mutta otetaan käyttöön vasta pääsäikeessä (apply_geometry_override)."""
    s = settings or Settings()
    c = client or api.Client(s.api_base, timeout=20)
    out: dict[str, Any] = {"current": VERSION}
    info = api.parse_version_info(c.version_info())
    out["info"] = info
    if info:
        out["newer"] = api.compare_versions(info["latest"], VERSION) > 0
        out["below_minimum"] = bool(info.get("minimum")) and api.compare_versions(VERSION, info["minimum"]) < 0
        out["url"] = api.safe_site_url(info.get("url"), s.api_base) or api.safe_site_url(info.get("download"), s.api_base)
    out["geometry_old"] = L.geometry_version()
    cache = s.geometry_cache()
    try:
        geo, etag = c.geometry(cached_geometry_etag(cache))
    except api.ApiError:
        geo, etag = None, None
    out["geometry_update"] = False
    if geo is not None and should_accept_geometry(geo, L.geometry.geo()):
        store_geometry_cache(cache, geo, etag)
        out["geometry_update"] = True
    return out


class UpdatesCmd(_Cmd):
    key = "updates"
    icon_file = "Update.svg"

    def Activated(self) -> None:  # noqa: N802
        s = Settings()
        try:
            r = run_async(lambda: check_updates(s), tr("net.busy"))
        except Cancelled:
            return
        except api.NetworkError as e:
            message(api_error_text(e), kind="warning")
            return
        except api.ApiError as e:
            if e.status in (404, 501, 502, 503):
                message(tr("upd.unavailable", status=e.status), title=tr("upd.title"))
            else:
                message(api_error_text(e), kind="warning")
            return
        geometry_new = apply_geometry_override(s.geometry_cache()) if r.get("geometry_update") else None
        lines = []
        info = r.get("info")
        if not info:
            lines.append(tr("upd.unknown"))
        elif r.get("newer"):
            lines.append(tr("upd.available", new=info["latest"], v=VERSION))
            notes = info.get("notes_en") if lang() == "en" and info.get("notes_en") else info.get("notes")
            if notes:
                lines.append(str(notes)[:1000])
        else:
            lines.append(tr("upd.current", v=VERSION))
        if r.get("below_minimum"):
            lines.append(tr("upd.minimum", v=VERSION, min=info["minimum"]))
        if geometry_new and geometry_new != r.get("geometry_old"):
            lines.append(tr("upd.geometry_new", d=geometry_new, old=r["geometry_old"]))
        elif geometry_new:
            lines.append(tr("upd.geometry_fix", d=geometry_new))
        else:
            lines.append(tr("upd.geometry_same", d=L.geometry_version()))
        if (r.get("newer") or r.get("below_minimum")) and channel.installed_with_addon_manager():
            # Addon Managerilla asennettu (julkinen repo): päivitys Addon Managerissa, ei lataussivulta
            lines.append(tr("upd.addon_manager"))
            if ask("\n\n".join(lines), tr("upd.open_addon_manager"), title=tr("upd.title")):
                open_addon_manager()
            return
        if r.get("newer") or r.get("below_minimum"):
            lines.append(tr("upd.zip_or_addon_manager"))
        text = "\n\n".join(lines)
        if r.get("newer") or r.get("below_minimum"):
            url = r.get("url") or PLUGINS_URL
            if ask(text, tr("upd.download"), title=tr("upd.title")):
                open_url(url)
        else:
            message(text, title=tr("upd.title"))


def open_addon_manager() -> None:
    """Avaa FreeCADin Addon Manager (Tools › Addon Manager), josta lisäosa päivitetään."""
    try:
        Gui.runCommand("Std_AddonMgr")
    except Exception as e:  # noqa: BLE001 - Addon Manager puuttuu tai on poistettu käytöstä
        log(f"Addon Manager: {e}")
        message(tr("upd.addon_manager"), title=tr("upd.title"))


class AboutCmd(_Cmd):
    key = "about"
    icon_file = "Letkupaja.svg"

    def Activated(self) -> None:  # noqa: N802
        message(tr("about.text", v=VERSION, g=L.geometry_version(), terms=TERMS_URL, privacy=PRIVACY_URL, manage=MANAGE_URL), title=tr("about.title"))


class HelpCmd(_Cmd):
    key = "help"
    icon_file = "Letkupaja.svg"

    def Activated(self) -> None:  # noqa: N802
        open_url(HELP_URL)


COMMANDS = {
    "Letkupaja_Route": RouteCmd,
    "Letkupaja_Update": UpdateCmd,
    "Letkupaja_ImportStep": ImportStepCmd,
    "Letkupaja_Detailed": DetailedCmd,
    "Letkupaja_Order": OrderCmd,
    "Letkupaja_Login": LoginCmd,
    "Letkupaja_Logout": LogoutCmd,
    "Letkupaja_CheckUpdates": UpdatesCmd,
    "Letkupaja_Help": HelpCmd,
    "Letkupaja_About": AboutCmd,
}
TOOLBAR = ["Letkupaja_Route", "Letkupaja_Update", "Letkupaja_Order", "Letkupaja_ImportStep", "Letkupaja_Detailed"]
MENU = ["Letkupaja_Route", "Letkupaja_Update", "Letkupaja_Order", "Separator", "Letkupaja_ImportStep", "Letkupaja_Detailed", "Separator", "Letkupaja_Login", "Letkupaja_Logout", "Letkupaja_CheckUpdates", "Separator", "Letkupaja_Help", "Letkupaja_About"]


def register() -> None:
    s = Settings()
    apply_geometry_override(s.geometry_cache())
    for name, cls in COMMANDS.items():
        Gui.addCommand(name, cls())
