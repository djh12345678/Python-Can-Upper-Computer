# Python-Can-Upper-Computer（CAN 上位机）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![GUI Framework](https://img.shields.io/badge/GUI-PyQt5-green.svg)](https://www.riverbankcomputing.com/software/pyqt/)

基于 PyQt5 + python-can + cantools 开发的 CAN 总线调试上位机，定位为 PCAN-View 的功能增强补充，支持 DBC 解析、波形绘制、报文构造、多路 CAN 文件解析等高级功能。

[English](#english) | [简体中文](#简体中文)

---

## English

### Core Features

| # | Feature | Description |
|---|---------|-------------|
| 1 | **CAN Send / Receive** | Open classic CAN or CAN-FD bus; monitor incoming frames in real time |
| 2 | **Bus Monitor** | Live table with timestamp, ID, DLC, raw bytes, and inline DBC-decoded signal values |
| 3 | **Waveform Analysis** | Select DBC signals by checkbox and plot real-time trend curves with Y-auto-scale and signal search |
| 4 | **Message Builder** | Build CAN/CAN-FD frames signal-by-signal (DBC-guided); raw Hex send with structured field sync; both support periodic send |
| 5 | **Capture & Replay** | Record traffic to ASC/BLF/CSV; replay at adjustable speed with waveform rendering |
| 6 | **Frame Parser** | Multi-DBC batch decode of CAN log files (ASC/BLF/CSV/TRC/MF4); click any row to inspect all signal values |

### Installation

```bash
git clone https://github.com/WenZhenJian-EE/python-can上位机.git
cd python-can上位机
pip install -r requirements.txt
python main.py
```

On Windows you can also double-click **`start.bat`** — it auto-installs missing dependencies before launching.

### CAN-FD Support

CAN-FD uses `can.BitTimingFd` (required by python-can 4.x for the PCAN backend). Five presets are built in for PCAN-USB FD devices (80 MHz clock):

| Preset | Nominal | Data |
|--------|---------|------|
| 500k/2M | 500 kbit/s | 2 Mbit/s |
| 500k/1M | 500 kbit/s | 1 Mbit/s |
| 250k/2M | 250 kbit/s | 2 Mbit/s |
| 250k/500k | 250 kbit/s | 500 kbit/s |
| 1M/4M | 1 Mbit/s | 4 Mbit/s |

### Hardware Testing

```bash
python tests/pcan_test.py
```

Runs end-to-end PCAN hardware tests (no GUI): CAN/CAN-FD connection, loopback TX/RX, DBC encode/decode, ASC/CSV capture, and file replay.

---

## Architecture

The application runs **three concurrent execution contexts** — all within a single process using `QThread` (OS threads). No multiprocessing is used.

```
┌──────────────────────────────────────────────────────────────────┐
│  Main thread  (Qt event loop)                                    │
│  · All UI rendering and user interaction                         │
│  · QTimer _monitor_timer  100 ms  → flush monitor table         │
│  · QTimer _wave_timer     100 ms  → redraw waveform curves      │
│  · QTimer _period_timer   N ms    → periodic DBC send           │
│  · QTimer _raw_period_timer N ms  → periodic raw Hex send       │
│  · bus.send()  ← called from main thread                        │
└──────────────────────────────────────────────────────────────────┘
         ↑  pyqtSignal  (Qt queued connection — lock-free)
┌──────────────────────────────────────────────────────────────────┐
│  CanWorker  (QThread)                                            │
│  · bus.recv(0.1)  blocking loop                                  │
│  · cantools decode of every received frame                       │
│  · capture_listener(msg)  write to ASC/CSV in-loop              │
│  · batch-emit at 50 Hz: decoded_received, raw_frame_received    │
└──────────────────────────────────────────────────────────────────┘
         ↑  pyqtSignal
┌──────────────────────────────────────────────────────────────────┐
│  ReplayWorker  (QThread)  — exists only during replay            │
│  · can.LogReader reads ASC / BLF / CSV capture files            │
│  · time.sleep() paces playback to chosen speed (0.1×–10×)      │
│  · emits decoded_received / raw_frame_received                   │
└──────────────────────────────────────────────────────────────────┘
```

**Cross-thread communication** uses `pyqtSignal`. Signals emitted from worker threads are delivered via Qt's event queue (`Qt::QueuedConnection`), keeping all UI updates on the main thread without explicit locks.

**Batch emission** (`EMIT_INTERVAL = 0.02 s`, 50 Hz) in `CanWorker` prevents event-queue flooding under high bus load.

**TX echo**: PCAN does not support `receive_own_messages=True`. Sent frames are injected back into the monitor/waveform pipeline via `_echo_sent_frame` in the main window, ensuring full-duplex visibility.

**Design notes:**
- `bus.send()` runs on the main thread while `bus.recv()` runs in `CanWorker`. The PCAN C library is internally thread-safe; the `bus` object is a shared reference without an application-level lock.
- `capture_listener` is a bare attribute written by the main thread and read by `CanWorker`. Python's GIL makes the reference swap atomic.

---

## Project Structure

```
python-can上位机/
├── main.py                Entry point
├── start.bat              Windows one-click launcher (auto-installs deps)
├── build_exe.py           PyInstaller packaging script
├── requirements.txt
│
├── app/
│   ├── main_window.py     MainWindow — all business logic and signal wiring
│   ├── ui_layout.py       setup_ui() — 5-tab widget tree (no logic)
│   ├── can_worker.py      CanWorker(QThread) — receive loop + batch emit
│   ├── replay_worker.py   ReplayWorker(QThread) — file replay with speed control
│   └── config_utils.py    Persistent JSON settings
│
├── tests/
│   └── pcan_test.py       End-to-end PCAN hardware test (no GUI)
│
├── dbc/                   Place .dbc files here
└── captures/              Capture files land here (gitignored)
```

---

## Tab Details

### Top Bar (always visible)

```
接口[pcan▼]  通道[PCAN_USBBUS1▼]  波特率[500000▼]  □CAN-FD [500k/2M▼]
[📂 DBC]  DBC: xxx.dbc    [打开CAN]  [关闭CAN]    状态: 已连接
```

### Tab 1 — 总线监控

- Real-time table: 时间(s) / ID / DLC / 数据(Hex) / DBC解析
- Filter by ID (hex), auto-scroll toggle, one-click clear
- DBC column populated automatically when a `.dbc` is loaded

### Tab 2 — 波形分析

- Signal list built from loaded DBC; checkbox to show/hide each curve
- Search box to filter signal list by name or ID
- Shows CAN ID alongside signal name
- Y-auto-scale toggle; rolling time window via pyqtgraph

### Tab 3 — 报文发送

**DBC 构造区（左）**
- Full DBC message list with checkboxes (全选/取消全选); click to select, check to include in batch send
- Signal table with SpinBox/ComboBox per signal; signal values persist per message across selections
- Batch send sends all checked messages at once (missing signals filled with valid minimum/0)
- Periodic send: period (ms) spin + 启动/停止; CAN-FD auto-detected from DBC message length

**原始 Hex 发送区（右）**
- ID field + □扩展帧 / □CAN-FD / □BRS checkboxes
- Multi-line hex data field (8 bytes per row)
- Structured description field: `ID=,Type=,Length=,Data=,CycleTime=,Paused=,IDFormat=` — bidirectionally synced with the hex fields; any edit in either field updates the other
- Periodic send: period (ms) spin + 启动/停止

### Tab 4 — 捕获 & 回放

**录制**
- Format: ASC or CSV (dropdown); filename auto-generated with timestamp
- Button shows `● 正在录制...` while recording
- Captures both RX and TX frames (TX echoed in software)

**回放**
- Open ASC/BLF/CSV files
- Scrubber slider, play/pause, speed selector (0.1× – 10×)
- Decoded signals rendered on waveform plot during replay

### Tab 5 — 报文解析

**DBC 管理**
- Add / remove / clear multiple DBC files independently of the global DBC
- Table shows: 标识 / 文件名 / 报文数 / 信号数
- Decode priority: parse-tab DBCs tried first in load order, then falls back to the global DBC

**解析输入**
- Manual single-frame: ID + data hex → 解析 button; import directly from monitor selection
- Raw text line: paste any ASC/monitor-style log line → auto-extract ID and data
- Log file import: open ASC / BLF / CSV / TRC / MF4 → parse up to N frames

**解析结果**
- Frame table: 时间 / 通道 / ID / DLC / 数据 / 报文名[DBC]
- Click any row → signal detail table: 信号名 / 原始值 / 物理值 / 单位 / 最小 / 最大
- Multi-channel CAN log support (channel column preserved)

---

## Tech Stack

| Component | Library |
|-----------|---------|
| GUI | PyQt5 |
| Plotting | PyQtGraph |
| CAN bus interface | python-can 4.x |
| DBC parsing / encoding | cantools |
| Numeric buffers | NumPy |

---

## 简体中文

### 核心功能

| # | 功能 | 说明 |
|---|------|------|
| 1 | **CAN 收发** | 打开/关闭经典 CAN 或 CAN-FD 总线，实时监控报文 |
| 2 | **总线监控** | 实时表格，显示时间戳、ID、DLC、原始字节及 DBC 解析信号值 |
| 3 | **波形分析** | 从 DBC 信号列表勾选信号，绘制实时趋势曲线；支持信号搜索、Y 轴自动缩放 |
| 4 | **报文发送** | DBC 构造（整个 DBC 多消息批量发送、信号值持久化）+ 原始 Hex 发送（结构化字段双向同步）；均支持周期发送 |
| 5 | **捕获与回放** | 录制为 ASC/CSV；以可调倍速（0.1×–10×）回放，同步绘制波形 |
| 6 | **报文解析** | 多 DBC 导入 + 多种 CAN 日志文件（ASC/BLF/CSV/TRC/MF4）批量解析，支持多路 CAN 通道 |

### 运行

```bash
git clone https://github.com/WenZhenJian-EE/python-can上位机.git
cd python-can上位机
pip install -r requirements.txt
python main.py
```

Windows 用户双击 **`start.bat`** 即可，脚本会自动检查并安装缺失依赖后启动。

### 并发架构

详见 [Architecture](#architecture) 节（与英文共用）。

### 关键设计说明

**批量 DBC 发送**：`_dbc_send_values` 字典以 `frame_id` 为键保存每条报文的信号值，切换行时自动持久化和恢复，确保多报文同时发送时每个信号都有合法值（不会出现 cantools `required for encoding` 报错）。

**双向同步**：原始 Hex 字段与结构化描述字段之间用 `_raw_sync` 布尔标志防止循环触发。

**TX 可见性**：PCAN 不支持 `receive_own_messages=True`，发送帧通过 `_echo_sent_frame` 软件注入到监控和波形管道，确保收发均可见。

**多 DBC 解析**：`_try_decode_all` 按顺序尝试所有已加载的解析标签 DBC，最后回退全局 DBC，首次成功即返回；不同通道可加载不同 DBC。

### 硬件测试

```bash
python tests/pcan_test.py
```

覆盖全部核心功能：经典 CAN/CAN-FD 连接、收发回环、DBC 编解码、ASC/CSV 录制、文件回放。

---

## License

Licensed under the [MIT License](LICENSE).
