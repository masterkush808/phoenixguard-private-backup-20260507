from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUT = (
    ROOT
    / "reports"
    / "PhoenixGuard_V3_Continuous_Market_Intelligence_Blueprint_2026-07-25.pdf"
)

NAVY = colors.HexColor("#10233F")
BLUE = colors.HexColor("#1E5AA8")
TEAL = colors.HexColor("#0B817D")
GOLD = colors.HexColor("#C88D1A")
RED = colors.HexColor("#B4404C")
PALE = colors.HexColor("#EDF3FA")
INK = colors.HexColor("#172233")
MUTED = colors.HexColor("#536577")


class Arrow(Flowable):
    def __init__(self, width=12, height=18, color=BLUE):
        super().__init__()
        self.width, self.height, self.color = width, height, color

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setFillColor(self.color)
        y = self.height / 2
        self.canv.setLineWidth(1.5)
        self.canv.line(1, y, self.width - 5, y)
        p = self.canv.beginPath()
        p.moveTo(self.width - 5, y + 4)
        p.lineTo(self.width, y)
        p.lineTo(self.width - 5, y - 4)
        p.close()
        self.canv.drawPath(p, fill=1, stroke=0)


def styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=27,
                                leading=32, textColor=colors.white, alignment=TA_LEFT, spaceAfter=10),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"], fontName="Helvetica", fontSize=11.5,
                                   leading=16, textColor=colors.HexColor("#D7E6F8")),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=19,
                              leading=24, textColor=NAVY, spaceBefore=12, spaceAfter=9),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=13.5,
                              leading=17, textColor=BLUE, spaceBefore=10, spaceAfter=6),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontName="Helvetica", fontSize=9.25,
                                leading=13.4, textColor=INK, spaceAfter=6),
        "small": ParagraphStyle("small", parent=base["BodyText"], fontName="Helvetica", fontSize=7.7,
                                 leading=10.3, textColor=MUTED, spaceAfter=3),
        "callout": ParagraphStyle("callout", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=10,
                                   leading=14, textColor=NAVY, spaceAfter=0),
        "box": ParagraphStyle("box", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8.2,
                                leading=10.8, alignment=TA_CENTER, textColor=NAVY),
        "table": ParagraphStyle("table", parent=base["BodyText"], fontName="Helvetica", fontSize=7.6,
                                  leading=10.1, textColor=INK),
        "tablehead": ParagraphStyle("tablehead", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=7.4,
                                      leading=9.6, textColor=colors.white),
    }


S = styles()


def p(text, style="body"):
    return Paragraph(text, S[style])


def bullets(items):
    return KeepTogether([p("• " + item) for item in items])


def table(headers, rows, widths):
    data = [[p(x, "tablehead") for x in headers]]
    for row in rows:
        data.append([p(str(x), "table") for x in row])
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C7D5E4")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def callout(label, text, color=TEAL):
    t = Table([[p(f"{label}<br/><font name='Helvetica'>{text}</font>", "callout")]], colWidths=[17.2 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E7F5F3") if color == TEAL else colors.HexColor("#FFF5E4")),
        ("BOX", (0, 0), (-1, -1), 0.7, color),
        ("LINEBEFORE", (0, 0), (0, -1), 4, color),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def flow(labels, fills=None):
    fills = fills or [PALE] * len(labels)
    cells = []
    for idx, label in enumerate(labels):
        cells.append(Table([[p(label, "box")]], colWidths=[3.1 * cm], rowHeights=[1.35 * cm], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), fills[idx]), ("BOX", (0, 0), (-1, -1), 0.65, BLUE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])))
        if idx != len(labels) - 1:
            cells.append(Arrow())
    return Table([cells], colWidths=[3.1*cm if not isinstance(c, Arrow) else .45*cm for c in cells], hAlign="LEFT")


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#B8CADC"))
    canvas.line(doc.leftMargin, A4[1] - 1.15 * cm, A4[0] - doc.rightMargin, A4[1] - 1.15 * cm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(doc.leftMargin, A4[1] - .85 * cm, "PHOENIXGUARD V3  |  CONTINUOUS MARKET INTELLIGENCE BLUEPRINT")
    canvas.drawRightString(A4[0] - doc.rightMargin, .78 * cm, f"Page {doc.page}")
    canvas.drawString(doc.leftMargin, .78 * cm, "Source-grounded local system blueprint • 25 July 2026")
    canvas.restoreState()


def section(story, title):
    story.append(p(title, "h1"))


def build():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, rightMargin=1.35*cm, leftMargin=1.35*cm,
                            topMargin=1.65*cm, bottomMargin=1.35*cm, title="PhoenixGuard V3 Continuous Market Intelligence Blueprint")
    story = []

    cover = Table([[p("PHOENIXGUARD V3<br/>CONTINUOUS MARKET<br/>INTELLIGENCE", "title"),
                    p("A source-grounded blueprint of how every proven closed candle becomes microstructure evidence, Pair DNA, a classified opportunity, an entry decision, and — only after independent validation — an external handoff.<br/><br/>Version: FINAL_LIVE / V3<br/>Prepared: 25 July 2026<br/>Streaming hardening revision: 28 July 2026", "subtitle")]],
                  colWidths=[10.8*cm, 6.4*cm], rowHeights=[10.2*cm])
    cover.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), NAVY), ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
                               ("LEFTPADDING", (0,0), (-1,-1), 16), ("RIGHTPADDING", (0,0), (-1,-1), 14),
                               ("TOPPADDING", (0,0), (-1,-1), 18), ("BOTTOMPADDING", (0,0), (-1,-1), 18)]))
    story += [Spacer(1, 2.1*cm), cover, Spacer(1, .55*cm),
              callout("EXECUTIVE POSITION", "PhoenixGuard is a local-first chart intelligence and execution-control workstation. It must answer BUY_NOW, SELL_NOW or DO_NOT_ENTER honestly; silence and generic WAIT are not substitutes for a current explanation. A direction, confidence score, overlay, regression study, stream event or dashboard display is not trade authority.", GOLD), Spacer(1, .55*cm),
              p("This document describes the repository’s active V3/FINAL_LIVE architecture and its declared boundaries. It is an engineering blueprint, not financial advice, a performance claim, or an instruction to place an order.", "small"), PageBreak()]

    section(story, "1. Executive blueprint")
    story += [p("PhoenixGuard separates a high-risk workflow into independently accountable layers. It observes a locked broker/chart surface, reconstructs what is visible candle by candle, builds one explainable regression study from current and historical evidence, applies a rule-based playbook, exposes a plain-language operator story, and permits an external handoff only if a separate executable-packet contract succeeds."),
              callout("CORE DOCTRINE", "Observation ≠ study ≠ execution. The sole executable artefact is a fresh <b>PG_EXECUTION_PACKET_V3</b> carrying an explicit accepted <b>PG_ALLOWANCE_PACKAGE_V1</b>.", TEAL),
              p("The layered design is intentional: screenshots can be stale; a visual model can be wrong; an overlay can be misaligned; historical similarity can be weak; and an apparent opportunity can be late. No single signal can jump directly to the execution edge."),
              p("The canonical authority chain", "h2"),
              flow(["Broker / chart<br/>surface", "Capture &amp;<br/>vision", "Decision &amp;<br/>playbook", "Packet<br/>validator", "External<br/>handoff"], [colors.HexColor("#E8F2FE"), colors.HexColor("#EAF8F6"), colors.HexColor("#FFF5E4"), colors.HexColor("#FCEBED"), colors.HexColor("#EAF8F6")]),
              Spacer(1, .25*cm),
              table(["Stage", "Question answered", "Can it trade?"], [
                  ["Observation", "What is visibly on the locked chart?", "No"],
                  ["Study", "What conditions, paths and setups are plausible?", "No"],
                  ["Permission", "Does the current full context meet the playbook?", "Not by itself"],
                  ["Packet validation", "Is the exact permission current, internally consistent and safe to hand off?", "Eligible only after pass"],
                  ["External boundary", "Does the independent bridge accept the allowance package?", "Separate downstream control"],
              ], [3.0*cm, 10.2*cm, 4.0*cm]), PageBreak()]

    section(story, "2. Runtime topology and source of truth")
    story += [p("The canonical full-live launcher is <font name='Helvetica-Bold'>Backend/launch/launch_phoenixguard_live_ready.ps1</font>. It resolves the project-local live environment, prepares bounded runtime output, runs integrity preflight, starts the local stack, and verifies a single logical topology. The normal local API address is <font name='Helvetica-Bold'>127.0.0.1:8793</font>; the canonical session is <font name='Helvetica-Bold'>pocket-live-8788</font>."),
              table(["Process / surface", "Owns", "Explicitly does not own"], [
                  ["24/7 tracker orchestrator", "Lifecycle, session creation, source locking and liveness", "Trade permission in the launcher"],
                  ["FastAPI mobile API", "Session state, capture services, public APIs and dashboard delivery", "Browser-owned market truth"],
                  ["CPU stream observer", "Continuous locked-surface sampling, bounded temporal evidence and event-gated keyframes", "Running the heavy study for every sample or treating correlated frames as votes"],
                  ["Model Council + playbook", "Evidence reconciliation, maturity and study/execution candidate output", "Bypassing packet validation"],
                  ["shooter.py package reporter", "Accepted allowance-package handshake", "Clicking, calibration, amount edits or broker timing"],
                  ["MT4 / external bridge", "Independent revalidation and optional file handoff", "Weakening the V3 contract"],
                  ["Dashboard", "Explainable rendering and commands", "Truth, permission or closed-candle study progression"],
              ], [3.7*cm, 6.9*cm, 6.6*cm]),
              p("State and publication", "h2"),
              p("A capture is published through an atomic session commit. The exact frame’s source identity, chart/window artifacts, overlay geometry, study contributors, council result, public state, and closed-candle study transition are bound together. Stale writers are rejected so a slow result cannot overwrite a newer frame. Live session artifacts are ephemeral under runtime/live; durable study memory lives under data/mobile_api/window_tracker/market_study_v3."),
              callout("FRONTEND CONTRACT", "The dashboard renders three answers first: where the market came from, which direction was and is being studied, and whether to BUY_NOW, SELL_NOW or DO_NOT_ENTER. Details remain available below; the browser does not recalculate truth or permission.", TEAL), PageBreak()]

    section(story, "3. How the chart is studied: capture, identity and reconstruction")
    story += [p("Every live study begins with a real external broker/chart window. The tracker resolves the locked handle, captures the complete window, derives or applies a chart-plane crop, records window/chart hashes, confirms market and timeframe continuity, then fails closed if the source is missing, changed, stale or ambiguous."),
              flow(["Locked<br/>window", "Chart-plane<br/>crop", "Source / pair /<br/>timeframe lock", "Candle &amp;<br/>structure vision", "Exact-frame<br/>artifacts"], [PALE, PALE, colors.HexColor("#FCEBED"), colors.HexColor("#EAF8F6"), PALE]), Spacer(1, .25*cm),
              table(["Study output", "How it is derived", "Use downstream"], [
                  ["Market identity", "Broker surface + source lock + detected pair/timeframe", "Rejects wrong-window / mixed-instrument truth"],
                  ["Candles", "Visible candle geometry, current forming candle and latest confirmed closed candle", "Exact micro-study, sequence identity and continuous history"],
                  ["Structure", "Impulse, pullback, retest, continuation, swings, support/resistance, supply/demand", "Playbook and price-location context"],
                  ["Chart transform", "Exact window-space ↔ chart-space mapping", "Keeps all overlays aligned with the frame"],
                  ["Quality / novelty", "Parse confidence, ensemble disagreement, OOD / artifact checks", "Caps confidence or makes entry DO_NOT_ENTER with a reason"],
              ], [3.4*cm, 8.2*cm, 5.6*cm]),
              p("Closed-candle identity bridge", "h2"),
              p("Tracker track_id, generic id and rolling sequence indices are positional display identifiers, not durable candle identity. For screenshot-only rows without a stable source timestamp, persistence requires exact <font name='Helvetica-Bold'>PG_CLOSED_CANDLE_IDENTITY_STATE_V3</font> proof: a stable closed-candle event key plus a non-negative monotonic event sequence for the same pair/timeframe. The resolver promotes only its confirmed current/batch rows. A prior event becomes eligible for outcome study only when it is uniquely re-observed at the exact predecessor position on the current candle axis. Shifted, replayed or arbitrarily reacquired windows cannot manufacture that proof."),
              p("Overlay and positioning discipline", "h2"),
              p("Raw detections are normalized to V3 object aliases, anchored to candle geometry, clipped to chart bounds, merged and collision-checked, then rendered only against their exact frame. Real source object keys remain distinct. Pixel bounds, touch points and anchor-wick points are normalized only with the exact captured image dimensions, after which raw pixel geometry is stripped. Anonymous objects receive observation-local <font name='Helvetica-Bold'>OBSERVATION_ONLY</font> identity. Graph edges are bounded study-only observations: never inferred causation, permission or an order. Entry, target and invalidation areas must be reconstructable from the anchor frame, preserve the correct price relationship, and be hidden rather than guessed when the global re-projection fit is not proven."),
              bullets(["BUY_LIMIT_ZONE: pullback opportunity below current price; SELL_LIMIT_ZONE: rally opportunity above current price.",
                       "BUY_STOP_ENTRY_ZONE and SELL_STOP_ENTRY_ZONE are prospective confirmation boundaries, not interchangeable protective stops.",
                       "PROTECTIVE_STOP_ZONE records plan invalidation with explicit broker-order side and thesis side.",
                       "NO_VALID_ZONE is an explicit honest classification when evidence cannot support a causal, anchored area."]), PageBreak()]

    section(story, "4. CPU-first continuous observation and event-gated study")
    story += [p("Streaming in V3 means continuously observing the same verified chart surface while keeping expensive reconstruction and ensemble inference event-gated. The constrained CPU-only profile targets 0.5 samples per second and may be raised only after measurement; the supported range is 0.5 to 8 FPS. Those samples are temporal evidence, not independent opinions. The observer performs only bounded image normalization, stable hashing, compact grayscale differencing, identity checks and event classification; the existing heavy V3 study runs only when a material event or heartbeat keyframe is accepted."),
              flow(["Verified local<br/>window capture", "0.5 FPS CPU<br/>baseline", "Bounded temporal<br/>evidence", "Latest accepted<br/>keyframe", "Heavy V3 study<br/>on demand"], [PALE, colors.HexColor("#EAF8F6"), PALE, colors.HexColor("#FFF5E4"), colors.HexColor("#FCEBED")]), Spacer(1, .25*cm),
              table(["Streaming stage", "Bounded responsibility", "Must never do"], [
                  ["Capture producer", "One process-global CPU stream acquires the locked HWND without activation; stamp monotonic and wall-clock times", "Queue unbounded full-resolution frames, steal broker focus or silently switch windows"],
                  ["Light observer", "Compute stable content digest, downsampled change, rest/motion evidence and identity generation", "Run model council, count repeated samples as votes or create candle closes"],
                  ["Ring buffers", "Retain only a tiny recent full-frame window plus compact grayscale evidence", "Become a video archive or retain stale pair/geometry generations"],
                  ["Keyframe handoff", "One pending slot plus one explicitly tracked in-flight keyframe; identity/geometry reset may replace pending work", "Build latency, lose in-flight lineage or admit a second global producer"],
                  ["Heavy study worker", "Consume one current keyframe, reconstruct the chart and publish through the atomic V3 commit", "Overwrite newer state with a slow stale result"],
                  ["SSE workspace", "Publish current stream health, three operator answers and exact-frame evidence", "Infer truth or permission in the browser"],
              ], [3.25*cm, 8.6*cm, 5.35*cm]),
              callout("GLOBAL SINGLE-FLIGHT / LATEST-FRAME-WINS", "There is at most one process-global CPU producer. Its handoff has one pending slot and one separately tracked in-flight keyframe. While either is occupied, routine material and heartbeat admissions coalesce; sequence, metrics and drop counters still advance, so no hidden inference backlog forms.", TEAL), PageBreak()]

    story += [p("Temporal evidence taxonomy", "h2"),
              table(["Observed class", "Evidence recorded", "Heavy-study behavior"], [
                  ["DUPLICATE", "Stable hash match or change below the duplicate floor; consecutive count and age advance", "Discard; never a new vote, candle, trend or state transition"],
                  ["REST", "Small but persistent bounded change consistent with a quiet/forming chart", "Accumulate descriptive rest duration; study only if a material threshold or heartbeat is reached"],
                  ["MATERIAL_CHANGE", "Change score, changed-area ratio or identity-safe motion exceeds the configured event threshold", "Admit only when no pending/in-flight study exists; otherwise coalesce while recording metrics"],
                  ["HEARTBEAT", "Maximum silence interval expires while identity remains valid", "Admit only when idle; otherwise coalesce without erasing the supported decision"],
                  ["IDENTITY_RESET", "Pair/selector, timeframe, HWND, focus/source lock, content size, DPI, ROI or geometry epoch changes", "Break through the busy gate, increment generation, flush stale evidence/pending work and force rebind"],
                  ["CAPTURE_FAILURE", "Window unavailable, minimized/occluded path invalid, focus ambiguous, capture exception or dimensions impossible", "Fail closed, retain diagnostic reason, retry with bounded backoff; never reuse old pixels as current"],
              ], [3.2*cm, 8.7*cm, 5.3*cm]),
              p("Busy means coalesce, not stop observing. Every accepted CPU sample still advances lightweight frame sequence, stable hashes, motion/rest measurements, duration counters and identity/geometry comparison. Routine material changes and heartbeats cannot create a second heavy admission while pending or in-flight work exists. A genuine identity or geometry reset is evaluated first and may break through that gate so stale pair/coordinates are revoked immediately."),
              p("A quiet heartbeat is an observation about liveness, not a market verdict. Stream duplicates and degraded snapshot-watchdog duplicates preserve the current supported BUY_NOW, SELL_NOW or DO_NOT_ENTER explanation; they update health only and never overwrite it with generic WAIT. When entry gates are not satisfied, the public answer is the explicit <font name='Helvetica-Bold'>DO_NOT_ENTER</font> with a current reason—not an unexplained holding label."),
              p("Cross-process stream truth", "h2"),
              p("The native tracker and HTTP API are separate processes. The tracker therefore publishes one atomic, replace-in-place <font name='Helvetica-Bold'>cpu_stream_v3.json</font> status sidecar at startup, approximately once per second, on degradation or recovery, and on stop. It contains bounded counters, ring and memory proof, monotonic lineage summaries and the current failure reason—never pixels or video. The public projection strips hashes, host paths, window identity, geometry internals and raw direction while explicitly denying broker-click authority. Compact session and operator reads merge this record without loading the large historical session document. Schema or session mismatch is rejected; unavailable or stopped telemetry fails closed. The forming-chart freshness window follows the slower of four target periods or three observed periods, with an eight-second floor and 45-second ceiling; it never refreshes closed-candle evidence or permission. Construction and capacity failures publish the same bounded record, so a producer that never starts still explains itself honestly."),
              p("Stable lineage and reset discipline", "h2"),
              p("Each stream owns a random stream_id, a monotonic frame_seq and a stream_generation. Content digests use a stable cryptographic hash such as BLAKE2 or SHA-256 over canonical pixels and declared dimensions; Python process-local hash values are forbidden. Every accepted keyframe carries captured_epoch, monotonic capture time, HWND, source identity, pair, timeframe, window/content dimensions, DPI, chart ROI, geometry epoch, input_frame_hash and generation. This allows an exact frame to be traced through analysis, artifacts, operator state and packet diagnostics."),
              p("The stable visible pair/timeframe selector fingerprint contract hashes a thresholded bright-glyph mask from the broker’s narrow selected-instrument control, excluding animated payout sparklines, clocks and candle pixels. The keyframe fingerprint must agree with its own full-window pixels. A change invalidates cached pair/timeframe reuse, forces a fresh selector scan and identity rebind, and keeps market identity pending until a normalized symbol is confirmed; the prior pair’s candle field, Pair DNA and decision cannot leak into the new pair."),
              bullets(["A pair or timeframe change is a semantic reset even if the pixels look similar.",
                       "An HWND or focus/source-lock change is a provenance reset even if the broker application is unchanged.",
                       "A resize, DPI, chart-plane ROI or geometry transform change is a coordinate reset; old boxes and pending results become ineligible.",
                       "A reset atomically clears recent evidence and the pending keyframe, increments generation and requires fresh identity proof before study resumes.",
                       "A late heavy result is rejected if its generation, frame sequence or exact-frame lineage no longer matches current session truth."]), PageBreak()]

    story += [p("Exact closed-candle causality inside a stream", "h2"),
              p("Continuous pixels improve attention between accepted studies; they do not redefine what a completed candle is. Intrabar samples may update forming-candle movement, wick development, pressure, rest/motion duration and a watch thesis. Pair DNA, outcome maturation, motif promotion, regression history and any execution-eligible decision advance only from the existing authoritative closed-candle resolver. The current closed-candle event key, order domain and monotonic sequence must still prove exactly one new completed candle."),
              callout("NO CORRELATED-FRAME VOTING", "Ten near-identical frames from one forming candle remain one correlated observation stream. They cannot be counted as ten model votes, ten historical cases, ten supports or a confidence multiplier. Ensemble diversity must come from independently defined models or evidence lanes, not temporal duplication.", GOLD),
              callout("BOUND BEFORE CROP", "The maximum pixel budget is checked against the complete captured window before any focus or chart crop. A small selected ROI cannot conceal an oversized full-window allocation from the CPU/RAM guard.", TEAL),
              p("Capture trust order", "h2"),
              table(["Capture route", "Acceptance rule", "Failure behavior"], [
                  ["Offscreen PrintWindow", "High-rate route whenever the broker is not already foreground; exact HWND/title and broker pixels must validate", "Never activates, raises or steals focus from the broker at stream cadence"],
                  ["Visible MSS / ImageGrab", "Allowed only when the locked HWND already owns foreground before and after the exact-rectangle grab", "Never activates the window; any ownership/title change falls back offscreen"],
                  ["Snapshot watchdog recovery", "Separate low-frequency recovery may request bounded visible verification under explicit controls", "Never becomes the CPU stream route or turns a duplicate into decision evidence"],
                  ["External frame feed", "Study-only unless it independently proves the full local execution source contract", "broker-click-safe remains false"],
              ], [3.25*cm, 9.1*cm, 4.85*cm]),
              p("The producer uses its own capture backend instance so thread-affine MSS state is not shared unsafely with the heavy worker. At stream cadence an offscreen chart is read only through validated PrintWindow; the stream never calls foreground activation. Desktop capture is allowed only when the locked broker already owns focus and still owns it after capture. On repeated failure the stream reports degraded/stopped health and uses bounded retry rather than spinning, switching screens or presenting stale pixels as live."), PageBreak()]

    story += [p("Three operator questions and the action boundary", "h2"),
              table(["Question shown first", "Required plain-language answer", "Evidence underneath"], [
                  ["1. Where is the market from, and how did it behave?", "Origin, major direction, swing/rest character and the relevant historical behavior", "Closed-candle structure, behavioral sequence, Pair DNA and exact paths"],
                  ["2. What direction was studied, and what is being studied now?", "Previous studied side, current major/inner direction, setup maturity and what would confirm or invalidate it", "Regression, object relationships, current forming evidence and playbook"],
                  ["3. What is the best decision to do right now?", "Enter a validated BUY/SELL, watch a pullback or rally, hold/protect, avoid chasing, or wait—with the decisive reason and change condition", "Current entry gates, live stream read, entry areas, freshness, source/geometry identity, risk, council and packet state"],
              ], [5.4*cm, 7.1*cm, 4.7*cm]),
              p("The first viewport is a calm decision cockpit, not a field catalogue. Stream status is one compact line—observing, latest material event age, accepted/dropped keyframes and source health—with detailed telemetry collapsed. Labels-on may reveal every chart label, but the three answers remain readable and independent of overlay density."),
              p("BUY_NOW and SELL_NOW are operator-facing conclusions produced only when the playbook says ENTER_NOW and current evidence survives all entry gates. When entry is not currently authorized, Question 3 still gives the useful next action—watch the named pullback/rally area, hold or protect a confirmed trade, avoid chasing an expired move, or wait for one explicit condition. The answer must name whether the blocker is forming, insufficient, invalidated, late, stale, contradictory or runtime integrity. These words do not themselves click a broker. Only a fresh validated <font name='Helvetica-Bold'>PG_EXECUTION_PACKET_V3</font> with an accepted allowance may cross the external handoff boundary; direct local broker clicks remain disabled."),
              p("CPU and memory envelope", "h2"),
              table(["Resource", "Target envelope on the CPU-only workstation", "Control"], [
                  ["Acquisition", "0.5 FPS constrained-host baseline; one global stream across sessions", "Configurable 0.5-8 FPS only after measurement; no second producer or overlapping capture tasks"],
                  ["Light processing", "Small grayscale/downsample differencing and stable digest only", "No model inference, OCR sweep or graph construction per raw sample"],
                  ["Full frames", "Latest current frame plus only a very small bounded recent ring", "Full-window pixel bound before focus crop; fixed item/byte cap and generation flush"],
                  ["Temporal data", "A few seconds of compact grayscale summaries and counters", "Fixed duration/count cap; no raw video persistence"],
                  ["Heavy study", "Single-flight with one pending and one tracked in-flight slot", "Busy material/heartbeat coalescing; reset breakthrough; stale-result rejection"],
                  ["Persistence", "Significant keyframes, derived features, proof and closed-candle history; one replace-in-place stream-health sidecar", "No continuous video archive; telemetry is one bounded schema-bound file"],
              ], [3.0*cm, 8.5*cm, 5.7*cm]), PageBreak()]

    story += [p("Failure recovery and certification", "h2"),
              p("The stream is an optional V3 observation accelerator with a snapshot-compatible fallback. A producer crash, capture-path loss or observer exception cannot kill the API or manufacture a decision. The supervisor records the fault, clears ineligible pending work, backs off, reacquires the locked surface and starts a new generation. Restart recovery restores durable closed-candle study state, not stale in-memory pixel rings. Emergency stop and session stop terminate the producer and prevent further heavy-work wakeups."),
              p("Safe relaunch proves ownership before deleting disposable state. If managed Windows denies CIM/WMI command-line inspection, the canonical launcher falls back to psutil and selects a process only when both the exact resolved PhoenixGuard repository and a fixed runtime entrypoint token match. It excludes itself and its ancestors, validates the fresh V3 runtime lock while port 8793 is active, stops the attributed tracker/API, reporter, disk-guard and MT4-bridge trees, rescans for survivors, and independently proves port 8793 closed. Any uncertainty aborts before cleanup, so no unrelated Python process is terminated and no split stack is launched."),
              p("Shutdown is a service-wide lifecycle barrier. The shutdown event is set before workers are signalled. Stream ensure checks it before constructing a capture backend/observer and checks it again under the service lock immediately before registry insertion and thread.start(); a shutdown race closes the partly constructed backend and cannot leave a late producer running."),
              table(["Certification layer", "Required proof"], [
                  ["Deterministic unit tests", "Stable hashes and visible selector fingerprint; forced pair/timeframe rebind; duplicate/rest/material/heartbeat classification; identity/geometry reset; bounded rings"],
                  ["Concurrency/lifecycle tests", "One global producer; one pending plus tracked in-flight; busy admission coalescing; reset breakthrough; stale-result rejection; shutdown-before-create/start race"],
                  ["Causality tests", "Intrabar samples never advance Pair DNA or close history; exactly one identity-proven close advances once; repeated frames never increase vote/support counts"],
                  ["Geometry tests", "Full-window pixel bound executes before focus crop; pair/HWND/focus/resize/DPI/ROI changes flush state; pixels, overlays and artifacts share dimensions"],
                  ["Performance tests", "Measured light observation remains within the configured CPU-rate band and declared RAM caps while heavy study is single-flight and backlog remains at most one"],
                  ["API/browser tests", "SSE and HTTP agree; three answers remain first; compact and cached operator reads receive fresh cross-process stream health; stale/tampered status fails closed; diagnostics remain collapsed"],
                  ["Live certification", "Offscreen PrintWindow never activates broker at stream cadence; foreground capture identity, pair rebind, geometry reset, recovery and local-click disablement are observed"],
              ], [4.0*cm, 13.2*cm]),
              callout("STREAMING AUTHORITY", "Streaming can make PhoenixGuard more attentive and more timely. It cannot grant execution authority, bypass exact closed-candle causality, repair an invalid source lock, or convert repeated pixels into independent evidence.", RED), PageBreak()]

    section(story, "5. Intelligence lanes: how a study is classified")
    story += [p("The main image-analysis entry point is <font name='Helvetica-Bold'>Frontend/dashboard/main.py::run_inference</font>. It composes computer-vision detections, chart/sequence extraction, optional local ensemble output, memory retrieval, uncertainty models, reinforcement-learning context, 13 core skill gates plus support gates, and an ensemble decision. These models may remain internal contributors, but their old public forecast lanes are retired. Their outputs are inputs to study and council logic; they are not execution authority."),
              table(["Lane", "What it contributes", "Authority"], [
                  ["Computer vision", "Detected patterns, candles, chart geometry, market state and reasoning trace", "Observational"],
                  ["Scene / sequence models", "Internal structure and sequence evidence from awake, versioned models", "Internal contributor; retired as a public visual route"],
                  ["High-frequency / candle models", "Near-term candle and movement context", "Internal contributor; never an entry lane"],
                  ["Visual memory", "Top-k analogous situations, similarity, ambiguity and transition tendencies", "Advisory; disagreement can reduce confidence"],
                  ["A* scenario engine", "Ranked continuation, pullback, reversal-attempt and fakeout paths", "Decision-kernel evidence"],
                  ["Regression / RL", "Quantiles, policy probabilities, online-feedback context and calibration features", "Diagnostic contributor"],
                  ["Skill gates", "Structured quality, sequence, regime and risk diagnostics", "Contributor gates, not execution authority"],
              ], [3.3*cm, 9.6*cm, 4.3*cm]),
              p("A* scenario classification", "h2"),
              p("The A* engine remains an internal challenger over CONTINUE, PULLBACK, REVERSAL_ATTEMPT and FAKEOUT transitions. It may inform the council, but Path A/Path B and double-lane public rendering are retired. The operator receives one studied BUY, SELL or MIXED_EVIDENCE regression read with confidence and reasons, while the separate entry answer remains exactly BUY_NOW, SELL_NOW or DO_NOT_ENTER."),
              p("Core skill gates", "h2"),
              p("The active source defines thirteen core gates: calibrated probability/conformal interval, discrete FSM state, algorithmic evidence heap, regression error estimation, knowledge representation, candle-group context, formal automata, predictive analytics and related base gates. Its router can learn relative weighting from feedback. The historical comment’s consensus recipe (confidence ≥ 0.82, at least nine gates passing and memory similarity ≥ 0.87) is a diagnostic threshold, not permission to trade."), PageBreak()]

    section(story, "6. Deep candle intelligence, Pair DNA and historical memory")
    story += [p("Every proven completed candle enters a V3 study lane before any directional read is published. Price OHLC is used when available; otherwise normalized-price or pixel-price proxies remain explicitly labelled so chart pixels are never presented as broker prices or pips."),
              table(["Service", "What it stores or measures", "Integrity boundary"], [
                  ["Candle Intelligence V3", "Exact body/range, upper and lower wicks, wick/body ratios, close location, candle type, personality, rejection, sweep, acceptance and relation to the previous candle", "Closed candles only; mixed or contradictory coordinate spaces fail closed"],
                  ["Behavioral Sequence V3", "UP swing, DOWN swing and REST states; segment candles and seconds; change, efficiency, transitions, major trend and inner trend", "Current segment remains open until a later boundary proves completion"],
                  ["Exact candle ledger", "SQLite WAL row keyed by pair, timeframe and authoritative closed-candle identity", "Upsert prevents duplicate unique candles; WAL + FULL sync + immediate transaction"],
                  ["Pair DNA", "Unique chronological candle aggregates, completed swing/rest boundaries, transitions, regimes, objects and non-causal outcome associations", "Locks CLOSED_TIMESTAMP_V1 or TRACKER_EVENT_SEQUENCE_V3 per pair/timeframe; conflicting order domains are skipped"],
                  ["Historical similarity", "Explainable 60-value sequence fingerprint, same-pair nearest sequences and supported UP/DOWN/REST outcomes", "Outcome requires exact resolver N to N+1 plus unique prior-close re-observation on the current axis"],
                  ["Object relationship graph", "Explicit candle anchors, observed-with, co-occurrence and proven normalized overlap", "No inferred anchor, causal claim, order field or execution authority"],
              ], [3.25*cm, 8.85*cm, 5.1*cm]),
              p("The one studied direction", "h2"),
              p("Major regression carries 52% of the directional study weight, inner regression 30%, and a support-qualified historical continuation 18%. Opposing evidence reduces confidence. History stays unavailable until at least three causally labelled similar cases exist, and close probabilities remain MIXED_EVIDENCE rather than being forced into BUY or SELL."),
              callout("NO AUTOSCALE OR REACQUISITION LEAKAGE", "A prior pixel/proxy close is never compared directly with a newly scaled frame. Positional IDs never persist. The current resolver event must be exactly prior sequence +1 and the prior close must be uniquely found again among the current frame's earlier candles; otherwise outcome maturation is skipped.", GOLD), PageBreak()]

    section(story, "7. From classification to a trading decision")
    story += [p("The decision kernel turns independent evidence into a coherent market read: regime, price location, path clarity, timing, risk, continuation/reversal plausibility and opposing-force room. It produces a candidate directional thesis only. The Book Strategy Master V3 then applies the complete rule set and is the final strategy decider; Model Council V3 contributes freshness, model health, source lock, sequence, timing and promotion evidence around it."),
              flow(["Classified<br/>market scene", "Decision<br/>kernel", "Book Strategy<br/>Master V3", "Model Council<br/>promotion", "Study or packet<br/>publication"], [PALE, colors.HexColor("#EAF8F6"), colors.HexColor("#FFF5E4"), colors.HexColor("#FFF5E4"), colors.HexColor("#FCEBED")]), Spacer(1, .25*cm),
              p("Playbook classification", "h2"),
              table(["Decision concept", "Examples in the implementation", "Effect"], [
                  ["Playbook family", "SMC turtle soup; BMS/RTO; AMD reversal; supply/demand reaction; trendline/channel; Fibonacci OTE; pivot reaction; slingshot false break; chop/no-trade", "Chooses the trading grammar that must fit the observed structure"],
                  ["Reaction grammar", "Wick rejection, body acceptance, retest hold, reclaim after sweep, continuation pressure, exhaustion, no reaction", "Distinguishes a real response from a merely nearby level"],
                  ["Entry profile", "Aggressive sniper, conservative retest, continuation retest, reversal reclaim, momentum acceptance, watch-only, no-trade", "Controls the evidence expected before maturity"],
                  ["Maturity state", "NO_OPPORTUNITY → EARLY_FORMING → VALID_WATCH → PREPARE → ENTER_NOW; or LATE_CHASE / INVALIDATED / MISSED", "Makes timing explicit and prevents late relabeling"],
              ], [3.3*cm, 8.5*cm, 5.4*cm]),
              p("Hard blockers and fail-closed behaviors", "h2"),
              bullets(["Stale, non-advancing or inconsistent live truth; dirty cache; missing required models; unhealthy API; invalid source lock.",
                       "Late chase, invalidated candidate, buy-high / sell-low classification, insufficient path room or opposing force too close.",
                       "Incomplete candle proof should remain WATCH/PREPARE rather than become a false runtime failure; it still cannot be entered.",
                       "The public interface keeps Major trend, Inner trend, Regression study and Entry permission separate so a historical lean cannot be mistaken for a permitted trade."]), PageBreak()]

    section(story, "8. Permission and packet validation: the real execution boundary")
    story += [p("A permitted trade is not just BUY or SELL. The current system requires an explicit playbook ENTER_NOW decision, council evidence, a current packet, and a separate schema/runtime validation. The executable packet is built by <font name='Helvetica-Bold'>execution/packet_v3.py::build_execution_packet_v3</font> and verified by <font name='Helvetica-Bold'>validate_execution_packet_v3</font>."),
              table(["Required contract group", "Validation expectation", "Why it exists"], [
                  ["Identity", "Session, symbol, timeframe, instrument context and source lock are present and match expected values", "Prevents wrong-chart / wrong-session action"],
                  ["Freshness", "Frame, capture and state versions advance; valid-until is in the future; fresh cache and hashes are supplied", "Prevents stale action"],
                  ["Live integrity", "is_live, frame_advancing, capture_advancing and state_advancing are true; source is model_council", "Separates runtime failure from a market decision"],
                  ["Execution", "state EXECUTABLE; side strictly BUY or SELL; expiry matches time sequence; amount action is DO_NOT_CHANGE_AMOUNT", "Eliminates legacy ambiguity and local amount control"],
                  ["Council health", "final_state/side agree; all required models awake; health provenance is carried", "Avoids unsupported/misaligned permission"],
                  ["Allowance", "Explicit PG_ALLOWANCE_PACKAGE_V1, accepted and execution-ready", "Makes handoff scope explicit"],
              ], [3.55*cm, 8.5*cm, 5.15*cm]),
              callout("WHAT IS REJECTED", "Raw action / execution_action payloads, old schemas, STUDY_PACKETs, CALL/PUT aliases, missing final side, stale packets, non-advancing frames and identity mismatches are not executable packets.", RED),
              p("The packet’s authority metadata carries <font name='Helvetica-Bold'>PLAYBOOK_FINAL_DECIDER_V3</font> as strategy authority and <font name='Helvetica-Bold'>PG_EXECUTION_PACKET_V3</font> as packet authority. Packet validation classifies freshness, identity, cache, live-state and model-awake failures as RUNTIME_INTEGRITY—not as a reason to reinterpret unsafe state as a market blocker."), PageBreak()]

    section(story, "9. What ‘traded’ means in FINAL_LIVE")
    story += [p("In the canonical FINAL_LIVE setup, direct local broker clicking is disabled. The launcher may enable live evaluation and construction of permission state, while PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS remains disabled. The historical shooter is now a package reporter, not a clicker."),
              flow(["Validated<br/>packet", "Accepted allowance<br/>package", "shooter.py<br/>handshake", "MT4 / external<br/>bridge revalidation", "Optional external<br/>execution"], [colors.HexColor("#FCEBED"), colors.HexColor("#FFF5E4"), PALE, PALE, colors.HexColor("#EAF8F6")]), Spacer(1, .25*cm),
              table(["Component", "Behavior at the handoff edge"], [
                  ["Execution packet endpoint", "Exposes executable packets only; a visible study is deliberately not enough."],
                  ["shooter.py", "Fetches the V3 packet, rejects absent/stale/malformed/contradictory payloads, and writes a bounded accepted-package handshake only."],
                  ["Allowance package", "Must be explicit, accepted, execution-ready and of a supported type such as INTRADAY_ENTER_NOW or SWING."],
                  ["MT4 file bridge", "Compacts and revalidates the allowance. It rejects inferred, missing, non-accepted, non-ready or non-professional packages before any command handoff."],
                  ["Broker amount / time", "The execution contract carries DO_NOT_CHANGE_AMOUNT; the local reporter does not calibrate controls, edit amount or set broker timing."],
              ], [4.4*cm, 12.8*cm]),
              callout("IMPORTANT BOUNDARY", "The repository proves a controlled handoff architecture, not a promise of broker fill, price, profit or loss avoidance. Broker rules, spread, slippage, trigger conventions and fill mechanics remain external and variable.", GOLD), PageBreak()]

    section(story, "10. Continuous closed-candle research history")
    story += [p("PhoenixGuard studies every newly proven pair/timeframe-scoped closed candle exactly once. No operator button creates a study baseline and no preset horizon ends the history. Repeated frames are idempotent; a stopped and restarted capture service resumes only after closed-candle identity and source continuity are proven."),
              table(["Research capability", "What it adds", "Hard interpretation boundary"], [
                  ["Motif lattice", "Single-candle micro-events; 3-5 candle atoms; 7-12 candle compounds; full swing/rest regimes", "Bounded historical composition, not a future outcome"],
                  ["Time-to-event", "Kaplan-Meier-style next-swing, direction-change and rest-end curves, plus object-conditioned curves from matured Pair DNA", "Descriptive duration evidence, not a deadline or cause"],
                  ["Adaptive ontology", "Pair-scoped shadow features evaluated on closed-candle train/holdout evidence, with versioned promotion and rollback", "Passing means eligible for study only"],
                  ["Exact paths", "Anchor-known normalized path, MFE, MAE, efficiency, time in state and a bounded motif-linked trajectory library", "Historical reconstruction only"],
                  ["Cross-pair graph", "Shared-timestamp normalized Granger-style proxy plus mutual information", "Explicitly non-causal association; no influence or entry claim"],
                  ["Concept drift", "Bounded distribution test and deterministic regime partition identity", "Distribution change, not direction"],
                  ["Proof certificates", "Hashes claim, inputs, derivation, closed-candle IDs, coordinate and order domains", "Integrity only; not source authentication or execution authority"],
              ], [3.5*cm, 8.5*cm, 5.2*cm]),
              p("Continuous integration", "h2"),
              p("MarketStudyServiceV3 publishes bounded motif_lattice, survival_network, path_reconstruction, adaptive_feature_ontology, concept_drift, regime_partition, cross_pair_association and claim_proofs fields from restart-safe history. Pair-scoped ontology audit state persists independently; drift snapshots and append-stable partitions persist inside Pair DNA. The atomic cross-pair coordinator retains only bounded normalized returns; without a genuine synchronized peer or support it reports insufficient evidence and never creates an edge."),
              p("Continuity handling", "h2"),
              p("A history row receives a market-study snapshot only when its closed-candle key and monotonic sequence match exactly. Missed visual rollovers enter a fail-closed reacquisition path. Unknown gaps remain explicit; the newest study is never copied backward. Cross-pair evidence additionally requires exact shared contiguous closed timestamps and compatible normalized geometry. Granger-style variance reduction and mutual information always remain non-causal associations."), PageBreak()]

    section(story, "11. Operator experience, auditability and operational controls")
    story += [p("The privacy-safe PG_OPERATOR_WORKSPACE_V1 presents three plain-language answers before technical detail. It removes provider internals, filesystem paths, raw telemetry and private strategy vocabulary from the first viewport. Major/inner trend, regression, continuous history, freshness, exact-frame media, stream health and overlay geometry remain supporting evidence in progressively disclosed detail."),
              table(["Primary answer", "Public contract"], [
                  ["Market origin and history", "Explain the completed movement, major trend, swing/rest rhythm and the closest supported historical behavior."],
                  ["Studied direction then and now", "Separate the prior studied side from the current major/inner read, maturity, confirmation and invalidation."],
                  ["Best decision right now", "Publish the best current action: BUY_NOW, SELL_NOW, watch a named pullback/rally, hold/protect, avoid chasing, or wait—with the decisive reason and change condition."],
              ], [5.0*cm, 12.2*cm]),
              p("Audit and certification", "h2"),
              bullets(["Runtime artifacts: session.json, compact_live_state.json, display_state.json, events.jsonl, continuous study stores, exact frame media and overlays.",
                       "Evidence loop: allowed-entry and blocked-ENTER_NOW screenshots, future outcome scoring, progression galleries, manifests and forensic reports.",
                       "Health and topology: API health, single runtime/venv validation, model warm state, source lock, dashboard hydration, broker freshness and runtime traces.",
                       "Useful live endpoints: /v1/mobile/health; operator/state/v1/{session}; model-council execution/latest; runtime/trace/v3; and the /v3 dashboard."]),
              callout("OPERATING RULE", "An absent or invalid execution packet makes the entry answer DO_NOT_ENTER and names the failing gate. It does not erase the studied direction, historical explanation or supported setup with an unexplained WAIT.", TEAL), PageBreak()]

    section(story, "12. Implementation map and verification checklist")
    story += [p("The following files are the most relevant active surfaces for understanding or changing the system. This blueprint intentionally prioritizes current source and canonical blueprints over archived architecture snapshots."),
              table(["Area", "Primary active source / canonical reference"], [
                  ["Launch / topology", "Backend/launch/launch_phoenixguard_live_ready.ps1; start_phoenixguard_full_local.ps1; start_phoenixguard_24_7_tracker.py"],
                  ["Live tracker", "Backend/src/phoenixguard/mobile_api/window_tracker.py"],
                  ["CPU stream observer", "Backend/src/phoenixguard/vision/cpu_stream_v3.py; window_tracker.py producer/consumer integration"],
                  ["Closed-candle resolver", "phoenixguard/decision/scene_forecast_contributor_v3.py; mobile_api/window_tracker.py resolver-to-study bridge"],
                  ["Deep V3 study", "phoenixguard/study/candle_intelligence_v3.py; behavioral_sequence_v3.py; candle_ledger_v3.py; pair_dna_v3.py; historical_similarity_v3.py; object_relationship_graph_v3.py"],
                  ["Advanced V3 research", "phoenixguard/study/motif_lattice_v3.py; adaptive_feature_ontology_v3.py; concept_drift_v3.py; cross_pair_association_v3.py; cross_pair_coordinator_v3.py; study_claim_proof_v3.py"],
                  ["API / public state", "Backend/src/phoenixguard/mobile_api/app.py; live_state_v3.py"],
                  ["Inference", "Frontend/dashboard/main.py::run_inference"],
                  ["Vision / overlays", "phoenixguard/vision/*; tracking/market_object_tracker_v3.py; vision/v3_overlay_contract.py"],
                  ["Decision", "phoenixguard/decision/model_council_v3.py; book_strategy/*; scenario_decision_kernel.py; a_star_scenarios.py; skill_gates.py"],
                  ["Execution boundary", "phoenixguard/execution/packet_v3.py; v3_language.py; Backend/launch/shooter.py; phoenixguard_mt4_file_bridge.py"],
                  ["Dashboard", "Frontend/dashboard/static/window_tracker_dashboard.html"],
                  ["Canonical specs", "docs/architecture/PHOENIXGUARD_V3_MARKET_STUDY_BLUEPRINT.md; PhoenixGuard_System_Blueprint.md; docs/active_execution_paths.md; docs/execution_packet_schema_matrix.md"],
              ], [4.3*cm, 12.9*cm]),
              p("Before considering a live stack healthy", "h2"),
              bullets(["Health returns 200 and exactly one logical stack owns the configured port.",
                       "The session is running; stream/frame/capture/state identities advance; pair, timeframe, HWND and geometry generation remain consistent.",
                       "The CPU observer sustains its declared sample rate without unbounded memory, pending backlog or overlapping heavy studies.",
                       "Latest window/chart/overlay artifacts, operator state and dashboard all return successfully and agree on version identity.",
                       "Continuous study requires no manual baseline; only genuinely new identity-proven closed candles advance it.",
                       "Model health, source lock, packet freshness and package handoff status are checked separately from market analysis.",
                       "No regression read, overlay or raw signal is treated as entry permission; only a fresh validated V3 packet plus accepted allowance may cross the handoff boundary."]),
              Spacer(1, .25*cm), callout("BOTTOM LINE", "PhoenixGuard is an evidence-to-permission system, not a raw-frame-to-click system. Observe continuously, study material events, classify explicitly, state the best useful action honestly, and permit handoff only through independently validated current truth.", GOLD)]

    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    print(OUT)


if __name__ == "__main__":
    build()
