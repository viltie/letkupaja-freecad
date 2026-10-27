# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Laitekirjautuminen letkupaja.fi-tiliin (kuten television sovelluksissa): lisäosa näyttää koodin, asiakas
vahvistaa sen selaimessa ja lisäosa saa tunnisteen. Salasanaa ei syötetä FreeCADiin.

Verkkopyynnöt tehdään taustasäikeessä (ikkuna ei jäädy). Tunniste tallennetaan settings.Settings-luokan kautta
käyttäjän omaan tiedostoon (oikeudet 0600), ei FreeCADin user.cfg-tiedostoon eikä lokiin.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from . import api
from .gui_util import QtCore, QtWidgets, exec_dialog, main_window, open_url
from .i18n import api_error_text, lang, tr
from .settings import Settings

MANAGE_URL = "https://letkupaja.fi/tili#cad-lisaosat"


class _Bg:
    """Yksi taustapyyntö: tulos tarkistetaan QTimerilla pääsäikeessä."""

    def __init__(self, fn: Callable[[], Any], done: Callable[[dict[str, Any]], None], parent: Any):
        self.out: dict[str, Any] = {}
        self.done = done

        def work() -> None:
            try:
                self.out["value"] = fn()
            except BaseException as e:  # noqa: BLE001
                self.out["error"] = e

        self.thread = threading.Thread(target=work, daemon=True)
        self.timer = QtCore.QTimer(parent)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._check)
        self.thread.start()
        self.timer.start()

    def _check(self) -> None:
        if self.thread.is_alive():
            return
        self.timer.stop()
        self.done(self.out)

    def cancel(self) -> None:
        self.timer.stop()


class DeviceLoginDialog(QtWidgets.QDialog):
    def __init__(self, settings: Settings | None = None, parent=None):
        super().__init__(parent or main_window())
        self.settings = settings or Settings()
        self.client = api.Client(self.settings.api_base, timeout=20)
        self.start: api.DeviceStart | None = None
        self.deadline = 0.0
        self.interval = 5
        self.bg: _Bg | None = None
        self.closed = False
        self.setWindowTitle(tr("login.title"))
        self.setMinimumWidth(420)
        lay = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(tr("login.intro"))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.where = QtWidgets.QLabel("")
        self.where.setWordWrap(True)
        self.where.setTextFormat(QtCore.Qt.PlainText)
        self.where.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(self.where)
        self.code = QtWidgets.QLineEdit("")
        self.code.setReadOnly(True)
        self.code.setAlignment(QtCore.Qt.AlignCenter)
        f = self.code.font()
        f.setPointSize(f.pointSize() + 8)
        f.setBold(True)
        self.code.setFont(f)
        lay.addWidget(self.code)
        self.state = QtWidgets.QLabel(tr("net.busy"))
        self.state.setWordWrap(True)
        self.state.setTextFormat(QtCore.Qt.PlainText)
        lay.addWidget(self.state)
        btns = QtWidgets.QHBoxLayout()
        self.b_open = QtWidgets.QPushButton(tr("login.open"))
        self.b_open.clicked.connect(self.open_browser)
        self.b_open.setEnabled(False)
        btns.addWidget(self.b_open)
        b_cancel = QtWidgets.QPushButton(tr("btn.cancel"))
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_cancel)
        lay.addLayout(btns)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.poll)
        QtCore.QTimer.singleShot(0, self.begin)

    def begin(self) -> None:
        ui_lang = lang()  # vahvistussivu ja kirjautumiskoodin sähköposti samalla kielellä
        self.bg = _Bg(lambda: self.client.device_start(lang=ui_lang), self._started, self)

    def _started(self, out: dict[str, Any]) -> None:
        if self.closed:
            return
        e = out.get("error")
        if e is not None:
            self.state.setText(api_error_text(e) if isinstance(e, api.ApiError) else str(e))
            return
        self.start = out["value"]
        self.where.setText(tr("login.code", url=self.start.verification_uri))
        self.code.setText(self.start.user_code)
        self.state.setText(tr("login.waiting"))
        self.b_open.setEnabled(True)
        self.interval = self.start.interval
        self.deadline = time.time() + self.start.expires_in
        self.timer.start(self.interval * 1000)
        self.open_browser()

    def open_browser(self) -> None:
        if self.start:
            open_url(self.start.verification_uri_complete or self.start.verification_uri)

    def poll(self) -> None:
        if not self.start or self.closed:
            return
        if time.time() > self.deadline:
            self.state.setText(tr("login.expired"))
            return
        code = self.start.device_code
        self.bg = _Bg(lambda: self.client.device_poll(code), self._polled, self)

    def _polled(self, out: dict[str, Any]) -> None:
        if self.closed:
            return
        e = out.get("error")
        if isinstance(e, api.NetworkError):
            self.timer.start(self.interval * 1000)  # yritetään uudelleen seuraavalla kierroksella
            return
        if e is not None:
            self.state.setText(api_error_text(e) if isinstance(e, api.ApiError) else str(e))
            return
        res: api.DevicePoll = out["value"]
        if res.status == "ok":
            self.settings.save_token(res.token, res.expires_at)
            self.state.setText(tr("login.ok"))
            QtCore.QTimer.singleShot(600, self.accept)
        elif res.status == "slow_down":
            self.interval = max(self.interval + 5, int(res.interval or 0))
            self.timer.start(self.interval * 1000)
        elif res.status == "pending":
            self.timer.start(self.interval * 1000)
        elif res.status == "denied":
            self.state.setText(tr("login.denied"))
        else:
            self.state.setText(tr("login.expired"))

    def _stop(self) -> None:
        self.closed = True
        self.timer.stop()
        if self.bg is not None:
            self.bg.cancel()

    def reject(self) -> None:
        self._stop()
        super().reject()

    def accept(self) -> None:
        self._stop()
        super().accept()


def ensure_login(settings: Settings | None = None) -> bool:
    """Kirjautunut (tunniste tallessa) tai kirjautuminen onnistui nyt."""
    s = settings or Settings()
    if s.token:
        return True
    dlg = DeviceLoginDialog(s)
    return bool(exec_dialog(dlg)) and bool(s.token)


def account_text(settings: Settings | None = None, info: Any = None, error: Exception | None = None) -> str:
    """Kirjautumisen tila tekstinä (tilin tiedot palvelimelta, jos saatavilla). info/error = jo haettu vastaus
    GET /api/cad/oikeudet (komento hakee sen taustasäikeessä); muuten haetaan nyt."""
    s = settings or Settings()
    if not s.token:
        return tr("login.not_signed_in")
    if info is None and error is None:
        try:
            info = api.Client(s.api_base, s.token, timeout=10).entitlements()
        except api.ApiError as e:
            error = e
    if isinstance(error, api.AuthRequired):
        s.clear_token()
        return tr("login.not_signed_in")
    who = ""
    manage = MANAGE_URL
    if isinstance(info, dict):
        acc = info.get("account") if isinstance(info.get("account"), dict) else {}
        for v in (info.get("email"), acc.get("email"), acc.get("company"), acc.get("name"), info.get("name"), info.get("company")):
            if isinstance(v, str) and v:
                who = v[:120]
                break
        manage = api.safe_site_url(info.get("manage"), s.api_base) or MANAGE_URL
    lines = [tr("login.signed_in", who=who or ("?" if error is not None else "✓"))]
    if isinstance(info, dict) and info.get("free"):
        lines.append(tr("login.free"))
    exp = s.token_expires
    if exp:
        lines.append(tr("login.expires", d=exp[:10]))
    lines.append(tr("login.manage", url=manage))
    where = s.token_location()
    if where:
        lines.append(tr("login.token_where", path=where))
    return "\n".join(lines)


__all__ = ["DeviceLoginDialog", "ensure_login", "account_text", "MANAGE_URL"]
