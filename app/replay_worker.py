# -*- coding: utf-8 -*-
import time
import can
from PyQt5.QtCore import QThread, pyqtSignal


class ReplayWorker(QThread):
    # list of (sig_name: str, val: float, ts: float)
    decoded_received = pyqtSignal(list)
    # can.Message object
    raw_frame_received = pyqtSignal(object)
    # 0.0 ~ 1.0
    progress_changed = pyqtSignal(float)
    finished = pyqtSignal()

    def __init__(self, filepath, db=None, speed=1.0):
        super().__init__()
        self.filepath = filepath
        self.db = db
        self.speed = max(0.01, speed)
        self.running = True
        self._msg_cache = {}

    def set_speed(self, speed):
        self.speed = max(0.01, speed)

    def run(self):
        try:
            reader = can.LogReader(self.filepath)
            msgs = list(reader)
        except Exception as e:
            print("ReplayWorker: cannot open %s: %s" % (self.filepath, e))
            self.finished.emit()
            return

        if not msgs:
            self.finished.emit()
            return

        total = len(msgs)
        t0_file = msgs[0].timestamp if msgs[0].timestamp else 0.0
        t0_wall = time.time()

        for i, msg in enumerate(msgs):
            if not self.running:
                break

            # pace replay to wall-clock at chosen speed
            file_elapsed = (msg.timestamp if msg.timestamp else 0.0) - t0_file
            wall_elapsed = time.time() - t0_wall
            wait = file_elapsed / self.speed - wall_elapsed
            if wait > 0:
                time.sleep(wait)

            self.raw_frame_received.emit(msg)

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
                        ts = msg.timestamp if msg.timestamp else time.time()
                        batch = [
                            (sig_name, float(val), ts)
                            for sig_name, val in decoded.items()
                            if isinstance(val, (int, float))
                        ]
                        if batch:
                            self.decoded_received.emit(batch)
                    except Exception:
                        pass

            self.progress_changed.emit((i + 1) / total)

        self.finished.emit()

    def stop(self):
        self.running = False
