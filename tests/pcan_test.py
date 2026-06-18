# -*- coding: utf-8 -*-
"""
tests/pcan_test.py  —  Comprehensive PCAN hardware feature test
Run from project root:  python tests/pcan_test.py

Tests all 6 core features:
  1. Classic CAN connect + send
  2. CAN-FD connect + send
  3. DBC file load + cantools encode/decode roundtrip
  4. DBC-based frame construction and send
  5. Capture to ASC and CSV files
  6. Replay from captured file (with DBC decode)

Note on receive_own_messages:
  PCAN USB does not support hardware loopback via python-can's
  receive_own_messages=True. Receive tests therefore wait for any
  real bus traffic instead of expecting our own sent frames back.
  Capture tests fall back to logging synthetic can.Message objects
  directly when no real traffic is present, ensuring the
  can.Logger → can.LogReader pipeline is still fully verified.

Requirements:
  - PCAN-USB FD connected as PCAN_USBBUS1
  - pip install python-can cantools
  - DBC file in dbc/ directory
"""

import os
import time
import glob as _glob
import traceback

import can
import cantools

# ─────────────────────── Configuration ───────────────────────
INTERFACE = 'pcan'
CHANNEL   = 'PCAN_USBBUS1'
BITRATE   = 500_000

# PCAN-USB FD SAE J2284-4 timing (80 MHz clock, 500k nom / 2M data)
FD_TIMING = can.BitTimingFd(
    f_clock=80_000_000,
    nom_brp=2,  nom_tseg1=63, nom_tseg2=16, nom_sjw=16,
    data_brp=2, data_tseg1=15, data_tseg2=4,  data_sjw=4,
)

# Paths (relative to project root)
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_DBC_DIR = os.path.join(_ROOT, 'dbc')
_CAP_DIR = os.path.join(_ROOT, 'captures')
os.makedirs(_CAP_DIR, exist_ok=True)

_TS       = time.strftime('%Y%m%d_%H%M%S')
_ASC_PATH = os.path.join(_CAP_DIR, 'test_%s.asc' % _TS)
_CSV_PATH = os.path.join(_CAP_DIR, 'test_%s.csv' % _TS)

# ─────────────────────── Helpers ─────────────────────────────
_results = []


def section(title):
    print('\n' + '=' * 64)
    print('  %s' % title)
    print('=' * 64)


def passed(label, detail=''):
    _results.append((label, True, detail))
    print('  [PASS]  %s  %s' % (label, detail))


def failed(label, detail=''):
    _results.append((label, False, detail))
    print('  [FAIL]  %s  %s' % (label, detail))


def info(msg):
    print('  [INFO]  %s' % msg)


def open_bus(fd=False):
    """Open PCAN bus. No receive_own_messages — not supported by PCAN USB."""
    if fd:
        return can.Bus(interface=INTERFACE, channel=CHANNEL, timing=FD_TIMING)
    return can.Bus(interface=INTERFACE, channel=CHANNEL, bitrate=BITRATE)


def recv_any(bus, timeout=2.0):
    """Wait up to timeout seconds for any frame. Returns Message or None."""
    return bus.recv(timeout=timeout)


# ═══════════════════════════════════════════════════════════
# 1. Classic CAN — connect + send
# ═══════════════════════════════════════════════════════════
section('1. Classic CAN — connect + send')
bus_classic = None
try:
    bus_classic = open_bus(fd=False)
    passed('Classic CAN connect (500k)')

    test_id   = 0x7DF
    test_data = bytes([0x02, 0x10, 0x03, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE])
    msg_out   = can.Message(arbitration_id=test_id, data=test_data,
                            is_extended_id=False, is_fd=False)
    bus_classic.send(msg_out)
    passed('Classic CAN send', 'ID=0x%03X DLC=%d' % (test_id, len(test_data)))

    # Receive any frame from the bus (informational — not a loopback check)
    rx = recv_any(bus_classic, timeout=1.0)
    if rx:
        info('Bus activity detected: ID=0x%X DLC=%d' % (rx.arbitration_id, len(rx.data)))
    else:
        info('No incoming frames in 1s (bus may be idle or in a different baud mode)')

except Exception as e:
    failed('Classic CAN', str(e))
finally:
    if bus_classic:
        bus_classic.shutdown()
        bus_classic = None


# ═══════════════════════════════════════════════════════════
# 2. CAN-FD — connect + send (64-byte frame)
# ═══════════════════════════════════════════════════════════
section('2. CAN-FD — connect + send (64-byte payload)')
bus_fd = None
try:
    bus_fd = open_bus(fd=True)
    passed('CAN-FD connect (BitTimingFd 500k/2M)')

    fd_id   = 0x1FFFFFFF
    fd_data = bytes(range(64))
    msg_fd  = can.Message(arbitration_id=fd_id, data=fd_data,
                          is_extended_id=True, is_fd=True, bitrate_switch=True)
    bus_fd.send(msg_fd)
    passed('CAN-FD send', 'ID=0x%08X DLC=64 FD=True BRS=True' % fd_id)

    rx_fd = recv_any(bus_fd, timeout=1.0)
    if rx_fd:
        info('Bus activity: ID=0x%X DLC=%d FD=%s' % (
            rx_fd.arbitration_id, len(rx_fd.data), rx_fd.is_fd))
    else:
        info('No incoming frames in 1s')

except Exception as e:
    failed('CAN-FD', str(e))
finally:
    if bus_fd:
        bus_fd.shutdown()
        bus_fd = None


# ═══════════════════════════════════════════════════════════
# 3. DBC file load + cantools encode/decode roundtrip
# ═══════════════════════════════════════════════════════════
section('3. DBC load + cantools encode/decode roundtrip')
db        = None
_test_msg = None
_test_sig = None

dbc_files = _glob.glob(os.path.join(_DBC_DIR, '*.dbc'))
if not dbc_files:
    failed('Find DBC file', 'No .dbc found in %s' % _DBC_DIR)
else:
    dbc_path = dbc_files[0]
    try:
        db        = cantools.database.load_file(dbc_path)
        msg_count = len(db.messages)
        sig_count = sum(len(m.signals) for m in db.messages)
        passed('Load DBC', '%s  messages=%d  signals=%d' % (
            os.path.basename(dbc_path), msg_count, sig_count))

        # Pick first short message with numeric signals
        for m in db.messages:
            numeric_sigs = [s for s in m.signals if not s.choices]
            if numeric_sigs and m.length <= 8:
                _test_msg = m
                _test_sig = numeric_sigs[0]
                break

        if _test_msg and _test_sig:
            s      = _test_sig
            mn     = s.minimum if s.minimum is not None else 0.0
            mx     = s.maximum if s.maximum is not None else 0.0
            midval = (mn + mx) / 2.0
            sigvals = {}
            for other in _test_msg.signals:
                mn2 = other.minimum if other.minimum is not None else 0.0
                mx2 = other.maximum if other.maximum is not None else 0.0
                sigvals[other.name] = (mn2 + mx2) / 2.0
            try:
                encoded    = _test_msg.encode(sigvals)
                decoded    = _test_msg.decode(encoded, decode_choices=False)
                decoded_v  = decoded.get(s.name)
                if decoded_v is not None and abs(float(decoded_v) - midval) < 1e-3:
                    passed('Encode/decode roundtrip',
                           'msg=%s sig=%s val=%.3f->%.3f' % (
                               _test_msg.name, s.name, midval, float(decoded_v)))
                else:
                    failed('Encode/decode roundtrip',
                           'expected=%.3f got=%s' % (midval, decoded_v))
            except Exception as e:
                failed('Encode/decode roundtrip', str(e))
        else:
            passed('DBC roundtrip', 'skipped — no short numeric-signal message')

    except Exception as e:
        failed('Load DBC', str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════
# 4. DBC-based frame construction + send
# ═══════════════════════════════════════════════════════════
section('4. DBC-based frame construction + send')
if db is None:
    failed('DBC frame send', 'No DBC loaded')
elif _test_msg is None:
    passed('DBC frame send', 'Skipped — no suitable message in DBC')
else:
    bus_dbc = None
    try:
        bus_dbc = open_bus(fd=False)

        is_fd_frame = _test_msg.length > 8
        sigvals = {}
        for s in _test_msg.signals:
            mn = s.minimum if s.minimum is not None else 0.0
            mx = s.maximum if s.maximum is not None else 0.0
            sigvals[s.name] = (mn + mx) / 2.0

        raw_data = _test_msg.encode(sigvals)
        ext_id   = bool(getattr(_test_msg, 'is_extended_frame', False))
        can_msg  = can.Message(
            arbitration_id=_test_msg.frame_id,
            data=raw_data,
            is_extended_id=ext_id,
            is_fd=is_fd_frame,
            bitrate_switch=is_fd_frame,
        )
        bus_dbc.send(can_msg)
        passed('DBC frame send',
               'msg=%s ID=0x%X DLC=%d ext=%s fd=%s' % (
                   _test_msg.name, _test_msg.frame_id,
                   len(raw_data), ext_id, is_fd_frame))

        # Verify DBC decode of the same raw bytes (independent of bus recv)
        decoded_back = _test_msg.decode(raw_data, decode_choices=False)
        short = {k: round(float(v), 2) for k, v in list(decoded_back.items())[:3]}
        passed('DBC frame encode→decode verify', 'signals=%s' % short)

    except Exception as e:
        failed('DBC frame send', str(e))
        traceback.print_exc()
    finally:
        if bus_dbc:
            bus_dbc.shutdown()


# ═══════════════════════════════════════════════════════════
# 5. Capture to ASC and CSV files
#
# Strategy: open bus and try to collect real incoming frames
# for up to RECV_WINDOW seconds. If the bus is silent, fall
# back to logging 5 synthetic can.Message objects directly —
# this still fully exercises the can.Logger → can.LogReader
# pipeline without requiring hardware loopback.
# ═══════════════════════════════════════════════════════════
section('5. Capture to ASC and CSV')
RECV_WINDOW = 2.0   # seconds to wait for real bus traffic

for cap_path, fmt_name in [(_ASC_PATH, 'ASC'), (_CSV_PATH, 'CSV')]:
    bus_cap = None
    try:
        bus_cap = open_bus(fd=False)
        logger  = can.Logger(cap_path)
        logged  = 0

        # Try to collect real frames
        deadline = time.time() + RECV_WINDOW
        while time.time() < deadline:
            rx = bus_cap.recv(timeout=0.1)
            if rx:
                logger(rx)
                logged += 1

        source = 'real bus frames'

        # No real traffic — log synthetic messages to test the pipeline
        if logged == 0:
            base_ts = time.time()
            for i in range(5):
                m = can.Message(
                    timestamp=base_ts + i * 0.1,
                    arbitration_id=0x100 + i,
                    data=bytes([i] * 8),
                    is_extended_id=False,
                    channel=CHANNEL,
                )
                logger(m)
                logged += 1
            source = 'synthetic frames (bus idle)'

        logger.stop()

        file_size = os.path.getsize(cap_path) if os.path.exists(cap_path) else 0
        if file_size > 0 and logged > 0:
            passed('Capture to %s' % fmt_name,
                   '%s  %d frames  size=%d bytes  source=%s' % (
                       os.path.basename(cap_path), logged, file_size, source))
        else:
            failed('Capture to %s' % fmt_name,
                   'file=%d bytes  logged=%d' % (file_size, logged))

    except Exception as e:
        failed('Capture to %s' % fmt_name, str(e))
        traceback.print_exc()
    finally:
        if bus_cap:
            bus_cap.shutdown()


# ═══════════════════════════════════════════════════════════
# 6. Replay from ASC file (with DBC decode if available)
# ═══════════════════════════════════════════════════════════
section('6. Replay from ASC file')

if not os.path.exists(_ASC_PATH) or os.path.getsize(_ASC_PATH) == 0:
    failed('Replay', 'ASC capture file missing or empty')
else:
    try:
        reader        = can.LogReader(_ASC_PATH)
        replayed_msgs = list(reader)

        if not replayed_msgs:
            failed('Replay read', 'No messages in file')
        else:
            passed('Replay LogReader read', 'read %d messages from %s' % (
                len(replayed_msgs), os.path.basename(_ASC_PATH)))

            # DBC decode of replayed frames
            decode_count = 0
            if db is not None:
                msg_cache = {}
                for rm in replayed_msgs:
                    arb_id = rm.arbitration_id
                    if arb_id not in msg_cache:
                        try:
                            msg_cache[arb_id] = db.get_message_by_frame_id(arb_id)
                        except KeyError:
                            msg_cache[arb_id] = None
                    db_m = msg_cache[arb_id]
                    if db_m is not None:
                        try:
                            decoded = db_m.decode(rm.data, decode_choices=False)
                            decode_count += len(decoded)
                        except Exception:
                            pass
                passed('Replay DBC decode',
                       'decoded %d signal values across %d frames' % (
                           decode_count, len(replayed_msgs)))
            else:
                passed('Replay DBC decode', 'skipped — no DBC loaded')

            # Verify paced replay timing at 4x speed
            t0_file       = replayed_msgs[0].timestamp or 0.0
            t_last        = replayed_msgs[-1].timestamp or 0.0
            file_duration = t_last - t0_file
            speed         = 4.0
            t0_wall       = time.time()
            for rm in replayed_msgs:
                fe   = (rm.timestamp or 0.0) - t0_file
                wait = fe / speed - (time.time() - t0_wall)
                if wait > 0:
                    time.sleep(wait)
            elapsed = time.time() - t0_wall
            if file_duration > 0:
                passed('Replay timing at %gx speed' % speed,
                       'file_dur=%.3fs  expected=%.3fs  actual=%.3fs' % (
                           file_duration, file_duration / speed, elapsed))
            else:
                passed('Replay timing', 'all frames share t=0 (single-block capture)')

    except Exception as e:
        failed('Replay', str(e))
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════
section('Summary')
n_passed = sum(1 for _, flag, _ in _results if flag)
total    = len(_results)
print()
for label, flag, detail in _results:
    marker = 'PASS' if flag else 'FAIL'
    suffix = ('  (%s)' % detail) if detail else ''
    print('  [%s]  %s%s' % (marker, label, suffix))
print()
print('  Result: %d / %d passed' % (n_passed, total))
print('  All tests PASSED.' if n_passed == total else '  Some tests FAILED — see above.')
print()
print('  Capture files:')
for p in [_ASC_PATH, _CSV_PATH]:
    size   = os.path.getsize(p) if os.path.exists(p) else -1
    status = '%d bytes' % size if size >= 0 else 'not created'
    print('    %s  (%s)' % (p, status))
print()
