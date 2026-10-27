# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Käyttöliittymän apufunktiot (FreeCADin PySide-kerros toimii sekä Qt5:llä että Qt6:lla).

* Viesti-ikkunat näyttävät tekstin aina pelkkänä tekstinä (dokumentista luettu nimi tai palvelimen viesti ei voi
  muuttua linkiksi tai muotoilluksi tekstiksi).
* open_url avaa vain https-osoitteita letkupaja.fi:ssä (tai asetetussa testipalvelimessa): ei file:-, UNC- eikä
  muiden ohjelmien osoitteita, vaikka osoite tulisi CAD-dokumentista tai palvelimelta.
* run_async ajaa verkkopyynnön tai laskennan taustasäikeessä edistymisikkunan (Peruuta) kanssa, jottei FreeCAD
  jäädy ("Ei vastaa").
"""
from __future__ import annotations

import contextlib
import os
import threading
import time
import webbrowser
from typing import Any, Callable, Iterator

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore
from PySide import QtCore, QtGui, QtWidgets  # type: ignore  # noqa: F401 - FreeCADin yhteensopivuuskerros

from .i18n import tr

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Resources", "icons")


class Cancelled(Exception):
    """Käyttäjä perui taustatehtävän."""


def icon(name: str) -> str:
    return os.path.join(ICON_DIR, name)


def main_window():
    return Gui.getMainWindow()


def _plain_box(text: str, title: str | None, kind: str) -> Any:
    icons = {"info": QtWidgets.QMessageBox.Information, "warning": QtWidgets.QMessageBox.Warning, "error": QtWidgets.QMessageBox.Critical, "question": QtWidgets.QMessageBox.Question}
    mb = QtWidgets.QMessageBox(main_window())
    mb.setIcon(icons.get(kind, QtWidgets.QMessageBox.Information))
    mb.setWindowTitle(title or tr("err.title"))
    mb.setTextFormat(QtCore.Qt.PlainText)
    mb.setText(text)
    return mb


def message(text: str, title: str | None = None, kind: str = "info") -> None:
    mb = _plain_box(text, title, kind)
    mb.addButton(QtWidgets.QMessageBox.Ok)
    exec_dialog(mb)


def ask(text: str, yes: str, title: str | None = None, informative: str = "") -> bool:
    """Kysymys omalla hyväksymispainikkeella ja Peruuta-painikkeella. True = hyväksytty."""
    mb = _plain_box(text, title, "question")
    if informative:
        mb.setInformativeText(informative)
    b_yes = mb.addButton(yes, QtWidgets.QMessageBox.AcceptRole)
    mb.addButton(QtWidgets.QMessageBox.Cancel)
    exec_dialog(mb)
    return mb.clickedButton() is b_yes


def choose(text: str, buttons: list[str], title: str | None = None, informative: str = "") -> int | None:
    """Kysymys usealla hyväksymispainikkeella (järjestyksessä, ensimmäinen oletus) ja Peruuta-painikkeella. Palauttaa
    valitun painikkeen indeksin tai None (peruttu)."""
    mb = _plain_box(text, title, "question")
    if informative:
        mb.setInformativeText(informative)
    added = [mb.addButton(b, QtWidgets.QMessageBox.AcceptRole) for b in buttons]
    mb.addButton(QtWidgets.QMessageBox.Cancel)
    if added:
        mb.setDefaultButton(added[0])
    exec_dialog(mb)
    clicked = mb.clickedButton()
    for i, b in enumerate(added):
        if clicked is b:
            return i
    return None


def checked_url(url: str) -> str | None:
    """Avattavaksi kelpaava osoite (https letkupaja.fi tai asetettu testipalvelin) tai None."""
    from .api import safe_site_url
    from .settings import Settings

    return safe_site_url(url, Settings().api_base)


def open_url(url: str) -> bool:
    """Avaa osoite järjestelmän selaimessa vain, jos se on letkupaja.fi:n https-osoite (tai testipalvelin)."""
    if not url:
        return False
    safe = checked_url(url)
    if safe is None:
        log("ei avattu: " + url[:200])
        message(tr("err.unsafe_url", url=url[:200]), kind="warning")
        return False
    ok = False
    try:
        ok = bool(QtGui.QDesktopServices.openUrl(QtCore.QUrl(safe)))
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        webbrowser.open(safe)
    return True


def copy_text(text: str) -> None:
    QtWidgets.QApplication.clipboard().setText(text)


@contextlib.contextmanager
def wait_cursor() -> Iterator[None]:
    QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
    try:
        yield
    finally:
        QtWidgets.QApplication.restoreOverrideCursor()


def run_async(fn: Callable[[], Any], text: str, cancellable: bool = True, show_after_ms: int = 250) -> Any:
    """Aja fn taustasäikeessä ja odota käsitellen käyttöliittymän tapahtumia (FreeCAD ei jäädy). Edistymisikkuna
    Peruuta-painikkeella tulee näkyviin, jos tehtävä kestää yli show_after_ms. Peruutus nostaa Cancelled-poikkeuksen
    (säie jatkaa taustalla, tulos jätetään käyttämättä). fn ei saa koskea Qt:hen eikä FreeCAD-dokumenttiin."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return fn()
    out: dict[str, Any] = {}

    def work() -> None:
        try:
            out["value"] = fn()
        except BaseException as e:  # noqa: BLE001
            out["error"] = e

    th = threading.Thread(target=work, daemon=True)
    th.start()
    dlg = QtWidgets.QProgressDialog(text, tr("btn.cancel") if cancellable else "", 0, 0, main_window())
    dlg.setWindowTitle(tr("err.title"))
    dlg.setWindowModality(QtCore.Qt.WindowModal)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)
    if not cancellable:
        dlg.setCancelButton(None)
    t0 = time.time()
    shown = False
    try:
        while th.is_alive():
            QtWidgets.QApplication.processEvents()
            th.join(0.03)
            if not shown and (time.time() - t0) * 1000 >= show_after_ms and th.is_alive():
                dlg.show()
                shown = True
            if shown and dlg.wasCanceled():
                raise Cancelled()
    finally:
        dlg.hide()
        dlg.deleteLater()
    if "error" in out:
        raise out["error"]
    return out.get("value")


def status(text: str, ms: int = 5000) -> None:
    try:
        main_window().statusBar().showMessage(text, ms)
    except Exception:  # noqa: BLE001
        pass


def log(text: str) -> None:
    """Viesti FreeCADin raporttinäkymään (ei koskaan tunnisteita)."""
    App.Console.PrintMessage("Letkupaja: " + text + "\n")


def link_label(url: str, text: str | None = None):
    """Napsautettava linkki (vain lisäosan omat letkupaja.fi-osoitteet; avataan open_url-tarkistuksen kautta)."""
    import html

    lab = QtWidgets.QLabel(f'<a href="{html.escape(url, quote=True)}">{html.escape(text or url)}</a>')
    lab.setTextFormat(QtCore.Qt.RichText)
    lab.setOpenExternalLinks(False)
    lab.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
    lab.linkActivated.connect(lambda u: open_url(u))
    return lab


def exec_dialog(dlg) -> int:
    return dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
