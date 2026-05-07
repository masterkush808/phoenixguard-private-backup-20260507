from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, PageBreak
from reportlab.lib.enums import TA_CENTER
from reportlab.lib import colors
from typing import Any

# Set up document
pdf_path = "docs/architecture/PhoenixGuard_Architecture.pdf"
doc = SimpleDocTemplate(pdf_path, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)

styles = getSampleStyleSheet()
# Modify the existing styles instead of redefining them
styles['Title'].fontName = 'Times-Roman'
styles['Title'].fontSize = 22
styles['Title'].alignment = TA_CENTER
styles['Title'].spaceAfter = 20
styles['Heading1'].fontName = 'Times-Roman'
styles['Heading1'].fontSize = 18
styles['Heading1'].spaceAfter = 14
styles['Heading2'].fontName = 'Times-Roman'
styles['Heading2'].fontSize = 16
styles['Heading2'].spaceAfter = 10
styles.add(ParagraphStyle(name='Normal14', fontName='Times-Roman', fontSize=14, spaceAfter=8))

content: list[Any] = []

content.append(Paragraph("PhoenixGuard: End-to-End System Blueprint", styles['Title']))
content.append(Paragraph("1. Introduction", styles['Heading1']))
content.append(Paragraph("PhoenixGuard is a hybrid chart intelligence system for financial signal review, memory-augmented reasoning, and multi-module decision support. This document provides a comprehensive index and architectural overview, explaining how each component is built, how they connect, and their purpose in the end-to-end pipeline.", styles['Normal14']))

content.append(Paragraph("2. High-Level Architecture Mind Map", styles['Heading1']))
content.append(Paragraph("See next page for diagram.", styles['Normal14']))
content.append(PageBreak())

content.append(Paragraph("PhoenixGuard Architecture Mind Map", styles['Heading2']))
content.append(Paragraph("User Input (Image/File) → Preprocess (preprocess.py) → CV Model Detection (cv_module.py) → Chart-State Extraction → Memory Retrieval & Context Injection (memory_ingest.py) → Regression Forecasting (regression_module.py) → Style Personalization (personalization.py) → RL Policy Inference (rl_module.py) → Feature Fusion (main.py) → Gate Layer (skill_gates.py) → Ensemble Decision (ensemble.py) → Final Action & Outputs → Online Adaptation Loop (feeds back to Memory Retrieval)", styles['Normal14']))
content.append(PageBreak())

content.append(Paragraph("3. System Overview Table", styles['Heading1']))
table_data = [
    ["Layer", "Main Components & Files", "Purpose"],
    ["Input", "main.py (run_inference)", "Accepts user chart/image input"],
    ["Preprocessing", "preprocess.py", "Loads and normalizes input for models"],
    ["CV Model", "cv_module.py", "Detects chart features using vision models"],
    ["Chart-State Extraction", "main.py, cv_module.py", "Extracts structured chart state from detections"],
    ["Memory Retrieval", "memory_ingest.py, main.py", "Retrieves similar past cases for context and recall"],
    ["Regression Forecasting", "regression_module.py", "Predicts future chart values/quantiles"],
    ["Personalization", "personalization.py", "Adapts style and behavior to user profile"],
    ["RL Policy Inference", "rl_module.py", "Makes policy decisions using reinforcement learning"],
    ["Feature Fusion", "main.py", "Combines all features into a single vector"],
    ["Gate Layer", "skill_gates.py", "Applies 12 formal gates for signal validation and curriculum logic"],
    ["Ensemble Decision", "ensemble.py", "Fuses all signals, applies consensus rules for final decision"],
    ["Final Action & Outputs", "main.py, ensemble.py", "Generates actionable output (BUY/SELL/HOLD), visualizations, explainers"],
    ["Online Adaptation Loop", "main.py, personalization.py", "Continuously adapts using feedback and memory updates"],
]
table = Table(table_data, colWidths=[120, 200, 270], repeatRows=1)
table.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
    ('TEXTCOLOR', (0,0), (-1,0), colors.black),
    ('ALIGN', (0,0), (-1,-1), 'LEFT'),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('FONTNAME', (0,0), (-1,0), 'Times-Bold'),  # Bold header
    ('FONTNAME', (0,1), (-1,-1), 'Times-Roman'),
    ('FONTSIZE', (0,0), (-1,-1), 14),
    ('BOTTOMPADDING', (0,0), (-1,0), 12),
    ('TOPPADDING', (0,0), (-1,-1), 8),
    ('LEFTPADDING', (0,0), (-1,-1), 8),
    ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ('BACKGROUND', (0,1), (-1,-1), colors.whitesmoke),
    ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
    ('WORDWRAP', (0,0), (-1,-1), 'CJK'),
]))
content.append(table)
content.append(PageBreak())

content.append(Paragraph("4. End-to-End Flow Explanation", styles['Heading1']))
flow_steps = [
    ("Step 1: Input", "User provides a chart image or file. Entry point: run_inference in main.py."),
    ("Step 2: Preprocessing", "Image is loaded and normalized for model compatibility (preprocess.py)."),
    ("Step 3: CV Model Detection", "Vision models (cv_module.py) detect chart features and patterns."),
    ("Step 4: Chart-State Extraction", "Detected features are structured into a chart-state payload."),
    ("Step 5: Memory Retrieval & Context Injection", "System retrieves similar past cases from the memory bank (memory_ingest.py). Few-shot context is built for reasoning."),
    ("Step 6: Regression Forecasting", "Predicts future chart values using regression models (regression_module.py)."),
    ("Step 7: Personalization", "Updates user style and adapts outputs (personalization.py)."),
    ("Step 8: RL Policy Inference", "RL engine makes policy decisions based on fused features (rl_module.py)."),
    ("Step 9: Feature Fusion", "All features are combined into a single vector (main.py)."),
    ("Step 10: Gate Layer", "12 formal gates validate and refine the signal (skill_gates.py)."),
    ("Step 11: Ensemble Decision", "All signals are fused and consensus rules applied (ensemble.py)."),
    ("Step 12: Final Action & Outputs", "System outputs BUY/SELL/HOLD, position sizing, and visual explanations."),
    ("Step 13: Online Adaptation Loop", "Feedback and memory updates continuously improve the system."),
]
for step, desc in flow_steps:
    content.append(Paragraph(f"<b>{step}</b> {desc}", styles['Normal14']))
content.append(PageBreak())

content.append(Paragraph("5. Component Purposes", styles['Heading1']))
components = [
    ("main.py", "Orchestrates the pipeline, manages input/output, and feature fusion."),
    ("preprocess.py", "Handles all input normalization and preprocessing."),
    ("cv_module.py", "Runs computer vision models for chart feature detection."),
    ("memory_ingest.py", "Manages memory bank retrieval and context injection."),
    ("regression_module.py", "Provides regression-based forecasting."),
    ("personalization.py", "Adapts system behavior to user preferences."),
    ("rl_module.py", "Implements reinforcement learning policy inference."),
    ("skill_gates.py", "Applies formal gates for signal validation."),
    ("ensemble.py", "Fuses all signals and applies consensus logic."),
]
for name, desc in components:
    content.append(Paragraph(f"<b>{name}</b>: {desc}", styles['Normal14']))
content.append(PageBreak())

content.append(Paragraph("6. Conclusion", styles['Heading1']))
content.append(Paragraph("PhoenixGuard is a modular, extensible, and adaptive system for advanced chart intelligence and decision support, with each component playing a critical role in the end-to-end workflow.", styles['Normal14']))

# Build PDF
doc.build(content)
print(f"PDF generated: {pdf_path}")
