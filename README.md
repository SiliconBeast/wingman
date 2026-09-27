# Wingman

**A Jetson GPU perceives hazards; an i.MX RT1170 makes the safety decision — deterministically —
and fails safe the moment perception goes silent.**

An ADAS *prototype* (proof of concept, not a certified system) for the All About Circuits /
PHYTEC / NXP 2026 Embedded Design Contest, built around the phyBOARD-RT1170.

```
 [OV5693 CSI cam] → Jetson TX2: detectNet (TensorRT) → pinhole distance → Kalman tracker
                        → UDP perception frame @30 Hz ──Ethernet──► phyBOARD-RT1170 (Zephyr, Cortex-M7)
                                                                    validate (len/CRC/seq) → TTC → state machine
                                                                    → LEDs + probe GPIOs, 10 ms heartbeat → FAILSAFE
                                                                    hardware watchdog, onboard IMU context
                   ◄──────────────── UDP status (verdict, TTC, latency) ─┘
 The TX2 draws boxes in the colour of the RT1170's verdict.
```

| | Perception (TX2) — the eyes | Supervisor (RT1170) — the reflexes |
|---|---|---|
| Runs | Linux + CUDA/TensorRT | Zephyr RTOS on a 996 MHz Cortex-M7 |
| Good at | heavy, fast, parallel vision | bounded-time decisions, instant boot |
| Failure mode | can stall, crash, drop frames | watched by a hardware watchdog |
| Owns | *what* is out there | *whether it's dangerous*, and what to do if the eyes go dark |

## Repository

| Path | What |
|---|---|
| `protocol/wingman_proto.h` | the wire format (packed structs, CRC-16/CCITT-FALSE) — single source of truth |
| `rt1170/` | Zephyr app for `phyboard_atlas/mimxrt1176/cm7`: supervisor threads, state machine, watchdog, IMU, `wm` shell |
| `rt1170/src/wm_logic.c` | the decision core, pure C, host-unit-tested (`make -C rt1170/tests/host`) |
| `tx2/` | `perception.py` (camera → tracks → RT1170), `tracker.py` (CV Kalman), `calibrate.py`, `wingman_proto.py` |
| `tools/` | `flash.sh`/`flash.ps1` (blhost over USB-OTG), `build.sh`, `scenario_player.py` (scripted scenarios + fault injection), `flood.py`, `dashboard.py` |
| `tests/` | protocol C↔Python byte-for-byte check, tracker test |
| `docs/runbook.md` | step-by-step bring-up and measurement procedure |

## How it works

**Protocol.** 146-byte perception frames (up to 8 tracks: id, class, confidence, distance, lateral
offset, closing speed) and 36-byte status replies, little-endian, CRC-16/CCITT-FALSE, sequence
numbers. Frames are sent every camera frame even with no tracks — they *are* the heartbeat.

**Supervisor threads (RT1170).** `net_rx` stamps each datagram with the cycle counter the moment
the socket delivers it, validates length/CRC/magic/version, and queues it; `decide` (highest
priority) runs the state machine, writes the LEDs, measures latency and feeds the watchdog;
a 10 ms timer detects heartbeat loss; `status_tx` replies to whoever sent the frame.

**Decision.** In-path tracks (|lateral| < 1.5 m, confidence ≥ 0.4, closing > 0.1 m/s);
TTC = distance / closing speed; the minimum-TTC track is the threat. Hysteresis:
CAUTION below 2.5 s (exit above 2.8 s), WARNING below 1.2 s (exit above 1.5 s), plus a
close-range override (< 3 m and closing). **Any state → FAILSAFE** after 100 ms without a valid
frame or 5 consecutive corrupt frames; back to normal only after 10 consecutive in-sequence frames.

**Watchdog.** WDOG1, 500 ms (the i.MX WDOG's minimum), fed only by the decide thread; the reset
cause is read from the SRC at boot and reported as a fault. `wm hang` demonstrates it.

**IMU context.** The onboard ICM-40627: sustained braking (< −0.3 g for 200 ms) downgrades
WARNING to CAUTION (the driver is already reacting — never for the close-range override); a swerve
during a threat is logged as a near miss; a > 2 g spike freezes a 64-decision black box.

**Perception.** SSD-Mobilenet-v2 via TensorRT; distance = f_px · H_class / box height with f_px
measured by `calibrate.py`; constant-velocity Kalman tracker with gated nearest-neighbour association.

## Results

*To be filled from measurements on the hardware (docs/runbook.md, P8). No number appears here
until it has been measured.*

- Decision latency (arrival → outputs): —
- Failover time (last valid frame → FAILSAFE): —
- Latency under network flood: —
- Round trip TX2 ↔ RT1170: —
- Scenario test matrix: —
- Perception FPS: —

## Limitations
- Monocular distance assumes a fixed height per class; truncated or unusual objects are wrong.
- Prototype on a bench / replayed footage. Not tested in traffic, not ISO 26262, not a product.
- The decision latency is measured from socket delivery, so the Ethernet driver and IP stack
  time before it is not included (the logic-analyzer probes bound it from the outside).

## Build it

See `docs/runbook.md`. Short version (Ubuntu, workspace per CLAUDE.md §4.1):
```bash
tools/build.sh                                 # firmware → ~/wingman-rt/build/zephyr/zephyr.bin
tools/flash.sh ~/wingman-rt/build/zephyr/zephyr.bin tools/ivt_flashloader.bin
python3 tools/scenario_player.py --matrix      # prove it without a camera
```
No board? `tools/build.sh --sim` builds the same firmware as a Linux program listening on :5005;
point the scenario player at `127.0.0.1`.

## Bill of materials
Hardware: PHYTEC phyBOARD-RT1170 kit (+5 V USB-C PSU), NVIDIA Jetson TX2 developer kit (+19 V PSU),
Cat5e/6 cable, 2× micro-USB, optional USB logic analyzer.
Software: Zephyr 4.1 (PHYTEC BSP v4.1.0-phy2), Zephyr SDK 0.17.0, NXP SPSDK/blhost, JetPack 4.6.6
(L4T R32.7.6, CUDA 10.2, TensorRT 8.2), jetson-inference, Python 3.
