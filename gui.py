#!/usr/bin/env python3
"""P2C Bot GUI — 099 SUPPLY style (design/DESIGN.md).

Светлый монохром: canvas #ffffff, ink #101010, hairline #e0e0e0.
Моно-шрифт везде, кнопки-пилюли 9999px, карточки 8px, без теней.
"""
import json
import os
import threading
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QSize, QPointF, Signal
from PySide6.QtGui import (
    QShortcut, QKeySequence, QIcon, QPixmap, QPainter, QPen, QColor,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame, QStackedWidget,
    QScrollArea, QMessageBox, QButtonGroup,
)
import sys

import p2c

# Frozen exe/app: .env и active.id — рядом с exe, а не в cwd.
if getattr(sys, "frozen", False):
    try:
        _d = os.path.dirname(sys.executable)
        if _d.endswith("Contents/MacOS"):
            _d = os.path.normpath(os.path.join(_d, "..", "..", ".."))
        os.chdir(_d)
    except Exception:
        pass

MONO = "'JetBrains Mono', 'IBM Plex Mono', 'Space Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

QSS = f"""
QMainWindow, QWidget {{ background-color: #ffffff; color: #101010;
  font-family: {MONO}; font-size: 12px; font-weight: 400; }}
QFrame#Sidebar {{ background-color: #ffffff; border-right: 1px solid #e0e0e0; }}
QFrame#Card {{ background-color: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px; }}
QFrame#Line {{ background-color: #e0e0e0; max-height: 1px; min-height: 1px; }}
QLabel#Logo {{ font-size: 26px; font-weight: 500; color: #101010; }}
QLabel#Sub {{ font-size: 10px; color: #555555; }}
QLabel#Hint {{ font-size: 10px; color: #999999; }}
QLabel#Section {{ font-size: 11px; font-weight: 400; color: #999999; }}
QLabel#Title {{ font-size: 16px; font-weight: 500; color: #999999; }}
QLabel#StatK {{ font-size: 10px; font-weight: 400; color: #999999; }}
QLabel#StatV {{ font-size: 18px; font-weight: 400; color: #101010; }}
QLabel#StatH {{ font-size: 10px; color: #999999; }}
QLabel#StatusDot {{ font-size: 14px; font-weight: 400; }}
QLabel#ActiveBig {{ font-family: {MONO}; font-size: 12px; color: #101010; }}
QLabel#FieldLabel {{ font-size: 11px; font-weight: 400; color: #555555; }}
QLabel#FieldHint {{ font-size: 10px; color: #999999; }}
QPushButton {{ background: transparent; color: #555555; border: 1px solid #999999;
  border-radius: 9999px; padding: 7px 16px; font-weight: 400; font-size: 12px;
  font-family: {MONO}; }}
QPushButton:hover {{ border-color: #101010; color: #101010; }}
QPushButton:disabled {{ color: #999999; border-color: #e0e0e0; background: transparent; }}
QPushButton#Primary {{ background: #101010; color: #ffffff; border: 1px solid #101010;
  font-size: 12px; padding: 9px 16px; }}
QPushButton#Primary:hover {{ background: #999999; border-color: #999999; color: #ffffff; }}
QPushButton#Danger {{ background: transparent; color: #555555; border: 1px solid #999999; }}
QPushButton#Danger:hover {{ border-color: #101010; color: #101010; }}
QPushButton#IconBtn {{ background: transparent; color: #101010; border: 1px solid #999999;
  border-radius: 9999px; padding: 0px; }}
QPushButton#IconBtn:hover {{ border-color: #101010; }}
QPushButton#Mode {{ background: transparent; border: 1px solid #e0e0e0; border-radius: 9999px;
  padding: 7px 4px; font-size: 12px; color: #555555; }}
QPushButton#Mode:hover {{ border-color: #999999; color: #101010; }}
QPushButton#Mode:checked {{ background: #101010; color: #ffffff; border: 1px solid #101010; font-weight: 500; }}
QPushButton#NavBtn {{ background: transparent; border: none; border-radius: 9999px;
  padding: 9px 8px; font-size: 12px; color: #555555; text-align: center; }}
QPushButton#NavBtn:hover {{ color: #101010; }}
QPushButton#NavBtn:checked {{ background: #101010; color: #ffffff; font-weight: 500; }}
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {{ background: #ffffff; color: #101010;
  border: 1px solid #e0e0e0; border-radius: 4px; padding: 8px; selection-background-color: #101010;
  selection-color: #ffffff; font-family: {MONO}; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid #101010; }}
QComboBox QAbstractItemView {{ background: #ffffff; color: #101010; selection-background-color: #101010;
  selection-color: #ffffff; border: 1px solid #e0e0e0; }}
QTableWidget {{ background: #ffffff; color: #101010; gridline-color: #e0e0e0;
  border: none; font-family: {MONO}; font-size: 12px; }}
QHeaderView::section {{ background: #ffffff; color: #999999; padding: 8px; border: none;
  border-bottom: 1px solid #e0e0e0; font-weight: 400; font-size: 11px; }}
QTableWidget::item:selected {{ background: #101010; color: #ffffff; }}
QScrollBar:vertical {{ background: transparent; width: 9px; }}
QScrollBar::handle:vertical {{ background: #e0e0e0; min-height: 24px; border-radius: 4px; }}
QScrollBar::handle:vertical:hover {{ background: #999999; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollArea {{ border: none; background: #ffffff; }}
"""

MODES = [("Тест", p2c.MODE_TEST), ("Ловля", p2c.MODE_SEMI), ("Авто", p2c.MODE_AUTO)]
MODE_HINTS = {
    p2c.MODE_TEST: "Только смотрит, ничего не берёт.",
    p2c.MODE_SEMI: "Берёт ордер, ты оплачиваешь и жмёшь C.",
    p2c.MODE_AUTO: "Берёт и платит в браузере сам (Альфа).",
}


def _icon(kind: str) -> QIcon:
    """Рисованная иконка 16px — не зависит от шрифта.

    Текстовые глифы вроде ☰ / ▶ / ■ в моно-шрифтах часто отсутствуют
    и рисуются пустым квадратом, поэтому меню/старт/стоп рисуем сами.
    """
    px = QPixmap(16, 16)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    ink = QColor("#101010")
    if kind == "menu":
        pen = QPen(ink)
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        for y in (4, 8, 12):
            p.drawLine(3, y, 13, y)
    elif kind == "play":
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        p.drawPolygon(QPolygonF([QPointF(5, 3), QPointF(12, 8), QPointF(5, 13)]))
    else:  # stop
        p.fillRect(5, 5, 6, 6, ink)
    p.end()
    return QIcon(px)


class App(QMainWindow):
    api_out = Signal(str)
    log_sig = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("P2C — merchant")
        self.resize(1280, 800)
        self.setMinimumSize(620, 440)
        self.setStyleSheet(QSS)
        self._sb_collapsed = False
        self._sb_manual = False
        self._stats_cols = 0
        self._act_cols = 0
        self._pay_stacked = False
        self._qr_hidden = False
        self._ic_menu = _icon("menu")
        self._ic_play = _icon("play")
        self._ic_stop = _icon("stop")
        self._ic_none = QIcon()

        self.cfg = p2c.load_config()
        self.api = p2c.Client(self.cfg)

        self.holder: dict = {}
        self.stop_ev: Optional[threading.Event] = None
        self.worker: Optional[threading.Thread] = None
        self.running = False
        self.mode = p2c.MODE_SEMI
        self._last: dict = {}
        self._queue_keys: list = []

        p2c.render = lambda *a, **k: None  # type: ignore
        p2c.clear_screen = lambda *a, **k: None  # type: ignore

        self._build()
        self._shortcuts()
        self._set_mode(self.mode)
        self.api_out.connect(self._on_api_out)
        self.log_sig.connect(self._on_log_sig)

        self.tick = QTimer(self)
        self.tick.setInterval(500)
        self.tick.timeout.connect(self._sync)
        self.tick.start()
        self._log("Готов. Вставь ключ в Настройках и нажми Запустить.")

    # ================= каркас =================
    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._sidebar())

        self.pages = QStackedWidget()
        lay.addWidget(self.pages, 1)
        self.pages.addWidget(self._work_page())
        self.pages.addWidget(self._settings_page())

    def _line(self) -> QFrame:
        f = QFrame()
        f.setObjectName("Line")
        f.setFrameShape(QFrame.HLine)
        return f

    def _sidebar(self) -> QFrame:
        sb = QFrame()
        sb.setObjectName("Sidebar")
        sb.setFixedWidth(248)
        self.sidebar = sb
        s = QVBoxLayout(sb)
        s.setContentsMargins(18, 22, 18, 16)
        s.setSpacing(12)
        self.sb_layout = s

        hrow = QHBoxLayout()
        self.logo = QLabel("P2C")
        self.logo.setObjectName("Logo")
        hrow.addWidget(self.logo)
        hrow.addStretch()
        self.burger = QPushButton()
        self.burger.setObjectName("IconBtn")
        self.burger.setIcon(self._ic_menu)
        self.burger.setIconSize(QSize(16, 16))
        self.burger.setCursor(Qt.PointingHandCursor)
        self.burger.setFixedSize(34, 30)
        self.burger.setToolTip("Скрыть / показать панель")
        self.burger.clicked.connect(self._sb_toggle)
        hrow.addWidget(self.burger)
        s.addLayout(hrow)
        self.sb_sub = QLabel("merchant · crypto bot")
        self.sb_sub.setObjectName("Sub")
        s.addWidget(self.sb_sub)
        s.addWidget(self._line())
        s.addSpacing(2)

        # --- статус ---
        self.sb_status_sec = QLabel("СТАТУС")
        self.sb_status_sec.setObjectName("Section")
        s.addWidget(self.sb_status_sec)
        self.sb_status_card = QFrame()
        self.sb_status_card.setObjectName("Card")
        cl = QVBoxLayout(self.sb_status_card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(4)
        row = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setObjectName("StatusDot")
        self.dot.setStyleSheet("color: #999999;")
        self.st_txt = QLabel("Остановлен")
        self.st_txt.setStyleSheet("font-weight: 500; font-size: 14px;")
        row.addWidget(self.dot)
        row.addWidget(self.st_txt)
        row.addStretch()
        cl.addLayout(row)
        self.st_sub = QLabel("нажми «Запустить»")
        self.st_sub.setObjectName("Sub")
        self.st_sub.setWordWrap(True)
        cl.addWidget(self.st_sub)
        s.addWidget(self.sb_status_card)

        # --- режим ---
        self.sb_mode_sec = QLabel("РЕЖИМ РАБОТЫ")
        self.sb_mode_sec.setObjectName("Section")
        s.addWidget(self.sb_mode_sec)
        self.mode_btns = QButtonGroup(self)
        self.mode_btns.setExclusive(True)
        self.mode_wrap = QWidget()
        mrow = QHBoxLayout(self.mode_wrap)
        mrow.setContentsMargins(0, 0, 0, 0)
        mrow.setSpacing(8)
        for i, (name, mval) in enumerate(MODES):
            b = QPushButton(name)
            b.setObjectName("Mode")
            b.setCheckable(True)
            b.setMinimumWidth(0)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=mval: self._set_mode(v))
            self.mode_btns.addButton(b, i)
            mrow.addWidget(b, 1)
            if mval == self.mode:
                b.setChecked(True)
        s.addWidget(self.mode_wrap)
        self.mode_hint = QLabel(MODE_HINTS[self.mode])
        self.mode_hint.setObjectName("Hint")
        self.mode_hint.setWordWrap(True)
        s.addWidget(self.mode_hint)
        s.addSpacing(2)

        self.btn_go = QPushButton("Запустить")
        self.btn_go.setObjectName("Primary")
        self.btn_go.setCursor(Qt.PointingHandCursor)
        self.btn_go.clicked.connect(self._toggle)
        s.addWidget(self.btn_go)

        s.addWidget(self._line())
        self.sb_nav_sec = QLabel("РАЗДЕЛЫ")
        self.sb_nav_sec.setObjectName("Section")
        s.addWidget(self.sb_nav_sec)
        self.nav_grp = QButtonGroup(self)
        self.nav_grp.setExclusive(True)
        self.nav_work = QPushButton("Работа")
        self.nav_set = QPushButton("Настройки")
        self.nav_work.setToolTip("Работа")
        self.nav_set.setToolTip("Настройки")
        for b in (self.nav_work, self.nav_set):
            b.setObjectName("NavBtn")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(42)
            b.setMinimumWidth(0)
            self.nav_grp.addButton(b)
        self.nav_work.setChecked(True)
        self.nav_work.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        self.nav_set.clicked.connect(lambda: self.pages.setCurrentIndex(1))
        s.addWidget(self.nav_work)
        s.addWidget(self.nav_set)

        s.addStretch()
        self.sb_ver = QLabel("v2.5 · чб")
        self.sb_ver.setObjectName("Sub")
        s.addWidget(self.sb_ver)
        return sb

    def _sb_toggle(self) -> None:
        self._sb_manual = True
        self._set_sb_collapsed(not self._sb_collapsed)

    def _set_sb_collapsed(self, on: bool) -> None:
        self._sb_collapsed = on
        sb = self.sidebar
        if on:
            sb.setFixedWidth(64)
            self.sb_layout.setContentsMargins(10, 16, 10, 12)
            self.logo.setVisible(False)
            for w in (self.sb_sub, self.sb_status_sec, self.sb_status_card,
                      self.sb_mode_sec, self.mode_wrap, self.mode_hint,
                      self.sb_nav_sec, self.sb_ver):
                w.setVisible(False)
            self.nav_work.setText("1")
            self.nav_set.setText("2")
        else:
            sb.setFixedWidth(248)
            self.sb_layout.setContentsMargins(18, 22, 18, 16)
            self.logo.setText("P2C")
            self.logo.setVisible(True)
            for w in (self.sb_sub, self.sb_status_sec, self.sb_status_card,
                      self.sb_mode_sec, self.mode_wrap, self.mode_hint,
                      self.sb_nav_sec, self.sb_ver):
                w.setVisible(True)
            self.nav_work.setText("Работа")
            self.nav_set.setText("Настройки")
        self._refresh_go()

    def _refresh_go(self) -> None:
        if self._sb_collapsed:
            self.btn_go.setObjectName("IconBtn")
            self.btn_go.setText("")
            self.btn_go.setIcon(self._ic_stop if self.running else self._ic_play)
        else:
            self.btn_go.setObjectName("Danger" if self.running else "Primary")
            self.btn_go.setText("Остановить" if self.running else "Запустить")
            self.btn_go.setIcon(self._ic_none)
        self.btn_go.setStyle(self.btn_go.style())

    def _sec(self, s: str) -> QLabel:
        l = QLabel(s)
        l.setObjectName("Section")
        return l

    def _set_mode(self, m: int) -> None:
        self.mode = m
        self.mode_hint.setText(MODE_HINTS.get(m, ""))
        is_auto = (m == p2c.MODE_AUTO)
        if hasattr(self, "b_bank"):
            self.b_bank.setEnabled(is_auto)
            self.b_bank.setToolTip("Запускает Chrome с Альфа-Банком. Работает только в режиме Авто"
                                   if is_auto else "Автобанк работает только в режиме Авто — переключи режим")
            self.b_bank.setText("Открыть браузер" if is_auto else "Открыть браузер (только Авто)")

    # ================= страница работы =================
    def _work_page(self) -> QWidget:
        outer = QWidget()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("Работа")
        title.setObjectName("Title")
        head.addWidget(title)
        head.addStretch()
        self.lbl_err = QLabel("")
        self.lbl_err.setObjectName("Sub")
        head.addWidget(self.lbl_err)
        lay.addLayout(head)

        # --- цифры компактно, перестраиваются под ширину ---
        stats_wrap = QWidget()
        self.stats_grid = QGridLayout(stats_wrap)
        self.stats_grid.setContentsMargins(0, 0, 0, 0)
        self.stats_grid.setSpacing(10)
        self.stat_cards: list[QFrame] = []
        self.v_seen = self._stat("Просмотрено")
        self.v_take = self._stat("Взято")
        self.v_done = self._stat("Готово")
        self.v_turn = self._stat("Оборот")
        lay.addWidget(stats_wrap)

        # --- ордер: всё в одну компактную карточку ---
        act = QFrame()
        act.setObjectName("Card")
        al = QVBoxLayout(act)
        al.setContentsMargins(16, 14, 16, 14)
        al.setSpacing(10)
        al.addWidget(self._sec("ОРДЕР"))
        self.lbl_active = QLabel("Нет активного заказа.")
        self.lbl_active.setObjectName("ActiveBig")
        self.lbl_active.setWordWrap(True)
        al.addWidget(self.lbl_active)
        self.b_done = QPushButton("Завершить (C)")
        self.b_done.setObjectName("Primary")
        self.b_done.setCursor(Qt.PointingHandCursor)
        self.b_done.setMinimumWidth(0)
        self.b_done.setToolTip("Нажимай после оплаты рублей в банке")
        self.b_done.clicked.connect(self._do_complete)
        al.addWidget(self.b_done)
        act_wrap = QWidget()
        self.act_grid = QGridLayout(act_wrap)
        self.act_grid.setContentsMargins(0, 0, 0, 0)
        self.act_grid.setSpacing(8)
        self.b_cancel = QPushButton("Отменить (X)")
        self.b_cancel.setObjectName("Danger")
        self.b_cancel.setCursor(Qt.PointingHandCursor)
        self.b_cancel.setMinimumWidth(0)
        self.b_cancel.clicked.connect(self._do_cancel)
        self.cmb_reason = QComboBox()
        self.cmb_reason.addItems(["bank", "balance", "qr", "qr-paid"])
        self.cmb_reason.setToolTip("Причина отмены")
        self.cmb_reason.setMinimumWidth(0)
        self.b_unhold = QPushButton("Сброс (U)")
        self.b_unhold.setCursor(Qt.PointingHandCursor)
        self.b_unhold.setMinimumWidth(0)
        self.b_unhold.clicked.connect(self._do_unhold)
        self.act_grid.addWidget(self.b_cancel, 0, 0)
        self.act_grid.addWidget(self.cmb_reason, 0, 1)
        self.act_grid.addWidget(self.b_unhold, 0, 2)
        al.addWidget(act_wrap)
        lay.addWidget(act)

        # --- автобанк: только АВТО (в Тест/Ловля кнопка мертва) ---
        bank = QFrame()
        bank.setObjectName("Card")
        bl = QVBoxLayout(bank)
        bl.setContentsMargins(16, 14, 16, 14)
        bl.setSpacing(10)
        bl.addWidget(self._sec("АВТОБАНК · АЛЬФА (только Авто)"))
        self.lbl_bank = QLabel("Браузер закрыт. Нажми «Открыть браузер», залогинься в Альфу один раз — дальше платит сам, 24/7.")
        self.lbl_bank.setObjectName("ActiveBig")
        self.lbl_bank.setWordWrap(True)
        bl.addWidget(self.lbl_bank)
        brow = QHBoxLayout()
        brow.setSpacing(8)
        self.b_bank = QPushButton("Открыть браузер")
        self.b_bank.setObjectName("Primary")
        self.b_bank.setCursor(Qt.PointingHandCursor)
        self.b_bank.setMinimumWidth(0)
        self.b_bank.setToolTip("Запускает Chrome с Альфа-Банком. Работает только в режиме Авто")
        self.b_bank.clicked.connect(self._do_bank_open)
        brow.addWidget(self.b_bank, 1)
        self.b_bank_close = QPushButton("Закрыть")
        self.b_bank_close.setCursor(Qt.PointingHandCursor)
        self.b_bank_close.setMinimumWidth(0)
        self.b_bank_close.clicked.connect(self._do_bank_close)
        brow.addWidget(self.b_bank_close)
        bl.addLayout(brow)
        lay.addWidget(bank)

        # --- чужой QR: одна строка ---
        pay = QFrame()
        pay.setObjectName("Card")
        pl = QVBoxLayout(pay)
        pl.setContentsMargins(16, 12, 16, 12)
        pl.setSpacing(8)
        pl.addWidget(self._sec("ЧУЖОЙ QR"))
        self.pay_row = QHBoxLayout()
        self.pay_row.setSpacing(8)
        self.in_pay = QLineEdit()
        self.in_pay.setPlaceholderText("Ссылка на QR…")
        self.in_pay.setMinimumWidth(0)
        self.in_pay.returnPressed.connect(self._do_pay)
        self.pay_row.addWidget(self.in_pay, 1)
        self.b_pay = QPushButton("Оплатить")
        self.b_pay.setCursor(Qt.PointingHandCursor)
        self.b_pay.setMinimumWidth(0)
        self.b_pay.clicked.connect(self._do_pay)
        self.pay_row.addWidget(self.b_pay)
        pl.addLayout(self.pay_row)
        lay.addWidget(pay)

        # --- очередь и журнал: сегмент-переключатель + одна карточка ---
        seg = QHBoxLayout()
        seg.setSpacing(8)
        self.seg_grp = QButtonGroup(self)
        self.seg_grp.setExclusive(True)
        self.tab_queue = QPushButton("Очередь")
        self.tab_log = QPushButton("Журнал")
        for i, b in enumerate((self.tab_queue, self.tab_log)):
            b.setObjectName("NavBtn")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(0)
            self.seg_grp.addButton(b, i)
        self.tab_queue.setChecked(True)
        self.tab_queue.clicked.connect(lambda: self._seg(0))
        self.tab_log.clicked.connect(lambda: self._seg(1))
        seg.addWidget(self.tab_queue)
        seg.addWidget(self.tab_log)
        seg.addStretch()
        self.q_sub = QLabel("")
        self.q_sub.setObjectName("Hint")
        seg.addWidget(self.q_sub)
        self.btn_clear_log = QPushButton("Очистить")
        self.btn_clear_log.setFixedHeight(28)
        self.btn_clear_log.setCursor(Qt.PointingHandCursor)
        self.btn_clear_log.setMinimumWidth(0)
        self.btn_clear_log.clicked.connect(lambda: self.log.clear())
        self.btn_clear_log.setVisible(False)
        seg.addWidget(self.btn_clear_log)
        lay.addLayout(seg)

        data = QFrame()
        data.setObjectName("Card")
        data.setMinimumHeight(280)
        dl = QVBoxLayout(data)
        dl.setContentsMargins(6, 6, 6, 6)
        dl.setSpacing(0)
        self.data_stack = QStackedWidget()

        qtab = QWidget()
        ql = QVBoxLayout(qtab)
        ql.setContentsMargins(8, 6, 8, 6)
        ql.setSpacing(0)
        self.tbl = QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["RUB", "USDT", "БРЕНД", "QR"])
        self.tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl.setShowGrid(False)
        self.tbl.horizontalHeader().setMinimumSectionSize(36)
        self.tbl.setMinimumWidth(0)
        ql.addWidget(self.tbl)
        self.data_stack.addWidget(qtab)

        ltab = QWidget()
        ll = QVBoxLayout(ltab)
        ll.setContentsMargins(8, 6, 8, 6)
        ll.setSpacing(0)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setMinimumHeight(180)
        self.log.setStyleSheet("font-family: 'JetBrains Mono', 'IBM Plex Mono', Menlo, Consolas, monospace; font-size: 12px; border: none; background: #ffffff; color: #101010;")
        ll.addWidget(self.log)
        self.data_stack.addWidget(ltab)

        dl.addWidget(self.data_stack)
        lay.addWidget(data)

        lay.addStretch()
        sc.setWidget(body)
        ol.addWidget(sc)
        return outer

    def _seg(self, i: int) -> None:
        self.data_stack.setCurrentIndex(i)
        self.q_sub.setVisible(i == 0)
        self.btn_clear_log.setVisible(i == 1)

    def _hint(self, s: str) -> QLabel:
        l = QLabel(s)
        l.setObjectName("Hint")
        return l

    def _stat(self, title: str) -> QLabel:
        c = QFrame()
        c.setObjectName("Card")
        c.setMinimumWidth(0)
        v = QVBoxLayout(c)
        v.setContentsMargins(14, 8, 14, 8)
        v.setSpacing(0)
        k = QLabel(title.upper())
        k.setObjectName("StatK")
        v.addWidget(k)
        val = QLabel("—")
        val.setObjectName("StatV")
        v.addWidget(val)
        self.stats_grid.addWidget(c, 0, len(self.stat_cards))
        self.stat_cards.append(c)
        return val

    def resizeEvent(self, ev) -> None:  # type: ignore
        super().resizeEvent(ev)
        self._relayout()

    def _relayout(self) -> None:
        if not hasattr(self, "stats_grid") or not hasattr(self, "sidebar"):
            return
        if not hasattr(self, "pay_row") or not hasattr(self, "tbl"):
            return
        # авто-складывание сайдбара на узких окнах
        if not self._sb_manual:
            want = self.width() < 920
            if want != self._sb_collapsed:
                self._set_sb_collapsed(want)
        avail = self.width() - (64 if self._sb_collapsed else 248) - 40
        scols = 4 if avail >= 720 else 2 if avail >= 400 else 1
        if scols != self._stats_cols:
            self._stats_cols = scols
            for i, card in enumerate(self.stat_cards):
                self.stats_grid.addWidget(card, i // scols, i % scols)
            for c in range(scols):
                self.stats_grid.setColumnStretch(c, 1)
        acols = 3 if avail >= 520 else 1
        if acols != self._act_cols:
            self._act_cols = acols
            for w in (self.b_cancel, self.cmb_reason, self.b_unhold):
                self.act_grid.removeWidget(w)
            if acols == 3:
                self.act_grid.addWidget(self.b_cancel, 0, 0)
                self.act_grid.addWidget(self.cmb_reason, 0, 1)
                self.act_grid.addWidget(self.b_unhold, 0, 2)
            else:
                self.act_grid.addWidget(self.b_cancel, 0, 0)
                self.act_grid.addWidget(self.cmb_reason, 1, 0)
                self.act_grid.addWidget(self.b_unhold, 2, 0)
        # оплата QR: в столбик на узких
        stack = avail < 420
        if stack != self._pay_stacked:
            self._pay_stacked = stack
            self.pay_row.removeWidget(self.in_pay)
            self.pay_row.removeWidget(self.b_pay)
            if stack:
                self.pay_row.addWidget(self.in_pay)
                self.pay_row.addWidget(self.b_pay)
            else:
                self.pay_row.addWidget(self.in_pay, 1)
                self.pay_row.addWidget(self.b_pay)
        # таблица: прячем колонку QR на узких, иначе текст давится
        hide_qr = avail < 480
        if hide_qr != self._qr_hidden:
            self._qr_hidden = hide_qr
            self.tbl.setColumnHidden(3, hide_qr)

    # ================= настройки =================
    def _settings_page(self) -> QWidget:
        outer = QWidget()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(16)

        head = QHBoxLayout()
        t = QLabel("Настройки")
        t.setObjectName("Title")
        head.addWidget(t)
        head.addStretch()
        b_check = QPushButton("Проверить ключ")
        b_check.setCursor(Qt.PointingHandCursor)
        b_check.clicked.connect(self._tool("getMe"))
        head.addWidget(b_check)
        lay.addLayout(head)
        lay.addWidget(self._hint("Настройки хранятся в файле .env рядом с программой. После сохранения бот подхватит их сам."))

        self.ed: dict[str, QLineEdit] = {}

        lay.addWidget(self._group("Доступ", "Ключ виден один раз при создании — вставь сюда.",
            [("CRBOT_API_KEY", "API-ключ", "cbak-prod-…", True, "Мини-аппа Crypto Bot → API → создать ключ")]))
        lay.addWidget(self._group("Фильтры очереди", "Какие заказы брать. 0 = без лимита.",
            [("MIN_RUB", "Мин. сумма, RUB", "500", False, ""),
             ("MAX_RUB", "Макс. сумма, RUB", "15000", False, ""),
             ("MIN_REWARD", "Мин. награда, USDT", "0.05", False, ""),
             ("MAX_TOTAL_RUB", "Лимит оборота, RUB", "10000", False, "Бот встанет на паузу, сброс — клавиша R"),
             ("BRAND_BAN", "Не брать бренды", "winline, 1xbet", False, "Через запятую, точные имена из очереди"),
             ("MCC_BAN", "Не брать MCC", "5812, 7995", False, "Через запятую")]))
        lay.addWidget(self._group("Работа", "Счёт, авто-режим и скорость.",
            [("ACCOUNT_ID", "Счёт для завершения", "", False, "Пусто = первый со счёта с can_complete. Кнопка getAccounts ниже покажет ID"),
             ("AUTO_DELAY_SEC", "Пауза перед автозавершением, сек", "20", False, "Только режим «Авто». Минимум 5 — раньше будет NotPaid и штраф"),
             ("WS_CONNS", "Параллельных подключений", "3", False, "1–5. Больше = раньше видим заказы")]))
        lay.addWidget(self._group("Автобанк · Альфа (только Авто)", "Браузер платит сам. В Тест/Ловля не работает.",
            [("AUTOPAY_ENABLED", "Автоплатеж в Авто (1/0)", "1", False, "1 = после take платить в браузере и завершать только по успеху, иначе отмена"),
             ("ALFA_URL", "Ссылка на банк", "https://web.alfabank.ru/dashboard/", False, "Человек один раз логинится — сессия живёт в ./chrome-profile 24/7"),
             ("AUTOPAY_TIMEOUT_SEC", "Таймаут оплаты, сек", "120", False, "Сколько ждать подтверждения банка, потом отмена"),
             ("AUTOBANK_DRY", "Сухой прогон (1/0)", "0", False, "1 = ищет кнопки и заполняет, но «Оплатить» НЕ жмёт (тест на твоём QR)")]))

        b_save = QPushButton("Сохранить")
        b_save.setObjectName("Primary")
        b_save.setCursor(Qt.PointingHandCursor)
        b_save.setMinimumHeight(48)
        b_save.clicked.connect(self._save)
        lay.addWidget(b_save)

        tools = QFrame()
        tools.setObjectName("Card")
        tl = QVBoxLayout(tools)
        tl.setContentsMargins(20, 18, 20, 18)
        tl.setSpacing(10)
        tl.addWidget(self._sec("ПРОВЕРКА API — нажми, ответ появится ниже"))
        r = QHBoxLayout()
        r.setSpacing(8)
        for name in ("getMe", "getStatus", "getAccounts", "pending"):
            b = QPushButton(name)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(0)
            b.clicked.connect(self._tool(name))
            r.addWidget(b)
        r.addStretch()
        tl.addLayout(r)
        self.out = QTextEdit()
        self.out.setReadOnly(True)
        self.out.setPlaceholderText("Здесь появится ответ API в формате JSON…")
        self.out.setFixedHeight(140)
        self.out.setStyleSheet("font-family: 'JetBrains Mono', 'IBM Plex Mono', Menlo, Consolas, monospace; font-size: 12px; color: #101010;")
        tl.addWidget(self.out)
        lay.addWidget(tools)

        lay.addStretch()
        sc.setWidget(body)
        ol.addWidget(sc)
        return outer

    def _group(self, title: str, hint: str, fields: list) -> QFrame:
        g = QFrame()
        g.setObjectName("Card")
        lay = QVBoxLayout(g)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        lay.addWidget(self._sec(title.upper()))
        lay.addWidget(self._hint(hint))
        for key, label, ph, secret, fhint in fields:
            fl = QLabel(label)
            fl.setObjectName("FieldLabel")
            lay.addWidget(fl)
            e = QLineEdit()
            if secret:
                e.setEchoMode(QLineEdit.Password)
            e.setText(os.environ.get(key, ""))
            e.setPlaceholderText(ph)
            e.setMinimumHeight(40)
            self.ed[key] = e
            lay.addWidget(e)
            if fhint:
                h = QLabel(fhint)
                h.setObjectName("FieldHint")
                h.setWordWrap(True)
                lay.addWidget(h)
        return g

    def _shortcuts(self) -> None:
        for key, fn in (("C", self._do_complete), ("X", self._do_cancel),
                        ("U", self._do_unhold), ("R", self._do_reset_spent)):
            QShortcut(QKeySequence(key), self, activated=fn)

    # ================= запуск / стоп =================
    def _toggle(self) -> None:
        if self.running:
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        if not self.cfg.api_key:
            QMessageBox.critical(self, "Нет ключа", "Вставь API-ключ во вкладке «Настройки».")
            self.pages.setCurrentIndex(1)
            self.nav_set.setChecked(True)
            return
        self.stop_ev = threading.Event()
        self.holder.clear()
        ev, holder, mode = self.stop_ev, self.holder, self.mode

        def run() -> None:
            try:
                p2c.run_merchant(self.cfg, self.api, mode, stop_event=ev, ui_holder=holder)
            except Exception as e:
                holder["error"] = str(e)
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()
        self.running = True
        self._refresh_go()
        for b in self.mode_btns.buttons():
            b.setEnabled(False)
        self.b_bank.setEnabled(self.mode == p2c.MODE_AUTO)
        self._log(f"Запущено: {p2c.mode_name(mode)}.")
        if mode == p2c.MODE_AUTO and os.environ.get("AUTOPAY_ENABLED", "").strip().lower() in ("1", "true", "yes", "on"):
            self._log("Авто: открываю браузер с Альфой…")
            threading.Thread(target=self._ex_bank_open, daemon=True).start()

    def _stop(self) -> None:
        if self.stop_ev is not None:
            self.stop_ev.set()
        self.running = False
        self.holder.pop("st", None)
        self._queue_keys = []
        self._refresh_go()
        for b in self.mode_btns.buttons():
            b.setEnabled(True)
        self._set_mode(self.mode)  # вернуть подпись/доступность кнопки браузера
        self.dot.setStyleSheet("color: #999999;")
        self._set("st_txt", self.st_txt, "Остановлен")
        self._set("st_sub", self.st_sub, "нажми «Запустить»")
        self._log("Остановлено.")

    # ================= синк =================
    def _set(self, key: str, w: QLabel, txt: str) -> None:
        if self._last.get(key) != txt:
            w.setText(txt)
            self._last[key] = txt

    def _sync(self) -> None:
        st: Optional[p2c.UiState] = self.holder.get("st")  # type: ignore
        if err := self.holder.pop("error", None):
            self._log(f"Ошибка: {err}")
            self._stop()
            return
        if st is None:
            return
        with st.mu:
            seen, taken, done = st.seen, st.taken, st.done
            spent, ws_ok, ws_n = st.spent_rub, st.ws_ok, st.ws_n
            avg = st.take_avg_ms()
            errs, r429 = st.errs, st.r429
            has_act, act = st.has_active, dict(st.active)
            auto_n = st.auto_count
            paused = st.paused_note
            q = list(st.queue)
            logs = list(st.logs)
            st.logs.clear()
        lim = "без лимита" if self.cfg.max_total_rub <= 0 else f"из {int(self.cfg.max_total_rub)}"
        self._set("seen", self.v_seen, str(seen))
        self._set("taken", self.v_take, str(taken))
        self._set("done", self.v_done, str(done))
        self._set("turn", self.v_turn, f"{int(spent)} {lim}" if lim != "без лимита" else str(int(spent)))
        self._set("err", self.lbl_err, f"ошибок {errs} · 429: {r429}" if (errs or r429) else "")
        self._set("qtab", self.tab_queue, f"Очередь ({len(q)})" if q else "Очередь")
        if q:
            sub = f"В очереди: {len(q)}" + (f" · пинг {avg} мс" if avg else "")
        elif self.running:
            sub = "Слушаю биржу — заказы появятся здесь"
        else:
            sub = "Очередь пуста — нажми «Запустить»"
        self._set("qsub", self.q_sub, sub)

        if ws_ok:
            self.dot.setStyleSheet("color: #101010;")
            self._set("st_txt", self.st_txt, "В работе")
            self._set("st_sub", self.st_sub, f"подключено ×{ws_n}")
        else:
            self.dot.setStyleSheet("color: #999999;")
            self._set("st_txt", self.st_txt, "Подключение…")
            self._set("st_sub", self.st_sub, "переподключение")

        if has_act and act:
            info = f"#{act.get('payment_id', '?')} · {act.get('in_amount')} RUB → {act.get('out_amount')} USDT"
            rw = act.get("reward_amount")
            if rw:
                info += f" · +{rw}"
            if self.mode == p2c.MODE_AUTO and auto_n > 0:
                info += f" · авто через {auto_n} с"
            self._set("act", self.lbl_active, info)
        elif paused:
            self._set("act", self.lbl_active, paused)
        else:
            self._set("act", self.lbl_active, "Нет активного заказа.")

        # --- автобанк-статус (из worker или локального драйвера) ---
        bank_txt = ""
        try:
            with st.mu:
                bank_txt, bank_busy = st.bank_status, st.bank_busy
        except Exception:
            bank_txt, bank_busy = "", False
        if not bank_txt:
            try:
                import autobank as _ab
                s = _ab.status()
                bank_txt = ("платит…" if s.get("busy") else s.get("status", "закрыт"))
                if s.get("last") and "ошибка" in str(bank_txt):
                    bank_txt += f": {p2c.cut(s['last'], 50)}"
            except Exception:
                bank_txt = "закрыт"
        auto_on = os.environ.get("AUTOPAY_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")
        if self.mode != p2c.MODE_AUTO:
            self._set("bank", self.lbl_bank, "Автобанк спит — работает только в режиме Авто.")
        elif bank_busy or "плач" in str(bank_txt):
            self._set("bank", self.lbl_bank, f"Автобанк: {bank_txt} — не трогай окно, идёт оплата.")
        elif auto_on and self.running:
            self._set("bank", self.lbl_bank, f"Автобанк: {bank_txt} · автоплатеж ВКЛ — ордера оплачиваются сами.")
        else:
            self._set("bank", self.lbl_bank,
                      f"Автобанк: {bank_txt} · автоплатеж ВЫКЛ (включи AUTOPAY_ENABLED=1 в Настройках)." if self.running
                      else f"Автобанк: {bank_txt}. Открой браузер и залогинься в Альфу до старта.")

        keys = [str(o.get("qr_id")) for o in q]
        if keys != self._queue_keys:
            self._queue_keys = keys
            self.tbl.setUpdatesEnabled(False)
            self.tbl.setRowCount(len(q))
            for i, o in enumerate(q):
                for j, k in enumerate(("in_amount", "reward_amount", "brand_name", "qr_id")):
                    raw = str(o.get(k, ""))
                    txt = p2c.cut(raw, 12) if k == "qr_id" else raw
                    it = QTableWidgetItem(txt)
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                    if txt != raw:
                        it.setToolTip(raw)
                    self.tbl.setItem(i, j, it)
            self.tbl.setUpdatesEnabled(True)

        for lg in logs:
            self.log.appendPlainText(f"{lg.at} {lg.text}")

    # ================= действия =================
    def _active_id(self) -> int:
        if os.path.exists("active.id"):
            try:
                with open("active.id") as f:
                    return int(f.read().strip() or 0)
            except Exception:
                pass
        st = self.holder.get("st")
        if st is not None and getattr(st, "active", None):
            try:
                return int(st.active.get("payment_id", 0))
            except Exception:
                pass
        return 0

    def _need_active(self) -> int:
        pid = self._active_id()
        if not pid:
            QMessageBox.information(self, "Нет заказа", "Активного заказа сейчас нет — нечего завершать.")
        return pid

    def _do_complete(self) -> None:
        if self._need_active() == 0:
            return
        if QMessageBox.question(self, "Завершить?",
                "Рубли по QR реально оплачены в банке?\nРаннее завершение = NotPaid и штраф.",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        threading.Thread(target=self._ex_complete, daemon=True).start()

    def _ex_complete(self) -> None:
        pid = self._active_id()
        acc = self.cfg.account_id
        if not acc:
            try:
                for a in self.api.get_accounts():
                    if a.get("can_complete"):
                        acc = str(a.get("id"))
                        break
            except Exception as e:
                self.log_sig.emit(f"complete: не дал счета: {e}")
                return
        if not acc:
            self.log_sig.emit("complete: нет счёта с can_complete=true")
            return
        try:
            p = self.api.complete_payment(pid, acc)
            self.log_sig.emit(f"Готово: ордер #{p.get('payment_id')} завершён.")
            try:
                os.remove("active.id")
            except OSError:
                pass
        except Exception as e:
            self.log_sig.emit(f"complete fail: {e}")

    def _do_cancel(self) -> None:
        if self._need_active() == 0:
            return
        reason = self.cmb_reason.currentText()
        if QMessageBox.question(self, "Отменить?", f"Отменить ордер ({reason})?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        threading.Thread(target=self._ex_cancel, args=(reason,), daemon=True).start()

    def _ex_cancel(self, reason: str) -> None:
        pid = self._active_id()
        try:
            p = self.api.cancel_payment(pid, reason)
            self.log_sig.emit(f"Отменён #{p.get('payment_id')} ({reason}).")
            try:
                os.remove("active.id")
            except OSError:
                pass
        except Exception as e:
            self.log_sig.emit(f"cancel fail: {e}")

    def _do_unhold(self) -> None:
        try:
            os.remove("active.id")
        except OSError:
            pass
        st = self.holder.get("st")
        if st is not None:
            with st.mu:
                st.has_active = False
                st.paused_note = ""
        self._log("Сброшено — ловлю дальше.")

    def _do_reset_spent(self) -> None:
        p2c.save_spent_rub(0.0)
        st = self.holder.get("st")
        if st is not None:
            with st.mu:
                st.spent_rub = 0.0
                st.paused_note = ""
        self._log("Оборот сброшен.")

    def _do_pay(self) -> None:
        url = self.in_pay.text().strip()
        if not url:
            QMessageBox.warning(self, "QR", "Вставь ссылку на QR.")
            return
        self.in_pay.clear()
        threading.Thread(target=self._ex_pay, args=(url,), daemon=True).start()

    def _ex_pay(self, payload: str) -> None:
        self.log_sig.emit(f"QR: считаю {p2c.cut(payload, 28)}…")
        rc = p2c.run_pay(self.cfg, self.api, payload)
        self.log_sig.emit("QR оплачен." if rc == 0 else "QR не оплачен — смотри журнал.")

    # ================= автобанк (только Авто) =================
    def _do_bank_open(self) -> None:
        if self.mode != p2c.MODE_AUTO:
            QMessageBox.information(self, "Только Авто",
                "Автобанк работает только в режиме Авто.\nПереключи режим — в Тест/Ловля браузер не запускаю.")
            return
        self._log("Открываю Chrome с Альфой…")
        threading.Thread(target=self._ex_bank_open, daemon=True).start()

    def _ex_bank_open(self) -> None:
        try:
            import autobank
        except ImportError:
            self.log_sig.emit("Нет selenium: ./.venv/bin/pip install -r requirements.txt")
            return
        try:
            url = os.environ.get("ALFA_URL", "").strip() or "https://web.alfabank.ru/dashboard/"
            ok, msg = autobank.ensure_browser(log=lambda m: self.log_sig.emit(f"Банк: {m}"), alfa_url=url)
            st = self.holder.get("st")
            if st is not None:
                with st.mu:
                    st.bank_status = "открыт" if ok else f"ошибка: {p2c.cut(msg, 60)}"
                    st.bank_busy = False
            self.log_sig.emit(f"Банк: {msg}. Залогинься один раз — дальше сам." if ok else f"Банк: {msg}")
        except Exception as e:
            self.log_sig.emit(f"Банк: {e}")

    def _do_bank_close(self) -> None:
        try:
            import autobank
            autobank.close_browser()
        except Exception:
            pass
        st = self.holder.get("st")
        if st is not None:
            with st.mu:
                st.bank_status = "закрыт"
                st.bank_busy = False
        self._log("Браузер закрыт.")

    def _tool(self, name: str):  # type: ignore
        def go() -> None:
            def work() -> None:
                try:
                    if name == "getMe":
                        r = self.api.get_me()
                    elif name == "getStatus":
                        r = self.api.get_status()
                    elif name == "getAccounts":
                        r = self.api.get_accounts()
                    else:
                        r = self.api.get_pending_actions()
                    txt = json.dumps(r, ensure_ascii=False, indent=2)[:4000]
                except Exception as e:
                    txt = f"Ошибка: {e}"
                self.api_out.emit(txt)
            threading.Thread(target=work, daemon=True).start()
        return go

    def _save(self) -> None:
        lines = []
        for k, w in self.ed.items():
            v = w.text().strip()
            os.environ[k] = v
            lines.append(f"{k}={v}\n")
        try:
            with open(".env", "w", encoding="utf-8") as f:
                f.writelines(lines)
            self.cfg = p2c.load_config()
            self.api = p2c.Client(self.cfg)
            QMessageBox.information(self, "Готово", "Сохранено в .env.")
            self._log("Настройки сохранены.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def _log(self, msg: str) -> None:
        self.log.appendPlainText(f"{datetime.now().strftime('%H:%M:%S')} {msg}")

    def _on_api_out(self, txt: str) -> None:
        self.out.setText(txt)

    def _on_log_sig(self, msg: str) -> None:
        self._log(msg)

    def closeEvent(self, ev) -> None:  # type: ignore
        if self.running and self.stop_ev is not None:
            self.stop_ev.set()
        ev.accept()


def main() -> None:
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
