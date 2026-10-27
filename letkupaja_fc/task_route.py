# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Laitehuolto Tiensuu Oy (Letkupaja)
"""Tehtäväpaneeli "Reititä letku": letkun ja liitinten valinta (tai Letkupaja-linkki), liitännät A ja B 3D-näkymästä
(tai Letkupaja-STEP-mallin holkit), esteet, tavoitemitta ja tulos (katkaisumitta, taivutussäde, tilauslinkki).

Reititys ajetaan taustasäikeessä (puhdas Python), jotta FreeCAD ei jäädy; dokumenttiin kirjoitetaan pääsäikeessä.
Peruuta perii kaikki paneelin muutokset (FreeCADin kumoustapahtuma). "Lataa tarkka malli" hyväksyy paneelin (OK)
ennen latausta, joten lataus ei sotke paneelin kumoustapahtumaa.

Valinnat 3D-näkymästä luetaan ratkaisemattomina (juuriobjekti + koko polku, esim. ("Kone", "Runko.Edge10")), jotta
App::Part-säiliöiden ja App::Link-kopioiden (kokoonpanot) sijoitukset tulevat mukaan.
"""
from __future__ import annotations

import html
import threading
import traceback
from typing import Any

import FreeCAD as App  # type: ignore
import FreeCADGui as Gui  # type: ignore
import Part  # type: ignore

from . import catalog as C
from . import fcgeom as F
from . import model as M
from . import routing as R
from .core import L
from .gui_util import QtCore, QtWidgets, copy_text, link_label, log, message, open_url, status
from .i18n import core_message, fmt_mm, fmt_num, fmt_vec, tr

TERMS_URL = "https://letkupaja.fi/ehdot#cad-lisaosat"
RED = "#c62828"
ORANGE = "#b25c00"
GREEN = "#2e7d32"


def _combo(items: list[tuple[str, Any]], current: Any = None) -> QtWidgets.QComboBox:
    cb = QtWidgets.QComboBox()
    _fill(cb, items, current)
    return cb


def _fill(cb: QtWidgets.QComboBox, items: list[tuple[str, Any]], current: Any = None) -> None:
    cb.blockSignals(True)
    cb.clear()
    for text, data in items:
        cb.addItem(text, data)
    idx = next((i for i, (_t, d) in enumerate(items) if d == current), 0)
    if items:
        cb.setCurrentIndex(idx)
    cb.blockSignals(False)


def _data(cb: QtWidgets.QComboBox) -> Any:
    return cb.itemData(cb.currentIndex())


class EndWidgets:
    """Yhden pään valinnat: liitin, kierre, kulma, banjon pultti ja tiiviste sekä liitännän tila ja valinta."""

    def __init__(self, panel: "RouteTaskPanel", key: str):
        self.panel = panel
        self.key = key
        K = key.upper()
        self.box = QtWidgets.QGroupBox(tr("lbl.end", k=K))
        lay = QtWidgets.QFormLayout(self.box)
        self.family = _combo([(C.family_label(f), f) for f in C.families()], "dkol")
        self.thread = QtWidgets.QComboBox()
        self.angle = QtWidgets.QComboBox()
        self.bolt = _combo([(C.bolt_label(b), b) for b in C.BOLTS], "")
        self.seal = _combo([(C.seal_label(s), s) for s in C.SEALS], "usit")
        lay.addRow(tr("lbl.family"), self.family)
        lay.addRow(tr("lbl.thread"), self.thread)
        lay.addRow(tr("lbl.angle"), self.angle)
        self.bolt_row = (QtWidgets.QLabel(tr("lbl.bolt")), self.bolt)
        self.seal_row = (QtWidgets.QLabel(tr("lbl.seal")), self.seal)
        lay.addRow(*self.bolt_row)
        lay.addRow(*self.seal_row)
        self.hint = QtWidgets.QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setTextFormat(QtCore.Qt.PlainText)
        self.hint.setStyleSheet(f"color: {ORANGE};")
        self.hint.setVisible(False)
        lay.addRow(self.hint)
        self.mode = _combo([(tr("end.mode.port"), "port"), (tr("end.mode.ferrule"), "ferrule")], "port")
        lay.addRow(self.mode)
        row = QtWidgets.QHBoxLayout()
        self.pick = QtWidgets.QPushButton(tr("end.pick", k=K))
        self.pick.setToolTip(tr("end.pick.tip"))
        self.pick.setCheckable(True)
        self.flip = QtWidgets.QCheckBox(tr("end.flip"))
        row.addWidget(self.pick)
        row.addWidget(self.flip)
        lay.addRow(row)
        self.status = QtWidgets.QLabel(tr("end.none"))
        self.status.setWordWrap(True)
        self.status.setTextFormat(QtCore.Qt.PlainText)
        lay.addRow(self.status)
        adv = QtWidgets.QHBoxLayout()
        self.offset = QtWidgets.QDoubleSpinBox()
        self.offset.setRange(-500, 500)
        self.offset.setDecimals(1)
        self.offset.setSuffix(" mm")
        self.offset.setToolTip(tr("end.offset.tip"))
        self.swing_auto = QtWidgets.QCheckBox(tr("end.swing.auto"))
        self.swing_auto.setChecked(True)
        self.swing = QtWidgets.QDoubleSpinBox()
        self.swing.setRange(-360, 360)
        self.swing.setDecimals(1)
        self.swing.setSuffix(" °")
        self.swing.setEnabled(False)
        self.swing.setToolTip(tr("end.swing.tip"))
        self.swing_auto.setToolTip(tr("end.swing.tip"))
        adv.addWidget(QtWidgets.QLabel(tr("end.offset")))
        adv.addWidget(self.offset)
        adv.addWidget(QtWidgets.QLabel(tr("end.swing")))
        adv.addWidget(self.swing_auto)
        adv.addWidget(self.swing)
        lay.addRow(adv)
        self.spec: R.EndSpec | None = None

        self.family.currentIndexChanged.connect(self.on_family)
        for w in (self.thread, self.angle, self.bolt, self.seal):
            w.currentIndexChanged.connect(self.on_bolt_visibility)
            w.currentIndexChanged.connect(panel.changed)
        self.mode.currentIndexChanged.connect(self.on_mode)
        self.flip.toggled.connect(self.on_spec_option)
        self.offset.valueChanged.connect(self.on_spec_option)
        self.swing_auto.toggled.connect(self.on_swing_auto)
        self.swing.valueChanged.connect(self.on_spec_option)
        self.pick.clicked.connect(lambda checked: panel.start_pick(self.key, checked))
        self.on_family()

    # valinnat
    def on_family(self, *_a: Any) -> None:
        fam = _data(self.family)
        size = self.panel.size()
        cur = _data(self.thread)
        threads = C.threads_for(fam)
        _fill(self.thread, [(C.thread_label(fam, t), t) for t in threads], cur if cur in threads else C.default_thread(fam, size))
        cur_a = _data(self.angle)
        angles = C.angles_for(fam)
        _fill(self.angle, [(C.angle_label(a), a) for a in angles], cur_a if cur_a in angles else angles[0])
        self.on_bolt_visibility()
        self.panel.changed()

    def on_size(self) -> None:
        fam = _data(self.family)
        _fill(self.thread, [(C.thread_label(fam, t), t) for t in C.threads_for(fam)], C.default_thread(fam, self.panel.size()))
        self.refresh_hint()

    def refresh_hint(self) -> None:
        """Vihje, jos kierre on tavallisesti selvästi eri kokoiselle letkulle (saatavuus tarkistetaan verkkokaupassa)."""
        mm = C.size_mismatch(self.end(), self.panel.size())
        self.hint.setText(tr("hint.size", thread=mm[0], nom=mm[1].upper(), size=self.panel.size().upper()) if mm else "")
        self.hint.setVisible(bool(mm))

    def on_bolt_visibility(self, *_a: Any) -> None:
        self.refresh_hint()
        banjo = _data(self.family) == "banjo"
        for w in self.bolt_row:
            w.setVisible(banjo)
        for w in self.seal_row:
            w.setVisible(banjo and bool(_data(self.bolt)))
        self.swing_auto.setEnabled(self.directional())
        self.swing.setEnabled(self.directional() and not self.swing_auto.isChecked())

    def directional(self) -> bool:
        return L.is_directional(self.end())

    def end(self) -> dict[str, Any]:
        return C.EndChoice(_data(self.family), _data(self.thread) or "", int(_data(self.angle) or 0), _data(self.bolt) or "", _data(self.seal) or "usit").to_end()

    def set_end(self, e: dict[str, Any]) -> None:
        ch = C.EndChoice.from_end(e)
        _fill(self.family, [(C.family_label(f), f) for f in C.families()], ch.family)
        self.on_family()
        _fill(self.thread, [(C.thread_label(ch.family, t), t) for t in C.threads_for(ch.family)], ch.thread)
        _fill(self.angle, [(C.angle_label(a), a) for a in C.angles_for(ch.family)], ch.angle)
        _fill(self.bolt, [(C.bolt_label(b), b) for b in C.BOLTS], ch.bolt)
        _fill(self.seal, [(C.seal_label(s), s) for s in C.SEALS], ch.seal)
        self.on_bolt_visibility()

    # liitäntä
    def on_mode(self, *_a: Any) -> None:
        if self.spec is not None:
            self.spec.mode = _data(self.mode)
            self.spec.draw_fitting = self.spec.mode == "port"
        self.refresh_status()
        self.panel.changed()

    def on_swing_auto(self, *_a: Any) -> None:
        self.swing.setEnabled(not self.swing_auto.isChecked() and self.directional())
        self.on_spec_option()

    def on_spec_option(self, *_a: Any) -> None:
        if self.spec is not None:
            self.spec.flip = self.flip.isChecked()
            self.spec.offset = float(self.offset.value())
            self.spec.swing = None if self.swing_auto.isChecked() else float(self.swing.value())
        self.refresh_status()
        self.panel.changed()

    def set_spec(self, spec: R.EndSpec | None) -> None:
        self.spec = spec
        if spec is not None:
            for w in (self.mode, self.flip, self.offset, self.swing_auto, self.swing):
                w.blockSignals(True)
            _fill(self.mode, [(tr("end.mode.port"), "port"), (tr("end.mode.ferrule"), "ferrule")], spec.mode)
            self.flip.setChecked(spec.flip)
            self.offset.setValue(spec.offset)
            self.swing_auto.setChecked(spec.swing is None)
            self.swing.setValue(spec.swing or 0.0)
            for w in (self.mode, self.flip, self.offset, self.swing_auto, self.swing):
                w.blockSignals(False)
        self.on_bolt_visibility()
        self.refresh_status()

    def refresh_status(self) -> None:
        s = self.spec
        if s is None:
            self.status.setText(tr("end.none"))
            return

        if s.mode == "ferrule":
            self.status.setText(tr("end.status.ferrule", p=fmt_vec(s.effective_point, 1), n=fmt_vec(s.axis, 2)))
        else:
            self.status.setText(tr("end.status", d=fmt_num(2 * s.radius, 1), p=fmt_vec(s.effective_point, 1), n=fmt_vec(s.axis, 2)))


class RouteTaskPanel:
    def __init__(self, group: Any = None):
        self.doc = App.ActiveDocument
        self.group = group
        self.picking: str | None = None
        self.obstacles: list[tuple[Any, str]] = []  # (juuriobjekti, aliobjektin polku)
        self.base_cfg: dict[str, Any] | None = None  # Letkupaja-linkin tai tallennetun letkun asennelma
        self.worker: threading.Thread | None = None
        self.worker_out: dict[str, Any] = {}
        self.dirty = False
        self.again = False
        self.closed = False
        self.summary: dict[str, Any] | None = None
        self.doc.openTransaction(tr("task.title"))
        self.form = [self._build_hose(), self._build_ends(), self._build_route(), self._build_result()]
        self.debounce = QtCore.QTimer()
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(600)
        self.debounce.timeout.connect(self.auto_update)
        self.poller = QtCore.QTimer()
        self.poller.setInterval(100)
        self.poller.timeout.connect(self.poll_worker)
        self.loading = True
        if group is not None:
            self.load_group(group)
        self.loading = False
        self.dirty = False
        try:  # ResolveMode.NoResolve: juuriobjekti ja koko polku (säiliöt ja linkit mukana)
            Gui.Selection.addObserver(self, 0)
        except TypeError:  # vanha FreeCAD
            Gui.Selection.addObserver(self)

    # ------------------------------------------------------------------------------------------ rakentaminen

    def _build_hose(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setWindowTitle(tr("grp.hose"))
        lay = QtWidgets.QVBoxLayout(w)
        row = QtWidgets.QHBoxLayout()
        self.link = QtWidgets.QLineEdit()
        self.link.setPlaceholderText(tr("link.placeholder"))
        b = QtWidgets.QPushButton(tr("link.read"))
        b.clicked.connect(self.read_link)
        row.addWidget(self.link)
        row.addWidget(b)
        lay.addLayout(row)
        form = QtWidgets.QFormLayout()
        self.hose = _combo([(C.hose_label(h), h) for h in C.hose_types()], "2sc")
        self.size_cb = QtWidgets.QComboBox()
        self.prot = _combo([(C.protection_label(p), p) for p in C.PROTECTIONS], "none")
        form.addRow(tr("lbl.hose"), self.hose)
        form.addRow(tr("lbl.size"), self.size_cb)
        form.addRow(tr("lbl.protection"), self.prot)
        lay.addLayout(form)
        self.extras = QtWidgets.QLabel("")
        self.extras.setWordWrap(True)
        self.extras.setTextFormat(QtCore.Qt.PlainText)
        self.extras.setVisible(False)
        lay.addWidget(self.extras)
        self._fill_sizes("dn12")
        self.hose.currentIndexChanged.connect(self.on_hose)
        self.size_cb.currentIndexChanged.connect(self.on_size)
        self.prot.currentIndexChanged.connect(self.changed)
        return w

    def on_hose(self, *_a: Any) -> None:
        self._fill_sizes(self.size())
        for e in getattr(self, "ends", {}).values():
            e.refresh_hint()
        self.changed()

    def _fill_sizes(self, current: str) -> None:
        sizes = C.sizes_for(_data(self.hose))
        _fill(self.size_cb, [(C.size_label(s), s) for s in sizes], current if current in sizes else ("dn12" if "dn12" in sizes else (sizes[0] if sizes else "")))

    def size(self) -> str:
        return _data(self.size_cb) or "dn12" if hasattr(self, "size_cb") else "dn12"

    def _build_ends(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setWindowTitle(tr("grp.ends"))
        lay = QtWidgets.QVBoxLayout(w)
        self.ends = {"a": EndWidgets(self, "a"), "b": EndWidgets(self, "b")}
        lay.addWidget(self.ends["a"].box)
        lay.addWidget(self.ends["b"].box)
        b = QtWidgets.QPushButton(tr("detect.step"))
        b.clicked.connect(self.detect_step)
        lay.addWidget(b)
        return w

    def _build_route(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setWindowTitle(tr("grp.route"))
        lay = QtWidgets.QFormLayout(w)
        orow = QtWidgets.QHBoxLayout()
        b_add = QtWidgets.QPushButton(tr("obst.add"))
        b_add.setToolTip(tr("obst.tip"))
        b_add.clicked.connect(self.add_obstacles)
        b_clr = QtWidgets.QPushButton(tr("obst.clear"))
        b_clr.clicked.connect(self.clear_obstacles)
        orow.addWidget(b_add)
        orow.addWidget(b_clr)
        lay.addRow(orow)
        self.obst_label = QtWidgets.QLabel(tr("obst.count", n=0))
        self.obst_label.setToolTip(tr("obst.tip"))
        lay.addRow(self.obst_label)
        self.safety = QtWidgets.QDoubleSpinBox()
        self.safety.setRange(1.0, 3.0)
        self.safety.setSingleStep(0.1)
        self.safety.setValue(1.0)
        self.safety.setToolTip(tr("lbl.safety.tip"))
        lay.addRow(tr("lbl.safety"), self.safety)
        self.margin = QtWidgets.QDoubleSpinBox()
        self.margin.setRange(0.0, 200.0)
        self.margin.setValue(2.0)
        self.margin.setSuffix(" mm")
        lay.addRow(tr("lbl.margin"), self.margin)
        trow = QtWidgets.QHBoxLayout()
        self.target_on = QtWidgets.QCheckBox(tr("lbl.target"))
        self.target_on.setToolTip(tr("lbl.target.tip"))
        self.target = QtWidgets.QDoubleSpinBox()
        self.target.setRange(10.0, 100000.0)
        self.target.setDecimals(0)
        self.target.setValue(1000.0)
        self.target.setSuffix(" mm")
        self.target.setEnabled(False)
        trow.addWidget(self.target_on)
        trow.addWidget(self.target)
        lay.addRow(trow)
        self.avoid = QtWidgets.QCheckBox(tr("lbl.avoid"))
        self.avoid.setChecked(True)
        lay.addRow(self.avoid)
        urow = QtWidgets.QHBoxLayout()
        self.auto = QtWidgets.QCheckBox(tr("lbl.auto"))
        self.auto.setChecked(True)
        self.b_update = QtWidgets.QPushButton(tr("btn.update"))
        self.b_update.clicked.connect(self.update_now)
        urow.addWidget(self.auto)
        urow.addWidget(self.b_update)
        lay.addRow(urow)
        self.target_on.toggled.connect(lambda on: (self.target.setEnabled(on), self.changed()))
        for sp in (self.safety, self.margin, self.target):
            sp.valueChanged.connect(self.changed)
        self.avoid.toggled.connect(self.changed)
        return w

    def _build_result(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setWindowTitle(tr("grp.result"))
        lay = QtWidgets.QVBoxLayout(w)
        self.r_state = QtWidgets.QLabel(tr("res.none"))
        self.r_state.setWordWrap(True)
        self.r_state.setTextFormat(QtCore.Qt.RichText)
        lay.addWidget(self.r_state)
        form = QtWidgets.QFormLayout()
        self.r_cut = QtWidgets.QLabel("–")
        f = self.r_cut.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        self.r_cut.setFont(f)
        self.r_total = QtWidgets.QLabel("–")
        self.r_radius = QtWidgets.QLabel("–")
        self.r_clear = QtWidgets.QLabel("–")
        self.r_orient = QtWidgets.QLabel("–")
        self.r_orient.setToolTip(tr("res.orient.tip"))
        form.addRow(tr("res.cut"), self.r_cut)
        form.addRow(tr("res.total"), self.r_total)
        form.addRow(tr("res.radius"), self.r_radius)
        form.addRow(tr("res.clear"), self.r_clear)
        form.addRow(tr("res.orient"), self.r_orient)
        lay.addLayout(form)
        self.r_warn = QtWidgets.QLabel("")
        self.r_warn.setWordWrap(True)
        self.r_warn.setTextFormat(QtCore.Qt.PlainText)
        self.r_warn.setStyleSheet(f"color: {RED};")
        lay.addWidget(self.r_warn)
        brow = QtWidgets.QHBoxLayout()
        self.b_copy = QtWidgets.QPushButton(tr("btn.copy"))
        self.b_copy.clicked.connect(self.copy_link)
        self.b_open = QtWidgets.QPushButton(tr("btn.open"))
        self.b_open.clicked.connect(self.open_link)
        brow.addWidget(self.b_copy)
        brow.addWidget(self.b_open)
        lay.addLayout(brow)
        self.b_detail = QtWidgets.QPushButton(tr("btn.detailed"))
        self.b_detail.setToolTip(tr("btn.detailed.tip"))
        self.b_detail.clicked.connect(self.download_detailed)
        lay.addWidget(self.b_detail)
        for b in (self.b_copy, self.b_open, self.b_detail):
            b.setEnabled(False)
        note = QtWidgets.QLabel(tr("res.note"))
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addWidget(link_label(TERMS_URL, tr("terms")))
        return w

    # ------------------------------------------------------------------------------------------ tila

    def config(self) -> dict[str, Any]:
        """Paneelin valinnat linkin/tallennetun asennelman päälle (linkin muut valinnat säilyvät)."""
        return R.panel_config(self.base_cfg, _data(self.hose), self.size(), _data(self.prot), self.ends["a"].end(), self.ends["b"].end())

    def _show_extras(self) -> None:
        keys = R.config_extras(self.base_cfg or {})
        names = [tr(f"extra.{k}") for k in keys]
        self.extras.setText(tr("link.extras", items=", ".join(names)) if names else "")
        self.extras.setVisible(bool(names))

    def set_config(self, cfg: dict[str, Any]) -> None:
        self.base_cfg = dict(cfg)
        self._show_extras()
        self.loading = True
        try:
            _fill(self.hose, [(C.hose_label(h), h) for h in C.hose_types()], cfg.get("hose", "2sc"))
            self._fill_sizes(cfg.get("size", "dn12"))
            _fill(self.prot, [(C.protection_label(p), p) for p in C.PROTECTIONS], cfg.get("protection", "none"))
            self.ends["a"].set_end(cfg["a"])
            self.ends["b"].set_end(cfg["b"])
        finally:
            self.loading = False
        for e in self.ends.values():
            e.refresh_hint()

    def load_group(self, g: Any) -> None:
        inp = M.load_inputs(g)
        if inp["config"]:
            self.set_config(inp["config"])
        self.ends["a"].set_spec(inp["a"])
        self.ends["b"].set_spec(inp["b"])
        self.obstacles = list(inp["obstacles"])
        self.obst_label.setText(tr("obst.count", n=len(self.obstacles)))
        self.safety.setValue(inp["safety"])
        self.margin.setValue(inp["margin"])
        if inp["target_cut"]:
            self.target_on.setChecked(True)
            self.target.setValue(inp["target_cut"])
        self.avoid.setChecked(inp["avoid"])
        if g.CenterlinePoints:
            self.show_result_from_group(g)

    def on_size(self, *_a: Any) -> None:
        if not self.loading:
            for e in self.ends.values():
                e.on_size()
        self.changed()

    def changed(self, *_a: Any) -> None:
        if getattr(self, "loading", True):
            return
        self.dirty = True
        if self.auto.isChecked() and self.ends["a"].spec is not None and self.ends["b"].spec is not None:
            self.debounce.start()

    # ------------------------------------------------------------------------------------------ linkki ja STEP

    def read_link(self) -> None:
        text = self.link.text().strip()
        if not text:
            return
        try:
            cfg = L.parse_link(text)
            err = L.validate_config(cfg)
            if err:
                raise ValueError("; ".join(err))
        except Exception as e:  # noqa: BLE001
            self.set_state(tr("err.link", e=core_message(str(e))))
            return
        self.set_config(cfg)
        if cfg.get("lengthMm"):
            cut = L.lengths(cfg).cut
            if cut > 0:
                self.target_on.setChecked(True)
                self.target.setValue(round(cut))
        self.set_state(tr("link.read.ok"))
        self.changed()

    def detect_step(self) -> None:
        ports = F.detect_step_ports(M.step_roots(self.doc))
        if not ports:
            self.set_state(tr("detect.none"))
            return
        for k, p in ports.items():
            self.ends[k.lower()].set_spec(M.ferrule_spec_from_step(p))
        self.set_state(tr("detect.ok", n=len(ports)))
        self.changed()

    # ------------------------------------------------------------------------------------------ valinnat

    def start_pick(self, key: str, checked: bool) -> None:
        for k, e in self.ends.items():
            if k != key:
                e.pick.setChecked(False)
        if not checked:
            self.picking = None
            return
        sel = Gui.Selection.getSelectionEx("", 0)
        items = [(s.Object, sub, (s.PickedPoints[i] if i < len(s.PickedPoints) else None)) for s in sel for i, sub in enumerate(s.SubElementNames) if sub]
        if items:
            obj, sub, pnt = items[-1]
            self.use_pick(key, obj, sub, pnt)
            self.ends[key].pick.setChecked(False)
            Gui.Selection.clearSelection()
            return
        self.picking = key
        status(tr("end.picking", k=key.upper()), 10000)

    def addSelection(self, doc: str, obj: str, sub: str, pnt: Any) -> None:  # noqa: N802 - FreeCADin SelectionObserver
        # Tarkkailija on rekisteröity ilman ratkaisua: obj = juuriobjekti, sub = koko polku (esim. "Runko.Edge10")
        if not self.picking or not sub or sub.endswith("."):
            return
        key = self.picking
        self.picking = None
        o = App.getDocument(doc).getObject(obj)
        if o is None:
            return
        picked = App.Vector(*pnt) if pnt is not None else None
        # Kutsutaan myöhemmin, jottei valinnan käsittely jää kesken
        QtCore.QTimer.singleShot(0, lambda: self._finish_pick(key, o, sub, picked))

    def _finish_pick(self, key: str, obj: Any, sub: str, picked: Any) -> None:
        self.ends[key].pick.setChecked(False)
        self.use_pick(key, obj, sub, picked)

    def use_pick(self, key: str, obj: Any, sub: str, picked: Any) -> None:
        """obj = juuriobjekti, sub = koko polku alielementtiin: muodot maailman koordinaateissa (Part.getShape
        kulkee säiliöiden ja linkkien sijoitusten läpi)."""
        try:
            sub_shape = Part.getShape(obj, sub, needSubElement=True)
            owner_sub = F.sub_path(sub)
            owner = Part.getShape(obj, owner_sub) if owner_sub else Part.getShape(obj)
            p = F.port_from_subshape(sub_shape, owner, picked)
        except Exception:  # noqa: BLE001
            p = None
        if p is None:
            self.set_state(tr("err.bad_pick"))
            return
        e = self.ends[key]
        mode = _data(e.mode)
        source = {"object": obj.Name, "doc": obj.Document.Name, "sub": sub, "rot": F.shape_rotation(obj, sub)}
        spec = R.EndSpec(mode, p["point"], p["direction"], radius=p["radius"], flip=False, offset=float(e.offset.value()), swing=None if e.swing_auto.isChecked() else float(e.swing.value()), draw_fitting=mode == "port", source=source)
        e.set_spec(spec)
        self.changed()

    def add_obstacles(self) -> None:
        """Valitut kappaleet esteiksi: (juuriobjekti, aliobjektin polku), jotta säiliön tai linkin kopion oikea
        sijainti tulee mukaan (myös Päivitä letkut -komennossa)."""
        sel = Gui.Selection.getSelectionEx("", 0)
        if not sel:
            self.set_state(tr("err.no_selection"))
            return
        have = {(o.Name, s) for o, s in self.obstacles}
        for so in sel:
            obj = so.Object
            subs = [F.sub_path(x) for x in (so.SubElementNames or [])] or [""]
            for path in dict.fromkeys(subs):
                leaf = F.sub_object(obj, path)
                if M.is_hose_group(obj) or M.is_hose_group(leaf) or M.owning_hose_group(leaf) is not None:
                    continue
                if (obj.Name, path) in have or F.bbox_of(obj, path) is None:
                    continue
                have.add((obj.Name, path))
                self.obstacles.append((obj, path))
        self.obst_label.setText(tr("obst.count", n=len(self.obstacles)))
        Gui.Selection.clearSelection()
        self.changed()

    def clear_obstacles(self) -> None:
        self.obstacles = []
        self.obst_label.setText(tr("obst.count", n=0))
        self.changed()

    # ------------------------------------------------------------------------------------------ reititys

    def auto_update(self) -> None:
        if self.auto.isChecked():
            self.update_now(quiet=True)

    def _ensure_group(self) -> Any:
        if self.group is None:
            self.group = M.create_group(self.doc)
        return self.group

    def _save(self) -> bool:
        a, b = self.ends["a"].spec, self.ends["b"].spec
        if a is None or b is None:
            self.set_state(tr("err.need_ports"))
            return False
        g = self._ensure_group()
        M.save_inputs(g, self.config(), a, b, self.obstacles, self.safety.value(), self.margin.value(), self.target.value() if self.target_on.isChecked() else None, self.avoid.isChecked())
        return True

    def update_now(self, quiet: bool = False) -> None:
        if self.worker is not None:
            self.again = True
            return
        if not self._save():
            return
        try:
            req, notes = M.prepare_request(self.group)
        except Exception as e:  # noqa: BLE001
            self.set_state(tr("err.route", e=core_message(str(e))))
            return
        self.dirty = False
        self.b_update.setEnabled(False)
        self.set_state(tr("busy"))
        self.worker_out = {"req": req, "notes": notes}

        def work(out: dict[str, Any] = self.worker_out) -> None:
            try:
                out["result"] = R.run_route(out["req"])
            except Exception as e:  # noqa: BLE001
                out["error"] = e
                out["trace"] = traceback.format_exc()

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        self.poller.start()

    def poll_worker(self) -> None:
        if self.worker is None or self.worker.is_alive():
            return
        self.poller.stop()
        self.worker = None
        self.b_update.setEnabled(True)
        out = self.worker_out
        if self.closed:
            return
        if "error" in out:
            self.set_state(tr("err.route", e=core_message(str(out["error"]))))
            log(out.get("trace", ""))
        else:
            try:
                self.summary = M.apply_result(self.group, out["req"], out["result"], out["notes"])
                self.show_result(self.summary)
            except Exception as e:  # noqa: BLE001
                self.set_state(tr("err.route", e=str(e)))
                log(traceback.format_exc())
            Gui.updateGui()
        if self.again or self.dirty:
            self.again = False
            if self.auto.isChecked() or self.dirty:
                self.debounce.start()

    def wait_worker(self) -> None:
        while self.worker is not None and self.worker.is_alive():
            self.worker.join(0.05)
            QtWidgets.QApplication.processEvents()
        self.poll_worker()

    # ------------------------------------------------------------------------------------------ tulos

    def set_state(self, text: str, color: str | None = None) -> None:
        """Tilarivi: teksti aina escapattuna (dokumentin tai palvelimen teksti ei muutu linkiksi)."""
        body = html.escape(text).replace("\n", "<br>")
        self.r_state.setText(f'<span style="color:{color}">{body}</span>' if color else body)

    def show_result(self, s: dict[str, Any]) -> None:
        ok = s.get("ok")
        self.set_state(tr("res.ok") if ok else tr("res.notok"), GREEN if ok else RED)
        cut = s.get("cut_length_mm")
        self.r_cut.setText(f"{cut} mm" if cut else "–")
        self.r_total.setText(fmt_mm(s.get("total_length"), 0))
        mr, req = s.get("min_radius"), s.get("required_radius") or 0
        bad = mr is not None and mr < req * (1 - 1e-3)
        rtxt = fmt_mm(float("inf") if mr is None else mr, 0) + " (" + tr("res.radius.req", r=fmt_mm(req, 0)) + ")"
        self.r_radius.setText(f'<span style="color:{RED if bad else GREEN}">{rtxt}</span>')
        cl = s.get("min_clearance")
        self.r_clear.setText(fmt_mm(cl, 1) if cl is not None else "–")
        ori = s.get("orientation_deg")
        both = self.ends["a"].directional() and self.ends["b"].directional()
        self.r_orient.setText(f"{fmt_num(ori, 0)}°" if ori is not None and both else (tr("res.orient.unknown") if both else "–"))
        self.r_warn.setText("\n".join("• " + w for w in s.get("warnings") or []))
        has_link = bool(s.get("link"))
        for b in (self.b_copy, self.b_open, self.b_detail):
            b.setEnabled(has_link)

    def show_result_from_group(self, g: Any) -> None:
        self.show_result(
            {
                "ok": bool(g.RouteOk),
                "cut_length_mm": round(float(g.CutLength)) if float(g.CutLength) else None,
                "total_length": float(g.TotalLength) or None,
                "min_radius": float(g.MinBendRadius) or None,
                "required_radius": float(g.RequiredBendRadius),
                "min_clearance": None,
                "orientation_deg": float(g.Orientation),
                "warnings": list(g.Warnings),
                "link": M.safe_order_link(g),
            }
        )

    def link_text(self) -> str:
        """Tilauslinkki: tässä paneelissa laskettu (ytimen koostama) tai tallennetusta asennelmasta koottu. Dokumentin
        OrderLink-tekstiä ei käytetä."""
        return M.safe_order_link(self.group) if self.group is not None else ""

    def _problems(self) -> list[str]:
        if self.group is None:
            return []
        return M.route_problems(self.group)

    def copy_link(self) -> None:
        from .commands import confirm_order

        link = self.link_text()
        if not link or not confirm_order(self._problems(), "order.anyway.copy"):
            return
        copy_text(link)
        status(tr("res.link.copied"))
        self.set_state(tr("res.link.copied"))

    def open_link(self) -> None:
        from .commands import confirm_order

        link = self.link_text()
        if link and confirm_order(self._problems(), "order.anyway.open"):
            open_url(link)

    def download_detailed(self) -> None:
        """Hyväksy paneeli (OK: reititys tallennetaan) ja lataa sitten tarkka malli omana kumoustapahtumanaan."""
        if self.group is None:
            return
        from .commands import download_detailed

        g = self.group
        self.accept()
        download_detailed(g)

    # ------------------------------------------------------------------------------------------ paneeli

    def getStandardButtons(self) -> int:  # noqa: N802
        b = QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        try:
            return int(b)
        except TypeError:  # Qt 6 / PySide6: lippu on enum.Flag
            return int(getattr(b, "value", 0x00000400 | 0x00400000))

    def _close(self) -> None:
        self.closed = True
        self.debounce.stop()
        try:
            Gui.Selection.removeObserver(self)
        except Exception:  # noqa: BLE001
            pass
        Gui.Control.closeDialog()

    def accept(self) -> bool:
        if self.closed:
            return True
        self.wait_worker()
        if self.dirty and self.ends["a"].spec is not None and self.ends["b"].spec is not None:
            self.update_now()
            self.wait_worker()
        self.closed = True
        if self.group is not None:
            self.doc.commitTransaction()
        else:
            self.doc.abortTransaction()
        self._close()
        return True

    def reject(self) -> bool:
        if self.closed:
            return True
        self.closed = True
        if self.worker is not None:
            self.worker.join(15)
        self.doc.abortTransaction()
        self.doc.recompute()
        self._close()
        return True


_CURRENT: RouteTaskPanel | None = None


def open_panel(group: Any = None) -> RouteTaskPanel | None:
    """Avaa Reititä letku -paneeli. Jos oma paneeli on jo auki, se perutaan hallitusti; toisen työpöydän avointa
    tehtävää (esim. luonnoksen muokkaus) ei suljeta, vaan käyttäjää pyydetään sulkemaan se ensin."""
    global _CURRENT
    if Gui.Control.activeDialog():
        cur = current()
        if cur is not None:
            cur.reject()
        if Gui.Control.activeDialog():
            message(tr("err.panel_busy"), kind="warning")
            return None
    panel = RouteTaskPanel(group)
    Gui.Control.showDialog(panel)
    _CURRENT = panel
    return panel


def current() -> RouteTaskPanel | None:
    """Avoinna oleva paneeli (testejä varten)."""
    return _CURRENT if _CURRENT is not None and not _CURRENT.closed else None
