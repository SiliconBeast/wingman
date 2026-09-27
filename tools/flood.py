#!/usr/bin/env python3
"""Blast junk UDP at the RT1170 to show decision latency stays flat under load.

Default target port is 5099 (nothing listens there): the packets still cost the
RT1170 an Ethernet interrupt, a trip through the IP stack and a drop - load on the
same path the real frames use - without touching the supervisor's input. Run it in
a second terminal while scenario_player.py runs, then compare `wm stats` with and
without it.

  python3 tools/flood.py --pps 5000 --seconds 30
  python3 tools/flood.py --port 5005 --pps 200     # attack the perception port itself:
                                                    # >=5 bad frames in a row -> FAILSAFE (by design)
"""
import argparse
import os
import socket
import time


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="192.168.10.2")
    ap.add_argument("--port", type=int, default=5099)
    ap.add_argument("--pps", type=float, default=5000.0, help="packets per second (0 = as fast as possible)")
    ap.add_argument("--size", type=int, default=146, help="payload bytes")
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dst = (args.target, args.port)
    payload = os.urandom(args.size)
    period = 1.0 / args.pps if args.pps > 0 else 0.0
    batch = max(1, int(args.pps / 1000)) if args.pps > 0 else 64   # ~1 ms granularity

    sent = 0
    t0 = time.monotonic()
    t_end = t0 + args.seconds
    next_t = t0
    last_report = t0
    print("flooding %s:%d with %d-byte packets at %s pps for %.0f s" % (
        args.target, args.port, args.size, args.pps or "max", args.seconds))
    try:
        while True:
            now = time.monotonic()
            if now >= t_end:
                break
            if period and now < next_t:
                time.sleep(min(next_t - now, 0.001))
                continue
            for _ in range(batch):
                try:
                    sock.sendto(payload, dst)
                    sent += 1
                except OSError:
                    pass       # ENOBUFS / ICMP unreachable echoes: keep going
            next_t += period * batch
            if now - last_report >= 1.0:
                print("  %d packets, %.0f pps" % (sent, sent / (now - t0)))
                last_report = now
    except KeyboardInterrupt:
        pass
    dt = time.monotonic() - t0
    print("sent %d packets in %.1f s (%.0f pps achieved)" % (sent, dt, sent / dt if dt else 0))


if __name__ == "__main__":
    main()
