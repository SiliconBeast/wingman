#!/usr/bin/env python3
"""Generate the Wingman technical write-up PDF for the AAC/PHYTEC/NXP 2026 contest submission.
Formal research-paper style: Times New Roman, numbered sections, black/grey only.
"""
import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, ListFlowable, ListItem, Image, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon
from reportlab.graphics import renderPDF  # noqa: F401  (keeps renderer registered)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "wingman_technical_writeup.pdf")

BLACK = colors.black
GREY = colors.HexColor("#444444")
RULE_GREY = colors.HexColor("#999999")
TABLE_HEAD = colors.HexColor("#e2e2e2")

styles = getSampleStyleSheet()

styles.add(ParagraphStyle("WTitle", fontName="Times-Bold", fontSize=18, leading=22,
                           alignment=TA_CENTER, spaceAfter=6, textColor=BLACK))
styles.add(ParagraphStyle("WAffil", fontName="Times-Roman", fontSize=11, leading=14,
                           alignment=TA_CENTER, spaceAfter=4, textColor=GREY))
styles.add(ParagraphStyle("WPitch", fontName="Times-Italic", fontSize=11.5, leading=15,
                           alignment=TA_CENTER, spaceAfter=16, textColor=BLACK))
styles.add(ParagraphStyle("H1", fontName="Times-Bold", fontSize=13, leading=16,
                           spaceBefore=16, spaceAfter=8, textColor=BLACK))
styles.add(ParagraphStyle("H2", fontName="Times-Bold", fontSize=11.5, leading=14,
                           spaceBefore=10, spaceAfter=6, textColor=BLACK))
styles.add(ParagraphStyle("Body", fontName="Times-Roman", fontSize=10.5, leading=15,
                           spaceAfter=8, alignment=TA_JUSTIFY, textColor=BLACK))
styles.add(ParagraphStyle("BodyBold", parent=styles["Body"], fontName="Times-Bold", alignment=TA_JUSTIFY))
styles.add(ParagraphStyle("Mono", fontName="Courier", fontSize=8, leading=10.5,
                           textColor=BLACK, spaceAfter=8, spaceBefore=4,
                           borderWidth=0.6, borderColor=RULE_GREY, borderPadding=8))
styles.add(ParagraphStyle("Caption", fontName="Times-Italic", fontSize=9, leading=12,
                           alignment=TA_CENTER, textColor=GREY, spaceAfter=14, spaceBefore=4))
styles.add(ParagraphStyle("WBullet", parent=styles["Body"], leftIndent=16, spaceAfter=6))
styles.add(ParagraphStyle("Cell", fontName="Times-Roman", fontSize=9.3, leading=12.5, textColor=BLACK))
styles.add(ParagraphStyle("CellBold", parent=styles["Cell"], fontName="Times-Bold"))


def rule():
    return HRFlowable(width="100%", thickness=0.6, color=RULE_GREY, spaceBefore=2, spaceAfter=12)


def table_style():
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEAD),
        ("FONTNAME", (0, 0), (-1, 0), "Times-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.3),
        ("GRID", (0, 0), (-1, -1), 0.6, RULE_GREY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ])


def cell(text, bold=False):
    return Paragraph(text, styles["CellBold"] if bold else styles["Cell"])


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


def box(d, x, y, w, h, lines, fontsize=8.2, fontname="Times-Bold"):
    d.add(Rect(x, y, w, h, strokeColor=BLACK, strokeWidth=1, fillColor=colors.white))
    n = len(lines)
    lh = fontsize + 2.2
    top = y + h / 2 + (n - 1) * lh / 2
    for i, line in enumerate(lines):
        d.add(String(x + w / 2, top - i * lh, line, fontName=fontname, fontSize=fontsize,
                      textAnchor="middle", fillColor=BLACK))


def h_arrow(d, x1, x2, y, label_lines=None, fontsize=7.3):
    d.add(Line(x1, y, x2 - 6, y, strokeColor=BLACK, strokeWidth=1))
    d.add(Polygon([x2, y, x2 - 7, y + 3.2, x2 - 7, y - 3.2],
                   strokeColor=BLACK, fillColor=BLACK))
    if label_lines:
        lh = fontsize + 1.6
        top = y + 5 + (len(label_lines) - 1) * lh
        for i, line in enumerate(label_lines):
            d.add(String((x1 + x2) / 2, top - i * lh, line, fontName="Times-Italic",
                          fontSize=fontsize, textAnchor="middle", fillColor=GREY))


def build_flowchart():
    W, H = 468, 210
    d = Drawing(W, H)

    bw, bh = 96, 50
    y_mid = 148
    x_cam, x_tx2, x_rt, x_out = 6, 128, 258, 388

    box(d, x_cam, y_mid, bw, bh, ["OV5693", "CSI Camera"])
    box(d, x_tx2, y_mid, bw, bh, ["Jetson TX2", "detectNet + Kalman", "tracker (TensorRT)"], fontsize=7.6)
    box(d, x_rt, y_mid, bw, bh, ["phyBOARD-RT1170", "validate -> TTC ->", "state machine"], fontsize=7.6)
    box(d, x_out, y_mid, bw, bh, ["LEDs + GPIO probes", "watchdog, IMU", "context"], fontsize=7.6)

    cy = y_mid + bh / 2
    h_arrow(d, x_cam + bw, x_tx2, cy)
    h_arrow(d, x_tx2 + bw, x_rt, cy, ["UDP perception frame", "146 B @ 30 Hz (heartbeat)"])
    h_arrow(d, x_rt + bw, x_out, cy)

    # return path: RT1170 -> TX2, status packet
    ry = y_mid - 34
    d.add(Line(x_rt + bw / 2, y_mid, x_rt + bw / 2, ry, strokeColor=BLACK, strokeWidth=1))
    d.add(Line(x_rt + bw / 2, ry, x_tx2 + bw / 2 + 6, ry, strokeColor=BLACK, strokeWidth=1))
    d.add(Polygon([x_tx2 + bw / 2, ry, x_tx2 + bw / 2 + 7, ry + 3.2, x_tx2 + bw / 2 + 7, ry - 3.2],
                   strokeColor=BLACK, fillColor=BLACK))
    d.add(Line(x_tx2 + bw / 2, ry, x_tx2 + bw / 2, y_mid, strokeColor=BLACK, strokeWidth=1))
    d.add(String((x_rt + bw / 2 + x_tx2 + bw / 2) / 2, ry - 11,
                  "UDP status: verdict, TTC, decision latency (36 B)",
                  fontName="Times-Italic", fontSize=7.3, textAnchor="middle", fillColor=GREY))

    d.add(String(W / 2, 8,
                  "The TX2 overlays detection boxes in the colour of the RT1170's verdict.",
                  fontName="Times-Italic", fontSize=7.6, textAnchor="middle", fillColor=GREY))
    return d


story = []
sec = [0]


def heading(text):
    sec[0] += 1
    story.append(Paragraph(f"{sec[0]}. {text}", styles["H1"]))


def subheading(text):
    story.append(Paragraph(text, styles["H2"]))


# ---------- Title block ----------
story.append(Paragraph("Wingman", styles["WTitle"]))
story.append(Paragraph(
    "A Deterministic Safety Supervisor for GPU-Based Perception, Built on the "
    "PHYTEC phyBOARD-RT1170", styles["WAffil"]))
story.append(Paragraph(
    "Submission &mdash; All About Circuits / PHYTEC / NXP 2026 Embedded Design Contest",
    styles["WAffil"]))
story.append(Spacer(1, 8))
story.append(Paragraph(
    "<i>A Jetson GPU perceives hazards; a PHYTEC phyBOARD-RT1170 makes the safety decision "
    "&mdash; deterministically &mdash; and fails safe the moment perception goes silent.</i>",
    styles["WPitch"]))
story.append(rule())

# ---------- Abstract ----------
story.append(Paragraph("Abstract", styles["H2"]))
story.append(Paragraph(
    "Wingman is an advanced driver-assistance (ADAS) <i>prototype</i> &mdash; a proof of concept, "
    "not a certified system &mdash; that separates perception from safety decision-making across "
    "two processors, mirroring the architecture of production vehicle safety systems. An NVIDIA "
    "Jetson TX2 performs GPU-accelerated object detection and tracking; a PHYTEC phyBOARD-RT1170 "
    "(NXP i.MX RT1176, Zephyr RTOS) independently evaluates collision risk and enforces a fail-safe "
    "response, entirely on deterministic, watchdog-protected hardware. This document reports the "
    "system design and results measured on physical hardware, including a full fault-injection "
    "test matrix, decision-latency and failover timing, a CUDA kernel benchmark, and a real "
    "firmware bug found and corrected in the board's own PHYTEC BSP branch.",
    styles["Body"]))
story.append(rule())

# ---------- 1. Problem ----------
heading("The Problem")
story.append(Paragraph(
    "GPU-based perception is fast and capable, but it is not deterministic: it can stall on an "
    "unexpected frame, crash, drop the camera feed, or simply take longer than expected under "
    "load. A safety system built entirely on that foundation inherits its uncertainty. Production "
    "vehicles address this by separating concerns: a perception computer determines <i>what</i> is "
    "present in the environment, while a simpler, independently-watchdogged electronic control "
    "unit determines <i>whether it is dangerous</i> and what action to take if the perception "
    "system becomes unavailable. Wingman implements this separation at prototype scale.",
    styles["Body"]))

# ---------- 2. Architecture ----------
heading("System Architecture")
arch_table = Table([
    [cell("", True), cell("Perception &mdash; Jetson TX2", True),
     cell("Supervisor &mdash; phyBOARD-RT1170", True)],
    [cell("Platform", True), cell("Linux, CUDA / TensorRT"),
     cell("Zephyr RTOS, 996 MHz Cortex-M7")],
    [cell("Role", True), cell("Heavy, parallel computer vision"),
     cell("Bounded-time decision-making")],
    [cell("Failure mode", True), cell("May stall, crash, or drop frames"),
     cell("Monitored by a hardware watchdog")],
    [cell("Responsibility", True), cell("Determines what is present"),
     cell("Determines whether it is dangerous, and the response if perception is lost")],
], colWidths=[1.0*inch, 2.65*inch, 2.65*inch])
arch_table.setStyle(table_style())
story.append(arch_table)
story.append(Spacer(1, 10))

story.append(KeepTogether([
    build_flowchart(),
    Paragraph("Figure 1. Data flow between the perception and supervisor subsystems.",
              styles["Caption"]),
]))

# ---------- 3. Why the RT1170 ----------
heading("Why the phyBOARD-RT1170")
story.append(Paragraph(
    "Wingman deliberately positions the phyBOARD-RT1170 as the deterministic authority to which "
    "the GPU-based perception system reports, rather than as a passive peripheral. Every safety "
    "decision, and the fail-safe response when perception is lost, executes entirely on the "
    "RT1170's Cortex-M7, independent of the Linux/GPU subsystem's state.",
    styles["Body"]))

rt_points = [
    ("Deterministic, cycle-accurate timing.", "Decision latency is measured using the M7's own "
     "996 MHz cycle counter rather than wall-clock estimation: mean 49.79 &micro;s, 99th-percentile "
     "118.62 &micro;s, maximum 205.10 &micro;s across 1,614 measured frames."),
    ("Hardware watchdog (WDOG1).", "Fed exclusively by the highest-priority decision thread and "
     "demonstrated live via a deliberate thread stall: the board resets within approximately 0.5 "
     "seconds and reports the reset cause &mdash; read from the SoC's SRC register &mdash; on the "
     "subsequent boot."),
    ("Real-time Ethernet connectivity.", "The board's 1 Gbit ENET_1G interface (verified "
     "full-duplex, carrier active) carries a custom low-latency UDP protocol; round-trip latency "
     "to the TX2 averages 0.87 ms with a maximum of 2.02 ms across 1,614 frames."),
    ("Fail-safe behavior by design.", "Any interruption of valid perception data &mdash; network "
     "congestion, a crashed camera pipeline, or corrupted frames &mdash; triggers a FAILSAFE state "
     "within a single heartbeat window. Across 50 independent trials: mean 107.26 ms, maximum "
     "112.36 ms, consistent with the design target of approximately 100 ms plus one 10 ms "
     "heartbeat interval."),
    ("Onboard IMU (ICM-40627) integration.", "A defect was identified and corrected in this "
     "board's PHYTEC BSP branch (v4.1.0-phy2): the IMU devicetree entry specifies an incorrect I2C "
     "address. PHYTEC's own upstream repository contains a corresponding fix on a newer branch; "
     "the same one-line address correction was applied here via a targeted devicetree overlay "
     "rather than a full BSP upgrade. Sensor axis behavior was subsequently verified against "
     "physical motion of the board."),
    ("Complete fault-injection test coverage.", "All fourteen test scenarios &mdash; six scripted "
     "scenarios and every fault-injection variant, including packet loss, CRC corruption, "
     "reordering, duplication, rate variation, and simulated perception failure &mdash; passed on "
     "physical hardware, matching the result obtained under native simulation."),
]
for h, b in rt_points:
    story.append(Paragraph(f"<b>{h}</b> {b}", styles["WBullet"]))

story.append(Paragraph(
    "The Jetson TX2 supplies perception input and is, by design, a secondary component in this "
    "evaluation: every safety-critical measurement reported here was obtained on the phyBOARD-"
    "RT1170 alone.",
    styles["Body"]))

story.append(PageBreak())

# ---------- 4. System Design ----------
heading("System Design")

subheading("4.1 Communication Protocol")
story.append(Paragraph(
    "Perception frames are 146 bytes and encode up to eight tracked objects (identifier, class, "
    "confidence, distance, lateral offset, closing speed). Status replies are 36 bytes. Both use "
    "little-endian encoding, CRC-16/CCITT-FALSE integrity checking, and monotonic sequence "
    "numbers. Frames are transmitted on every camera cycle regardless of detection count, serving "
    "as the supervisor's heartbeat signal.",
    styles["Body"]))

subheading("4.2 Supervisor Threading Model")
story.append(Paragraph(
    "The RT1170 firmware runs four cooperating threads. <i>net_rx</i> timestamps each incoming "
    "datagram at the moment of socket delivery, validates length, CRC, magic number, and protocol "
    "version, and enqueues valid frames. <i>decide</i>, the highest-priority thread, executes the "
    "state machine, drives status LEDs, measures decision latency, and services the hardware "
    "watchdog. A 10 ms timer thread detects heartbeat loss. <i>status_tx</i> transmits the reply "
    "packet to the originating host.",
    styles["Body"]))

subheading("4.3 Decision Logic")
story.append(Paragraph(
    "Tracks are considered in-path when lateral offset is under 1.5 m, confidence is at least "
    "0.4, and closing speed exceeds 0.1 m/s. Time-to-collision (TTC) is computed as distance "
    "divided by closing speed; the track with minimum TTC is designated the primary threat. State "
    "transitions use hysteresis: CAUTION below 2.5 s (exit above 2.8 s), WARNING below 1.2 s (exit "
    "above 1.5 s), with an additional close-range override for objects within 3 m and closing, "
    "independent of computed TTC. Any state transitions to FAILSAFE after 100 ms without a valid "
    "frame or five consecutive corrupt frames; recovery to normal operation requires ten "
    "consecutive valid, in-sequence frames.",
    styles["Body"]))

subheading("4.4 Inertial Measurement Context")
story.append(Paragraph(
    "Sustained longitudinal deceleration below &minus;0.3 g for 200 ms downgrades a WARNING state "
    "to CAUTION, reflecting driver-initiated braking, except during the close-range override "
    "condition. A lateral swerve during an active threat is logged as a near-miss event; an "
    "impact exceeding 2 g latches a 64-decision black-box buffer.",
    styles["Body"]))

subheading("4.5 Perception Pipeline")
story.append(Paragraph(
    "Object detection uses SSD-MobileNet-v2 via TensorRT. Monocular distance is estimated by the "
    "pinhole relation distance = f &middot; H / h, where f is the calibrated focal length in "
    "pixels, H is an assumed real-world object height by class, and h is the detected bounding-box "
    "height in pixels. A constant-velocity Kalman filter with gated nearest-neighbor data "
    "association performs tracking, requiring three consecutive detections before a track is "
    "reported.",
    styles["Body"]))

img_camera = os.path.join(HERE, "camera_test2.jpg")
if os.path.exists(img_camera):
    story.append(KeepTogether([
        Spacer(1, 4),
        Image(img_camera, width=4.2*inch, height=4.2*inch*720/1280, hAlign="CENTER"),
        Paragraph(
            "Figure 2. A frame captured directly by the TX2's OV5693 CSI camera during initial "
            "hardware verification, prior to any perception software being run, confirming the "
            "camera pipeline was functioning correctly.",
            styles["Caption"]),
    ]))

# ---------- 5. Results ----------
heading("Results")
story.append(Paragraph(
    "All figures below were obtained on physical hardware &mdash; the phyBOARD-RT1170 and Jetson "
    "TX2 &mdash; rather than simulation. Complete raw data accompanies the project source.",
    styles["Body"]))

results_table = Table([
    [cell("Metric", True), cell("Result", True)],
    [cell("Decision latency (socket delivery to output)"),
     cell("n = 1,614; min 11.92, mean 49.79, p99 118.62, max 205.10 &micro;s")],
    [cell("Failover time (last valid frame to FAILSAFE)"),
     cell("n = 50, 50/50 passed; min 102.32, mean 107.26, p99 112.36, max 112.36 ms")],
    [cell("Round-trip latency (TX2 to RT1170 to TX2)"),
     cell("n = 1,614; min 0.50, mean 0.87, p99 1.37, max 2.02 ms")],
    [cell("Scenario / fault-injection test matrix"),
     cell("14 of 14 passed &mdash; six scenarios and every fault variant; matches native-simulation result")],
    [cell("Latency under network flood (5,000 packets/s)"),
     cell("RT1170 decision latency unaffected: 51.19 vs. 47.46 &micro;s baseline mean. A host-side "
          "packet-delivery irregularity was observed during the flood interval (21/30 vs. 20/20 "
          "scenario passes), attributed to CPU/socket contention on the TX2 host rather than the "
          "RT1170 firmware.")],
    [cell("Perception frame rate"),
     cell("Approximately 30.2 fps sustained (441 samples) over a 10-minute continuous live-camera evaluation, with no failures")],
    [cell("CUDA Kalman-filter kernel (Jetson TX2, sm_62)"),
     cell("CPU outperforms GPU at N = 64 (ratio 0.91); GPU becomes favorable at N = 256 (3.33"
          "&times;); scales to 59&ndash;61&times; at N &ge; 16,384. Structure-of-arrays layout "
          "achieves 100% global memory efficiency versus 14.04% / 12.50% for array-of-structures "
          "(measured via nvprof), at comparable occupancy")],
], colWidths=[2.2*inch, 4.1*inch])
results_table.setStyle(table_style())
story.append(results_table)
story.append(Spacer(1, 14))

img_1m = os.path.join(HERE, "paper_check_1m.jpg")
img_18m = os.path.join(HERE, "paper_check_1_8m.jpg")
if os.path.exists(img_1m) and os.path.exists(img_18m):
    w = 2.55*inch
    h = w*720/1280
    cal_imgs = Table(
        [[Image(img_1m, width=w, height=h), Image(img_18m, width=w, height=h)]],
        colWidths=[w+0.1*inch, w+0.1*inch],
    )
    cal_imgs.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(KeepTogether([
        cal_imgs,
        Paragraph(
            "Figure 3. The substitute calibration procedure described in Section 6: a sheet of "
            "standard letter paper (0.2794 m, portrait orientation) held at two measured distances "
            "from the camera, 1.0 m (left) and 1.8 m (right). Focal length was computed from the "
            "paper's measured pixel height at each distance; the two independent estimates differed "
            "by 13.3%, which is reported as a limitation rather than resolved by additional "
            "averaging. The subject's face is intentionally obscured by the paper in both frames.",
            styles["Caption"]),
    ]))

story.append(PageBreak())

# ---------- 6. Limitations ----------
heading("Limitations")
limitations = [
    "Monocular distance estimation assumes a fixed real-world height per object class; "
    "unusually-sized or partially-occluded objects will produce inaccurate distance estimates.",
    "The available test environment could not accommodate a fully-framed standing person at any "
    "usable camera distance, so the standard three-distance person-based calibration procedure "
    "could not be executed. A substitute procedure using a known-dimension object (a sheet of "
    "standard letter paper) at two measured distances was used instead, yielding a 13.3% spread "
    "between the two focal-length estimates &mdash; above the calibration tool's own 10% warning "
    "threshold, most plausibly attributable to imprecision in holding the reference object level "
    "by hand. Reported distance and time-to-collision values should be regarded as approximate.",
    "Object tracking exhibits reduced stability at very close range (approximately arm's length), "
    "where the detector intermittently loses lock. This can cause the state machine to revert to "
    "NOMINAL abruptly, reflecting loss of a confirmed detection rather than resolution of the "
    "underlying hazard.",
    "The IMU's sign convention &mdash; which physical direction corresponds to braking versus "
    "acceleration &mdash; depends on final vehicle-mounting orientation and was intentionally left "
    "unconfigured pending that decision. Axis <i>assignment</i> (distinguishing pitch from roll) "
    "was independently verified by physically manipulating the board and observing live sensor "
    "output.",
    "Reported latency figures are self-reported by the RT1170's internal cycle counter; no "
    "external logic analyzer was available to independently corroborate these measurements against "
    "the two GPIO timing probes provided for that purpose.",
    "This is a bench-evaluated prototype using live and replayed video. It is not qualified to "
    "ISO 26262 and is not a commercial product.",
]
story.append(ListFlowable(
    [ListItem(Paragraph(t, styles["Body"]), leftIndent=6, bulletFontName="Times-Roman") for t in limitations],
    bulletType="bullet", start="•", leftIndent=16,
))

# ---------- 7. Future Work ----------
heading("Future Work")
future = [
    "Repeat camera calibration using the standard person-based procedure in a larger test space "
    "to obtain verified distance and TTC accuracy.",
    "Perform an independent logic-analyzer cross-check of decision-latency and failover "
    "measurements.",
    "Finalize IMU mounting orientation and configure the corresponding sign convention for a "
    "specific vehicle installation.",
    "Implement a TinyML driving-maneuver classifier operating on IMU windows (steady, braking, "
    "accelerating, swerving); scaffolded but not completed within the contest timeline.",
    "Employ the RT1170's secondary Cortex-M4 core as an independent hardware monitor of the M7's "
    "heartbeat, via the Messaging Unit / inter-processor mailbox.",
    "Investigate radar or GPS sensor fusion, and a custom carrier board sized to the 30 &times; 30 "
    "mm phyCORE-RT1170 module.",
]
for t in future:
    story.append(Paragraph(t, styles["WBullet"]))

# ---------- 8. BOM ----------
heading("Bill of Materials")
bom_table = Table([
    [cell("Category", True), cell("Items", True)],
    [cell("Hardware"),
     cell("PHYTEC phyBOARD-RT1170 development kit with 5 V USB-C power supply; NVIDIA Jetson TX2 "
          "developer kit with 19 V power supply; Cat5e/6 Ethernet cable; two micro-USB cables "
          "(console and USB-OTG programming); OV5693 CSI camera module (included with TX2 kit)")],
    [cell("Software and Toolchain"),
     cell("Zephyr RTOS 4.1 (PHYTEC BSP v4.1.0-phy2); Zephyr SDK 0.17.0; NXP SPSDK / blhost "
          "(USB-OTG programming, no debug probe required); NVIDIA JetPack 4.6.6 (L4T R32.7.6, "
          "CUDA 10.2, TensorRT 8.2); jetson-inference; Python 3")],
], colWidths=[1.7*inch, 4.6*inch])
bom_table.setStyle(table_style())
story.append(bom_table)

# ---------- 9. Availability ----------
heading("Source Code and Data Availability")
story.append(Paragraph(
    "Complete source code, build and programming instructions, a step-by-step hardware bring-up "
    "procedure, and all raw measurement data referenced in this document are available at: "
    "<link href=\"https://github.com/SiliconBeast/wingman\" color=\"#000000\">"
    "<u>github.com/SiliconBeast/wingman</u></link>.",
    styles["Body"]))

doc = SimpleDocTemplate(OUT, pagesize=letter,
                         topMargin=0.85*inch, bottomMargin=0.85*inch,
                         leftMargin=0.9*inch, rightMargin=0.9*inch,
                         title="Wingman -- Technical Write-up",
                         author="Suvir")
doc.build(story)
print("Wrote", OUT)
