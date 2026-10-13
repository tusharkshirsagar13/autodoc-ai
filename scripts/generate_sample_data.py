"""Generate the SYNTHETIC sample HLD documents used for demos and tests.

All component, interface and signal names are fictional. The documents are
NOT AUTOSAR specifications and contain no OEM or supplier material.

Two revisions are produced with intentional differences and intentional
issues so that every feature (Q&A with citations, extraction, dependency
graph, revision comparison, consistency checks, exports) can be demonstrated.

Usage:  python scripts/generate_sample_data.py [output_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
DOC_TITLE = "Electronic Brake Control (EBC) Software — High-Level Design"

styles = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=16, spaceAfter=8)
H2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, spaceBefore=10, spaceAfter=6)
H3 = ParagraphStyle("h3", parent=styles["Heading3"], fontName="Helvetica-Bold", fontSize=11.5, spaceBefore=8, spaceAfter=4)
BODY = ParagraphStyle("body", parent=styles["BodyText"], fontName="Helvetica", fontSize=10, leading=13)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.5, leading=10.5)
BANNER = ParagraphStyle("banner", parent=BODY, textColor=colors.HexColor("#9a3412"), fontName="Helvetica-Bold")


def table(rows, widths):
    data = [[Paragraph(str(c), CELL) for c in row] for row in rows]
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbe4f0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def content(rev: str):
    is_b = rev == "B"
    story = []
    p = lambda text, style=BODY: story.append(Paragraph(text, style))  # noqa: E731

    # ---------------------------------------------------------------- page 1
    p(DOC_TITLE, H1)
    p(f"Document ID: SYN-EBC-HLD-001 &nbsp;&nbsp; Revision: {rev} &nbsp;&nbsp; Status: Draft for review")
    p("SYNTHETIC SAMPLE DOCUMENT — generated for the AutoDoc AI demonstration. All names are fictional. "
      "This is not an AUTOSAR specification and contains no OEM or supplier information.", BANNER)
    story.append(Spacer(1, 6))
    p("Revision History", H2)
    hist = [["Revision", "Date", "Description"], ["A", "2026-03-02", "Initial architecture baseline."]]
    if is_b:
        hist.append(["B", "2026-06-18", "Added PedalInputHandler, removed HmiWarningManager, updated signal "
                                         "resolutions, defined IF_ActuatorStatus, added degraded mode section."])
    story.append(table(hist, [25, 30, 115]))
    p("1 Introduction", H2)
    p("This High-Level Design (HLD) describes the software architecture of the Electronic Brake Control (EBC) "
      "application layer for a fictional passenger vehicle platform. It identifies the software components, their "
      "ports and port interfaces, the exchanged signals, the dependencies between components and the main "
      "functional flows. The document is intended for software architects, integration engineers and test engineers.")
    p("2 Scope", H2)
    p("The scope covers the application software components hosted on the brake ECU. Basic software modules, "
      "the hydraulic hardware and calibration data are outside the scope of this document.")
    p("3 Architecture Overview", H2)
    if is_b:
        p("The EBC application is organised as a processing chain. Pedal and wheel sensor information is converted "
          "into a vehicle state estimate, which is used to arbitrate a brake torque request. The arbitrated request "
          "is executed by the actuator layer. Diagnostic events from all components are collected centrally.")
    else:
        p("The EBC application is organised as a processing chain. Wheel sensor information is converted into a "
          "vehicle state estimate, which is used to arbitrate a brake torque request. The arbitrated request is "
          "executed by the actuator layer. Driver warnings are presented through the HmiWarningManager.")
    story.append(PageBreak())

    # ---------------------------------------------------------------- page 2
    p("4 Software Components", H2)
    p("Table 4-1 lists the application software components (SWCs) and their responsibilities.")
    comps = [["ID", "Component", "Type", "Description"],
             ["C-01", "WheelSpeedProcessor", "Application SWC",
              "Converts raw wheel sensor pulses into filtered wheel speeds for all four wheels every 10 ms."],
             ["C-02", "VehicleStateEstimator", "Application SWC",
              "Estimates vehicle longitudinal speed and wheel slip from wheel speeds."],
             ["C-03", "BrakeTorqueCoordinator", "Application SWC",
              "Arbitrates brake torque requests and computes the target brake torque per axle."],
             ["C-04", "BrakeActuatorManager", "Application SWC",
              "Commands the hydraulic modulator valves to realise the target brake torque."],
             ["C-05", "DiagnosticEventHandler", "Service SWC",
              "Collects diagnostic events reported by the EBC components and forwards them to the diagnostic stack."]]
    if is_b:
        comps.append(["C-06", "PedalInputHandler", "Sensor-Actuator SWC",
                      "Reads and plausibilises the brake pedal travel sensor and provides the driver brake request."])
    else:
        comps.append(["C-05", "HmiWarningManager", "Application SWC", ""])
    story.append(table(comps, [14, 42, 32, 82]))
    p("4.1 WheelSpeedProcessor", H3)
    p("Responsibilities: filter wheel sensor pulses, detect implausible wheel speed values and provide wheel speeds "
      "to downstream components.")
    p("The WheelSpeedProcessor sends Sig_WheelSpeed_FL to the VehicleStateEstimator together with the other three "
      "wheel speed signals.")
    p("4.2 VehicleStateEstimator", H3)
    p("The VehicleStateEstimator depends on the WheelSpeedProcessor. It uses Sig_YawRate, when available, to improve "
      "the slip estimate in curves.")
    p("4.3 BrakeTorqueCoordinator", H3)
    if is_b:
        p("The BrakeTorqueCoordinator receives Sig_BrakePedalPosition from the PedalInputHandler and combines it with "
          "the vehicle state to compute Sig_BrakeTorqueRequest.")
    else:
        p("The BrakeTorqueCoordinator requires the PedalInputHandler to obtain the driver brake request and combines it "
          "with the vehicle state to compute Sig_BrakeTorqueRequest.")
    p("4.4 BrakeActuatorManager", H3)
    p("The BrakeActuatorManager depends on the BrakeTorqueCoordinator and reports actuator health via the "
      "PP_ActuatorStatus port.")
    story.append(PageBreak())

    # ---------------------------------------------------------------- page 3
    p("5 Ports and Port Interfaces", H2)
    ports = [["Component", "Port", "Direction", "Interface", "Interface Type"],
             ["WheelSpeedProcessor", "PP_WheelSpeed", "Provided", "IF_WheelSpeed", "Sender-Receiver"],
             ["VehicleStateEstimator", "RP_WheelSpeed", "Required", "IF_WheelSpeed", "Sender-Receiver"],
             ["VehicleStateEstimator", "PP_VehicleState", "Provided", "IF_VehicleState", "Sender-Receiver"],
             ["BrakeTorqueCoordinator", "RP_VehicleState", "Required", "IF_VehicleState", "Sender-Receiver"],
             ["BrakeTorqueCoordinator", "PP_BrakeTorqueCmd", "Provided", "IF_BrakeTorqueCmd", "Sender-Receiver"],
             ["BrakeActuatorManager", "RP_BrakeTorqueCmd", "Required", "IF_BrakeTorqueCmd", "Sender-Receiver"],
             ["BrakeActuatorManager", "PP_ActuatorStatus", "Provided", "IF_ActuatorStatus", "Sender-Receiver"],
             ["DiagnosticEventHandler", "RP_DiagEvent", "Required", "IF_DiagEvent", "Client-Server"]]
    if is_b:
        ports += [["PedalInputHandler", "PP_PedalRequest", "Provided", "IF_PedalRequest", "Sender-Receiver"],
                  ["BrakeTorqueCoordinator", "RP_PedalRequest", "Required", "IF_PedalRequest", "Sender-Receiver"]]
    story.append(table(ports, [44, 38, 22, 36, 30]))
    story.append(Spacer(1, 8))
    p("Table 5-2 defines the port interfaces.")
    ifaces = [["Interface", "Type", "Data Elements / Operations", "Description"],
              ["IF_WheelSpeed", "Sender-Receiver", "Sig_WheelSpeed_FL, Sig_WheelSpeed_FR, Sig_WheelSpeed_RL, Sig_WheelSpeed_RR",
               "Filtered wheel speeds."],
              ["IF_VehicleState", "Sender-Receiver", "Sig_VehicleSpeed, Sig_LongitudinalSlip", "Estimated vehicle state."],
              ["IF_BrakeTorqueCmd", "Sender-Receiver", "Sig_BrakeTorqueRequest", "Target brake torque command."],
              ["IF_DiagEvent", "Client-Server", "", "Diagnostic event reporting service."]]
    if is_b:
        ifaces += [["IF_ActuatorStatus", "Sender-Receiver", "Sig_ActuatorHealth", "Actuator health status."],
                   ["IF_PedalRequest", "Sender-Receiver", "Sig_BrakePedalPosition", "Driver brake request."]]
    story.append(table(ifaces, [34, 28, 64, 44]))
    story.append(PageBreak())

    # ---------------------------------------------------------------- page 4
    p("6 Signals", H2)
    sigs = [["Signal", "Interface", "Data Type", "Unit", "Range", "Producer", "Consumer"]]
    for wheel in ("FL", "FR", "RL", "RR"):
        sigs.append([f"Sig_WheelSpeed_{wheel}", "IF_WheelSpeed", "uint16", "rpm", "0..3000", "WheelSpeedProcessor",
                     "VehicleStateEstimator"])
    if is_b:
        sigs += [["Sig_VehicleSpeed", "IF_VehicleState", "uint16", "0.01 km/h", "0..30000", "VehicleStateEstimator", "BrakeTorqueCoordinator"],
                 ["Sig_LongitudinalSlip", "IF_VehicleState", "sint8", "%", "-100..100", "VehicleStateEstimator", "BrakeTorqueCoordinator"],
                 ["Sig_BrakeTorqueRequest", "IF_BrakeTorqueCmd", "float32", "Nm", "0..6000", "BrakeTorqueCoordinator", "BrakeActuatorManager"],
                 ["Sig_ActuatorHealth", "IF_ActuatorStatus", "uint8", "-", "0..3", "BrakeActuatorManager", "DiagnosticEventHandler"],
                 ["Sig_BrakePedalPosition", "IF_PedalRequest", "uint8", "%", "0..100", "PedalInputHandler", "BrakeTorqueCoordinator"]]
    else:
        sigs += [["Sig_VehicleSpeed", "IF_VehicleState", "float32", "km/h", "0..300", "VehicleStateEstimator", "BrakeTorqueCoordinator"],
                 ["Sig_LongitudinalSlip", "IF_VehicleState", "sint8", "%", "-100..100", "VehicleStateEstimator", "BrakeTorqueCoordinator"],
                 ["Sig_BrakeTorqueRequest", "IF_BrakeTorqueCmd", "float32", "Nm", "0..5000", "BrakeTorqueCoordinator", ""]]
    story.append(table(sigs, [34, 24, 15, 15, 16, 33, 33]))
    story.append(Spacer(1, 8))
    p("7 Component Dependencies", H2)
    p("Table 7-1 lists the documented dependencies between software components.")
    deps = [["Source", "Relation", "Target", "Via", "Description"],
            ["VehicleStateEstimator", "depends on", "WheelSpeedProcessor", "IF_WheelSpeed", "Needs filtered wheel speeds."],
            ["BrakeTorqueCoordinator", "depends on", "VehicleStateEstimator", "IF_VehicleState", "Needs vehicle speed and slip."],
            ["BrakeActuatorManager", "depends on", "BrakeTorqueCoordinator", "IF_BrakeTorqueCmd", "Executes the torque command."],
            ["BrakeTorqueCoordinator", "depends on", "PedalInputHandler", "IF_PedalRequest", "Needs the driver brake request."],
            ["DiagnosticEventHandler", "depends on", "BrakeActuatorManager", "IF_ActuatorStatus", "Monitors actuator health."]]
    if not is_b:
        deps.append(["HmiWarningManager", "depends on", "DiagnosticEventHandler", "IF_DiagEvent", "Shows warnings for active faults."])
    story.append(table(deps, [40, 20, 40, 32, 38]))
    story.append(PageBreak())

    # ---------------------------------------------------------------- page 5
    p("8 Functional Flows", H2)
    if is_b:
        p("FF-01 Normal service braking: PedalInputHandler -> BrakeTorqueCoordinator -> BrakeActuatorManager")
        p("When the driver presses the brake pedal, the PedalInputHandler publishes Sig_BrakePedalPosition. The "
          "BrakeTorqueCoordinator converts the request into Sig_BrakeTorqueRequest using Sig_VehicleSpeed, and the "
          "BrakeActuatorManager applies the corresponding valve commands.")
    else:
        p("FF-01 Normal service braking: BrakeTorqueCoordinator -> BrakeActuatorManager")
        p("When the driver presses the brake pedal, the BrakeTorqueCoordinator converts Sig_BrakePedalPosition into "
          "Sig_BrakeTorqueRequest using Sig_VehicleSpeed, and the BrakeActuatorManager applies the corresponding "
          "valve commands.")
    p("FF-02 Wheel slip control: WheelSpeedProcessor -> VehicleStateEstimator -> BrakeTorqueCoordinator -> BrakeActuatorManager")
    p("If Sig_LongitudinalSlip exceeds the slip threshold, the BrakeTorqueCoordinator reduces the torque request on the "
      "affected axle until the slip returns below the threshold.")
    p("FF-03 Fault reporting: BrakeActuatorManager -> DiagnosticEventHandler")
    p("Actuator faults are reported to the DiagnosticEventHandler, which stores the event and informs the "
      "diagnostic stack.")
    if is_b:
        p("9 Degraded Mode Handling", H2)
        p("If Sig_VehicleSpeed is invalid for more than 100 ms, the BrakeTorqueCoordinator switches to a degraded mode "
          "that applies the driver request without slip control. The DiagnosticEventHandler records the transition.")
    p("Appendix A Component Catalogue", H2)
    p("The catalogue below summarises component purposes for onboarding.")
    cat = [["Component", "Responsibility"],
           ["VehicleStateEstimator", "Calculates the steering angle target for lane keeping."],
           ["BrakeActuatorManager", "Commands the hydraulic modulator valves to realise the target brake torque."]]
    story.append(table(cat, [50, 120]))
    return story


def build(rev: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"EBC_HLD_Synthetic_Rev{rev}.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title=f"{DOC_TITLE} (Synthetic, Rev {rev})",
                            author="AutoDoc AI sample generator", subject="Synthetic sample — not a real specification")
    doc.build(content(rev))
    return path


def main(out_dir: Path | None = None) -> list[Path]:
    out_dir = out_dir or ROOT / "sample_data"
    return [build("A", out_dir), build("B", out_dir)]


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    for path in main(target):
        print(f"Wrote {path}")
