#!/usr/bin/env python3
"""Standalone diagrams for the Wingman contest submission (portal image uploads / README),
separate from the vector Figure 1 embedded in the technical write-up. Same visual language:
black/grey, Times-family text, native reportlab vector shapes rendered to PNG.
"""
import os
from reportlab.lib import colors
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon
from reportlab.graphics import renderPM

HERE = os.path.dirname(os.path.abspath(__file__))

BLACK = colors.black
GREY = colors.HexColor("#444444")
LIGHT_GREY = colors.HexColor("#f2f2f2")


def wrap_lines(text, max_chars):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if len(trial) > max_chars and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def box(d, x, y, w, h, lines, fontsize=9.5, fontname="Times-Bold", fill=colors.white, stroke=BLACK, stroke_width=1.2):
    d.add(Rect(x, y, w, h, strokeColor=stroke, strokeWidth=stroke_width, fillColor=fill))
    n = len(lines)
    lh = fontsize + 2.6
    top = y + h / 2 + (n - 1) * lh / 2
    for i, line in enumerate(lines):
        d.add(String(x + w / 2, top - i * lh, line, fontName=fontname, fontSize=fontsize,
                      textAnchor="middle", fillColor=stroke))


def h_arrow(d, x1, x2, y, label_lines=None, fontsize=8.0, label_dy=6, color=BLACK):
    forward = x2 >= x1
    xa, xb = (x1, x2 - 6) if forward else (x1, x2 + 6)
    d.add(Line(xa, y, xb, y, strokeColor=color, strokeWidth=1.1))
    tip = x2
    back = x2 - 8 if forward else x2 + 8
    d.add(Polygon([tip, y, back, y + 3.6, back, y - 3.6], strokeColor=color, fillColor=color))
    if label_lines:
        lh = fontsize + 2
        top = y + label_dy + (len(label_lines) - 1) * lh
        for i, line in enumerate(label_lines):
            d.add(String((x1 + x2) / 2, top - i * lh, line, fontName="Times-Italic",
                          fontSize=fontsize, textAnchor="middle", fillColor=GREY))


def v_arrow(d, x, y1, y2, label_lines=None, fontsize=8.0, label_dx=8, align="left", color=BLACK):
    forward = y2 <= y1
    ya, yb = (y1, y2 + 6) if forward else (y1, y2 - 6)
    d.add(Line(x, ya, x, yb, strokeColor=color, strokeWidth=1.1))
    tip = y2
    back = y2 + 8 if forward else y2 - 8
    d.add(Polygon([x, tip, x - 3.6, back, x + 3.6, back], strokeColor=color, fillColor=color))
    if label_lines:
        lh = fontsize + 2
        top = (y1 + y2) / 2 + (len(label_lines) - 1) * lh / 2
        anchor = "start" if align == "right" else "end"
        tx = x + label_dx if align == "right" else x - label_dx
        for i, line in enumerate(label_lines):
            d.add(String(tx, top - i * lh, line, fontName="Times-Italic",
                          fontSize=fontsize, textAnchor=anchor, fillColor=GREY))


# ============================================================
# Diagram 1: System architecture / data flow
# ============================================================
def build_architecture():
    W, H = 1000, 460
    d = Drawing(W, H)

    d.add(String(W / 2, H - 30, "Wingman -- System Architecture", fontName="Times-Bold",
                  fontSize=17, textAnchor="middle", fillColor=BLACK))
    d.add(String(W / 2, H - 52, "GPU perception (Jetson TX2) reporting to a deterministic "
                  "safety supervisor (PHYTEC phyBOARD-RT1170)",
                  fontName="Times-Italic", fontSize=10.5, textAnchor="middle", fillColor=GREY))

    bw, bh = 190, 100
    y_mid = 220
    x_cam, x_tx2, x_rt, x_out = 20, 250, 550, 810

    box(d, x_cam, y_mid, bw, bh, ["OV5693", "CSI Camera"], fontsize=12)
    box(d, x_tx2, y_mid, bw, bh,
        ["Jetson TX2", "detectNet (TensorRT,", "SSD-MobileNet-v2)", "+ Kalman tracker"], fontsize=10.5)
    box(d, x_rt, y_mid, bw, bh,
        ["phyBOARD-RT1170", "(Zephyr, Cortex-M7)", "validate -> TTC ->", "state machine -> outputs"], fontsize=10.5)
    box(d, x_out, y_mid, bw, bh,
        ["LEDs + GPIO probes", "hardware watchdog", "onboard IMU context"], fontsize=10.5)

    cy = y_mid + bh / 2
    h_arrow(d, x_cam + bw, x_tx2, cy)
    h_arrow(d, x_tx2 + bw, x_rt, cy, ["UDP perception frame", "146 B @ 30 Hz -- this IS the heartbeat"],
            label_dy=40, fontsize=9)
    h_arrow(d, x_rt + bw, x_out, cy)

    ry = y_mid - 55
    xm_rt = x_rt + bw / 2
    xm_tx2 = x_tx2 + bw / 2
    d.add(Line(xm_rt, y_mid, xm_rt, ry, strokeColor=BLACK, strokeWidth=1.1))
    d.add(Line(xm_rt, ry, xm_tx2 + 6, ry, strokeColor=BLACK, strokeWidth=1.1))
    d.add(Polygon([xm_tx2, ry, xm_tx2 + 8, ry + 3.6, xm_tx2 + 8, ry - 3.6], strokeColor=BLACK, fillColor=BLACK))
    d.add(Line(xm_tx2, ry, xm_tx2, y_mid, strokeColor=BLACK, strokeWidth=1.1))
    d.add(String((xm_rt + xm_tx2) / 2, ry - 16,
                  "UDP status reply: verdict, TTC, decision latency (36 B)",
                  fontName="Times-Italic", fontSize=9, textAnchor="middle", fillColor=GREY))

    d.add(String(W / 2, 22,
                  "The TX2 overlays live detection boxes in the colour of the RT1170's verdict "
                  "(green / amber / red).",
                  fontName="Times-Italic", fontSize=9, textAnchor="middle", fillColor=GREY))
    return d


# ============================================================
# Diagram 2: Safety state machine
# ============================================================
def build_state_machine():
    W, H = 1000, 620
    d = Drawing(W, H)

    d.add(String(W / 2, H - 30, "Wingman -- RT1170 Safety State Machine", fontName="Times-Bold",
                  fontSize=17, textAnchor="middle", fillColor=BLACK))
    d.add(String(W / 2, H - 52, "Evaluated independently of the Jetson TX2's health, on every frame",
                  fontName="Times-Italic", fontSize=10.5, textAnchor="middle", fillColor=GREY))

    bh = 56
    y_top = H - 130
    y_mid = y_top - 140

    boot_w, wl_w = 130, 150
    x_boot = 30
    x_wl = x_boot + boot_w + 60
    box(d, x_boot, y_top, boot_w, bh, ["BOOT"], fontsize=11)
    box(d, x_wl, y_top, wl_w, bh, ["WAIT_LINK"], fontsize=11)
    h_arrow(d, x_boot + boot_w, x_wl, y_top + bh / 2)

    nb_w = 160
    x_nom = x_wl
    box(d, x_nom, y_mid, nb_w, bh, ["NOMINAL"], fontsize=11.5)
    v_arrow(d, x_wl + wl_w / 2, y_top, y_mid + bh, ["first valid frame"], label_dx=10, align="right")

    gap = 70
    x_caution = x_nom + nb_w + gap
    x_warning = x_caution + nb_w + gap
    box(d, x_caution, y_mid, nb_w, bh, ["CAUTION"], fontsize=11.5)
    box(d, x_warning, y_mid, nb_w, bh, ["WARNING"], fontsize=11.5)

    box_top = y_mid + bh
    y_fwd = box_top + 16
    y_bwd = box_top + 40
    h_arrow(d, x_nom + nb_w, x_caution, y_fwd, ["TTC < 2.5 s"], label_dy=5, fontsize=8.3)
    h_arrow(d, x_caution, x_nom + nb_w, y_bwd, ["TTC > 2.8 s"], label_dy=5, fontsize=8.3)
    h_arrow(d, x_caution + nb_w, x_warning, y_fwd,
            ["TTC < 1.2 s, or dist < 3 m", "and closing (override)"], label_dy=5, fontsize=8.3)
    h_arrow(d, x_warning, x_caution + nb_w, y_bwd,
            ["TTC > 1.5 s (sustained braking", "downgrades WARNING->CAUTION)"], label_dy=22, fontsize=8.3)

    xm_nom = x_nom + nb_w / 2
    xm_warning = x_warning + nb_w / 2
    fw = (xm_warning + 40) - (xm_nom - 40)
    x_fail = xm_nom - 40
    fh = 60
    y_fail = 90
    box(d, x_fail, y_fail, fw, fh, ["FAILSAFE"], fontsize=13)

    for xb in (x_nom, x_caution, x_warning):
        xm = xb + nb_w / 2
        d.add(Line(xm, y_mid, xm, y_fail + fh + 6, strokeColor=BLACK, strokeWidth=1.0))
        d.add(Polygon([xm, y_fail + fh, xm - 3.6, y_fail + fh + 8, xm + 3.6, y_fail + fh + 8],
                       strokeColor=BLACK, fillColor=BLACK))
    d.add(String(x_fail + fw / 2, y_fail + fh + 40,
                  "any state -> FAILSAFE: heartbeat loss > 100 ms,",
                  fontName="Times-Italic", fontSize=9, textAnchor="middle", fillColor=GREY))
    d.add(String(x_fail + fw / 2, y_fail + fh + 28,
                  "or >= 5 consecutive corrupt frames",
                  fontName="Times-Italic", fontSize=9, textAnchor="middle", fillColor=GREY))

    x_ret = x_fail - 30
    d.add(Line(x_ret, y_fail + fh, x_ret, y_mid - 6, strokeColor=BLACK, strokeWidth=1.1))
    d.add(Polygon([x_ret, y_mid, x_ret - 3.6, y_mid - 8, x_ret + 3.6, y_mid - 8],
                   strokeColor=BLACK, fillColor=BLACK))
    for i, line in enumerate(["10 consecutive valid,", "in-sequence frames"]):
        d.add(String(x_ret - 10, (y_fail + fh + y_mid) / 2 - i * 11, line, fontName="Times-Italic",
                      fontSize=8.3, textAnchor="end", fillColor=GREY))

    d.add(String(W / 2, 22,
                  "Hysteresis prevents alert flicker at a threshold boundary; the close-range "
                  "override ignores TTC entirely inside 3 m.",
                  fontName="Times-Italic", fontSize=9, textAnchor="middle", fillColor=GREY))
    return d


if __name__ == "__main__":
    for name, builder in [("architecture_diagram", build_architecture),
                           ("state_machine_diagram", build_state_machine)]:
        d = builder()
        out = os.path.join(HERE, f"{name}.png")
        renderPM.drawToFile(d, out, fmt="PNG", dpi=200, bg=0xFFFFFF)
        print("Wrote", out)
