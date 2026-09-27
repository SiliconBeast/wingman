#!/usr/bin/env python3
"""Wingman perception node (Jetson TX2): camera -> detectNet -> distance -> tracker -> RT1170.

  python3 tx2/perception.py                                  # live CSI camera, display on HDMI
  python3 tx2/perception.py --input file://clip.mp4 --loop   # replay a dashcam clip (repeatable demo)
  python3 tx2/perception.py --output file://run.mp4          # record the overlay (e.g. over SSH)
  python3 tx2/perception.py --input synthetic                # no camera/DNN: scripted pedestrian

Every frame sends one perception packet (even with 0 tracks: the RT1170 uses them as its
heartbeat) and reads the RT1170's status replies. Boxes are coloured by the RT1170's verdict.
Everything is logged to logs/perception_<time>.csv.

Distance is monocular pinhole: dist = f_px * H_real / box_height_px, with a fixed
assumed real height per class. f_px comes from tx2/calib.json (run tx2/calibrate.py).
Python 3.6 compatible.
"""
import argparse
import csv
import json
import math
import os
import random
import socket
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wingman_proto as wp  # noqa: E402
from tracker import Detection, Tracker  # noqa: E402

# Assumed real heights (m). A stated limitation: kids, SUVs, cyclists vary.
CLASS_HEIGHT_M = {"person": 1.70, "car": 1.50, "bus": 3.0, "truck": 3.0,
                  "bicycle": 1.1, "motorcycle": 1.1}
CLASS_ID = {"person": wp.CLS_PERSON, "car": wp.CLS_CAR, "bus": wp.CLS_TRUCK,
            "truck": wp.CLS_TRUCK, "bicycle": wp.CLS_BIKE, "motorcycle": wp.CLS_BIKE}
TRUNCATED_CONF_SCALE = 0.8   # box touches the image edge -> height (and distance) unreliable
EDGE_PX = 2

STATE_RGB = {wp.BOOT: (128, 128, 128), wp.WAIT_LINK: (108, 142, 191), wp.NOMINAL: (46, 157, 74),
             wp.CAUTION: (224, 161, 0), wp.WARNING: (214, 39, 40), wp.FAILSAFE: (122, 0, 16)}


# ---------------------------------------------------------------- geometry

def box_to_detection(label, conf, box, f_px, cx, img_w, img_h):
    """box = (left, top, right, bottom) px. Returns Detection or None."""
    if label not in CLASS_HEIGHT_M:
        return None
    l, t, r, b = box
    h = b - t
    if h < 4 or f_px is None:
        return None
    dist = f_px * CLASS_HEIGHT_M[label] / h
    lat = ((l + r) / 2.0 - cx) * dist / f_px
    if l <= EDGE_PX or t <= EDGE_PX or r >= img_w - EDGE_PX or b >= img_h - EDGE_PX:
        conf *= TRUNCATED_CONF_SCALE
    return Detection(CLASS_ID[label], conf, lat, dist, box)


def load_calib(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (IOError, ValueError):
        return {}


# ---------------------------------------------------------------- sources

class SyntheticSource(object):
    """Scripted scene, no camera or DNN: a pedestrian walks from 14 m toward the camera
    at 1.4 m/s (slightly off-centre), a parked car sits in the next lane. Loops."""

    def __init__(self, f_px, width=1280, height=720, fps=30.0):
        self.f_px, self.w, self.h, self.fps = f_px, width, height, fps
        self.t0 = time.monotonic()
        self.n = 0
        self.rng = random.Random(0)

    def _box(self, label, dist, lat):
        hpx = self.f_px * CLASS_HEIGHT_M[label] / dist
        cxp = self.w / 2.0 + lat * self.f_px / dist
        wpx = hpx * (0.4 if label == "person" else 1.6)
        cy = self.h * 0.55
        jitter = self.rng.gauss(0, 1.0)
        l, r = cxp - wpx / 2, cxp + wpx / 2
        t, b = cy - hpx / 2 + jitter, cy + hpx / 2 + jitter
        return (max(0.0, l), max(0.0, t), min(self.w - 1.0, r), min(self.h - 1.0, b))

    def capture(self):
        target = self.t0 + self.n / self.fps
        d = target - time.monotonic()
        if d > 0:
            time.sleep(d)
        t = (self.n / self.fps) % 12.0
        self.n += 1
        dets = [("car", 0.9, self._box("car", 15.0, 3.2))]
        dist = 14.0 - 1.4 * t
        if t > 1.0 and dist > 1.2:
            dets.append(("person", 0.85, self._box("person", dist, 0.3)))
        return None, self.w, self.h, dets

    def render(self, img, overlays, text):
        pass


class JetsonSource(object):
    def __init__(self, args):
        try:
            from jetson_inference import detectNet
            import jetson_utils as ju
        except ImportError:
            from jetson.inference import detectNet
            import jetson.utils as ju
        self.ju = ju
        argv = ["--input-width=%d" % args.width, "--input-height=%d" % args.height]
        if args.loop:
            argv.append("--loop=-1")
        if args.flip:
            argv.append("--input-flip=%s" % args.flip)
        self.net = detectNet(args.network, [], args.threshold)
        self.src = ju.videoSource(args.input, argv=argv)
        self.out = ju.videoOutput(args.output, argv=[]) if args.output else None
        self.font = ju.cudaFont(size=max(16, args.height // 28))
        self.draw_rect = getattr(ju, "cudaDrawRect", None)

    def capture(self):
        img = self.src.Capture()
        if img is None:              # timeout, or end of a non-looping file
            if not self.src.IsStreaming():
                raise EOFError
            return None
        dets = []
        for d in self.net.Detect(img, overlay="none"):
            label = self.net.GetClassDesc(d.ClassID)
            dets.append((label, d.Confidence, (d.Left, d.Top, d.Right, d.Bottom)))
        return img, img.width, img.height, dets

    def render(self, img, overlays, text):
        if self.out is None:
            return
        for box, rgb, width in overlays:
            if self.draw_rect is None:
                break
            try:
                self.draw_rect(img, box, (rgb[0], rgb[1], rgb[2], 30),
                               line_color=(rgb[0], rgb[1], rgb[2], 255), line_width=width)
            except TypeError:        # older jetson-utils: fill only
                self.draw_rect(img, box, (rgb[0], rgb[1], rgb[2], 80))
        y = 8
        for line, rgb in text:
            self.font.OverlayText(img, img.width, img.height, line, 8, y,
                                  (rgb[0], rgb[1], rgb[2], 255), self.font.Gray40)
            y += int(self.font.GetSize() * 1.4) if hasattr(self.font, "GetSize") else 34
        self.out.Render(img)
        self.out.SetStatus("Wingman | %s" % text[0][0])
        if not self.out.IsStreaming():
            raise EOFError


# ---------------------------------------------------------------- status receiver

class StatusReceiver(threading.Thread):
    """Blocking receive on :5006 in its own thread, so each status is timestamped when it
    arrives (not when the frame loop gets round to it - that would add up to a frame period
    to every round-trip measurement)."""

    def __init__(self, sock, mirror):
        threading.Thread.__init__(self)
        self.daemon = True
        self.sock, self.mirror = sock, mirror
        self.lock = threading.Lock()
        self.latest = None
        self.rtts = []           # round trips (ms) since the last drain
        self._sent = {}          # seq -> tx_time_us of frames sent, awaiting their first reply

    def run(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                return
            t_us = time.monotonic() * 1e6
            if self.mirror:
                try:
                    self.sock.sendto(data, self.mirror)
                except OSError:
                    pass
            st = wp.unpack_status(data)
            if st is None:
                continue
            with self.lock:
                self.latest = st
                # first reply to a frame we sent; keepalives re-echo an older frame
                if self._sent.get(st.seq_echo) == st.tx_time_us_echo:
                    del self._sent[st.seq_echo]
                    self.rtts.append((t_us - st.tx_time_us_echo) / 1000.0)

    def sent(self, seq, tx_us):
        with self.lock:
            self._sent[seq] = tx_us
            if len(self._sent) > 256:        # replies for frames this old won't come
                for k in sorted(self._sent, key=self._sent.get)[:128]:
                    del self._sent[k]

    def drain(self):
        with self.lock:
            rtts, self.rtts = self.rtts, []
            return self.latest, rtts


# ---------------------------------------------------------------- main loop

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="csi://0", help="csi://0, file://clip.mp4, v4l2:///dev/video1, synthetic")
    ap.add_argument("--output", default="display://0", help="display://0, file://out.mp4, or '' for none")
    ap.add_argument("--loop", action="store_true", help="loop a file input")
    ap.add_argument("--flip", default="", help="camera flip, e.g. rotate-180")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--network", default="ssd-mobilenet-v2")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--calib", default=os.path.join(HERE, "calib.json"))
    ap.add_argument("--fpx", type=float, default=None, help="override focal length in px")
    ap.add_argument("--target", default="192.168.10.2")
    ap.add_argument("--port", type=int, default=wp.PORT_PERCEPTION)
    ap.add_argument("--mirror", default=None, metavar="HOST:PORT", help="forward status packets (dashboard.py)")
    ap.add_argument("--log-dir", default=os.path.join(HERE, "..", "logs"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    calib = load_calib(args.calib)
    f_px = args.fpx or calib.get("f_px")
    if args.input == "synthetic":
        f_px = f_px or 1000.0   # synthetic scene is self-consistent with whatever f_px it's given
        source = SyntheticSource(f_px, args.width, args.height)
    else:
        if not f_px:
            sys.exit("no focal length: run tx2/calibrate.py first (writes %s) or pass --fpx" % args.calib)
        source = JetsonSource(args)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", wp.PORT_STATUS))
    sock.settimeout(0.5)
    target = (args.target, args.port)
    mirror = None
    if args.mirror:
        host, _, port = args.mirror.rpartition(":")
        mirror = (host or "127.0.0.1", int(port))

    if not os.path.isdir(args.log_dir):
        os.makedirs(args.log_dir)
    log_path = os.path.join(args.log_dir, time.strftime("perception_%Y%m%d_%H%M%S.csv"))
    log_f = open(log_path, "w")
    log = csv.writer(log_f)
    log.writerow(["t_s", "seq", "n_det", "n_tracks", "fps", "proc_ms", "rt_state", "rt_ttc_ms",
                  "rt_threat_id", "rt_faults", "rt_latency_us", "rtt_ms", "threat_dist_m",
                  "threat_closing_ms"])
    print("logging to %s" % log_path)

    rx = StatusReceiver(sock, mirror)
    rx.start()
    tracker = Tracker()
    seq = 0
    rtt_ms = float("nan")
    t_prev = None
    fps = 0.0
    t_start = time.monotonic()
    last_print = 0.0

    try:
        while True:
            try:
                frame = source.capture()
            except EOFError:
                break
            if frame is None:
                continue
            img, w, h, raw = frame
            t_cap = time.monotonic()
            dt = (t_cap - t_prev) if t_prev else 1.0 / 30
            t_prev = t_cap
            fps = 0.9 * fps + 0.1 / dt if fps else 1.0 / dt

            cx = calib.get("cx") or w / 2.0
            dets = [d for d in (box_to_detection(lbl, c, box, f_px, cx, w, h) for lbl, c, box in raw) if d]
            tracks = tracker.step(dets, dt)
            # nearest first, so the 8 slots go to what matters
            tracks.sort(key=lambda t: t.dist)
            out = [wp.Track(t.id, t.cls, int(round(255 * min(1.0, t.conf))),
                            t.dist * 1000, t.lat * 1000, t.closing * 1000)
                   for t in tracks[:wp.MAX_TRACKS]]
            tx_us = int(time.monotonic() * 1e6)
            rx.sent(seq, tx_us)
            try:
                sock.sendto(wp.pack_perception(seq, tx_us, out), target)
            except OSError:
                pass            # cable out: keep running, the RT1170 will fail safe
            proc_ms = (time.monotonic() - t_cap) * 1000.0
            sent_seq = seq
            seq = (seq + 1) & 0xFFFFFFFF

            last_status, rtts = rx.drain()
            if rtts:
                rtt_ms = rtts[-1]

            state = last_status.state if last_status else None
            threat_id = last_status.threat_id if last_status else wp.THREAT_NONE
            threat = next((t for t in tracks if t.id == threat_id), None)

            # overlay: threat box in the RT1170's colour, other confirmed tracks green, raw grey
            overlays = []
            confirmed_boxes = set()
            for t in tracks:
                if t.box is None:
                    continue
                confirmed_boxes.add(t.box)
                if threat is t:
                    overlays.append((t.box, STATE_RGB.get(state, (255, 255, 255)), 4))
                else:
                    overlays.append((t.box, (46, 157, 74), 2))
            for d in dets:
                if d.box not in confirmed_boxes:
                    overlays.append((d.box, (160, 160, 160), 1))

            if last_status:
                s = last_status
                ttc = "%.2f s" % (s.ttc_ms / 1000.0) if s.ttc_ms >= 0 else "-"
                text = [("RT1170: %s  TTC %s" % (wp.state_name(s.state), ttc), STATE_RGB.get(s.state, (255, 255, 255))),
                        ("decision %.1f us  round trip %.1f ms  faults %s" % (
                            s.decision_latency_ns / 1000.0, rtt_ms, wp.fault_str(s.fault_flags)), (255, 255, 255))]
            else:
                text = [("RT1170: no status (link?)", (255, 255, 255))]
            text.append(("%.1f fps  %d tracks" % (fps, len(tracks)), (255, 255, 255)))
            if threat is not None:
                text.append(("threat #%d  %.1f m  closing %.1f m/s" % (threat.id, threat.dist, threat.closing),
                             STATE_RGB.get(state, (255, 255, 255))))
            try:
                source.render(img, overlays, text)
            except EOFError:
                break

            log.writerow(["%.4f" % (t_cap - t_start), sent_seq, len(dets), len(tracks), "%.2f" % fps,
                          "%.2f" % proc_ms,
                          wp.state_name(state) if state is not None else "",
                          last_status.ttc_ms if last_status else "",
                          threat_id if last_status else "",
                          last_status.fault_flags if last_status else "",
                          "%.2f" % (last_status.decision_latency_ns / 1000.0) if last_status else "",
                          "%.2f" % rtt_ms if not math.isnan(rtt_ms) else "",
                          "%.2f" % threat.dist if threat else "",
                          "%.2f" % threat.closing if threat else ""])

            if not args.quiet and t_cap - last_print > 1.0:
                last_print = t_cap
                print(" | ".join(line for line, _ in text))
    except KeyboardInterrupt:
        pass
    finally:
        log_f.close()
        print("log: %s" % log_path)


if __name__ == "__main__":
    main()
