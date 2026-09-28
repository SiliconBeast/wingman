# Wingman bring-up runbook

Exact order for P1 → P5 → P8. **[YOU]** = physical step (cables, switches, power). Everything
else is a command to paste. Stop at the first step that doesn't match its "expect" line.

Repo checkout assumed at `~/wingman` (Ubuntu on the Odroid) and on the TX2.
Zephyr workspace at `~/wingman-rt` (CLAUDE.md §4.1).

---

## P1 — RT1170 alive, toolchain, flash loop

### 1.1 Console before anything else  *(proves the board and cable, no risk)*
- **[YOU]** micro-USB into **X15** (debug/console, top-left, silkscreened "CONSOLE") → Odroid.
  5 V PSU into **X9**. S7 OFF, S5 = QSPI Flash (see 1.4 for exact switch positions).
- `sudo minicom -D /dev/ttyUSB0 -b 115200` (Windows: PuTTY, lower of the two new COM ports, 115200 8N1)
- **[YOU]** power on.
- Type `kernel version` → **expect** `Zephyr version 4.1.0`. Green heartbeat LED blinking.
- If nothing enumerates at all on Windows (no new COM port), double check it's genuinely X15 and
  not one of the two OTG ports at the bottom of the board (X16/X17) — easy to mix up, all three
  are visually similar micro-USB jacks.

### 1.2 Toolchain + workspace (Odroid, Ubuntu 26.04)
CLAUDE.md §4.1, then:
```bash
git clone https://github.com/SiliconBeast/wingman ~/wingman && cd ~/wingman && git checkout claude/wingman-setup-q973co
make -C rt1170/tests/host          # expect: ALL TESTS PASSED  (logic tested on the PC)
python3 tests/test_proto.py        # expect: ALL PROTOCOL TESTS PASSED
```

### 1.3 blhost + flashloader
CLAUDE.md §4.2 (spsdk venv + udev rules). Download **ivt_flashloader.bin** from PHYTEC's
*Pre-Built Binaries* page (`https://download.phytec.de/Software/Zephyr/BSP-Zephyr-RT1170/BSP-Zephyr-RT1170-PD25.1.1/ivt_flashloader.bin`
for the PD25.1.1 BSP) → `~/wingman/tools/ivt_flashloader.bin`.

**Windows native (no debug probe, `blhost` talks HID directly):**
```powershell
pip install spsdk
pip install platformdirs==4.0.0   # spsdk 3.11.0 breaks against platformdirs 4.12.0+
                                   # ("_optionally_create_directory() missing... 'private'")
```
`blhost.exe` lands in spsdk's package `Scripts` dir, not on PATH by default — add it to `$env:Path`
for the session, or add it permanently.

### 1.4 First flash = hello_world  *(smallest possible thing that proves the flash loop)*
```bash
cd ~/wingman-rt && source .venv/bin/activate && source zephyr/zephyr-env.sh
west build -p always -b phyboard_atlas/mimxrt1176/cm7 zephyr/samples/hello_world -d build-hello
```
**S5 exact switch positions** (8-position DIP bank, bottom-right, silkscreened "Bootmode"):
all 8 OFF (down) = QSPI Flash (normal boot); **switch 8 alone ON** (up), 1-7 still OFF =
USB-OTG Serial Downloader. Only switch 8 changes between the two modes.

- **[YOU]** power off, flip switch 8 up (Serial Downloader), micro-USB into **X16** (labeled
  **OTG1** on the silkscreen — the *left* of the two bottom ports; X17/OTG2 is a second, unused
  port), power on.
- Check the boot ROM enumerated before flashing: `blhost -u 0x1fc9:0x013d get-property 1` →
  **expect** `Success`. On Windows this needs `spsdk` installed in native Python (`pip install
  spsdk`) with its `Scripts` dir on PATH — see the 1.3 note above if `blhost` isn't found.
- `. ~/spsdk-venv/bin/activate && ~/wingman/tools/flash.sh ~/wingman-rt/build-hello/zephyr/zephyr.bin`
  (Windows: `tools\flash.ps1`, and pass `-Loader` explicitly if invoking via `powershell -File`
  from a parent shell — `$PSScriptRoot` doesn't resolve through that nesting)
  → **expect** `FLASHED OK`.
- Flashing a second image right after, in the same Serial Downloader session (no switch flip
  needed between the two): the flashloader has replaced the boot ROM's USB identity by then, so
  `blhost -u 0x1fc9:0x013d get-property 1` will fail. Run `blhost -u 0x15a2:0x0073 reset` first to
  bounce it back to the boot ROM before flashing again.
- **[YOU]** power off, flip switch 8 back down (QSPI Flash), power on → **expect**
  `Hello World! phyboard_atlas/...` on the console.

### 1.5 Wingman firmware
```bash
~/wingman/tools/build.sh           # -> ~/wingman-rt/build/zephyr/zephyr.bin (~177 KB)
```
Flash exactly as 1.4 (S5 → OTG, `flash.sh ~/wingman-rt/build/zephyr/zephyr.bin`, S5 → QSPI).
**Expect** on the console:
```
<inf> wingman: Wingman safety supervisor, 996000000 Hz cycle counter
<inf> wingman: SRC SRSR = 0x...
<inf> wingman: watchdog armed, 500 ms
<inf> wingman: listening on udp :5005
<inf> wm_imu: IMU zeroed: bias ... mg
```
Slow green blink = WAIT_LINK. `wm state` → `state WAIT_LINK`.

There's no `wm imu` shell command (an earlier version of this runbook assumed one) — the IMU's
`ax_mg`/`ay_mg` only go out in the UDP status packet, not to the shell. To watch it live, use
`tools/imu_monitor.py` from the TX2 (or Odroid, on the 192.168.10.x link):
```bash
python3 tools/imu_monitor.py 25     # 25-second window, prints ax/ay/maneuver from every reply
```
**If it reads exactly 0/0 no matter how you move the board**: check the boot log for
`<err> ICM40627: Could not initialize sensor`. If present, this BSP branch (`v4.1.0-phy2`) ships
the ICM40627 at the wrong I2C address (`0x6b`); PHYTEC fixed it to `0x69` upstream in a later
branch. Fixed here via `rt1170/boards/phyboard_atlas_mimxrt1176_cm7.overlay`
(`&icm40627 { reg = <0x69>; };`) rather than upgrading the whole BSP. If you're on a clean
`v4.1.0-phy2` checkout without that overlay line, add it.

---

## P2 — Ethernet link

**Corrected from an earlier guess**: it's actually the **1 Gbit** port (`ENET_1G`, RGMII) that's
live in this build, not the 100 Mbit KSZ8081 one — confirmed via the RT1170 shell's `net iface`,
which shows `carrier=ON` and "1 Gbits full-duplex" on `eth0`. Static IP (192.168.10.2/24) is
already baked into the flashed firmware's `prj.conf`, nothing to configure on that side.

- TX2: `sudo nmcli con add type ethernet ifname eth0 con-name wingman ipv4.method manual ipv4.addresses 192.168.10.1/24 && sudo nmcli con up wingman`
- **[YOU]** cable TX2 eth0 ↔ RT1170 RJ45.
- TX2: `ip link show eth0` → **expect** `LOWER_UP`.
- RT1170 shell: `net iface` → **expect** `Status : oper=UP, admin=UP, carrier=ON` and
  `192.168.10.2/255.255.255.0` under IPv4 unicast addresses.
- TX2: `ping -c 5 192.168.10.2` → replies (measured: 0% loss, 0.3-0.7 ms RTT).

---

## P5 — prove the supervisor (no camera needed)

On the TX2 (or the Odroid if it is on the 192.168.10.x link):
```bash
cd ~/wingman
python3 tools/scenario_player.py --scenario approach -v           # expect PASS, NOMINAL→CAUTION→WARNING
python3 tools/scenario_player.py --matrix --md docs/test_matrix.md # expect 14/14 passed
```
RT1170 console `wm stats` → latency histogram, interval jitter, counters.

**Failover ×50** (the headline number):
```bash
python3 tools/scenario_player.py --scenario follow --stop-at 2s --repeat 50 --csv docs/failover.csv
```
Measured: 50/50 passed, failover min 102.32 / mean 107.26 / p99 112.36 / max 112.36 ms — right on
the "~100 ms + one 10 ms tick" design prediction. `wm stats` on the RT1170 confirms the same
number from the MCU's own side.

**Latency under load**: terminal A `python3 tools/flood.py --pps 5000 --seconds 60`,
terminal B `python3 tools/scenario_player.py --scenario approach --repeat 10`, then compare
`wm stats` against an unloaded run (`wm stats reset` between runs).

**Watchdog**: `wm hang` → prints `stalling decide thread...` → board resets within ~0.5 s →
`wm state` on the fresh boot shows `faults 0x20 WDT_RESET` (and `last seq 0`, confirming it's a
real reboot, not a stale read). Confirmed working via a forced reboot + fresh boot-log capture.
One open question, not fully resolved: the very first post-flash boot (a normal power-on, not a
deliberate `wm hang`) also reported `WDT_RESET` — could be a real earlier watchdog trip, or the
SRC reset-cause bit mapping misclassifying ordinary power-on resets too. Would need a clean
power-cycle-only boot to compare and rule out; not done here.

---

## P6 — perception (TX2)

```bash
sudo nvpmodel -m 0 && sudo jetson_clocks
python3 tx2/perception.py --input synthetic --output ''        # full TX2 path, no camera: RT1170 LEDs react
python3 tx2/calibrate.py --person-height <your height in m>   # writes tx2/calib.json
python3 tx2/perception.py --output ''                           # live camera, no display attached
python3 tx2/perception.py --input file://clip.mp4 --loop       # replayed footage
```
No HDMI display in this setup — always pass `--output ''`, otherwise it tries to open one and fails.

**If `calibrate.py`'s person-based method won't get usable frames** (`only N usable frames... need
one person, fully in frame - skipped`, repeatedly): check with a plain snapshot first
(`gst-launch-1.0 nvarguscamerasrc num-buffers=60 ... ! nvjpegenc ! filesink ...`, wait ~2s for
auto-exposure before trusting a single-frame grab) whether a full standing person's feet are
actually getting cut off at the bottom of frame at any distance the room allows — this is a real
room-size/camera-angle constraint, not a bug. Fallback used here: a letter-size sheet of paper
(0.2794 m, portrait) held at 2 known distances, pixel-measured by hand from the photos (brightness
thresholding) rather than via `calibrate.py`'s person detection. Wrote `tx2/calib.json` directly
in the same schema (`perception.py` only reads `f_px` and `cx` from it, nothing else is load-bearing).
Got 13.3% spread between the two distances — above `calibrate.py`'s own 10% warning threshold,
most likely from hand-held tilt imprecision. Treat resulting distances as approximate; redo with
the real method in a bigger room if precision matters more than the deadline.

---

## P8 — measurement checklist (paste real numbers into README.md Results; never estimate)

| Metric | Where it comes from | Status |
|---|---|---|
| Decision latency min/mean/p99/max | scenario_player summary after the full matrix (n=1614) | **done** — see README |
| Inter-decision jitter | not separately measured | not done |
| Failover time | `--stop-at 2s --repeat 50` | **done** — see README, `docs/failover.csv` |
| Latency under stress | `flood.py` vs baseline, compared | **done** — see README, honest result (host-side gaps found) |
| Round trip TX2↔RT1170 | scenario_player summary | **done** — see README |
| Scenario matrix | `docs/test_matrix.md` | **done** — 14/14 |
| Logic-analyzer cross-check | probes on expansion header **pin 11** (RX) / **pin 33** (ALERT) | **not done — no logic analyzer available**; latency numbers above are the RT1170's own self-reported figures only, not independently cross-checked |
| Perception FPS | tx2 perception CSV, 10-min soak | **done** — ~30.2 fps avg |
| CUDA kernel | `kernels/` sweep + `nvprof` occupancy/efficiency | **done** — see README, `docs/kernel_sweep.csv` |

## Demo money shot
Run `python3 tx2/perception.py --input synthetic --output ''` (or live), then **[YOU]** unplug the
Ethernet cable → RT1170 LEDs go solid red. Console: `NOMINAL -> FAILSAFE`. `wm stats` failover line.
Plug back in → 10 good frames later it's NOMINAL again.
