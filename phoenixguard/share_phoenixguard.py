from __future__ import annotations
# pyright: reportPrivateUsage=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportPossiblyUnboundVariable=false, reportOperatorIssue=false, reportUnnecessaryCast=false, reportArgumentType=false

from dataclasses import dataclass, field
import base64
import hashlib
import html
import io
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Callable, Mapping, cast
from uuid import uuid4
import warnings

import gradio as gr
import numpy as np
from PIL import Image, UnidentifiedImageError

import main as pg


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


SHARE_MODE_ACTIVE = _env_bool("PHOENIXGUARD_SHARE_MODE", True)
SHARE_PUBLIC_ALIAS = "808Fx Standard System Hybrid"
SHARE_CREATOR_NAME = "Thabang Johnson Masoabi"
SHARE_CREATOR_STORY = (
    "Developed and created by Thabang Johnson Masoabi to pursue advanced market vision "
    "through the lens of Artificial Intelligence."
)
SHARE_UI_TITLE = f"{SHARE_PUBLIC_ALIAS} | Protected AI Vision Desk"
SHARE_UI_SUBTITLE = (
    "Editorial-grade protected landing surface with cinematic repo imagery, server-side inference, "
    "and disciplined access control."
)
DEFAULT_SHARE_RENDER = {
    "overlay_mode": str(pg.DEFAULT_OVERLAY_MODE),
    "vision_extras": list(pg.DEFAULT_VISION_EXTRAS),
    "council_scope": str(pg.DEFAULT_COUNCIL_SCOPE),
    "min_conf_global": 0.42,
    "min_conf_latest": 0.50,
    "history_depth": 8,
    "label_density": 10,
    "projection_focus": 0.35,
    "debug_depth": 4,
    "higher_timeframe": "M15",
    "lower_timeframe": "M5",
}
SHARE_SESSION_TTL_SEC = _env_int("PHOENIXGUARD_SHARE_SESSION_TTL_SEC", 4 * 60 * 60)
SHARE_MAX_SESSIONS = _env_int("PHOENIXGUARD_SHARE_MAX_SESSIONS", 256)
SHARE_AUTH_MAX_FAILURES = max(1, _env_int("PHOENIXGUARD_SHARE_AUTH_MAX_FAILURES", 5))
SHARE_AUTH_LOCKOUT_SEC = max(30, _env_int("PHOENIXGUARD_SHARE_AUTH_LOCKOUT_SEC", 10 * 60))
SHARE_STRICT_PASSWORDS = _env_bool("PHOENIXGUARD_SHARE_STRICT_PASSWORDS", False)
SHARE_REASON_MAX_CHARS = max(80, _env_int("PHOENIXGUARD_SHARE_REASON_MAX_CHARS", 500))
SHARE_ENABLE_FEEDBACK = _env_bool("PHOENIXGUARD_SHARE_ENABLE_FEEDBACK", True)
SHARE_ENABLE_LEARNING_MUTATIONS = _env_bool("PHOENIXGUARD_SHARE_ENABLE_LEARNING_MUTATIONS", True)
SHARE_SIDE_EFFECT_FREE = _env_bool("PHOENIXGUARD_SHARE_SIDE_EFFECT_FREE", True)
SHARE_QUEUE_MAX_SIZE = max(1, _env_int("PHOENIXGUARD_SHARE_QUEUE_MAX_SIZE", 40))
SHARE_DEFAULT_CONCURRENCY = max(1, _env_int("PHOENIXGUARD_SHARE_DEFAULT_CONCURRENCY", 2))
SHARE_HEAVY_CONCURRENCY = max(1, _env_int("PHOENIXGUARD_SHARE_HEAVY_CONCURRENCY", 2))
SHARE_FEEDBACK_CONCURRENCY = max(1, _env_int("PHOENIXGUARD_SHARE_FEEDBACK_CONCURRENCY", 1))
SHARE_SIGNAL_RATE_LIMIT = max(1, _env_int("PHOENIXGUARD_SHARE_SIGNAL_RATE_LIMIT", 6))
SHARE_SIGNAL_RATE_WINDOW_SEC = max(10, _env_int("PHOENIXGUARD_SHARE_SIGNAL_RATE_WINDOW_SEC", 60))
SHARE_PREVIEW_RATE_LIMIT = max(1, _env_int("PHOENIXGUARD_SHARE_PREVIEW_RATE_LIMIT", 45))
SHARE_PREVIEW_RATE_WINDOW_SEC = max(10, _env_int("PHOENIXGUARD_SHARE_PREVIEW_RATE_WINDOW_SEC", 60))
SHARE_MODEL_COUNCIL_RATE_LIMIT = max(1, _env_int("PHOENIXGUARD_SHARE_MODEL_COUNCIL_RATE_LIMIT", 2))
SHARE_MODEL_COUNCIL_RATE_WINDOW_SEC = max(30, _env_int("PHOENIXGUARD_SHARE_MODEL_COUNCIL_RATE_WINDOW_SEC", 5 * 60))
SHARE_FEEDBACK_RATE_LIMIT = max(1, _env_int("PHOENIXGUARD_SHARE_FEEDBACK_RATE_LIMIT", 4))
SHARE_FEEDBACK_RATE_WINDOW_SEC = max(30, _env_int("PHOENIXGUARD_SHARE_FEEDBACK_RATE_WINDOW_SEC", 5 * 60))
SHARE_MAX_UPLOAD_FILES = 4
SHARE_MAX_UPLOAD_BYTES = max(1_000_000, _env_int("PHOENIXGUARD_SHARE_MAX_UPLOAD_BYTES", 12 * 1024 * 1024))
SHARE_MAX_IMAGE_PIXELS = max(1_000_000, _env_int("PHOENIXGUARD_SHARE_MAX_IMAGE_PIXELS", 16_000_000))
SHARE_MAX_IMAGE_EDGE = max(512, _env_int("PHOENIXGUARD_SHARE_MAX_IMAGE_EDGE", 4096))
SHARE_MIN_IMAGE_EDGE = max(32, _env_int("PHOENIXGUARD_SHARE_MIN_IMAGE_EDGE", 128))
SHARE_ENABLE_MODEL_COUNCIL = _env_bool("PHOENIXGUARD_SHARE_ENABLE_MODEL_COUNCIL", True)
SHARE_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
SHARE_CONTACT_RATE_LIMIT = max(1, _env_int("PHOENIXGUARD_SHARE_CONTACT_RATE_LIMIT", 3))
SHARE_CONTACT_RATE_WINDOW_SEC = max(60, _env_int("PHOENIXGUARD_SHARE_CONTACT_RATE_WINDOW_SEC", 10 * 60))
SHARE_CONTACT_FIELD_MAX_CHARS = max(48, _env_int("PHOENIXGUARD_SHARE_CONTACT_FIELD_MAX_CHARS", 180))
SHARE_BRAND_IMAGE_LIMIT = max(1, _env_int("PHOENIXGUARD_SHARE_BRAND_IMAGE_LIMIT", 4))
SHARE_BRAND_IMAGE_EDGE = max(960, _env_int("PHOENIXGUARD_SHARE_BRAND_IMAGE_EDGE", 1600))
_SHARE_DEFAULT_BRAND_ASSET_DIR = Path(pg.RUNTIME.project_root) / "assets" / "share" / "css-control"
SHARE_BRAND_ASSET_DIR = Path(
    str(
        os.getenv(
            "PHOENIXGUARD_SHARE_BRAND_ASSET_DIR",
            _SHARE_DEFAULT_BRAND_ASSET_DIR,
        )
    )
)
SHARE_BRAND_IMAGE_PREFERRED_ORDER = (
    "landing-transition-market-vision.png",
    "landing-transition-market-vision-alt.png",
    "landing-transition-lifestyle-suite.png",
    "landing-transition-lifestyle-travel.png",
)

SHARE_LOGGER = pg.setup_logger(pg.RUNTIME.logs_dir / "phoenixguard_share.log", name="phoenixguard.share")
SHARE_AUDIT_LOG_PATH = pg.RUNTIME.logs_dir / "phoenixguard_share_audit_hash_chain.log"
SHARE_CONTACT_LOG_PATH = pg.RUNTIME.logs_dir / "phoenixguard_share_contact_hash_chain.log"
SHARE_EXTRA_CSS = """
:root {
  --pg-lux-ink: #071017;
  --pg-lux-panel: rgba(11, 16, 22, 0.74);
  --pg-lux-panel-strong: rgba(14, 16, 20, 0.88);
  --pg-lux-gold: #f5c982;
  --pg-lux-gold-strong: #ffcf82;
  --pg-lux-gold-deep: #9f6630;
  --pg-lux-cream: #f6eee1;
  --pg-lux-copy: rgba(236, 240, 244, 0.82);
  --pg-lux-copy-soft: rgba(217, 225, 232, 0.68);
  --pg-lux-display: "Palatino Linotype", "Book Antiqua", Georgia, serif;
  --pg-lux-sans: "Aptos", "Segoe UI", "Trebuchet MS", sans-serif;
}
.gradio-container {
  background:
    radial-gradient(circle at top, rgba(245, 201, 130, 0.10), transparent 24%),
    radial-gradient(circle at 12% 18%, rgba(147, 82, 39, 0.10), transparent 18%),
    linear-gradient(180deg, #071017 0%, #09131b 40%, #050b10 100%);
  color: var(--pg-lux-cream);
  font-family: var(--pg-lux-sans);
}
.pg-auth-mount {
  display: none;
}
.pg-feedback-intent-lock {
    display: none;
}
html[data-pg-feedback-intent="true"] .pg-feedback-intent-lock {
    display: block;
}
.pg-feedback-intent-note {
    color: var(--pg-lux-copy-soft);
    font-size: 12px;
    line-height: 1.5;
}
.pg-share-toast-host {
    position: fixed;
    right: 18px;
    bottom: 18px;
    z-index: 9999;
    display: grid;
    gap: 8px;
    width: min(360px, calc(100vw - 36px));
}
.pg-share-toast {
    border-radius: 14px;
    border: 1px solid rgba(255, 238, 212, 0.22);
    background: linear-gradient(140deg, rgba(8, 18, 26, 0.96), rgba(20, 22, 26, 0.95));
    color: var(--pg-lux-cream);
    padding: 10px 12px;
    box-shadow: 0 16px 36px rgba(2, 7, 14, 0.44);
}
.pg-share-toast strong {
    display: block;
    font-size: 12px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
}
.pg-share-toast span {
    display: block;
    margin-top: 4px;
    font-size: 12px;
    line-height: 1.45;
    color: var(--pg-lux-copy-soft);
}
.pg-panel,
.pg-share-note,
.pg-share-disclosure-item,
.pg-card-note,
.pg-muted,
.pg-chip,
.pg-tile-value {
    overflow-wrap: anywhere;
    word-break: break-word;
}
.pg-tile-value {
    overflow-wrap: break-word;
    word-break: normal;
    hyphens: none;
}
.pg-auth-shell {
  display: grid;
  grid-template-columns: minmax(0, 1.34fr) minmax(360px, 0.92fr);
  gap: 18px;
  align-items: stretch;
  min-height: calc(100svh - 32px);
}
.pg-auth-hero,
.pg-share-hero {
  position: relative;
  overflow: hidden;
  border-radius: 34px;
  padding: clamp(24px, 3.4vw, 40px);
  background:
    radial-gradient(circle at top right, rgba(255, 207, 130, 0.22), transparent 30%),
    radial-gradient(circle at left center, rgba(154, 92, 40, 0.18), transparent 32%),
    radial-gradient(circle at 50% 0%, rgba(116, 73, 32, 0.18), transparent 28%),
    linear-gradient(132deg, #071017 0%, #0c1821 48%, #1b130d 100%);
  border: 1px solid rgba(255, 238, 212, 0.10);
  box-shadow:
    0 36px 96px rgba(4, 10, 18, 0.44),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
}
.pg-share-hero {
  width: 100vw;
  min-height: min(96svh, 920px);
  margin-left: calc(50% - 50vw);
  margin-right: calc(50% - 50vw);
  margin-bottom: 18px;
  border-radius: 0 0 38px 38px;
  padding-top: clamp(28px, 4vw, 48px);
  padding-bottom: clamp(28px, 4vw, 42px);
}
.pg-auth-hero::before,
.pg-share-hero::before {
  content: "";
  position: absolute;
  inset: auto -12% -26% auto;
  width: 42%;
  height: 62%;
  border-radius: 999px;
  background: radial-gradient(circle, rgba(255, 214, 126, 0.24), transparent 68%);
  filter: blur(20px);
  pointer-events: none;
}
.pg-auth-hero::after,
.pg-share-hero::after {
  content: "";
  position: absolute;
  inset: 0;
  background:
    linear-gradient(115deg, rgba(255,255,255,0.04), transparent 30%),
    repeating-linear-gradient(
      135deg,
      rgba(255,255,255,0.02) 0px,
      rgba(255,255,255,0.02) 1px,
      transparent 1px,
      transparent 15px
    );
  pointer-events: none;
}
.pg-auth-copy,
.pg-auth-stage,
.pg-share-copy,
.pg-share-stage,
.pg-share-note-grid {
  position: relative;
  z-index: 2;
  transition:
    transform 0.35s ease,
    opacity 0.35s ease,
    filter 0.35s ease;
}
.pg-auth-copy,
.pg-share-copy,
.pg-share-manifesto,
.pg-share-access-rail,
.pg-share-note,
.pg-share-disclosure-item,
.pg-share-disclosure-card,
.pg-auth-panel,
.pg-auth-form-note,
.pg-auth-footnote {
  font-family: var(--pg-lux-sans);
}
.pg-auth-hero-shell,
.pg-share-hero-shell {
  position: relative;
  z-index: 2;
  display: grid;
  gap: 24px;
}
.pg-auth-hero-shell::before,
.pg-share-hero-shell::before {
  content: "808";
  position: absolute;
  inset: auto auto 4% -1.5%;
  color: rgba(245, 201, 130, 0.045);
  font-family: var(--pg-lux-display);
  font-size: clamp(8rem, 18vw, 17rem);
  line-height: 0.82;
  letter-spacing: -0.1em;
  pointer-events: none;
  z-index: 0;
}
.pg-share-hero-shell {
  width: min(100%, 1320px);
  margin: 0 auto;
}
.pg-auth-scene-plane,
.pg-share-scene-plane {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}
.pg-auth-scene-plane::after,
.pg-share-scene-plane::after {
  content: "";
  position: absolute;
  inset: 0;
  background:
    linear-gradient(180deg, rgba(5, 11, 18, 0.26) 0%, rgba(5, 11, 18, 0.22) 22%, rgba(5, 11, 18, 0.68) 100%),
    linear-gradient(92deg, rgba(5, 11, 18, 0.70) 0%, rgba(5, 11, 18, 0.22) 44%, rgba(5, 11, 18, 0.50) 100%);
}
.pg-auth-scene-plane::before,
.pg-share-scene-plane::before {
  content: "";
  position: absolute;
  inset: 0;
  background:
    repeating-linear-gradient(
      120deg,
      rgba(255, 255, 255, 0.018) 0px,
      rgba(255, 255, 255, 0.018) 1px,
      transparent 1px,
      transparent 18px
    );
  mix-blend-mode: soft-light;
}
.pg-auth-scene,
.pg-share-scene {
  position: absolute;
  inset: -8%;
  opacity: 0;
  background-position: center;
  background-size: cover;
  transform: scale(1.18);
  filter: saturate(1.08) contrast(1.08) brightness(0.50) blur(18px);
  animation: pgShareScenePlane 32s cubic-bezier(0.45, 0.05, 0.2, 1.0) infinite;
  will-change: opacity, transform, filter;
}
.pg-auth-scene:nth-child(1),
.pg-share-scene:nth-child(1) {
  animation-delay: 0s;
}
.pg-auth-scene:nth-child(2),
.pg-share-scene:nth-child(2) {
  animation-delay: 8s;
}
.pg-auth-scene:nth-child(3),
.pg-share-scene:nth-child(3) {
  animation-delay: 16s;
}
.pg-auth-scene:nth-child(4),
.pg-share-scene:nth-child(4) {
  animation-delay: 24s;
}
@keyframes pgShareScenePlane {
  0%, 18% {
    opacity: 0;
    transform: scale(1.22);
    filter: saturate(1.02) contrast(1.04) brightness(0.46) blur(22px);
  }
  22%, 44% {
    opacity: 1;
    transform: scale(1.12);
    filter: saturate(1.10) contrast(1.08) brightness(0.56) blur(14px);
  }
  50%, 100% {
    opacity: 0;
    transform: scale(1.18);
    filter: saturate(1.02) contrast(1.04) brightness(0.46) blur(20px);
  }
}
.pg-auth-kicker,
.pg-share-kicker {
  color: var(--pg-lux-gold);
  text-transform: uppercase;
  letter-spacing: 0.18em;
  font-size: 11px;
  font-weight: 700;
}
.pg-auth-copy,
.pg-share-copy {
  max-width: min(620px, 100%);
}
.pg-auth-lux-rail,
.pg-share-lux-rail {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  color: rgba(255, 238, 212, 0.74);
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.pg-auth-lux-rail::before,
.pg-share-lux-rail::before {
  content: "";
  width: 48px;
  height: 1px;
  background: linear-gradient(90deg, rgba(245, 201, 130, 0.84), rgba(245, 201, 130, 0.12));
}
.pg-auth-title,
.pg-share-title {
  margin-top: 14px;
  max-width: 14ch;
  font-size: clamp(2.6rem, 5vw, 5.25rem);
  line-height: 0.92;
  letter-spacing: -0.06em;
  font-family: var(--pg-lux-display);
  font-weight: 700;
  text-shadow: 0 10px 32px rgba(0, 0, 0, 0.28);
}
.pg-auth-title strong,
.pg-share-title strong {
  display: block;
  color: var(--pg-lux-gold-strong);
}
.pg-share-title strong {
    white-space: nowrap;
}
.pg-auth-body,
.pg-share-body {
  margin-top: 18px;
  max-width: 62ch;
  color: var(--pg-lux-copy);
  font-size: 14px;
  line-height: 1.72;
}
.pg-auth-body strong,
.pg-share-body strong {
  color: var(--pg-lux-cream);
}
.pg-auth-actions,
.pg-share-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  margin-top: 24px;
}
.pg-auth-cta,
.pg-share-actions .pg-inline-button {
  appearance: none;
  border-radius: 0;
  border: 0;
  border-bottom: 1px solid rgba(245, 201, 130, 0.18);
  padding: 0 0 9px;
  background: none;
  color: var(--pg-lux-cream);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  cursor: pointer;
  transition:
    transform 0.18s ease,
    border-color 0.18s ease,
    color 0.18s ease,
    opacity 0.18s ease;
}
.pg-auth-cta:hover,
.pg-auth-cta:focus-visible,
.pg-share-actions .pg-inline-button:hover,
.pg-share-actions .pg-inline-button:focus-visible {
  transform: translateY(-1px);
  border-color: rgba(245, 201, 130, 0.46);
  color: var(--pg-lux-gold-strong);
  outline: none;
}
.pg-auth-cta[data-tone="secondary"],
.pg-share-actions .pg-inline-button[data-tone="secondary"] {
  border-color: rgba(255, 255, 255, 0.10);
  color: var(--pg-lux-copy);
}
.pg-share-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 22px;
  padding-top: 14px;
  border-top: 1px solid rgba(255, 238, 212, 0.10);
}
.pg-auth-badge,
.pg-share-badge {
  display: inline-flex;
  align-items: center;
  padding: 0;
  background: none;
  border: 0;
  color: rgba(246, 238, 225, 0.78);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.pg-auth-badge:not(:last-child)::after,
.pg-share-badge:not(:last-child)::after {
  content: "";
  display: inline-block;
  width: 18px;
  height: 1px;
  margin-left: 16px;
  background: rgba(245, 201, 130, 0.24);
}
.pg-auth-metric-grid,
.pg-share-microgrid,
.pg-share-note-grid,
.pg-share-disclosure-grid {
  display: grid;
  gap: 12px;
}
.pg-auth-metric-grid,
.pg-share-microgrid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 24px;
  padding-top: 14px;
  border-top: 1px solid rgba(255, 238, 212, 0.10);
}
.pg-auth-metric,
.pg-share-microcard,
.pg-share-note,
.pg-share-disclosure-card {
  position: relative;
  overflow: hidden;
  border: 0;
  border-radius: 0;
  padding: 0;
  background: none;
  backdrop-filter: none;
  box-shadow: none;
}
.pg-auth-metric::before,
.pg-share-microcard::before,
.pg-share-note::before,
.pg-share-disclosure-card::before {
  display: none;
}
.pg-auth-metric span,
.pg-share-microcard span,
.pg-share-note span,
.pg-share-disclosure-card span {
  display: block;
  color: rgba(217, 225, 232, 0.60);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.pg-auth-metric strong,
.pg-share-microcard strong,
.pg-share-note strong,
.pg-share-disclosure-card strong {
  display: block;
  margin-top: 8px;
  font-size: 14px;
  line-height: 1.55;
  color: var(--pg-lux-cream);
}
.pg-auth-metric:not(:last-child),
.pg-share-microcard:not(:last-child),
.pg-share-note:not(:last-child) {
  padding-right: 18px;
  border-right: 1px solid rgba(255, 238, 212, 0.08);
}
.pg-auth-stage,
.pg-share-stage {
  margin-top: 24px;
  min-height: 340px;
  border-radius: 28px;
  overflow: hidden;
  isolation: isolate;
  border: 1px solid rgba(255, 255, 255, 0.11);
  background:
    linear-gradient(165deg, rgba(7, 16, 24, 0.96), rgba(13, 24, 35, 0.86)),
    radial-gradient(circle at top right, rgba(159, 242, 223, 0.12), transparent 34%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06);
}
.pg-share-stage {
  min-height: 320px;
}
.pg-share-hero-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(360px, 0.92fr);
  gap: 24px;
  align-items: end;
  min-height: min(70svh, 720px);
}
.pg-auth-stage::before,
.pg-share-stage::before {
  content: "";
  position: absolute;
  inset: -12% 34% auto -16%;
  height: 48%;
  border-radius: 999px;
  background: radial-gradient(circle, rgba(255, 208, 122, 0.22), transparent 68%);
  filter: blur(32px);
  opacity: 0.82;
  pointer-events: none;
  animation: pgShareAmbientGlow 16s ease-in-out infinite alternate;
}
.pg-auth-stage::after,
.pg-share-stage::after {
  content: "";
  position: absolute;
  inset: 0;
  background:
    linear-gradient(180deg, rgba(5, 11, 18, 0.08) 0%, rgba(5, 11, 18, 0.22) 38%, rgba(5, 11, 18, 0.80) 100%),
    linear-gradient(90deg, rgba(5, 11, 18, 0.62) 0%, rgba(5, 11, 18, 0.18) 44%, rgba(5, 11, 18, 0.54) 100%),
    radial-gradient(circle at 22% 22%, rgba(255, 255, 255, 0.10), transparent 28%);
  backdrop-filter: blur(4px);
  pointer-events: none;
}
.pg-auth-stage,
.pg-share-stage {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.06),
    0 26px 60px rgba(0, 0, 0, 0.24);
}
.pg-auth-slides,
.pg-share-slides {
  position: absolute;
  inset: -6%;
  transform: scale(1.02);
}
.pg-auth-slide,
.pg-share-slide {
  position: absolute;
  inset: 0;
  opacity: 0;
  background-position: center;
  background-size: cover;
  transform: scale(1.14);
  transform-origin: center;
  filter: saturate(1.04) contrast(1.06) brightness(0.82) blur(7px);
  animation: pgShareSlide 32s cubic-bezier(0.45, 0.05, 0.2, 1.0) infinite;
  will-change: opacity, transform, filter;
}
.pg-auth-slide::after,
.pg-share-slide::after {
  content: "";
  position: absolute;
  inset: 0;
  background:
    linear-gradient(180deg, rgba(6, 12, 18, 0.10), rgba(6, 12, 18, 0.48) 42%, rgba(6, 12, 18, 0.82)),
    linear-gradient(90deg, rgba(6, 12, 18, 0.56) 0%, rgba(6, 12, 18, 0.08) 44%, rgba(6, 12, 18, 0.42) 100%),
    radial-gradient(circle at center, rgba(255, 255, 255, 0.12), transparent 42%);
  backdrop-filter: blur(3px);
}
.pg-auth-slide:nth-child(1),
.pg-share-slide:nth-child(1) {
  animation-delay: 0s;
}
.pg-auth-slide:nth-child(2),
.pg-share-slide:nth-child(2) {
  animation-delay: 8s;
}
.pg-auth-slide:nth-child(3),
.pg-share-slide:nth-child(3) {
  animation-delay: 16s;
}
.pg-auth-slide:nth-child(4),
.pg-share-slide:nth-child(4) {
  animation-delay: 24s;
}
@keyframes pgShareSlide {
  0%, 18% {
    opacity: 0;
    transform: scale(1.16);
    filter: saturate(1.00) contrast(1.04) brightness(0.76) blur(9px);
  }
  22%, 44% {
    opacity: 1;
    transform: scale(1.08);
    filter: saturate(1.06) contrast(1.08) brightness(0.84) blur(4px);
  }
  50%, 100% {
    opacity: 0;
    transform: scale(1.14);
    filter: saturate(1.02) contrast(1.04) brightness(0.76) blur(8px);
  }
}
.pg-scene-manual .pg-auth-scene,
.pg-scene-manual .pg-share-scene,
.pg-scene-manual .pg-auth-slide,
.pg-scene-manual .pg-share-slide,
.pg-scene-manual .pg-auth-stage-caption,
.pg-scene-manual .pg-share-stage-caption {
  animation: none !important;
  opacity: 0;
}
.pg-scene-manual .pg-auth-scene.is-active,
.pg-scene-manual .pg-share-scene.is-active {
  opacity: 1;
  transform: scale(1.11);
  filter: saturate(1.12) contrast(1.10) brightness(0.58) blur(14px);
}
.pg-scene-manual .pg-auth-slide.is-active,
.pg-scene-manual .pg-share-slide.is-active {
  opacity: 1;
  transform: scale(1.06);
  filter: saturate(1.08) contrast(1.08) brightness(0.84) blur(4px);
}
.pg-scene-manual .pg-auth-stage-caption.is-active,
.pg-scene-manual .pg-share-stage-caption.is-active {
  opacity: 1;
  transform: translateY(0);
  filter: blur(0);
}
.pg-auth-stage-copy,
.pg-share-stage-copy {
  position: relative;
  z-index: 1;
  display: grid;
  align-content: end;
  align-items: start;
  gap: 16px;
  min-height: inherit;
  padding: 28px;
}
.pg-auth-stage-copy-track,
.pg-share-stage-copy-track {
  position: relative;
  width: min(100%, 440px);
  min-height: 186px;
}
.pg-auth-stage-caption,
.pg-share-stage-caption {
  position: absolute;
  inset: auto 0 0 0;
  opacity: 0;
  transform: translateY(18px);
  filter: blur(10px);
  animation: pgShareCaption 32s cubic-bezier(0.45, 0.05, 0.2, 1.0) infinite;
}
.pg-auth-stage-caption:nth-child(1),
.pg-share-stage-caption:nth-child(1) {
  animation-delay: 0s;
}
.pg-auth-stage-caption:nth-child(2),
.pg-share-stage-caption:nth-child(2) {
  animation-delay: 8s;
}
.pg-auth-stage-caption:nth-child(3),
.pg-share-stage-caption:nth-child(3) {
  animation-delay: 16s;
}
.pg-auth-stage-caption:nth-child(4),
.pg-share-stage-caption:nth-child(4) {
  animation-delay: 24s;
}
@keyframes pgShareCaption {
  0%, 18% {
    opacity: 0;
    transform: translateY(22px);
    filter: blur(10px);
  }
  22%, 44% {
    opacity: 1;
    transform: translateY(0);
    filter: blur(0);
  }
  50%, 100% {
    opacity: 0;
    transform: translateY(-8px);
    filter: blur(8px);
  }
}
.pg-auth-stage-caption span,
.pg-share-stage-caption span {
  color: rgba(255, 229, 187, 0.86);
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  font-weight: 700;
}
.pg-auth-stage-caption strong,
.pg-share-stage-caption strong {
  display: block;
  margin-top: 8px;
  max-width: 18ch;
  font-size: clamp(1.8rem, 3vw, 2.6rem);
  line-height: 0.96;
  letter-spacing: -0.05em;
  font-family: var(--pg-lux-display);
  font-weight: 700;
  color: var(--pg-lux-cream);
}
.pg-auth-stage-caption p,
.pg-share-stage-caption p {
  margin-top: 10px;
  max-width: 34ch;
  color: rgba(236, 240, 244, 0.72);
  font-size: 13px;
  line-height: 1.6;
}
.pg-stage-progress {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  width: min(100%, 248px);
}
.pg-stage-progress span {
  position: relative;
  display: block;
  height: 3px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.14);
}
.pg-stage-progress span::after {
  content: "";
  position: absolute;
  inset: 0;
  transform: scaleX(0);
  transform-origin: left center;
  background: linear-gradient(90deg, rgba(245, 201, 130, 0.96), rgba(159, 242, 223, 0.92));
  animation: pgShareProgress 32s linear infinite;
}
.pg-stage-progress span:nth-child(1)::after {
  animation-delay: 0s;
}
.pg-stage-progress span:nth-child(2)::after {
  animation-delay: 8s;
}
.pg-stage-progress span:nth-child(3)::after {
  animation-delay: 16s;
}
.pg-stage-progress span:nth-child(4)::after {
  animation-delay: 24s;
}
.pg-scene-manual .pg-stage-progress span::after {
  animation: none !important;
  opacity: 0;
  transform: scaleX(0);
}
.pg-scene-manual .pg-stage-progress span.is-active::after {
  opacity: 1;
  transform: scaleX(1);
}
@keyframes pgShareProgress {
  0%, 18% {
    transform: scaleX(0);
    opacity: 0.72;
  }
  22%, 44% {
    transform: scaleX(1);
    opacity: 1;
  }
  50%, 100% {
    transform: scaleX(1);
    opacity: 0;
  }
}
@keyframes pgShareAmbientGlow {
  0% {
    transform: translate3d(-4%, 0, 0) scale(0.96);
    opacity: 0.46;
  }
  100% {
    transform: translate3d(6%, -8%, 0) scale(1.08);
    opacity: 0.88;
  }
}
.pg-auth-panel {
  position: relative;
  border: 1px solid rgba(255, 238, 212, 0.10);
  border-radius: 30px;
  padding: 18px;
  background:
    linear-gradient(180deg, rgba(12, 15, 19, 0.98), rgba(10, 12, 15, 0.94)),
    radial-gradient(circle at top right, rgba(245, 201, 130, 0.10), transparent 28%);
  box-shadow:
    0 32px 84px rgba(4, 10, 18, 0.34),
    inset 0 1px 0 rgba(255, 255, 255, 0.04);
  backdrop-filter: blur(20px);
  transition:
    transform 0.32s ease,
    box-shadow 0.32s ease,
    border-color 0.32s ease,
    background 0.32s ease;
}
.pg-auth-panel::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.05), transparent 26%);
  pointer-events: none;
}
.pg-auth-panel > div {
  width: 100%;
}
.pg-auth-form-shell {
  width: min(100%, 470px);
  margin: 0 auto;
}
.pg-auth-panel h2 {
  margin-bottom: 10px;
  color: var(--pg-lux-cream);
  font-size: 28px;
  line-height: 1;
  letter-spacing: -0.04em;
  font-family: var(--pg-lux-display);
  font-weight: 700;
}
.pg-auth-panel .auth {
  display: none;
}
.pg-auth-form-note,
.pg-auth-footnote {
  color: var(--pg-lux-copy-soft);
  font-size: 12px;
  line-height: 1.65;
}
.pg-auth-form-note strong {
  color: var(--pg-lux-cream);
}
.pg-auth-panel form {
  margin-top: 16px;
}
.pg-auth-panel input {
  border-radius: 16px !important;
  min-height: 48px !important;
  background: rgba(255, 255, 255, 0.04) !important;
  border: 1px solid rgba(255, 238, 212, 0.12) !important;
  color: var(--pg-lux-cream) !important;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
}
.pg-auth-panel input::placeholder {
  color: rgba(236, 240, 244, 0.34) !important;
}
.pg-auth-panel button {
  width: 100%;
  margin-top: 14px;
  border-radius: 16px !important;
  min-height: 50px !important;
  font-weight: 700 !important;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #1a1208 !important;
  background: linear-gradient(135deg, #f5c982, #b67636) !important;
  border: 1px solid rgba(255, 232, 190, 0.32) !important;
  box-shadow: 0 18px 42px rgba(18, 10, 4, 0.32);
}
.pg-auth-panel .creds {
  margin-top: 16px;
  margin-bottom: 10px;
  color: #ff9d90;
  font-weight: 700;
}
.pg-auth-footnote {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.10);
}
.pg-auth-shell[data-auth-focus="true"] .pg-auth-panel {
  transform: translateY(-4px) scale(1.01);
  border-color: rgba(245, 201, 130, 0.30);
  box-shadow: 0 34px 84px rgba(4, 10, 18, 0.42);
  background:
    linear-gradient(180deg, rgba(8, 15, 22, 0.98), rgba(10, 18, 27, 0.96)),
    radial-gradient(circle at top right, rgba(245, 201, 130, 0.12), transparent 30%);
}
.pg-auth-shell[data-auth-focus="true"] .pg-auth-scene,
.pg-auth-shell[data-auth-focus="true"] .pg-auth-slide {
  filter: saturate(0.96) contrast(1.04) brightness(0.44) blur(20px) !important;
}
.pg-auth-shell[data-auth-focus="true"] .pg-auth-copy,
.pg-auth-shell[data-auth-focus="true"] .pg-auth-stage-copy {
  opacity: 0.92;
  transform: translateY(4px);
}
.pg-share-title {
    max-width: none;
}
.pg-share-microgrid {
  margin-top: 18px;
}
.pg-share-note-grid,
.pg-share-disclosure-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 18px;
}
.pg-share-note-grid {
  width: min(100%, 1320px);
  margin-left: auto;
  margin-right: auto;
  gap: 22px;
  padding-top: 20px;
  border-top: 1px solid rgba(255, 238, 212, 0.10);
}
.pg-share-creator {
  margin-top: 18px;
  color: var(--pg-lux-copy-soft);
  font-size: 12px;
  line-height: 1.7;
  max-width: 58ch;
}
.pg-share-stack {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.pg-share-briefing {
  display: grid;
  gap: 22px;
  padding-top: 12px;
}
.pg-share-editorial-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.18fr) minmax(290px, 0.82fr);
  gap: 24px;
  align-items: start;
}
.pg-share-manifesto {
  padding-top: 4px;
}
.pg-share-editorial-kicker {
  color: #f5c982;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.pg-share-manifesto h3 {
  margin-top: 12px;
  max-width: 13ch;
  font-size: clamp(2.2rem, 4vw, 4.1rem);
  line-height: 0.95;
  letter-spacing: -0.06em;
  font-family: var(--pg-lux-display);
  font-weight: 700;
}
.pg-share-manifesto p {
  margin-top: 16px;
  max-width: 62ch;
  color: var(--pg-lux-copy);
  font-size: 14px;
  line-height: 1.72;
}
.pg-share-manifesto .pg-inline-actions {
  margin-top: 20px;
}
.pg-share-access-rail {
  display: grid;
  gap: 16px;
  padding-left: 18px;
  border-left: 1px solid rgba(255, 255, 255, 0.12);
}
.pg-share-access-step {
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.pg-share-access-step:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}
.pg-share-access-step span {
  color: rgba(245, 201, 130, 0.88);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.pg-share-access-step strong {
  display: block;
  margin-top: 8px;
  font-size: 16px;
  line-height: 1.4;
}
.pg-share-access-step p {
  margin-top: 8px;
  color: var(--pg-lux-copy-soft);
  font-size: 13px;
  line-height: 1.65;
}
.pg-share-disclosure-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 20px;
  padding-top: 20px;
  border-top: 1px solid rgba(255, 255, 255, 0.12);
}
.pg-share-disclosure-item span {
  display: block;
  color: rgba(217, 225, 232, 0.60);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.pg-share-disclosure-item strong {
  display: block;
  margin-top: 8px;
  font-size: 15px;
  line-height: 1.5;
  color: var(--pg-lux-cream);
}
.pg-share-contact-panel .gr-button {
  border-radius: 16px !important;
  min-height: 44px;
  font-weight: 700 !important;
  background: linear-gradient(135deg, #41311c, #7a5631) !important;
  border: 1px solid rgba(215, 166, 90, 0.36) !important;
}
.pg-share-contact-status textarea {
  font-size: 12px !important;
  line-height: 1.5 !important;
}
:root {
  --pg-lux-ink: #050a10;
  --pg-lux-panel: rgba(9, 13, 19, 0.72);
  --pg-lux-panel-strong: rgba(10, 14, 20, 0.90);
  --pg-lux-gold: #f3c57d;
  --pg-lux-gold-strong: #ffd69d;
  --pg-lux-gold-deep: #9a6330;
  --pg-lux-cream: #f7efe1;
  --pg-lux-copy: rgba(239, 242, 246, 0.84);
  --pg-lux-copy-soft: rgba(219, 226, 233, 0.70);
  --pg-lux-display: "Baskerville Old Face", "Palatino Linotype", "Book Antiqua", Georgia, serif;
  --pg-lux-sans: "Aptos", "Trebuchet MS", "Segoe UI", sans-serif;
  --pg-shell-width: 1440px;
}
.gradio-container {
  background:
    radial-gradient(circle at top, rgba(243, 197, 125, 0.16), transparent 18%),
    radial-gradient(circle at 10% 18%, rgba(88, 143, 255, 0.10), transparent 16%),
    radial-gradient(circle at 88% 10%, rgba(255, 154, 97, 0.12), transparent 20%),
    linear-gradient(180deg, #05090d 0%, #08111a 42%, #04080d 100%);
}
.pg-auth-shell {
  grid-template-columns: minmax(0, 1.56fr) minmax(380px, 0.78fr);
  gap: clamp(18px, 2vw, 30px);
  min-height: calc(100svh - 24px);
}
.pg-auth-hero,
.pg-share-hero {
  border-radius: 38px;
  padding: clamp(26px, 3.2vw, 48px);
  background:
    radial-gradient(circle at top right, rgba(255, 210, 124, 0.14), transparent 26%),
    radial-gradient(circle at left center, rgba(76, 133, 255, 0.10), transparent 26%),
    linear-gradient(142deg, rgba(6, 11, 16, 0.94), rgba(11, 18, 26, 0.90) 48%, rgba(28, 17, 10, 0.90) 100%);
  border: 1px solid rgba(255, 241, 215, 0.12);
  box-shadow:
    0 44px 120px rgba(2, 6, 12, 0.42),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
}
.pg-share-hero {
  min-height: min(100svh, 980px);
  border-radius: 0 0 48px 48px;
  padding-top: clamp(34px, 4.4vw, 62px);
  padding-bottom: clamp(32px, 4vw, 54px);
}
.pg-auth-hero::before,
.pg-share-hero::before {
  inset: auto -6% -18% auto;
  width: 48%;
  height: 64%;
  background: radial-gradient(circle, rgba(255, 205, 122, 0.24), transparent 70%);
  filter: blur(32px);
  opacity: 0.92;
}
.pg-auth-hero::after,
.pg-share-hero::after {
  background:
    linear-gradient(118deg, rgba(255, 255, 255, 0.05), transparent 28%),
    linear-gradient(180deg, rgba(6, 10, 15, 0.00) 0%, rgba(6, 10, 15, 0.18) 36%, rgba(6, 10, 15, 0.42) 100%),
    repeating-linear-gradient(
      135deg,
      rgba(255, 255, 255, 0.018) 0px,
      rgba(255, 255, 255, 0.018) 1px,
      transparent 1px,
      transparent 18px
    );
}
.pg-auth-hero-shell,
.pg-share-hero-shell {
  width: min(100%, var(--pg-shell-width));
  gap: clamp(24px, 3vw, 36px);
}
.pg-auth-hero-shell {
  min-height: 100%;
  grid-template-rows: auto 1fr;
  align-content: stretch;
}
.pg-auth-hero-shell::before,
.pg-share-hero-shell::before {
  inset: auto auto -1% -1%;
  color: rgba(245, 201, 130, 0.032);
  font-size: clamp(12rem, 22vw, 22rem);
}
.pg-auth-copy,
.pg-share-copy {
  position: relative;
  display: grid;
  align-content: end;
  gap: 0;
  max-width: none;
  min-width: 0;
  padding: clamp(20px, 2.6vw, 32px) clamp(20px, 2.4vw, 30px) clamp(18px, 2.2vw, 28px);
}
.pg-auth-copy::before,
.pg-share-copy::before {
  content: "";
  position: absolute;
  inset: -4% 10% -7% -4%;
  border-radius: 40px;
  background:
    linear-gradient(140deg, rgba(7, 12, 18, 0.72), rgba(7, 12, 18, 0.40) 46%, rgba(7, 12, 18, 0.00) 100%);
  border: 1px solid rgba(255, 241, 215, 0.08);
  backdrop-filter: blur(14px);
  pointer-events: none;
}
.pg-auth-copy > *,
.pg-share-copy > * {
  position: relative;
  z-index: 1;
}
.pg-auth-kicker,
.pg-share-kicker {
  margin-top: 14px;
  color: rgba(255, 216, 157, 0.90);
  letter-spacing: 0.22em;
}
.pg-auth-lux-rail,
.pg-share-lux-rail {
  color: rgba(255, 241, 215, 0.78);
  letter-spacing: 0.18em;
}
.pg-auth-lux-rail::before,
.pg-share-lux-rail::before {
  width: 58px;
  background: linear-gradient(90deg, rgba(243, 197, 125, 0.92), rgba(243, 197, 125, 0.12));
}
.pg-auth-title,
.pg-share-title {
  margin-top: 18px;
  max-width: 11.4ch;
  font-size: clamp(3.2rem, 6.4vw, 6.9rem);
  line-height: 0.88;
  letter-spacing: -0.07em;
  text-wrap: balance;
  text-shadow: 0 16px 46px rgba(0, 0, 0, 0.26);
}
.pg-auth-title strong,
.pg-share-title strong {
  margin-top: 6px;
  max-width: 10.8ch;
  color: var(--pg-lux-gold-strong);
  white-space: normal;
  text-wrap: balance;
}
.pg-auth-body,
.pg-share-body {
  margin-top: 20px;
  max-width: 44ch;
  color: var(--pg-lux-copy);
  font-size: 15px;
  line-height: 1.76;
}
.pg-auth-actions,
.pg-share-actions {
  gap: 20px;
  margin-top: 28px;
}
.pg-auth-cta,
.pg-share-actions .pg-inline-button {
  padding-bottom: 10px;
  letter-spacing: 0.10em;
}
.pg-auth-cta:hover,
.pg-auth-cta:focus-visible,
.pg-share-actions .pg-inline-button:hover,
.pg-share-actions .pg-inline-button:focus-visible {
  transform: translateY(-2px);
}
.pg-share-badges {
  gap: 12px 18px;
  margin-top: 26px;
  padding-top: 18px;
}
.pg-auth-badge,
.pg-share-badge {
  position: relative;
  padding-left: 14px;
  color: rgba(246, 238, 225, 0.82);
  letter-spacing: 0.12em;
}
.pg-auth-badge::before,
.pg-share-badge::before {
  content: "";
  position: absolute;
  left: 0;
  top: 50%;
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: linear-gradient(135deg, rgba(243, 197, 125, 1), rgba(109, 216, 255, 0.92));
  box-shadow: 0 0 16px rgba(243, 197, 125, 0.42);
  transform: translateY(-50%);
}
.pg-auth-badge:not(:last-child)::after,
.pg-share-badge:not(:last-child)::after {
  display: none;
}
.pg-auth-metric-grid,
.pg-share-microgrid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
  margin-top: 28px;
  padding-top: 18px;
}
.pg-auth-metric,
.pg-share-microcard {
  min-width: 0;
  padding-top: 16px;
  padding-right: 0;
  border-top: 1px solid rgba(255, 241, 215, 0.12);
  border-right: 0;
}
.pg-auth-metric strong,
.pg-share-microcard strong {
  margin-top: 10px;
  font-size: 15px;
  line-height: 1.62;
}
.pg-share-creator {
  margin-top: 24px;
  max-width: 46ch;
  padding-left: 18px;
  border-left: 1px solid rgba(255, 241, 215, 0.12);
  font-size: 12px;
  line-height: 1.9;
}
.pg-share-hero-grid {
  grid-template-columns: minmax(0, 1.12fr) minmax(360px, 0.88fr);
  gap: clamp(24px, 3vw, 42px);
  align-items: stretch;
  min-height: min(78svh, 820px);
}
.pg-auth-stage,
.pg-share-stage {
  margin-top: 0;
  min-height: 380px;
  border-radius: 34px;
  border: 1px solid rgba(255, 241, 215, 0.12);
  background:
    linear-gradient(165deg, rgba(7, 13, 19, 0.96), rgba(12, 21, 30, 0.88)),
    radial-gradient(circle at 86% 12%, rgba(255, 205, 122, 0.16), transparent 28%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.06),
    0 34px 84px rgba(0, 0, 0, 0.28);
}
.pg-share-stage {
  min-height: min(72svh, 720px);
}
.pg-auth-stage::before,
.pg-share-stage::before {
  inset: -8% 34% auto -12%;
  height: 46%;
  background: radial-gradient(circle, rgba(255, 206, 122, 0.24), transparent 70%);
  filter: blur(36px);
  animation: pgShareAmbientGlow 18s ease-in-out infinite alternate;
}
.pg-auth-stage::after,
.pg-share-stage::after {
  background:
    linear-gradient(180deg, rgba(6, 11, 16, 0.06) 0%, rgba(6, 11, 16, 0.22) 34%, rgba(6, 11, 16, 0.82) 100%),
    linear-gradient(90deg, rgba(6, 11, 16, 0.60) 0%, rgba(6, 11, 16, 0.10) 42%, rgba(6, 11, 16, 0.48) 100%),
    radial-gradient(circle at 24% 18%, rgba(255, 255, 255, 0.12), transparent 24%);
  backdrop-filter: blur(3px);
}
.pg-auth-slides,
.pg-share-slides {
  inset: -4%;
  transform: scale(1.01);
}
.pg-auth-scene,
.pg-share-scene {
  inset: -6%;
  transform: scale(1.18);
  filter: saturate(1.04) contrast(1.08) brightness(0.48) blur(22px);
  animation: pgShareScenePlane 36s cubic-bezier(0.45, 0.05, 0.2, 1.0) infinite;
}
.pg-auth-slide,
.pg-share-slide {
  transform: scale(1.12);
  filter: saturate(1.06) contrast(1.08) brightness(0.80) blur(5px);
  animation: pgShareSlide 36s cubic-bezier(0.45, 0.05, 0.2, 1.0) infinite;
}
.pg-auth-slide::after,
.pg-share-slide::after {
  background:
    linear-gradient(180deg, rgba(6, 12, 18, 0.08), rgba(6, 12, 18, 0.40) 42%, rgba(6, 12, 18, 0.78)),
    linear-gradient(90deg, rgba(6, 12, 18, 0.52) 0%, rgba(6, 12, 18, 0.06) 42%, rgba(6, 12, 18, 0.42) 100%),
    radial-gradient(circle at center, rgba(255, 255, 255, 0.14), transparent 42%);
}
.pg-auth-scene:nth-child(2),
.pg-share-scene:nth-child(2),
.pg-auth-slide:nth-child(2),
.pg-share-slide:nth-child(2),
.pg-auth-stage-caption:nth-child(2),
.pg-share-stage-caption:nth-child(2),
.pg-stage-progress span:nth-child(2)::after {
  animation-delay: 9s;
}
.pg-auth-scene:nth-child(3),
.pg-share-scene:nth-child(3),
.pg-auth-slide:nth-child(3),
.pg-share-slide:nth-child(3),
.pg-auth-stage-caption:nth-child(3),
.pg-share-stage-caption:nth-child(3),
.pg-stage-progress span:nth-child(3)::after {
  animation-delay: 18s;
}
.pg-auth-scene:nth-child(4),
.pg-share-scene:nth-child(4),
.pg-auth-slide:nth-child(4),
.pg-share-slide:nth-child(4),
.pg-auth-stage-caption:nth-child(4),
.pg-share-stage-caption:nth-child(4),
.pg-stage-progress span:nth-child(4)::after {
  animation-delay: 27s;
}
.pg-scene-manual .pg-auth-scene.is-active,
.pg-scene-manual .pg-share-scene.is-active {
  opacity: 1;
  transform: scale(1.11);
  filter: saturate(1.10) contrast(1.10) brightness(0.56) blur(16px);
}
.pg-scene-manual .pg-auth-slide.is-active,
.pg-scene-manual .pg-share-slide.is-active {
  opacity: 1;
  transform: scale(1.05);
  filter: saturate(1.08) contrast(1.08) brightness(0.86) blur(3px);
}
.pg-auth-stage-copy,
.pg-share-stage-copy {
  gap: 20px;
  padding: clamp(26px, 2.8vw, 38px);
}
.pg-auth-stage-copy-track,
.pg-share-stage-copy-track {
  width: min(100%, 520px);
  min-height: 220px;
}
.pg-auth-stage-caption,
.pg-share-stage-caption {
  animation: pgShareCaption 36s cubic-bezier(0.45, 0.05, 0.2, 1.0) infinite;
}
.pg-auth-stage-caption strong,
.pg-share-stage-caption strong {
  max-width: 9.8ch;
  font-size: clamp(2rem, 3vw, 2.7rem);
  line-height: 0.90;
  text-wrap: balance;
}
.pg-auth-stage-caption p,
.pg-share-stage-caption p {
  margin-top: 12px;
  max-width: 30ch;
  font-size: 14px;
  line-height: 1.68;
}
.pg-stage-progress {
  gap: 10px;
  width: min(100%, 340px);
}
.pg-stage-progress span {
  height: 4px;
  background: rgba(255, 255, 255, 0.12);
}
.pg-stage-progress span::after {
  background: linear-gradient(90deg, rgba(243, 197, 125, 0.98), rgba(255, 154, 97, 0.92) 54%, rgba(109, 216, 255, 0.92));
  animation: pgShareProgress 36s linear infinite;
}
.pg-auth-shell[data-auth-focus="true"] .pg-auth-panel {
  transform: translateY(-6px) scale(1.01);
  border-color: rgba(243, 197, 125, 0.34);
  box-shadow: 0 38px 90px rgba(4, 10, 18, 0.44);
}
.pg-auth-shell[data-auth-focus="true"] .pg-auth-scene,
.pg-auth-shell[data-auth-focus="true"] .pg-auth-slide {
  filter: saturate(0.96) contrast(1.04) brightness(0.42) blur(18px) !important;
}
.pg-share-note-grid {
  width: min(100%, var(--pg-shell-width));
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
  margin-top: 0;
  padding-top: 24px;
}
.pg-share-note {
  min-width: 0;
  padding-top: 18px;
  padding-right: 0;
  border-top: 1px solid rgba(255, 241, 215, 0.12);
  border-right: 0;
}
.pg-share-note strong {
  margin-top: 10px;
  font-size: 14px;
  line-height: 1.74;
}
.pg-share-briefing,
.pg-share-contact-panel {
  width: min(100%, 1340px);
  margin-left: auto;
  margin-right: auto;
}
.pg-share-briefing {
  gap: 28px;
  padding-top: 24px;
}
.pg-share-editorial-grid {
  gap: 40px;
  grid-template-columns: minmax(0, 1.08fr) minmax(340px, 0.92fr);
}
.pg-share-manifesto h3 {
  max-width: 11ch;
  font-size: clamp(2.7rem, 5.1vw, 5.2rem);
  line-height: 0.90;
  text-wrap: balance;
}
.pg-share-manifesto p {
  margin-top: 18px;
  max-width: 64ch;
  font-size: 15px;
  line-height: 1.84;
}
.pg-share-access-rail {
  gap: 20px;
  padding-left: 28px;
  border-left-color: rgba(255, 241, 215, 0.12);
}
.pg-share-access-step {
  padding-bottom: 18px;
}
.pg-share-disclosure-strip {
  gap: 24px;
  padding-top: 24px;
}
.pg-share-disclosure-item {
  padding-top: 18px;
  border-top: 1px solid rgba(255, 241, 215, 0.12);
}
.pg-share-contact-panel {
  border: 1px solid rgba(255, 241, 215, 0.12);
  border-radius: 30px;
  background:
    linear-gradient(180deg, rgba(10, 14, 20, 0.92), rgba(8, 12, 18, 0.90)),
    radial-gradient(circle at top right, rgba(243, 197, 125, 0.10), transparent 28%);
  box-shadow:
    0 32px 84px rgba(2, 6, 12, 0.28),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
}
.pg-share-contact-panel .gr-button {
  min-height: 46px;
  background: linear-gradient(135deg, #5a3f22, #8f6135) !important;
}
@keyframes pgShareScenePlane {
  0%, 16% {
    opacity: 0;
    transform: scale(1.22);
    filter: saturate(1.00) contrast(1.04) brightness(0.44) blur(24px);
  }
  21%, 43% {
    opacity: 1;
    transform: scale(1.12);
    filter: saturate(1.08) contrast(1.10) brightness(0.56) blur(16px);
  }
  48%, 100% {
    opacity: 0;
    transform: scale(1.18);
    filter: saturate(1.00) contrast(1.04) brightness(0.44) blur(22px);
  }
}
@keyframes pgShareSlide {
  0%, 16% {
    opacity: 0;
    transform: scale(1.16);
    filter: saturate(1.02) contrast(1.04) brightness(0.74) blur(7px);
  }
  21%, 43% {
    opacity: 1;
    transform: scale(1.06);
    filter: saturate(1.08) contrast(1.08) brightness(0.86) blur(3px);
  }
  48%, 100% {
    opacity: 0;
    transform: scale(1.12);
    filter: saturate(1.02) contrast(1.04) brightness(0.74) blur(6px);
  }
}
@keyframes pgShareCaption {
  0%, 16% {
    opacity: 0;
    transform: translateY(22px);
    filter: blur(10px);
  }
  21%, 43% {
    opacity: 1;
    transform: translateY(0);
    filter: blur(0);
  }
  48%, 100% {
    opacity: 0;
    transform: translateY(-8px);
    filter: blur(8px);
  }
}
@keyframes pgShareProgress {
  0%, 16% {
    transform: scaleX(0);
    opacity: 0.70;
  }
  21%, 43% {
    transform: scaleX(1);
    opacity: 1;
  }
  48%, 100% {
    transform: scaleX(1);
    opacity: 0;
  }
}
@keyframes pgShareAmbientGlow {
  0% {
    transform: translate3d(-4%, 0, 0) scale(0.96);
    opacity: 0.42;
  }
  100% {
    transform: translate3d(7%, -8%, 0) scale(1.08);
    opacity: 0.88;
  }
}
@media (max-width: 1180px) {
  .pg-auth-shell,
  .pg-share-hero-grid,
  .pg-share-editorial-grid {
    grid-template-columns: 1fr;
  }
  .pg-share-hero-grid {
    min-height: auto;
  }
  .pg-share-stage {
    min-height: 520px;
  }
  .pg-share-note-grid,
  .pg-share-disclosure-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 820px) {
  .pg-auth-hero,
  .pg-share-hero {
    padding: 22px;
    border-radius: 28px;
  }
  .pg-share-hero {
    border-radius: 0 0 30px 30px;
    min-height: auto;
  }
  .pg-auth-copy,
  .pg-share-copy {
    padding: 18px;
  }
  .pg-auth-copy::before,
  .pg-share-copy::before {
    inset: -2% 4% -4% -2%;
  }
  .pg-auth-title,
  .pg-share-title {
    max-width: none;
    font-size: clamp(2.5rem, 11vw, 4.2rem);
  }
  .pg-auth-metric-grid,
  .pg-share-microgrid,
  .pg-share-note-grid,
  .pg-share-access-rail,
  .pg-share-disclosure-grid,
  .pg-share-disclosure-strip {
    grid-template-columns: 1fr;
  }
  .pg-auth-metric,
  .pg-share-microcard,
  .pg-share-note,
  .pg-share-disclosure-item,
  .pg-share-access-step {
    padding-right: 0;
    padding-bottom: 16px;
    border-right: 0;
    border-bottom: 1px solid rgba(255, 238, 212, 0.08);
  }
  .pg-auth-metric:last-child,
  .pg-share-microcard:last-child,
  .pg-share-note:last-child,
  .pg-share-disclosure-item:last-child,
  .pg-share-access-step:last-child {
    padding-bottom: 0;
    border-bottom: 0;
  }
  .pg-auth-stage,
  .pg-share-stage {
    min-height: 300px;
  }
  .pg-auth-stage-copy-track,
  .pg-share-stage-copy-track {
    min-height: 190px;
  }
  .pg-share-creator {
    padding-left: 0;
    border-left: 0;
    padding-top: 16px;
    border-top: 1px solid rgba(255, 241, 215, 0.10);
  }
  .pg-share-access-rail {
    padding-left: 0;
    border-left: 0;
    border-top: 1px solid rgba(255, 255, 255, 0.12);
    padding-top: 18px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .pg-auth-stage::before,
  .pg-share-stage::before,
  .pg-auth-scene,
  .pg-share-scene,
  .pg-auth-slide,
  .pg-share-slide,
  .pg-auth-stage-caption,
  .pg-share-stage-caption,
  .pg-stage-progress span::after {
    animation: none !important;
  }
  .pg-auth-slide:first-child,
  .pg-share-slide:first-child,
  .pg-auth-scene:first-child,
  .pg-share-scene:first-child,
  .pg-auth-stage-caption:first-child,
  .pg-share-stage-caption:first-child {
    opacity: 1;
    transform: none;
    filter: none;
  }
  .pg-stage-progress span::after {
    transform: scaleX(1);
    opacity: 1;
  }
}
.pg-share-workspace {
  display: grid !important;
  grid-template-columns: minmax(300px, 350px) minmax(0, 1fr);
  gap: 18px;
  align-items: start;
  margin-top: 12px;
}
.pg-share-workspace > * {
  min-width: 0;
}
.pg-share-rail,
.pg-share-main,
.pg-share-stack {
  display: grid;
  gap: 14px;
  align-content: start;
}
.pg-share-main {
  min-width: 0;
  display: grid;
  gap: 14px;
  align-content: start;
}
.pg-share-signal-card,
.pg-share-status-card,
.pg-share-stage-card {
  min-width: 0;
}
.pg-share-signal-card .pg-panel,
.pg-share-status-card .pg-panel,
.pg-share-stage-card .pg-panel {
  height: 100%;
}
.pg-share-workspace .pg-control-board {
  padding: 18px;
  border-radius: 28px;
  background:
    linear-gradient(156deg, rgba(7, 12, 18, 0.98), rgba(10, 18, 27, 0.94)),
    radial-gradient(circle at top right, rgba(243, 197, 125, 0.12), transparent 26%);
  overflow: hidden;
}
.pg-share-workspace .pg-control-board::after {
  content: "";
  position: absolute;
  inset: 0;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.03), transparent 18%),
    linear-gradient(90deg, rgba(255, 255, 255, 0.02), transparent 18%, transparent 82%, rgba(255, 255, 255, 0.02));
  pointer-events: none;
}
.pg-share-workspace .pg-control-board h3 {
  margin: 0;
  color: rgba(255, 241, 215, 0.82);
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.pg-share-workspace .pg-control-board .gr-markdown > :last-child {
  margin-bottom: 0;
}
.pg-share-status-card .pg-panel {
  border-radius: 28px;
  background:
    linear-gradient(150deg, rgba(7, 13, 20, 0.98), rgba(9, 16, 24, 0.94)),
    radial-gradient(circle at top right, rgba(79, 140, 255, 0.10), transparent 28%);
}
.pg-share-signal-card .pg-signal-overview {
  border-radius: 30px;
  padding: 18px 20px;
  background:
    radial-gradient(circle at 84% 12%, rgba(79, 140, 255, 0.12), transparent 22%),
    radial-gradient(circle at 24% 0%, rgba(243, 197, 125, 0.10), transparent 24%),
    linear-gradient(140deg, rgba(8, 14, 21, 0.98), rgba(7, 13, 20, 0.96));
}
.pg-share-stage-row {
  display: grid !important;
  grid-template-columns: minmax(0, 5.5fr) minmax(280px, 1fr);
  gap: 16px;
  align-items: stretch;
  overflow: visible;
}
.pg-share-stage-row .column,
.pg-share-stage-row [class*="column"] {
  min-width: 0;
  overflow: visible;
}
.pg-stage-media,
.pg-share-stage-media,
.pg-share-stage-media > div,
.pg-share-stage-media img,
.pg-share-stage-media canvas {
  border-radius: 28px;
  box-shadow: 0 26px 54px rgba(0, 0, 0, 0.22);
}
.pg-stage-media,
.pg-share-stage-media {
  width: 100% !important;
  height: auto !important;
  min-height: 480px !important;
  max-height: none !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  overflow: visible !important;
  padding: 0 !important;
  margin: 0 !important;
}
.pg-stage-media img,
.pg-share-stage-media img,
.pg-stage-media canvas,
.pg-share-stage-media canvas {
  width: 100% !important;
  height: auto !important;
  object-fit: contain !important;
  max-width: 100% !important;
  max-height: none !important;
  aspect-ratio: auto !important;
}
.pg-stage-media > div,
.pg-share-stage-media > div {
  width: 100% !important;
  height: auto !important;
  overflow: visible !important;
}
.pg-share-stage-row .column:first-child {
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.pg-share-heatmap-img,
.pg-share-feedback-img {
  width: 100% !important;
  height: auto !important;
  min-height: 320px !important;
  max-height: none !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  overflow: visible !important;
}
.pg-share-heatmap-img img,
.pg-share-feedback-img img,
.pg-share-heatmap-img canvas,
.pg-share-feedback-img canvas {
  width: 100% !important;
  height: auto !important;
  object-fit: contain !important;
  max-width: 100% !important;
  max-height: none !important;
  aspect-ratio: auto !important;
}
.pg-share-stage-card .pg-panel {
  border-radius: 26px;
  background:
    linear-gradient(155deg, rgba(8, 14, 21, 0.98), rgba(9, 16, 24, 0.94)),
    radial-gradient(circle at top right, rgba(243, 197, 125, 0.08), transparent 24%);
}
.pg-share-stack .pg-gauge-card {
  border-color: rgba(255, 241, 215, 0.12);
  background:
    linear-gradient(180deg, rgba(8, 14, 21, 0.98), rgba(9, 16, 24, 0.94));
  min-height: 200px;
  max-height: 240px;
}
.pg-share-stack {
  gap: 12px;
  align-content: start;
}
.pg-share-stack .pg-share-stage-card {
  min-height: 140px;
}
.pg-share-stack h3 {
  font-size: 13px;
  margin-bottom: 6px;
}
.pg-share-stack .pg-panel {
  padding: 14px !important;
}
@media (min-width: 1181px) {
  .pg-share-rail {
    position: sticky;
    top: 24px;
    align-self: start;
  }
}
@media (max-width: 1180px) {
  .pg-share-workspace,
  .pg-share-stage-row {
    grid-template-columns: 1fr;
  }
  .pg-share-rail {
    position: static;
  }
}
"""
SHARE_UI_CSS = f"{pg.UI_CSS}\n{SHARE_EXTRA_CSS}"


@dataclass(slots=True)
class ShareSession:
    session_id: str
    result: dict[str, Any] | None = None
    source_image_state: Any = None
    active_file_path: str = ""
    render_config: dict[str, Any] = field(default_factory=lambda: _default_render_config())
    updated_at: float = field(default_factory=time.time)


_share_sessions: dict[str, ShareSession] = {}
_share_sessions_lock = threading.Lock()
_share_auth_failures: dict[str, dict[str, float | int]] = {}
_share_auth_lock = threading.Lock()
_share_rate_limit_state: dict[str, list[float]] = {}
_share_rate_limit_lock = threading.Lock()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _session_label(session_id: str | None) -> str:
    token = str(session_id or "").strip()
    if not token:
        return "none"
    return token[:12]


def _request_headers(request: gr.Request | None) -> dict[str, str]:
    if request is None:
        return {}
    try:
        return {str(key).lower(): str(value) for key, value in dict(getattr(request, "headers", {}) or {}).items()}
    except Exception:
        return {}


def _request_client_ip(request: gr.Request | None) -> str:
    headers = _request_headers(request)
    forwarded = str(headers.get("cf-connecting-ip") or headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    try:
        client = getattr(request, "client", None)
        host = str(getattr(client, "host", "") or "").strip()
        if host:
            return host
    except Exception:
        pass
    return "unknown"


def _request_identity(request: gr.Request | None, session_id: str | None = None) -> dict[str, str]:
    username = ""
    session_hash = ""
    try:
        username = str(getattr(request, "username", "") or "").strip()
    except Exception:
        username = ""
    try:
        session_hash = str(getattr(request, "session_hash", "") or "").strip()
    except Exception:
        session_hash = ""
    client_ip = _request_client_ip(request)
    identity = username or session_hash or str(session_id or "").strip() or client_ip or "anonymous"
    return {
        "identity": identity,
        "username": username,
        "session_hash": session_hash,
        "client_ip": client_ip,
    }


def _request_audit_fields(request: gr.Request | None, session_id: str | None = None) -> dict[str, Any]:
    identity = _request_identity(request, session_id)
    payload: dict[str, Any] = {
        "identity_hash": _hash_text(identity["identity"])[:16],
    }
    if identity["username"]:
        payload["user_hash"] = _hash_text(identity["username"])[:16]
    if identity["session_hash"]:
        payload["request_session_hash"] = _hash_text(identity["session_hash"])[:16]
    if identity["client_ip"] and identity["client_ip"] != "unknown":
        payload["client_hash"] = _hash_text(identity["client_ip"])[:16]
    return payload


def _audit_safe_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_audit_safe_value(item) for item in value[:8]]
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 16:
                break
            safe[str(key)] = _audit_safe_value(item)
        return safe
    return str(value)


def _append_share_audit(payload: Mapping[str, Any]) -> None:
    if not SHARE_MODE_ACTIVE:
        return
    try:
        pg.append_hash_chain(
            SHARE_AUDIT_LOG_PATH,
            cast(dict[str, Any], {str(key): _audit_safe_value(value) for key, value in payload.items()}),
        )
    except Exception as exc:
        SHARE_LOGGER.exception("share audit append failed: %s", exc)


def _log_share_event(level: str, event: str, *, session_id: str | None = None, **fields: Any) -> None:
    session_tag = _session_label(session_id)
    message = f"{event} | session={session_tag}"
    if fields:
        message += " | " + ", ".join(f"{key}={_audit_safe_value(value)}" for key, value in fields.items())
    if level == "error":
        SHARE_LOGGER.error(message)
    elif level == "warning":
        SHARE_LOGGER.warning(message)
    else:
        SHARE_LOGGER.info(message)
    _append_share_audit(
        {
            "ts": pg.utc_now_iso(),
            "event": event,
            "level": level,
            "session": session_tag,
            **fields,
        }
    )


def _record_share_error(
    stage: str,
    message: str,
    exc: Exception | None = None,
    *,
    session_id: str | None = None,
    **fields: Any,
) -> None:
    payload = {
        "stage": stage,
        "message": message,
        "error_type": type(exc).__name__ if exc is not None else "UnknownError",
        **fields,
    }
    if exc is not None:
        SHARE_LOGGER.exception("%s | session=%s | %s", stage, _session_label(session_id), message)
    else:
        SHARE_LOGGER.error("%s | session=%s | %s", stage, _session_label(session_id), message)
    _append_share_audit(
        {
            "ts": pg.utc_now_iso(),
            "event": "share_error",
            "session": _session_label(session_id),
            **payload,
        }
    )


def _default_render_config() -> dict[str, Any]:
    return pg._build_render_config(
        overlay_mode=str(DEFAULT_SHARE_RENDER["overlay_mode"]),
        min_conf_global=float(DEFAULT_SHARE_RENDER["min_conf_global"]),
        min_conf_latest=float(DEFAULT_SHARE_RENDER["min_conf_latest"]),
        history_depth=int(DEFAULT_SHARE_RENDER["history_depth"]),
        label_density=int(DEFAULT_SHARE_RENDER["label_density"]),
        projection_focus=float(DEFAULT_SHARE_RENDER["projection_focus"]),
        debug_depth=int(DEFAULT_SHARE_RENDER["debug_depth"]),
        higher_timeframe=str(DEFAULT_SHARE_RENDER["higher_timeframe"]),
        lower_timeframe=str(DEFAULT_SHARE_RENDER["lower_timeframe"]),
    )


def _share_clean_text(value: Any, *, limit: int = SHARE_CONTACT_FIELD_MAX_CHARS) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _store_contact_brief_securely(
    *,
    session_id: str,
    full_name: str,
    contact_channel: str,
    organization: str,
    purpose: str,
    consent_ack: bool,
    request: gr.Request | None,
) -> None:
    get_pref_store = getattr(pg, "_get_pref_store", None)
    if not callable(get_pref_store):
        raise RuntimeError("Encrypted contact storage is unavailable.")
    pref_store = get_pref_store()
    if not bool(getattr(pref_store, "available", False)):
        reason = str(getattr(pref_store, "reason", "Encrypted contact storage is unavailable.")).strip()
        raise RuntimeError(reason or "Encrypted contact storage is unavailable.")
    insert_contact_brief = getattr(pref_store, "insert_contact_brief", None)
    if not callable(insert_contact_brief):
        raise RuntimeError("Encrypted contact storage does not support contact briefs.")
    insert_contact_brief(
        {
            "ts": pg.utc_now_iso(),
            "session_id": str(session_id),
            "alias": SHARE_PUBLIC_ALIAS,
            "creator": SHARE_CREATOR_NAME,
            "full_name": str(full_name),
            "contact_channel": str(contact_channel),
            "organization": str(organization),
            "purpose": str(purpose),
            "consent_ack": bool(consent_ack),
            "meta": _request_audit_fields(request, session_id),
        }
    )


def _share_brand_asset_dirs() -> list[Path]:
    candidates = [SHARE_BRAND_ASSET_DIR]
    if SHARE_BRAND_ASSET_DIR == _SHARE_DEFAULT_BRAND_ASSET_DIR:
        candidates.append(_SHARE_DEFAULT_BRAND_ASSET_DIR.parent)
    elif SHARE_BRAND_ASSET_DIR.name.lower() == "css-control":
        candidates.append(SHARE_BRAND_ASSET_DIR.parent)
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            normalized = str(candidate.resolve())
        except Exception:
            normalized = str(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(candidate)
    return unique


def _share_brand_asset_paths(asset_dir: Path) -> list[Path]:
    preferred_names = {name.lower() for name in SHARE_BRAND_IMAGE_PREFERRED_ORDER}
    ordered: list[Path] = []
    for filename in SHARE_BRAND_IMAGE_PREFERRED_ORDER:
        path = asset_dir / filename
        if path.is_file() and path.suffix.lower() in SHARE_ALLOWED_IMAGE_EXTS:
            ordered.append(path)
    ordered.extend(
        path
        for path in sorted(asset_dir.iterdir())
        if path.is_file()
        and path.suffix.lower() in SHARE_ALLOWED_IMAGE_EXTS
        and path.name.lower() not in preferred_names
    )
    return ordered


def _share_brand_data_uri(path: Path) -> str:
    try:
        with Image.open(path) as img:
            working = img.convert("RGB")
            resampling = getattr(Image, "Resampling", None)
            lanczos = getattr(resampling, "LANCZOS", getattr(Image, "LANCZOS", 1))
            if max(working.size) > SHARE_BRAND_IMAGE_EDGE:
                working.thumbnail((SHARE_BRAND_IMAGE_EDGE, SHARE_BRAND_IMAGE_EDGE), lanczos)
            buffer = io.BytesIO()
            working.save(buffer, format="JPEG", quality=84, optimize=True)
    except (FileNotFoundError, UnidentifiedImageError, OSError):
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _share_brand_slides() -> list[dict[str, str]]:
    slides = [
        {
            "scene_key": "vision",
            "eyebrow": "Hybrid Command Surface",
            "title": "AI market vision staged as a cinematic protected gateway.",
            "body": "Charts, symbolic security, and system identity blend into one deliberate entry scene instead of a generic login wall.",
            "position": "50% 48%",
        },
        {
            "scene_key": "security",
            "eyebrow": "Security And Risk Layer",
            "title": "Authentication and audit posture stay visible before any desk action begins.",
            "body": "Rate limits, blocked local paths, and host-side state keep the public surface restrained instead of exposing runtime internals.",
            "position": "56% 52%",
        },
        {
            "scene_key": "creator",
            "eyebrow": "Creator Story",
            "title": "Creator intent shapes the surface without weakening discipline.",
            "body": "The experience stays cinematic, but the system story, obligations, and guarded access language remain explicit at every step.",
            "position": "50% 40%",
        },
        {
            "scene_key": "disclosure",
            "eyebrow": "Private Access Discipline",
            "title": "Risk language remains part of the experience rather than hidden below it.",
            "body": "This environment is educational, rate-limited, and protected, but it does not remove trading risk or replace independent judgment.",
            "position": "50% 42%",
        },
    ]
    fallbacks = [
        "linear-gradient(150deg, rgba(8, 18, 26, 0.16), rgba(8, 18, 26, 0.72)), radial-gradient(circle at 18% 22%, rgba(159, 242, 223, 0.34), transparent 36%), radial-gradient(circle at 82% 18%, rgba(245, 201, 130, 0.28), transparent 30%), linear-gradient(132deg, #09141d 0%, #132b39 52%, #10384e 100%)",
        "linear-gradient(150deg, rgba(8, 18, 26, 0.18), rgba(8, 18, 26, 0.78)), radial-gradient(circle at 24% 20%, rgba(111, 176, 255, 0.30), transparent 34%), radial-gradient(circle at 72% 34%, rgba(159, 242, 223, 0.20), transparent 32%), linear-gradient(130deg, #09121b 0%, #112435 46%, #1f3147 100%)",
        "linear-gradient(150deg, rgba(8, 18, 26, 0.22), rgba(8, 18, 26, 0.78)), radial-gradient(circle at 20% 24%, rgba(245, 201, 130, 0.28), transparent 34%), radial-gradient(circle at 78% 16%, rgba(159, 242, 223, 0.24), transparent 30%), linear-gradient(130deg, #0a1219 0%, #102537 50%, #123047 100%)",
        "linear-gradient(150deg, rgba(8, 18, 26, 0.18), rgba(8, 18, 26, 0.76)), radial-gradient(circle at 26% 22%, rgba(213, 132, 90, 0.24), transparent 30%), radial-gradient(circle at 70% 18%, rgba(140, 170, 255, 0.22), transparent 28%), linear-gradient(130deg, #091218 0%, #152635 48%, #1c3951 100%)",
    ]
    image_uris: list[str] = []
    for asset_dir in _share_brand_asset_dirs():
        try:
            candidate_dir = asset_dir.resolve()
        except Exception:
            candidate_dir = asset_dir
        if not candidate_dir.exists() or not candidate_dir.is_dir():
            continue
        for path in _share_brand_asset_paths(candidate_dir):
            uri = _share_brand_data_uri(path)
            if uri:
                image_uris.append(uri)
            if len(image_uris) >= SHARE_BRAND_IMAGE_LIMIT:
                break
        if image_uris:
            break
    for index, slide in enumerate(slides):
        slide["image"] = image_uris[index % len(image_uris)] if image_uris else ""
        slide["gradient"] = fallbacks[index % len(fallbacks)]
    return slides


def _share_stage_items(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    slide_payload = cast(list[dict[str, Any]], payload.get("slides", []))
    items = [
        {
            "scene_key": str(slide.get("scene_key", "")),
            "eyebrow": str(slide.get("eyebrow", payload.get("stage_kicker", "Protected AI Vision"))),
            "title": str(
                slide.get(
                    "title",
                    payload.get("stage_title", "Hybrid market intelligence presented as a controlled visual narrative."),
                )
            ),
            "body": str(
                slide.get(
                    "body",
                    payload.get(
                        "stage_body",
                        "Protected visuals, disciplined interpretation, and creator intent move together in this surface.",
                    ),
                )
            ),
        }
        for slide in slide_payload
    ]
    if items:
        return items
    return [
        {
            "scene_key": "vision",
            "eyebrow": str(payload.get("stage_kicker", "Protected AI Vision")),
            "title": str(payload.get("stage_title", "Hybrid market intelligence presented as a controlled visual narrative.")),
            "body": str(
                payload.get(
                    "stage_body",
                    "Protected visuals, disciplined interpretation, and creator intent move together in this surface.",
                )
            ),
        }
    ]


def _share_dialogs_html() -> str:
    return "".join(
        [
            pg._build_help_dialog(
                "share-vision",
                "System Vision",
                f"What {SHARE_PUBLIC_ALIAS} represents",
                SHARE_CREATOR_STORY,
                [
                    ("Purpose", "A hybrid AI vision desk for reading structure, confluence, and execution context."),
                    ("Experience", "A premium, operator-controlled access surface that emphasizes clarity, discipline, and trust."),
                    ("Outcome", "Visitors receive a refined educational view instead of direct backend access or exposed model state."),
                ],
            ),
            pg._build_help_dialog(
                "share-security",
                "Security Controls",
                "How the public surface stays protected",
                "This share mode keeps sensitive runtime state on the host and reduces what the browser can reach.",
                [
                    ("Authentication", "Credential checks, lockouts, and session TTLs gate access to the desk."),
                    ("Containment", "Server-side inference, blocked local paths, and quiet errors reduce exposed attack surface."),
                    ("Audit", "Tamper-evident hash-chain logs preserve operator visibility into sensitive events."),
                ],
            ),
            pg._build_help_dialog(
                "share-disclosure",
                "Risk Disclosure",
                "Important use and legal context",
                "This environment is educational and research-oriented. It does not replace independent financial judgment.",
                [
                    ("Educational Only", "Outputs are provided for study, interpretation, and system evaluation."),
                    ("Trading Risk", "Financial trading carries material risk, including the risk of loss."),
                    ("User Responsibility", "Each participant remains responsible for compliance, judgment, and local legal obligations."),
                ],
            ),
            pg._build_help_dialog(
                "share-creator",
                "Creator Note",
                "Built by Thabang Johnson Masoabi",
                "The desk exists to express advanced AI-guided vision in a disciplined, secure, and intentional format.",
                [
                    ("Identity", "Creator: Thabang Johnson Masoabi."),
                    ("Goal", "Translate market structure into a clearer educational visual narrative."),
                    ("Vision", "Combine elegant design, guarded infrastructure, and explainable hybrid intelligence in one surface."),
                ],
            ),
        ]
    )


def _share_surface_payload() -> dict[str, Any]:
    return {
        "alias": SHARE_PUBLIC_ALIAS,
        "creator": SHARE_CREATOR_NAME,
        "creator_story": SHARE_CREATOR_STORY,
        "kicker": "Protected Welcome Surface",
        "hero_title": "Welcome to the",
        "hero_accent": "808Fx Standard Hybrid System",
        "hero_body": (
          "Through the eyes of intelligence, life is divine. Thabang Johnson Masoabi built this protected experience for advanced market vision through Artificial Intelligence while keeping the core runtime on the host machine."
        ),
        "badges": [
            "Private Auth",
            "Server-Side State",
            "Tamper-Evident Audit",
            "Rate Limited",
            "Educational Use",
        ],
        "metrics": [
            {"label": "Vision", "value": "Advanced AI-guided structure interpretation"},
            {"label": "Security", "value": "Host-side inference with layered controls"},
            {"label": "Purpose", "value": "Educational signal review and guided context"},
        ],
        "slides": _share_brand_slides(),
        "stage_kicker": "Hybrid Command Surface",
        "stage_title": "AI market vision staged as a cinematic protected gateway.",
        "stage_body": "Charts, symbolic security, and system identity blend into one deliberate entry scene instead of a generic login wall.",
        "form_title": "Secure Access Gateway",
        "form_note": (
            "Authorized collaborators enter through a protected, rate-limited surface. "
            f"Use the public alias <strong>{SHARE_PUBLIC_ALIAS}</strong> when sharing the experience."
        ),
        "enter_label": "Enter Protected Desk",
        "footnote": (
            "Risk disclosure: this environment is for educational purposes only. Financial trading is risky and "
            "all users remain responsible for independent judgment, legal compliance, and local policy requirements."
        ),
        "dialogs_html": _share_dialogs_html(),
    }


def _share_auth_message() -> str:
    return "<span class='pg-auth-mount' data-share-auth='true'></span>"


def _share_ui_head() -> str:
    payload_json = json.dumps(_share_surface_payload(), ensure_ascii=True).replace("</", "<\\/")
    extra_head = """
<script>
(() => {
  if (window.__pgShareAuthBooted) return;
  window.__pgShareAuthBooted = true;

  const shareData = __PAYLOAD__;
  window.__PG_SHARE_SURFACE__ = shareData;

  const esc = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const slideBackground = (slide) => slide.image
    ? "linear-gradient(155deg, rgba(6, 10, 16, 0.08), rgba(6, 10, 16, 0.56)), radial-gradient(circle at 82% 18%, rgba(245, 201, 130, 0.18), transparent 24%), url('" + slide.image + "')"
    : slide.gradient;

  const stageItems = (items) =>
    items && items.length
      ? items
      : [{ eyebrow: shareData.stage_kicker, title: shareData.stage_title, body: shareData.stage_body }];

  const sceneKey = (item, index) =>
    String(item.scene_key || ("scene-" + String(index + 1)));

  const badgeHtml = (items, klass) =>
    items.map((item) => "<span class=\\"" + klass + "\\">" + esc(item) + "</span>").join("");

  const metricHtml = (items, klass) =>
    items
      .map(
        (item) =>
          "<div class=\\"" + klass + "\\"><span>" + esc(item.label) + "</span><strong>" + esc(item.value) + "</strong></div>"
      )
      .join("");

  const slideHtml = (items, klass) =>
    stageItems(items)
      .map(
        (item, index) =>
          "<span class=\\"" + klass + "\\" data-scene-key=\\"" + esc(sceneKey(item, index)) + "\\" style=\\"background-image:" + slideBackground(item) + ";background-position:"
          + esc(item.position || "center center") + ";\\"></span>"
      )
      .join("");

  const stageCaptionHtml = (items, klass) =>
    stageItems(items)
      .map(
        (item, index) =>
          "<div class=\\"" + klass + "\\" data-scene-key=\\"" + esc(sceneKey(item, index)) + "\\"><span>" + esc(item.eyebrow || shareData.stage_kicker)
          + "</span><strong>" + esc(item.title || shareData.stage_title)
          + "</strong><p>" + esc(item.body || shareData.stage_body) + "</p></div>"
      )
      .join("");

  const stageProgressHtml = (items) =>
    stageItems(items).map((item, index) => "<span data-scene-key=\\"" + esc(sceneKey(item, index)) + "\\"></span>").join("");

  const buildHero = () => {
    return `
      <div class="pg-auth-hero">
        <div class="pg-auth-scene-plane" aria-hidden="true">${slideHtml(shareData.slides, "pg-auth-scene")}</div>
        <div class="pg-auth-hero-shell">
          <div class="pg-auth-copy">
            <div class="pg-auth-lux-rail">Protected Access Surface</div>
            <div class="pg-auth-kicker">${esc(shareData.kicker)}</div>
            <div class="pg-auth-title">${esc(shareData.hero_title)}<strong>${esc(shareData.hero_accent)}</strong></div>
            <div class="pg-auth-body">${esc(shareData.hero_body)}</div>
            <div class="pg-auth-actions">
              <button type="button" class="pg-auth-cta" data-help-open="share-vision" data-share-scene="vision">System Vision</button>
              <button type="button" class="pg-auth-cta" data-help-open="share-security" data-share-scene="security">Security Layer</button>
              <button type="button" class="pg-auth-cta" data-help-open="share-disclosure" data-share-scene="disclosure">Risk Disclosure</button>
              <button type="button" class="pg-auth-cta" data-tone="secondary" data-help-open="share-creator" data-share-scene="creator">Creator Note</button>
            </div>
            <div class="pg-share-badges">${badgeHtml(shareData.badges, "pg-auth-badge")}</div>
            <div class="pg-auth-metric-grid">${metricHtml(shareData.metrics, "pg-auth-metric")}</div>
          </div>
          <div class="pg-auth-stage">
            <div class="pg-auth-slides">${slideHtml(shareData.slides, "pg-auth-slide")}</div>
            <div class="pg-auth-stage-copy">
              <div class="pg-auth-stage-copy-track">${stageCaptionHtml(shareData.slides, "pg-auth-stage-caption")}</div>
              <div class="pg-stage-progress">${stageProgressHtml(shareData.slides)}</div>
            </div>
          </div>
        </div>
      </div>
    `;
  };

  let sceneResetHandle = 0;

  const sceneRoots = () => Array.from(document.querySelectorAll(".pg-auth-hero, .pg-share-hero"));

  const setSceneForRoot = (root, key) => {
    const normalizedKey = String(key || "").trim();
    root.classList.toggle("pg-scene-manual", Boolean(normalizedKey));
    root.querySelectorAll("[data-scene-key]").forEach((node) => {
      const isActive = Boolean(normalizedKey) && node.getAttribute("data-scene-key") === normalizedKey;
      node.classList.toggle("is-active", isActive);
    });
  };

  const resetScenes = () => {
    window.clearTimeout(sceneResetHandle);
    sceneRoots().forEach((root) => setSceneForRoot(root, ""));
  };

  const activateScene = (key, holdMs = 0) => {
    if (!key) {
      resetScenes();
      return;
    }
    window.clearTimeout(sceneResetHandle);
    sceneRoots().forEach((root) => setSceneForRoot(root, key));
    if (holdMs > 0) {
      sceneResetHandle = window.setTimeout(resetScenes, holdMs);
    }
  };

  const wireSceneButtons = (scope = document) => {
    scope.querySelectorAll("[data-share-scene]").forEach((button) => {
      if (button.dataset.sceneBound === "true") return;
      button.dataset.sceneBound = "true";
      const key = String(button.getAttribute("data-share-scene") || "").trim();
      if (!key) return;
      button.addEventListener("pointerenter", () => activateScene(key));
      button.addEventListener("focus", () => activateScene(key));
      button.addEventListener("pointerleave", resetScenes);
      button.addEventListener("blur", resetScenes);
      button.addEventListener("click", () => activateScene(key, 3200));
    });
  };

  const wireAuthFocus = (panel, wrap) => {
    if (panel.dataset.authFocusBound === "true") return;
    panel.dataset.authFocusBound = "true";
    const syncFocusState = () => {
      wrap.dataset.authFocus = panel.matches(":focus-within") ? "true" : "false";
    };
    panel.addEventListener("focusin", syncFocusState);
    panel.addEventListener("focusout", () => window.setTimeout(syncFocusState, 0));
    syncFocusState();
  };

    const notifiedEvents = new Set();

    const ensureToastHost = () => {
        let host = document.querySelector('.pg-share-toast-host');
        if (host) return host;
        host = document.createElement('div');
        host.className = 'pg-share-toast-host';
        document.body.appendChild(host);
        return host;
    };

    const showToast = (title, body) => {
        const host = ensureToastHost();
        const toast = document.createElement('div');
        toast.className = 'pg-share-toast';
        toast.innerHTML = '<strong>' + esc(title || 'Update') + '</strong><span>' + esc(body || '') + '</span>';
        host.appendChild(toast);
        window.setTimeout(() => {
            toast.remove();
        }, 4800);
    };

    const showBrowserNotification = (title, body) => {
        if (!('Notification' in window)) return;
        if (Notification.permission === 'granted') {
            new Notification(String(title || 'Update'), { body: String(body || '') });
            return;
        }
        if (Notification.permission === 'default') {
            Notification.requestPermission().then((permission) => {
                if (permission === 'granted') {
                    new Notification(String(title || 'Update'), { body: String(body || '') });
                }
            }).catch(() => {});
        }
    };

    const processNotifyEvents = (scope = document) => {
        scope.querySelectorAll('.pg-notify-event').forEach((node) => {
            const id = String(node.getAttribute('data-id') || '').trim();
            if (!id || notifiedEvents.has(id)) return;
            notifiedEvents.add(id);
            const title = String(node.getAttribute('data-title') || 'Signal Update');
            const body = String(node.getAttribute('data-body') || 'A protected run has completed.');
            showToast(title, body);
            showBrowserNotification(title, body);
        });
    };

    const wireFeedbackIntentGate = (scope = document) => {
        if (!document.documentElement.dataset.pgFeedbackIntent) {
            document.documentElement.dataset.pgFeedbackIntent = 'false';
        }
        scope.querySelectorAll('.pg-tab-wrap .tab-nav button').forEach((button) => {
            if (button.dataset.feedbackIntentBound === 'true') return;
            button.dataset.feedbackIntentBound = 'true';
            const label = String(button.textContent || '').trim().toLowerCase();
            if (!label.includes('feedback')) return;
            button.addEventListener('click', () => {
                document.documentElement.dataset.pgFeedbackIntent = 'true';
            });
            if (button.getAttribute('aria-selected') === 'true') {
                document.documentElement.dataset.pgFeedbackIntent = 'true';
            }
        });
    };

  const decorateLoginSurface = () => {
    const mount = document.querySelector(".auth .pg-auth-mount");
    if (!mount) return;
    const wrap = mount.closest(".wrap");
    if (!wrap) return;

    wrap.classList.add("pg-auth-shell");

    const panel = Array.from(wrap.children).find((node) => !node.classList.contains("pg-auth-hero"));
    if (!panel) return;
    panel.classList.add("pg-auth-panel");

    const formShell = panel.firstElementChild;
    if (formShell) formShell.classList.add("pg-auth-form-shell");

    const heading = panel.querySelector("h2");
    if (heading) heading.textContent = shareData.form_title;

    const auth = panel.querySelector(".auth");
    if (auth) auth.style.display = "none";

    if (!panel.querySelector(".pg-auth-form-note")) {
      const note = document.createElement("div");
      note.className = "pg-auth-form-note";
      note.innerHTML = shareData.form_note;
      if (heading?.nextSibling) {
        heading.parentNode.insertBefore(note, heading.nextSibling);
      } else if (heading?.parentNode) {
        heading.parentNode.appendChild(note);
      }
    }

    if (!panel.querySelector(".pg-auth-footnote")) {
      const footnote = document.createElement("div");
      footnote.className = "pg-auth-footnote";
      footnote.textContent = shareData.footnote;
      panel.appendChild(footnote);
    }

    const inputs = panel.querySelectorAll("input");
    if (inputs[0]) {
      inputs[0].setAttribute("placeholder", "Authorized username");
      inputs[0].setAttribute("autocomplete", "username");
    }
    if (inputs[1]) {
      inputs[1].setAttribute("placeholder", "Private password");
      inputs[1].setAttribute("autocomplete", "current-password");
    }

    const button = panel.querySelector("button");
    if (button) button.textContent = shareData.enter_label || ("Enter " + shareData.alias);

    if (!wrap.querySelector(".pg-auth-hero")) {
      panel.insertAdjacentHTML("beforebegin", buildHero());
    }

    if (!document.querySelector('.pg-help-dialog[data-help-dialog="share-vision"]')) {
      document.body.insertAdjacentHTML("beforeend", shareData.dialogs_html);
    }

    wireAuthFocus(panel, wrap);
    wireSceneButtons(wrap);
    document.title = shareData.alias + " | Protected AI Vision Desk";
  };

  const bootstrap = () => {
    decorateLoginSurface();
    wireSceneButtons(document);
        wireFeedbackIntentGate(document);
        processNotifyEvents(document);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap, { once: true });
  } else {
    bootstrap();
  }

  new MutationObserver(() => {
    decorateLoginSurface();
    wireSceneButtons(document);
        wireFeedbackIntentGate(document);
        processNotifyEvents(document);
  }).observe(document.documentElement, {
    childList: true,
    subtree: true
  });
})();
</script>
"""
    return pg.UI_HEAD + extra_head.replace("__PAYLOAD__", payload_json)


def _empty_share_outputs(session_id: str = "") -> tuple[Any, ...]:
    return (
        None,
        None,
    pg._placeholder_panel("Signal Overview", "Premium launch mode is active. Upload exactly four chart images to unlock the advanced feed, zone studio, and live review desk."),
    pg._placeholder_panel("Forecast & Risk", "Forecast and risk controls are active from launch. The first quartet will populate the live read."),
    _build_share_launch_timeframe_html(),
    pg._placeholder_panel("Binary Timing Playbook", "Launch-ready timing guidance is armed from the premium surface. Run a signal to open the full timing playbook."),
        None,
    pg._placeholder_panel("Confidence Heatmap", "The heatmap engine is armed from launch and will render concentration once a quartet is uploaded."),
    pg._placeholder_panel("Compare Desk", "The compare desk is active from launch and will render the split view after the first shared inference."),
    pg._build_adaptive_guidance_html(None),
    pg._placeholder_panel("Model Council", "Premium model council refinement is armed from launch and will activate after the first quartet."),
    _build_share_launch_console_html(),
        session_id,
    )


def _share_feedback_placeholder() -> str:
    return "Feedback submitted here will be recorded for server-side review and learning audit logging."


def _share_status_html(
    message: str,
    *,
    result: Mapping[str, Any] | None = None,
    render_config: Mapping[str, Any] | None = None,
    notification_event: str = "",
) -> str:
    config = dict(render_config or {})
    chip_items = [
        pg._chip("Auth Enabled", "teal"),
        pg._chip("Server-Side State", "soft"),
        pg._chip("Audit Hash Chain", "amber"),
        pg._chip("Quiet Errors", "soft"),
        pg._chip("Rate Limited", "amber"),
    ]
    if SHARE_SIDE_EFFECT_FREE:
        chip_items.append(pg._chip("Mutation Guard", "teal"))
    if not SHARE_ENABLE_FEEDBACK:
        chip_items.append(pg._chip("Feedback Disabled", "soft"))
    chips = "".join(chip_items)
    rows = [
        "<div class='pg-panel'>",
        "<div class='pg-section-title'>Share Guard</div>",
        f"<div class='pg-chip-row'>{chips}</div>",
        f"<div class='pg-muted' style='margin-top:10px;'>{html.escape(str(message))}</div>",
    ]
    if result:
        action = str(result.get("action", "HOLD")).upper()
        confidence = float(result.get("confidence", 0.0) or 0.0)
        overlay_mode = str(config.get("overlay_mode", DEFAULT_SHARE_RENDER["overlay_mode"])).replace("-", " ")
        rows.append(
            (
                "<div class='pg-muted' style='margin-top:10px;'>"
                "Signal run complete. Premium visuals are exposed to the browser while the backend remains server-side."
                "</div>"
            )
        )
        rows.append(
            (
                "<div class='pg-muted' style='margin-top:10px;'>"
                f"Current signal: {html.escape(action)} at {html.escape(pg._fmt_pct01(confidence))}. "
                f"Overlay view: {html.escape(overlay_mode)}."
                "</div>"
            )
        )
    rows.append(
        "<div class='pg-muted' style='margin-top:10px;'>"
        "Restricted response surface: signal summary, visual desk, model council, and learning feedback only."
        "</div>"
    )
    notify_event = str(notification_event or "").strip().lower()
    notify_id = ""
    notify_title = ""
    notify_body = ""
    if notify_event:
        notify_id = f"{notify_event}:{int(time.time() * 1000)}"
        if result:
            action = str(result.get("action", "HOLD")).upper()
            confidence = float(result.get("confidence", 0.0) or 0.0)
            notify_title = "Signal Review Complete" if notify_event == "signal_complete" else "Model Council Complete"
            notify_body = f"{action} signal at {pg._fmt_pct01(confidence)} confidence."
        else:
            notify_title = "PhoenixGuard Update"
            notify_body = "A protected desk action has completed."
    if notify_event and notify_id:
        rows.append(
            (
                "<span class='pg-notify-event' "
                f"data-event='{html.escape(notify_event, quote=True)}' "
                f"data-id='{html.escape(notify_id, quote=True)}' "
                f"data-title='{html.escape(notify_title, quote=True)}' "
                f"data-body='{html.escape(notify_body, quote=True)}'></span>"
            )
        )
    rows.append("</div>")
    return "".join(rows)


def _share_slide_background(slide: Mapping[str, Any]) -> str:
    image = str(slide.get("image", "") or "").strip()
    if image:
        return (
            "linear-gradient(155deg, rgba(6, 10, 16, 0.08), rgba(6, 10, 16, 0.56)), "
            "radial-gradient(circle at 82% 18%, rgba(245, 201, 130, 0.18), transparent 24%), "
            f"url('{image}')"
        )
    return str(slide.get("gradient", ""))


def _share_badges_html(items: list[Any]) -> str:
    return "".join(
        f"<span class='pg-share-badge'>{html.escape(str(item))}</span>"
        for item in items
    )


def _share_micro_cards_html(cards: list[dict[str, str]]) -> str:
    return "".join(
        (
            "<div class='pg-share-microcard'>"
            f"<span>{html.escape(str(card.get('label', '')))}</span>"
            f"<strong>{html.escape(str(card.get('value', '')))}</strong>"
            "</div>"
        )
        for card in cards
    )


def _share_stage_slides_html(slides: list[dict[str, str]]) -> str:
    return "".join(
        (
            "<span class='pg-share-slide' "
            f"data-scene-key='{html.escape(str(slide.get('scene_key', 'vision')), quote=True)}' "
            f"style=\"background-image:{html.escape(_share_slide_background(slide), quote=True)};"
            f"background-position:{html.escape(str(slide.get('position', 'center center')), quote=True)};\"></span>"
        )
        for slide in slides
    )


def _share_scene_plane_html(slides: list[dict[str, str]]) -> str:
    return "".join(
        (
            "<span class='pg-share-scene' "
            f"data-scene-key='{html.escape(str(slide.get('scene_key', 'vision')), quote=True)}' "
            f"style=\"background-image:{html.escape(_share_slide_background(slide), quote=True)};"
            f"background-position:{html.escape(str(slide.get('position', 'center center')), quote=True)};\"></span>"
        )
        for slide in slides
    )


def _share_stage_captions_html(stage_items: list[dict[str, str]], payload: Mapping[str, Any]) -> str:
    return "".join(
        (
            "<div class='pg-share-stage-caption' "
            f"data-scene-key='{html.escape(str(item.get('scene_key', 'vision')), quote=True)}'>"
            f"<span>{html.escape(str(item.get('eyebrow', payload.get('stage_kicker', 'Protected AI Vision'))))}</span>"
            f"<strong>{html.escape(str(item.get('title', payload.get('stage_title', ''))))}</strong>"
            f"<p>{html.escape(str(item.get('body', payload.get('stage_body', ''))))}</p>"
            "</div>"
        )
        for item in stage_items
    )


def _share_stage_progress_html(stage_items: list[dict[str, str]]) -> str:
    return "".join(
        f"<span data-scene-key='{html.escape(str(item.get('scene_key', 'vision')), quote=True)}'></span>"
        for item in stage_items
    )


def _share_hero_note_cards() -> list[tuple[str, str]]:
    return [
        (
            "What It Represents",
            "A controlled AI-vision desk for structure interpretation, guided review, and educational analysis.",
        ),
        (
            "Security Posture",
            "Authentication, rate limiting, blocked local paths, and audit chaining help preserve a restrained public surface.",
        ),
        (
            "Risk Position",
            "This environment does not remove trading risk and must not be treated as financial advice.",
        ),
    ]


def _share_note_grid_html(note_cards: list[tuple[str, str]]) -> str:
    return "".join(
        (
            "<div class='pg-share-note'>"
            f"<span>{html.escape(label)}</span>"
            f"<strong>{html.escape(body)}</strong>"
            "</div>"
        )
        for label, body in note_cards
    )


def _build_share_hero_html() -> str:
    payload = _share_surface_payload()
    stage_items = _share_stage_items(payload)
    slides = cast(list[dict[str, str]], payload.get("slides", []))
    badges = _share_badges_html(cast(list[Any], payload.get("badges", [])))
    micro_cards = _share_micro_cards_html(cast(list[dict[str, str]], payload.get("metrics", [])))
    scene_plane = _share_scene_plane_html(slides)
    slide_html = _share_stage_slides_html(slides)
    stage_captions = _share_stage_captions_html(stage_items, payload)
    stage_progress = _share_stage_progress_html(stage_items)
    notes_html = _share_note_grid_html(_share_hero_note_cards())
    return (
        "<div class='pg-share-hero'>"
        f"<div class='pg-share-scene-plane' aria-hidden='true'>{scene_plane}</div>"
        "<div class='pg-share-hero-shell'>"
        "<div class='pg-share-hero-grid'>"
        "<div class='pg-share-copy'>"
        "<div class='pg-share-lux-rail'>Protected Access Surface</div>"
        f"<div class='pg-share-kicker'>{html.escape(str(payload.get('kicker', 'Protected Welcome Surface')))}</div>"
        f"<div class='pg-share-title'>{html.escape(str(payload.get('hero_title', 'Welcome to the')))}"
        f"<strong>{html.escape(str(payload.get('hero_accent', SHARE_PUBLIC_ALIAS)))}</strong>"
        f"<span class='pg-visually-hidden'>{html.escape(SHARE_PUBLIC_ALIAS)}</span>"
        f"<span class='pg-visually-hidden'>808FxStandardSystemHybrid</span></div>"
        f"<div class='pg-share-body'>{html.escape(str(payload.get('hero_body', '')))}</div>"
        "<div class='pg-share-actions'>"
        "<button type='button' class='pg-inline-button' data-help-open='share-vision' data-share-scene='vision'>System Vision</button>"
        "<button type='button' class='pg-inline-button' data-help-open='share-security' data-share-scene='security'>Security Layer</button>"
        "<button type='button' class='pg-inline-button' data-help-open='share-disclosure' data-share-scene='disclosure'>Risk Disclosure</button>"
        "<button type='button' class='pg-inline-button' data-tone='secondary' data-help-open='share-creator' data-share-scene='creator'>Creator Note</button>"
        "</div>"
        f"<div class='pg-share-badges'>{badges}</div>"
        f"<div class='pg-share-microgrid'>{micro_cards}</div>"
        f"<div class='pg-share-creator'>{html.escape(SHARE_CREATOR_STORY)}</div>"
        "</div>"
        "<div class='pg-share-stage'>"
        f"<div class='pg-share-slides'>{slide_html}</div>"
        "<div class='pg-share-stage-copy'>"
        f"<div class='pg-share-stage-copy-track'>{stage_captions}</div>"
        f"<div class='pg-stage-progress'>{stage_progress}</div>"
        "</div>"
        "</div>"
        "</div>"
        f"<div class='pg-share-note-grid'>{notes_html}</div>"
        "</div>"
        f"{_share_dialogs_html()}"
        "</div>"
    )


def _build_share_briefing_html() -> str:
    access_steps = [
        ("01", "Scan the system story", "Start with the vision, protection model, and risk posture before requesting or granting access."),
        ("02", "Leave a private brief", "Use the contact form to explain who you are, how to reach you, and what part of the desk you want to review."),
        ("03", "Enter with discipline", "Treat the desk as an educational review surface where judgment, compliance, and risk remain your responsibility."),
    ]
    access_html = "".join(
        (
            "<div class='pg-share-access-step'>"
            f"<span>{html.escape(index)}</span>"
            f"<strong>{html.escape(title)}</strong>"
            f"<p>{html.escape(body)}</p>"
            "</div>"
        )
        for index, title, body in access_steps
    )
    disclosure_items = [
        ("Protected Runtime", "The browser receives rendered outputs while the working state, inference path, and audit handling stay on the host."),
        ("Educational Use", "This experience is for interpretation, review, and system demonstration rather than promises of profit."),
        ("Operator Responsibility", "Every participant remains responsible for trade decisions, legal compliance, and independent judgment."),
    ]
    disclosure_html = "".join(
        (
            "<div class='pg-share-disclosure-item'>"
            f"<span>{html.escape(label)}</span>"
            f"<strong>{html.escape(body)}</strong>"
            "</div>"
        )
        for label, body in disclosure_items
    )
    return (
        "<div class='pg-share-briefing'>"
        "<div class='pg-share-editorial-grid'>"
        "<div class='pg-share-manifesto'>"
        "<div class='pg-share-editorial-kicker'>Private Access Narrative</div>"
        "<h3>Atmosphere leads, but trust rails carry the page.</h3>"
        f"<p>{html.escape(SHARE_CREATOR_STORY)} The landing sequence now uses the project's own blurred market and lifestyle imagery to keep the page expansive, premium, and readable while the protected desk remains disciplined.</p>"
        "<div class='pg-inline-actions'>"
        "<button type='button' class='pg-inline-button' data-help-open='share-vision' data-share-scene='vision'>Open Vision Brief</button>"
        "<button type='button' class='pg-inline-button' data-help-open='share-creator' data-share-scene='creator'>Open Creator Note</button>"
        "<button type='button' class='pg-inline-button' data-tone='secondary' data-help-open='share-disclosure' data-share-scene='disclosure'>Open Risk Disclosure</button>"
        "</div>"
        "</div>"
        f"<div class='pg-share-access-rail'>{access_html}</div>"
        "</div>"
        f"<div class='pg-share-disclosure-strip'>{disclosure_html}</div>"
        "</div>"
    )


def _cleanup_share_sessions() -> None:
    now = time.time()
    removed = 0
    with _share_sessions_lock:
        expired_ids = [
            session_id
            for session_id, session in _share_sessions.items()
            if (now - float(session.updated_at)) > float(SHARE_SESSION_TTL_SEC)
        ]
        for session_id in expired_ids:
            _share_sessions.pop(session_id, None)
            removed += 1
        if len(_share_sessions) > SHARE_MAX_SESSIONS:
            ordered = sorted(_share_sessions.values(), key=lambda item: float(item.updated_at))
            overflow = max(0, len(ordered) - SHARE_MAX_SESSIONS)
            for session in ordered[:overflow]:
                _share_sessions.pop(session.session_id, None)
                removed += 1
    if removed:
        _log_share_event("info", "session_cleanup", removed=removed)


def _get_share_session(session_id: str | None, *, create: bool = True) -> ShareSession | None:
    _cleanup_share_sessions()
    normalized = str(session_id or "").strip()
    created = False
    with _share_sessions_lock:
        session = _share_sessions.get(normalized)
        if session is None and create:
            normalized = normalized or uuid4().hex
            session = ShareSession(session_id=normalized, render_config=_default_render_config())
            _share_sessions[normalized] = session
            created = True
        if session is not None:
            session.updated_at = time.time()
    if created and session is not None:
        _log_share_event("info", "session_created", session_id=session.session_id)
    return session


def _update_share_session(
    session_id: str,
    *,
    result: dict[str, Any] | None = None,
    source_image_state: Any = None,
    active_file_path: str | None = None,
    render_config: Mapping[str, Any] | None = None,
) -> ShareSession:
    session = _get_share_session(session_id, create=True)
    assert session is not None
    with _share_sessions_lock:
        if result is not None:
            session.result = result
        if source_image_state is not None:
            session.source_image_state = source_image_state
        if active_file_path is not None:
            session.active_file_path = str(active_file_path)
        if render_config is not None:
            session.render_config = dict(render_config)
        session.updated_at = time.time()
    return session


def _build_share_render_config(
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
    vision_extras: Any = None,
    council_scope: str = "",
) -> dict[str, Any]:
    return pg._build_render_config(
        overlay_mode=overlay_mode,
        min_conf_global=min_conf_global,
        min_conf_latest=min_conf_latest,
        history_depth=history_depth,
        label_density=label_density,
        projection_focus=projection_focus,
        debug_depth=int(DEFAULT_SHARE_RENDER["debug_depth"]),
        vision_extras=vision_extras if vision_extras is not None else DEFAULT_SHARE_RENDER["vision_extras"],
        council_scope=council_scope or DEFAULT_SHARE_RENDER["council_scope"],
        higher_timeframe=str(DEFAULT_SHARE_RENDER["higher_timeframe"]),
        lower_timeframe=str(DEFAULT_SHARE_RENDER["lower_timeframe"]),
    )


def _build_share_adaptive_guidance_html(result: Mapping[str, Any] | None) -> str:
    if not result:
        return pg._placeholder_panel("Adaptive Guidance", "The desk will guide the next best panel after the first signal run.")
    multi_timeframe = cast(dict[str, Any], result.get("multi_timeframe", {}))
    zone_learning = cast(dict[str, Any], result.get("zone_learning", {}))
    confidence = float(result.get("confidence", 0.0) or 0.0)
    recommended_panel = "Model Council"
    rationale = "Heavyweight council review is the fastest way to sharpen conviction on the current shared signal."
    tone = "soft"
    if multi_timeframe and not bool(multi_timeframe.get("aligned", False)):
        recommended_panel = "Compare Desk"
        rationale = "Higher and lower timeframe structure are disagreeing, so split compare is the cleanest next read."
        tone = "amber"
    elif 0.46 <= confidence <= 0.68:
        recommended_panel = "Confidence Heatmap"
        rationale = "The read is tradable but not decisive, so hotspot concentration is the best next visual filter."
        tone = "amber"
    elif int(zone_learning.get("match_count", 0) or 0) > 0:
        recommended_panel = "Timeframe Overlays"
        rationale = "Taught structural zones are intersecting the chart, so overlay context is the highest-value follow-up."
        tone = "teal"
    elif confidence >= 0.78:
        recommended_panel = "Feedback Feed"
        rationale = "This is a high-conviction read. After the outcome plays out, feedback will keep the live learner honest."
        tone = "teal"
    return (
        "<div class='pg-live-panel'>"
        "<div class='pg-section-title'>Adaptive Guidance</div>"
        f"<div class='pg-chip-row'>{pg._chip(f'Open {recommended_panel}', tone)}</div>"
        f"<div class='pg-muted'>{html.escape(rationale)}</div>"
        "</div>"
    )


def _build_share_launch_console_html() -> str:
    return (
        "<div class='pg-panel'>"
        "<div class='pg-section-title'>Premium Launch Console</div>"
        "<div class='pg-status-grid'>"
        f"{pg._status_card('Advanced Feed', 'Active', 'Outcome review and learning telemetry are visible from launch.', 'teal')}"
        f"{pg._status_card('Zone Studio', 'Active', 'Saved zones and structural memory are ready for reuse.', 'soft')}"
    f"{pg._status_card('Super Powers', 'Ready', 'Open the dedicated lab to inspect timing, regime, counterfactual, memory, and explanation layers.', 'teal')}"
        f"{pg._status_card('Timeframe Overlays', 'Armed', 'Higher and lower timeframe overlays will render after the first quartet.', 'amber')}"
        f"{pg._status_card('RL Loop', 'Live', 'Server-side learning and feedback review are updating from the first run.', 'teal' if SHARE_ENABLE_LEARNING_MUTATIONS else 'soft')}"
        "</div>"
        "</div>"
    )


def _build_share_launch_timeframe_html() -> str:
    return (
        "<div class='pg-panel'>"
        "<div class='pg-section-title'>Timeframe Overlays</div>"
        "<div class='pg-status-grid'>"
        f"{pg._status_card('Higher timeframe', 'Armed', 'The higher timeframe pair is ready to render as soon as the first quartet arrives.', 'soft')}"
        f"{pg._status_card('Lower timeframe', 'Armed', 'The lower timeframe pair will stay fused with the premium review surface.', 'teal')}"
        "</div>"
        "<div class='pg-muted' style='margin-top:10px;'>Upload the quartet to open the live overlay gallery and fusion view.</div>"
        "</div>"
    )


def _share_super_power_result_view(result: Mapping[str, Any] | None) -> dict[str, Any]:
  if not result:
    return {}
  sanitized = pg._sanitize_result_for_ui(dict(result))
  return cast(dict[str, Any], pg._ensure_active_trade_overlay(cast(dict[str, Any], sanitized)))


def _share_regime_snapshot(result_view: Mapping[str, Any]) -> dict[str, Any]:
  chart_state = cast(dict[str, Any], result_view.get("chart_state", {}))
  trend_regime = cast(dict[str, Any], result_view.get("trend_regime", {}))
  market_state_raw = str(
    result_view.get(
      "market_state",
      chart_state.get("market_state", trend_regime.get("market_state", "UNSPECIFIED")),
    )
  ).upper()
  trend_phase = str(trend_regime.get("trend_phase", "transition")).lower()
  trend_direction = str(trend_regime.get("trend_direction", result_view.get("directional_intent", "HOLD"))).upper()
  trend_strength = float(np.clip(trend_regime.get("trend_strength", 0.0), 0.0, 1.0))
  continuation_reload_score = float(np.clip(trend_regime.get("continuation_reload_score", 0.0), 0.0, 1.0))
  impulse_extension = float(np.clip(trend_regime.get("impulse_extension", 0.0), 0.0, 1.0))
  reversal_risk = float(np.clip(trend_regime.get("reversal_risk", 0.0), 0.0, 1.0))
  breakout_failure_risk = float(np.clip(trend_regime.get("breakout_failure_risk", 0.0), 0.0, 1.0))
  pullback_active = bool(trend_regime.get("pullback_active", False))
  rejection_pressure = float(np.clip(trend_regime.get("rejection_pressure", 0.0), 0.0, 1.0))
  counter_run_len = int(max(0, int(trend_regime.get("counter_run_len", 0) or 0)))

  if market_state_raw == "NEWS_SPIKE":
    label = "NEWS SHOCK"
    tone = "sell"
    note = "Freeze new entries until the shock cools and the market normalizes."
    threshold_action = "Pause fresh entries and wait for volatility to settle."
  elif market_state_raw == "BREAKOUT" or impulse_extension >= 0.52 or continuation_reload_score >= 0.58:
    label = "EXPANSION"
    tone = "teal"
    note = "Expansion is active. Keep continuation thresholds responsive while timing stays disciplined."
    threshold_action = "Allow faster continuation triggers while keeping invalidation disciplined."
  elif market_state_raw == "RANGING" or trend_phase == "range_coil" or (trend_strength < 0.35 and rejection_pressure >= 0.35):
    label = "RANGE"
    tone = "amber"
    note = "The market is coiling. Require more confirmation before calling the move directional."
    threshold_action = "Raise the confidence floor and wait for a cleaner multi-timeframe break."
  elif market_state_raw == "CHOPPY" or (rejection_pressure >= 0.45 and counter_run_len >= 2) or (reversal_risk >= 0.48 and breakout_failure_risk >= 0.35):
    label = "CHOP"
    tone = "amber"
    note = "Chop is active. False starts are more likely, so the setup needs tighter confirmation."
    threshold_action = "Slow the trigger, widen invalidation awareness, and reject weak continuation."
  elif market_state_raw == "REVERSAL" or trend_phase == "reversal_watch" or reversal_risk >= 0.48 or breakout_failure_risk >= 0.48:
    label = "REVERSAL"
    tone = "sell"
    note = "Reversal pressure is elevated. Prefer caution unless confirmation is strong."
    threshold_action = "Demand stronger confirmation and reduce confidence in continuation bias."
  elif trend_phase in {"trend_impulse", "trend_extension", "trend_pullback"} and trend_strength >= 0.40:
    label = "TREND"
    tone = "teal"
    note = "Trend structure is intact. Continuation can stay active while pullbacks are monitored."
    threshold_action = "Keep continuation thresholds live and use timing to separate pullback from exhaustion."
  else:
    label = "TRANSITION"
    tone = "soft"
    note = "The regime is still transitioning. Wait for the next clean alignment."
    threshold_action = "Stay selective and wait for clearer regime confirmation."

  market_state_label = {
    "TREND": "TRENDING",
    "RANGE": "RANGING",
    "CHOP": "CHOPPY",
    "EXPANSION": "BREAKOUT",
    "REVERSAL": "REVERSAL",
    "NEWS SHOCK": "NEWS_SPIKE",
    "TRANSITION": "UNSPECIFIED",
  }[label]
  if label in {"TREND", "EXPANSION"}:
    setup_state = "CONTINUATION"
  elif label == "REVERSAL":
    setup_state = "REVERSAL"
  elif label == "NEWS SHOCK":
    setup_state = "LIQUIDITY_SWEEP"
  elif label == "CHOP":
    setup_state = "FAKEOUT"
  elif label == "RANGE":
    setup_state = "PULLBACK"
  else:
    setup_state = "UNSPECIFIED"
  return {
    "label": label,
    "tone": tone,
    "note": note,
    "threshold_action": threshold_action,
    "market_state": market_state_label,
    "setup_state": setup_state,
    "trend_phase": trend_phase,
    "trend_direction": trend_direction,
    "trend_strength": trend_strength,
    "continuation_reload_score": continuation_reload_score,
    "impulse_extension": impulse_extension,
    "reversal_risk": reversal_risk,
    "breakout_failure_risk": breakout_failure_risk,
    "pullback_active": pullback_active,
    "rejection_pressure": rejection_pressure,
    "counter_run_len": counter_run_len,
  }


def _share_echo_case_score(
  row: Mapping[str, Any],
  *,
  current_action: str,
  current_market_state: str,
  current_setup_state: str,
  current_timing_state: str,
) -> float:
  score = 0.0
  row_signal_direction = str(row.get("signal_direction", row.get("inference_action", "HOLD"))).upper()
  row_actual_outcome = str(row.get("actual_outcome", row.get("verdict", "HOLD"))).upper()
  row_market_state = str(row.get("market_state", "UNSPECIFIED")).upper()
  row_setup_state = str(row.get("setup_state", "UNSPECIFIED")).upper()
  row_failure_mode = str(row.get("failure_mode", "NONE")).upper()

  if row_signal_direction == current_action:
    score += 0.35
  if row_actual_outcome == current_action:
    score += 0.10
  if row_market_state == current_market_state:
    score += 0.22
  if row_setup_state == current_setup_state:
    score += 0.18
  if current_timing_state == "LATE" and row_failure_mode == "LATE_ENTRY":
    score += 0.09
  elif current_timing_state == "PREMATURE" and row_failure_mode in {"FAKEOUT", "LATE_ENTRY"}:
    score += 0.07
  elif current_timing_state == "WATCH" and row_failure_mode in {"LOW_MOMENTUM", "COUNTERTREND"}:
    score += 0.06
  elif current_timing_state == "READY" and row_actual_outcome == row_signal_direction:
    score += 0.05
  if row_failure_mode == "NONE" and row_actual_outcome == row_signal_direction:
    score += 0.03
  return float(score)


def _build_share_super_powers_overview_html(result: Mapping[str, Any] | None) -> str:
  if not result:
    return pg._placeholder_panel(
      "Super Powers Lab",
      "Click Refresh Super Powers after the first quartet lands to bind timing, regime, memory, scenario, and explanation lenses to the active setup.",
    )
  result_view = _share_super_power_result_view(result)
  escape_html = pg._escape_html
  fmt_num = pg._fmt_num
  timing_signal = cast(dict[str, Any], result_view.get("timing_signal", {}))
  regime = _share_regime_snapshot(result_view)
  explanation_lines = pg._public_signal_explanation_lines(str(result_view.get("explanation", "")), limit=2)
  chips = "".join(
    [
      pg._chip(
        f"Timing {str(timing_signal.get('entry_state', 'WATCH')).upper()}",
        "teal" if str(timing_signal.get("entry_state", "WATCH")).upper() == "READY" else "amber",
      ),
      pg._chip(f"Regime {regime['label']}", str(regime["tone"])),
      pg._chip(f"Memory {fmt_num(result_view.get('memory_similarity', 0.0), 3)}", "soft"),
      pg._chip(f"Window {fmt_num(timing_signal.get('timing_score', 0.0), 2)}", "teal" if float(timing_signal.get("timing_score", 0.0) or 0.0) >= 0.64 else "amber"),
      pg._chip(
        f"Review {str(result_view.get('execution_permission', 'WAIT_FOR_CONFIRMATION')).replace('_', ' ').title()}",
        "teal" if str(result_view.get("execution_permission", "WAIT_FOR_CONFIRMATION")).upper() == "EXECUTE" else "amber",
      ),
    ]
  )
  summary_lines = "".join(f"<li>{escape_html(line)}</li>" for line in explanation_lines)
  return (
    "<div class='pg-panel'>"
    "<div class='pg-section-title'>Super Powers Lab</div>"
    f"<div class='pg-chip-row'>{chips}</div>"
    "<div class='pg-muted' style='margin-top:10px;'>Click Refresh Super Powers after each new run to rebind the lenses to the latest quartet.</div>"
    "<div class='pg-brief-section' style='margin-top:12px;'>"
    "<div class='pg-card-label'>What this component adds</div>"
    "<ul class='pg-brief-list'>"
    "<li>Entry Window Engine for timing, expiry, and setup windows.</li>"
    "<li>Regime Radar for trend, range, chop, expansion, and shock detection.</li>"
    "<li>Counterfactual Lab for earlier, later, and skip-the-trade scenarios.</li>"
    "<li>Pattern Memory Echoes for similar historical setups and failure modes.</li>"
    "<li>Explainability Layer for plain-English reasons and invalidation context.</li>"
    "</ul>"
    "</div>"
    "<div class='pg-brief-section' style='margin-top:12px;'>"
    "<div class='pg-card-label'>Live signal summary</div>"
    f"<ul class='pg-brief-list'>{summary_lines}</ul>"
    "</div>"
    f"<div class='pg-muted' style='margin-top:12px;'>Regime: {escape_html(regime['label'])} | Threshold shift: {escape_html(regime['threshold_action'])}</div>"
    "</div>"
  )


def _build_share_super_power_entry_window_html(result: Mapping[str, Any] | None) -> str:
  if not result:
    return pg._placeholder_panel(
      "Entry Window Engine",
      "Run a signal to open the live timing, expiry, and safe-entry window for the current setup.",
    )
  return pg.build_timing_playbook_html(_share_super_power_result_view(result))


def _build_share_super_power_regime_radar_html(result: Mapping[str, Any] | None) -> str:
  if not result:
    return pg._placeholder_panel(
      "Regime Radar",
      "Run a signal to classify trend, range, chop, expansion, or news shock and adjust thresholds automatically.",
    )
  result_view = _share_super_power_result_view(result)
  fmt_num = pg._fmt_num
  metric_tile = pg._metric_tile
  escape_html = pg._escape_html
  tone_class_for_action = pg._tone_class_for_action
  regime = _share_regime_snapshot(result_view)
  chips = "".join(
    [
      pg._chip(f"{regime['label']}", str(regime["tone"])),
      pg._chip(f"Phase {str(regime['trend_phase']).replace('_', ' ').title()}", "soft"),
      pg._chip(f"Direction {str(regime['trend_direction']).upper()}", tone_class_for_action(str(regime["trend_direction"]).upper())),
      pg._chip(f"Strength {fmt_num(regime['trend_strength'], 2)}", "teal" if float(regime["trend_strength"]) >= 0.50 else "amber"),
      pg._chip(f"Reload {fmt_num(regime['continuation_reload_score'], 2)}", "soft"),
      pg._chip(f"Reversal {fmt_num(regime['reversal_risk'], 2)}", "sell" if float(regime["reversal_risk"]) >= 0.48 else "soft"),
    ]
  )
  tiles = "".join(
    [
      metric_tile("Breakout Risk", fmt_num(regime["breakout_failure_risk"], 2)),
      metric_tile("Counter-run", str(int(regime["counter_run_len"]))),
      metric_tile("Pullback", "Active" if bool(regime["pullback_active"]) else "Off"),
      metric_tile("Rejection Pressure", fmt_num(regime["rejection_pressure"], 2)),
      metric_tile("Market State", str(regime["market_state"])),
      metric_tile("Setup State", str(regime["setup_state"])),
    ]
  )
  return (
    "<div class='pg-panel'>"
    "<div class='pg-section-title'>Regime Radar</div>"
    f"<div class='pg-chip-row'>{chips}</div>"
    f"<div class='pg-muted' style='margin-top:10px;'>{escape_html(str(regime['note']))}</div>"
    f"<div class='pg-metric-grid' style='margin-top:12px;'>{tiles}</div>"
    "<div class='pg-brief-section' style='margin-top:12px;'>"
    "<div class='pg-card-label'>Automatic threshold shift</div>"
    f"<div class='pg-muted'>{escape_html(str(regime['threshold_action']))}</div>"
    "</div>"
    "</div>"
  )


def _build_share_super_power_counterfactual_html(result: Mapping[str, Any] | None) -> str:
  if not result:
    return pg._placeholder_panel(
      "Counterfactual Lab",
      "Run a signal to see the one-candle-earlier, one-candle-later, and skip-the-trade scenario lens.",
    )
  result_view = _share_super_power_result_view(result)
  fmt_num = pg._fmt_num
  escape_html = pg._escape_html
  timing_signal = cast(dict[str, Any], result_view.get("timing_signal", {}))
  regime = _share_regime_snapshot(result_view)
  eta_candles = cast(dict[str, Any], timing_signal.get("eta_candles", {}))
  eta_mid = float(np.clip(eta_candles.get("mid", timing_signal.get("projected_candle_count", 0.0)), 0.0, 24.0))
  timeframe_minutes = float(np.clip(timing_signal.get("timeframe_minutes", 5.0) or 5.0, 1.0, 240.0))
  expiry_minutes = float(np.clip(timing_signal.get("expiry_minutes", 60.0) or 60.0, timeframe_minutes, 240.0))
  buffer_minutes = float(np.clip(timing_signal.get("entry_buffer_minutes", 8.0) or 8.0, 0.0, expiry_minutes))
  safe_window_minutes = max(expiry_minutes - buffer_minutes, timeframe_minutes)
  safe_window_candles = safe_window_minutes / max(timeframe_minutes, 1.0)
  entry_state = str(timing_signal.get("entry_state", "WATCH")).upper()
  timing_score = float(np.clip(timing_signal.get("timing_score", 0.0), 0.0, 1.0))
  earlier_eta = max(eta_mid - 1.0, 0.0)
  later_eta = eta_mid + 1.0

  def _scenario_note(candidate_eta: float, label: str) -> tuple[str, str]:
    if label == "Skip the trade":
      if regime["label"] in {"CHOP", "RANGE", "NEWS SHOCK"} or entry_state in {"LATE", "PREMATURE"}:
        return ("Capital preserved", "Skipping is favored because the regime is not clean enough to justify exposure.")
      return ("Optional flat", "Skipping preserves capital, but it also gives up the edge if the move is already forming.")
    if candidate_eta > safe_window_candles:
      return ("Too late", "The entry would likely expire before the timing window completes.")
    if candidate_eta < 1.0:
      return ("Premature", "The setup is early enough that fakeout risk rises and confirmation weakens.")
    if entry_state == "READY":
      return ("Still viable", "The current timing window likely remains open.")
    if entry_state == "WATCH":
      return ("Still waiting", "Structure is still not clean enough to justify the trigger yet.")
    return ("Mixed", "The move may still exist, but the timing edge is weaker.")

  scenario_cards: list[str] = []
  for title, candidate_eta, tone in [
    ("Earlier by 1 candle", earlier_eta, "amber"),
    ("Later by 1 candle", later_eta, "teal"),
    ("Skip the trade", eta_mid, "soft"),
  ]:
    outcome_label, note = _scenario_note(candidate_eta, title)
    scenario_cards.append(
      "<div class='pg-memory-card'>"
      f"<div class='pg-card-label'>{escape_html(title)}</div>"
      f"<div class='pg-card-title pg-{tone}'>{escape_html(outcome_label)}</div>"
      f"<div class='pg-card-note'>scenario ETA {fmt_num(candidate_eta, 1)} candles | safe window {fmt_num(safe_window_candles, 1)} candles</div>"
      f"<div class='pg-card-note'>{escape_html(note)}</div>"
      "</div>"
    )

  chips = "".join(
    [
      pg._chip(f"Timing {entry_state}", "teal" if entry_state == "READY" else "amber"),
      pg._chip(f"ETA {fmt_num(eta_mid, 1)}c", "soft"),
      pg._chip(f"Window {fmt_num(safe_window_candles, 1)}c", "soft"),
      pg._chip(f"Score {fmt_num(timing_score, 2)}", "teal" if timing_score >= 0.64 else "amber"),
      pg._chip(f"Regime {regime['label']}", str(regime["tone"])),
    ]
  )
  return (
    "<div class='pg-panel'>"
    "<div class='pg-section-title'>Counterfactual Lab</div>"
    f"<div class='pg-chip-row'>{chips}</div>"
    "<div class='pg-muted' style='margin-top:10px;'>This is a scenario lens, not a historical backtest. It estimates what the entry would look like one candle earlier, one candle later, or skipped entirely.</div>"
    f"<div class='pg-memory-grid' style='margin-top:14px;'>{''.join(scenario_cards)}</div>"
    "</div>"
  )


def _build_share_super_power_pattern_echoes_html(result: Mapping[str, Any] | None) -> str:
  if not result:
    return pg._placeholder_panel(
      "Pattern Memory Echoes",
      "Run a signal to pull the closest historical setups, cluster win rate, and dominant failure mode.",
    )
  result_view = _share_super_power_result_view(result)
  fmt_pct01 = pg._fmt_pct01
  escape_html = pg._escape_html
  timing_signal = cast(dict[str, Any], result_view.get("timing_signal", {}))
  regime = _share_regime_snapshot(result_view)
  current_action = str(result_view.get("execution_action", result_view.get("action", "HOLD"))).upper()
  current_timing_state = str(timing_signal.get("entry_state", "WATCH")).upper()
  rows = pg._feedback_target_entries(limit=120)
  if not rows:
    return pg._placeholder_panel(
      "Pattern Memory Echoes",
      "No reviewed outcomes are available yet. Submit feedback to populate the echo trail.",
    )
  scored_rows = sorted(
    rows,
    key=lambda row: _share_echo_case_score(
      row,
      current_action=current_action,
      current_market_state=str(regime["market_state"]),
      current_setup_state=str(regime["setup_state"]),
      current_timing_state=current_timing_state,
    ),
    reverse=True,
  )[:4]
  wins = sum(1 for row in scored_rows if str(row.get("signal_direction", "HOLD")).upper() == str(row.get("actual_outcome", "HOLD")).upper())
  win_rate = float(wins / max(len(scored_rows), 1))
  failure_counts: dict[str, int] = {}
  for row in scored_rows:
    failure_mode = str(row.get("failure_mode", "NONE")).upper()
    failure_counts[failure_mode] = int(failure_counts.get(failure_mode, 0)) + 1
  dominant_failure = max(failure_counts.items(), key=lambda item: item[1])[0] if failure_counts else "NONE"
  chips = "".join(
    [
      pg._chip(f"Win rate {fmt_pct01(win_rate)}", "teal" if win_rate >= 0.55 else "amber"),
      pg._chip(f"Timing {current_timing_state}", "teal" if current_timing_state == "READY" else "amber"),
      pg._chip(f"Dominant failure {dominant_failure.replace('_', ' ')}", "sell" if dominant_failure not in {"NONE", "UNSPECIFIED"} else "soft"),
      pg._chip(f"Matches {len(scored_rows)}", "soft"),
    ]
  )
  cards: list[str] = []
  for row in scored_rows:
    signal_direction = str(row.get("signal_direction", row.get("inference_action", "HOLD"))).upper()
    actual_outcome = str(row.get("actual_outcome", "HOLD")).upper()
    failure_mode = str(row.get("failure_mode", "NONE")).upper()
    cards.append(
      "<div class='pg-memory-card'>"
      f"<div class='pg-card-label'>{escape_html(str(row.get('timestamp', 'unknown')))}</div>"
      f"<div class='pg-card-title'>{escape_html(signal_direction)} → {escape_html(actual_outcome)}</div>"
      f"<div class='pg-card-note'>market={escape_html(str(row.get('market_state', 'UNSPECIFIED')).upper())} | setup={escape_html(str(row.get('setup_state', 'UNSPECIFIED')).upper())}</div>"
      f"<div class='pg-card-note'>failure mode: {escape_html(failure_mode)}</div>"
      "</div>"
    )
  return (
    "<div class='pg-panel'>"
    "<div class='pg-section-title'>Pattern Memory Echoes</div>"
    f"<div class='pg-chip-row'>{chips}</div>"
    "<div class='pg-muted' style='margin-top:10px;'>The cluster is ranked by signal direction, market state, setup state, and timing proximity. Win rate is measured from the matched cluster, not from a synthetic score.</div>"
    f"<div class='pg-memory-grid' style='margin-top:14px;'>{''.join(cards)}</div>"
    f"{pg._build_pattern_memory_browser_html(result_view)}"
    "</div>"
  )


def _build_share_super_power_explainability_html(result: Mapping[str, Any] | None) -> str:
  if not result:
    return pg._placeholder_panel(
      "Explainability Layer",
      "Run a signal to see the plain-English reason, invalidation line, and strongest conflicting evidence.",
    )
  result_view = _share_super_power_result_view(result)
  fmt_num = pg._fmt_num
  fmt_pct01 = pg._fmt_pct01
  escape_html = pg._escape_html
  public_signal_explanation_lines = pg._public_signal_explanation_lines
  interpreter = pg._build_interpreter_fusion_payload(result_view)
  context = cast(dict[str, Any], interpreter.get("context", {}))
  gates = cast(dict[str, Any], interpreter.get("gates", {}))
  forecast = cast(dict[str, Any], interpreter.get("forecast", {}))
  reason_lines = public_signal_explanation_lines(str(result_view.get("explanation", "")), limit=3)
  invalidation = str(context.get("invalidation", ""))
  risk_factors = [str(item) for item in cast(list[Any], context.get("risk_factors", [])) if str(item).strip()]
  gate_blockers = [str(item) for item in cast(list[Any], gates.get("blockers", [])) if str(item).strip()]
  support_blockers = [str(item) for item in cast(list[Any], gates.get("support_blockers", [])) if str(item).strip()]
  conflict_lines = risk_factors[:3] or gate_blockers[:3] or support_blockers[:3] or ["No major conflict surfaced."]
  if gate_blockers or support_blockers:
    invalidation_level = "High"
    tone = "sell"
  elif float(forecast.get("execution_readiness", 0.0) or 0.0) < 0.55:
    invalidation_level = "Moderate"
    tone = "amber"
  else:
    invalidation_level = "Low"
    tone = "teal"
  chips = "".join(
    [
      pg._chip(f"Invalidation {invalidation_level}", tone),
      pg._chip(f"Execution {fmt_num(forecast.get('execution_readiness', 0.0), 2)}", "soft"),
      pg._chip(f"Memory {fmt_num(cast(dict[str, Any], interpreter.get('memory', {})).get('similarity', 0.0), 3)}", "soft"),
      pg._chip(f"Confidence {fmt_pct01(result_view.get('confidence', 0.0))}", "teal" if float(result_view.get("confidence", 0.0) or 0.0) >= 0.75 else "amber"),
    ]
  )
  reason_html = "".join(f"<li>{escape_html(line)}</li>" for line in reason_lines)
  conflict_html = "".join(f"<li>{escape_html(line)}</li>" for line in conflict_lines)
  return (
    "<div class='pg-panel'>"
    "<div class='pg-section-title'>Explainability Layer</div>"
    f"<div class='pg-chip-row'>{chips}</div>"
    "<div class='pg-brief-section' style='margin-top:12px;'>"
    "<div class='pg-card-label'>Plain-English reason</div>"
    f"<ul class='pg-brief-list'>{reason_html}</ul>"
    "</div>"
    "<div class='pg-brief-section' style='margin-top:12px;'>"
    "<div class='pg-card-label'>Invalidation line</div>"
    f"<div class='pg-muted'>{escape_html(invalidation)}</div>"
    "</div>"
    "<div class='pg-brief-section' style='margin-top:12px;'>"
    "<div class='pg-card-label'>Strongest conflicting evidence</div>"
    f"<ul class='pg-brief-list'>{conflict_html}</ul>"
    "</div>"
    "</div>"
  )


def load_share_super_powers(session_id: str, request: gr.Request | None = None) -> tuple[str, str, str, str, str, str]:
  session = _get_share_session(session_id, create=True)
  assert session is not None
  _enforce_rate_limit(
    "preview",
    request=request,
    session_id=session.session_id,
    max_calls=SHARE_PREVIEW_RATE_LIMIT,
    window_sec=SHARE_PREVIEW_RATE_WINDOW_SEC,
    user_message="Super power refreshes are arriving too quickly.",
  )
  result_view = _share_super_power_result_view(session.result)
  _log_share_event(
    "info",
    "super_powers_opened",
    session_id=session.session_id,
    has_result=bool(result_view),
    **_request_audit_fields(request, session.session_id),
  )
  return (
    _build_share_super_powers_overview_html(result_view),
    _build_share_super_power_entry_window_html(result_view),
    _build_share_super_power_regime_radar_html(result_view),
    _build_share_super_power_counterfactual_html(result_view),
    _build_share_super_power_pattern_echoes_html(result_view),
    _build_share_super_power_explainability_html(result_view),
  )


def _render_share_outputs(
    result: dict[str, Any],
    source_image_state: Any,
    render_config: Mapping[str, Any],
    *,
    status_message: str,
    notification_event: str = "",
) -> tuple[Any, ...]:
    source_image = pg._image_from_state(source_image_state)
    if source_image is None:
        return _empty_share_outputs()
    display_result = pg._sanitize_result_for_ui(result)
    # Draw overlay on the original source image, not on any cropped version
    # This ensures coordinates are correct and display matches the full chart
    overlay = pg._build_overlay_image(
        source_image,
        result,
        overlay_mode=str(render_config.get("overlay_mode", DEFAULT_SHARE_RENDER["overlay_mode"])),
        min_conf_global=float(render_config.get("min_conf_global", DEFAULT_SHARE_RENDER["min_conf_global"])),
        min_conf_latest=float(render_config.get("min_conf_latest", DEFAULT_SHARE_RENDER["min_conf_latest"])),
        history_limit=int(render_config.get("history_depth", DEFAULT_SHARE_RENDER["history_depth"])),
        label_budget=int(render_config.get("label_density", DEFAULT_SHARE_RENDER["label_density"])),
        projection_confidence_floor=float(render_config.get("projection_focus", DEFAULT_SHARE_RENDER["projection_focus"])),
        vision_extras=render_config.get("vision_extras", DEFAULT_SHARE_RENDER["vision_extras"]),
    )
    gauge = pg._build_decision_gauge_from_result(display_result)
    heatmap_payload = pg._build_confidence_heatmap_payload(display_result, source_image)
    heatmap_image = pg._compose_confidence_heatmap_image(heatmap_payload, source_image)
    return (
        overlay,
        gauge,
        pg.build_signal_overview_html(display_result),
        pg.build_forecast_panel_html(display_result),
      pg._build_timeframe_overlay_gallery_html(display_result, source_image_state),
        pg.build_timing_playbook_html(display_result),
        heatmap_image,
        pg._build_heatmap_summary_html(display_result, source_image, heatmap_payload=heatmap_payload),
        pg._build_compare_desk_html(display_result, source_image, overlay, heatmap_image, render_config=render_config),
        _build_share_adaptive_guidance_html(display_result),
        pg.build_model_council_html(display_result),
        _share_status_html(
            status_message,
            result=display_result,
            render_config=render_config,
            notification_event=notification_event,
        ),
    )


def _render_share_session(session: ShareSession, *, status_message: str, notification_event: str = "") -> tuple[Any, ...]:
    if session.result is None or session.source_image_state is None:
        return _empty_share_outputs(session.session_id)
    return (
        *_render_share_outputs(
            session.result,
            session.source_image_state,
            session.render_config,
            status_message=status_message,
            notification_event=notification_event,
        ),
        session.session_id,
    )


def _generic_user_error(
    message: str,
    exc: Exception | None = None,
    *,
    stage: str,
    session_id: str | None = None,
    **fields: Any,
) -> None:
    _record_share_error(stage, message, exc, session_id=session_id, **fields)
    raise gr.Error(message)


def _enforce_rate_limit(
    action: str,
    *,
    request: gr.Request | None,
    session_id: str | None,
    max_calls: int,
    window_sec: int,
    user_message: str,
) -> None:
    identity = _request_identity(request, session_id)
    key = f"{action}:{identity['identity']}"
    now = time.time()
    with _share_rate_limit_lock:
        history = [
            ts
            for ts in _share_rate_limit_state.get(key, [])
            if (now - float(ts)) < float(window_sec)
        ]
        if len(history) >= max_calls:
            retry_after = max(1, int(window_sec - (now - float(history[0]))))
            _share_rate_limit_state[key] = history
            _log_share_event(
                "warning",
                "rate_limited",
                session_id=session_id,
                action=action,
                limit=max_calls,
                window_sec=window_sec,
                retry_after=retry_after,
                **_request_audit_fields(request, session_id),
            )
            raise gr.Error(f"{user_message} Please wait about {retry_after} seconds and try again.")
        history.append(now)
        _share_rate_limit_state[key] = history


def _validate_share_upload_paths(
    upload_paths: list[str],
    *,
    request: gr.Request | None,
    session_id: str | None,
    min_files: int = SHARE_MAX_UPLOAD_FILES,
    max_files: int = SHARE_MAX_UPLOAD_FILES,
  invalid_message: str = "Upload exactly four chart images: two higher timeframe views first, then two lower timeframe views.",
) -> list[str]:
    if len(upload_paths) < min_files or len(upload_paths) > max_files:
        raise gr.Error(invalid_message)

    validated: list[str] = []
    total_bytes = 0
    for raw_path in upload_paths[:max_files]:
        candidate = Path(str(raw_path or "").strip())
        try:
            resolved = candidate.resolve(strict=True)
        except Exception as exc:
            _generic_user_error(
                "One of the uploaded files could not be resolved on the server.",
                exc,
                stage="upload_resolve",
                session_id=session_id,
                **_request_audit_fields(request, session_id),
            )
        if not resolved.is_file():
            raise gr.Error("One of the uploaded files is invalid.")
        if resolved.suffix.lower() not in SHARE_ALLOWED_IMAGE_EXTS:
            _log_share_event(
                "warning",
                "upload_rejected_extension",
                session_id=session_id,
                extension=resolved.suffix.lower(),
                **_request_audit_fields(request, session_id),
            )
            raise gr.Error("Only standard chart image formats are allowed.")
        stat = resolved.stat()
        size_bytes = int(stat.st_size)
        total_bytes += size_bytes
        if size_bytes <= 0 or size_bytes > SHARE_MAX_UPLOAD_BYTES:
            _log_share_event(
                "warning",
                "upload_rejected_size",
                session_id=session_id,
                size_bytes=size_bytes,
                max_size_bytes=SHARE_MAX_UPLOAD_BYTES,
                **_request_audit_fields(request, session_id),
            )
            raise gr.Error("One of the uploaded images is too large for this shared server.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(resolved) as image_probe:
                    image_probe.verify()
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(resolved) as image_probe:
                    width, height = image_probe.size
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            _generic_user_error(
                "One of the uploaded files is not a safe image.",
                exc,
                stage="upload_validate",
                session_id=session_id,
                file_name=resolved.name,
                **_request_audit_fields(request, session_id),
            )
        if (
            width < SHARE_MIN_IMAGE_EDGE
            or height < SHARE_MIN_IMAGE_EDGE
            or width > SHARE_MAX_IMAGE_EDGE
            or height > SHARE_MAX_IMAGE_EDGE
            or (width * height) > SHARE_MAX_IMAGE_PIXELS
        ):
            _log_share_event(
                "warning",
                "upload_rejected_dimensions",
                session_id=session_id,
                width=width,
                height=height,
                max_pixels=SHARE_MAX_IMAGE_PIXELS,
                max_edge=SHARE_MAX_IMAGE_EDGE,
                **_request_audit_fields(request, session_id),
            )
            raise gr.Error("One of the uploaded images is outside the allowed size limits for this server.")
        validated.append(str(resolved))

    _log_share_event(
        "info",
        "upload_validated",
        session_id=session_id,
        file_count=len(validated),
        total_bytes=total_bytes,
        **_request_audit_fields(request, session_id),
    )
    return validated


def _analyze_share_bundle(
    upload_paths: list[str],
    render_config: Mapping[str, Any],
    *,
    use_local_ensemble: bool | None = None,
    side_effect_free: bool = False,
) -> tuple[dict[str, Any], Any, str]:
    labels = pg._multi_timeframe_bundle_labels(len(upload_paths))
    higher_timeframe = str(render_config.get("higher_timeframe", "M15") or "M15").upper()
    lower_timeframe = str(render_config.get("lower_timeframe", "M5") or "M5").upper()
    if len(upload_paths) >= 4:
        timeframe_overrides = [higher_timeframe, higher_timeframe, lower_timeframe, lower_timeframe]
    elif len(upload_paths) == 2:
        timeframe_overrides = [higher_timeframe, lower_timeframe]
    else:
        timeframe_overrides = [higher_timeframe] * max(1, len(upload_paths))
    analyzed: list[dict[str, Any]] = []
    for index, file_path in enumerate(upload_paths):
        result, overlay_image, _gauge_unused, _skill_unused = pg.pg_main.run_inference(
            file_path,
            annotation_text="",
            overlay_mode=str(render_config["overlay_mode"]),
            min_conf_global=float(render_config["min_conf_global"]),
            min_conf_latest=float(render_config["min_conf_latest"]),
            history_depth=int(render_config["history_depth"]),
            label_density=int(render_config["label_density"]),
            projection_focus=float(render_config["projection_focus"]),
            side_effect_free=side_effect_free,
            use_local_ensemble=use_local_ensemble,
            vision_extras=render_config.get("vision_extras", pg.DEFAULT_VISION_EXTRAS),
            council_scope=str(render_config.get("council_scope", pg.DEFAULT_COUNCIL_SCOPE)),
            timeframe_override=timeframe_overrides[min(index, len(timeframe_overrides) - 1)],
        )
        source_image_state = pg._source_image_to_state(file_path)
        analyzed.append(
            {
                "result": result,
                "file_path": file_path,
                "source_image_state": source_image_state,
                "compare_entry": pg._build_timeframe_compare_entry(
                    result,
                    source_image_state,
                    file_path,
                    labels[min(index, len(labels) - 1)],
                    overlay_image=overlay_image,
                    render_config=render_config,
                ),
            }
        )
    bundle_result = (
        pg._build_multi_timeframe_result(analyzed)
        if len(analyzed) > 1
        else cast(dict[str, Any], analyzed[0]["result"])
    )
    source_image_state = analyzed[-1]["source_image_state"]
    active_file_path = str(analyzed[-1]["file_path"])
    return bundle_result, source_image_state, active_file_path


def run_share_signal(
    session_id: str,
    file_obj: Any,
    overlay_mode: str,
    vision_extras: Any,
    council_scope: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
    request: gr.Request | None = None,
) -> tuple[Any, ...]:
    session = _get_share_session(session_id, create=True)
    assert session is not None
    upload_paths = pg._uploaded_file_paths(file_obj)
    if not upload_paths:
        return _empty_share_outputs(session.session_id)
    if len(upload_paths) != SHARE_MAX_UPLOAD_FILES:
        _log_share_event("warning", "signal_rejected_bad_bundle", session_id=session.session_id, file_count=len(upload_paths))
        raise gr.Error("Upload exactly four chart images: two higher timeframe views first, then two lower timeframe views.")
    _enforce_rate_limit(
        "signal",
        request=request,
        session_id=session.session_id,
        max_calls=SHARE_SIGNAL_RATE_LIMIT,
        window_sec=SHARE_SIGNAL_RATE_WINDOW_SEC,
        user_message="You are sending signal runs too quickly.",
    )
    validated_paths = _validate_share_upload_paths(
        upload_paths[:SHARE_MAX_UPLOAD_FILES],
        request=request,
        session_id=session.session_id,
        min_files=SHARE_MAX_UPLOAD_FILES,
        max_files=SHARE_MAX_UPLOAD_FILES,
        invalid_message="Upload exactly four chart images: two higher timeframe views first, then two lower timeframe views.",
    )
    render_config = _build_share_render_config(
        overlay_mode,
        min_conf_global,
        min_conf_latest,
        history_depth,
        label_density,
        projection_focus,
        vision_extras=vision_extras,
        council_scope=council_scope,
    )
    try:
        result, source_image_state, active_file_path = _analyze_share_bundle(
            validated_paths,
            render_config,
            side_effect_free=SHARE_SIDE_EFFECT_FREE,
        )
    except Exception as exc:
        _generic_user_error(
            "Signal run failed. Please try again.",
            exc,
            stage="signal_run",
            session_id=session.session_id,
            file_count=len(validated_paths),
            **_request_audit_fields(request, session.session_id),
        )
    _update_share_session(
        session.session_id,
        result=result,
        source_image_state=source_image_state,
        active_file_path=active_file_path,
        render_config=render_config,
    )
    display_result = pg._sanitize_result_for_ui(result)
    _log_share_event(
        "info",
        "signal_completed",
        session_id=session.session_id,
        action=str(display_result.get("action", "HOLD")).upper(),
        confidence=round(float(display_result.get("confidence", 0.0) or 0.0), 4),
        overlay_mode=str(render_config["overlay_mode"]),
        council_loaded=bool(cast(dict[str, Any], display_result.get("local_ensemble", {})).get("models")),
        **_request_audit_fields(request, session.session_id),
    )
    return _render_share_session(
        session,
        status_message="Signal run complete. Premium visuals are exposed to the browser while the backend remains server-side.",
        notification_event="signal_complete",
    )


def refresh_share_preview(
    session_id: str,
    overlay_mode: str,
    vision_extras: Any,
    council_scope: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
    request: gr.Request | None = None,
) -> tuple[Any, ...]:
    session = _get_share_session(session_id, create=True)
    assert session is not None
    _enforce_rate_limit(
        "preview",
        request=request,
        session_id=session.session_id,
        max_calls=SHARE_PREVIEW_RATE_LIMIT,
        window_sec=SHARE_PREVIEW_RATE_WINDOW_SEC,
        user_message="Preview updates are arriving too quickly.",
    )
    render_config = _build_share_render_config(
        overlay_mode,
        min_conf_global,
        min_conf_latest,
        history_depth,
        label_density,
        projection_focus,
        vision_extras=vision_extras,
        council_scope=council_scope,
    )
    _update_share_session(session.session_id, render_config=render_config)
    if session.result is None or session.source_image_state is None:
        return _empty_share_outputs(session.session_id)
    _log_share_event(
        "info",
        "preview_refreshed",
        session_id=session.session_id,
        overlay_mode=str(render_config["overlay_mode"]),
        min_conf_global=round(float(render_config["min_conf_global"]), 2),
        min_conf_latest=round(float(render_config["min_conf_latest"]), 2),
        **_request_audit_fields(request, session.session_id),
    )
    return _render_share_session(
        session,
        status_message="Visual studio refreshed from server-side state.",
    )


def load_share_model_council(session_id: str, request: gr.Request | None = None) -> tuple[Any, ...]:
    session = _get_share_session(session_id, create=False)
    if session is None or session.result is None or session.source_image_state is None:
        return _empty_share_outputs(session_id)
    if not SHARE_ENABLE_MODEL_COUNCIL:
        return _render_share_session(
            session,
            status_message="Model council is disabled on this shared server.",
        )
    _enforce_rate_limit(
        "model_council",
        request=request,
        session_id=session.session_id,
        max_calls=SHARE_MODEL_COUNCIL_RATE_LIMIT,
        window_sec=SHARE_MODEL_COUNCIL_RATE_WINDOW_SEC,
        user_message="Model council requests are limited on this server.",
    )

    local_ensemble = cast(dict[str, Any], session.result.get("local_ensemble", {}))
    existing_models = cast(dict[str, Any], local_ensemble.get("models", {}))
    if existing_models:
        _log_share_event(
            "info",
            "model_council_reused",
            session_id=session.session_id,
            models=len(existing_models),
            **_request_audit_fields(request, session.session_id),
        )
        return _render_share_session(
            session,
            status_message="Model council already loaded for this share session.",
        )

    multi_timeframe = cast(dict[str, Any], session.result.get("multi_timeframe", {}))
    entries = cast(list[dict[str, Any]], multi_timeframe.get("entries", []))
    bundle_paths = [
        str(entry.get("file_path", "") or "").strip()
        for entry in entries
        if str(entry.get("file_path", "") or "").strip()
    ]
    if not bundle_paths and session.active_file_path:
        bundle_paths = [session.active_file_path]
    if not bundle_paths:
        _log_share_event(
            "warning",
            "model_council_skipped_missing_bundle",
            session_id=session.session_id,
            **_request_audit_fields(request, session.session_id),
        )
        return _render_share_session(
            session,
            status_message="No active chart bundle is available for the model council.",
        )

    try:
        validated_paths = _validate_share_upload_paths(
            bundle_paths[:SHARE_MAX_UPLOAD_FILES],
            request=request,
            session_id=session.session_id,
            min_files=1,
            max_files=SHARE_MAX_UPLOAD_FILES,
            invalid_message="No valid chart bundle is available for the model council.",
        )
        refined_result, source_image_state, active_file_path = _analyze_share_bundle(
            validated_paths,
            session.render_config,
            use_local_ensemble=True,
            side_effect_free=True,
        )
    except Exception as exc:
        _generic_user_error(
            "Model council is unavailable right now. Please try again later.",
            exc,
            stage="model_council",
            session_id=session.session_id,
            bundle_size=len(bundle_paths),
            **_request_audit_fields(request, session.session_id),
        )
    _update_share_session(
        session.session_id,
        result=refined_result,
        source_image_state=source_image_state,
        active_file_path=active_file_path,
    )
    display_result = pg._sanitize_result_for_ui(refined_result)
    model_rows = cast(dict[str, Any], cast(dict[str, Any], display_result.get("local_ensemble", {})).get("models", {}))
    _log_share_event(
        "info",
        "model_council_completed",
        session_id=session.session_id,
        action=str(display_result.get("action", "HOLD")).upper(),
        confidence=round(float(display_result.get("confidence", 0.0) or 0.0), 4),
        models=len(model_rows),
        **_request_audit_fields(request, session.session_id),
    )
    return _render_share_session(
        session,
        status_message="Model council refinement complete. The browser still only receives rendered outputs.",
        notification_event="model_council_complete",
    )


def load_share_timing_playbook(session_id: str, request: gr.Request | None = None) -> str:
    session = _get_share_session(session_id, create=False)
    if session is None or session.result is None:
        return pg._placeholder_panel(
            "Binary Timing Playbook",
      "Launch-ready timing guidance is armed from the premium surface. Run a signal to open the full timing playbook.",
        )
    _log_share_event(
        "info",
        "timing_playbook_opened",
        session_id=session.session_id,
        **_request_audit_fields(request, session.session_id),
    )
    return pg.build_timing_playbook_html(pg._sanitize_result_for_ui(session.result))


def load_share_timeframe_overlays(session_id: str, request: gr.Request | None = None) -> str:
    session = _get_share_session(session_id, create=True)
    assert session is not None
    _enforce_rate_limit(
        "preview",
        request=request,
        session_id=session.session_id,
        max_calls=SHARE_PREVIEW_RATE_LIMIT,
        window_sec=SHARE_PREVIEW_RATE_WINDOW_SEC,
        user_message="Overlay refreshes are arriving too quickly.",
    )
    if session.result is None:
      return _build_share_launch_timeframe_html()
    _log_share_event(
        "info",
        "timeframe_overlays_opened",
        session_id=session.session_id,
        **_request_audit_fields(request, session.session_id),
    )
    source_image_state = session.source_image_state
    if source_image_state is None and session.active_file_path:
      try:
        source_image_state = pg._source_image_to_state(session.active_file_path)
      except Exception:
        source_image_state = None
    return pg._build_timeframe_overlay_gallery_html(pg._sanitize_result_for_ui(session.result), source_image_state)


def load_share_feedback_feed(session_id: str, request: gr.Request | None = None) -> tuple[str, str]:
    session = _get_share_session(session_id, create=True)
    assert session is not None
    _enforce_rate_limit(
        "preview",
        request=request,
        session_id=session.session_id,
        max_calls=SHARE_PREVIEW_RATE_LIMIT,
        window_sec=SHARE_PREVIEW_RATE_WINDOW_SEC,
        user_message="Feed refreshes are arriving too quickly.",
    )
    _log_share_event(
        "info",
        "feedback_feed_opened",
        session_id=session.session_id,
        has_result=bool(session.result),
        **_request_audit_fields(request, session.session_id),
    )
    return pg._build_learning_feed_html(), pg._build_zone_library_html()


def submit_share_contact_brief(
    session_id: str,
    full_name: str,
    contact_channel: str,
    organization: str,
    purpose: str,
    consent_ack: bool,
    request: gr.Request | None = None,
) -> tuple[str, str]:
    normalized_name = _share_clean_text(full_name)
    normalized_channel = _share_clean_text(contact_channel)
    normalized_org = _share_clean_text(organization)
    normalized_purpose = _share_clean_text(purpose, limit=max(SHARE_CONTACT_FIELD_MAX_CHARS * 2, 220))

    if not consent_ack:
        return ("Confirm the educational-use and risk disclosure before submitting contact details.", str(session_id or ""))
    if len(normalized_name) < 3:
        return ("Add your full name so the operator can identify the request.", str(session_id or ""))
    if len(normalized_channel) < 5:
        return ("Add an email address or WhatsApp contact so the operator can respond privately.", str(session_id or ""))
    if len(normalized_purpose) < 12:
        return ("Describe your intended use briefly so the operator understands the request context.", str(session_id or ""))

    session = _get_share_session(session_id, create=True)
    if session is None:
        return ("The host could not create a private access record right now. Please try again.", str(session_id or ""))

    _enforce_rate_limit(
        "contact_brief",
        request=request,
        session_id=session.session_id,
        max_calls=SHARE_CONTACT_RATE_LIMIT,
        window_sec=SHARE_CONTACT_RATE_WINDOW_SEC,
        user_message="Contact submissions are limited on this server.",
    )

    try:
        _store_contact_brief_securely(
            session_id=session.session_id,
            full_name=normalized_name,
            contact_channel=normalized_channel,
            organization=normalized_org,
            purpose=normalized_purpose,
            consent_ack=bool(consent_ack),
            request=request,
        )
        pg.append_hash_chain(
            SHARE_CONTACT_LOG_PATH,
            {
                "alias": SHARE_PUBLIC_ALIAS,
                "session": _session_label(session.session_id),
                "creator": SHARE_CREATOR_NAME,
                "name_hash": _hash_text(normalized_name)[:16],
                "channel_hash": _hash_text(normalized_channel)[:16],
                "organization_hash": _hash_text(normalized_org)[:16] if normalized_org else "",
                "purpose_len": len(normalized_purpose),
                "consent_ack": bool(consent_ack),
                **_request_audit_fields(request, session.session_id),
            },
        )
    except Exception as exc:
        _record_share_error(
            "contact_brief",
            "contact brief submission failed",
            exc,
            session_id=session.session_id,
            **_request_audit_fields(request, session.session_id),
        )
        return ("The host could not store the contact brief. Please try again in a moment.", session.session_id)

    _log_share_event(
        "info",
        "contact_brief_captured",
        session_id=session.session_id,
        name_hash=_hash_text(normalized_name)[:16],
        channel_hash=_hash_text(normalized_channel)[:16],
        organization_hash=_hash_text(normalized_org)[:16] if normalized_org else "",
        purpose_len=len(normalized_purpose),
        **_request_audit_fields(request, session.session_id),
    )
    return (
        "Private access brief captured on the host machine for operator review. Continue only if you understand this is an educational surface and not financial advice.",
        session.session_id,
    )


def _sanitize_feedback_reason(reason: str) -> str:
    normalized = str(reason or "").strip()
    if len(normalized) > SHARE_REASON_MAX_CHARS:
        normalized = normalized[:SHARE_REASON_MAX_CHARS].rstrip()
    return normalized


def submit_share_feedback(
    session_id: str,
    verdict: str,
    reason: str,
    feedback_image: Any | None = None,
    request: gr.Request | None = None,
) -> str:
    if not SHARE_ENABLE_FEEDBACK:
        return "Feedback is disabled on this shared server."
    session = _get_share_session(session_id, create=False)
    if session is None or not str(session.active_file_path).strip():
        return "Run a signal before submitting feedback."

    _enforce_rate_limit(
        "feedback",
        request=request,
        session_id=session.session_id,
        max_calls=SHARE_FEEDBACK_RATE_LIMIT,
        window_sec=SHARE_FEEDBACK_RATE_WINDOW_SEC,
        user_message="Feedback submissions are limited on this server.",
    )
    file_path = str(session.active_file_path)
    safe_reason = _sanitize_feedback_reason(reason)
    if not SHARE_ENABLE_LEARNING_MUTATIONS:
        try:
            _img_unused, meta = pg.load_any_file_as_image(file_path)
            chosen = str(verdict or "HOLD").upper()
            rejected = "SELL" if chosen == "BUY" else "BUY"
            feedback_asset = pg._save_feedback_result_image(str(meta.get("sha256", "")), chosen, feedback_image)
            pg._append_jsonl(
                pg._feedback_feed_path(),
                {
                    "ts": pg.utc_now_iso(),
                    "source_path": file_path,
                    "source_image_hash": str(meta.get("sha256", "")),
                    "verdict": chosen,
                    "rejected": rejected,
                    "reason": safe_reason,
                    "feedback_image": dict(feedback_asset),
                    "share_feedback_review_only": True,
                    "online_learning_enabled": False,
                },
            )
            _log_share_event(
                "info",
                "feedback_recorded_review_only",
                session_id=session.session_id,
                verdict=chosen,
                reason_hash=_hash_text(safe_reason)[:16] if safe_reason else "",
                reason_length=len(safe_reason),
                **_request_audit_fields(request, session.session_id),
            )
            return "Feedback captured for operator review. Online learning is disabled in share mode."
        except Exception as exc:
            _record_share_error(
                "feedback_submit_review_only",
                "share feedback review-only submission failed",
                exc,
                session_id=session.session_id,
                verdict=str(verdict or "HOLD").upper(),
                **_request_audit_fields(request, session.session_id),
            )
            return "Feedback could not be recorded right now. Please try again."
    try:
        personal = pg._get_personal()
        continual_learning = pg._get_continual_learning()
        rl_engine = pg._get_rl_engine()
        _img_unused, meta = pg.load_any_file_as_image(file_path)
        chosen = str(verdict or "HOLD").upper()
        rejected = "SELL" if chosen == "BUY" else "BUY"
        feedback_asset = pg._save_feedback_result_image(str(meta.get("sha256", "")), chosen, feedback_image)
        feedback_image_path = str(feedback_asset.get("path", "") or "").strip()
        annotation_text = pg._build_feedback_annotation_text(feedback_asset)
        personal.record_feedback(str(meta.get("sha256", "")), chosen, rejected, safe_reason, annotation_text)
        replay_item = (
            continual_learning.record_feedback(
                str(meta.get("sha256", "")),
                chosen,
                safe_reason,
                feedback_image_path=feedback_image_path,
                feedback_image_sha256=str(feedback_asset.get("sha256", "") or "").strip(),
                feedback_image_meta=dict(feedback_asset),
            )
            if pg.RUNTIME.enable_replay_continual_learning
            else {}
        )
        rl_feedback = (
            rl_engine.record_feedback(
                str(meta.get("sha256", "")),
                chosen,
                safe_reason,
                feedback_image_path=feedback_image_path,
                feedback_image_sha256=str(feedback_asset.get("sha256", "") or "").strip(),
                feedback_image_meta=dict(feedback_asset),
            )
            if not pg.RUNTIME.pause_rl_updates
            else {}
        )
        if replay_item:
            personal.record_context_feedback(
                str(replay_item.get("context_key", "default")),
                str(replay_item.get("context_descriptor", "")),
                chosen,
                safe_reason,
                annotation_text,
            )

        bank = pg._get_memory_bank()
        if bank is not None:
            try:
                dpo_pairs = personal.generate_dpo_pairs(memory_bank=bank, n=50)
                personal.update_style_from_memory_bank(dpo_pairs)
            except Exception as exc:
                _record_share_error(
                    "feedback_style_refresh",
                    "style refresh failed during share feedback",
                    exc,
                    session_id=session.session_id,
                )

        pg._append_jsonl(
            pg._feedback_feed_path(),
            {
                "ts": pg.utc_now_iso(),
                "source_path": file_path,
                "source_image_hash": str(meta.get("sha256", "")),
                "verdict": chosen,
                "rejected": rejected,
                "reason": safe_reason,
                "feedback_image": dict(feedback_asset),
                "learning_snapshot_path": str(replay_item.get("snapshot_path", feedback_image_path)),
                "continual_learning_updated": bool(replay_item),
                "continual_learning_success": bool(replay_item.get("success", False)) if replay_item else False,
                "rl_feedback_updated": bool(rl_feedback),
                "rl_online_updated": bool(rl_feedback.get("updated", False)) if rl_feedback else False,
            },
        )
        _log_share_event(
            "info",
            "feedback_recorded",
            session_id=session.session_id,
            verdict=chosen,
            reason_hash=_hash_text(safe_reason)[:16] if safe_reason else "",
            reason_length=len(safe_reason),
            rl_updated=bool(rl_feedback.get("updated", False)) if rl_feedback else False,
            replay_updated=bool(replay_item),
            **_request_audit_fields(request, session.session_id),
        )
        if rl_feedback and bool(rl_feedback.get("updated", False)):
            return "Feedback captured and the online learner updated."
        if rl_feedback:
            return "Feedback captured and queued for server-side learning."
        return "Feedback captured for server-side learning."
    except Exception as exc:
        _record_share_error(
            "feedback_submit",
            "share feedback submission failed",
            exc,
            session_id=session.session_id,
            verdict=str(verdict or "HOLD").upper(),
            **_request_audit_fields(request, session.session_id),
        )
        return "Feedback could not be recorded right now. Please try again."


def submit_share_feedback_and_refresh(
    session_id: str,
    verdict: str,
    reason: str,
    feedback_image: Any | None = None,
    request: gr.Request | None = None,
) -> tuple[str, str, str]:
    status = submit_share_feedback(
        session_id,
        verdict,
        reason,
        feedback_image=feedback_image,
        request=request,
    )
    return status, pg._build_learning_feed_html(), pg._build_zone_library_html()


def _password_is_strong(password: str) -> bool:
    value = str(password or "")
    return (
        len(value) >= 12
        and any(char.islower() for char in value)
        and any(char.isupper() for char in value)
        and any(char.isdigit() for char in value)
    )


def _share_surface_is_public(host: str, *, tunnel_enabled: bool) -> bool:
    if tunnel_enabled:
        return True
    normalized = str(host or "").strip().lower()
    return normalized not in {"", "127.0.0.1", "localhost", "::1"}


def _share_credentials(*, strict_passwords: bool, public_surface: bool) -> list[tuple[str, str]]:
    raw_pairs = str(os.getenv("PHOENIXGUARD_SHARE_CREDENTIALS", "") or "").strip()
    parsed_pairs: list[tuple[str, str]] = []
    if raw_pairs:
        for chunk in raw_pairs.split(","):
            piece = chunk.strip()
            if not piece or ":" not in piece:
                continue
            username, password = piece.split(":", 1)
            username = username.strip()
            password = password.strip()
            if username and password:
                parsed_pairs.append((username, password))
    if not parsed_pairs:
        username = str(os.getenv("PHOENIXGUARD_SHARE_USERNAME", "operator") or "operator").strip() or "operator"
        password = str(os.getenv("PHOENIXGUARD_SHARE_PASSWORD", "") or "").strip()
        if password:
            parsed_pairs.append((username, password))
    if not parsed_pairs:
      if not public_surface:
        _log_share_event(
          "info",
          "share_local_preview_without_auth",
          strict_passwords=strict_passwords,
          public_surface=public_surface,
        )
        return []
        raise RuntimeError(
            "Set PHOENIXGUARD_SHARE_PASSWORD or PHOENIXGUARD_SHARE_CREDENTIALS before launching share mode."
        )
    for username, password in parsed_pairs:
        is_strong = _password_is_strong(password)
        if not is_strong:
            _log_share_event("warning", "weak_share_password", user_hash=_hash_text(username)[:16], length=len(password))
            if strict_passwords:
                raise RuntimeError(
                    "Share credentials are too weak. Use at least 12 characters with upper, lower, and numeric characters."
                )
    _log_share_event(
        "info",
        "share_credentials_loaded",
        credential_count=len(parsed_pairs),
        strict_passwords=strict_passwords,
        public_surface=public_surface,
    )
    return parsed_pairs


def _build_share_auth(credentials: list[tuple[str, str]]) -> Callable[[str, str], bool]:
    credential_map = {username: password for username, password in credentials}

    def _authenticate(username: str, password: str) -> bool:
        normalized_user = str(username or "").strip()
        now = time.time()
        with _share_auth_lock:
            auth_state = dict(_share_auth_failures.get(normalized_user, {}))
            locked_until = float(auth_state.get("locked_until", 0.0) or 0.0)
            fail_count = int(auth_state.get("count", 0) or 0)
            if locked_until > now:
                _log_share_event(
                    "warning",
                    "auth_locked_out",
                    user_hash=_hash_text(normalized_user)[:16],
                    remaining_lock_sec=int(max(0.0, locked_until - now)),
                )
                return False
            expected_password = credential_map.get(normalized_user)
            success = expected_password is not None and secrets.compare_digest(str(password or ""), expected_password)
            if success:
                _share_auth_failures.pop(normalized_user, None)
                _log_share_event("info", "auth_success", user_hash=_hash_text(normalized_user)[:16])
                return True
            fail_count += 1
            next_locked_until = now + SHARE_AUTH_LOCKOUT_SEC if fail_count >= SHARE_AUTH_MAX_FAILURES else 0.0
            _share_auth_failures[normalized_user] = {
                "count": fail_count,
                "locked_until": next_locked_until,
            }
        _log_share_event(
            "warning",
            "auth_failure",
            user_hash=_hash_text(normalized_user)[:16],
            failure_count=fail_count,
            locked=bool(next_locked_until),
        )
        return False

    return _authenticate


def _share_blocked_paths() -> list[str]:
    project_root = Path(pg.RUNTIME.project_root)
    candidates = [
        project_root,
        project_root.parent / ".codex",
        project_root / ".venv",
        project_root / ".hf_cache",
        project_root / ".hf_offload",
    ]
    return [str(path) for path in candidates if path.exists()]


def launch_share_ui() -> None:
    share_host = str(os.getenv("PHOENIXGUARD_SHARE_HOST", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
    share_port = _env_int("PHOENIXGUARD_SHARE_PORT", 7861)
    share_tunnel = _env_bool("PHOENIXGUARD_SHARE_TUNNEL", False)
    public_surface = _share_surface_is_public(share_host, tunnel_enabled=share_tunnel)
    strict_passwords = SHARE_STRICT_PASSWORDS or public_surface
    credentials = _share_credentials(strict_passwords=strict_passwords, public_surface=public_surface)
    share_auth = _build_share_auth(credentials) if credentials else None
    auth_message = _share_auth_message() if share_auth else None
    max_file_size = str(os.getenv("PHOENIXGUARD_SHARE_MAX_FILE_SIZE", "25mb") or "25mb").strip() or "25mb"

    with gr.Blocks(
        title=SHARE_UI_TITLE,
        fill_width=True,
        analytics_enabled=False,
        delete_cache=(24 * 60 * 60, 24 * 60 * 60),
    ) as demo:
        session_id_state = gr.State(value="")

        gr.HTML(_build_share_hero_html())

        with gr.Row(elem_classes=["pg-share-workspace"]):
            with gr.Column(scale=3, elem_classes=["pg-share-rail"]):
                with gr.Group(elem_classes=["pg-panel", "pg-controls", "pg-control-board"]):
                    gr.Markdown("### Operator Controls")
                    gr.Markdown("Upload exactly four chart images in order: the first two are the higher timeframe pair and the last two are the lower timeframe pair.")
                    file_input = gr.File(
                      label="Upload Exactly Four Chart Images",
                        file_types=sorted(SHARE_ALLOWED_IMAGE_EXTS),
                        file_count="multiple",
                    )
                    with gr.Accordion("Overlay Controls", open=True):
                        overlay_mode = gr.Dropdown(
                            choices=pg.VISION_LEVEL_CHOICES,
                            value=str(DEFAULT_SHARE_RENDER["overlay_mode"]),
                            label="Vision Level",
                        )
                        vision_extras = gr.CheckboxGroup(
                            choices=pg.VISION_EXTRA_CHOICES,
                            value=list(DEFAULT_SHARE_RENDER["vision_extras"]),
                            label="Vision Extras",
                        )
                        council_scope = gr.Dropdown(
                            choices=pg.COUNCIL_SCOPE_CHOICES,
                            value=str(DEFAULT_SHARE_RENDER["council_scope"]),
                            label="Council Depth",
                        )
                        min_conf_global = gr.Slider(
                            minimum=0.2,
                            maximum=0.95,
                            value=float(DEFAULT_SHARE_RENDER["min_conf_global"]),
                            step=0.01,
                            label="Global Min Confidence",
                        )
                        min_conf_latest = gr.Slider(
                            minimum=0.2,
                            maximum=0.95,
                            value=float(DEFAULT_SHARE_RENDER["min_conf_latest"]),
                            step=0.01,
                            label="Latest Min Confidence",
                        )
                        history_depth = gr.Slider(
                            minimum=1,
                            maximum=pg.MAX_SEQUENCE_HISTORY_DEPTH,
                            value=int(DEFAULT_SHARE_RENDER["history_depth"]),
                            step=1,
                            label="Sequence History Depth",
                        )
                        label_density = gr.Slider(
                            minimum=2,
                            maximum=18,
                            value=int(DEFAULT_SHARE_RENDER["label_density"]),
                            step=1,
                            label="Overlay Label Density",
                        )
                        projection_focus = gr.Slider(
                            minimum=0.0,
                            maximum=0.9,
                            value=float(DEFAULT_SHARE_RENDER["projection_focus"]),
                            step=0.01,
                            label="Projection Visibility Floor",
                        )
                    with gr.Row():
                        run_btn = gr.Button("Run Protected Signal Review")
                        timing_playbook_btn = gr.Button("Binary Timing Playbook")
                status_html = gr.HTML(
                  value=_build_share_launch_console_html(),
                    elem_classes=["pg-share-status-card"],
                )
            with gr.Column(scale=9, elem_classes=["pg-share-main"]):
                signal_html = gr.HTML(
                  value=pg._placeholder_panel("Signal Overview", "Premium launch mode is active. Upload exactly four chart images to activate the advanced feed, zone studio, and live review desk."),
                    elem_classes=["pg-share-signal-card"],
                )
                with gr.Row(elem_classes=["pg-share-stage-row"]):
                    with gr.Column(scale=11):
                        overlay_img = gr.Image(
                            label="Annotated Chart",
                            type="pil",
                            elem_classes=["pg-stage-media", "pg-share-stage-media"],
                            container=False,
                        )
                    with gr.Column(scale=2, elem_classes=["pg-share-stack"]):
                        confidence_gauge = gr.Plot(label="Decision Gauge", elem_classes=["pg-gauge-card"])
                        forecast_html = gr.HTML(
                        value=pg._placeholder_panel("Forecast & Risk", "Forecast and risk controls are active from launch. The first quartet will populate the live read."),
                            elem_classes=["pg-share-stage-card"],
                        )
                        adaptive_guidance_html = gr.HTML(
                        value=pg._build_adaptive_guidance_html(None),
                            elem_classes=["pg-share-stage-card"],
                        )
                timeframe_overlay_html = gr.HTML(
                    value=_build_share_launch_timeframe_html(),
                    elem_classes=["pg-share-stage-card"],
                )
                timing_playbook_html = gr.HTML(
                    value=pg._placeholder_panel(
                        "Binary Timing Playbook",
                      "Launch-ready timing guidance is armed from the premium surface. Run a signal to open the full timing playbook.",
                    ),
                    elem_classes=["pg-share-stage-card"],
                )

        with gr.Tabs(elem_classes=["pg-tab-wrap"]):
            with gr.Tab("Mission & Contact"):
                gr.HTML(value=_build_share_briefing_html())
                with gr.Group(elem_classes=["pg-panel", "pg-share-contact-panel"]):
                    gr.Markdown("### Private Access Brief")
                    gr.Markdown(
                        "Leave a private contact brief for operator follow-up. The details stay on the host machine for review and do not expose backend state."
                    )
                    with gr.Row():
                        contact_name = gr.Textbox(
                            label="Full Name",
                            placeholder="Your full name",
                            max_lines=1,
                        )
                        contact_channel = gr.Textbox(
                            label="Email Or WhatsApp",
                            placeholder="How the operator should reach you",
                            max_lines=1,
                        )
                    with gr.Row():
                        contact_org = gr.Textbox(
                            label="Organization Or Team",
                            placeholder="Optional team, desk, or project name",
                            max_lines=1,
                        )
                        contact_purpose = gr.Textbox(
                            label="Intended Use",
                            placeholder="Why you want access or what you want to review",
                            lines=3,
                        )
                    contact_consent = gr.Checkbox(
                        label="I understand this surface is educational, not financial advice, and trading remains risky.",
                        value=False,
                    )
                    contact_btn = gr.Button("Submit Private Access Brief")
                    contact_status = gr.Textbox(
                        label="Contact Brief Status",
                        lines=3,
                        interactive=False,
                        elem_classes=["pg-share-contact-status"],
                        value="Contact briefs stay on the host machine for operator review. Provide only the details you want retained.",
                    )
            with gr.Tab("Visual Studio"):
              compare_desk_html = gr.HTML(
                value=pg._placeholder_panel("Compare Desk", "Compare desk is armed from launch and will render the split view after the first shared inference.")
              )
              with gr.Row():
                with gr.Column(scale=6):
                  heatmap_img = gr.Image(label="Confidence Heatmap", type="pil", container=False, elem_classes=["pg-share-heatmap-img"])
                with gr.Column(scale=4):
                  heatmap_summary_html = gr.HTML(
                    value=pg._placeholder_panel("Confidence Heatmap", "The heatmap engine is armed from launch and will summarize hotspots after the first signal run.")
                  )
            with gr.Tab("Timeframe Overlays") as timeframe_overlays_tab:
              timeframe_overlay_tab_html = gr.HTML(
                value=_build_share_launch_timeframe_html()
              )
            with gr.Tab("Model Council") as model_council_tab:
              model_council_html = gr.HTML(
                value=pg._placeholder_panel("Model Council", "Premium model council refinement is armed from launch and will activate after the first quartet.")
              )
            with gr.Tab("Super Powers") as super_powers_tab:
              gr.Markdown(
                "Click Refresh Super Powers after each new run to bind timing, regime, counterfactual, memory, and explainability to the active quartet.",
                elem_classes=["pg-feedback-intent-note"],
              )
              super_powers_refresh_btn = gr.Button("Refresh Super Powers")
              super_powers_overview_html = gr.HTML(
                value=_build_share_super_powers_overview_html(None)
              )
              with gr.Accordion("Entry Window Engine", open=True):
                super_power_entry_html = gr.HTML(
                  value=pg._placeholder_panel("Entry Window Engine", "Run a signal to open the live timing, expiry, and safe-entry window for the current setup.")
                )
              with gr.Accordion("Regime Radar", open=False):
                super_power_regime_html = gr.HTML(
                  value=pg._placeholder_panel("Regime Radar", "Run a signal to classify trend, range, chop, expansion, or news shock and adjust thresholds automatically.")
                )
              with gr.Accordion("Counterfactual Lab", open=False):
                super_power_counterfactual_html = gr.HTML(
                  value=pg._placeholder_panel("Counterfactual Lab", "Run a signal to see the one-candle-earlier, one-candle-later, and skip-the-trade scenario lens.")
                )
              with gr.Accordion("Pattern Memory Echoes", open=False):
                super_power_pattern_html = gr.HTML(
                  value=pg._placeholder_panel("Pattern Memory Echoes", "Run a signal to pull the closest historical setups, cluster win rate, and dominant failure mode.")
                )
              with gr.Accordion("Explainability Layer", open=False):
                super_power_explain_html = gr.HTML(
                  value=pg._placeholder_panel("Explainability Layer", "Run a signal to see the plain-English reason, invalidation line, and strongest conflicting evidence.")
                )
            with gr.Tab("Advanced Feed") as feedback_feed_tab:
              gr.Markdown(
                "Advanced RL, zone memory, and outcome review are live from launch. Use this rail to review outcomes and keep the learning loop current.",
                elem_classes=["pg-feedback-intent-note"],
              )
              with gr.Group(elem_classes=["pg-panel", "pg-feedback"]):
                gr.Markdown("### Advanced Feed")
                if SHARE_ENABLE_FEEDBACK:
                  feedback_copy = (
                    "Submit the verdict and optional marked-up result image so the server-side learning feed keeps improving."
                    if SHARE_ENABLE_LEARNING_MUTATIONS
                    else "Feedback is stored for operator review only. Online learning is disabled on this shared server."
                  )
                else:
                  feedback_copy = "Feedback is disabled on this shared server to protect the VM and model state."
                gr.Markdown(feedback_copy)
                with gr.Row(elem_classes=["pg-share-learning-row"]):
                  with gr.Column(scale=6):
                    feedback_feed_html = gr.HTML(
                      value=pg._build_learning_feed_html()
                    )
                  with gr.Column(scale=4):
                    gr.Markdown("### Zone Studio")
                    zone_library_html = gr.HTML(
                      value=pg._build_zone_library_html()
                    )
                verdict = gr.Dropdown(
                  choices=["BUY", "SELL", "HOLD", "WRONG"],
                  value="HOLD",
                  label="Verdict",
                  interactive=SHARE_ENABLE_FEEDBACK,
                )
                feedback_result_image = gr.Image(label="Result Image For Learning", type="pil", container=False, elem_classes=["pg-share-feedback-img"])
                reason = gr.Textbox(
                  label="Reason",
                  lines=3,
                  placeholder="Why are you submitting this feedback?",
                  interactive=SHARE_ENABLE_FEEDBACK,
                )
                fb_btn = gr.Button("Submit Feedback", interactive=SHARE_ENABLE_FEEDBACK)
                fb_status = gr.Textbox(
                  label="Feedback Status",
                  lines=2,
                  interactive=False,
                  value=_share_feedback_placeholder() if SHARE_ENABLE_FEEDBACK else "Feedback is disabled on this shared server.",
                )

        signal_inputs = [
            session_id_state,
            file_input,
            overlay_mode,
            vision_extras,
            council_scope,
            min_conf_global,
            min_conf_latest,
            history_depth,
            label_density,
            projection_focus,
        ]
        signal_outputs = [
            overlay_img,
            confidence_gauge,
            signal_html,
            forecast_html,
            timeframe_overlay_html,
            timing_playbook_html,
            heatmap_img,
            heatmap_summary_html,
            compare_desk_html,
            adaptive_guidance_html,
            model_council_html,
            status_html,
            session_id_state,
        ]
        preview_inputs = [
            session_id_state,
            overlay_mode,
            vision_extras,
            council_scope,
            min_conf_global,
            min_conf_latest,
            history_depth,
            label_density,
            projection_focus,
        ]

        run_btn.click(
            run_share_signal,
            inputs=signal_inputs,
            outputs=signal_outputs,
            api_visibility="private",
            show_progress="minimal",
            trigger_mode="once",
            concurrency_limit=SHARE_HEAVY_CONCURRENCY,
            concurrency_id="share-heavy",
        )
        timing_playbook_btn.click(
            load_share_timing_playbook,
            inputs=[session_id_state],
            outputs=[timing_playbook_html],
            api_visibility="private",
            show_progress="minimal",
            trigger_mode="once",
        )
        model_council_tab.select(
            load_share_model_council,
            inputs=[session_id_state],
            outputs=signal_outputs,
            api_visibility="private",
            show_progress="minimal",
            concurrency_limit=SHARE_HEAVY_CONCURRENCY,
            concurrency_id="share-heavy",
        )
        super_power_outputs = [
          super_powers_overview_html,
          super_power_entry_html,
          super_power_regime_html,
          super_power_counterfactual_html,
          super_power_pattern_html,
          super_power_explain_html,
        ]
        super_powers_tab.select(
          load_share_super_powers,
          inputs=[session_id_state],
          outputs=super_power_outputs,
          api_visibility="private",
          show_progress="minimal",
          trigger_mode="always_last",
        )
        super_powers_refresh_btn.click(
          load_share_super_powers,
          inputs=[session_id_state],
          outputs=super_power_outputs,
          api_visibility="private",
          show_progress="minimal",
          trigger_mode="once",
        )
        timeframe_overlays_tab.select(
            load_share_timeframe_overlays,
            inputs=[session_id_state],
            outputs=[timeframe_overlay_tab_html],
            api_visibility="private",
            show_progress="minimal",
            trigger_mode="always_last",
        )
        feedback_feed_tab.select(
            load_share_feedback_feed,
            inputs=[session_id_state],
          outputs=[feedback_feed_html, zone_library_html],
            api_visibility="private",
            show_progress="minimal",
            trigger_mode="always_last",
        )
        overlay_mode.change(
            refresh_share_preview,
            inputs=preview_inputs,
            outputs=signal_outputs,
            api_visibility="private",
            queue=False,
            show_progress="hidden",
            trigger_mode="always_last",
        )
        vision_extras.change(
            refresh_share_preview,
            inputs=preview_inputs,
            outputs=signal_outputs,
            api_visibility="private",
            queue=False,
            show_progress="hidden",
            trigger_mode="always_last",
        )
        council_scope.change(
            refresh_share_preview,
            inputs=preview_inputs,
            outputs=signal_outputs,
            api_visibility="private",
            queue=False,
            show_progress="hidden",
            trigger_mode="always_last",
        )
        for component in [
            min_conf_global,
            min_conf_latest,
            history_depth,
            label_density,
            projection_focus,
        ]:
            component.input(
                refresh_share_preview,
                inputs=preview_inputs,
                outputs=signal_outputs,
                api_visibility="private",
                queue=False,
                show_progress="hidden",
                trigger_mode="always_last",
            )
        contact_btn.click(
            submit_share_contact_brief,
            inputs=[session_id_state, contact_name, contact_channel, contact_org, contact_purpose, contact_consent],
            outputs=[contact_status, session_id_state],
            api_visibility="private",
            show_progress="minimal",
            trigger_mode="once",
        )
        fb_btn.click(
            submit_share_feedback_and_refresh,
            inputs=[session_id_state, verdict, reason, feedback_result_image],
          outputs=[fb_status, feedback_feed_html, zone_library_html],
            api_visibility="private",
            show_progress="minimal",
            trigger_mode="once",
            concurrency_limit=SHARE_FEEDBACK_CONCURRENCY,
            concurrency_id="share-feedback",
        )

    demo.queue(max_size=SHARE_QUEUE_MAX_SIZE, default_concurrency_limit=SHARE_DEFAULT_CONCURRENCY)
    _log_share_event(
        "info",
        "share_launch",
        host=share_host,
        port=share_port,
        tunnel=share_tunnel,
        strict_passwords=strict_passwords,
        public_surface=public_surface,
        queue_max_size=SHARE_QUEUE_MAX_SIZE,
        default_concurrency=SHARE_DEFAULT_CONCURRENCY,
        heavy_concurrency=SHARE_HEAVY_CONCURRENCY,
        side_effect_free=SHARE_SIDE_EFFECT_FREE,
        feedback_enabled=SHARE_ENABLE_FEEDBACK,
        learning_mutations=SHARE_ENABLE_LEARNING_MUTATIONS,
    )
    demo.launch(
        server_name=share_host,
        server_port=share_port,
        share=share_tunnel,
        inbrowser=_env_bool("PHOENIXGUARD_UI_OPEN_BROWSER", False),
        prevent_thread_lock=False,
        auth=share_auth,
        auth_message=auth_message,
        debug=False,
        show_error=False,
        quiet=True,
        footer_links=[],
        allowed_paths=[],
        blocked_paths=_share_blocked_paths(),
        strict_cors=True,
        max_file_size=max_file_size,
        enable_monitoring=True,
        state_session_capacity=SHARE_MAX_SESSIONS,
        app_kwargs={"docs_url": None, "redoc_url": None, "openapi_url": None},
        pwa=False,
        mcp_server=False,
        theme="default",
        css=SHARE_UI_CSS,
        head=_share_ui_head(),
    )


analyze_share_bundle = _analyze_share_bundle
build_share_hero_html = _build_share_hero_html
build_share_render_config = _build_share_render_config
share_rate_limit_state = _share_rate_limit_state
share_sessions = _share_sessions
share_status_html = _share_status_html
share_surface_payload = _share_surface_payload
update_share_session = _update_share_session


if __name__ == "__main__":
    launch_share_ui()
