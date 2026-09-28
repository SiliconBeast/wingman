#!/usr/bin/env python3
"""Live IMU readback: sends empty perception frames to keep the RT1170 out of FAILSAFE,
prints imu_ax_mg/imu_ay_mg/maneuver from every status reply. Used to verify the onboard
IMU is actually feeding the firmware real accelerometer data (it wasn't - see CLAUDE.md P7).

  python3 tools/imu_monitor.py [seconds]
"""
import os
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tx2"))
import wingman_proto as wp  # noqa: E402

TARGET = ("192.168.10.2", 5005)
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("192.168.10.1", 5006))
sock.settimeout(0.5)

seq = 0
t0 = time.monotonic()
t_end = t0 + DURATION
while time.monotonic() < t_end:
    seq += 1
    frame = wp.pack_perception(seq, int(time.monotonic() * 1e6), [])
    sock.sendto(frame, TARGET)
    try:
        data, _ = sock.recvfrom(200)
        st = wp.unpack_status(data)
        print("t=%.1f  ax=%5d mg  ay=%5d mg  maneuver=%d  state=%s" % (
            time.monotonic() - t0, st.imu_ax_mg, st.imu_ay_mg, st.maneuver,
            wp.state_name(st.state)))
    except socket.timeout:
        print("  (no reply)")
    time.sleep(0.03)  # well under the 100ms heartbeat timeout
