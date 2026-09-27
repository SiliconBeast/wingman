#!/usr/bin/env python3
"""Measure the camera's focal length in pixels (f_px) for pinhole distance estimates.

A person of known height stands at tape-measured distances straight ahead of the camera.
At each distance this grabs ~2 s of detections, takes the median person-box height, and
computes f_px = box_height_px * distance / person_height. The average goes to tx2/calib.json.

  python3 tx2/calibrate.py --person-height 1.78 --dist 3 5 8

Stand fully in frame (head and feet visible), facing the camera. Python 3.6.
"""
import argparse
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--person-height", type=float, required=True, help="metres, shoes on")
    ap.add_argument("--dist", type=float, nargs="+", default=[3.0, 5.0, 8.0], help="metres")
    ap.add_argument("--input", default="csi://0")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--flip", default="")
    ap.add_argument("--network", default="ssd-mobilenet-v2")
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--out", default=os.path.join(HERE, "calib.json"))
    args = ap.parse_args()

    try:
        from jetson_inference import detectNet
        import jetson_utils as ju
    except ImportError:
        from jetson.inference import detectNet
        import jetson.utils as ju

    argv = ["--input-width=%d" % args.width, "--input-height=%d" % args.height]
    if args.flip:
        argv.append("--input-flip=%s" % args.flip)
    net = detectNet(args.network, [], 0.5)
    src = ju.videoSource(args.input, argv=argv)

    samples = []
    img_w = img_h = None
    for d in args.dist:
        input("\nStand %.2f m from the camera (tape-measured, to the lens), then press Enter..." % d)
        heights = []
        t_end = time.monotonic() + args.seconds
        while time.monotonic() < t_end:
            img = src.Capture()
            if img is None:
                continue
            img_w, img_h = img.width, img.height
            people = [x for x in net.Detect(img, overlay="none") if net.GetClassDesc(x.ClassID) == "person"]
            if len(people) != 1:
                continue            # exactly one person in view, or skip the frame
            p = people[0]
            if p.Top <= 2 or p.Bottom >= img.height - 2:
                continue            # head or feet cut off: height is wrong
            heights.append(p.Bottom - p.Top)
        if len(heights) < 5:
            print("  only %d usable frames at %.2f m (need one person, fully in frame) - skipped" % (len(heights), d))
            continue
        h = statistics.median(heights)
        f = h * d / args.person_height
        samples.append({"dist_m": d, "box_h_px": h, "frames": len(heights), "f_px": f})
        print("  %.2f m: median box height %.1f px over %d frames -> f_px %.1f" % (d, h, len(heights), f))

    if not samples:
        sys.exit("no usable measurements; nothing written")
    f_px = statistics.mean(s["f_px"] for s in samples)
    spread = (max(s["f_px"] for s in samples) - min(s["f_px"] for s in samples)) / f_px * 100
    calib = {
        "f_px": round(f_px, 1),
        "cx": img_w / 2.0,
        "width": img_w,
        "height": img_h,
        "person_height_m": args.person_height,
        "samples": samples,
        "measured": time.strftime("%Y-%m-%d %H:%M"),
    }
    with open(args.out, "w") as fp:
        json.dump(calib, fp, indent=2)
    print("\nf_px = %.1f (spread %.1f%% across distances) -> %s" % (f_px, spread, args.out))
    if spread > 10:
        print("spread > 10%%: re-check the tape measurements and that the whole body was in frame")


if __name__ == "__main__":
    main()
