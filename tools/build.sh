#!/usr/bin/env bash
# Build the RT1170 firmware (or the desktop simulation of it).
#
#   tools/build.sh          -> $WINGMAN_RT/build/zephyr/zephyr.bin for phyboard_atlas/mimxrt1176/cm7
#   tools/build.sh --sim    -> $WINGMAN_RT/build-sim/zephyr/zephyr.exe, runs on Linux, listens on :5005
#
# Expects the Zephyr workspace from CLAUDE.md 4.1 at ~/wingman-rt (override: WINGMAN_RT=...).
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
ws="${WINGMAN_RT:-$HOME/wingman-rt}"
[[ -d "$ws/zephyr" ]] || { echo "no Zephyr workspace at $ws (CLAUDE.md 4.1), or set WINGMAN_RT" >&2; exit 1; }
cd "$ws"
# shellcheck disable=SC1091
source .venv/bin/activate
# shellcheck disable=SC1091
source zephyr/zephyr-env.sh

if [[ "${1:-}" == "--sim" ]]; then
	west build -p always -b native_sim/native/64 "$repo/rt1170" -d build-sim
	echo "run: $ws/build-sim/zephyr/zephyr.exe   then: python3 $repo/tools/scenario_player.py --target 127.0.0.1 --matrix"
else
	west build -p always -b phyboard_atlas/mimxrt1176/cm7 "$repo/rt1170" -d build
	ls -l build/zephyr/zephyr.bin
	echo "flash: $repo/tools/flash.sh $ws/build/zephyr/zephyr.bin <ivt_flashloader.bin>"
fi
