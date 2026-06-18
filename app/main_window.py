# -*- coding: utf-8 -*-
import sys
import os
import time
import datetime
import numpy as np
import can
import cantools
import pyqtgraph as pg

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QMessageBox, QFileDialog,
    QTableWidgetItem, QListWidgetItem, QDoubleSpinBox, QComboBox,
)
from PyQt5.QtCore import Qt, QTimer

from .ui_layout import setup_ui
from .can_worker import CanWorker
from .replay_worker import ReplayWorker

# CAN-FD presets for PCAN-USB FD (80 MHz clock, SAE J2284-4 timings)
# Verified working via pcan_test.py: timing=can.BitTimingFd(...) is required
_FD_PRESETS = {
    '500k/2M': can.BitTimingFd(
        f_clock=80_000_000,
        nom_brp=2,  nom_tseg1=63,  nom_tseg2=16, nom_sjw=16,
        data_brp=2, data_tseg1=15, data_tseg2=4,  data_sjw=4,
    ),
    '500k/1M': can.BitTimingFd(
        f_clock=80_000_000,
        nom_brp=2,  nom_tseg1=63,  nom_tseg2=16, nom_sjw=16,
        data_brp=2, data_tseg1=31, data_tseg2=8,  data_sjw=8,
    ),
    '250k/2M': can.BitTimingFd(
        f_clock=80_000_000,
        nom_brp=2,  nom_tseg1=127, nom_tseg2=32, nom_sjw=32,
        data_brp=2, data_tseg1=15, data_tseg2=4,  data_sjw=4,
    ),
    '250k/500k': can.BitTimingFd(
        f_clock=80_000_000,
        nom_brp=2,  nom_tseg1=127, nom_tseg2=32, nom_sjw=32,
        data_brp=2, data_tseg1=63, data_tseg2=16, data_sjw=16,
    ),
    '1M/4M': can.BitTimingFd(
        f_clock=80_000_000,
        nom_brp=1,  nom_tseg1=63,  nom_tseg2=16, nom_sjw=16,
        data_brp=1, data_tseg1=15, data_tseg2=4,  data_sjw=4,
    ),
}

def _fmt_hex(data_bytes, per_line=8):
    """Format bytes as uppercase hex, `per_line` bytes per row."""
    rows = [data_bytes[i:i+per_line] for i in range(0, len(data_bytes), per_line)]
    return '\n'.join(' '.join('%02X' % b for b in row) for row in rows)


MAX_MONITOR_ROWS = 1000
MAX_WAVE_POINTS = 5000
WAVE_COLORS = ['#ff4444', '#44ff44', '#4488ff', '#ffaa00', '#ff44ff',
               '#00ffff', '#ff8800', '#88ff00', '#0088ff', '#ff0088']


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        setup_ui(self)
        self._init_state()
        self._connect_signals()

    # ─────────────────────────────────────────────────────────────────────────
    def _init_state(self):
        self.db = None
        self.can_worker = None
        self.replay_worker = None
        self._capture_logger = None
        self._period_timer = None
        self._color_idx = 0

        # {sig_name: {'t': np.zeros(MAX_WAVE_POINTS), 'v': np.zeros(MAX_WAVE_POINTS), 'ptr': 0}}
        self.sig_buffers = {}
        # {sig_name: pg.PlotDataItem}
        self.sig_curves = {}
        # {sig_name: pg.PlotDataItem}  for replay tab
        self.replay_curves = {}

        self.start_time = time.time()

        # throttle monitor table redraws
        self._pending_raw_frames = []
        self._monitor_timer = QTimer(self)
        self._monitor_timer.timeout.connect(self._flush_monitor)
        self._monitor_timer.start(100)  # 10 Hz

        # {arb_id: {sig_name: value_str}}  for DBC decode column aggregation
        self._dbc_decode_cache = {}  # {arb_id: {sig_name: float_val}}
        self._sig_to_arb_id = {}     # {sig_name: arb_id}  built on DBC load
        self._replay_t0 = None       # first timestamp of current replay session
        self._dbc_send_values = {}   # {frame_id: {sig_name: float}}  persisted spin values
        self._current_dbc_frame_id = None  # frame_id of message currently shown in sig table
        self._raw_sync = False       # re-entrancy guard for raw hex ↔ struct sync
        self._raw_period_timer = None
        self._parse_dbc_list = []    # [{'name', 'file', 'path', 'db'}]  parse-tab DBCs
        self._parse_log_path = None
        self._parse_frame_data = []  # [(phys_dict, raw_dict, sig_map)] per frame row

    # ─────────────────────────────────────────────────────────────────────────
    # Channel presets keyed by python-can interface name
    _CHANNEL_PRESETS = {
        'pcan':          ['PCAN_USBBUS1', 'PCAN_USBBUS2', 'PCAN_USBBUS3', 'PCAN_USBBUS4',
                          'PCAN_PCIBUS1', 'PCAN_PCIBUS2'],
        'slcan':         ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0', 'COM3', 'COM4', 'COM5'],
        'socketcan':     ['vcan0', 'can0', 'can1', 'can2'],
        'virtual':       ['test', 'vbus0'],
        'kvaser':        ['0', '1', '2'],
        'ixxat':         ['0', '1', '2'],
        'vector':        ['0', '1', '2'],
        'gs_usb':        ['0', '1'],
        'canalystii':    ['0', '1'],
        'nican':         ['CAN0', 'CAN1'],
        'systec':        ['0', '1'],
        'neovi':         ['0', '1'],
        'usb2can':       ['0', '1'],
        'cantact':       ['/dev/ttyACM0', '/dev/ttyUSB0', 'COM3'],
        'robotell':      ['/dev/ttyUSB0', 'COM3'],
        'seeedstudio':   ['0', '1'],
        'udp_multicast': ['239.0.0.1', '239.0.0.2'],
    }

    def _connect_signals(self):
        self.fd_check.toggled.connect(lambda v: self.fd_preset_combo.setEnabled(v))
        self.fd_check.toggled.connect(lambda v: self.bitrate_combo.setDisabled(v))
        self.interface_combo.currentTextChanged.connect(self._on_interface_changed)
        self._on_interface_changed(self.interface_combo.currentText())
        self.connect_btn.clicked.connect(self._on_connect_toggle)
        self.dbc_btn.clicked.connect(self._on_load_dbc)
        self.monitor_clear_btn.clicked.connect(self._on_monitor_clear)
        self.raw_monitor_check.toggled.connect(self._on_raw_monitor_toggled)
        self.wave_clear_btn.clicked.connect(self._on_wave_clear)
        self.dbc_msg_list.currentItemChanged.connect(self._on_dbc_msg_list_changed)
        self.dbc_select_all_btn.clicked.connect(self._on_dbc_select_all)
        self.dbc_deselect_all_btn.clicked.connect(self._on_dbc_deselect_all)
        self.dbc_send_once_btn.clicked.connect(self._on_dbc_send_once)
        self.dbc_period_start_btn.clicked.connect(self._on_dbc_period_start)
        self.dbc_period_stop_btn.clicked.connect(self._on_dbc_period_stop)
        self.raw_send_btn.clicked.connect(self._on_raw_send)
        self.raw_period_start_btn.clicked.connect(self._on_raw_period_start)
        self.raw_period_stop_btn.clicked.connect(self._on_raw_period_stop)
        # raw hex ↔ struct bidirectional sync
        self.raw_id_edit.textChanged.connect(self._on_raw_fields_changed)
        self.raw_data_edit.textChanged.connect(self._on_raw_fields_changed)
        self.raw_ext_check.toggled.connect(self._on_raw_fields_changed)
        self.raw_fd_check.toggled.connect(self._on_raw_fd_toggled)   # auto-fill data length
        self.raw_fd_check.toggled.connect(self._on_raw_fields_changed)
        self.raw_brs_check.toggled.connect(self._on_raw_fields_changed)
        self.raw_struct_edit.textChanged.connect(self._on_raw_struct_changed)
        self._on_raw_fields_changed()   # populate struct field from defaults
        self.capture_start_btn.clicked.connect(self._on_capture_start)
        self.capture_stop_btn.clicked.connect(self._on_capture_stop)
        self.signal_list.itemChanged.connect(lambda _: self._refresh_wave_curves())
        self.wave_search_edit.textChanged.connect(self._on_wave_search)
        self.parse_add_dbc_btn.clicked.connect(self._on_parse_add_dbc)
        self.parse_remove_dbc_btn.clicked.connect(self._on_parse_remove_dbc)
        self.parse_clear_dbc_btn.clicked.connect(self._on_parse_clear_dbc)
        self.parse_btn.clicked.connect(self._on_parse_frame)
        self.parse_from_monitor_btn.clicked.connect(self._on_parse_from_monitor)
        self.parse_raw_btn.clicked.connect(self._on_parse_raw_line)
        self.parse_open_log_btn.clicked.connect(self._on_parse_open_log)
        self.parse_file_btn.clicked.connect(self._on_parse_file)
        self.parse_frame_table.currentCellChanged.connect(
            lambda r, c, pr, pc: self._on_parse_row_selected(r))
        self.replay_open_btn.clicked.connect(self._on_replay_open)
        self.replay_play_btn.clicked.connect(self._on_replay_play)
        self.replay_pause_btn.clicked.connect(self._on_replay_pause)
        self.replay_stop_btn.clicked.connect(self._on_replay_stop)


    def _on_interface_changed(self, iface):
        presets = self._CHANNEL_PRESETS.get(iface, ['0'])
        current = self.channel_combo.currentText()
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        self.channel_combo.addItems(presets)
        # restore user's previous value if it makes sense, else use first preset
        if current in presets:
            self.channel_combo.setCurrentText(current)
        self.channel_combo.blockSignals(False)

    # ── CAN connection ────────────────────────────────────────────────────────
    def _on_connect_toggle(self):
        if self.can_worker is not None:
            self._disconnect_can()
        else:
            self._connect_can()

    def _connect_can(self):
        iface = self.interface_combo.currentText()
        ch = self.channel_combo.currentText()
        br = int(self.bitrate_combo.currentText())
        # CAN-FD: use BitTimingFd preset; classic CAN: timing=None
        timing = None
        if self.fd_check.isChecked():
            preset_name = self.fd_preset_combo.currentText()
            timing = _FD_PRESETS.get(preset_name)
        # disable button immediately to prevent multiple clicks
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText('连接中...')
        self.status_label.setText('状态: 正在连接...')
        self.can_worker = CanWorker(iface, ch, br, timing=timing)
        self.can_worker.connection_status_changed.connect(self._on_can_status)
        self.can_worker.raw_frame_received.connect(self._on_raw_frames)
        self.can_worker.decoded_received.connect(self._on_decoded_batch)
        self.can_worker.dbc_signals_updated.connect(self._on_dbc_signals_updated)
        self.can_worker.enable_raw_monitor = self.raw_monitor_check.isChecked()
        if self.db:
            self.can_worker.set_dbc(self.db)
        self.can_worker.start()
        self.start_time = time.time()

    def _disconnect_can(self):
        if self._period_timer:
            self._on_dbc_period_stop()
        if self._raw_period_timer:
            self._on_raw_period_stop()
        if self._capture_logger:
            self._on_capture_stop()
        self.connect_btn.setEnabled(False)
        self.can_worker.stop()
        self.can_worker.wait(3000)
        self.can_worker = None
        self._set_btn_open()
        self.status_label.setText('状态: 未连接')

    def _set_btn_open(self):
        self.connect_btn.setText('打开 CAN')
        self.connect_btn.setStyleSheet('QPushButton { background: #2a6; color: white; font-weight: bold; }')
        self.connect_btn.setEnabled(True)

    def _set_btn_close(self):
        self.connect_btn.setText('关闭 CAN')
        self.connect_btn.setStyleSheet('QPushButton { background: #a33; color: white; font-weight: bold; }')
        self.connect_btn.setEnabled(True)

    def _on_can_status(self, ok, msg):
        self.status_label.setText('状态: ' + msg)
        if ok:
            self._set_btn_close()
        else:
            self.can_worker = None
            self._set_btn_open()
            QMessageBox.critical(self, 'CAN 连接失败', msg +
                '\n\n提示: 若使用普通 CAN（非 FD）设备，请取消勾选 CAN-FD 复选框后重试。')

    # ── DBC loading ───────────────────────────────────────────────────────────
    def _on_load_dbc(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '加载 DBC 文件', '', 'DBC Files (*.dbc);;All Files (*)',
            options=QFileDialog.DontUseNativeDialog,
        )
        if not path:
            return
        try:
            self.db = cantools.database.load_file(path)
        except Exception as e:
            QMessageBox.critical(self, 'DBC 加载失败', str(e))
            return
        name = os.path.basename(path)
        self.dbc_label.setText('DBC: ' + name)
        # build reverse map for monitor DBC-decode column
        self._sig_to_arb_id = {
            sig.name: msg.frame_id
            for msg in self.db.messages
            for sig in msg.signals
        }
        self._dbc_decode_cache.clear()
        if self.can_worker:
            self.can_worker.set_dbc(self.db)
        else:
            names = [sig.name for msg in self.db.messages for sig in msg.signals]
            self._on_dbc_signals_updated(names)
        # populate message list for send tab
        self._dbc_send_values.clear()
        self._current_dbc_frame_id = None
        self.dbc_msg_list.clear()
        for msg in sorted(self.db.messages, key=lambda m: m.frame_id):
            label = '0x%03X  %s  %dB' % (msg.frame_id, msg.name, msg.length)
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, msg.frame_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.dbc_msg_list.addItem(item)
        if self.dbc_msg_list.count():
            self.dbc_msg_list.setCurrentRow(0)

    def _on_dbc_signals_updated(self, names):
        self.signal_list.clear()
        for name in sorted(names):
            arb_id = self._sig_to_arb_id.get(name)
            label = ('0x%03X  %s' % (arb_id, name)) if arb_id is not None else name
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, name)   # actual signal name used as buffer key
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.signal_list.addItem(item)
        # reapply any active search filter
        self._on_wave_search(self.wave_search_edit.text())


    # ── Bus monitor ───────────────────────────────────────────────────────────
    def _on_raw_frames(self, frames):
        self._pending_raw_frames.extend(frames)

    def _flush_monitor(self):
        if not self._pending_raw_frames:
            return
        frames = self._pending_raw_frames
        self._pending_raw_frames = []

        filter_text = self.filter_id_edit.text().strip()
        filter_id = None
        if filter_text:
            try:
                filter_id = int(filter_text, 16)
            except ValueError:
                pass

        tbl = self.monitor_table
        tbl.setUpdatesEnabled(False)
        for frame in frames:
            # 6-tuple = RX from CanWorker; 7-tuple = TX echo (is_tx=True)
            is_tx = len(frame) > 6 and frame[6]
            ts, arb_id, data, is_ext, is_fd, brs = frame[:6]
            if filter_id is not None and arb_id != filter_id:
                continue
            t_rel = '%.3f' % (ts - self.start_time)
            id_str = '0x%X' % arb_id
            dlc_str = str(len(data))
            hex_str = ' '.join('%02X' % b for b in data)
            dbc_str = ''
            if self.db and arb_id in self._dbc_decode_cache:
                kv = self._dbc_decode_cache[arb_id]
                dbc_str = '  '.join('%s=%.3g' % (k, v) for k, v in kv.items())
            if is_tx:
                dbc_str = '[TX]' if not dbc_str else '[TX]  ' + dbc_str
            row = tbl.rowCount()
            if row >= MAX_MONITOR_ROWS:
                tbl.removeRow(0)
                row = tbl.rowCount()
            tbl.insertRow(row)
            for col, val in enumerate([t_rel, id_str, dlc_str, hex_str, dbc_str]):
                tbl.setItem(row, col, QTableWidgetItem(val))
        tbl.setUpdatesEnabled(True)
        if self.autoscroll_check.isChecked():
            tbl.scrollToBottom()

    def _on_monitor_clear(self):
        self.monitor_table.setRowCount(0)
        self._dbc_decode_cache.clear()

    def _on_raw_monitor_toggled(self, checked):
        if self.can_worker:
            self.can_worker.enable_raw_monitor = checked

    # ── Waveform ──────────────────────────────────────────────────────────────
    def _on_decoded_batch(self, batch):
        # batch: list of (sig_name, val, ts)
        for sig_name, val, ts in batch:
            # update DBC decode cache so monitor table can show signal values
            arb_id = self._sig_to_arb_id.get(sig_name)
            if arb_id is not None:
                if arb_id not in self._dbc_decode_cache:
                    self._dbc_decode_cache[arb_id] = {}
                self._dbc_decode_cache[arb_id][sig_name] = val
            self._update_wave_buffer(sig_name, val, ts)
        self._refresh_wave_curves()

    def _update_wave_buffer(self, sig_name, val, ts):
        if sig_name not in self.sig_buffers:
            self.sig_buffers[sig_name] = {
                't': np.zeros(MAX_WAVE_POINTS),
                'v': np.zeros(MAX_WAVE_POINTS),
                'ptr': 0,
            }
        b = self.sig_buffers[sig_name]
        p = b['ptr'] % MAX_WAVE_POINTS
        b['t'][p] = ts - self.start_time
        b['v'][p] = val
        b['ptr'] += 1

    def _on_wave_search(self, text):
        text = text.strip().lower()
        for i in range(self.signal_list.count()):
            item = self.signal_list.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _refresh_wave_curves(self):
        checked = set()
        for i in range(self.signal_list.count()):
            item = self.signal_list.item(i)
            if item.checkState() == Qt.Checked:
                # use stored signal name (UserRole), not the display label
                sig_name = item.data(Qt.UserRole) or item.text()
                checked.add(sig_name)

        # remove unchecked curves
        for name in list(self.sig_curves.keys()):
            if name not in checked:
                self.wave_plot.removeItem(self.sig_curves.pop(name))

        # add/update checked curves
        for name in checked:
            if name not in self.sig_buffers:
                continue
            b = self.sig_buffers[name]
            ptr = b['ptr']
            if ptr == 0:
                continue
            if ptr < MAX_WAVE_POINTS:
                t_data = b['t'][:ptr]
                v_data = b['v'][:ptr]
            else:
                idx = ptr % MAX_WAVE_POINTS
                t_data = np.roll(b['t'], -idx)
                v_data = np.roll(b['v'], -idx)

            if name not in self.sig_curves:
                color = WAVE_COLORS[self._color_idx % len(WAVE_COLORS)]
                self._color_idx += 1
                self.sig_curves[name] = self.wave_plot.plot(
                    t_data, v_data, pen=pg.mkPen(color, width=1.5), name=name)
            else:
                self.sig_curves[name].setData(t_data, v_data)

        if self.wave_auto_y_check.isChecked():
            self.wave_plot.enableAutoRange('y', True)

    def _on_wave_clear(self):
        self.sig_buffers.clear()
        for curve in self.sig_curves.values():
            self.wave_plot.removeItem(curve)
        self.sig_curves.clear()
        self._color_idx = 0
        self.start_time = time.time()


    # ── DBC send tab ──────────────────────────────────────────────────────────
    def _on_dbc_msg_list_changed(self, current, previous):
        if previous is not None:
            self._save_dbc_sig_values(previous.data(Qt.UserRole))
        if current is not None:
            frame_id = current.data(Qt.UserRole)
            self._current_dbc_frame_id = frame_id
            self._load_dbc_msg_signals(frame_id)

    def _save_dbc_sig_values(self, frame_id):
        if frame_id is None:
            return
        values = {}
        tbl = self.dbc_sig_table
        for row in range(tbl.rowCount()):
            item = tbl.item(row, 0)
            spin = tbl.cellWidget(row, 4)
            if item and spin:
                values[item.text()] = spin.value()
        self._dbc_send_values[frame_id] = values

    def _load_dbc_msg_signals(self, frame_id):
        if self.db is None or frame_id is None:
            return
        try:
            dbc_msg = self.db.get_message_by_frame_id(frame_id)
        except KeyError:
            return
        saved = self._dbc_send_values.get(frame_id, {})
        tbl = self.dbc_sig_table
        tbl.setRowCount(0)
        for sig in dbc_msg.signals:
            row = tbl.rowCount()
            tbl.insertRow(row)
            tbl.setItem(row, 0, QTableWidgetItem(sig.name))
            tbl.setItem(row, 1, QTableWidgetItem(sig.unit or ''))
            tbl.setItem(row, 2, QTableWidgetItem('%.4g' % (sig.minimum if sig.minimum is not None else 0)))
            tbl.setItem(row, 3, QTableWidgetItem('%.4g' % (sig.maximum if sig.maximum is not None else 0)))
            spin = QDoubleSpinBox()
            lo = sig.minimum if sig.minimum is not None else -1e9
            hi = sig.maximum if sig.maximum is not None else 1e9
            spin.setRange(lo, hi)
            spin.setDecimals(4)
            default = saved.get(sig.name, lo if lo > -1e8 else 0.0)
            spin.setValue(default)
            tbl.setCellWidget(row, 4, spin)
        self.dbc_fd_check.setChecked(dbc_msg.length > 8)

    def _on_dbc_select_all(self):
        for i in range(self.dbc_msg_list.count()):
            self.dbc_msg_list.item(i).setCheckState(Qt.Checked)

    def _on_dbc_deselect_all(self):
        for i in range(self.dbc_msg_list.count()):
            self.dbc_msg_list.item(i).setCheckState(Qt.Unchecked)

    def _build_dbc_frame(self, frame_id):
        if self.db is None or frame_id is None:
            return None
        try:
            dbc_msg = self.db.get_message_by_frame_id(frame_id)
        except KeyError:
            return None
        stored = self._dbc_send_values.get(frame_id, {})
        # Provide a complete values dict — cantools requires every signal to be present.
        # Use the stored value when available; fall back to minimum (or 0).
        values = {}
        for sig in dbc_msg.signals:
            if sig.name in stored:
                values[sig.name] = stored[sig.name]
            else:
                lo = sig.minimum if sig.minimum is not None else 0.0
                values[sig.name] = lo if lo > -1e8 else 0.0
        try:
            data = dbc_msg.encode(values)
        except Exception as e:
            QMessageBox.warning(self, '编码错误', '0x%X %s: %s' % (frame_id, dbc_msg.name, str(e)))
            return None
        is_fd = self.dbc_fd_check.isChecked() or dbc_msg.length > 8
        return can.Message(
            arbitration_id=dbc_msg.frame_id,
            data=data,
            is_extended_id=dbc_msg.is_extended_frame,
            is_fd=is_fd,
            bitrate_switch=is_fd,
        )

    def _on_dbc_send_once(self):
        if self.can_worker is None or self.can_worker.bus is None:
            if self._period_timer:
                self._on_dbc_period_stop()
                return
            QMessageBox.warning(self, '未连接', '请先打开 CAN 总线')
            return
        # Persist current table values before sending
        if self._current_dbc_frame_id is not None:
            self._save_dbc_sig_values(self._current_dbc_frame_id)
        # Send every checked message
        for i in range(self.dbc_msg_list.count()):
            item = self.dbc_msg_list.item(i)
            if item.checkState() != Qt.Checked:
                continue
            frame_id = item.data(Qt.UserRole)
            msg = self._build_dbc_frame(frame_id)
            if msg:
                try:
                    self.can_worker.bus.send(msg)
                    self._echo_sent_frame(msg)
                except Exception as e:
                    QMessageBox.warning(self, '发送失败', str(e))
                    break

    def _on_dbc_period_start(self):
        if self.can_worker is None or self.can_worker.bus is None:
            QMessageBox.warning(self, '未连接', '请先打开 CAN 总线')
            return
        interval_ms = self.dbc_period_spin.value()
        self._period_timer = QTimer(self)
        self._period_timer.timeout.connect(self._on_dbc_send_once)
        self._period_timer.start(interval_ms)
        self.dbc_period_start_btn.setEnabled(False)
        self.dbc_period_stop_btn.setEnabled(True)

    def _on_dbc_period_stop(self):
        if self._period_timer:
            self._period_timer.stop()
            self._period_timer = None
        self.dbc_period_start_btn.setEnabled(True)
        self.dbc_period_stop_btn.setEnabled(False)

    # ── Raw hex ↔ struct sync ─────────────────────────────────────────────────
    def _on_raw_fd_toggled(self, checked):
        """Auto-expand data to 64 bytes when FD is enabled, shrink to 8 when disabled."""
        if self._raw_sync:
            return
        try:
            current = [int(x, 16) for x in self.raw_data_edit.toPlainText().split() if x]
        except ValueError:
            current = []
        if checked and len(current) <= 8:
            padded = (current + [0] * 64)[:64]
        elif not checked and len(current) > 8:
            padded = current[:8]
        else:
            return
        self._raw_sync = True
        self.raw_data_edit.setPlainText(_fmt_hex(padded))
        self._raw_sync = False

    def _on_raw_fields_changed(self):
        """Rebuild struct description from hex fields and checkboxes (one field per line)."""
        if self._raw_sync:
            return
        self._raw_sync = True
        try:
            id_text = self.raw_id_edit.text().strip().upper() or '0'
            is_ext = self.raw_ext_check.isChecked()
            is_fd = self.raw_fd_check.isChecked()
            is_brs = self.raw_brs_check.isChecked()
            try:
                data_bytes = [int(x, 16) for x in self.raw_data_edit.toPlainText().split() if x]
            except ValueError:
                data_bytes = []
            if is_fd:
                frame_type = 'B' if is_brs else 'F'
            elif is_ext:
                frame_type = 'E'
            else:
                frame_type = 'S'
            data_hex = ''.join('%02X' % b for b in data_bytes)
            self.raw_struct_edit.setPlainText(
                'ID=%s\nType=%s\nLength=%d\nData=%s' % (
                    id_text, frame_type, len(data_bytes), data_hex))
        except Exception:
            pass
        finally:
            self._raw_sync = False

    def _on_raw_struct_changed(self):
        """Parse struct description and update hex fields + checkboxes."""
        if self._raw_sync:
            return
        self._raw_sync = True
        try:
            fields = {}
            # support both newline-separated and comma-separated key=value pairs
            text = self.raw_struct_edit.toPlainText().replace('\n', ',')
            for part in text.split(','):
                if '=' in part:
                    k, v = part.split('=', 1)
                    fields[k.strip().upper()] = v.strip()
            if 'ID' in fields:
                self.raw_id_edit.setText(fields['ID'].upper())
            if 'TYPE' in fields:
                t = fields['TYPE'].upper()
                self.raw_fd_check.setChecked(t in ('F', 'B'))
                self.raw_brs_check.setChecked(t == 'B')
                self.raw_ext_check.setChecked(t == 'E')
            if 'DATA' in fields:
                hex_str = fields['DATA'].upper()
                pairs = [hex_str[i:i+2] for i in range(0, len(hex_str), 2)
                         if len(hex_str[i:i+2]) == 2]
                try:
                    data = [int(p, 16) for p in pairs]
                    self.raw_data_edit.setPlainText(_fmt_hex(data))
                except ValueError:
                    pass
            elif 'LENGTH' in fields:
                try:
                    n = max(0, min(int(fields['LENGTH']), 64))
                    self.raw_data_edit.setPlainText(_fmt_hex([0] * n))
                except ValueError:
                    pass
        except Exception:
            pass
        finally:
            self._raw_sync = False

    def _on_raw_send(self):
        if self.can_worker is None or self.can_worker.bus is None:
            QMessageBox.warning(self, '未连接', '请先打开 CAN 总线')
            return
        msg = self._build_raw_msg()
        if msg is None:
            QMessageBox.warning(self, '错误', 'ID 或数据格式无效，请检查输入')
            return
        try:
            self.can_worker.bus.send(msg)
            self._echo_sent_frame(msg)
        except Exception as e:
            QMessageBox.warning(self, '发送失败', str(e))

    def _build_raw_msg(self):
        """Build a can.Message from the raw send fields. Returns None on parse error."""
        try:
            arb_id = int(self.raw_id_edit.text().strip(), 16)
        except ValueError:
            return None
        try:
            data = bytes(int(x, 16) for x in self.raw_data_edit.toPlainText().split() if x)
        except ValueError:
            return None
        is_fd = self.raw_fd_check.isChecked()
        return can.Message(
            arbitration_id=arb_id,
            data=data,
            is_extended_id=self.raw_ext_check.isChecked(),
            is_fd=is_fd,
            bitrate_switch=is_fd and self.raw_brs_check.isChecked(),
        )

    def _on_raw_period_start(self):
        if self.can_worker is None or self.can_worker.bus is None:
            QMessageBox.warning(self, '未连接', '请先打开 CAN 总线')
            return
        self._raw_period_timer = QTimer(self)
        self._raw_period_timer.timeout.connect(self._on_raw_send_periodic)
        self._raw_period_timer.start(self.raw_period_spin.value())
        self.raw_period_start_btn.setEnabled(False)
        self.raw_period_stop_btn.setEnabled(True)

    def _on_raw_period_stop(self):
        if self._raw_period_timer:
            self._raw_period_timer.stop()
            self._raw_period_timer = None
        self.raw_period_start_btn.setEnabled(True)
        self.raw_period_stop_btn.setEnabled(False)

    def _on_raw_send_periodic(self):
        if self.can_worker is None or self.can_worker.bus is None:
            self._on_raw_period_stop()
            return
        msg = self._build_raw_msg()
        if msg:
            try:
                self.can_worker.bus.send(msg)
                self._echo_sent_frame(msg)
            except Exception:
                pass


    # ── TX echo ───────────────────────────────────────────────────────────────
    def _echo_sent_frame(self, msg):
        """Feed a sent frame back into the monitor and waveform buffers.

        PCAN USB does not loop back sent frames via recv(), so CanWorker never
        sees them. This method injects the frame into the same pipelines that
        received frames use, marking it [TX] in the monitor DBC column.
        """
        ts = time.time()
        # Write TX frame to capture file if recording is active
        if self._capture_logger is not None:
            msg.timestamp = ts
            try:
                self._capture_logger(msg)
            except Exception:
                pass
        # Always echo TX frames to monitor regardless of raw_monitor_check
        self._pending_raw_frames.append((
            ts,
            int(msg.arbitration_id),
            bytes(msg.data),
            bool(msg.is_extended_id),
            bool(getattr(msg, 'is_fd', False)),
            bool(getattr(msg, 'bitrate_switch', False)),
            True,   # is_tx marker — tells _flush_monitor this is an outgoing frame
        ))
        # Decode signals and update waveform buffers
        if self.db is not None:
            try:
                dbc_msg = self.db.get_message_by_frame_id(msg.arbitration_id)
                decoded = dbc_msg.decode(msg.data, decode_choices=False)
                batch = [
                    (k, float(v), ts)
                    for k, v in decoded.items()
                    if isinstance(v, (int, float))
                ]
                if batch:
                    self._on_decoded_batch(batch)
            except Exception:
                pass


    # ── Capture ───────────────────────────────────────────────────────────────
    def _on_capture_start(self):
        if self.can_worker is None:
            QMessageBox.warning(self, '未连接', '请先打开 CAN 总线')
            return
        fmt = self.capture_fmt_combo.currentText().lower()
        ext = '.asc' if 'asc' in fmt else ('.blf' if 'blf' in fmt else '.csv')
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _cap_dir = os.path.join(_project_root, 'captures')
        os.makedirs(_cap_dir, exist_ok=True)
        filepath = os.path.join(_cap_dir, 'capture_' + ts + ext)
        try:
            self._capture_logger = can.Logger(filepath)
        except Exception as e:
            QMessageBox.critical(self, '录制失败', str(e))
            return
        self.can_worker.capture_listener = self._capture_logger
        self.capture_file_label.setText(filepath)
        self.capture_start_btn.setText('● 正在录制...')
        self.capture_start_btn.setEnabled(False)
        self.capture_stop_btn.setEnabled(True)

    def _on_capture_stop(self):
        if self._capture_logger:
            try:
                self._capture_logger.stop()
            except Exception:
                pass
            self._capture_logger = None
        if self.can_worker:
            self.can_worker.capture_listener = None
        self.capture_start_btn.setText('开始录制')
        self.capture_start_btn.setEnabled(True)
        self.capture_stop_btn.setEnabled(False)

    # ── Replay ────────────────────────────────────────────────────────────────
    def _on_replay_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '打开捕获文件', '',
            'CAN Log Files (*.asc *.csv *.log *.blf);;All Files (*)',
            options=QFileDialog.DontUseNativeDialog,
        )
        if path:
            name = os.path.basename(path)
            self.replay_file_label.setText(name)
            self.replay_file_label.setToolTip(path)
            self.replay_play_btn.setEnabled(True)

    def _on_replay_play(self):
        path = self.replay_file_label.toolTip()
        if not path:
            return
        speed_text = self.replay_speed_combo.currentText().replace('x', '')
        speed = float(speed_text)
        # clear previous replay plot
        self.replay_plot.clear()
        self.replay_curves.clear()
        self._color_idx = 0

        self._replay_t0 = None   # reset so first decoded timestamp becomes t=0
        self.replay_worker = ReplayWorker(path, db=self.db, speed=speed)
        self.replay_worker.decoded_received.connect(self._on_replay_decoded)
        self.replay_worker.progress_changed.connect(self._on_replay_progress)
        self.replay_worker.finished.connect(self._on_replay_finished)
        self.replay_worker.start()

        self.replay_play_btn.setEnabled(False)
        self.replay_pause_btn.setEnabled(True)
        self.replay_stop_btn.setEnabled(True)
        self.main_tab.setCurrentIndex(3)

    def _on_replay_pause(self):
        if self.replay_worker:
            # simple pause: terminate and re-open from last point is complex
            # for now just stop
            self.replay_worker.stop()
        self.replay_pause_btn.setEnabled(False)

    def _on_replay_stop(self):
        if self.replay_worker:
            self.replay_worker.stop()
            self.replay_worker.wait(2000)
            self.replay_worker = None
        self.replay_play_btn.setEnabled(True)
        self.replay_pause_btn.setEnabled(False)
        self.replay_stop_btn.setEnabled(False)
        self.replay_progress_label.setText('0%')

    def _on_replay_decoded(self, batch):
        # normalize timestamps so replay always starts at t=0
        if self._replay_t0 is None and batch:
            self._replay_t0 = batch[0][2]

        # accumulate all points then setData once per curve (avoid per-point redraws)
        updates = {}   # {sig_name: ([x...], [y...])}
        for sig_name, val, ts in batch:
            rel_ts = ts - self._replay_t0
            if sig_name not in self.replay_curves:
                color = WAVE_COLORS[self._color_idx % len(WAVE_COLORS)]
                self._color_idx += 1
                curve = self.replay_plot.plot(
                    [], [], pen=pg.mkPen(color, width=1.5), name=sig_name)
                curve._xdata = []
                curve._ydata = []
                self.replay_curves[sig_name] = curve
            c = self.replay_curves[sig_name]
            if sig_name not in updates:
                updates[sig_name] = (list(c._xdata), list(c._ydata))
            updates[sig_name][0].append(rel_ts)
            updates[sig_name][1].append(val)

        for sig_name, (xs, ys) in updates.items():
            c = self.replay_curves[sig_name]
            c._xdata = xs
            c._ydata = ys
            c.setData(xs, ys)

    def _on_replay_progress(self, frac):
        self.replay_progress_label.setText('%d%%' % int(frac * 100))

    def _on_replay_finished(self):
        self.replay_play_btn.setEnabled(True)
        self.replay_pause_btn.setEnabled(False)
        self.replay_stop_btn.setEnabled(False)
        self.replay_progress_label.setText('100%')
        self.replay_worker = None

    # ── 报文解析 ──────────────────────────────────────────────────────────────
    def _on_parse_add_dbc(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '加载 DBC', '', 'DBC Files (*.dbc);;All Files (*)',
            options=QFileDialog.DontUseNativeDialog)
        if not path:
            return
        try:
            db = cantools.database.load_file(path)
        except Exception as e:
            QMessageBox.critical(self, 'DBC 加载失败', str(e))
            return
        idx = len(self._parse_dbc_list) + 1
        self._parse_dbc_list.append({
            'name': 'DB%d' % idx,
            'file': os.path.basename(path),
            'path': path,
            'db': db,
        })
        self._refresh_parse_dbc_table()

    def _on_parse_remove_dbc(self):
        row = self.parse_dbc_table.currentRow()
        if 0 <= row < len(self._parse_dbc_list):
            self._parse_dbc_list.pop(row)
            self._refresh_parse_dbc_table()

    def _on_parse_clear_dbc(self):
        self._parse_dbc_list.clear()
        self._refresh_parse_dbc_table()

    def _refresh_parse_dbc_table(self):
        tbl = self.parse_dbc_table
        tbl.setRowCount(0)
        for entry in self._parse_dbc_list:
            db = entry['db']
            sig_count = sum(len(m.signals) for m in db.messages)
            row = tbl.rowCount()
            tbl.insertRow(row)
            tbl.setItem(row, 0, QTableWidgetItem(entry['name']))
            tbl.setItem(row, 1, QTableWidgetItem(entry['file']))
            tbl.setItem(row, 2, QTableWidgetItem(str(len(db.messages))))
            tbl.setItem(row, 3, QTableWidgetItem(str(sig_count)))

    def _try_decode_all(self, arb_id, data):
        """Try every parse-tab DBC then the global DBC.
        Returns (db_label, msg_name, phys_dict, raw_dict, sig_map) or all-None."""
        candidates = [(e['name'], e['db']) for e in self._parse_dbc_list]
        if self.db is not None:
            candidates.append(('全局DBC', self.db))
        for label, db in candidates:
            try:
                dbc_msg = db.get_message_by_frame_id(arb_id)
            except KeyError:
                continue
            try:
                phys = dbc_msg.decode(data, decode_choices=False)
            except Exception:
                continue
            try:
                raw = dbc_msg.decode(data, decode_choices=False, scaling=False)
            except Exception:
                raw = {}
            sig_map = {s.name: s for s in dbc_msg.signals}
            return label, dbc_msg.name, phys, raw, sig_map
        return None, None, None, None, None

    def _on_parse_frame(self):
        if not self._parse_dbc_list and self.db is None:
            QMessageBox.warning(self, '未加载 DBC',
                                '请在本标签页添加 DBC，或通过顶部"加载 DBC"按钮加载全局 DBC')
            return
        try:
            arb_id = int(self.parse_id_edit.text().strip(), 16)
        except ValueError:
            QMessageBox.warning(self, '错误', 'ID 格式无效，请输入十六进制')
            return
        try:
            data = bytes(int(x, 16) for x in self.parse_data_edit.text().split() if x)
        except ValueError:
            QMessageBox.warning(self, '错误', '数据格式无效，请输入十六进制字节')
            return
        self._do_parse(arb_id, data)

    def _on_parse_from_monitor(self):
        row = self.monitor_table.currentRow()
        if row < 0:
            QMessageBox.information(self, '提示', '请先在总线监控中选中一行')
            return
        id_item = self.monitor_table.item(row, 1)
        data_item = self.monitor_table.item(row, 3)
        if not id_item or not data_item:
            return
        try:
            arb_id = int(id_item.text(), 16)
        except ValueError:
            return
        self.parse_id_edit.setText('%X' % arb_id)
        self.parse_data_edit.setText(data_item.text())
        self._do_parse(arb_id, bytes(int(x, 16) for x in data_item.text().split() if x))

    def _on_parse_raw_line(self):
        line = self.parse_raw_edit.text().strip()
        if not line:
            return
        tokens = line.split()
        arb_id, data_start = None, 0
        for i, tok in enumerate(tokens):
            try:
                val = int(tok, 16)
                if 0 <= val <= 0x1FFFFFFF:
                    arb_id = val
                    data_start = i + 1
                    break
            except ValueError:
                continue
        if arb_id is None:
            QMessageBox.warning(self, '解析失败', '无法从文本中识别 CAN ID')
            return
        remaining = tokens[data_start:]
        if remaining and remaining[0].isdigit() and int(remaining[0]) <= 64:
            remaining = remaining[1:]
        try:
            data = bytes(int(x, 16) for x in remaining if len(x) <= 2)
        except ValueError:
            QMessageBox.warning(self, '解析失败', '数据字节格式无效')
            return
        self.parse_id_edit.setText('%X' % arb_id)
        self.parse_data_edit.setText(' '.join('%02X' % b for b in data))
        self._do_parse(arb_id, data)

    def _on_parse_open_log(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '打开 CAN 日志文件', '',
            'CAN Log (*.asc *.blf *.csv *.log *.trc *.mf4);;All Files (*)',
            options=QFileDialog.DontUseNativeDialog)
        if path:
            self._parse_log_path = path
            name = os.path.basename(path)
            self.parse_log_label.setText(name)
            self.parse_log_label.setToolTip(path)

    def _on_parse_file(self):
        if not self._parse_log_path:
            QMessageBox.warning(self, '未选择文件', '请先打开 CAN 日志文件')
            return
        if not self._parse_dbc_list and self.db is None:
            QMessageBox.warning(self, '未加载 DBC', '请先加载至少一个 DBC 文件')
            return
        try:
            reader = can.LogReader(self._parse_log_path)
        except Exception as e:
            QMessageBox.critical(self, '文件读取失败', str(e))
            return
        limit = self.parse_limit_spin.value()
        self.parse_frame_table.setRowCount(0)
        self.parse_signal_table.setRowCount(0)
        self._parse_frame_data.clear()
        t0 = None
        count = decoded_count = 0
        self.parse_frame_table.setUpdatesEnabled(False)
        try:
            for msg in reader:
                if count >= limit:
                    break
                ts = msg.timestamp or 0.0
                if t0 is None:
                    t0 = ts
                arb_id = msg.arbitration_id
                data = bytes(msg.data)
                ch = str(getattr(msg, 'channel', '') or '')
                db_label, msg_name, phys, raw, sig_map = self._try_decode_all(arb_id, data)
                row = self.parse_frame_table.rowCount()
                self.parse_frame_table.insertRow(row)
                self.parse_frame_table.setItem(row, 0, QTableWidgetItem('%.4f' % (ts - t0)))
                self.parse_frame_table.setItem(row, 1, QTableWidgetItem(ch))
                self.parse_frame_table.setItem(row, 2, QTableWidgetItem('0x%X' % arb_id))
                self.parse_frame_table.setItem(row, 3, QTableWidgetItem(str(len(data))))
                self.parse_frame_table.setItem(row, 4, QTableWidgetItem(
                    ' '.join('%02X' % b for b in data)))
                self.parse_frame_table.setItem(row, 5, QTableWidgetItem(
                    '%s [%s]' % (msg_name, db_label) if msg_name else ''))
                self._parse_frame_data.append((phys or {}, raw or {}, sig_map or {}))
                if phys:
                    decoded_count += 1
                count += 1
        except Exception as e:
            QMessageBox.warning(self, '解析警告', str(e))
        finally:
            self.parse_frame_table.setUpdatesEnabled(True)
        lbl = self.parse_result_label
        lbl.setText('共 %d 帧  |  已解析 %d 帧  |  未识别 %d 帧' % (
            count, decoded_count, count - decoded_count))
        lbl.setStyleSheet('color: #4c4; font-weight: bold; padding: 2px;')
        if count > 0:
            self.parse_frame_table.selectRow(0)

    def _do_parse(self, arb_id, data, ts=0.0, channel=''):
        """Single-frame decode: clears frame table, adds one row, auto-selects it."""
        self.parse_frame_table.setRowCount(0)
        self.parse_signal_table.setRowCount(0)
        self._parse_frame_data.clear()
        db_label, msg_name, phys, raw, sig_map = self._try_decode_all(arb_id, data)
        self.parse_frame_table.insertRow(0)
        self.parse_frame_table.setItem(0, 0, QTableWidgetItem('%.4f' % ts))
        self.parse_frame_table.setItem(0, 1, QTableWidgetItem(channel))
        self.parse_frame_table.setItem(0, 2, QTableWidgetItem('0x%X' % arb_id))
        self.parse_frame_table.setItem(0, 3, QTableWidgetItem(str(len(data))))
        self.parse_frame_table.setItem(0, 4, QTableWidgetItem(
            ' '.join('%02X' % b for b in data)))
        self.parse_frame_table.setItem(0, 5, QTableWidgetItem(
            '%s [%s]' % (msg_name, db_label) if msg_name else ''))
        self._parse_frame_data.append((phys or {}, raw or {}, sig_map or {}))
        lbl = self.parse_result_label
        if msg_name:
            lbl.setText('报文: 0x%X  %s  DLC=%d  [%s]' % (arb_id, msg_name, len(data), db_label))
            lbl.setStyleSheet('color: #4c4; font-weight: bold; padding: 2px;')
        else:
            lbl.setText('0x%X — 所有已加载 DBC 中未找到此报文' % arb_id)
            lbl.setStyleSheet('color: #c44; padding: 2px;')
        self.parse_frame_table.selectRow(0)

    def _on_parse_row_selected(self, row):
        tbl = self.parse_signal_table
        tbl.setRowCount(0)
        if row < 0 or row >= len(self._parse_frame_data):
            return
        phys, raw, sig_map = self._parse_frame_data[row]
        if not phys:
            return
        for sig_name, phys_val in phys.items():
            sig = sig_map.get(sig_name)
            r = tbl.rowCount()
            tbl.insertRow(r)
            tbl.setItem(r, 0, QTableWidgetItem(sig_name))
            raw_val = raw.get(sig_name)
            if raw_val is not None:
                try:
                    rv = int(raw_val)
                    raw_str = '0x%X  (%d)' % (rv, rv)
                except Exception:
                    raw_str = str(raw_val)
            else:
                raw_str = ''
            tbl.setItem(r, 1, QTableWidgetItem(raw_str))
            phys_str = '%.6g' % phys_val if isinstance(phys_val, float) else str(phys_val)
            tbl.setItem(r, 2, QTableWidgetItem(phys_str))
            tbl.setItem(r, 3, QTableWidgetItem(sig.unit or '' if sig else ''))
            mn = ('%.4g' % sig.minimum) if sig and sig.minimum is not None else ''
            mx = ('%.4g' % sig.maximum) if sig and sig.maximum is not None else ''
            tbl.setItem(r, 4, QTableWidgetItem(mn))
            tbl.setItem(r, 5, QTableWidgetItem(mx))

    # ─────────────────────────────────────────────────────────────────────────
    def closeEvent(self, event):
        self._on_capture_stop()
        if self.replay_worker:
            self.replay_worker.stop()
            self.replay_worker.wait(2000)
        if self.can_worker:
            self.can_worker.stop()
            self.can_worker.wait(3000)
        event.accept()