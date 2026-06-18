# Python-Can-Upper-Computer（CAN 上位机）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![GUI Framework](https://img.shields.io/badge/GUI-PyQt5-green.svg)](https://www.riverbankcomputing.com/software/pyqt/)
[![Version](https://img.shields.io/badge/version-2.0.0-orange.svg)](pyproject.toml)

基于 PyQt5 + python-can + cantools 开发的 CAN 总线调试上位机（V2.0），定位为 PCAN-View 的功能增强补充，支持 DBC 解析、波形绘制、报文构造、多路 CAN 文件解析等高级功能。

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

**Using pip:**

```bash
git clone <repo-url>
cd Python-Can-Upper-Computer
pip install -r requirements.txt
python main.py
```

**Using uv (recommended on Windows):**

```bash
git clone <repo-url>
cd Python-Can-Upper-Computer
uv sync
uv run python main.py
```

**Package as a standalone EXE (Windows):**

```bash
python build_exe.py
# Output: dist/UpperComputer_V2.exe
```

### Supported Interfaces

The interface dropdown exposes all python-can backends. Channel presets are populated automatically when you select an interface.

| Interface | Hardware |
|-----------|---------|
| `pcan` | PEAK PCAN-USB / PCIe |
| `slcan` | Serial CAN (LAWICEL / CANable) |
| `socketcan` | Linux SocketCAN |
| `virtual` | Software virtual bus (no hardware needed) |
| `kvaser` | Kvaser USB / PCIe |
| `ixxat` | HMS IXXAT USB / PCIe |
| `vector` | Vector VN-series (requires XL-Driver) |
| `gs_usb` | candleLight / USB2CAN (Linux) |
| `canalystii` | 创芯科技 CANalyst-II |
| `nican` | National Instruments NI-CAN |
| `systec` | SYS TEC USB-CANmodul |
| `neovi` | Intrepid neoVI |
| `usb2can` | 8devices USB2CAN |
| `cantact` | CANtact USB |
| `robotell` | Robotell USB-CAN |
| `seeedstudio` | Seeed Studio CAN HAT |
| `udp_multicast` | UDP multicast virtual bus |

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
│  · QTimer _period_timer   N ms    → periodic DBC send (on-demand)│
│  · QTimer _raw_period_timer N ms  → periodic raw Hex send        │
│  · bus.send()  ← called from main thread                        │
│  · _refresh_wave_curves() called inline from decoded callbacks   │
└──────────────────────────────────────────────────────────────────┘
         ↑  pyqtSignal  (Qt queued connection — lock-free)
┌──────────────────────────────────────────────────────────────────┐
│  CanWorker  (QThread)                                            │
│  · bus.recv(0.1)  blocking loop                                  │
│  · cantools decode of every received frame                       │
│  · capture_listener(msg)  write to ASC/BLF/CSV in-loop          │
│  · decoded signals batched at 50 Hz (EMIT_INTERVAL = 0.02 s)    │
│  · raw frames batched at ≥200 frames OR 0.1 s (RAW_INTERVAL)    │
└──────────────────────────────────────────────────────────────────┘
         ↑  pyqtSignal
┌──────────────────────────────────────────────────────────────────┐
│  ReplayWorker  (QThread)  — exists only during replay            │
│  · can.LogReader reads ASC / BLF / CSV capture files            │
│  · time.sleep() paces playback to chosen speed (0.25×–8×)       │
│  · emits decoded_received / raw_frame_received                   │
└──────────────────────────────────────────────────────────────────┘
```

**Cross-thread communication** uses `pyqtSignal`. Signals emitted from worker threads are delivered via Qt's event queue (`Qt::QueuedConnection`), keeping all UI updates on the main thread without explicit locks.

**Batch emission** in `CanWorker` uses two separate intervals:
- Decoded signals (`decoded_received`): 50 Hz (`EMIT_INTERVAL = 0.02 s`)
- Raw frames (`raw_frame_received`): flush when ≥ 200 frames queued OR 0.1 s elapsed (`RAW_INTERVAL`)

**TX echo**: PCAN does not support `receive_own_messages=True`. Sent frames are injected back into the monitor/waveform pipeline via `_echo_sent_frame` in the main window, ensuring full-duplex visibility. TX frames are always shown in the monitor table (regardless of the raw monitor toggle) and are also written to any active capture file.

**Design notes:**
- `bus.send()` runs on the main thread while `bus.recv()` runs in `CanWorker`. The PCAN C library is internally thread-safe; the `bus` object is a shared reference without an application-level lock.
- `capture_listener` is a bare attribute written by the main thread and read by `CanWorker`. Python's GIL makes the reference swap atomic.

---

## Project Structure

```
Python-Can-Upper-Computer/
├── main.py                Entry point; Ctrl+C handler via QTimer + SIGINT
├── build_exe.py           PyInstaller packaging script → dist/UpperComputer_V2.exe
├── requirements.txt       pip dependency list
├── pyproject.toml         uv / PEP 517 project metadata (Python ≥ 3.9, Windows only)
│
├── app/
│   ├── main_window.py     MainWindow — all business logic and signal wiring
│   ├── ui_layout.py       setup_ui() — 5-tab widget tree (no logic)
│   ├── can_worker.py      CanWorker(QThread) — receive loop + batch emit
│   ├── replay_worker.py   ReplayWorker(QThread) — file replay with speed control
│   └── config_utils.py    pyqtgraph global color config (background / foreground)
│
├── tests/
│   └── pcan_test.py       End-to-end PCAN hardware test (no GUI)
│
└── captures/              Capture files land here (gitignored)
```

---

## Tab Details

### Top Bar (always visible)

```
接口[pcan▼]  通道[PCAN_USBBUS1▼]  波特率[500000▼]  □CAN-FD  FD预设[500k/2M▼]
[加载 DBC]  DBC: xxx.dbc    [打开 CAN]    状态: 已连接
```

### Tab 1 — 总线监控

- Real-time table: 时间(s) / ID / DLC / 数据(Hex) / DBC解析
- Filter by ID (hex), auto-scroll toggle, one-click clear
- **启用监控** checkbox controls whether raw RX frames appear in the table; TX frames are always shown
- DBC column populated automatically when a `.dbc` is loaded; format: `sig=value  sig=value …`
- Table capped at 1 000 rows; oldest rows are removed automatically

### Tab 2 — 波形分析

- Signal list built from loaded DBC; checkbox to show/hide each curve
- Search box filters by signal name or CAN ID (e.g. `0x1F8`)
- Each signal is displayed as `0x1F8  SignalName` in the list
- Y-auto-scale toggle; ring-buffer with up to 5 000 points per signal; rendered by PyQtGraph

### Tab 3 — 报文发送

**DBC 构造区（左）**
- Full DBC message list with checkboxes (全选/取消全选); click to view signal table, check to include in batch send
- Signal table: 信号名 / 单位 / 最小值 / 最大值 / 发送值 (SpinBox per signal)
- Signal values are persisted per `frame_id` — switching rows preserves edits
- "单次发送" sends every checked message at once; missing signals fall back to minimum value or 0
- Periodic send: period (ms) spin + 启动/停止; CAN-FD auto-detected from DBC message length

**原始 Hex 发送区（右）**
- ID field (hex) + 扩展帧 / CAN-FD / BRS checkboxes
- Multi-line hex data field (8 bytes per row; auto-expands to 64 bytes when CAN-FD is checked)
- Structured description field (bidirectionally synced with hex fields):
  ```
  ID=<hex>
  Type=<S|E|F|B>    # S=standard, E=extended, F=FD, B=FD+BRS
  Length=<n>
  Data=<hex-string>
  ```
- Periodic send: period (ms) spin + 启动/停止

### Tab 4 — 捕获 & 回放

**录制**
- Format: ASC, BLF, or CSV (dropdown); filename auto-generated with timestamp → `captures/capture_YYYYMMDD_HHMMSS.<ext>`
- Button shows `● 正在录制...` while recording
- Captures both RX and TX frames (TX echoed in software)

**回放**
- Open ASC / BLF / CSV files
- Play / Pause / Stop buttons with progress percentage label
- Speed selector: 0.25× / 0.5× / 1× / 2× / 4× / 8×
- Decoded signals rendered on a dedicated PyQtGraph waveform plot during replay

### Tab 5 — 报文解析

**DBC 管理**
- Add / remove / clear multiple DBC files independently of the global DBC
- Table shows: 标识 / 文件名 / 报文数 / 信号数
- Decode priority: parse-tab DBCs tried first in load order, then falls back to the global DBC

**解析输入**
- Manual single-frame: ID + data hex → 解析 button; import directly from monitor selection via 从监控导入
- Raw text line: paste any ASC/monitor-style log line → auto-extract ID and data (skips leading timestamps and DLC)
- Log file import: open ASC / BLF / CSV / TRC / MF4 → parse up to N frames (configurable, default 5 000)

**解析结果**
- Frame table: 时间(s) / 通道 / ID / DLC / 数据(Hex) / 报文名 [DBC]
- Click any row → signal detail table: 信号名 / 原始值(hex) / 物理值 / 单位 / 最小值 / 最大值
- Multi-channel CAN log support (channel column preserved)
- Status bar: `共 N 帧 | 已解析 M 帧 | 未识别 K 帧`

---

## Tech Stack

| Component | Library |
|-----------|---------|
| GUI | PyQt5 ≥ 5.15 |
| Plotting | PyQtGraph ≥ 0.12 |
| CAN bus interface | python-can ≥ 4.0 |
| DBC parsing / encoding | cantools ≥ 37.0 |
| Numeric buffers | NumPy ≥ 1.19 |

---

## 简体中文

### 核心功能

| # | 功能 | 说明 |
|---|------|------|
| 1 | **CAN 收发** | 打开/关闭经典 CAN 或 CAN-FD 总线，实时监控报文 |
| 2 | **总线监控** | 实时表格，显示时间戳、ID、DLC、原始字节及 DBC 解析信号值 |
| 3 | **波形分析** | 从 DBC 信号列表勾选信号，绘制实时趋势曲线；支持信号搜索、Y 轴自动缩放 |
| 4 | **报文发送** | DBC 构造（整个 DBC 多消息批量发送、信号值持久化）+ 原始 Hex 发送（结构化字段双向同步）；均支持周期发送 |
| 5 | **捕获与回放** | 录制为 ASC/BLF/CSV；以可调倍速（0.25×–8×）回放，同步绘制波形 |
| 6 | **报文解析** | 多 DBC 导入 + 多种 CAN 日志文件（ASC/BLF/CSV/TRC/MF4）批量解析，支持多路 CAN 通道 |

### 运行

**pip 方式：**

```bash
git clone <repo-url>
cd Python-Can-Upper-Computer
pip install -r requirements.txt
python main.py
```

**uv 方式（Windows 推荐）：**

```bash
git clone <repo-url>
cd Python-Can-Upper-Computer
uv sync
uv run python main.py
```

**打包为 EXE（Windows）：**

```bash
python build_exe.py
# 生成文件: dist/UpperComputer_V2.exe
```

### 并发架构

详见 [Architecture](#architecture) 节（与英文共用）。

### 关键设计说明

**批量 DBC 发送**：`_dbc_send_values` 字典以 `frame_id` 为键保存每条报文的信号值，切换行时自动持久化和恢复，确保多报文同时发送时每个信号都有合法值（不会出现 cantools `required for encoding` 报错）。未编辑信号回退至最小值或 0。

**双向同步**：原始 Hex 字段与结构化描述字段之间用 `_raw_sync` 布尔标志防止循环触发。帧类型字段含义：`S`=标准帧、`E`=扩展帧、`F`=CAN-FD（无 BRS）、`B`=CAN-FD+BRS。

**TX 可见性**：PCAN 不支持 `receive_own_messages=True`，发送帧通过 `_echo_sent_frame` 软件注入到监控和波形管道，同时写入当前活跃的录制文件（如有）。TX 帧在监控表中始终显示，不受"启用监控"开关影响。

**多 DBC 解析**：`_try_decode_all` 按顺序尝试所有已加载的解析标签 DBC，最后回退全局 DBC，首次成功即返回；解析完成后状态栏显示总帧数、已解析帧数、未识别帧数。

**批量发射策略**：CanWorker 采用两套独立的 flush 节奏——解码信号以 50 Hz（每 20 ms）批量 emit；原始帧以 100 ms 或单次积累 ≥200 帧时 emit，防止高总线负载下事件队列溢出。

### 硬件测试

```bash
python tests/pcan_test.py
```

覆盖全部核心功能：经典 CAN/CAN-FD 连接、收发回环、DBC 编解码、ASC/CSV 录制、文件回放。

---

## License

Licensed under the [MIT License](LICENSE).
