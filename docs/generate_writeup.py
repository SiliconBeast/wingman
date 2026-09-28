#!/usr/bin/env python3
"""Generate the Wingman technical write-up PDF for the AAC/PHYTEC/NXP 2026 contest submission."""
import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image,
    PageBreak, HRFlowable, KeepTogether, ListFlowable, ListItem
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "wingman_technical_writeup.pdf")

styles = getSampleStyleSheet()
NAVY = colors.HexColor("#1a2744")
ACCENT = colors.HexColor("#2563a8")
GREY = colors.HexColor("#555555")

styles.add(ParagraphStyle("WTitle", parent=styles["Title"], fontSize=22, textColor=NAVY, spaceAfter=4))
styles.add(ParagraphStyle("WSubtitle", parent=styles["Normal"], fontSize=12.5, textColor=GREY,
                           spaceAfter=14, leading=16))
styles.add(ParagraphStyle("WPitch", parent=styles["Normal"], fontSize=12, leading=16,
                           textColor=NAVY, spaceAfter=14, fontName="Helvetica-Bold"))
styles.add(ParagraphStyle("H1", parent=styles["Heading1"], fontSize=15, textColor=NAVY,
                           spaceBefore=18, spaceAfter=8, fontName="Helvetica-Bold"))
styles.add(ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, textColor=ACCENT,
                           spaceBefore=10, spaceAfter=6, fontName="Helvetica-Bold"))
styles.add(ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=14.5, spaceAfter=8))
styles.add(ParagraphStyle("BodyBold", parent=styles["Body"], fontName="Helvetica-Bold"))
styles.add(ParagraphStyle("Mono", parent=styles["Normal"], fontName="Courier", fontSize=8.2,
                           leading=11, textColor=colors.HexColor("#222222"),
                           backColor=colors.HexColor("#f2f4f7"), borderPadding=8, spaceAfter=8))
styles.add(ParagraphStyle("Caption", parent=styles["Normal"], fontSize=8.5, textColor=GREY,
                           alignment=TA_CENTER, spaceAfter=14, spaceBefore=4, fontName="Helvetica-Oblique"))
styles.add(ParagraphStyle("WBullet", parent=styles["Body"], leftIndent=14, spaceAfter=5))

def rule():
    return HRFlowable(width="100%", thickness=0.75, color=colors.HexColor("#c9ced6"), spaceAfter=10)

def table_style(header_bg=NAVY):
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9ced6")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ])

def para_cell(text, style="Body"):
    return Paragraph(text, ParagraphStyle("cell", parent=styles[style], fontSize=8.7, leading=11.5))

story = []

# ---------- Title ----------
story.append(Paragraph("Wingman", styles["WTitle"]))
story.append(Paragraph(
    "A Jetson GPU perceives hazards; a PHYTEC phyBOARD-RT1170 makes the safety decision — "
    "deterministically — and fails safe the moment perception goes silent.",
    styles["WPitch"]))
story.append(Paragraph(
    "ADAS <i>prototype</i> (proof of concept, not a certified system) — All About Circuits / "
    "PHYTEC / NXP 2026 Embedded Design Contest, built around the PHYTEC phyBOARD-RT1170 "
    "(phyCORE-RT1170 SoM, NXP i.MX RT1176).",
    styles["WSubtitle"]))
story.append(rule())

# ---------- Problem ----------
story.append(Paragraph("The Problem", styles["H1"]))
story.append(Paragraph(
    "GPU-based perception is fast and powerful, but it is not deterministic: it can stall on a "
    "bad frame, crash, drop the camera, or simply take longer than expected under load. A safety "
    "system built entirely on that foundation inherits its uncertainty. Real vehicles solve this "
    "by splitting the problem: a perception computer figures out <i>what</i> is out there, and a "
    "separate, simpler, hardware-watchdogged ECU decides <i>whether it's dangerous</i> and what to "
    "do if the eyes go dark. Wingman is that split, built at prototype scale.",
    styles["Body"]))

# ---------- Architecture ----------
story.append(Paragraph("Architecture", styles["H1"]))
arch_table = Table([
    [para_cell("", "BodyBold"), para_cell("Perception (Jetson TX2) — the eyes", "BodyBold"),
     para_cell("Supervisor (phyBOARD-RT1170) — the reflexes", "BodyBold")],
    [para_cell("Runs", "BodyBold"), para_cell("Linux + CUDA / TensorRT"),
     para_cell("Zephyr RTOS on a 996 MHz Cortex-M7")],
    [para_cell("Good at", "BodyBold"), para_cell("heavy, fast, parallel vision"),
     para_cell("bounded-time decisions, instant boot")],
    [para_cell("Failure mode", "BodyBold"), para_cell("can stall, crash, drop frames"),
     para_cell("watched by a hardware watchdog")],
    [para_cell("Owns", "BodyBold"), para_cell("<i>what</i> is out there"),
     para_cell("<i>whether it's dangerous</i>, and what to do if the eyes go dark")],
], colWidths=[0.9*inch, 2.75*inch, 2.75*inch])
arch_table.setStyle(table_style())
story.append(arch_table)
story.append(Spacer(1, 8))

story.append(Paragraph(
    "[OV5693 CSI cam] -&gt; Jetson TX2: detectNet (TensorRT) -&gt; pinhole distance -&gt; Kalman tracker\n"
    "    -&gt; UDP perception frame @30 Hz --Ethernet--&gt; phyBOARD-RT1170 (Zephyr, Cortex-M7)\n"
    "                                                   validate (len/CRC/seq) -&gt; TTC -&gt; state machine\n"
    "                                                   -&gt; LEDs + probe GPIOs, 10 ms heartbeat -&gt; FAILSAFE\n"
    "                                                   hardware watchdog, onboard IMU context\n"
    "    &lt;----------------- UDP status (verdict, TTC, latency) ---'\n"
    "The TX2 draws boxes in the colour of the RT1170's verdict.",
    styles["Mono"]))

# ---------- Why the RT1170 ----------
story.append(Paragraph("Why the phyBOARD-RT1170", styles["H1"]))
story.append(Paragraph(
    "Wingman deliberately puts the phyBOARD-RT1170 where its strengths matter most: as the "
    "deterministic authority a GPU-based perception system reports <i>to</i>, not the other way "
    "around. Every safety decision, and the fail-safe response when perception goes dark, happens "
    "entirely on the RT1170's Cortex-M7 — independent of whether the Linux/GPU side is even alive.",
    styles["Body"]))
rt_points = [
    ("Deterministic, cycle-accurate timing", "Decision latency is measured with the M7's own "
     "996 MHz cycle counter (<font name=\"Courier\">k_cycle_get_32</font>), not wall-clock "
     "estimates — mean 49.79 &micro;s, p99 118.62 &micro;s, max 205.10 &micro;s across 1,614 real frames."),
    ("Hardware watchdog (WDOG1)", "Fed only by the highest-priority decision thread. Demonstrated "
     "live via a deliberate thread stall (<font name=\"Courier\">wm hang</font>): the board resets "
     "within ~0.5 s and reports the reset cause, read back from the SoC's own SRC register, over "
     "the wire on the very next boot."),
    ("Real-time Ethernet link", "The board's 1 Gbit <font name=\"Courier\">ENET_1G</font> interface "
     "(confirmed full-duplex, carrier up) carries a custom low-latency UDP protocol to the TX2 — "
     "round trip averages 0.87 ms, max 2.02 ms across 1,614 frames."),
    ("Fail-safe by design, not by afterthought", "Any loss of valid perception data — network "
     "flood, a crashed camera pipeline, corrupted frames — trips FAILSAFE within one heartbeat "
     "window. Measured over 50 real trials: mean 107.26 ms, max 112.36 ms, landing exactly on the "
     "\"~100 ms + one 10 ms heartbeat tick\" design prediction."),
    ("Onboard IMU (ICM-40627) integration", "Hit and fixed a real bug in this board's PHYTEC BSP "
     "branch (<font name=\"Courier\">v4.1.0-phy2</font>): the IMU's devicetree entry ships at the "
     "wrong I2C address. PHYTEC's own upstream repository already carried the fix on a newer "
     "branch; we traced it and applied the same one-line address correction via a targeted "
     "devicetree overlay rather than a full BSP upgrade this close to the deadline. Axis behavior "
     "was then verified against real physical motion."),
    ("Full test matrix on real silicon", "14 of 14 scenarios passed on the physical RT1170 — every "
     "scripted scenario plus every fault-injection variant (packet drop, CRC corruption, reorder, "
     "duplicate, rate change, burst-loss, simulated perception crash) — matching the board's own "
     "<font name=\"Courier\">native_sim</font> result exactly."),
]
for head, body in rt_points:
    story.append(Paragraph(f"<b>{head}.</b> {body}", styles["WBullet"]))

story.append(Paragraph(
    "The Jetson TX2 supplies the perception input and is, deliberately, supporting cast: every "
    "number above about <i>safety</i> was earned on the phyBOARD-RT1170 alone.",
    styles["Body"]))

story.append(PageBreak())

# ---------- How it works ----------
story.append(Paragraph("How It Works", styles["H1"]))

story.append(Paragraph("Protocol", styles["H2"]))
story.append(Paragraph(
    "146-byte perception frames (up to 8 tracks: id, class, confidence, distance, lateral offset, "
    "closing speed) and 36-byte status replies, little-endian, CRC-16/CCITT-FALSE, sequence "
    "numbers. Frames are sent every camera frame even with zero tracks — they <i>are</i> the "
    "heartbeat the supervisor watches.",
    styles["Body"]))

story.append(Paragraph("Supervisor threads (RT1170)", styles["H2"]))
story.append(Paragraph(
    "<font name=\"Courier\">net_rx</font> stamps each datagram with the cycle counter the instant "
    "the socket delivers it, validates length/CRC/magic/version, and queues it. "
    "<font name=\"Courier\">decide</font> (highest priority) runs the state machine, writes the "
    "LEDs, measures latency, and feeds the watchdog. A 10 ms timer detects heartbeat loss. "
    "<font name=\"Courier\">status_tx</font> replies to whoever sent the frame.",
    styles["Body"]))

story.append(Paragraph("Decision logic", styles["H2"]))
story.append(Paragraph(
    "In-path tracks (|lateral| &lt; 1.5 m, confidence &ge; 0.4, closing &gt; 0.1 m/s); "
    "time-to-collision = distance &divide; closing speed; the minimum-TTC track is the threat. "
    "Hysteresis: CAUTION below 2.5 s (exit above 2.8 s), WARNING below 1.2 s (exit above 1.5 s), "
    "plus a close-range override (&lt; 3 m and closing) for hazards too near for TTC math to "
    "matter. <b>Any state &rarr; FAILSAFE</b> after 100 ms without a valid frame, or 5 consecutive "
    "corrupt frames; recovery only after 10 consecutive in-sequence frames.",
    styles["Body"]))

story.append(Paragraph("IMU context", styles["H2"]))
story.append(Paragraph(
    "Sustained braking (&lt; &minus;0.3 g for 200 ms) downgrades WARNING to CAUTION — the driver "
    "is already reacting — except during the close-range override, where it never suppresses. A "
    "swerve during an active threat is logged as a near-miss; a &gt; 2 g spike freezes a "
    "64-decision black box.",
    styles["Body"]))

story.append(Paragraph("Perception (TX2)", styles["H2"]))
story.append(Paragraph(
    "SSD-MobileNet-v2 via TensorRT; monocular pinhole distance "
    "(<font name=\"Courier\">dist = f_px &middot; H_class / box_height</font>) with focal length "
    "measured by <font name=\"Courier\">calibrate.py</font>; constant-velocity Kalman tracker with "
    "gated nearest-neighbour association, requiring 3 consecutive hits before a track is trusted.",
    styles["Body"]))

# ---------- Results ----------
story.append(Paragraph("Results", styles["H1"]))
story.append(Paragraph(
    "All numbers below were measured on real hardware — the physical phyBOARD-RT1170 and Jetson "
    "TX2 — not simulated. Raw CSVs and the full scenario matrix are in the repository's "
    "<font name=\"Courier\">docs/</font> folder.",
    styles["Body"]))

results_table = Table([
    [para_cell("Metric", "BodyBold"), para_cell("Result", "BodyBold")],
    [para_cell("Decision latency (socket delivery &rarr; outputs)"),
     para_cell("n=1,614 &nbsp; min 11.92 / mean 49.79 / p99 118.62 / max 205.10 &micro;s")],
    [para_cell("Failover time (last valid frame &rarr; FAILSAFE)"),
     para_cell("n=50, <b>50/50 passed</b> &nbsp; min 102.32 / mean 107.26 / p99 112.36 / max 112.36 ms")],
    [para_cell("Round trip (TX2 &rarr; RT1170 &rarr; TX2)"),
     para_cell("n=1,614 &nbsp; min 0.50 / mean 0.87 / p99 1.37 / max 2.02 ms")],
    [para_cell("Scenario test matrix"),
     para_cell("<b>14/14 passed</b> — 6 scenarios &times; every fault-injection variant, matches native_sim")],
    [para_cell("Latency under network flood (5,000 pps)"),
     para_cell("RT1170 decision latency stays flat: 51.19 vs 47.46 &micro;s baseline. Real host-side "
                "packet-delivery gap found during flood's active window (21/30 vs 20/20 baseline) — "
                "traced to TX2 CPU/socket contention between two Python processes, not an RT1170 issue.")],
    [para_cell("Perception FPS"),
     para_cell("steady ~30.2 fps average (441 samples) over a 10-minute continuous live-camera soak, zero crashes")],
    [para_cell("CUDA Kalman kernel (Tegra X2, sm_62)"),
     para_cell("Loses to CPU at N=64 (0.91&times;, as expected from launch overhead); crossover at "
                "N=256 (3.33&times;); scales to 59-61&times; at N&ge;16,384. SoA vs AoS: 100% vs "
                "14.04%/12.50% memory-coalescing efficiency (nvprof), similar occupancy (~0.85 both)")],
], colWidths=[2.1*inch, 4.3*inch])
results_table.setStyle(table_style())
story.append(results_table)
story.append(Spacer(1, 10))

# ---------- Figure: room constraint ----------
img_path = os.path.join(HERE, "pos_check.jpg")
if os.path.exists(img_path):
    story.append(KeepTogether([
        Image(img_path, width=4.6*inch, height=4.6*inch*720/1280),
        Paragraph(
            "Fig. 1 — A real finding, not a staged shot: at the working distance available, a full "
            "standing person's feet fall outside the camera frame. This directly motivated the "
            "calibration workaround described under Limitations, and is left in this document as "
            "evidence rather than cropped out.",
            styles["Caption"]),
    ]))

# ---------- Limitations ----------
story.append(Paragraph("Honest Limitations", styles["H1"]))
limitations = [
    "Monocular distance estimation assumes a fixed real-world height per object class; truncated "
    "or unusually-sized objects will read wrong.",
    "The test space could not fit a full standing person in frame at any usable distance (Fig. 1), "
    "so the standard 3-distance person-based calibration procedure could not be used. A "
    "known-size-object workaround (a sheet of letter paper at two hand-held distances) was used "
    "instead, with a documented 13.3% spread between the two measurements — above the tooling's "
    "own 10% warning threshold, most likely from hand-held tilt imprecision. Distance/TTC numbers "
    "should be read as approximate, not precision-verified.",
    "Object tracking becomes unstable at extreme close range (roughly arm's length): the detector "
    "intermittently loses lock, which can cause the state machine to revert to NOMINAL abruptly — "
    "the detection was lost, not the threat resolved. A real characteristic of monocular detection "
    "at very close proximity, not a state-machine defect.",
    "The IMU's sign convention (which physical direction reads as \"braking\" vs. \"accelerating\") "
    "depends on which edge of the board faces vehicle-front in the final mounting. Left "
    "unconfigured deliberately, since it is a mounting decision, not a firmware bug — axis "
    "<i>assignment</i> (which channel responds to pitch vs. roll) was verified correct by physically "
    "moving the board and reading live IMU output.",
    "Decision-latency numbers above are the RT1170's own self-reported cycle-counter figures; no "
    "external logic analyzer was available to independently cross-check them against the two "
    "provided GPIO probe points.",
    "This is a bench prototype evaluated on live and replayed footage. It has not been tested in "
    "traffic, is not ISO 26262 qualified, and is not a product.",
]
story.append(ListFlowable(
    [ListItem(Paragraph(t, styles["Body"]), leftIndent=6) for t in limitations],
    bulletType="bullet", start="•", leftIndent=14,
))

# ---------- Future work ----------
story.append(Paragraph("Future Work", styles["H1"]))
for t in [
    "Redo camera calibration with the standard person-based method in a larger space for verified "
    "distance/TTC accuracy.",
    "Independent logic-analyzer cross-check of the decision-latency and failover measurements.",
    "Resolve IMU mounting orientation and lock in the sign convention for a specific vehicle install.",
    "TinyML driving-maneuver classifier on IMU windows (steady / brake / accelerate / swerve), "
    "scaffolded but not built due to time.",
    "M4 core as an independent hardware monitor of the M7's own heartbeat (dual-core redundancy), "
    "using the RT1170's Messaging Unit / IPM.",
    "Radar or GPS fusion, and a custom carrier board sized to the 30&times;30 mm phyCORE-RT1170 SoM.",
]:
    story.append(Paragraph(t, styles["WBullet"]))

story.append(PageBreak())

# ---------- BOM ----------
story.append(Paragraph("Bill of Materials", styles["H1"]))
bom_table = Table([
    [para_cell("Category", "BodyBold"), para_cell("Items", "BodyBold")],
    [para_cell("Hardware"),
     para_cell("PHYTEC phyBOARD-RT1170 dev kit (+5 V USB-C PSU) &bull; NVIDIA Jetson TX2 developer "
                "kit (+19 V PSU) &bull; Cat5e/6 Ethernet cable &bull; 2&times; micro-USB cables "
                "(console + USB-OTG flashing) &bull; OV5693 CSI camera (TX2 kit)")],
    [para_cell("Software / Toolchain"),
     para_cell("Zephyr 4.1 (PHYTEC BSP v4.1.0-phy2) &bull; Zephyr SDK 0.17.0 &bull; NXP SPSDK / "
                "blhost (USB-OTG flashing, no debug probe) &bull; JetPack 4.6.6 (L4T R32.7.6, CUDA "
                "10.2, TensorRT 8.2) &bull; jetson-inference &bull; Python 3")],
], colWidths=[1.6*inch, 4.8*inch])
bom_table.setStyle(table_style())
story.append(bom_table)

# ---------- Links ----------
story.append(Paragraph("Source and Raw Data", styles["H1"]))
story.append(Paragraph(
    'Full source, build/flash instructions, the complete bring-up runbook, and every raw '
    'measurement CSV referenced above: '
    '<link href="https://github.com/SiliconBeast/wingman" color="#2563a8">'
    'github.com/SiliconBeast/wingman</link>',
    styles["Body"]))

doc = SimpleDocTemplate(OUT, pagesize=letter,
                         topMargin=0.65*inch, bottomMargin=0.65*inch,
                         leftMargin=0.75*inch, rightMargin=0.75*inch,
                         title="Wingman — Technical Write-up",
                         author="Suvir")
doc.build(story)
print("Wrote", OUT)
