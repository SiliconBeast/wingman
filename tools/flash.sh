#!/usr/bin/env bash
# Flash a Zephyr image to the phyBOARD-RT1170 QSPI NOR over USB-OTG with blhost.
# No debug probe needed. Linux; Windows equivalent is tools/flash.ps1.
#
#   tools/flash.sh build/zephyr/zephyr.bin [path/to/ivt_flashloader.bin]
#
# Before running (you do these):
#   1. Power off. S5 -> USB-OTG Serial Downloader. S7 OFF.
#   2. Micro-USB from the board's USB-OTG port to this PC.
#   3. Power on (X9, supplied 5 V PSU).
# ivt_flashloader.bin comes from PHYTEC's "Pre-Built Binaries" page (not from the
# build). Default location: next to this script, or set IVT_FLASHLOADER=...
# blhost comes from:  python3 -m venv ~/spsdk-venv && . ~/spsdk-venv/bin/activate && pip install spsdk
set -euo pipefail

ROM="0x1fc9:0x013d"      # i.MX RT1170 boot ROM (serial downloader)
LDR="0x15a2:0x0073"      # flashloader, after it has been loaded
FLASH_BASE=0x30000000
MIN_ERASE=$((0x80000))   # 512 KiB

here="$(cd "$(dirname "$0")" && pwd)"
img="${1:-}"
loader="${2:-${IVT_FLASHLOADER:-$here/ivt_flashloader.bin}}"

die() { echo; echo "FLASH FAILED: $*" >&2; exit 1; }
hints() {
	cat >&2 <<'EOF'

Checks, in order:
  - S5 really in USB-OTG Serial Downloader position (compare with PHYTEC's photo), S7 OFF
  - cable in the USB-OTG micro-USB port (not X15 debug/console), try another cable/port
  - power-cycle the board after moving S5
  - `lsusb | grep -i -e 1fc9 -e 15a2` should list the board
  - udev rules installed (CLAUDE.md 4.2), or run once with sudo to rule permissions out
  - `dmesg | tail` right after plugging in
EOF
}
run() {
	echo "+ blhost $*"
	local out
	if ! out="$(blhost "$@" 2>&1)"; then
		echo "$out"
		hints
		die "blhost $*"
	fi
	echo "$out" | sed 's/^/    /'
}

[[ -n "$img" ]] || die "usage: $0 <zephyr.bin> [ivt_flashloader.bin]"
[[ -f "$img" ]] || die "image not found: $img"
[[ -f "$loader" ]] || die "flashloader not found: $loader  (download ivt_flashloader.bin from PHYTEC's Pre-Built Binaries page, or pass its path)"
command -v blhost >/dev/null || die "blhost not in PATH (activate the spsdk venv: . ~/spsdk-venv/bin/activate)"

size=$(stat -c %s "$img")
erase=$MIN_ERASE
if (( size > erase )); then
	erase=$(( (size + 0xFFFF) / 0x10000 * 0x10000 ))   # round up to 64 KiB
	echo "image is $size bytes: erase region bumped to $(printf 0x%x $erase)"
fi
(( size <= erase )) || die "image ($size B) larger than erase region ($erase B)"
printf 'image %s: %d bytes, erasing %s bytes at %s\n' "$img" "$size" "$(printf 0x%x $erase)" "$FLASH_BASE"

echo "==> 1/4 talk to the boot ROM"
run -u "$ROM" get-property 1

echo "==> 2/4 load the flashloader into RAM"
run -u "$ROM" load-image "$loader"

echo "    waiting for the flashloader to enumerate..."
ok=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
	sleep 0.5
	if blhost -u "$LDR" get-property 1 >/dev/null 2>&1; then ok=1; break; fi
done
(( ok )) || { hints; die "flashloader ($LDR) did not appear after load-image"; }
run -u "$LDR" get-property 1

echo "==> 3/4 configure QSPI NOR (FlexSPI)"
run -u "$LDR" fill-memory 0x00002000 4 0xCF900001
run -u "$LDR" configure-memory 9 0x00002000
run -u "$LDR" fill-memory 0x00002000 4 0xC0000007
run -u "$LDR" configure-memory 9 0x00002000

echo "==> 4/4 erase + write"
run -u "$LDR" flash-erase-region "$FLASH_BASE" "$(printf 0x%x $erase)"
run -u "$LDR" write-memory "$FLASH_BASE" "$img"

cat <<'EOF'

FLASHED OK. Now (you):
  1. Power off.
  2. S5 -> QSPI Flash (normal boot).
  3. Power on and watch the console (X15, lower COM port / /dev/ttyUSB0, 115200 8N1).
EOF
