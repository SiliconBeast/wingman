# CLAUDE.md — Wingman (full project context)

Read this whole file before doing anything. It is the complete handoff from a long planning/setup
conversation. Keep the "Status" section at the bottom updated as work gets done.

---

## 0. TL;DR

- **Project:** Wingman, a driver-assist (ADAS) *prototype*. A Jetson TX2 does GPU perception
  (camera → detection → distance → tracking). A PHYTEC phyBOARD-RT1170 (NXP i.MX RT1176 MCU,
  Zephyr RTOS) is the **deterministic safety supervisor**: it receives tracks over Ethernet/UDP,
  computes time-to-collision, drives alerts, and **fails safe within ~100 ms if perception goes silent**.
- **Pitch:** *"A Jetson GPU perceives hazards; an i.MX RT1170 makes the safety decision —
  deterministically — and fails safe the moment perception goes silent."*
- **Contest:** All About Circuits / PHYTEC / NXP **2026 Embedded Design Contest**.
  **Final submission deadline: Wed Sep 30, 2026. Plan to submit Tue Sep 29.** Winners Oct 15.
  Prizes $2,500 / $1,500 / $500.
- **What's judged:** the RT1170. The TX2 is supporting cast. If time is short, the RT1170 side wins.
- **Current date when this was written:** Sat Sep 26, 2026, ~10:20 pm Mountain (America/Edmonton).
- **RT1170 status: nothing flashed yet.** Phase 1 has not started. This is the #1 risk.

---

## 1. Who you're working with

- Suvir, 4th-year Computer Engineering, University of Alberta. Strong hardware/PCB/RF background.
  **New to Zephyr, RTOS work, and NXP tooling.** Explain what each step does and why, briefly.
- Prefers direct, terse, blunt communication. No fluff. Give exact commands to paste.
- Solo builder. Also doing coursework; time is scarce.
- Has been burned repeatedly by setup rabbit holes (VMs, USB passthrough, old installers).
  **Bias hard toward the path that has already worked.** If something fails twice the same way,
  stop and pick a different route instead of a third retry.
- Physical actions (boot switches, cables, power cycling, pressing buttons, filming) are his.
  Say exactly what to do, then wait for confirmation.
- Honesty rules: **never invent or estimate a benchmark number**; only report measured values.
  Call it an "ADAS prototype" / "proof-of-concept", never a certified ADAS (no ISO 26262).
- Secondary goal: a GPU/CUDA portfolio piece for AMD/NVIDIA internship applications
  (the CUDA Kalman kernel + profiling numbers).

---

## 2. Machines and their state

### 2.1 Odroid-H3 — main dev host  (hostname `desktop-2eriav9` on Windows)
- x86_64, **64 GB RAM**, 2 TB storage. AMI BIOS (Del = setup, F4 = save).
- **Dual boot: Windows 11 + Ubuntu 26.04.** He is currently on **Windows** and prefers to stay there
  for now (other coursework). Claude Code may be running here on Windows.
- Windows home: `C:\Users\suvir`. Project folder intended: `C:\Users\suvir\wingman`.
- Has a VirtualBox Ubuntu 18.04 VM (`C:\Users\suvir\VirtualBox VMs\Ubuntu`). **Do not use it for
  USB flashing** — VirtualBox USB passthrough failed 3× mid-flash (error code 11). Fine for nothing else we need.
- Ubuntu 18.04 live-USB installs fail on this hardware (casper/overlayfs + NVMe issue). Irrelevant now.
- Ubuntu 26.04 native: had SDK Manager installed (unused now). **This is the preferred RT1170 build/flash host.**

### 2.2 Sony VAIO laptop (Core i3) — native Ubuntu
- Successfully flashed the TX2 with SDK Manager natively (JetPack 4.6.6). Ubuntu version likely 18.04/20.04.
- Too old for Claude Code (needs Ubuntu 20.04+) and for Zephyr 4.1 tooling (needs newer Python).
  Only use if the Odroid can't do something.

### 2.3 Jetson TX2 developer kit — perception node
- **Freshly flashed: JetPack 4.6.6, L4T R32.7.6** (verified with `cat /etc/nv_tegra_release`).
- Ubuntu 18.04 aarch64, **CUDA 10.2** (`nvcc` V10.2.300 at `/usr/local/cuda/bin/nvcc`),
  TensorRT 8.2 present (`/usr/src/tensorrt`), cuDNN 8, **Python 3.6** (no dataclasses; f-strings OK).
- GPU: Pascal GP10B, **compute capability 6.2 → `sm_62`**. 256 CUDA cores. **No INT8 inference support**
  on TX2 → quantization story is FP32 vs **FP16**.
- Login: user **`jetson`**; password is the one set in SDK Manager's pre-config screen (Suvir knows it).
  Hostname `ubuntu`.
- Network right now: **WiFi on iPhone hotspot, IP `172.20.10.13`** (may change on reconnect;
  re-check with `hostname -I`; ignore `172.17.0.1` = docker0). `eth0` is free for the RT1170 link.
- SSH server: running and enabled. Passwordless key auth from the Odroid is being set up.
- Onboard camera: OV5693 CSI module. Use `nvarguscamerasrc` (GStreamer) / `csi://0` (jetson-inference).
- Performance mode before any benchmark: `sudo nvpmodel -m 0 && sudo jetson_clocks`.
- `git cmake libpython3-dev python3-numpy` already installed. jetson-inference NOT yet cloned/built.
- 32 GB eMMC — watch disk space (`df -h`). microSD slot available for data.
- Firefox/Chromium on 18.04 are too old for many sites; do web stuff on the Odroid.

### 2.4 phyBOARD-RT1170 (a.k.a. **phyBOARD-Atlas i.MX RT1170**) — the judged board
- SoM phyCORE-RT1170, NXP **MIMXRT1176**: Cortex-**M7 @ ~1 GHz (SysTick 996 MHz)** + Cortex-**M4 @ 400 MHz**.
  2 MB SRAM, 64 MB SDRAM, 16 MB QSPI NOR (M7 boots from `0x30000000`).
- **Zephyr board target: `phyboard_atlas/mimxrt1176/cm7`** (M4: `.../cm4`).
- Useful onboard hardware (per Zephyr board docs):
  - **ICM-40627 6-axis IMU** on LPI2C5 (`GPIO_LPSR_08/09`) — shared bus with codec + display.
  - RGB user LED; red/green LEDs on SOM (`GPIO_SNVS_08/09`) and carrier (`GPIO_AD_14` red, `GPIO_LPSR_13` green).
  - User button `GPIO_AD_35`.
  - **Two Ethernet:** 100 Mbit (ENET, KSZ8081 PHY) and 1 Gbit (ENET_1G RGMII, DP83867 PHY).
    Which RJ45 is live under the default BSP is not documented — find out with `net iface`.
  - CAN FD (1x on 2x5 header), RS-232 (LPUART8, 2x5 header — RS-232 levels, not 3.3 V TTL),
    UART via 60-pin expansion, MIPI-DSI, MIPI-CSI, audio codec TLV320AIC3110, microSD, TPM,
    hardware watchdogs (WDOG, EWM), CAAM crypto, PXP 2D engine.
  - Console: M7 on **LPUART1**, M4 on LPUART6, both via FTDI on **micro-USB X15**
    (Linux: `/dev/ttyUSB0` = M7 console; Windows: two COM ports, use the **lower** number). **115200 8N1.**
- Power: **X9 USB-C, use the supplied 5 V PSU** (phone chargers can't supply enough). Safe shutdown: hold S3 for 3 s.
- Switches: **S7 must be OFF.** **S5 = boot mode:** QSPI Flash (normal boot) or USB-OTG Serial Downloader (flashing).
  Check orientation against PHYTEC's photos before flipping.
- Factory firmware: Zephyr shell (`uart:~$`), green heartbeat LED. `kernel version` → 4.1.0.
- **Unknown:** whether a MIPI-DSI display panel (NXP RK055HDMIPI4MA0) came in the kit. Ask Suvir.
  Only matters for the HUD stretch goal.
- Docs:
  - PHYTEC BSP: https://docs.phytec.com/projects/zephyr-phycore-rt1170/en/bsp-zephyr-rt1170-pd25.1.1/
    (Quickstart, Building the Firmware, Installing the Firmware → Flashing with USB-OTG, Pre-Built Binaries).
    Ethernet/Display pages are still "we're working on this" stubs → use upstream Zephyr docs.
  - Zephyr board page: https://docs.zephyrproject.org/latest/boards/phytec/phyboard_atlas/doc/index.html
  - PHYTEC app manifest repo: https://github.com/phytec/zephyr-phytec-application (code lives in `v4.1.0-phy*` branches; `main` is empty).

### 2.5 Other hardware
- Raspberry Pi 5 camera module: spare, unused.
- **Not owned / out of scope:** radar, ToF, GPS, external IMU, haptic driver. Don't plan around them.
  (The RT1170's onboard IMU replaces the need for an external one.)
- Nice-to-have: a cheap USB logic analyzer (Saleae clone) or lab oscilloscope for latency cross-checks.

---

## 3. Lessons already paid for (don't repeat)

1. VirtualBox USB passthrough cannot sustain Jetson/NXP flashing. Use native Linux or native Windows USB.
2. Old installers (JetPack 3.1) → dead end. TX2 is now on 4.6.6; never reflash unless it's bricked.
3. The VM had no Guest Additions → no clipboard. Avoid workflows that require typing long commands by hand.
4. `NO_PUBKEY` apt errors on NVIDIA repos → fetch key over HTTPS (`wget -qO - <url> | sudo apt-key add -`), keyservers were blocked.
5. When a tool says "Automatic Setup" it assumes the device is already running the expected OS. Don't trust it after a failed flash.

---

## 4. RT1170 toolchain

### 4.1 Preferred: Odroid booted into Ubuntu 26.04 (native)
```bash
sudo apt update
sudo apt install --no-install-recommends git cmake ninja-build gperf ccache dfu-util \
  device-tree-compiler wget python3-dev python3-pip python3-venv python3-setuptools \
  python3-tk python3-wheel xz-utils file make gcc g++ libsdl2-dev libmagic1 minicom
sudo usermod -aG dialout $USER     # re-login once, for /dev/ttyUSB*

# Zephyr SDK (ARM only)
cd ~
wget https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v0.17.0/zephyr-sdk-0.17.0_linux-x86_64_minimal.tar.xz
tar xvf zephyr-sdk-0.17.0_linux-x86_64_minimal.tar.xz
cd zephyr-sdk-0.17.0 && ./setup.sh -c -h -t arm-zephyr-eabi

# Workspace (PHYTEC manifest pins Zephyr 4.1 + NXP HAL)
mkdir -p ~/wingman-rt && cd ~/wingman-rt
python3 -m venv .venv && source .venv/bin/activate
pip install west pyelftools
west init -m https://github.com/phytec/zephyr-phytec-application --mr v4.1.0-phy2 .
west update
west zephyr-export
pip install -r zephyr/scripts/requirements.txt
```
Every new terminal: `cd ~/wingman-rt && source .venv/bin/activate && source zephyr/zephyr-env.sh`

Build check: `west build -p always -b phyboard_atlas/mimxrt1176/cm7 zephyr/samples/hello_world`
→ `build/zephyr/zephyr.bin`.
(PHYTEC's own demo: `west build -p auto -b phyboard_atlas/mimxrt1176/cm7 zephyr-phytec-application/app/rt1170 -- -DSHIELD=rk055hdmipi4ma0`)

If `pip install` complains about externally-managed environments, you're outside the venv.
Ubuntu 26.04 is newer than PHYTEC tested (they list 20.04+/24.04+); if a host package name
differs, find the equivalent rather than downgrading.

### 4.2 Flashing over USB-OTG with `blhost` (no debug probe needed)
Needs `ivt_flashloader.bin` from PHYTEC **Pre-Built Binaries** page (not produced by the build).
```bash
python3 -m venv ~/spsdk-venv && source ~/spsdk-venv/bin/activate && pip install spsdk
# udev rules (Linux):
cat <<'EOF' | sudo tee /etc/udev/rules.d/50-nxp.rules
SUBSYSTEM=="hidraw", KERNEL=="hidraw*", ATTRS{idVendor}=="0d28", MODE="0666"
SUBSYSTEM=="hidraw", KERNEL=="hidraw*", ATTRS{idVendor}=="1fc9", MODE="0666"
SUBSYSTEM=="hidraw", KERNEL=="hidraw*", ATTRS{idVendor}=="15a2", MODE="0666"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
```
Flash cycle (Suvir does the physical parts):
1. Power off. **S5 → USB-OTG Serial Downloader.** S7 OFF. Cable: RT1170 **USB-OTG** micro-USB port → host.
2. Power on. Check: `blhost -u 0x1fc9:0x013d get-property 1` → "Success".
3. Run `tools/flash.sh build/zephyr/zephyr.bin` which must do:
```bash
blhost -u 0x1fc9:0x013d load-image ivt_flashloader.bin
blhost -u 0x15a2:0x0073 get-property 1
blhost -u 0x15a2:0x0073 fill-memory 0x00002000 4 0xCF900001
blhost -u 0x15a2:0x0073 configure-memory 9 0x00002000
blhost -u 0x15a2:0x0073 fill-memory 0x00002000 4 0xC0000007
blhost -u 0x15a2:0x0073 configure-memory 9 0x00002000
blhost -u 0x15a2:0x0073 flash-erase-region 0x30000000 0x80000   # 512 KiB; bump if image grows
blhost -u 0x15a2:0x0073 write-memory 0x30000000 "$1"
```
(`flash.sh` should fail loudly on any step, check the image is < erase size, and print the next physical step.)
4. Power off. **S5 → QSPI Flash.** Power on. Watch the console.

Alternative: NXP MCU-Link / J-Link probe on the expansion-header JTAG → `west flash` / `west debug`, no switch flipping. Not owned.

### 4.3 If staying on Windows (untested fallback — try only if Suvir refuses to boot Ubuntu)
- Serial console: PuTTY/TeraTerm on the lower FTDI COM port, 115200 8N1.
- Flashing: `pip install spsdk` in native Windows Python; `blhost` talks HID, which Windows supports
  without drivers. The same `flash.sh` commands work as a `.ps1`/`.bat`. Plausible, not verified.
- Building: Zephyr officially supports native Windows builds, or build inside **WSL2 (Ubuntu 24.04)**
  and flash from Windows using the resulting `zephyr.bin` (avoids USB-in-WSL entirely).
- Don't attempt USB passthrough into WSL/VMs for flashing.

---

## 5. System architecture

```
 [OV5693 CSI cam] → TX2: detectNet (TensorRT, SSD-Mobilenet-v2)
                        → pinhole distance + lateral offset
                        → Kalman tracker (NumPy → CUDA kernel via ctypes)
                        → UDP perception packet @30 Hz ──eth──► RT1170 (192.168.10.2:5005)
                                                               │ net_rx → validate → decide → LEDs/GPIO/log
                                                               │ heartbeat watchdog → FAILSAFE
                                                               │ onboard IMU (braking/swerve context)
                   ◄──────────────── UDP status packet ────────┘ (to 192.168.10.1:5006)
 TX2 draws boxes coloured by the RT1170's verdict (so viewers see which chip decides).
```
- Link: direct Ethernet cable, static IPs. TX2 `eth0` 192.168.10.1/24, RT1170 192.168.10.2/24.
  TX2: `sudo nmcli con add type ethernet ifname eth0 con-name wingman ipv4.method manual ipv4.addresses 192.168.10.1/24 && sudo nmcli con up wingman`
- Why this split (use in writeup): Linux + GPU = fast but non-deterministic and can crash;
  the MCU = deterministic, instant boot, hardware watchdog, independent of perception health.
  Mirrors how real vehicles split perception computers from safety ECUs.

---

## 6. Protocol (single shared header `protocol/wingman_proto.h`, mirrored in `tx2/wingman_proto.py`)

All little-endian, packed. Ports: perception → RT1170 **5005**; status → TX2 **5006**.
CRC: **CRC-16/CCITT-FALSE** (poly 0x1021, init 0xFFFF, no reflection) over every byte before the crc field.
- Zephyr: `crc16_itu_t(0xFFFF, buf, len)` (`#include <zephyr/sys/crc.h>`, `CONFIG_CRC=y`)
- Python: `binascii.crc_hqx(data, 0xFFFF)`
- Unit-test both sides against the same known vector (e.g. b"123456789" → 0x29B1).

```c
#define WM_MAGIC_PERCEPTION 0x574D  /* "WM" */
#define WM_MAGIC_STATUS     0x5753  /* "WS" */
#define WM_VERSION          1
#define WM_MAX_TRACKS       8

enum wm_class { WM_CLS_UNKNOWN=0, WM_CLS_PERSON=1, WM_CLS_CAR=2, WM_CLS_BIKE=3, WM_CLS_TRUCK=4 };
enum wm_state { WM_BOOT=0, WM_WAIT_LINK=1, WM_NOMINAL=2, WM_CAUTION=3, WM_WARNING=4, WM_FAILSAFE=5 };
/* fault_flags bits */
#define WM_FAULT_HEARTBEAT (1u<<0)
#define WM_FAULT_CRC       (1u<<1)
#define WM_FAULT_SEQ_GAP   (1u<<2)
#define WM_FAULT_SEQ_OLD   (1u<<3)  /* duplicate / out-of-order */
#define WM_FAULT_BAD_LEN   (1u<<4)
#define WM_FAULT_WDT_RESET (1u<<5)  /* last boot was a watchdog reset */

typedef struct __attribute__((packed)) {
    uint16_t id;
    uint8_t  cls;           /* enum wm_class */
    uint8_t  conf;          /* 0..255 = 0..1 */
    int32_t  dist_mm;       /* longitudinal distance ahead, >0 */
    int32_t  lat_mm;        /* lateral offset, +right */
    int32_t  closing_mm_s;  /* >0 = getting closer */
} wm_track_t;               /* 16 bytes */

typedef struct __attribute__((packed)) {
    uint16_t   magic;       /* WM_MAGIC_PERCEPTION */
    uint8_t    version;
    uint8_t    n_tracks;    /* 0..8 */
    uint32_t   seq;         /* +1 per frame */
    uint64_t   tx_time_us;  /* TX2 monotonic clock */
    wm_track_t tracks[WM_MAX_TRACKS];
    uint16_t   crc16;
} wm_perception_t;          /* 146 bytes, always sent full-size */

typedef struct __attribute__((packed)) {
    uint16_t magic;         /* WM_MAGIC_STATUS */
    uint8_t  version;
    uint8_t  state;         /* enum wm_state */
    uint32_t seq_echo;      /* seq of the frame that produced this decision */
    uint64_t tx_time_us_echo; /* copied back → TX2 computes round trip */
    int32_t  ttc_ms;        /* -1 = no threat */
    uint16_t threat_id;     /* 0xFFFF = none */
    uint16_t fault_flags;
    uint32_t decision_latency_ns; /* packet arrival → outputs written */
    int16_t  imu_ax_mg;     /* longitudinal accel, milli-g */
    int16_t  imu_ay_mg;     /* lateral accel */
    uint8_t  maneuver;      /* 0 unknown,1 steady,2 brake,3 accel,4 swerve */
    uint8_t  reserved;
    uint16_t crc16;
} wm_status_t;
```
Add `_Static_assert(sizeof(...) == N)` on both structs and `struct.calcsize` asserts in Python.

---

## 7. RT1170 firmware design (`rt1170/`, Zephyr app)

### 7.1 Threads (priority: lower number = higher priority in Zephyr preemptive range)
| Thread | Prio | Job |
|---|---|---|
| `decide` | highest (e.g. 2) | pop frame from `k_msgq`, pick most-threatening in-path track, TTC, state machine, drive outputs, stamp latency, feed HW watchdog, queue status |
| `net_rx` | 3 | blocking `zsock_recvfrom` on :5005; stamp arrival with cycle counter **first thing**; validate len/magic/version/CRC/seq; push to `k_msgq` |
| heartbeat | `k_timer` 10 ms | if `now - last_valid_rx > 100 ms` → force FAILSAFE (set fault bit) |
| `status_tx` | 5 | send `wm_status_t` to 192.168.10.1:5006 |
| `imu` | 6 | sample ICM-40627 at 100–200 Hz, low-pass, expose ax/ay; maneuver detect |
| `tinyml` (stretch) | 8 (lowest) | classifier inference on IMU windows |
| shell | default | `wm stats`, `wm state`, `wm reset`, `wm thresh <caution_ms> <warn_ms>` |

### 7.2 Decision logic
- In-path filter: `|lat_mm| < 1500`. Ignore tracks with `conf < ~0.4` or `dist_mm <= 0`.
- TTC = `dist_mm / closing_mm_s` (ms) only when `closing_mm_s > 100` (≈0.1 m/s deadband). Else no threat.
- Threat = in-path track with minimum TTC.
- State machine with hysteresis (defaults, tunable via shell):
  - enter CAUTION: TTC < 2500 ms; leave when TTC > 2800 ms
  - enter WARNING: TTC < 1200 ms; leave when TTC > 1500 ms
  - also WARNING if in-path distance < 3 m and closing (close-range override)
  - BOOT → WAIT_LINK (no packets yet) → NOMINAL on first valid frame
  - **any → FAILSAFE** on heartbeat loss (>100 ms) or ≥5 consecutive CRC/format failures
  - FAILSAFE → NOMINAL only after 10 consecutive valid, in-sequence frames
- IMU context (Phase 7): if longitudinal decel > ~0.3 g sustained ~200 ms (driver already braking),
  WARNING → CAUTION (nuisance-alert suppression). Swerve during a threat → log a near-miss event.
  Impact spike > ~2 g → latch event flag, keep last 2 s of decisions in a RAM ring buffer ("black box").

### 7.3 Outputs
- LEDs: NOMINAL green; CAUTION amber (red+green) or slow red blink; WARNING fast red blink (~8 Hz);
  FAILSAFE solid red; WAIT_LINK slow green blink. Use devicetree aliases — read
  `zephyr/boards/phytec/phyboard_atlas/*.dts*` and `zephyr/dts/arm/phytec/phycore_rt1170_common.dtsi`
  for `led0`/`led1` etc. before hard-coding anything.
- Two spare GPIOs on the 60-pin expansion: `GPIO_RX` high on packet arrival, `GPIO_ALERT` toggles on
  output write → logic analyzer cross-check of the software latency number. Pick pins from the
  PHYTEC expansion pinout; confirm free in the devicetree.
- Console log line on every state change: `[t_ms] NOMINAL→WARNING id=3 ttc=980ms dist=9.8m`.
- Optional: audio chime via codec; CAN frame broadcasting state (needs USB-CAN adapter to show).

### 7.4 Watchdog
- Enable on-chip WDOG (`CONFIG_WATCHDOG=y`, see `zephyr/samples/drivers/watchdog`). Timeout ~200 ms, fed by `decide` only.
  **(Implemented as 500 ms: the i.MX WDOG1 driver's minimum. See §17.)**
- At boot, read reset cause (`hwinfo_get_reset_cause`, `CONFIG_HWINFO=y`) → set `WM_FAULT_WDT_RESET`, log it.
- Demo: shell command `wm hang` that deliberately stalls `decide` → board resets and reports it.

### 7.5 Instrumentation (this is the entry's evidence)
- Timestamps: `k_cycle_get_32()` (≈1 ns resolution at 996 MHz, wraps every ~4.3 s — only use for deltas)
  or the `timing` API (`CONFIG_TIMING_FUNCTIONS=y`). Convert with `k_cyc_to_ns_floor64()`.
- Track: decision latency (arrival→outputs) min/mean/max/p99 + histogram (e.g. 1 µs buckets to 200 µs);
  inter-decision interval jitter; failover time (last valid packet → FAILSAFE); counts of CRC fails,
  seq gaps, duplicates; watchdog resets.
- `wm stats` prints all; `wm stats reset` clears. Also stream summary in status packets.

### 7.6 prj.conf starting point
```
CONFIG_NETWORKING=y
CONFIG_NET_IPV4=y
CONFIG_NET_IPV6=n
CONFIG_NET_UDP=y
CONFIG_NET_SOCKETS=y
CONFIG_NET_CONFIG_SETTINGS=y
CONFIG_NET_CONFIG_MY_IPV4_ADDR="192.168.10.2"
CONFIG_NET_CONFIG_MY_IPV4_NETMASK="255.255.255.0"
CONFIG_NET_SHELL=y
CONFIG_SHELL=y
CONFIG_LOG=y
CONFIG_GPIO=y
CONFIG_CRC=y
CONFIG_WATCHDOG=y
CONFIG_HWINFO=y
CONFIG_I2C=y
CONFIG_SENSOR=y
CONFIG_TIMING_FUNCTIONS=y
CONFIG_MAIN_STACK_SIZE=4096
```
Check that the ICM-40627 has a Zephyr sensor driver in this tree (`grep -r icm40627 zephyr/drivers`).
If not, read registers directly over I2C (it's on the same bus as the codec/display).

### 7.7 Dual-core (stretch, only if everything else works)
M4 as an independent monitor of the M7's heartbeat via the Messaging Unit / IPM
(`phyboard_atlas/mimxrt1176/cm4`, Zephyr IPM samples; shared memory at 0x200C0000, only first 16 KB MPU-configured).

---

## 8. TX2 perception (`tx2/`)

### 8.1 jetson-inference
```bash
cd ~ && git clone --recursive --depth=1 https://github.com/dusty-nv/jetson-inference
cd jetson-inference && mkdir build && cd build && cmake ../
make -j$(nproc) && sudo make install && sudo ldconfig   # 30–60 min on TX2
```
Model downloader: keep **SSD-Mobilenet-v2**. **Skip** the PyTorch install. Smoke test: `detectnet csi://0`
(first run builds a TensorRT engine; a few minutes, once). Run long builds with `nohup ... &> build.log &`.

### 8.2 perception.py
- Input: `csi://0` live or `file://path.mp4` replay (dashcam clips / BDD100K samples — repeatable demos).
- Keep classes person/car/bicycle/motorcycle/bus/truck.
- **Distance (pinhole):** `dist = f_px * H_real / box_h_px`; `lat = (u_center - cx) * dist / f_px`.
  H_real: person 1.70 m, car 1.50 m, truck 3.0 m, bike 1.1 m (state as a limitation).
  Calibrate `f_px` once: person of known height at tape-measured 3/5/8 m → `f_px = box_px * dist / H`, average.
  Save in `tx2/calib.json`. Occluded/truncated boxes at image edges → lower confidence.
- **Tracker:** constant-velocity KF, state `[lat, dist, v_lat, v_dist]` (reuse `kernels/kalman_math.h`
  math / `python/reference_kalman.py`). Greedy nearest-neighbour association, 2 m gate (scale with distance),
  birth on unmatched detection, delete after 10 misses, only publish tracks with ≥3 hits.
  `closing_mm_s = -v_dist`.
- Pack `wm_perception_t` with `struct` (Python 3.6-compatible), send at camera rate (~30 Hz).
  Always send, even with 0 tracks — the RT1170 uses packets as the heartbeat.
- Receive `wm_status_t` on :5006 (non-blocking); overlay boxes coloured by RT1170 state + TTC,
  round-trip latency (`now - tx_time_us_echo`), RT1170 decision latency, FPS.
- Log everything to CSV for the results section.
- Later: swap NumPy tracker for the CUDA kernel via `nvcc -shared -Xcompiler -fPIC` + `ctypes`.

### 8.3 CUDA kernel work (portfolio piece, already written)
- `kernels/kalman_math.h` — `__host__ __device__` CV Kalman predict/update/init, hand-expanded F/H (no general matmul),
  closed-form 2×2 S⁻¹, snapshots rows before covariance update. Verified vs NumPy
  (60-frame test: vel est 4.98/1.96 vs true 5.0/2.0; 1000 tracks × 200 frames: mean vel err 0.04 m/s, |P−Pᵀ| ~7e-9).
- `kernels/kalman_tracker.cu` — thread-per-track KF bank. v1 AoS vs v2 SoA (coalesced `[k*n+i]`), same math;
  CPU reference; warm-up launch; cudaEvent timing; N sweep 64…262144; correctness check vs CPU.
  (Has `#include <cstring>` fix.) Build: `make` (sm_62). Profile: `make profile`
  (`nvprof --metrics gld_efficiency,gst_efficiency,achieved_occupancy`).
- Expected story: GPU loses at small N (launch overhead), wins above a crossover N; SoA beats AoS on gld_efficiency.
  **Report only measured numbers.** Real ADAS track counts (10–50) are below the crossover — say so honestly.
- `kernels/verify_math.cpp` — host-only math check (`g++ -O2 verify_math.cpp && ./a.out` → "MATH OK").
- Status: files not yet on the TX2; benchmark not yet run.
- **REWRITTEN Sun Sep 27** (the originals were lost — never reached the repo or the TX2). The
  verification numbers above came from the *old* code and no longer apply; the new code's own
  measured numbers are in §17. See §17 for what changed (P stored as upper triangle, equal occupancy).

---

## 9. Test tools (`tools/`, run on the Odroid or TX2, plain Python 3)

- `scenario_player.py --target 192.168.10.2 --scenario approach` — scripted tracks at 30 Hz:
  - `approach`: car from 40 m closing at 10 m/s → expect NOMINAL→CAUTION at TTC 2.5 s→WARNING at 1.2 s
  - `stationary`: object at 15 m, not moving → no alert
  - `cut_in`: track appears at 8 m closing 3 m/s → fast escalation
  - `lateral_pass`: close object with |lat| > 1.5 m → no alert
  - `follow`: matched speed at 20 m → no alert
  - `multi`: several tracks, only one in-path threatening
- Fault injection flags: `--drop 0.1`, `--burst-loss 200ms@5s`, `--corrupt-crc 0.05`, `--reorder`,
  `--duplicate`, `--stop-at 5s` (simulates Jetson crash), `--rate 15`.
- Each scenario has an expected state timeline; script checks status packets and prints PASS/FAIL.
- `dashboard.py` — live plot of state, TTC, decision latency (matplotlib), for video footage.
- `flood.py` — blast junk UDP at the RT1170 during a scenario to show latency stays flat (stress test).
- Build this **before** perception, so the RT1170 can be developed and proven independently of the camera.

---

## 10. TinyML (stretch — only if Phases 1–6 work by Sun Sep 27 night)

Driving-maneuver classifier on the M7 from onboard IMU windows: classes steady / brake / accel / swerve.
- Template: `zephyr/samples/modules/tflite-micro/magic_wand` (needs optional modules:
  `west config manifest.group-filter -- +optional && west update`).
- Data: logging firmware streams 100 Hz accel+gyro over UDP; `tools/imu_logger.py` with keypress labels.
  Collect as a **passenger** (never while driving) or on a rolling cart (note realism limit). ≥60 windows/class.
- Model: 1D CNN, input 200×6, int8 quantized, < 50 KB, `xxd -i model.tflite > model_data.cc`.
- Measure: inference time, safety-loop latency with vs without classifier running, confusion matrix, flash/RAM.
- **Fallback:** threshold-based IMU logic (7.2) and mention TinyML as future work.

---

## 11. Measurements to collect (Phase 8)

| Metric | Method | Report |
|---|---|---|
| RT1170 decision latency | cycle counter, ≥10k samples; logic-analyzer cross-check | min/mean/p99/max + histogram |
| Decision jitter | inter-decision interval | std dev / max deviation |
| Failover time | `--stop-at`, ×50 | mean/max (should be ~100 ms ± one 10 ms tick) |
| Latency under stress | same, with `flood.py` + shell spam | compare to unloaded |
| Round-trip TX2↔RT1170 | `tx_time_us_echo` | mean/p99 |
| End-to-end camera→alert | camera pipeline time + above | honest estimate, labelled |
| Scenario test matrix | Section 9 | table of PASS/FAIL |
| Perception FPS | TX2 log | FP32 vs FP16 if both available |
| CUDA kernel | `kalman_tracker` sweep + nvprof | crossover N, AoS vs SoA |

---

## 12. Demo video (2–3 min) — Mon Sep 28

1. Pitch over footage of both boards.
2. Architecture diagram: eyes (GPU) vs reflexes (MCU), why.
3. Live: person walks toward camera → boxes amber → red; RT1170 LED matches.
4. **Money shot:** unplug the TX2 Ethernet cable → RT1170 solid red within ~100 ms; show logic-analyzer trace / console.
5. Watchdog demo (`wm hang` → reset → fault reported).
6. IMU: tilt/slide the board → braking suppression (or TinyML class shown).
7. Numbers: latency histogram, failover time, test matrix.
8. Future: radar/GPS, M4 independent monitor, custom carrier board around the 30×30 mm SoM.
Tabletop or parked car with replayed footage only. **Never test while driving.**

---

## 13. Submission (Tue Sep 29)

Required by the contest rules: project title + one-sentence elevator pitch; Bill of Materials
(hardware, software, tools); full explanation of how it works; photos and/or videos; supporting
resources (code, schematics, CAD if applicable); all form fields; English; before the deadline.
Must use the phyBOARD-RT1170 and highlight real-time performance and connectivity.

Write-up outline:
1. Title + pitch  2. Problem (perception fast but unreliable; safety needs determinism)
3. Architecture + eyes/reflexes table  4. How it works (protocol, threads, state machine, watchdog, IMU, TX2 pipeline)
5. **Results** (Section 11 numbers, test matrix, video)  6. Why the RT1170 (determinism, cycle counter,
watchdog, dual Ethernet, onboard IMU, instant boot, security features)  7. Honest limitations
(monocular distance assumptions, prototype, not certified)  8. Future work  9. BOM  10. GitHub + video links.

BOM: phyBOARD-RT1170 dev kit; Jetson TX2 dev kit (+19 V PSU); Cat5e/6 cable; micro-USB ×2; RT1170 5 V USB-C PSU;
optional logic analyzer. Software: Zephyr 4.1 (PHYTEC BSP v4.1.0-phy2), Zephyr SDK 0.17.0, NXP SPSDK/blhost,
JetPack 4.6.6 (L4T R32.7.6, CUDA 10.2, TensorRT 8.2), jetson-inference, Python 3, (TFLite Micro if done).

Submit via the All About Circuits account used for the idea entry:
https://www.allaboutcircuits.com/giveaways/2026-design-contest-using-phytecs-phyboard-rt1170-development-kit/
Rules PDF: https://www.allaboutcircuits.com/uploads/articles/Contest_Rules_PHYTEC_NXP_2026_Design_Contest.pdf

---

## 14. Repo layout (GitHub, public at submission)
```
wingman/
  CLAUDE.md
  README.md                  # mirrors the write-up
  protocol/wingman_proto.h
  rt1170/                    # Zephyr app: CMakeLists.txt, prj.conf, app.overlay, src/*.c
  tx2/                       # perception.py, tracker.py, wingman_proto.py, calib.json
  kernels/                   # CUDA Kalman bank + Makefile
  python/reference_kalman.py
  tools/                     # flash.sh, scenario_player.py, dashboard.py, flood.py, imu_logger.py
  docs/                      # diagrams, results CSVs, plots, test matrix, photos
```

---

## 15. Schedule, priorities, fallbacks

| Day | Must finish |
|---|---|
| Sat Sep 26 (night) | Claude Code + SSH set up; kernel benchmark on TX2; start jetson-inference build overnight |
| **Sun Sep 27** | **P1 RT1170 bring-up + flash loop**, P2 Ethernet ping, P3 protocol header, start P4 firmware |
| Mon Sep 28 | P4 firmware done, P5 scenario tools + test matrix, P6 perception live, P8 measurements, P10 video |
| Tue Sep 29 | P11 write-up, repo cleanup, **submit** |
| Wed Sep 30 | buffer only |

Priority order if time runs out: **P1 → P2 → P3 → P4 → P5 → P8 → P10 → P11** is a complete winning-quality
entry by itself (scenario player stands in for perception). Then P6 perception, then IMU, then TinyML/HUD/dual-core.

Fallbacks:
- blhost won't connect → re-check S5 orientation, other micro-USB port, `dmesg`, udev rules; ask PHYTEC support.
- No Ethernet link → try the other RJ45; `net iface`; check which ENET the devicetree enables; last resort UART.
- jetson-inference fails → demo with scenario_player + replayed recorded tracks.
- IMU driver missing → raw I2C, or drop IMU.
- Anything eats >2 h with no progress → stop, tell Suvir, cut scope.

---

## 16. Status (keep updated)
Legend: [x] done · [~] code done + verified off-target, **not yet on hardware** · [ ] not started
- [x] TX2 reflashed to JetPack 4.6.6; CUDA/TensorRT verified; SSH running (172.20.10.13, hotspot)
- [~] CUDA Kalman kernel + verification: **rewritten** (originals lost) — `kernels/kalman_math.h`,
      `kalman_tracker.cu`, `verify_math.cpp`, `Makefile`, `python/reference_kalman.py`. Math verified
      on host; `.cu` compiles for sm_62 (nvcc 12.0) and its logic passes under serial emulation.
      **Never run on a GPU yet** → on the TX2: `cd kernels && make && ./kalman_tracker --csv ../docs/kernel_sweep.csv`
      (copy the new files over the TX2's old `~/wingman/kernels/Makefile`).
- [x] Passwordless SSH Odroid → TX2 (ed25519 key, verified with `BatchMode=yes` — no password fallback)
- [ ] Kernel benchmark run on TX2 (paste table into docs/results)
- [x] Camera hardware confirmed working: `nvarguscamerasrc` captures real frames (OV5693, sensor
      modes up to 2592x1944 detected). First single-frame grab came out black — auto-exposure
      hadn't converged in ~0.4s; a 60-frame/~2s capture produced a correct image, visually verified.
- [x] jetson-inference **built successfully** on the TX2 (all binaries: detectnet, imagenet, posenet,
      camera-capture, python bindings for 2.7/3.6). Two build issues hit and fixed:
      (1) the `sudo apt-get install` inside `CMakePreBuild.sh` silently failed over a non-TTY SSH
      session ("no tty present and no askpass program specified") — had to be run interactively by
      Suvir in his own terminal; (2) `-lnpymath` link failure for the Python 3.6 bindings — the lib
      exists (`/usr/lib/python3/dist-packages/numpy/core/lib/libnpymath.a`) but wasn't on the linker
      search path; fixed via `-DCMAKE_SHARED_LINKER_FLAGS`/`-DCMAKE_EXE_LINKER_FLAGS` pointing at it,
      no sudo needed. `sudo make install && sudo ldconfig` still pending (Suvir's step) — until then
      `import jetson_inference` fails system-wide; **`csi://0` live detectNet path still untested.**
- [x] `tx2/` code verified on the TX2's real Python 3.6.9 (not just native_sim): `tests/test_proto.py`
      and `tests/test_tracker.py` both pass in full (CRC vector, struct sizes 146/36, C↔Python
      byte-identical, tracker distance/velocity estimates correct). All four `tx2/*.py` files
      syntax-clean on 3.6.9.
- [x] **Full live pipeline proven on real hardware**: `perception.py --input csi://0` ran for a
      full 10-minute soak (`timeout 600`), zero crashes/errors. SSD-Mobilenet-v2 TensorRT engine
      auto-downloaded + built on first run (~2-3 min of tactic autotuning — not cached anywhere
      by the `cmake`/`make install` steps, budget for it each fresh TX2 setup). Steady ~30 fps
      (avg 30.2, min 29.6 across 441 samples). 19/441 samples (~4.3%) tracked one real object off
      the live camera (survived the tracker's 3-hit confirm threshold) — genuine detectNet output,
      not synthetic. Correctly reported "no status (link?)" throughout, no RT1170 on the wire yet.
      `--fpx 900` was a placeholder for this smoke test only — **real calibration
      (`tx2/calibrate.py`, tape-measure a person at 3/5/8 m) still not done**, so no real distance
      numbers exist yet.
- [x] Passwordless sudo configured on the TX2 (`/etc/sudoers.d/wingman-automation`,
      `jetson ALL=(ALL) NOPASSWD: ALL`, Suvir's explicit choice for zero-friction automation
      mid-crunch) — apt/make/install steps no longer need him at the keyboard.
- [x] **P1 RT1170 bring-up + flash loop: DONE on real hardware.** Console verified (runbook 1.1,
      `kernel version` -> 4.1.0). `flash.ps1` flashed `hello_world` (40398 B) then the real
      `rt1170/` supervisor firmware (177228 B, matches the predicted zero-warning build exactly)
      back-to-back in one Serial Downloader session — used `blhost -u 0x15a2:0x0073 reset` to
      bounce the flashloader back to boot-ROM identity between the two flashes instead of a second
      physical power cycle. Board boots the real firmware: `wm stats` and `wm state` both respond
      correctly (state `WAIT_LINK`, thresholds match §7.2 exactly, all counters correctly zeroed).
      S5 boot-mode switch identified precisely: 8-position DIP bank, all-off = QSPI Flash (normal
      boot), switch 8 alone ON = USB-OTG Serial Downloader. USB-OTG connector is **X16** (labeled
      OTG1 on silkscreen) — X17/OTG2 is a second, unused USB port. Console is confirmed **X15**
      (matches CLAUDE.md, not X17 as briefly misread from a low-res photo).
- [x] **P2 Ethernet: DONE.** Live port is actually the **1 Gbit ENET_1G** (RGMII), not the 100 Mbit
      KSZ8081 as guessed — `net iface` on the RT1170 shows `carrier=ON`, "1 Gbits full-duplex",
      confirming the physical RJ45. Static IPs set both sides (RT1170 192.168.10.2/24 was already
      in the flashed firmware's `prj.conf`; TX2 eth0 192.168.10.1/24 via `nmcli`, using the new
      passwordless sudo). Ping TX2 -> RT1170: 5/5 received, 0% loss, RTT 0.32-0.73 ms.
- Windows flashing toolchain notes: `spsdk` 3.11.0 + `platformdirs` 4.12.0 (latest at install time)
  are incompatible (`_optionally_create_directory() missing... 'private'`) — pin
  `platformdirs==4.0.0`. Also `spsdk`'s installed scripts (`blhost.exe` etc.) land outside PATH by
  default; add its Scripts dir explicitly. `flash.ps1`'s `$PSScriptRoot`-based default `-Loader`
  path doesn't resolve when invoked via `powershell -File` from a parent PowerShell session — pass
  `-Loader` explicitly, or dot-invoke the script in-session (`& .\tools\flash.ps1 ...`) instead.
- [x] P3 protocol header + Python mirror + CRC test vectors (`tests/test_proto.py`: C and Python
      produce identical bytes)
- [~] P4 supervisor firmware: all of §7.1–7.5 implemented; builds with **zero warnings** for
      `phyboard_atlas/mimxrt1176/cm7` (177 KB); decision core host-unit-tested; whole app runs on
      `native_sim` and passes 14/14 scenarios. Not yet flashed.
- [~] P5 scenario_player (6 scenarios, all fault flags, --repeat, --matrix → markdown, --csv),
      flood.py, dashboard.py (+ --snapshot PNG): verified against native_sim. Hardware run pending.
- [~] P6 perception.py (+ `--input synthetic`, replay, overlay by RT1170 verdict, CSV), tracker.py
      (tests/test_tracker.py), calibrate.py. Synthetic path verified end-to-end vs native_sim;
      jetson-inference path untested (no TX2 here). `tx2/calib.json` does not exist until calibrate.py runs.
- [~] P7 IMU threshold context (braking suppression, swerve near-miss, impact black box) in firmware;
      axis mapping unverified on the board. TinyML: not started. `tools/imu_logger.py`: not written.
- [ ] P8 measurements (runbook P8 checklist)
- [ ] P10 video
- [ ] P11 write-up + repo + **submitted** (README.md has the write-up skeleton; Results section empty on purpose)
- Open question for Suvir: did a MIPI-DSI panel come in the RT1170 kit?

---

## 17. Implementation notes (decisions made while building — read before changing things)

- **Zephyr workspace / PHYTEC manifest**: the manifest module injects demo modules (watchdog,
  heartbeat LED, button, LVGL, USB, …) into *every* app. `rt1170/prj.conf` disables all of them
  (`CONFIG_APP_*=n`); otherwise their watchdog/LEDs fight ours (and they fail to compile out of tree).
- **Watchdog = WDOG1 at 500 ms**, not 200 ms: `wdt_mcux_imx_wdog.c` rejects < 500 ms (0.5 s steps);
  the tree has no RTWDOG node for RT11xx. The 100 ms heartbeat failsafe is independent of it.
- **Reset cause**: RT1170 `hwinfo` driver has no reset-cause support → read `SRC->SRSR` via
  `fsl_soc_src.h` (`kSRC_M7CoreWdog*ResetFlag`). Bit mapping unverified on hardware: check the
  `SRC SRSR = 0x…` boot log after a `wm hang`.
- **RAM stays in SDRAM (BSP default).** Tried `zephyr,sram = &dtcm`: it links but overlaps the NXP
  drivers' own `.dtcm_bss/.dtcm_noinit` at 0x20000000 → silent corruption. Don't. If latency jitter
  needs it later, move specific buffers with `__dtcm_*` section attributes and measure before/after.
- **`CONFIG_NET_CONFIG_INIT_TIMEOUT=0`**: default 30 s would stall boot whenever the cable is unplugged.
- **Single-writer design**: net_rx only parses/CRCs and queues; *all* supervisor state changes happen
  in `decide` (heartbeat timer just queues an `EV_HB_LOST`). Shell/IMU touch `g_sup` under `g_lock`.
- **Latency definition**: socket delivery (`recvfrom` return, cycle counter) → LEDs written. Excludes
  ENET/IP-stack time before delivery; the probe pins bound it externally. Say this in the write-up.
- **Status replies go to the sender's IP**, port 5006 (default 192.168.10.1 before any frame), so the
  scenario player can run on either the TX2 or the Odroid.
- **Heartbeat loss clears sequence tracking**, so a restarted perception.py (seq back at 0) is accepted.
  Recovery needs 10 consecutive in-sequence frames; a gap restarts the count.
- **Braking suppression never downgrades the close-range (< 3 m) WARNING.**
- **Truncated boxes** (touching the image edge) get confidence × 0.8, not × 0.5: × 0.5 would push a
  close, cut-off pedestrian under the RT1170's 0.4 confidence floor and hide the most dangerous case.
- **LEDs**: carrier `led3` (red) / `led4` (green) + SoM `led1`/`led0` mirrored. CAUTION = both on.
- **Probe pins**: 60-pin expansion header pin 11 (`GPIO_AD_34`, RX: high arrival→outputs) and
  pin 33 (`GPIO_SNVS_00`, ALERT: toggles per decision). Pins 27/29 are the IMU interrupts — don't use.
- **flood.py targets port 5099 by default** (IP-stack load without feeding the supervisor garbage;
  aiming it at 5005 trips FAILSAFE after 5 bad frames — by design).
- **Round trips** are only counted for the first reply matching a frame this process sent
  (keepalives during FAILSAFE re-echo older frames and gave bogus 800 ms "RTTs").
- **native_sim**: `rt1170/boards/native_sim_native_64.conf` maps Zephyr sockets to host sockets; the
  firmware then listens on the host's :5005 (`tools/build.sh --sim`). Its latency/timing numbers are
  simulation artefacts — never report them.
- **CUDA kernels (rewritten)**: P stored as its 10-float upper triangle (exactly symmetric; 14
  floats/track = 56 B). Model constants are plain literals so the double path equals NumPy exactly.
  Measured on host (these runs, not the TX2): hand-expanded vs naive full-matrix max rel diff
  8.6e-14 (double) / 3.8e-5 (float) over 20k random steps; vs `python/reference_kalman.py` replay
  5.8e-15 over 2020 states; single track 60 frames q=0.01 → velocity 4.995/2.011 (true 5/2);
  1000 tracks × 200 frames float, monocular noise → mean vel err 0.295 m/s, all P positive definite.
  **Fair AoS/SoA comparison**: naive SoA indexing used 56 registers vs 32 for AoS (half the
  occupancy); an `asm volatile` address barrier brings SoA to 32, no spills; both kernels have
  `__launch_bounds__(256, 8)`. `--block` > 256 is rejected. The GPU correctness check was
  mutation-tested (planted SoA index bug → CORRECTNESS FAILED). No GPU timing exists yet.
- Cloud build env used for verification: Zephyr SDK 0.17.0 + `west init -m …zephyr-phytec-application
  --mr v4.1.0-phy2`, `west update --narrow -o=--depth=1`.
