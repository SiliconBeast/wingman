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
| `tools/` | `flash.sh`/`flash.ps1` (blhost over USB-OTG), `build.sh`, `scenario_player.py` (scripted scenarios + fault injection), `flood.py`, `dashboard.py`, `imu_monitor.py` (live IMU readback over UDP) |
| `kernels/`, `python/` | CUDA Kalman filter bank (AoS vs SoA, one thread per track) + CPU/NumPy references (`make` in kernels/) |
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

All measured on real hardware (phyBOARD-RT1170 + Jetson TX2), not simulated. Raw data in `docs/`.

- **Decision latency** (socket delivery → outputs written), n=1614 frames: min 11.92 / mean 49.79 /
  p99 118.62 / max 205.10 **microseconds** — three orders of magnitude inside the 100 ms heartbeat
  budget. (`docs/scenario_results.csv`)
- **Failover time** (last valid frame → FAILSAFE outputs), n=50 (**50/50 passed**): min 102.32 /
  mean 107.26 / p99 112.36 / max 112.36 **ms** — lands exactly on the design prediction of
  "~100 ms + one 10 ms heartbeat tick." (`docs/failover.csv`)
- **Latency under network flood** (`flood.py`, 5000 pps): RT1170's own decision latency stayed
  essentially flat — 51.19 µs mean vs 47.46 µs unloaded baseline — confirming the design goal that
  IP-stack load on the supervisor doesn't touch its decision-making speed. Separately, we found a
  real **host-side** packet-delivery instability during the flood's active window (21/30 scenario
  repeats passed vs 20/20 unloaded, several "no status received" gaps concentrated exactly while
  flood.py was running, full recovery immediately after) — most likely two Python processes
  contending for the TX2's CPU/sockets, not an RT1170 firmware issue. Reported as found, not
  smoothed over. (`docs/baseline_results.csv`, `docs/flood_stress_results.csv`)
- **Round trip** (TX2 send → RT1170 → TX2 receive), n=1614: min 0.50 / mean 0.87 / p99 1.37 /
  max 2.02 ms.
- **Scenario test matrix**: **14/14 passed** on real hardware — all 6 scripted scenarios plus every
  fault-injection variant (packet drop, CRC corruption, reorder, duplicate, rate change, short and
  long burst-loss, simulated perception crash). Matches the 14/14 result from `native_sim`.
  (`docs/test_matrix.md`)
- **Perception FPS**: steady ~30.2 fps average (441 samples, 29.6-30.3 range) over a 10-minute
  continuous live-camera soak test, zero crashes.
- **CUDA Kalman kernel** (NVIDIA Tegra X2, sm_62, 2 SMs): CPU beats GPU at N=64 (ratio 0.91x — GPU
  loses at small N, as expected from launch overhead); crossover at **N=256** (3.33x); scales to
  **59-61x** at N≥16384. Real ADAS track counts (10-50) sit at/below the crossover — said plainly,
  not oversold. SoA beats AoS via memory coalescing, confirmed with `nvprof`: **100% global
  load/store efficiency vs 14.04%/12.50%** for AoS, at similar occupancy (~0.85 both) — the
  speedup is specifically about memory access pattern, not thread scheduling.
  (`docs/kernel_sweep.csv`)

## Limitations
- Monocular distance assumes a fixed height per class; truncated or unusual objects are wrong.
- Prototype on a bench / replayed footage. Not tested in traffic, not ISO 26262, not a product.
- The decision latency is measured from socket delivery, so the Ethernet driver and IP stack
  time before it is not included (the logic-analyzer probes bound it from the outside, but no
  logic analyzer was actually available for this build — that cross-check wasn't done).
- **Calibration used an improvised method**: the test room can't fit a full standing person in
  frame at any usable distance (verified by camera — feet get cut off), so `calibrate.py`'s
  standard 3-distance person method doesn't work here. Used a known-size object (letter paper) at
  2 hand-held distances instead, with 13.3% spread between them (above the tool's own 10% warning
  threshold) — likely hand-held tilt imprecision. Distance/TTC numbers are approximate, not
  precision-verified.
- **Tracking gets unstable at very close range**: within roughly arm's length of the camera, the
  detector intermittently loses lock (confirmed via track-count logging), causing the state
  machine to occasionally revert to NOMINAL abruptly rather than through the state machine's
  hysteresis — the detection was lost, not the threat resolved. A real limitation of monocular
  detection at extreme proximity, not a state-machine bug.
- **IMU works, but with one unresolved question**: the onboard ICM-40627 originally failed to
  initialize entirely (traced to a known upstream bug — this BSP branch ships it at the wrong I2C
  address, fixed via a devicetree overlay). Axis assignment verified correct by physically tilting
  the board. Sign convention (which direction reads as "braking") depends on which physical edge
  faces vehicle-front in the final mounting — not fixed here, deliberately, since it's a mounting
  decision rather than a firmware bug.

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
