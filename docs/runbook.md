# Wingman bring-up runbook

Exact order for P1 → P5 → P8. **[YOU]** = physical step (cables, switches, power). Everything
else is a command to paste. Stop at the first step that doesn't match its "expect" line.

Repo checkout assumed at `~/wingman` (Ubuntu on the Odroid) and on the TX2.
Zephyr workspace at `~/wingman-rt` (CLAUDE.md §4.1).

---

## P1 — RT1170 alive, toolchain, flash loop

### 1.1 Console before anything else  *(proves the board and cable, no risk)*
- **[YOU]** micro-USB into **X15** (debug/console) → Odroid. 5 V PSU into **X9**. S7 OFF, S5 = QSPI Flash.
- `sudo minicom -D /dev/ttyUSB0 -b 115200` (Windows: PuTTY, lower of the two new COM ports, 115200 8N1)
- **[YOU]** power on.
- Type `kernel version` → **expect** `Zephyr version 4.1.0`. Green heartbeat LED blinking.

### 1.2 Toolchain + workspace (Odroid, Ubuntu 26.04)
CLAUDE.md §4.1, then:
```bash
git clone https://github.com/SiliconBeast/j-obber ~/wingman && cd ~/wingman && git checkout claude/wingman-setup-q973co
make -C rt1170/tests/host          # expect: ALL TESTS PASSED  (logic tested on the PC)
python3 tests/test_proto.py        # expect: ALL PROTOCOL TESTS PASSED
```

### 1.3 blhost + flashloader
CLAUDE.md §4.2 (spsdk venv + udev rules). Download **ivt_flashloader.bin** from PHYTEC's
*Pre-Built Binaries* page → `~/wingman/tools/ivt_flashloader.bin`.

### 1.4 First flash = hello_world  *(smallest possible thing that proves the flash loop)*
```bash
cd ~/wingman-rt && source .venv/bin/activate && source zephyr/zephyr-env.sh
west build -p always -b phyboard_atlas/mimxrt1176/cm7 zephyr/samples/hello_world -d build-hello
```
- **[YOU]** power off, **S5 → USB-OTG Serial Downloader**, micro-USB into the **USB-OTG** port, power on.
- `. ~/spsdk-venv/bin/activate && ~/wingman/tools/flash.sh ~/wingman-rt/build-hello/zephyr/zephyr.bin`
  → **expect** `FLASHED OK`.
- **[YOU]** power off, **S5 → QSPI Flash**, power on → **expect** `Hello World! phyboard_atlas/...` on the console.

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
Slow green blink = WAIT_LINK. `wm state` → `state WAIT_LINK`. `wm imu` → live accel numbers.

---

## P2 — Ethernet link

Only the **100 Mbit** port (KSZ8081 PHY, `ENET`) is enabled in the devicetree; the 1 Gbit port
has no driver in this build. The docs don't say which RJ45 is which, so:

- TX2: `sudo nmcli con add type ethernet ifname eth0 con-name wingman ipv4.method manual ipv4.addresses 192.168.10.1/24 && sudo nmcli con up wingman`
- **[YOU]** cable TX2 eth0 ↔ one RT1170 RJ45.
- TX2: `ip link show eth0` → **expect** `LOWER_UP` and `ethtool eth0 | grep Speed` → `100Mb/s`.
  No `LOWER_UP` after 5 s → **[YOU]** move the cable to the other RJ45.
- TX2: `ping -c 5 192.168.10.2` → replies. RT1170 console: `net ping 192.168.10.1`.

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
Then `wm stats` on the RT1170 → `failover ... n=50 mean ... max ...` (measured on the MCU itself).

**Latency under load**: terminal A `python3 tools/flood.py --pps 5000 --seconds 60`,
terminal B `python3 tools/scenario_player.py --scenario approach --repeat 10`, then compare
`wm stats` against an unloaded run (`wm stats reset` between runs).

**Watchdog**: `wm hang` → board resets within ~0.5 s → console shows
`last reset was caused by the WATCHDOG`; `wm state` shows `WDT_RESET`.

---

## P6 — perception (TX2)

```bash
sudo nvpmodel -m 0 && sudo jetson_clocks
python3 tx2/perception.py --input synthetic --output ''        # full TX2 path, no camera: RT1170 LEDs react
python3 tx2/calibrate.py --person-height <your height in m>   # writes tx2/calib.json
python3 tx2/perception.py                                      # live camera, HDMI display
python3 tx2/perception.py --input file://clip.mp4 --loop       # replayed footage
```

---

## P8 — measurement checklist (paste real numbers into docs/results.md; never estimate)

| Metric | Where it comes from |
|---|---|
| Decision latency min/mean/p99/max + histogram | RT1170 `wm stats` after ≥10k frames (≈6 min of `--repeat`) |
| Inter-decision jitter | `wm stats` interval line |
| Failover time | `wm stats` failover line after the ×50 run; player's own number as cross-check |
| Latency under stress | `wm stats` with flood.py running vs not |
| Round trip TX2↔RT1170 | scenario_player / perception.py summary |
| Scenario matrix | docs/test_matrix.md |
| Logic-analyzer cross-check | probes on expansion header **pin 11** (RX: high from arrival to outputs written) and **pin 33** (ALERT: toggles per decision), GND on the same header |
| Perception FPS | tx2 perception CSV (`logs/`) |
| CUDA kernel | `kernels/` sweep + nvprof |

## Demo money shot
Run `python3 tx2/perception.py --input synthetic --output ''` (or live), then **[YOU]** unplug the
Ethernet cable → RT1170 LEDs go solid red. Console: `NOMINAL -> FAILSAFE`. `wm stats` failover line.
Plug back in → 10 good frames later it's NOMINAL again.
