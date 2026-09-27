#!/usr/bin/env python3
"""Protocol conformance: Python mirror vs the C header, byte for byte.

Packs the same frames in C (protocol/wingman_proto.h, compiled with the host gcc) and in
Python (tx2/wingman_proto.py) and compares the bytes and CRCs. Also checks the shared CRC
vector and a round trip through unpack.

  python3 tests/test_proto.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tx2"))
import wingman_proto as wp  # noqa: E402

C_SRC = r"""
#include <stdio.h>
#include <string.h>
#include "wingman_proto.h"

static void hex(const void *p, size_t n) {
    const unsigned char *b = p;
    for (size_t i = 0; i < n; i++) printf("%02x", b[i]);
    printf("\n");
}

int main(void) {
    wm_perception_t f;
    memset(&f, 0, sizeof f);
    f.magic = WM_MAGIC_PERCEPTION; f.version = WM_VERSION; f.n_tracks = 2;
    f.seq = 0xA1B2C3D4u; f.tx_time_us = 0x0102030405060708ull;
    f.tracks[0] = (wm_track_t){ .id = 7, .cls = WM_CLS_CAR, .conf = 200,
                                .dist_mm = 12345, .lat_mm = -678, .closing_mm_s = 9000 };
    f.tracks[1] = (wm_track_t){ .id = 65534, .cls = WM_CLS_PERSON, .conf = 255,
                                .dist_mm = 2147483647, .lat_mm = -2147483647 - 1, .closing_mm_s = -1 };
    f.crc16 = wm_crc16(&f, WM_PERCEPTION_CRC_LEN);
    hex(&f, sizeof f);

    wm_status_t s = { .magic = WM_MAGIC_STATUS, .version = WM_VERSION, .state = WM_WARNING,
                      .seq_echo = 42, .tx_time_us_echo = 123456789012ull, .ttc_ms = 980,
                      .threat_id = 3, .fault_flags = WM_FAULT_CRC | WM_FAULT_SEQ_GAP,
                      .decision_latency_ns = 4321, .imu_ax_mg = -312, .imu_ay_mg = 45,
                      .maneuver = WM_MAN_BRAKE };
    s.crc16 = wm_crc16(&s, WM_STATUS_CRC_LEN);
    hex(&s, sizeof s);
    printf("%04x\n", wm_crc16("123456789", 9));
    return 0;
}
"""


def c_output():
    cc = shutil.which("gcc") or shutil.which("cc")
    if not cc:
        return None
    d = tempfile.mkdtemp()
    src, exe = os.path.join(d, "p.c"), os.path.join(d, "p")
    with open(src, "w") as f:
        f.write(C_SRC)
    subprocess.check_call([cc, "-std=c11", "-Wall", "-Werror", "-I", os.path.join(ROOT, "protocol"),
                           "-o", exe, src])
    return subprocess.check_output([exe]).decode().split()


def main():
    fails = 0

    def check(cond, msg):
        nonlocal fails
        print(("ok    " if cond else "FAIL  ") + msg)
        fails += 0 if cond else 1

    check(wp.crc16(b"123456789") == 0x29B1, "CRC-16/CCITT-FALSE vector 0x29B1")
    check(wp.PERCEPTION_SIZE == 146 and wp.STATUS_SIZE == 36, "sizes 146 / 36")

    tracks = [wp.Track(7, wp.CLS_CAR, 200, 12345, -678, 9000),
              wp.Track(65534, wp.CLS_PERSON, 255, 2147483647, -2147483648, -1)]
    py_f = wp.pack_perception(0xA1B2C3D4, 0x0102030405060708, tracks)
    py_s = wp.pack_status(wp.WARNING, 42, 123456789012, ttc_ms=980, threat_id=3,
                          fault_flags=wp.FAULT_CRC | wp.FAULT_SEQ_GAP, decision_latency_ns=4321,
                          imu_ax_mg=-312, imu_ay_mg=45, maneuver=2)

    seq, tx, back = wp.unpack_perception(py_f)
    check(seq == 0xA1B2C3D4 and tx == 0x0102030405060708 and back == tracks, "perception round trip")
    st = wp.unpack_status(py_s)
    check(st is not None and st.state == wp.WARNING and st.ttc_ms == 980 and st.imu_ax_mg == -312,
          "status round trip")
    bad = bytearray(py_s)
    bad[5] ^= 1
    check(wp.unpack_status(bytes(bad)) is None, "corrupted status rejected")
    try:
        wp.unpack_perception(py_f[:-1])
        check(False, "short perception rejected")
    except ValueError:
        check(True, "short perception rejected")

    out = c_output()
    if out is None:
        print("skip  C cross-check (no gcc)")
    else:
        c_f, c_s, c_crc = out
        check(c_f == py_f.hex(), "perception bytes identical in C and Python")
        check(c_s == py_s.hex(), "status bytes identical in C and Python")
        check(c_crc == "29b1", "C CRC vector 0x29B1")

    print("ALL PROTOCOL TESTS PASSED" if not fails else "%d FAILURE(S)" % fails)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
