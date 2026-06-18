# -*- coding: utf-8 -*-
import time
import can
from PyQt5.QtCore import QThread, pyqtSignal


class CanWorker(QThread):
    # list of (sig_name: str, val: float, ts: float)
    decoded_received = pyqtSignal(list)
    # list of (ts, arb_id, data_bytes, is_ext, is_fd, brs)
    raw_frame_received = pyqtSignal(list)
    connection_status_changed = pyqtSignal(bool, str)
    # list of signal name strings from loaded DBC
    dbc_signals_updated = pyqtSignal(list)

    def __init__(self, interface, channel, bitrate, timing=None):
        super().__init__()
        self.interface = interface
        self.channel = channel
        self.bitrate = bitrate
        # timing: can.BitTimingFd instance for CAN-FD, None for classic CAN
        self.timing = timing
        self.running = True
        self.db = None
        self._msg_cache = {}
        self.capture_listener = None
        self.enable_raw_monitor = False
        self.bus = None
        self._pending_decoded = []
        self._pending_raw = []
        self._last_decoded_emit = time.time()
        self._last_raw_emit = time.time()
        self.EMIT_INTERVAL = 0.02   # 50 Hz GUI update
        self.RAW_INTERVAL = 0.1

    def set_dbc(self, db):
        self.db = db
        self._msg_cache.clear()
        names = [sig.name for msg in db.messages for sig in msg.signals]
        self.dbc_signals_updated.emit(names)

    def run(self):
        is_fd = self.timing is not None
        fd_info = " [CAN-FD]" if is_fd else ""
        print("CanWorker start (%s, %s, %dbps%s)" % (self.interface, self.channel, self.bitrate, fd_info))
        try:
            if is_fd:
                self.bus = can.Bus(interface=self.interface, channel=self.channel, timing=self.timing)
            else:
                self.bus = can.Bus(interface=self.interface, channel=self.channel, bitrate=self.bitrate)
            label = "CAN FD 已连接" if is_fd else "CAN 已连接"
            self.connection_status_changed.emit(True, label)
        except Exception as e:
            self.connection_status_changed.emit(False, "CAN 错误: %s" % e)
            self.running = False
            return

        while self.running:
            try:
                msg = self.bus.recv(0.1)
                if msg is None:
                    self._flush_decoded()
                    self._flush_raw()
                    continue

                ts = msg.timestamp if msg.timestamp else time.time()

                if self.capture_listener is not None:
                    try:
                        self.capture_listener(msg)
                    except Exception:
                        pass

                if self.enable_raw_monitor:
                    self._pending_raw.append((
                        ts,
                        int(msg.arbitration_id),
                        bytes(msg.data),
                        bool(msg.is_extended_id),
                        bool(getattr(msg, 'is_fd', False)),
                        bool(getattr(msg, 'bitrate_switch', False)),
                    ))

                if self.db is not None:
                    arb_id = msg.arbitration_id
                    if arb_id not in self._msg_cache:
                        try:
                            self._msg_cache[arb_id] = self.db.get_message_by_frame_id(arb_id)
                        except KeyError:
                            self._msg_cache[arb_id] = None
                    db_msg = self._msg_cache[arb_id]
                    if db_msg is not None:
                        try:
                            decoded = db_msg.decode(msg.data, decode_choices=False)
                            for sig_name, val in decoded.items():
                                if isinstance(val, (int, float)):
                                    self._pending_decoded.append((sig_name, float(val), ts))
                        except Exception:
                            pass

                self._flush_decoded()
                self._flush_raw()

            except Exception as e:
                if not self.running:
                    break
                print("CanWorker recv error: %s" % e)
                time.sleep(1.0)

        if self.bus is not None:
            try:
                self.bus.shutdown()
            except Exception:
                pass
            self.bus = None
        print("CanWorker stopped.")

    def _flush_decoded(self):
        if not self._pending_decoded:
            return
        now = time.time()
        if (now - self._last_decoded_emit) >= self.EMIT_INTERVAL:
            self.decoded_received.emit(self._pending_decoded)
            self._pending_decoded = []
            self._last_decoded_emit = now

    def _flush_raw(self):
        if not self._pending_raw:
            return
        now = time.time()
        if len(self._pending_raw) >= 200 or (now - self._last_raw_emit) >= self.RAW_INTERVAL:
            self.raw_frame_received.emit(self._pending_raw)
            self._pending_raw = []
            self._last_raw_emit = now

    def stop(self):
        self.running = False
        if self.bus is not None:
            try:
                self.bus.shutdown()
            except Exception:
                pass
