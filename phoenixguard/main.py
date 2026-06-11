"""
PhoenixGuard SIGE-VLA 3.0 - Main Pipeline
==========================================
Full wiring of:
    - MemoryBank (HNSW few-shot recall + logit boost)
    - 12-gate CurriculumGates (formal automata, ontology, regression, predictive)
    - 3-condition ensemble consensus (confidence >= 0.82, gates >= 9, memory >= 0.87)
    - Per-run Plotly skill-contribution dashboard
    - Online RL update every 50 memory-bank recalls
"""
from __future__ import annotations

import asyncio
import base64
from concurrent.futures import Future, ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import copy
import gc
import hashlib
import html
import importlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib import request as urllib_request
import warnings
from typing import Any, Callable, TYPE_CHECKING, Mapping, Sequence, cast
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray
import torch
from PIL import Image, ImageDraw, ImageFilter, ImageGrab

# Suppress noisy non-actionable runtime warnings on Windows/CUDA.
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ.setdefault("PHOENIXGUARD_CV_REASONING_V2", "1")
warnings.filterwarnings(
    "ignore",
    message=r".*Torch was not compiled with flash attention.*",
    category=UserWarning,
)

# GTX 1650 is SM 7.5 — below flash attention's SM 8.0 minimum.
# Disable flash_sdp so PyTorch routes directly to mem_efficient_sdp without
# attempting flash first and emitting the "not compiled with flash attention" warning.
if torch.cuda.is_available():
    torch.backends.cuda.enable_flash_sdp(False)


import gradio as gr
go = cast(Any, importlib.import_module("plotly.graph_objects"))

from phoenixguard.core.config import MODELS, RUNTIME, SECURITY, TRAIN, MEMORY_BANK as MEMORY_BANK_CFG
from phoenixguard.core.utils import append_hash_chain, setup_logger, utc_now_iso
from phoenixguard.runtime.security import (
    SecurityManager,
    EncryptedPreferenceStore,
    UnavailablePreferenceStore,
    open_preference_store,
)
from phoenixguard.vision.preprocess import extract_price_floats, indicator_regex_filter, load_any_file_as_image
from phoenixguard.decision.ensemble import TransitionSummary
# ------------------------------------------------------------------
# Gradio callbacks
# ------------------------------------------------------------------

# pg_main wrapper class to delegate to local pipeline functions
class PGMainWrapper:
    def run_inference(
        self,
        file_path: str,
        annotation_text: str = "",
        overlay_mode: str = "history-plus-projection",
        min_conf_global: float = 0.42,
        min_conf_latest: float = 0.50,
        history_depth: int = 8,
        label_density: int = 10,
        projection_focus: float = 0.35,
        side_effect_free: bool = False,
        use_local_ensemble: bool | None = None,
    ) -> tuple[dict[str, Any], Image.Image | None, Any, Any]:
        # Use the local run_inference function
        return run_inference(
            file_path,
            annotation_text=annotation_text,
            overlay_mode=overlay_mode,
            min_conf_global=min_conf_global,
            min_conf_latest=min_conf_latest,
            history_depth=history_depth,
            label_density=label_density,
            projection_focus=projection_focus,
            side_effect_free=side_effect_free,
            use_local_ensemble=use_local_ensemble,
        )

    def build_cv_debug_payload(
        self,
        result: dict[str, Any],
        render_config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return build_cv_debug_payload(result, render_config=render_config)

# Instantiate the wrapper for use in callbacks
pg_main = PGMainWrapper()

if TYPE_CHECKING:
        from phoenixguard.vision.cv_module import CVPatternDetector
        from phoenixguard.decision.regression_module import Forecast3MOutput, ForecastRouter
        from phoenixguard.decision.rl_module import RLPolicyEngine
        from phoenixguard.decision.skill_gates import SkillGatedMoE, CurriculumGates
        from phoenixguard.decision.ensemble import EnsembleDecisionEngine
        from phoenixguard.runtime.local_ensemble_runtime import LocalCVEnsembleRuntime
        from phoenixguard.decision.personalization import PersonalizationEngine


def _gr_skip() -> Any:
    skip_fn = cast(Callable[[], Any], getattr(gr, "skip"))
    return skip_fn()


def _tk_attributes(window: Any, *args: Any) -> Any:
    return window.attributes(*args)


def _pillow_lanczos() -> Any:
    resampling = getattr(Image, "Resampling", None)
    if resampling is not None:
        return getattr(resampling, "LANCZOS", getattr(Image, "LANCZOS", 1))
    return getattr(Image, "LANCZOS", 1)


logger = setup_logger(RUNTIME.logs_dir / "phoenixguard.log")
security = SecurityManager(RUNTIME.data_dir, RUNTIME.logs_dir, SECURITY.kdf_iterations)


def get_configured_passphrase() -> str | None:
    try:
        value = str(os.getenv("PHOENIXGUARD_PASSPHRASE", "") or "").strip()
        if value:
            return value
    except Exception:
        pass
    return None

UI_BRAND_NAME = "808Fx Standard Hybrid System"
UI_BRAND_SUBTITLE = "Hybrid chart intelligence desk for live structure control and execution review."
MODEL_ALIAS_MAP = {
    "dinov2": "Hybrid Core",
    "simclr": "Vector Edge",
    "byol": "Signal Drift",
    "swav": "Structure Wave",
    "clip": "Context Prism",
    "mobilenetv3": "Rapid Pulse",
    "mobilenetv3small": "Rapid Pulse",
    "mobilenet": "Rapid Pulse",
}
MODEL_ROLE_ALIAS_MAP = {
    "buy_specialist": "Buy Specialist",
    "sell_specialist": "Sell Specialist",
    "structure_specialist": "Structure Lens",
    "execution_specialist": "Execution Lens",
    "local_pattern_confirmer": "Signal Verifier",
    "regime_checker": "Context Lens",
    "macro_trend_reader": "Macro Lens",
    "macro_bias_reader": "Macro Lens",
    "pattern_specialist": "Pattern Lens",
    "generalist": "Hybrid Node",
}
GATE_ALIAS_MAP = {
    "prob_stats": "Range Engine",
    "discrete_fsm": "State Engine",
    "algo_heap": "Signal Stack",
    "ml_stacking": "Fusion Layer",
    "db_context": "Context Vault",
    "ops_stability": "Runtime Guard",
    "ui_analytics": "Desk Telemetry",
    "meta_constraints": "Safety Rail",
    "regression_est": "Path Estimator",
    "knowledge_rep": "Structure Logic",
    "formal_automata": "Pattern Grid",
    "predictive_analytics": "Forward Edge",
    "continuation_strength": "Trend Pulse",
    "macro_local_alignment": "Trend Sync",
    "memory_regime_agreement": "Memory Sync",
    "opposition_strength": "Counterforce",
    "execution_permission": "Execution Guard",
    "forecast_calibration": "Forecast Calibration",
    "interval_efficiency": "Interval Efficiency",
    "regime_stability": "Regime Stability",
    "transition_alignment": "Transition Alignment",
}

# ------------------------------------------------------------------
# Engine constructors (lazy init so UI can launch immediately)
# ------------------------------------------------------------------
_cv_engine: Any | None = None
_forecast_engine: Any | None = None
_rl_engine: Any | None = None
_gates_engine: Any | None = None
_moe: Any | None = None
_ensemble: Any | None = None
_personal: Any | None = None
_local_ensemble: Any | None = None
_local_ensemble_future: Future[Any] | None = None
_local_ensemble_last_error = ""
_model_council_daemon_process: subprocess.Popen[Any] | None = None
_tta_manager: Any | None = None
_ood_detector: Any | None = None
_continual_learning: Any | None = None
_pref_store: EncryptedPreferenceStore | UnavailablePreferenceStore | None = None
_pref_store_lock = threading.Lock()
_cv_engine_lock = threading.Lock()
_forecast_engine_lock = threading.Lock()
_personal_lock = threading.Lock()
_memory_bank_lock = threading.Lock()
_local_ensemble_lock = threading.Lock()
_background_executor_lock = threading.Lock()
_background_warmup_lock = threading.Lock()
_model_council_daemon_lock = threading.Lock()
_background_executor: ThreadPoolExecutor | None = None
_background_warmup_started = False
_capture_runtime_lock = threading.Lock()
_capture_selector_lock = threading.Lock()
_session_runtime_lock = threading.Lock()
_zone_memory_lock = threading.Lock()
_compare_frame_cache_lock = threading.Lock()
_processed_capture_files: set[str] = set()
_hotkey_listener_started = False
_runtime_maintenance_state: dict[str, int] = {"inference_runs": 0}
_capture_runtime_state: dict[str, Any] = {
    "requested_hotkey": str(RUNTIME.capture_hotkey),
    "active_hotkey": "",
    "status": "Hotkey capture offline.",
    "last_capture_time": "",
    "last_capture_file": "",
    "last_error": "",
    "selection_active": False,
    "inference_active": False,
    "token": 0,
    "status_token": 0,
    "latest_result": None,
    "latest_source_image_state": None,
    "latest_file_path": "",
    "latest_overlay": None,
    "latest_gauge": None,
    "latest_skill_fig": None,
    "pending_bundle": [],
    "bundle_size": int(max(1, RUNTIME.capture_bundle_size)),
    "bundle_started_at": "",
    "bundle_started_epoch": 0.0,
    "last_bundle_id": "",
}
_session_runtime_state: dict[str, Any] = {
    "session_id": time.strftime("%Y%m%d_%H%M%S", time.localtime()),
    "started_at": utc_now_iso(),
    "entries": [],
}
_zone_memory_cache: list[dict[str, Any]] | None = None
_compare_frame_uri_cache: dict[str, str] = {}


def _memory_image_dirs() -> list[str]:
    return [
        str(RUNTIME.project_root / MEMORY_BANK_CFG.buys_dir),
        str(RUNTIME.project_root / MEMORY_BANK_CFG.sells_dir),
    ]


def _get_background_executor() -> ThreadPoolExecutor:
    global _background_executor
    if _background_executor is None:
        with _background_executor_lock:
            if _background_executor is None:
                _background_executor = ThreadPoolExecutor(
                    max_workers=2,
                    thread_name_prefix="phoenixguard-bg",
                )
    return _background_executor


def _should_background_warm_local_ensemble() -> bool:
    if not bool(getattr(RUNTIME, "enable_local_ensemble", True)):
        return False
    env_override = str(os.getenv("PHOENIXGUARD_WARM_LOCAL_ENSEMBLE_ON_START", "") or "").strip()
    if env_override:
        return env_override.lower() in {"1", "true", "yes", "on"}
    return bool(getattr(RUNTIME, "warm_local_ensemble_on_launch", False))


def _model_council_daemon_port() -> int:
    try:
        return int(os.getenv("PHOENIXGUARD_MODEL_COUNCIL_PORT", "8767") or "8767")
    except Exception:
        return 8767


def _model_council_daemon_base_url() -> str:
    return f"http://127.0.0.1:{_model_council_daemon_port()}"


def _model_council_daemon_request(
    path: str,
    *,
    payload: Mapping[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    url = f"{_model_council_daemon_base_url()}{path}"
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(dict(payload), ensure_ascii=True).encode("utf-8")
    req = urllib_request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    with urllib_request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    parsed: dict[str, Any] = json.loads(raw) if raw else {}
    return dict(parsed)


def _start_model_council_daemon() -> None:
    global _model_council_daemon_process
    with _model_council_daemon_lock:
        if _model_council_daemon_process is not None and _model_council_daemon_process.poll() is None:
            return

        env = os.environ.copy()
        env["PHOENIXGUARD_ENABLE_LOCAL_ENSEMBLE"] = "1"
        env["PHOENIXGUARD_LOCAL_ENSEMBLE_MAX_LOADED"] = str(
            max(1, int(getattr(RUNTIME, "local_ensemble_max_loaded_models", 2) or 2))
        )
        env["PHOENIXGUARD_MODEL_COUNCIL_CACHE_SIZE"] = str(
            max(1, int(getattr(RUNTIME, "model_council_cache_size", 24) or 24))
        )
        if bool(getattr(RUNTIME, "force_full_council_on_cpu", False)):
            env["PHOENIXGUARD_FORCE_FULL_COUNCIL_ON_CPU"] = "1"
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _model_council_daemon_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "phoenixguard.runtime.model_council_daemon",
                "--port",
                str(_model_council_daemon_port()),
            ],
            cwd=str(RUNTIME.project_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )


def _ensure_model_council_daemon_ready(timeout_sec: float = 20.0) -> dict[str, Any]:
    try:
        return _model_council_daemon_request("/status", timeout=3.0)
    except Exception:
        _start_model_council_daemon()

    deadline = time.time() + max(timeout_sec, 1.0)
    last_error = "daemon did not become ready"
    while time.time() < deadline:
        try:
            return _model_council_daemon_request("/status", timeout=3.0)
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.5)
    raise RuntimeError(last_error)


def _predict_with_model_council_daemon(
    image: Image.Image,
    *,
    adaptation_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure_model_council_daemon_ready()
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    payload = _model_council_daemon_request(
        "/predict",
        payload={
            "image_b64": base64.b64encode(buffer.getvalue()).decode("utf-8"),
            "adaptation_profile": dict(adaptation_profile or {}),
        },
        timeout=900.0,
    )
    prediction = payload.get("prediction", {})
    if not isinstance(prediction, dict):
        raise RuntimeError("Model council daemon returned an invalid prediction payload.")
    prediction_dict = dict(cast(dict[str, Any], prediction))
    prediction_dict["_daemon_cached"] = bool(payload.get("cached", False))
    prediction_dict["_daemon_status"] = dict(cast(dict[str, Any], payload.get("status", {})))
    return prediction_dict


def _load_local_ensemble_runtime() -> LocalCVEnsembleRuntime:
    from phoenixguard.runtime.local_ensemble_runtime import LocalCVEnsembleRuntime

    target_models = None
    if (
        bool(getattr(RUNTIME, "force_full_council_on_cpu", False))
        and str(RUNTIME.device_preference) == "cpu"
        and not str(os.getenv("PHOENIXGUARD_LOCAL_ENSEMBLE_MODELS", "") or "").strip()
    ):
        target_models = list(LocalCVEnsembleRuntime.DEFAULT_MODELS)

    return LocalCVEnsembleRuntime(
        image_dirs=_memory_image_dirs(),
        model_dir=RUNTIME.models_dir,
        compute_device=torch.device(RUNTIME.device_preference),
        logger=logger,
        target_models=target_models,
    )


def _local_ensemble_status() -> str:
    with _local_ensemble_lock:
        if _local_ensemble is not None:
            return "ready"
        if _local_ensemble_future is not None and not _local_ensemble_future.done():
            return "warming_up"
        if _local_ensemble_last_error:
            return f"warmup_failed:{_local_ensemble_last_error}"
    return "not_loaded"


def _start_background_warmup() -> None:
    global _background_warmup_started
    if not bool(getattr(RUNTIME, "background_warmup_on_launch", True)):
        return
    if _background_warmup_started:
        return
    with _background_warmup_lock:
        if _background_warmup_started:
            return
        _background_warmup_started = True

    def _warmup() -> None:
        try:
            _get_cv_engine()
        except Exception as exc:
            logger.warning("Background CV warmup failed: %s", exc)
        try:
            _get_forecast_engine()
        except Exception as exc:
            logger.warning("Background forecast warmup failed: %s", exc)
        if bool(getattr(RUNTIME, "preload_memory_bank_on_launch", False)):
            try:
                _get_memory_bank()
            except Exception as exc:
                logger.warning("Background memory-bank preload failed: %s", exc)
        if _should_background_warm_local_ensemble():
            try:
                _get_local_ensemble(block=False)
            except Exception as exc:
                logger.warning("Background local ensemble warmup failed: %s", exc)

    _get_background_executor().submit(_warmup)


def _get_cv_engine() -> CVPatternDetector:
    global _cv_engine
    if _cv_engine is None:
        with _cv_engine_lock:
            if _cv_engine is None:
                from phoenixguard.vision.cv_module import CVPatternDetector

                _cv_engine = CVPatternDetector(MODELS.cv_primary, MODELS.cv_fallback, logger)
    return _cv_engine
def _get_forecast_engine() -> ForecastRouter:
    global _forecast_engine
    if _forecast_engine is None:
        with _forecast_engine_lock:
            if _forecast_engine is None:
                from phoenixguard.decision.regression_module import ForecastRouter

                _forecast_engine = ForecastRouter(
                    model_name=MODELS.chronos_model,
                    logger=logger,
                    max_interval_pct=RUNTIME.conformal_max_interval_pct,
                )
    return _forecast_engine
def _get_local_ensemble(*, block: bool = True) -> LocalCVEnsembleRuntime | None:
    global _local_ensemble, _local_ensemble_future, _local_ensemble_last_error
    if not bool(getattr(RUNTIME, "enable_local_ensemble", True)):
        return None
    if _local_ensemble is not None:
        return _local_ensemble

    future: Future[Any] | None = None
    with _local_ensemble_lock:
        if _local_ensemble is not None:
            return _local_ensemble
        if _local_ensemble_future is None:
            _local_ensemble_last_error = ""
            logger.info("Starting local ensemble warmup in the background.")
            _local_ensemble_future = _get_background_executor().submit(_load_local_ensemble_runtime)
        future = _local_ensemble_future

    # The type of future is Future[Any], so this check is always False and can be removed
    # if future is None:
    #     return _local_ensemble
    if not block and not future.done():
        return None

    try:
        runtime = future.result()
    except Exception as exc:
        with _local_ensemble_lock:
            if _local_ensemble_future is future:
                _local_ensemble_future = None
            _local_ensemble_last_error = str(exc)
        if block:
            raise
        logger.warning("Local ensemble warmup failed: %s", exc)
        return None

    with _local_ensemble_lock:
        if _local_ensemble is None:
            _local_ensemble = runtime
        if _local_ensemble_future is future:
            _local_ensemble_future = None
        _local_ensemble_last_error = ""
    return _local_ensemble


def _neutral_local_ensemble_prediction(reason: str = "unavailable") -> dict[str, Any]:
    return {
        "models": {},
        "ensemble": {
            "buy_prob": 0.5,
            "sell_prob": 0.5,
            "predicted_label": "HOLD",
            "confidence": 0.5,
            "margin": 0.0,
            "entropy": 1.0,
            "disagreement": 0.0,
            "consensus_ratio": 0.0,
            "vote_counts": {"BUY": 0, "SELL": 0},
            "champion_model": "",
            "confirmer_model": "",
            "live_models": [],
            "shadow_models": [],
            "failed_models": {"runtime": reason},
        },
    }


def _get_pref_store() -> EncryptedPreferenceStore | UnavailablePreferenceStore:
    global _pref_store
    if _pref_store is None:
        with _pref_store_lock:
            if _pref_store is None:
                passphrase = get_configured_passphrase()
                fernet = security.derive_fernet(passphrase) if passphrase else None
                _pref_store = open_preference_store(
                    RUNTIME.data_dir / SECURITY.prefs_db_path,
                    fernet,
                    logger=logger,
                )
    return _pref_store


def _get_personal() -> PersonalizationEngine:
    global _personal
    if _personal is None:
        with _personal_lock:
            if _personal is None:
                from phoenixguard.decision.personalization import PersonalizationEngine

                _personal = PersonalizationEngine(
                    MODELS.style_embedder,
                    _get_pref_store(),
                    logger,
                    meta_profile_path=RUNTIME.personalization_profiles_path,
                )
    return _personal
def _get_tta_manager() -> Any:
    global _tta_manager
    if _tta_manager is None:
        from phoenixguard.runtime.adaptive_runtime import TestTimeAdaptationManager
        _tta_manager = TestTimeAdaptationManager(logger)
    return _tta_manager
def _get_ood_detector() -> Any:
    global _ood_detector
    if _ood_detector is None:
        from phoenixguard.runtime.adaptive_runtime import OpenSetDetector
        _ood_detector = OpenSetDetector(logger)
    return _ood_detector
def _get_continual_learning() -> Any:
    global _continual_learning
    if _continual_learning is None:
        from phoenixguard.runtime.adaptive_runtime import ContinualLearningManager
        _continual_learning = ContinualLearningManager(
            RUNTIME.data_dir,
            logger,
            replay_buffer_size=TRAIN.replay_buffer_size,
            ewc_lambda=TRAIN.ewc_lambda,
            lwf_temperature=TRAIN.lwf_temperature,
        )
    return _continual_learning
def _get_rl_engine() -> RLPolicyEngine:
    global _rl_engine
    if _rl_engine is None:
        from phoenixguard.decision.rl_module import RLPolicyEngine
        _rl_engine = RLPolicyEngine(logger, in_dim=64, mcts_sims=RUNTIME.mcts_sims)
    return _rl_engine
def _get_gates_engine() -> CurriculumGates:
    global _gates_engine
    if _gates_engine is None:
        from phoenixguard.decision.skill_gates import CurriculumGates
        _gates_engine = CurriculumGates(logger)
    return _gates_engine
def _get_moe() -> SkillGatedMoE:
    global _moe
    if _moe is None:
        from phoenixguard.decision.skill_gates import SkillGatedMoE
        _moe = SkillGatedMoE(n_features=16, n_gates=12)
    return _moe
def _get_ensemble() -> EnsembleDecisionEngine:
    global _ensemble
    if _ensemble is None:
        from phoenixguard.decision.ensemble import EnsembleDecisionEngine
        _ensemble = EnsembleDecisionEngine(
            consensus_threshold=RUNTIME.consensus_threshold,
            max_interval_pct=RUNTIME.conformal_max_interval_pct,
            risk_min_pct=RUNTIME.risk_min_pct,
            risk_max_pct=RUNTIME.risk_max_pct,
            gates_pass_minimum=RUNTIME.gates_pass_minimum,
            memory_veto_threshold=MEMORY_BANK_CFG.recall_veto_threshold,
        )
    return _ensemble
# ------------------------------------------------------------------
# Memory bank (HNSW zero-shot recall) — lazy load
# ------------------------------------------------------------------
_memory_bank = None


def _get_memory_bank():
    global _memory_bank
    if _memory_bank is not None:
        return _memory_bank
    with _memory_bank_lock:
        if _memory_bank is not None:
            return _memory_bank
        try:
            from phoenixguard.memory.memory_ingest import MemoryBank, MemoryIngestor

            bank_dir = RUNTIME.memory_bank_dir   # already a Path object
            # Bug fix: MemoryBank.save() writes metadata.json, NOT memory_bank.pkl.
            # Using the correct sentinel file so the bank actually loads.
            metadata_path = bank_dir / "metadata.json"
            if not metadata_path.exists():
                buys_dir = RUNTIME.project_root / MEMORY_BANK_CFG.buys_dir
                sells_dir = RUNTIME.project_root / MEMORY_BANK_CFG.sells_dir
                if buys_dir.exists() and sells_dir.exists():
                    logger.info("Memory bank missing at %s — auto-building from labeled folders.", bank_dir)
                    ingestor = MemoryIngestor(
                        buys_dir=buys_dir,
                        sells_dir=sells_dir,
                        output_dir=bank_dir,
                        logger=logger,
                    )
                    ingestor.ingest()
                else:
                    logger.info(
                        "Memory bank missing and source folders are unavailable: buys=%s sells=%s",
                        buys_dir,
                        sells_dir,
                    )

            if metadata_path.exists():
                # Bug fix: pass bank_dir (Path) directly — load() uses Path / operator
                # internally; passing str() caused a TypeError on the first / operation.
                _memory_bank = MemoryBank.load(bank_dir, logger)
                loaded_bank = _memory_bank
                logger.info("MemoryBank loaded from %s (%d entries)", bank_dir, len(loaded_bank.entries))
                dominant_label = (
                    str(
                        max(
                            set(e.label for e in loaded_bank.entries),
                            key=lambda l: sum(1 for e in loaded_bank.entries if e.label == l),
                        )
                    )
                    if loaded_bank.entries
                    else "N/A"
                )
                stats: dict[str, Any] = {
                    "total_entries": len(loaded_bank.entries),
                    "archetype_count": sum(1 for e in loaded_bank.entries if e.is_archetype_centroid),
                    "dominant_label": dominant_label,
                }
                _get_personal().update_memory_bank_stats(stats)
            else:
                logger.warning("Memory bank still unavailable at %s after auto-build attempt.", bank_dir)
        except Exception as e:
            logger.warning("MemoryBank load failed: %s", e)
    return _memory_bank


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _normalize_alias_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _alias_model_name(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Hybrid Node"
    key = _normalize_alias_key(raw)
    if key in {"na", "n/a"}:
        return "N/A"
    return MODEL_ALIAS_MAP.get(key, raw.replace("_", " ").replace("-", " ").title())


def _alias_model_role(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Hybrid Node"
    key = str(raw).lower().strip()
    return MODEL_ROLE_ALIAS_MAP.get(key, raw.replace("_", " ").title())


def _alias_gate_name(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Signal Gate"
    key = str(raw).lower().strip()
    return GATE_ALIAS_MAP.get(key, raw.replace("_", " ").title())


def _mask_display_text(value: Any) -> str:
    text = str(value or "")
    replacement_map = {
        "phoenixguard sige-vla 3.0": UI_BRAND_NAME,
        "phoenixguard workstation": UI_BRAND_NAME,
        "phoenixguard": UI_BRAND_NAME,
        "sige-vla 3.0": UI_BRAND_NAME,
    }
    for raw_name, alias in MODEL_ALIAS_MAP.items():
        replacement_map[raw_name] = alias
    for raw_name, alias in GATE_ALIAS_MAP.items():
        replacement_map[raw_name] = alias
    for source, target in replacement_map.items():
        if re.fullmatch(r"[a-z0-9]+", source):
            pattern = rf"(?<![A-Za-z0-9]){re.escape(source)}(?![A-Za-z0-9])"
        else:
            pattern = re.escape(source)
        text = re.sub(pattern, target, text, flags=re.IGNORECASE)
    return text


def _mask_nested_display_values(value: Any) -> Any:
    if isinstance(value, dict):
        mapping = cast(dict[Any, Any], value)
        return {str(key): _mask_nested_display_values(item) for key, item in mapping.items()}
    if isinstance(value, list):
        items = cast(list[Any], value)
        return [_mask_nested_display_values(item) for item in items]
    if isinstance(value, str):
        return _mask_display_text(value)
    return value


def _sanitize_result_for_ui(result: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = dict(result)

    local_ensemble = dict(cast(dict[str, Any], result.get("local_ensemble", {})))
    if local_ensemble:
        sanitized["local_ensemble"] = local_ensemble
    ensemble_view = dict(cast(dict[str, Any], local_ensemble.get("ensemble", {})))
    if ensemble_view:
        local_ensemble["ensemble"] = ensemble_view
        if "champion_model" in ensemble_view:
            ensemble_view["champion_model"] = _alias_model_name(ensemble_view.get("champion_model", ""))
        if "confirmer_model" in ensemble_view:
            ensemble_view["confirmer_model"] = _alias_model_name(ensemble_view.get("confirmer_model", ""))

    model_rows = cast(dict[str, dict[str, Any]], local_ensemble.get("models", {}))
    if model_rows:
        aliased_models: dict[str, dict[str, Any]] = {}
        for raw_key, raw_row in model_rows.items():
            row = dict(raw_row)
            alias_name = _alias_model_name(row.get("name", raw_key))
            row["name"] = alias_name
            row["role"] = _alias_model_role(row.get("role", "generalist"))
            metrics = dict(cast(dict[str, Any], row.get("metrics", {})))
            if metrics:
                row["metrics"] = metrics
                metrics["model"] = alias_name
            aliased_models[alias_name] = row
        local_ensemble["models"] = aliased_models

    gate_details = [dict(gate) for gate in cast(list[dict[str, Any]], result.get("gate_details", []))]
    if gate_details:
        sanitized["gate_details"] = gate_details
    for gate in gate_details:
        gate["name"] = _alias_gate_name(gate.get("name", "gate"))

    gate_scores = dict(cast(dict[str, Any], result.get("gate_scores", {})))
    if gate_scores:
        sanitized["gate_scores"] = {_alias_gate_name(name): score for name, score in gate_scores.items()}

    support_gate_details = [dict(gate) for gate in cast(list[dict[str, Any]], result.get("support_gate_details", []))]
    if support_gate_details:
        sanitized["support_gate_details"] = support_gate_details
    for gate in support_gate_details:
        gate["name"] = _alias_gate_name(gate.get("name", "gate"))

    support_gate_scores = dict(cast(dict[str, Any], result.get("support_gate_scores", {})))
    if support_gate_scores:
        sanitized["support_gate_scores"] = {_alias_gate_name(name): score for name, score in support_gate_scores.items()}

    shap_contributions = dict(cast(dict[str, Any], result.get("shap_contributions", {})))
    if shap_contributions:
        sanitized["shap_contributions"] = {_alias_gate_name(name): score for name, score in shap_contributions.items()}

    return cast(dict[str, Any], _mask_nested_display_values(sanitized))


def _is_latest_branch_pattern(name: str) -> bool:
    normalized = name.lower().strip().replace(" ", "_")
    return normalized.startswith("latest_") or normalized.startswith("next_") or normalized.startswith("wick_")


def _filter_detections_for_overlay(
    detections: list[dict[str, Any]],
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
) -> list[dict[str, Any]]:
    mode = overlay_mode.strip().lower()
    if mode in {"history-boxes", "history-plus-projection"}:
        return []
    out: list[dict[str, Any]] = []
    for d in detections:
        name = str(d.get("pattern", ""))
        conf = float(d.get("confidence", 0.0) or 0.0)
        is_latest = _is_latest_branch_pattern(name)
        if is_latest and conf < min_conf_latest:
            continue
        if (not is_latest) and conf < min_conf_global:
            continue
        if _is_parser_artifact(name):
            continue
        if mode == "global-only" and is_latest:
            continue
        if mode == "latest-only" and not is_latest:
            continue
        out.append(d)
    return out


def _build_render_config(
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float | int = 8,
    label_density: float | int = 10,
    projection_focus: float = 0.35,
    debug_depth: float | int = 6,
) -> dict[str, Any]:
    return {
        "overlay_mode": str(overlay_mode or "history-plus-projection"),
        "min_conf_global": float(np.clip(float(min_conf_global), 0.0, 1.0)),
        "min_conf_latest": float(np.clip(float(min_conf_latest), 0.0, 1.0)),
        "history_depth": int(np.clip(int(round(float(history_depth))), 1, 24)),
        "label_density": int(np.clip(int(round(float(label_density))), 2, 24)),
        "projection_focus": float(np.clip(float(projection_focus), 0.0, 0.95)),
        "debug_depth": int(np.clip(int(round(float(debug_depth))), 3, 16)),
    }


def _source_image_to_state(file_path: str) -> NDArray[np.uint8]:
    img_raw, _meta = load_any_file_as_image(file_path)
    return np.asarray(img_raw.convert("RGB"), dtype=np.uint8)


def _uploaded_file_path(item: Any) -> str:
    if item is None:
        return ""
    path = getattr(item, "name", item)
    return str(path or "").strip()


def _uploaded_file_paths(file_obj: Any) -> list[str]:
    if file_obj is None:
        return []
    if isinstance(file_obj, (list, tuple)):
        paths: list[str] = []
        for item in cast(Sequence[Any], file_obj):
            path = _uploaded_file_path(item)
            if path:
                paths.append(path)
        return paths
    path = _uploaded_file_path(file_obj)
    return [path] if path else []


def _image_from_state(image_state: Any) -> Image.Image | None:
    if image_state is None:
        return None
    if isinstance(image_state, Image.Image):
        return image_state.convert("RGB")
    arr = np.asarray(image_state, dtype=np.uint8)
    if arr.ndim != 3:
        return None
    return Image.fromarray(arr, mode="RGB")


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    return str(value)


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, default=_json_default), encoding="utf-8")
    tmp_path.replace(path)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=True, default=_json_default) + "\n")


def _clamp_bbox(bbox: Sequence[Any], width: float, height: float) -> tuple[int, int, int, int] | None:
    if len(bbox) != 4:
        return None
    x1, y1, x2, y2 = [float(v) for v in bbox]
    left = int(np.clip(min(x1, x2), 0.0, max(width - 1.0, 0.0)))
    top = int(np.clip(min(y1, y2), 0.0, max(height - 1.0, 0.0)))
    right = int(np.clip(max(x1, x2), left + 1.0, max(width, left + 1.0)))
    bottom = int(np.clip(max(y1, y2), top + 1.0, max(height, top + 1.0)))
    if right - left < 2 or bottom - top < 2:
        return None
    return left, top, right, bottom


def _bbox_area(bbox: Sequence[Any]) -> float:
    if len(bbox) != 4:
        return 0.0
    x1, y1, x2, y2 = [float(v) for v in bbox]
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _bbox_overlap_ratio(left_bbox: Sequence[Any], right_bbox: Sequence[Any]) -> float:
    if len(left_bbox) != 4 or len(right_bbox) != 4:
        return 0.0
    lx1, ly1, lx2, ly2 = [float(v) for v in left_bbox]
    rx1, ry1, rx2, ry2 = [float(v) for v in right_bbox]
    ix1 = max(lx1, rx1)
    iy1 = max(ly1, ry1)
    ix2 = min(lx2, rx2)
    iy2 = min(ly2, ry2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    denom = max(min(_bbox_area(left_bbox), _bbox_area(right_bbox)), 1.0)
    return float(np.clip(inter / denom, 0.0, 1.0))


def _relative_bbox(abs_bbox: Sequence[Any], reference_bbox: Sequence[Any]) -> list[float]:
    if len(abs_bbox) != 4 or len(reference_bbox) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    rx1, ry1, rx2, ry2 = [float(v) for v in reference_bbox]
    rw = max(rx2 - rx1, 1.0)
    rh = max(ry2 - ry1, 1.0)
    ax1, ay1, ax2, ay2 = [float(v) for v in abs_bbox]
    return [
        float(np.clip((ax1 - rx1) / rw, 0.0, 1.0)),
        float(np.clip((ay1 - ry1) / rh, 0.0, 1.0)),
        float(np.clip((ax2 - rx1) / rw, 0.0, 1.0)),
        float(np.clip((ay2 - ry1) / rh, 0.0, 1.0)),
    ]


def _resolve_relative_bbox(relative_bbox: Sequence[Any], reference_bbox: Sequence[Any]) -> list[float]:
    if len(relative_bbox) != 4 or len(reference_bbox) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    rx1, ry1, rx2, ry2 = [float(v) for v in reference_bbox]
    rw = max(rx2 - rx1, 1.0)
    rh = max(ry2 - ry1, 1.0)
    nx1, ny1, nx2, ny2 = [float(v) for v in relative_bbox]
    return [
        float(rx1 + np.clip(nx1, 0.0, 1.0) * rw),
        float(ry1 + np.clip(ny1, 0.0, 1.0) * rh),
        float(rx1 + np.clip(nx2, 0.0, 1.0) * rw),
        float(ry1 + np.clip(ny2, 0.0, 1.0) * rh),
    ]


def _image_to_data_uri(
    image: Image.Image | None,
    *,
    max_width: int = 520,
    max_height: int = 340,
    fmt: str = "PNG",
) -> str:
    if image is None:
        return ""
    buffer = io.BytesIO()
    fmt_upper = fmt.upper()
    copied = image.copy()
    if not (fmt_upper == "PNG" and "A" in copied.getbands()):
        copied = copied.convert("RGB")
    copied.thumbnail((max_width, max_height), _pillow_lanczos())
    copied.save(buffer, format=fmt)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/{fmt.lower()};base64,{encoded}"


def _write_resized_image_asset(
    image: Image.Image | None,
    output_path: Path,
    *,
    max_width: int,
    max_height: int,
    fmt: str = "JPEG",
    quality: int = 82,
) -> str:
    if image is None:
        return ""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    copied = image.convert("RGB").copy()
    copied.thumbnail((max_width, max_height), _pillow_lanczos())
    save_kwargs: dict[str, Any] = {"format": fmt}
    fmt_upper = fmt.upper()
    if fmt_upper in {"JPEG", "WEBP"}:
        save_kwargs["quality"] = int(quality)
        save_kwargs["optimize"] = True
    if fmt_upper == "JPEG":
        save_kwargs["subsampling"] = 0
        save_kwargs["progressive"] = True
    if fmt_upper == "PNG":
        save_kwargs["optimize"] = True
    copied.save(buffer, **save_kwargs)
    output_path.write_bytes(buffer.getvalue())
    return str(output_path)


def _delete_file_quietly(path_str: str) -> None:
    if not path_str:
        return
    try:
        Path(path_str).unlink(missing_ok=True)
    except Exception:
        pass


def _image_uri_from_file(
    path_str: str,
    *,
    max_width: int,
    max_height: int,
) -> str:
    if not path_str:
        return ""
    file_path = Path(path_str)
    if not file_path.exists():
        return ""
    try:
        stat = file_path.stat()
    except Exception:
        return ""
    cache_key = f"file:{file_path.resolve()}:{int(stat.st_mtime_ns)}:{max_width}x{max_height}"
    def _build_uri() -> str:
        with Image.open(file_path) as image:
            return _image_to_data_uri(image, max_width=max_width, max_height=max_height)
    return _cached_compare_frame_uri(
        cache_key,
        _build_uri,
    )


def _compare_cache_prefix(result: Mapping[str, Any]) -> str:
    meta = cast(dict[str, Any], result.get("meta", {}))
    sha256 = str(meta.get("sha256", "")).strip()
    if sha256:
        return sha256
    file_name = str(meta.get("file_name", meta.get("source_name", "capture"))).strip() or "capture"
    timestamp = str(result.get("timestamp", "")).strip()
    return f"{file_name}:{timestamp}"


def _remember_compare_frame_uri(cache_key: str, uri: str) -> str:
    if not uri:
        return uri
    with _compare_frame_cache_lock:
        _compare_frame_uri_cache[cache_key] = uri
        while len(_compare_frame_uri_cache) > 96:
            _compare_frame_uri_cache.pop(next(iter(_compare_frame_uri_cache)))
    return uri


def _cached_compare_frame_uri(
    cache_key: str,
    factory: Any,
) -> str:
    with _compare_frame_cache_lock:
        cached = _compare_frame_uri_cache.get(cache_key, "")
    if cached:
        return cached
    return _remember_compare_frame_uri(cache_key, str(factory() or ""))


def _editor_item_to_image(item: Any) -> Image.Image | None:
    if item is None:
        return None
    if isinstance(item, Image.Image):
        return item.convert("RGBA")
    if isinstance(item, np.ndarray):
        arr = np.asarray(item, dtype=np.uint8)
        if arr.ndim == 2:
            arr = np.stack([arr, arr, arr, np.full_like(arr, 255)], axis=-1)
        if arr.ndim == 3 and arr.shape[2] == 3:
            alpha = np.full((arr.shape[0], arr.shape[1], 1), 255, dtype=np.uint8)
            arr = np.concatenate([arr, alpha], axis=2)
        if arr.ndim == 3 and arr.shape[2] == 4:
            return Image.fromarray(arr, mode="RGBA")
        return None
    if isinstance(item, str) and item.strip():
        try:
            return Image.open(item).convert("RGBA")
        except Exception:
            return None
    if isinstance(item, dict):
        item_dict = cast(dict[str, Any], item)
        for key in ("image", "composite", "background"):
            if key in item_dict:
                nested = _editor_item_to_image(item_dict.get(key))
                if nested is not None:
                    return nested
    return None


def _extract_editor_layers(editor_value: Any) -> list[Image.Image]:
    if not isinstance(editor_value, dict):
        return []
    layers_obj = cast(dict[str, Any], editor_value).get("layers", [])
    if not isinstance(layers_obj, list):
        return []
    out: list[Image.Image] = []
    for layer in cast(list[Any], layers_obj):
        image = _editor_item_to_image(layer)
        if image is not None:
            out.append(image)
    return out


def _build_zone_editor_value(
    source_image_state: Any,
    *,
    base_image: Image.Image | None = None,
) -> Image.Image | None:
    background = base_image.convert("RGBA") if isinstance(base_image, Image.Image) else None
    if background is not None:
        return background
    source_image = _image_from_state(source_image_state)
    if source_image is None:
        return None
    return source_image.convert("RGBA")


def _extract_painted_zone_regions(editor_value: Any) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for layer_idx, layer in enumerate(_extract_editor_layers(editor_value)):
        rgba = np.asarray(layer.convert("RGBA"), dtype=np.uint8)
        alpha = rgba[..., 3]
        ys, xs = np.where(alpha > 16)
        if len(xs) == 0 or len(ys) == 0:
            continue
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
        pixels = rgba[ys, xs, :3]
        rgb_mean = pixels.mean(axis=0) if pixels.size else np.array([0.0, 0.0, 0.0], dtype=np.float32)
        regions.append(
            {
                "layer_index": layer_idx,
                "bbox": bbox,
                "pixel_count": int(len(xs)),
                "mean_rgb": [float(rgb_mean[0]), float(rgb_mean[1]), float(rgb_mean[2])],
            }
        )
    return regions


def _load_zone_memory() -> list[dict[str, Any]]:
    global _zone_memory_cache
    with _zone_memory_lock:
        if _zone_memory_cache is not None:
            return [dict(row) for row in _zone_memory_cache]
        path = RUNTIME.zone_memory_path
        if not path.exists():
            _zone_memory_cache = []
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows: list[dict[str, Any]] = []
            if isinstance(payload, list):
                payload_rows = cast(list[Any], payload)
                for row in payload_rows:
                    if isinstance(row, dict):
                        row_dict = cast(dict[Any, Any], row)
                        rows.append({str(key): value for key, value in row_dict.items()})
        except Exception:
            rows = []
        _zone_memory_cache = rows
        return [dict(row) for row in _zone_memory_cache]


def _save_zone_memory(zones: list[dict[str, Any]]) -> None:
    global _zone_memory_cache
    with _zone_memory_lock:
        ordered = sorted(
            [dict(zone) for zone in zones],
            key=lambda row: str(row.get("created_at", "")),
        )
        _write_json_atomic(RUNTIME.zone_memory_path, ordered)
        _zone_memory_cache = ordered


def _get_session_snapshot() -> dict[str, Any]:
    with _session_runtime_lock:
        return {
            "session_id": str(_session_runtime_state.get("session_id", "")),
            "started_at": str(_session_runtime_state.get("started_at", "")),
            "entries": [dict(entry) for entry in cast(list[dict[str, Any]], _session_runtime_state.get("entries", []))],
        }


def _session_thumbnail_path(entry_id: str) -> Path:
    return RUNTIME.session_thumbnails_dir / f"{entry_id}.png"


def _session_entry_thumbnail_uri(entry: Mapping[str, Any]) -> str:
    return _image_uri_from_file(
        str(entry.get("thumbnail_path", "")),
        max_width=960,
        max_height=540,
    )


def _compare_asset_path(compare_key: str, suffix: str) -> Path:
    safe_key = re.sub(r"[^A-Za-z0-9._-]+", "_", str(compare_key or "compare")).strip("._") or "compare"
    safe_suffix = re.sub(r"[^A-Za-z0-9._-]+", "_", str(suffix or "asset")).strip("._") or "asset"
    return RUNTIME.compare_assets_dir / f"{safe_key}_{safe_suffix}.png"


def _append_session_entry(entry: Mapping[str, Any]) -> None:
    session_payload = dict(entry)
    persistent_payload = {
        key: value
        for key, value in session_payload.items()
        if key not in {"thumbnail_uri", "thumbnail_path"}
    }
    dropped_entries: list[dict[str, Any]] = []
    with _session_runtime_lock:
        _session_runtime_state["entries"].append(session_payload)
        entries = cast(list[dict[str, Any]], _session_runtime_state["entries"])
        if len(entries) > 120:
            dropped_entries = [dict(item) for item in entries[:-120]]
            _session_runtime_state["entries"] = entries[-120:]
        session_payload["session_id"] = _session_runtime_state["session_id"]
    for dropped in dropped_entries:
        _delete_file_quietly(str(dropped.get("thumbnail_path", "")))
    _append_jsonl(RUNTIME.session_log_path, persistent_payload)


def _build_session_entry(
    result: Mapping[str, Any],
    source_image_state: Any,
    file_path: str,
    *,
    source: str,
) -> dict[str, Any]:
    source_image = _image_from_state(source_image_state)
    projection = cast(dict[str, Any], result.get("projection", {}))
    zone_learning = cast(dict[str, Any], result.get("zone_learning", {}))
    multi_timeframe = cast(dict[str, Any], result.get("multi_timeframe", {}))
    entry_id = uuid4().hex
    thumbnail_path = _write_resized_image_asset(
        source_image,
        _session_thumbnail_path(entry_id),
        max_width=960,
        max_height=540,
        fmt="PNG",
    )
    return {
        "entry_id": entry_id,
        "timestamp": str(result.get("timestamp", utc_now_iso())),
        "source": source,
        "file_path": file_path,
        "file_name": os.path.basename(file_path),
        "action": str(result.get("action", "HOLD")).upper(),
        "confidence": float(result.get("confidence", 0.0) or 0.0),
        "expected_move_pct": float(result.get("expected_3min_move_pct", 0.0) or 0.0),
        "projection_direction": str(projection.get("direction", "HOLD")).upper(),
        "memory_similarity": float(result.get("memory_similarity", 0.0) or 0.0),
        "zone_match_count": int(zone_learning.get("match_count", 0) or 0),
        "zone_preferred_action": str(zone_learning.get("preferred_action", "HOLD")).upper(),
        "multi_timeframe": bool(multi_timeframe),
        "multi_timeframe_alignment": bool(multi_timeframe.get("aligned", False)) if multi_timeframe else False,
        "thumbnail_path": thumbnail_path,
    }


def _chart_reference_bbox(result: Mapping[str, Any]) -> list[float]:
    geometry = cast(dict[str, Any], result.get("chart_geometry", {}))
    plot_bbox = cast(list[float], geometry.get("plot_inner_bbox", geometry.get("plot_bbox", [])))
    if len(plot_bbox) == 4:
        return [float(v) for v in plot_bbox]
    meta = cast(dict[str, Any], result.get("meta", {}))
    width = float(meta.get("width", 1.0) or 1.0)
    height = float(meta.get("height", 1.0) or 1.0)
    return [0.0, 0.0, width, height]


def _candidate_zone_bboxes(result: Mapping[str, Any]) -> list[list[float]]:
    candidates: list[list[float]] = []
    for box in [
        cast(dict[str, Any], result.get("current_box", {})),
        cast(dict[str, Any], cast(dict[str, Any], result.get("projection", {})).get("next_box", {})),
    ]:
        bbox = cast(list[float], box.get("bbox", []))
        if len(bbox) == 4:
            candidates.append([float(v) for v in bbox])
    geometry = cast(dict[str, Any], result.get("chart_geometry", {}))
    latest_candle_bbox = cast(list[float], geometry.get("latest_candle_bbox", []))
    if len(latest_candle_bbox) == 4:
        candidates.append([float(v) for v in latest_candle_bbox])
    return candidates


def _normalize_probabilities(probs: Mapping[str, Any]) -> dict[str, float]:
    normalized: dict[str, float] = {
        str(key).upper(): float(max(0.0, float(value or 0.0)))
        for key, value in probs.items()
    }
    total = float(sum(normalized.values()))
    if total <= 1e-9:
        return {"BUY": 1.0 / 3.0, "SELL": 1.0 / 3.0, "HOLD": 1.0 / 3.0}
    return {key: float(value / total) for key, value in normalized.items()}


def _apply_probability_bias(
    probabilities: Mapping[str, Any],
    preferred_action: str,
    probability_bias: float,
) -> dict[str, float]:
    probs = _normalize_probabilities(probabilities)
    target = str(preferred_action or "HOLD").upper()
    if target not in probs or probability_bias <= 1e-6:
        return probs
    bias = float(np.clip(probability_bias, 0.0, 0.10))
    losers = [key for key in probs if key != target]
    if not losers:
        return probs
    probs[target] = min(0.985, probs[target] + bias)
    bleed = bias / len(losers)
    for key in losers:
        probs[key] = max(0.001, probs[key] - bleed)
    return _normalize_probabilities(probs)


def _directional_action(label: Any) -> str:
    action = str(label or "HOLD").upper()
    return action if action in {"BUY", "SELL"} else "HOLD"


def _momentum_bias_to_action(momentum_bias: Any) -> str:
    momentum = str(momentum_bias or "neutral").strip().lower()
    if momentum.startswith("bull"):
        return "BUY"
    if momentum.startswith("bear"):
        return "SELL"
    return "HOLD"


def _dedupe_text_items(items: Sequence[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _build_mtf_frame_profile(result: Mapping[str, Any]) -> dict[str, Any]:
    chart_state = cast(dict[str, Any], result.get("chart_state", {}))
    projection = cast(dict[str, Any], result.get("projection", {}))
    probabilities = _normalize_probabilities(cast(dict[str, Any], result.get("probabilities", {})))

    action = _directional_action(result.get("action", "HOLD"))
    action_confidence = float(
        np.clip(
            result.get("confidence", probabilities.get(action, 0.0) if action in probabilities else 0.0),
            0.0,
            1.0,
        )
    )
    engine_direction = _directional_action(chart_state.get("direction", action))
    engine_confidence = float(np.clip(chart_state.get("direction_probability", action_confidence), 0.0, 1.0))
    projection_direction = _directional_action(
        projection.get("direction", chart_state.get("projection_bias_direction", "HOLD"))
    )
    projection_confidence = float(
        np.clip(
            projection.get("confidence", chart_state.get("projection_bias_confidence", 0.0)),
            0.0,
            1.0,
        )
    )
    projection_dominance = float(
        np.clip(
            projection.get("dominance", chart_state.get("projection_dominance", 0.0)),
            0.0,
            1.0,
        )
    )
    structure_direction = _directional_action(chart_state.get("structure_bias_direction", "HOLD"))
    structure_confidence = float(np.clip(chart_state.get("structure_bias_confidence", 0.0), 0.0, 1.0))
    sequence_direction = _directional_action(chart_state.get("sequence_bias_direction", "HOLD"))
    sequence_confidence = float(np.clip(chart_state.get("sequence_bias_confidence", 0.0), 0.0, 1.0))
    momentum_direction = _momentum_bias_to_action(chart_state.get("momentum_bias", "neutral"))
    memory_direction = _directional_action(result.get("memory_direction", "HOLD"))
    memory_similarity = float(np.clip(result.get("memory_similarity", 0.0), 0.0, 1.0))
    path_clarity = float(np.clip(chart_state.get("path_clarity", 0.0), 0.0, 1.0))
    gates_passing = int(result.get("gates_passing", 0) or 0)
    gates_norm = float(np.clip(gates_passing / 12.0, 0.0, 1.0))
    structure_setup = str(chart_state.get("structure_setup", "none") or "none")
    structure_trade_ready = bool(chart_state.get("structure_trade_ready", False))
    continuation_probability = float(np.clip(chart_state.get("continuation_probability", 0.0), 0.0, 1.0))
    reversal_probability = float(np.clip(chart_state.get("reversal_probability", 0.0), 0.0, 1.0))
    macro_trend = str(chart_state.get("macro_trend", "unknown") or "unknown").upper()
    local_phase = str(chart_state.get("local_phase", "unknown") or "unknown")
    entry_type = str(chart_state.get("entry_type", "continuation") or "continuation")
    countertrend_ready = bool(
        structure_setup == "reversal_release"
        or (entry_type == "reversal" and reversal_probability >= 0.50)
    )

    directional_scores = {
        "BUY": float(probabilities.get("BUY", 0.0)),
        "SELL": float(probabilities.get("SELL", 0.0)),
    }
    components: list[dict[str, Any]] = []

    def add_component(direction: Any, weight: float, label: str) -> None:
        dir_key = _directional_action(direction)
        value = float(np.clip(weight, 0.0, 0.60))
        if dir_key not in {"BUY", "SELL"} or value <= 1e-6:
            return
        directional_scores[dir_key] = float(directional_scores.get(dir_key, 0.0) + value)
        components.append(
            {
                "direction": dir_key,
                "weight": value,
                "label": label,
            }
        )

    if action in {"BUY", "SELL"}:
        add_component(action, 0.22 + 0.22 * action_confidence, f"action {action} {action_confidence:.2f}")
    if engine_direction in {"BUY", "SELL"}:
        add_component(engine_direction, 0.10 + 0.14 * engine_confidence, f"engine {engine_direction} {engine_confidence:.2f}")
    if projection_direction in {"BUY", "SELL"}:
        add_component(
            projection_direction,
            0.12 + 0.18 * projection_confidence + 0.08 * projection_dominance,
            f"projection {projection_direction} {projection_confidence:.2f}",
        )
    if structure_direction in {"BUY", "SELL"} and structure_confidence > 0.0:
        add_component(
            structure_direction,
            0.08 + 0.16 * structure_confidence,
            f"structure {structure_direction} {structure_confidence:.2f}",
        )
    if sequence_direction in {"BUY", "SELL"} and sequence_confidence > 0.0:
        add_component(
            sequence_direction,
            0.06 + 0.12 * sequence_confidence,
            f"sequence {sequence_direction} {sequence_confidence:.2f}",
        )
    if memory_direction in {"BUY", "SELL"} and memory_similarity > 0.05:
        add_component(
            memory_direction,
            0.05 + 0.12 * memory_similarity,
            f"memory {memory_direction} {memory_similarity:.2f}",
        )
    if momentum_direction in {"BUY", "SELL"}:
        add_component(momentum_direction, 0.05, f"momentum {momentum_direction}")
    if structure_trade_ready and projection_direction in {"BUY", "SELL"}:
        add_component(
            projection_direction,
            0.06 + 0.10 * path_clarity,
            f"setup {structure_setup}",
        )
    if continuation_probability >= 0.56 and engine_direction in {"BUY", "SELL"}:
        add_component(
            engine_direction,
            0.03 + 0.05 * continuation_probability,
            f"continuation {continuation_probability:.2f}",
        )
    if reversal_probability >= 0.52 and projection_direction in {"BUY", "SELL"}:
        add_component(
            projection_direction,
            0.03 + 0.04 * reversal_probability,
            f"reversal {reversal_probability:.2f}",
        )

    directional_total = float(directional_scores["BUY"] + directional_scores["SELL"])
    directional_spread = float(directional_scores["BUY"] - directional_scores["SELL"])
    directional_balance = (
        abs(directional_spread) / max(directional_total, 1e-6)
        if directional_total > 1e-6
        else 0.0
    )
    bias_direction = "BUY" if directional_spread > 0.05 else ("SELL" if directional_spread < -0.05 else "HOLD")

    conviction = float(
        np.clip(
            0.30 * max(probabilities.get("BUY", 0.0), probabilities.get("SELL", 0.0))
            + 0.22 * projection_confidence
            + 0.14 * structure_confidence
            + 0.10 * sequence_confidence
            + 0.10 * path_clarity
            + 0.08 * gates_norm
            + (0.06 if bool(result.get("consensus_ok", False)) else 0.0),
            0.0,
            1.0,
        )
    )
    agreement_bonus = 0.0
    if bias_direction in {"BUY", "SELL"}:
        if action == bias_direction:
            agreement_bonus += 0.08
        if projection_direction == bias_direction:
            agreement_bonus += 0.10
        if structure_direction == bias_direction and structure_trade_ready:
            agreement_bonus += 0.08
        if sequence_direction == bias_direction:
            agreement_bonus += 0.05
        if momentum_direction == bias_direction:
            agreement_bonus += 0.04
    bias_strength = float(
        np.clip(
            (0.42 * directional_balance + 0.38 * conviction + agreement_bonus)
            if bias_direction in {"BUY", "SELL"}
            else (0.24 * directional_balance + 0.30 * conviction),
            0.0,
            1.0,
        )
    )

    entry_direction = action if action in {"BUY", "SELL"} else projection_direction
    if entry_direction not in {"BUY", "SELL"}:
        entry_direction = bias_direction if bias_direction in {"BUY", "SELL"} else "HOLD"
    entry_confidence = float(
        np.clip(
            max(
                probabilities.get(entry_direction, 0.0),
                action_confidence if action == entry_direction else 0.0,
                engine_confidence if engine_direction == entry_direction else 0.0,
                projection_confidence if projection_direction == entry_direction else 0.0,
                structure_confidence if structure_direction == entry_direction else 0.0,
                sequence_confidence if sequence_direction == entry_direction else 0.0,
                bias_strength if bias_direction == entry_direction else 0.0,
            ),
            0.0,
            1.0,
        )
    )
    bias_reasons = [
        str(component.get("label", ""))
        for component in components
        if str(component.get("direction", "")) == bias_direction
    ]

    return {
        "action": action,
        "action_confidence": action_confidence,
        "engine_direction": engine_direction,
        "engine_confidence": engine_confidence,
        "projection_direction": projection_direction,
        "projection_confidence": projection_confidence,
        "projection_dominance": projection_dominance,
        "structure_direction": structure_direction,
        "structure_confidence": structure_confidence,
        "sequence_direction": sequence_direction,
        "sequence_confidence": sequence_confidence,
        "momentum_direction": momentum_direction,
        "memory_direction": memory_direction,
        "memory_similarity": memory_similarity,
        "path_clarity": path_clarity,
        "gates_passing": gates_passing,
        "gates_norm": gates_norm,
        "structure_setup": structure_setup,
        "structure_trade_ready": structure_trade_ready,
        "continuation_probability": continuation_probability,
        "reversal_probability": reversal_probability,
        "macro_trend": macro_trend,
        "local_phase": local_phase,
        "entry_type": entry_type,
        "countertrend_ready": countertrend_ready,
        "bias_direction": bias_direction,
        "bias_strength": bias_strength,
        "entry_direction": entry_direction,
        "entry_confidence": entry_confidence,
        "components": components,
        "bias_reasons": _dedupe_text_items(bias_reasons),
        "directional_scores": directional_scores,
    }


def _evaluate_multi_timeframe_gate(
    lead_profile: Mapping[str, Any],
    trigger_profile: Mapping[str, Any],
) -> dict[str, Any]:
    trigger_direction = _directional_action(trigger_profile.get("entry_direction", "HOLD"))
    if trigger_direction not in {"BUY", "SELL"}:
        explanation = "Lower timeframe does not have a directional trigger to validate yet."
        return {
            "state": "watch",
            "entry_allowed": False,
            "gate_score": 0.0,
            "gate_strength": 0.0,
            "trigger_direction": trigger_direction,
            "lead_bias_direction": _directional_action(lead_profile.get("bias_direction", "HOLD")),
            "confirmation_reasons": [],
            "blocking_reasons": [],
            "explanation": explanation,
            "headline": "Higher timeframe is waiting for a cleaner lower-timeframe trigger.",
        }

    lead_bias = _directional_action(lead_profile.get("bias_direction", "HOLD"))
    lead_projection = _directional_action(lead_profile.get("projection_direction", "HOLD"))
    lead_momentum = _directional_action(lead_profile.get("momentum_direction", "HOLD"))
    lead_memory = _directional_action(lead_profile.get("memory_direction", "HOLD"))
    lead_strength = float(np.clip(lead_profile.get("bias_strength", 0.0), 0.0, 1.0))
    lead_projection_conf = float(np.clip(lead_profile.get("projection_confidence", 0.0), 0.0, 1.0))
    lead_memory_similarity = float(np.clip(lead_profile.get("memory_similarity", 0.0), 0.0, 1.0))
    trigger_strength = float(np.clip(trigger_profile.get("entry_confidence", 0.0), 0.0, 1.0))
    trigger_projection = _directional_action(trigger_profile.get("projection_direction", "HOLD"))
    trigger_projection_conf = float(np.clip(trigger_profile.get("projection_confidence", 0.0), 0.0, 1.0))

    support_score = 0.0
    block_score = 0.0
    confirmation_reasons: list[str] = []
    blocking_reasons: list[str] = []

    if lead_bias == trigger_direction:
        support_score += 0.36 + 0.34 * lead_strength
        confirmation_reasons.append(f"higher bias is {lead_bias} ({lead_strength:.2f})")
    elif lead_bias in {"BUY", "SELL"}:
        block_score += 0.40 + 0.36 * lead_strength
        blocking_reasons.append(f"higher bias is {lead_bias} ({lead_strength:.2f})")

    if lead_projection == trigger_direction and lead_projection_conf >= 0.38:
        support_score += 0.18 + 0.18 * lead_projection_conf
        confirmation_reasons.append(f"higher projection confirms {trigger_direction} ({lead_projection_conf:.2f})")
    elif lead_projection in {"BUY", "SELL"} and lead_projection != trigger_direction and lead_projection_conf >= 0.45:
        block_score += 0.17 + 0.20 * lead_projection_conf
        blocking_reasons.append(f"higher projection points {lead_projection} ({lead_projection_conf:.2f})")

    if bool(lead_profile.get("structure_trade_ready", False)) and lead_projection == trigger_direction:
        support_score += 0.12 + 0.10 * float(np.clip(lead_profile.get("path_clarity", 0.0), 0.0, 1.0))
        confirmation_reasons.append(f"higher setup {str(lead_profile.get('structure_setup', 'none'))} is trade-ready")
    elif bool(lead_profile.get("structure_trade_ready", False)) and lead_projection in {"BUY", "SELL"} and lead_projection != trigger_direction:
        block_score += 0.12 + 0.10 * float(np.clip(lead_profile.get("path_clarity", 0.0), 0.0, 1.0))
        blocking_reasons.append(f"higher setup {str(lead_profile.get('structure_setup', 'none'))} points {lead_projection}")

    if lead_momentum == trigger_direction:
        support_score += 0.07
        confirmation_reasons.append(f"higher momentum leans {lead_momentum}")
    elif lead_momentum in {"BUY", "SELL"}:
        block_score += 0.07
        blocking_reasons.append(f"higher momentum leans {lead_momentum}")

    if lead_memory == trigger_direction and lead_memory_similarity >= 0.10:
        support_score += 0.05 + 0.08 * lead_memory_similarity
        confirmation_reasons.append(f"higher memory recall agrees ({lead_memory_similarity:.2f})")
    elif lead_memory in {"BUY", "SELL"} and lead_memory != trigger_direction and lead_memory_similarity >= 0.14:
        block_score += 0.04 + 0.08 * lead_memory_similarity
        blocking_reasons.append(f"higher memory recall leans {lead_memory} ({lead_memory_similarity:.2f})")

    if bool(trigger_profile.get("structure_trade_ready", False)):
        support_score += 0.05 + 0.07 * float(np.clip(trigger_profile.get("path_clarity", 0.0), 0.0, 1.0))
        confirmation_reasons.append(f"lower setup {str(trigger_profile.get('structure_setup', 'none'))} is trade-ready")
    else:
        block_score += 0.04
        blocking_reasons.append("lower setup is not fully trade-ready")

    if trigger_projection == trigger_direction and trigger_projection_conf >= 0.45:
        support_score += 0.06 + 0.08 * trigger_projection_conf
        confirmation_reasons.append(f"lower projection confirms {trigger_direction} ({trigger_projection_conf:.2f})")

    if bool(trigger_profile.get("countertrend_ready", False)) and lead_bias in {"BUY", "SELL"} and lead_bias != trigger_direction:
        block_score *= 0.82
        support_score += 0.04
        confirmation_reasons.append("lower reversal structure softens the higher-timeframe veto")

    if trigger_strength < 0.45:
        block_score += 0.06
        blocking_reasons.append("lower timeframe conviction is still soft")

    gate_score = float(np.clip(support_score - block_score, -1.0, 1.0))
    if (
        lead_bias in {"BUY", "SELL"}
        and lead_bias != trigger_direction
        and lead_strength >= 0.58
        and gate_score <= -0.08
        and not bool(trigger_profile.get("countertrend_ready", False))
    ) or (block_score >= 0.60 and gate_score <= -0.16):
        state = "blocked"
    elif support_score >= 0.48 and gate_score >= 0.12:
        state = "confirmed"
    else:
        state = "watch"

    if state == "confirmed":
        gate_strength = float(
            np.clip(
                0.55 * lead_strength + 0.45 * trigger_strength + 0.20 * max(gate_score, 0.0),
                0.0,
                1.0,
            )
        )
    elif state == "blocked":
        gate_strength = float(
            np.clip(
                0.60 * lead_strength + 0.20 * trigger_strength + 0.18 * min(block_score, 1.0),
                0.0,
                1.0,
            )
        )
    else:
        gate_strength = float(
            np.clip(
                0.40 * lead_strength + 0.30 * trigger_strength + 0.15 * abs(gate_score),
                0.0,
                1.0,
            )
        )

    confirmation_reasons = _dedupe_text_items(confirmation_reasons)
    blocking_reasons = _dedupe_text_items(blocking_reasons)
    if state == "confirmed":
        explanation = (
            f"Higher timeframe confirms the {trigger_direction} trigger: "
            + "; ".join(confirmation_reasons[:3])
            + "."
        )
        headline = f"Higher timeframe confirms the {trigger_direction} entry."
    elif state == "blocked":
        explanation = (
            f"Higher timeframe blocks the {trigger_direction} trigger: "
            + "; ".join(blocking_reasons[:3])
            + "."
        )
        headline = f"Higher timeframe blocks the {trigger_direction} entry."
    else:
        mixed_parts = confirmation_reasons[:2] + blocking_reasons[:2]
        explanation = (
            f"Higher timeframe is mixed on the {trigger_direction} trigger: "
            + "; ".join(mixed_parts[:4])
            + "."
            if mixed_parts
            else f"Higher timeframe is mixed on the {trigger_direction} trigger."
        )
        headline = f"Higher timeframe is mixed on the {trigger_direction} entry."

    return {
        "state": state,
        "entry_allowed": bool(state != "blocked"),
        "gate_score": gate_score,
        "gate_strength": gate_strength,
        "trigger_direction": trigger_direction,
        "lead_bias_direction": lead_bias,
        "confirmation_reasons": confirmation_reasons,
        "blocking_reasons": blocking_reasons,
        "explanation": explanation,
        "headline": headline,
    }


def _match_zone_memory_to_result(result: Mapping[str, Any]) -> dict[str, Any]:
    zones = _load_zone_memory()
    if not zones:
        return {
            "match_count": 0,
            "preferred_action": "HOLD",
            "probability_bias": 0.0,
            "alignment_score": 0.0,
            "matching_zones": [],
            "visible_zones": [],
        }

    reference_bbox = _chart_reference_bbox(result)
    structure_setup = str(cast(dict[str, Any], result.get("chart_state", {})).get("structure_setup", "none"))
    current_action = str(result.get("action", "HOLD")).upper()
    projection_direction = str(cast(dict[str, Any], result.get("projection", {})).get("direction", "HOLD")).upper()
    candidate_boxes = _candidate_zone_bboxes(result)

    buy_bias = 0.0
    sell_bias = 0.0
    matching_zones: list[dict[str, Any]] = []
    visible_zones: list[dict[str, Any]] = []

    for zone in zones[-300:]:
        relative_bbox = cast(list[float], zone.get("relative_bbox", []))
        if len(relative_bbox) != 4:
            continue
        abs_bbox = _resolve_relative_bbox(relative_bbox, reference_bbox)
        overlap = max((_bbox_overlap_ratio(abs_bbox, box) for box in candidate_boxes), default=0.0)
        if overlap < 0.03:
            continue
        strength = float(np.clip(zone.get("strength", 0.7), 0.1, 1.0))
        score = 0.72 * overlap + 0.28 * strength
        if str(zone.get("structure_setup", "none")) == structure_setup:
            score += 0.08
        if str(zone.get("context_projection", "HOLD")).upper() == projection_direction:
            score += 0.06
        if str(zone.get("context_action", "HOLD")).upper() == current_action:
            score += 0.04
        score = float(np.clip(score, 0.0, 1.0))
        kind = str(zone.get("kind", "reaction")).lower()
        matching_row: dict[str, Any] = {
            "zone_id": str(zone.get("zone_id", "")),
            "kind": kind,
            "label": str(zone.get("label", "")),
            "notes": str(zone.get("notes", "")),
            "score": score,
            "bbox": abs_bbox,
            "strength": strength,
        }
        matching_zones.append(matching_row)
        visible_zones.append(
            {
                "kind": kind,
                "label": str(zone.get("label", kind.title())),
                "bbox": abs_bbox,
                "score": score,
            }
        )
        if kind == "support":
            buy_bias += score
        elif kind == "resistance":
            sell_bias += score
        else:
            context_action = str(zone.get("context_action", "HOLD")).upper()
            if context_action == "BUY":
                buy_bias += score * 0.55
            elif context_action == "SELL":
                sell_bias += score * 0.55

    matching_zones.sort(key=lambda row: float(row.get("score", 0.0)), reverse=True)
    visible_zones = visible_zones[:6]
    total_bias = max(buy_bias + sell_bias, 1e-6)
    preferred_action = "HOLD"
    if buy_bias > sell_bias * 1.08:
        preferred_action = "BUY"
    elif sell_bias > buy_bias * 1.08:
        preferred_action = "SELL"
    alignment_score = float(np.clip(abs(buy_bias - sell_bias) / total_bias, 0.0, 1.0))
    probability_bias = float(np.clip(total_bias * 0.035 * max(alignment_score, 0.2), 0.0, 0.08))
    return {
        "match_count": len(matching_zones),
        "preferred_action": preferred_action,
        "probability_bias": probability_bias,
        "alignment_score": alignment_score,
        "buy_bias": float(buy_bias),
        "sell_bias": float(sell_bias),
        "matching_zones": matching_zones[:8],
        "visible_zones": visible_zones,
    }


def _apply_zone_memory_to_result(result: Mapping[str, Any]) -> dict[str, Any]:
    enriched: dict[str, Any] = copy.deepcopy(dict(result))
    zone_learning = _match_zone_memory_to_result(enriched)
    probabilities = _normalize_probabilities(cast(dict[str, Any], enriched.get("probabilities", {})))
    preferred_action = str(zone_learning.get("preferred_action", "HOLD")).upper()
    probability_bias = float(zone_learning.get("probability_bias", 0.0) or 0.0)
    if zone_learning.get("match_count") and preferred_action in {"BUY", "SELL"}:
        probabilities = _apply_probability_bias(probabilities, preferred_action, probability_bias)
    action = max(probabilities.items(), key=lambda item: float(item[1]))[0]
    enriched["probabilities"] = probabilities
    enriched["action"] = action
    confidence_val = probabilities.get(action, enriched.get("confidence", 0.0))
    try:
        enriched["confidence"] = float(confidence_val) if confidence_val is not None else 0.0
    except Exception:
        enriched["confidence"] = 0.0
    zone_learning["applied"] = bool(zone_learning.get("match_count", 0))
    zone_learning["result_action_after_zone_bias"] = action
    enriched["zone_learning"] = zone_learning
    return enriched


def _save_zone_teaching(
    editor_value: Any,
    zone_kind: str,
    label: str,
    notes: str,
    strength: float,
    active_file_path: str,
    result_state: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    if result_state is None:
        return False, "Run a chart first before saving a zone."
    regions = _extract_painted_zone_regions(editor_value)
    if not regions:
        return False, "Draw at least one zone region before saving."
    result = cast(dict[str, Any], result_state)
    reference_bbox = _chart_reference_bbox(result)
    structure_setup = str(cast(dict[str, Any], result.get("chart_state", {})).get("structure_setup", "none"))
    projection_direction = str(cast(dict[str, Any], result.get("projection", {})).get("direction", "HOLD")).upper()
    action = str(result.get("action", "HOLD")).upper()
    zones = _load_zone_memory()
    created_at = utc_now_iso()
    for region in regions:
        bbox = cast(list[float], region.get("bbox", []))
        zones.append(
            {
                "zone_id": uuid4().hex,
                "created_at": created_at,
                "kind": str(zone_kind or "reaction").lower(),
                "label": str(label or zone_kind or "Zone").strip() or "Zone",
                "notes": str(notes or "").strip(),
                "strength": float(np.clip(float(strength or 0.7), 0.1, 1.0)),
                "relative_bbox": _relative_bbox(bbox, reference_bbox),
                "source_file": os.path.basename(str(active_file_path or "")),
                "context_action": action,
                "context_projection": projection_direction,
                "structure_setup": structure_setup,
                "pixel_count": int(region.get("pixel_count", 0) or 0),
            }
        )
    _save_zone_memory(zones)
    return True, f"Saved {len(regions)} {str(zone_kind).lower()} zone teaching region(s)."


def _capture_file_key(file_path: str) -> str:
    return os.path.abspath(file_path).lower()


def _update_capture_runtime_state(**kwargs: Any) -> None:
    with _capture_runtime_lock:
        _capture_runtime_state.update(kwargs)
        _bump_capture_status_token()


def _get_capture_runtime_snapshot() -> dict[str, Any]:
    with _capture_runtime_lock:
        pending_bundle = cast(list[dict[str, Any]], _capture_runtime_state.get("pending_bundle", []))
        return {
            "requested_hotkey": str(_capture_runtime_state.get("requested_hotkey", "")),
            "active_hotkey": str(_capture_runtime_state.get("active_hotkey", "")),
            "status": str(_capture_runtime_state.get("status", "")),
            "last_capture_time": str(_capture_runtime_state.get("last_capture_time", "")),
            "last_capture_file": str(_capture_runtime_state.get("last_capture_file", "")),
            "last_error": str(_capture_runtime_state.get("last_error", "")),
            "selection_active": bool(_capture_runtime_state.get("selection_active", False)),
            "inference_active": bool(_capture_runtime_state.get("inference_active", False)),
            "token": int(_capture_runtime_state.get("token", 0) or 0),
            "status_token": int(_capture_runtime_state.get("status_token", 0) or 0),
            "pending_bundle_count": len(pending_bundle),
            "bundle_size": int(_capture_runtime_state.get("bundle_size", max(1, RUNTIME.capture_bundle_size)) or max(1, RUNTIME.capture_bundle_size)),
            "bundle_started_at": str(_capture_runtime_state.get("bundle_started_at", "")),
            "last_bundle_id": str(_capture_runtime_state.get("last_bundle_id", "")),
        }


def _get_latest_capture_payload() -> tuple[int, dict[str, Any] | None, Any, str, Any, Any, Any]:
    with _capture_runtime_lock:
        return (
            int(_capture_runtime_state.get("token", 0) or 0),
            cast(dict[str, Any] | None, _capture_runtime_state.get("latest_result")),
            _capture_runtime_state.get("latest_source_image_state"),
            str(_capture_runtime_state.get("latest_file_path", "")),
            _capture_runtime_state.get("latest_overlay"),
            _capture_runtime_state.get("latest_gauge"),
            _capture_runtime_state.get("latest_skill_fig"),
        )


def _parse_hotkey_spec(spec: str) -> tuple[int, int, str]:
    normalized = str(spec or "").upper().replace(" ", "")
    if not normalized:
        raise ValueError("Empty hotkey specification.")

    mod_map = {
        "ALT": 0x0001,
        "CTRL": 0x0002,
        "CONTROL": 0x0002,
        "SHIFT": 0x0004,
        "WIN": 0x0008,
        "META": 0x0008,
    }
    display_map = {
        "ALT": "Alt",
        "CTRL": "Ctrl",
        "CONTROL": "Ctrl",
        "SHIFT": "Shift",
        "WIN": "Win",
        "META": "Win",
    }
    key_map = {
        "SPACE": 0x20,
        "TAB": 0x09,
        "ENTER": 0x0D,
        "ESC": 0x1B,
    }

    modifiers = 0x4000
    display_parts: list[str] = []
    key_part = ""
    for part in [chunk for chunk in normalized.split("+") if chunk]:
        if part in mod_map:
            modifiers |= mod_map[part]
            display_value = display_map[part]
            if display_value not in display_parts:
                display_parts.append(display_value)
        else:
            key_part = part

    if not key_part:
        raise ValueError(f"Hotkey '{spec}' is missing a key.")
    if len(key_part) == 1 and key_part.isalnum():
        vk_code = ord(key_part.upper())
        key_label = key_part.upper()
    elif re.fullmatch(r"F([1-9]|1[0-2])", key_part):
        vk_code = 0x6F + int(key_part[1:])
        key_label = key_part
    elif key_part in key_map:
        vk_code = key_map[key_part]
        key_label = key_part.title()
    else:
        raise ValueError(f"Unsupported hotkey key '{key_part}'.")

    return modifiers, vk_code, "+".join([*display_parts, key_label])


def _virtual_screen_bounds() -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    x = int(user32.GetSystemMetrics(76))
    y = int(user32.GetSystemMetrics(77))
    width = int(user32.GetSystemMetrics(78))
    height = int(user32.GetSystemMetrics(79))
    if width <= 0 or height <= 0:
        x = 0
        y = 0
        width = int(user32.GetSystemMetrics(0))
        height = int(user32.GetSystemMetrics(1))
    return x, y, width, height


def _select_capture_region_with_overlay(hotkey_label: str) -> tuple[int, int, int, int] | None:
    import tkinter as tk

    vx, vy, vw, vh = _virtual_screen_bounds()
    selection: dict[str, Any] = {"bbox": None, "rect": None, "start_root": None, "size_text": None}
    confirmed: dict[str, Any] = {"bbox": None}

    root = tk.Tk()
    root.withdraw()
    overlay = tk.Toplevel(root)
    overlay.overrideredirect(True)
    overlay.geometry(f"{vw}x{vh}{vx:+d}{vy:+d}")
    overlay.configure(bg="#061017")
    _tk_attributes(overlay, "-topmost", True)
    try:
        _tk_attributes(overlay, "-alpha", 0.22)
    except tk.TclError:
        pass

    canvas = tk.Canvas(overlay, bg="#061017", highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)
    headline = canvas.create_text(
        24,
        24,
        anchor="nw",
        fill="#f5efe4",
        text=f"{hotkey_label}: drag to size the chart capture.",
        font=("Segoe UI", 14, "bold"),
    )
    _subtitle = canvas.create_text(
        24,
        54,
        anchor="nw",
        fill="#97a6b3",
        text="Release the mouse to lock the area. Press Enter to confirm or Esc to cancel. Drag again to redraw.",
        font=("Segoe UI", 11),
    )

    def _clear_rect() -> None:
        if selection.get("rect") is not None:
            canvas.delete(selection["rect"])
            selection["rect"] = None
        if selection.get("size_text") is not None:
            canvas.delete(selection["size_text"])
            selection["size_text"] = None

    def _begin_drag(event: Any) -> None:
        _clear_rect()
        selection["bbox"] = None
        selection["start_root"] = (int(event.x_root), int(event.y_root), int(event.x), int(event.y))
        selection["rect"] = canvas.create_rectangle(
            event.x,
            event.y,
            event.x,
            event.y,
            outline="#4ca59a",
            width=3,
            dash=(10, 4),
        )
        canvas.itemconfigure(headline, text=f"{hotkey_label}: drag to define the capture area.")

    def _drag(event: Any) -> None:
        start = selection.get("start_root")
        rect_id = selection.get("rect")
        if not start or rect_id is None:
            return
        _sx_root, _sy_root, sx_canvas, sy_canvas = start
        canvas.coords(rect_id, sx_canvas, sy_canvas, event.x, event.y)

    def _finish_drag(event: Any) -> None:
        start = selection.get("start_root")
        rect_id = selection.get("rect")
        if not start or rect_id is None:
            return
        sx_root, sy_root, sx_canvas, sy_canvas = start
        left, right = sorted((int(sx_root), int(event.x_root)))
        top, bottom = sorted((int(sy_root), int(event.y_root)))
        width = right - left
        height = bottom - top
        if width < 16 or height < 16:
            selection["bbox"] = None
            canvas.itemconfigure(headline, text="Selection too small. Drag a larger chart region.")
            canvas.coords(rect_id, sx_canvas, sy_canvas, event.x, event.y)
            return
        selection["bbox"] = (left, top, right, bottom)
        if selection.get("size_text") is not None:
            canvas.delete(selection["size_text"])
        selection["size_text"] = canvas.create_text(
            min(max(event.x + 14, 24), max(vw - 160, 24)),
            min(max(event.y + 14, 80), max(vh - 24, 80)),
            anchor="nw",
            fill="#f5efe4",
            text=f"{width} x {height}",
            font=("Segoe UI", 11, "bold"),
        )
        canvas.itemconfigure(
            headline,
            text=f"Selection locked at {width} x {height}. Press Enter to confirm or drag again to redraw.",
        )

    def _confirm(_event: Any | None = None) -> None:
        bbox = selection.get("bbox")
        if bbox is None:
            canvas.itemconfigure(headline, text="Drag to create a valid selection before confirming.")
            return
        confirmed["bbox"] = cast(tuple[int, int, int, int], bbox)
        overlay.quit()

    def _cancel(_event: Any | None = None) -> None:
        confirmed["bbox"] = None
        overlay.quit()

    canvas.bind("<ButtonPress-1>", _begin_drag)
    canvas.bind("<B1-Motion>", _drag)
    canvas.bind("<ButtonRelease-1>", _finish_drag)
    overlay.bind("<Return>", _confirm)
    overlay.bind("<Escape>", _cancel)
    overlay.focus_force()
    overlay.deiconify()
    overlay.mainloop()
    try:
        overlay.destroy()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass
    return cast(tuple[int, int, int, int] | None, confirmed["bbox"])


def _save_capture_region_to_inbox(bbox: tuple[int, int, int, int]) -> str:
    time.sleep(0.08)
    try:
        captured = ImageGrab.grab(bbox=bbox, all_screens=True)
    except TypeError:
        captured = ImageGrab.grab(bbox=bbox)
    file_stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    millis = int((time.time() % 1.0) * 1000.0)
    file_path = RUNTIME.screenshots_inbox / f"hotkey_capture_{file_stamp}_{millis:03d}.png"
    captured.save(file_path)
    return str(file_path)


def _show_capture_hud(title: str, body: str, timeout_ms: int = 1600) -> None:
    if os.name != "nt":
        return

    def _runner() -> None:
        try:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            hud = tk.Toplevel(root)
            hud.overrideredirect(True)
            _tk_attributes(hud, "-topmost", True)
            width = 360
            height = 124
            screen_width = hud.winfo_screenwidth()
            x = max(24, screen_width - width - 36)
            y = 42
            hud.geometry(f"{width}x{height}+{x}+{y}")
            hud.configure(bg="#0d161d")
            frame = tk.Frame(hud, bg="#0d161d", bd=1, relief="solid", highlightthickness=0)
            frame.pack(fill="both", expand=True)
            tk.Label(frame, text=title, fg="#f5efe4", bg="#0d161d", font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=16, pady=(16, 6))
            tk.Label(frame, text=body, fg="#97a6b3", bg="#0d161d", justify="left", wraplength=320, font=("Segoe UI", 10)).pack(anchor="w", padx=16)
            hud.after(int(max(timeout_ms, 600)), hud.destroy)
            hud.after(int(max(timeout_ms, 600)) + 80, root.destroy)
            hud.mainloop()
        except Exception:
            return

    threading.Thread(target=_runner, daemon=True, name="pg-capture-hud").start()


def _clear_expired_bundle_locked() -> None:
    bundle_started_at = str(_capture_runtime_state.get("bundle_started_at", ""))
    if not bundle_started_at:
        return
    started_epoch = float(_capture_runtime_state.get("bundle_started_epoch", 0.0) or 0.0)
    if started_epoch <= 0.0:
        return
    if time.time() - started_epoch < float(max(30, RUNTIME.capture_bundle_timeout_sec)):
        return
    _capture_runtime_state["pending_bundle"] = []
    _capture_runtime_state["bundle_started_at"] = ""
    _capture_runtime_state["bundle_started_epoch"] = 0.0
    _bump_capture_status_token()


def _queue_hotkey_capture(file_path: str) -> tuple[bool, list[dict[str, Any]], int, int]:
    with _capture_runtime_lock:
        _clear_expired_bundle_locked()
        bundle = cast(list[dict[str, Any]], _capture_runtime_state.get("pending_bundle", []))
        bundle_size = int(_capture_runtime_state.get("bundle_size", max(1, RUNTIME.capture_bundle_size)) or max(1, RUNTIME.capture_bundle_size))
        if not bundle:
            _capture_runtime_state["bundle_started_at"] = utc_now_iso()
            _capture_runtime_state["bundle_started_epoch"] = time.time()
        slot_index = len(bundle) + 1
        bundle.append(
            {
                "file_path": file_path,
                "captured_at": utc_now_iso(),
                "slot_index": slot_index,
            }
        )
        _capture_runtime_state["pending_bundle"] = bundle
        _capture_runtime_state["last_capture_file"] = os.path.basename(file_path)
        _capture_runtime_state["last_capture_time"] = utc_now_iso()
        ready = len(bundle) >= bundle_size
        bundle_payload = copy.deepcopy(bundle)
        if ready:
            _capture_runtime_state["pending_bundle"] = []
            _capture_runtime_state["bundle_started_at"] = ""
            _capture_runtime_state["bundle_started_epoch"] = 0.0
            _capture_runtime_state["last_bundle_id"] = uuid4().hex
        _bump_capture_status_token()
        return ready, bundle_payload, slot_index, bundle_size


def _build_timeframe_compare_entry(
    result: Mapping[str, Any],
    source_image_state: Any,
    file_path: str,
    label: str,
    overlay_image: Image.Image | None = None,
    render_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_image = _image_from_state(source_image_state)
    cache_prefix = _compare_cache_prefix(result)
    label_key = re.sub(r"[^A-Za-z0-9._-]+", "_", str(label or "frame")).strip("._") or "frame"
    render_state = dict(render_config or {})
    if source_image is not None and overlay_image is None:
        overlay_image = _build_overlay_image(
            source_image,
            result,
            overlay_mode=str(render_state.get("overlay_mode", "history-plus-projection")),
            min_conf_global=float(render_state.get("min_conf_global", 0.42) or 0.42),
            min_conf_latest=float(render_state.get("min_conf_latest", 0.50) or 0.50),
            history_limit=int(render_state.get("history_depth", 8) or 8),
            label_budget=int(render_state.get("label_density", 10) or 10),
            projection_confidence_floor=float(render_state.get("projection_focus", 0.35) or 0.35),
        )
    raw_asset_path = _write_resized_image_asset(
        source_image,
        _compare_asset_path(f"{cache_prefix}_{label_key}", "raw"),
        max_width=1280,
        max_height=720,
        fmt="PNG",
    )
    overlay_asset_path = _write_resized_image_asset(
        overlay_image,
        _compare_asset_path(f"{cache_prefix}_{label_key}", "overlay"),
        max_width=1280,
        max_height=720,
        fmt="PNG",
    )
    frame_profile = _build_mtf_frame_profile(result)
    chart_state = cast(dict[str, Any], result.get("chart_state", {}))
    projection = cast(dict[str, Any], result.get("projection", {}))
    return {
        "label": label,
        "file_name": os.path.basename(file_path),
        "file_path": file_path,
        "action": str(result.get("action", "HOLD")).upper(),
        "confidence": float(result.get("confidence", 0.0) or 0.0),
        "projection_direction": str(projection.get("direction", "HOLD")).upper(),
        "bias_direction": str(frame_profile.get("bias_direction", "HOLD")).upper(),
        "bias_strength": float(frame_profile.get("bias_strength", 0.0) or 0.0),
        "setup": str(chart_state.get("structure_setup", "none") or "none"),
        "momentum_bias": str(chart_state.get("momentum_bias", "neutral") or "neutral"),
        "raw_asset_path": raw_asset_path,
        "overlay_asset_path": overlay_asset_path,
    }


def _build_multi_timeframe_result(bundle_results: list[dict[str, Any]]) -> dict[str, Any]:
    if not bundle_results:
        return {}
    lead = bundle_results[0]
    trigger = bundle_results[-1]
    combined = cast(dict[str, Any], copy.deepcopy(trigger["result"]))
    lead_result = cast(dict[str, Any], lead["result"])
    trigger_result = cast(dict[str, Any], trigger["result"])

    lead_profile = _build_mtf_frame_profile(lead_result)
    trigger_profile = _build_mtf_frame_profile(trigger_result)
    gate = _evaluate_multi_timeframe_gate(lead_profile, trigger_profile)

    lead_projection = str(lead_profile.get("projection_direction", "HOLD")).upper()
    trigger_projection = str(trigger_profile.get("projection_direction", "HOLD")).upper()
    trigger_direction = _directional_action(trigger_profile.get("entry_direction", trigger_result.get("action", "HOLD")))
    lead_bias_direction = _directional_action(lead_profile.get("bias_direction", lead_result.get("action", "HOLD")))
    aligned = bool(
        gate.get("state") == "confirmed"
        or (
            trigger_direction in {"BUY", "SELL"}
            and lead_bias_direction in {"BUY", "SELL"}
            and trigger_direction == lead_bias_direction
        )
        or (lead_projection in {"BUY", "SELL"} and lead_projection == trigger_projection)
    )

    probabilities = _normalize_probabilities(cast(dict[str, Any], combined.get("probabilities", {})))
    gate_state = str(gate.get("state", "watch") or "watch").lower()
    gate_strength = float(np.clip(gate.get("gate_strength", 0.0), 0.0, 1.0))
    position_size_pct = float(np.clip(combined.get("position_size_pct", 0.0) or 0.0, 0.0, 100.0))
    expected_move_pct = float(combined.get("expected_3min_move_pct", 0.0) or 0.0)
    quantile_range_raw = cast(Sequence[Any], combined.get("quantile_range", [0.0, 0.0]))
    quantile_range = (
        [float(quantile_range_raw[0] or 0.0), float(quantile_range_raw[1] or 0.0)]
        if len(quantile_range_raw) >= 2
        else [0.0, 0.0]
    )

    if gate_state == "confirmed" and trigger_direction in {"BUY", "SELL"}:
        confirm_bias = 0.04 + 0.05 * gate_strength
        probabilities = _apply_probability_bias(probabilities, trigger_direction, confirm_bias)
        move_scale = 1.0 + 0.12 * gate_strength
        expected_move_pct *= move_scale
        quantile_range = [float(value) * move_scale for value in quantile_range]
        position_size_pct = float(np.clip(position_size_pct * (1.0 + 0.16 * gate_strength), 0.0, 100.0))
    elif gate_state == "blocked":
        probabilities = _apply_probability_bias(probabilities, "HOLD", 0.10)
        hold_floor = 0.46 + 0.18 * gate_strength
        probabilities["HOLD"] = max(float(probabilities.get("HOLD", 0.0)), hold_floor)
        if trigger_direction in probabilities:
            probabilities[trigger_direction] = min(
                float(probabilities.get(trigger_direction, 0.0)),
                max(0.06, 0.26 - 0.10 * gate_strength),
            )
        probabilities = _normalize_probabilities(probabilities)
        move_scale = max(0.15, 0.50 - 0.28 * gate_strength)
        expected_move_pct *= move_scale
        quantile_range = [float(value) * move_scale for value in quantile_range]
        position_size_pct = 0.0
        combined["execution_guard_ok"] = False
        combined["support_gates_ok"] = False
        combined["consensus_ok"] = False
        combined["opposition_alert"] = True
    else:
        if trigger_direction in {"BUY", "SELL"} and lead_bias_direction == trigger_direction:
            probabilities = _apply_probability_bias(probabilities, trigger_direction, 0.02 + 0.02 * gate_strength)
        elif lead_bias_direction in {"BUY", "SELL"} and lead_bias_direction != trigger_direction:
            probabilities = _apply_probability_bias(probabilities, "HOLD", 0.03 + 0.03 * gate_strength)
            position_size_pct = float(np.clip(position_size_pct * (0.88 - 0.14 * gate_strength), 0.0, 100.0))

    final_action = max(probabilities.items(), key=lambda item: float(item[1]))[0]
    if gate_state == "blocked":
        final_action = "HOLD"
    combined["probabilities"] = probabilities
    combined["action"] = final_action
    confidence_value = probabilities.get(final_action)
    if confidence_value is None:
        confidence_value = float(combined.get("confidence", 0.0) or 0.0)
    combined["confidence"] = float(confidence_value)
    combined["position_size_pct"] = position_size_pct
    combined["expected_3min_move_pct"] = expected_move_pct
    combined["quantile_range"] = quantile_range

    base_trigger_explanation = str(trigger_result.get("explanation", "") or "").strip()
    fusion_explanation = str(gate.get("explanation", "") or "").strip()
    combined["explanation"] = (
        f"{fusion_explanation} Lower timeframe read: {base_trigger_explanation}"
        if base_trigger_explanation
        else fusion_explanation
    ).strip()

    gate_headline = str(gate.get("headline", "") or "").strip()
    summary_suffix = (
        f" | Gate {gate_state.upper()} | {gate_headline}"
        if gate_headline
        else f" | Gate {gate_state.upper()}"
    )
    combined["multi_timeframe"] = {
        "aligned": aligned,
        "lead_action": str(lead_result.get("action", "HOLD")).upper(),
        "trigger_action": str(trigger_result.get("action", "HOLD")).upper(),
        "lead_projection": lead_projection,
        "trigger_projection": trigger_projection,
        "lead_bias_direction": lead_bias_direction,
        "lead_bias_strength": float(lead_profile.get("bias_strength", 0.0) or 0.0),
        "trigger_direction": trigger_direction,
        "trigger_strength": float(trigger_profile.get("entry_confidence", 0.0) or 0.0),
        "gate_state": gate_state,
        "gate_strength": gate_strength,
        "gate_score": float(gate.get("gate_score", 0.0) or 0.0),
        "entry_allowed": bool(gate.get("entry_allowed", gate_state != "blocked")),
        "gate_explanation": fusion_explanation,
        "confirmation_reasons": list(cast(Sequence[str], gate.get("confirmation_reasons", []))),
        "blocking_reasons": list(cast(Sequence[str], gate.get("blocking_reasons", []))),
        "summary": (
            f"Higher TF {str(lead_result.get('action', 'HOLD')).upper()} / {lead_projection} | "
            f"Lower TF {str(trigger_result.get('action', 'HOLD')).upper()} / {trigger_projection}"
            f"{summary_suffix}"
        ),
        "entries": [dict(entry["compare_entry"]) for entry in bundle_results],
    }
    return combined


def _record_capture_result(
    result: dict[str, Any],
    source_image_state: Any,
    file_path: str,
    *,
    status: str,
    source: str,
    overlay: Any = None,
    gauge: Any = None,
    skill_fig: Any = None,
) -> None:
    with _capture_runtime_lock:
        next_token = int(_capture_runtime_state.get("token", 0) or 0) + 1
        _capture_runtime_state.update(
            {
                "token": next_token,
                "latest_result": result,
                "latest_source_image_state": source_image_state,
                "latest_file_path": file_path,
                "latest_overlay": overlay,
                "latest_gauge": gauge,
                "latest_skill_fig": skill_fig,
                "last_capture_time": str(result.get("timestamp", utc_now_iso())),
                "last_capture_file": os.path.basename(file_path),
                "inference_active": False,
                "selection_active": False,
                "last_error": "",
                "status": status,
            }
        )
        _bump_capture_status_token()
    _append_session_entry(_build_session_entry(result, source_image_state, file_path, source=source))


def _process_multi_timeframe_bundle(bundle: list[dict[str, Any]], source: str = "hotkey") -> bool:
    if not bundle:
        return False
    labels = ["Higher TF", "Lower TF", "Frame 3", "Frame 4"]
    analyzed: list[dict[str, Any]] = []
    try:
        for index, row in enumerate(bundle):
            file_path = str(row.get("file_path", ""))
            result, overlay, _gauge_unused, _skill_unused = run_inference(
                file_path,
                annotation_text="",
                overlay_mode="history-plus-projection",
                min_conf_global=0.42,
                min_conf_latest=0.50,
            )
            source_image_state = _source_image_to_state(file_path)
            analyzed.append(
                {
                    "result": result,
                    "file_path": file_path,
                    "source_image_state": source_image_state,
                    "compare_entry": _build_timeframe_compare_entry(
                        result,
                        source_image_state,
                        file_path,
                        labels[min(index, len(labels) - 1)],
                        overlay_image=overlay,
                        render_config={
                            "overlay_mode": "history-plus-projection",
                            "min_conf_global": 0.42,
                            "min_conf_latest": 0.50,
                            "history_depth": 8,
                            "label_density": 10,
                            "projection_focus": 0.35,
                        },
                    ),
                }
            )
        combined = _build_multi_timeframe_result(analyzed)
        final = analyzed[-1]
        status = f"{source.title()} multi-timeframe bundle ready. The desk will refresh automatically."
        _record_capture_result(
            combined,
            final["source_image_state"],
            str(final["file_path"]),
            status=status,
            source=f"{source}-multi-timeframe",
        )
        return True
    except Exception as exc:
        _update_capture_runtime_state(
            inference_active=False,
            selection_active=False,
            status="Multi-timeframe capture inference failed.",
            last_error=_truncate_text(exc, 180),
        )
        logger.exception("Multi-timeframe capture inference failed: %s", exc)
        return False


def process_capture_file(file_path: str, source: str = "inbox") -> bool:
    file_key = _capture_file_key(file_path)
    with _capture_runtime_lock:
        if file_key in _processed_capture_files:
            return False
        _processed_capture_files.add(file_key)

    _update_capture_runtime_state(
        inference_active=True,
        selection_active=False,
        status=f"Running inference from {source} capture...",
        last_error="",
    )
    try:
        result, overlay, gauge, skill_fig = run_inference(
            file_path,
            annotation_text="",
            overlay_mode="history-plus-projection",
            min_conf_global=0.42,
            min_conf_latest=0.50,
        )
        source_image_state = _source_image_to_state(file_path)
        _record_capture_result(
            result,
            source_image_state,
            file_path,
            status=f"{source.title()} capture ready. The desk will refresh automatically.",
            source=source,
            overlay=overlay,
            gauge=gauge,
            skill_fig=skill_fig,
        )
        return True
    except Exception as exc:
        with _capture_runtime_lock:
            _processed_capture_files.discard(file_key)
        _update_capture_runtime_state(
            inference_active=False,
            selection_active=False,
            status="Capture inference failed.",
            last_error=_truncate_text(exc, 180),
        )
        logger.exception("Capture inference failed for %s: %s", file_path, exc)
        return False


def _hotkey_capture_flow() -> None:
    if not _capture_selector_lock.acquire(blocking=False):
        _update_capture_runtime_state(status="Capture selector already open.", last_error="")
        return

    try:
        runtime = _get_capture_runtime_snapshot()
        active_hotkey = runtime.get("active_hotkey", "") or runtime.get("requested_hotkey", "F4")
        _update_capture_runtime_state(
            selection_active=True,
            inference_active=False,
            status=f"{active_hotkey}: drag to select the chart region, then press Enter to confirm.",
            last_error="",
        )
        bbox = _select_capture_region_with_overlay(active_hotkey)
        if bbox is None:
            _update_capture_runtime_state(
                selection_active=False,
                inference_active=False,
                status="Capture cancelled.",
                last_error="",
            )
            return
        _update_capture_runtime_state(
            selection_active=False,
            inference_active=True,
            status="Selection confirmed. Capturing region...",
            last_error="",
        )
        file_path = _save_capture_region_to_inbox(bbox)
        ready, bundle, slot_index, bundle_size = _queue_hotkey_capture(file_path)
        if not ready:
            status = f"Capture {slot_index}/{bundle_size} stored. Switch timeframe, then press {active_hotkey} again."
            _update_capture_runtime_state(
                selection_active=False,
                inference_active=False,
                status=status,
                last_error="",
            )
            _show_capture_hud(
                f"Capture {slot_index}/{bundle_size} saved",
                f"Switch to the next timeframe, then press {active_hotkey} again. Inference will start after the final confirmation.",
                timeout_ms=1900,
            )
            return
        _update_capture_runtime_state(
            selection_active=False,
            inference_active=True,
            status=f"Capture {bundle_size}/{bundle_size} stored. Running multi-timeframe inference...",
            last_error="",
        )
        _show_capture_hud(
            "Running multi-timeframe inference",
            "Both timeframe captures are locked in. The desk is stitching them together now.",
            timeout_ms=1800,
        )
        _process_multi_timeframe_bundle(bundle, source="hotkey")
    except Exception as exc:
        _update_capture_runtime_state(
            selection_active=False,
            inference_active=False,
            status="Hotkey capture failed.",
            last_error=_truncate_text(exc, 180),
        )
        logger.exception("Hotkey capture failed: %s", exc)
    finally:
        _capture_selector_lock.release()


def _hotkey_listener_loop() -> None:
    if os.name != "nt":
        _update_capture_runtime_state(
            status="Global hotkey capture is available on Windows only.",
            last_error="Unsupported platform.",
        )
        return

    user32 = ctypes.windll.user32
    hotkey_id = 0x8080
    requested = str(RUNTIME.capture_hotkey or "F4")
    fallback = str(RUNTIME.capture_hotkey_fallback or "CTRL+SHIFT+4")
    registered_label = ""
    registration_note = ""

    for candidate in [requested, fallback]:
        try:
            modifiers, vk_code, label = _parse_hotkey_spec(candidate)
        except ValueError as exc:
            registration_note = str(exc)
            continue
        if bool(user32.RegisterHotKey(None, hotkey_id, modifiers, vk_code)):
            registered_label = label
            if candidate != requested:
                registration_note = f"Requested hotkey {requested} was unavailable. Using {label}."
            break

    if not registered_label:
        _update_capture_runtime_state(
            active_hotkey="",
            status="Unable to register the global capture hotkey.",
            last_error=registration_note or "Hotkey registration failed.",
        )
        return

    _update_capture_runtime_state(
        active_hotkey=registered_label,
        status=registration_note or f"Global capture hotkey ready: {registered_label}.",
        last_error="",
    )

    msg = wintypes.MSG()
    while True:
        msg_result = int(user32.GetMessageW(ctypes.byref(msg), None, 0, 0))
        if msg_result <= 0:
            break
        if int(msg.message) == 0x0312 and int(msg.wParam) == hotkey_id:
            threading.Thread(target=_hotkey_capture_flow, daemon=True, name="pg-hotkey-capture").start()

    try:
        user32.UnregisterHotKey(None, hotkey_id)
    except Exception:
        pass


def _start_capture_hotkey_listener() -> None:
    global _hotkey_listener_started
    if _hotkey_listener_started:
        return
    _hotkey_listener_started = True
    threading.Thread(target=_hotkey_listener_loop, daemon=True, name="pg-hotkey-listener").start()


def _is_synthetic_signal(name: str) -> bool:
    normalized = name.lower().strip().replace(" ", "_")
    return normalized in {
        "buy_memory_bias",
        "sell_memory_bias",
        "chart_plot_region",
        "recent_sequence_context",
    }


def _group_debug_detections(
    detections: Sequence[Mapping[str, Any]],
    limit: int = 10,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    global_patterns: list[dict[str, Any]] = []
    latest_patterns: list[dict[str, Any]] = []
    synthetic_patterns: list[dict[str, Any]] = []
    for detection in detections:
        pattern = str(detection.get("pattern", ""))
        if _is_parser_artifact(pattern):
            continue
        row: dict[str, Any] = {
            "pattern": pattern,
            "confidence": float(detection.get("confidence", 0.0) or 0.0),
            "bbox": detection.get("bbox", []),
            "priority_score": float(detection.get("priority_score", 0.0) or 0.0),
            "overlay_confidence": float(detection.get("overlay_confidence", detection.get("confidence", 0.0)) or 0.0),
            "geometry_score": float(detection.get("geometry_score", 0.0) or 0.0),
            "context_score": float(detection.get("context_score", 0.0) or 0.0),
            "sequence_role": str(detection.get("sequence_role", "global")),
            "evidence": cast(list[str], detection.get("evidence", [])),
        }
        normalized = pattern.lower().strip().replace(" ", "_")
        if _is_synthetic_signal(normalized):
            synthetic_patterns.append(row)
        elif _is_latest_branch_pattern(normalized):
            latest_patterns.append(row)
        else:
            global_patterns.append(row)

    def _rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(rows, key=lambda item: float(item["confidence"]), reverse=True)[:limit]

    return _rank(global_patterns), _rank(latest_patterns), _rank(synthetic_patterns)


def _build_transition_summary(probabilities: Mapping[str, float]) -> TransitionSummary:
    normalized = _normalize_transition_keys(dict(probabilities))
    return TransitionSummary(
        continue_prob=float(normalized["continue"]),
        pullback_prob=float(normalized["pullback"]),
        reversal_attempt_prob=float(normalized["reversal_attempt"]),
        fakeout_prob=float(normalized["fakeout"]),
    )


def _cap_parse_quality_value(
    latest_parse_quality: float,
    latest_candle_confidence: float,
    chart_geometry: Mapping[str, Any] | None,
    sequence_state: Mapping[str, Any] | None,
) -> float:
    parse_q = float(np.clip(latest_parse_quality, 0.0, 1.0))
    latest_conf = float(np.clip(latest_candle_confidence, 0.0, 1.0))
    geometry_conf = float(np.clip(float((chart_geometry or {}).get("geometry_confidence", 0.0) or 0.0), 0.0, 1.0))
    spacing = float(np.clip(float((sequence_state or {}).get("spacing_consistency", 0.0) or 0.0), 0.0, 1.0))
    if latest_conf < 0.20:
        hard_cap = 0.45 * (0.60 + 0.40 * geometry_conf)
        return float(min(parse_q, hard_cap, 0.35 + 0.20 * spacing))
    if latest_conf < 0.35:
        soft_cap = 0.70 * (0.55 + 0.45 * geometry_conf)
        return float(min(parse_q, soft_cap))
    return parse_q


def _apply_parse_quality_cap_to_detections(
    detections: list[dict[str, Any]],
    latest_candle_confidence: float,
    chart_geometry: Mapping[str, Any] | None,
    sequence_state: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    capped_value = _cap_parse_quality_value(
        latest_parse_quality=max(
            (float(d.get("confidence", 0.0) or 0.0) for d in detections if str(d.get("pattern", "")).lower().strip().replace(" ", "_") == "latest_parse_quality"),
            default=0.0,
        ),
        latest_candle_confidence=latest_candle_confidence,
        chart_geometry=chart_geometry,
        sequence_state=sequence_state,
    )
    out: list[dict[str, Any]] = []
    for detection in detections:
        copied = dict(detection)
        pattern = str(copied.get("pattern", "")).lower().strip().replace(" ", "_")
        if pattern == "latest_parse_quality":
            copied["confidence"] = capped_value
            copied["overlay_confidence"] = float(min(float(copied.get("overlay_confidence", capped_value) or 0.0), capped_value))
        out.append(copied)
    return out


def _apply_memory_ambiguity_to_detections(
    detections: list[dict[str, Any]],
    memory_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not memory_summary:
        return detections
    ambiguity = float(np.clip(memory_summary.get("ambiguity", 0.0), 0.0, 1.0))
    consensus_ratio = float(np.clip(memory_summary.get("consensus_ratio", 0.0), 0.0, 1.0))
    label_entropy = float(np.clip(memory_summary.get("label_entropy", 0.0), 0.0, 1.0))
    mixed = bool(memory_summary.get("mixed_labels", False))
    penalty = float((1.0 - ambiguity) * consensus_ratio * (1.0 - 0.15 * label_entropy))
    if mixed:
        penalty *= 0.90
    penalty = float(np.clip(penalty, 0.05, 1.0))
    out: list[dict[str, Any]] = []
    for detection in detections:
        copied = dict(detection)
        pattern = str(copied.get("pattern", "")).lower().strip().replace(" ", "_")
        if pattern in {"buy_memory_bias", "sell_memory_bias"}:
            current_conf = float(copied.get("confidence", 0.0) or 0.0)
            adjusted_conf = float(np.clip(current_conf * penalty, 0.0, 1.0))
            copied["confidence"] = adjusted_conf
            copied["overlay_confidence"] = float(min(float(copied.get("overlay_confidence", adjusted_conf) or 0.0), adjusted_conf))
            copied["memory_ambiguity_penalty"] = penalty
        out.append(copied)
    return out


def _reconcile_memory_bias_detections(
    detections: list[dict[str, Any]],
    memory_summary: dict[str, Any] | None,
    memory_direction: str,
) -> list[dict[str, Any]]:
    if not memory_summary:
        return detections
    dominant_label = str(memory_summary.get("dominant_label", memory_direction)).upper()
    top_similarity = float(np.clip(memory_summary.get("top_similarity", 0.0), 0.0, 1.0))
    consensus_ratio = float(np.clip(memory_summary.get("consensus_ratio", 0.0), 0.0, 1.0))
    mixed = bool(memory_summary.get("mixed_labels", False))
    if dominant_label not in {"BUY", "SELL"} or mixed or top_similarity < 0.72:
        return detections

    target_pattern = "buy_memory_bias" if dominant_label == "BUY" else "sell_memory_bias"
    opposite_pattern = "sell_memory_bias" if dominant_label == "BUY" else "buy_memory_bias"
    suppress_scale = float(np.clip(1.0 - (0.55 * top_similarity + 0.25 * consensus_ratio), 0.08, 0.55))
    support_floor = float(np.clip(0.35 * top_similarity + 0.25 * consensus_ratio, 0.0, 0.92))

    reconciled: list[dict[str, Any]] = []
    for detection in detections:
        copied = dict(detection)
        pattern = str(copied.get("pattern", "")).lower().strip().replace(" ", "_")
        current_conf = float(copied.get("confidence", 0.0) or 0.0)
        if pattern == target_pattern:
            new_conf = float(max(current_conf, support_floor))
            copied["confidence"] = new_conf
            copied["overlay_confidence"] = float(max(float(copied.get("overlay_confidence", new_conf) or 0.0), new_conf))
            copied["bank_memory_alignment"] = dominant_label
        elif pattern == opposite_pattern:
            new_conf = float(np.clip(current_conf * suppress_scale, 0.0, 1.0))
            copied["confidence"] = new_conf
            copied["overlay_confidence"] = float(min(float(copied.get("overlay_confidence", new_conf) or 0.0), new_conf))
            copied["bank_memory_alignment"] = dominant_label
        reconciled.append(copied)
    return reconciled


def _should_relax_hold_veto(
    *,
    local_ensemble: Mapping[str, Any],
    memory_direction: str,
    memory_summary: Mapping[str, Any],
    fused_transition_probabilities: Mapping[str, float],
    latest_candle_confidence: float,
    latest_candle_direction: str,
    reasoning_trace: Mapping[str, Any],
    chart_state: Mapping[str, Any] | None = None,
) -> bool:
    ensemble_view = cast(dict[str, Any], local_ensemble.get("ensemble", {}))
    ensemble_dir = str(ensemble_view.get("predicted_label", "HOLD")).upper()
    ensemble_conf = float(ensemble_view.get("confidence", 0.0) or 0.0)
    continue_prob = float(np.clip(fused_transition_probabilities.get("continue", 0.25), 0.0, 1.0))
    reversal_prob = float(np.clip(fused_transition_probabilities.get("reversal_attempt", 0.25), 0.0, 1.0))
    fakeout_prob = float(np.clip(fused_transition_probabilities.get("fakeout", 0.25), 0.0, 1.0))
    dominant_label = str(memory_summary.get("dominant_label", memory_direction)).upper()
    top_similarity = float(np.clip(memory_summary.get("top_similarity", 0.0), 0.0, 1.0))
    mixed = bool(memory_summary.get("mixed_labels", False))
    market_state = cast(dict[str, Any], reasoning_trace.get("market_state", {}))
    local_phase = str(market_state.get("local_phase", ""))
    state = cast(dict[str, Any], chart_state or {})
    projected_box = cast(dict[str, Any], state.get("projected_next_box", {}))
    projected_direction = str(projected_box.get("direction", ensemble_dir)).upper()
    projected_conf = float(np.clip(projected_box.get("confidence", 0.0), 0.0, 1.0))
    structure_trade_ready = bool(state.get("structure_trade_ready", False))
    structure_setup = str(state.get("structure_setup", "none")).lower()
    path_clarity = float(np.clip(state.get("path_clarity", 0.0), 0.0, 1.0))

    latest_alignment = (
        ensemble_dir in {"BUY", "SELL"}
        and ensemble_dir == latest_candle_direction
        and ensemble_dir == dominant_label
        and ensemble_dir == str(memory_direction).upper()
    )
    structural_alignment = (
        structure_trade_ready
        and projected_conf >= 0.58
        and ensemble_dir in {"BUY", "SELL"}
        and ensemble_dir == projected_direction
        and ensemble_dir == dominant_label
        and ensemble_dir == str(memory_direction).upper()
    )
    memory_clean = (not mixed) and top_similarity >= 0.78
    continuation_setup = continue_prob >= 0.42 and reversal_prob <= 0.26 and fakeout_prob <= 0.34
    allowed_phase = local_phase in {"counter_trend_pullback", "with_trend_push", "with_trend_pause", "continuation_base"}
    return bool(
        (latest_alignment or structural_alignment)
        and memory_clean
        and continuation_setup
        and allowed_phase
        and ensemble_conf >= (
            0.54
            if structural_alignment and structure_setup == "impulse_chain" and path_clarity >= 0.68
            else (0.56 if structural_alignment else 0.60)
        )
        and (latest_candle_confidence >= 0.55 or structural_alignment)
    )


def _update_reasoning_trace_with_fused_transitions(
    reasoning_trace: dict[str, Any],
    fused_transition_probabilities: Mapping[str, float],
    memory_episode_matches: list[dict[str, Any]],
) -> dict[str, Any]:
    if not reasoning_trace:
        return reasoning_trace
    updated = dict(reasoning_trace)
    fused = _normalize_transition_keys(dict(fused_transition_probabilities))
    updated["transition_probabilities"] = fused
    updated["memory_transition_probabilities"] = fused
    if memory_episode_matches:
        updated["episode_matches"] = memory_episode_matches
    market_state = dict(cast(dict[str, Any], updated.get("market_state", {})))
    if market_state:
        best_transition = max(fused.items(), key=lambda item: float(item[1]))[0]
        market_state["intent_next"] = best_transition
        updated["market_state"] = market_state
    return updated

def _is_parser_artifact(name: str) -> bool:
    normalized = name.lower().strip().replace(' ', '_')
    return normalized in {'latest_parse_quality', 'scene_parse_quality'}


def _extract_chart_geometry_from_detections(detections: list[dict[str, Any]]) -> dict[str, Any]:
    for detection in detections:
        geometry = detection.get('chart_geometry')
        if isinstance(geometry, dict):
            return cast(dict[str, Any], geometry)
    return {}


def _normalize_transition_keys(probabilities: dict[str, Any]) -> dict[str, float]:
    raw = {
        'continue': float(probabilities.get('continue', probabilities.get('continue_prob', 0.0)) or 0.0),
        'pullback': float(probabilities.get('pullback', probabilities.get('pullback_prob', 0.0)) or 0.0),
        'reversal_attempt': float(probabilities.get('reversal_attempt', probabilities.get('reversal_attempt_prob', 0.0)) or 0.0),
        'fakeout': float(probabilities.get('fakeout', probabilities.get('fakeout_prob', 0.0)) or 0.0),
    }
    total = sum(max(value, 0.0) for value in raw.values())
    if total <= 1e-9:
        return {'continue': 0.25, 'pullback': 0.25, 'reversal_attempt': 0.25, 'fakeout': 0.25}
    return {key: float(max(value, 0.0) / total) for key, value in raw.items()}


def _fuse_transition_probabilities(
    reasoning_trace: dict[str, Any],
    sequence_transition_probabilities: dict[str, float],
    sequence_state: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    trace_probs: dict[str, float] = _normalize_transition_keys(cast(dict[str, Any], reasoning_trace.get('transition_probabilities', {})))
    seq_state = cast(dict[str, Any], sequence_state or reasoning_trace.get('sequence_state', {}))
    seq_probs: dict[str, float] = _normalize_transition_keys({
        'continue': seq_state.get('continuation_probability', 0.0),
        'pullback': seq_state.get('pullback_probability', 0.0),
        'reversal_attempt': seq_state.get('reversal_probability', 0.0),
        'fakeout': seq_state.get('fakeout_probability', 0.0),
    }) if seq_state else {'continue': 0.25, 'pullback': 0.25, 'reversal_attempt': 0.25, 'fakeout': 0.25}
    memory_probs: dict[str, float] = _normalize_transition_keys(sequence_transition_probabilities) if sequence_transition_probabilities else {'continue': 0.25, 'pullback': 0.25, 'reversal_attempt': 0.25, 'fakeout': 0.25}
    fused: dict[str, float] = {
        key: float(np.clip(0.38 * trace_probs[key] + 0.37 * seq_probs[key] + 0.25 * memory_probs[key], 0.0, 1.0))
        for key in trace_probs
    }
    current_box = cast(dict[str, Any], seq_state.get('current_box', {})) if seq_state else {}
    next_boxes = cast(list[dict[str, Any]], seq_state.get('next_box_hypotheses', [])) if seq_state else []
    primary_next = next_boxes[0] if next_boxes else {}
    current_type = str(current_box.get('box_type', 'balance')).lower()
    consolidation_score = float(np.clip(current_box.get('consolidation_score', 0.0), 0.0, 1.0))
    has_consolidation = bool(current_box.get('contains_consolidation', False) or current_type == 'balance' or consolidation_score >= 0.52)
    next_type = str(primary_next.get('box_type', '')).lower()
    if has_consolidation and next_type == 'impulse':
        fused['continue'] = float(np.clip(fused['continue'] + 0.12 + 0.10 * consolidation_score, 0.0, 1.0))
        fused['fakeout'] = float(np.clip(fused['fakeout'] - 0.05 * max(0.4, consolidation_score), 0.0, 1.0))
    elif current_type == 'reversal_base':
        fused['reversal_attempt'] = float(np.clip(fused['reversal_attempt'] + 0.10, 0.0, 1.0))
    elif current_type == 'impulse' and next_type == 'pullback':
        fused['pullback'] = float(np.clip(fused['pullback'] + 0.08, 0.0, 1.0))
    total: float = sum(fused.values())
    if total <= 1e-9:
        return {'continue': 0.25, 'pullback': 0.25, 'reversal_attempt': 0.25, 'fakeout': 0.25}
    return {key: float(value / total) for key, value in fused.items()}


def _summarize_memory_ambiguity(recall_results: list[Any]) -> dict[str, Any]:
    if not recall_results:
        return {'top_similarity': 0.0, 'ambiguity': 0.0, 'label_entropy': 0.0, 'consensus_ratio': 0.0, 'mixed_labels': False, 'dominant_label': 'HOLD', 'recall_count': 0}
    labels = [str(getattr(item, 'label', 'HOLD')).upper() for item in recall_results]
    sims = np.array([float(getattr(item, 'similarity', 0.0) or 0.0) for item in recall_results], dtype=np.float64)
    weights = np.clip(sims, 1e-6, None)
    weight_sum = float(np.sum(weights))
    unique_labels = sorted(set(labels))
    probs: list[float] = []
    for label in unique_labels:
        label_weight = float(np.sum(weights[[idx for idx, value in enumerate(labels) if value == label]]))
        probs.append(label_weight / max(weight_sum, 1e-12))
    entropy = 0.0
    for prob in probs:
        entropy -= prob * float(np.log(prob + 1e-12))
    if len(unique_labels) > 1:
        entropy /= float(np.log(len(unique_labels)))
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    dominant_label: str = max(counts.items(), key=lambda item: item[1])[0] if counts else 'HOLD'
    consensus_ratio = float(counts.get(dominant_label, 0) / max(len(labels), 1))
    ambiguity = float(np.clip(0.55 * entropy + 0.45 * (1.0 - consensus_ratio), 0.0, 1.0))
    return {
        'top_similarity': float(sims[0]) if sims.size else 0.0,
        'ambiguity': ambiguity,
        'label_entropy': float(entropy),
        'consensus_ratio': consensus_ratio,
        'mixed_labels': len(unique_labels) > 1,
        'dominant_label': dominant_label,
        'recall_count': len(recall_results),
    }


def _cap_directional_detections(detections: list[dict[str, Any]], latest_candle_confidence: float) -> list[dict[str, Any]]:
    if latest_candle_confidence >= 0.35:
        return detections
    directional_cap = float(max(0.20, latest_candle_confidence))
    structure_cap = float(max(0.25, min(0.60, 0.30 + latest_candle_confidence)))
    capped: list[dict[str, Any]] = []
    for detection in detections:
        copied = dict(detection)
        pattern = str(copied.get('pattern', '')).lower().strip().replace(' ', '_')
        current_conf = float(copied.get('confidence', 0.0) or 0.0)
        if pattern.startswith(('latest_candle_', 'next_candle_')):
            copied['confidence'] = float(min(current_conf, directional_cap))
            copied['overlay_confidence'] = float(min(float(copied.get('overlay_confidence', copied['confidence']) or 0.0), copied['confidence']))
        elif pattern.startswith(('next_move_', 'wick_dominance_')):
            copied['confidence'] = float(min(current_conf, structure_cap))
            copied['overlay_confidence'] = float(min(float(copied.get('overlay_confidence', copied['confidence']) or 0.0), copied['confidence']))
        capped.append(copied)
    return capped


def _extract_latest_signal_state(detections: Sequence[Mapping[str, Any]]) -> dict[str, float | str]:
    latest_parse_quality = 0.0
    latest_buy_conf = 0.0
    latest_sell_conf = 0.0
    for detection in detections:
        name = str(detection.get("pattern", "")).strip().lower().replace(" ", "_")
        conf = float(detection.get("confidence", 0.0) or 0.0)
        if name == "latest_parse_quality":
            latest_parse_quality = max(latest_parse_quality, conf)
        elif name == "latest_candle_buy":
            latest_buy_conf = max(latest_buy_conf, conf)
        elif name == "latest_candle_sell":
            latest_sell_conf = max(latest_sell_conf, conf)

    latest_candle_confidence = max(latest_buy_conf, latest_sell_conf)
    latest_candle_direction = "BUY" if latest_buy_conf >= latest_sell_conf and latest_buy_conf > 0.0 else (
        "SELL" if latest_sell_conf > 0.0 else "HOLD"
    )
    return {
        "latest_parse_quality": float(latest_parse_quality),
        "latest_buy_conf": float(latest_buy_conf),
        "latest_sell_conf": float(latest_sell_conf),
        "latest_candle_confidence": float(latest_candle_confidence),
        "latest_candle_direction": latest_candle_direction,
    }


def _safe_bbox_union(items: Sequence[Mapping[str, Any]], fallback: Sequence[float]) -> list[float]:
    xs1: list[float] = []
    ys1: list[float] = []
    xs2: list[float] = []
    ys2: list[float] = []
    for item in items:
        bbox = cast(list[float], item.get("bbox", []))
        if len(bbox) != 4:
            continue
        xs1.append(float(bbox[0]))
        ys1.append(float(bbox[1]))
        xs2.append(float(bbox[2]))
        ys2.append(float(bbox[3]))
    if not xs1:
        return [float(v) for v in fallback]
    return [float(min(xs1)), float(min(ys1)), float(max(xs2)), float(max(ys2))]


def _bbox_center(bbox: Sequence[float]) -> tuple[float, float]:
    if len(bbox) != 4:
        return (0.0, 0.0)
    return (0.5 * (float(bbox[0]) + float(bbox[2])), 0.5 * (float(bbox[1]) + float(bbox[3])))


def _compress_color_runs(colors: Sequence[str]) -> str:
    if not colors:
        return ""
    runs: list[str] = []
    current = colors[0]
    count = 1
    for color in colors[1:]:
        if color == current:
            count += 1
            continue
        runs.append(f"{'G' if current == 'green' else 'R'}{count}")
        current = color
        count = 1
    runs.append(f"{'G' if current == 'green' else 'R'}{count}")
    return "-".join(runs[:8])


def _candle_sequence_tokens(chunk: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for idx, item in enumerate(chunk, start=1):
        color = "green" if float(item.get("candle_color_green", 0.0) or 0.0) >= 0.5 else "red"
        upper = float(item.get("upper_wick_pct", 0.0) or 0.0)
        lower = float(item.get("lower_wick_pct", 0.0) or 0.0)
        if abs(upper - lower) <= 0.04:
            wick_bias = "balanced"
        else:
            wick_bias = "upper" if upper > lower else "lower"
        body_pct = float(item.get("body_height_pct", 0.0) or 0.0)
        if body_pct <= 0.18:
            body_class = "small"
        elif body_pct >= 0.34:
            body_class = "expansion"
        else:
            body_class = "medium"
        tokens.append(
            {
                "index": idx,
                "color": color,
                "body_pct": body_pct,
                "body_class": body_class,
                "wick_bias": wick_bias,
                "bbox": [float(v) for v in cast(list[float], item.get("bbox", [0.0, 0.0, 0.0, 0.0]))],
            }
        )
    return tokens


def _classify_box_type(chunk: Sequence[Mapping[str, Any]]) -> str:
    if not chunk:
        return "balance"
    body_pcts = [float(item.get("body_height_pct", 0.0) or 0.0) for item in chunk]
    upper_wicks = [float(item.get("upper_wick_pct", 0.0) or 0.0) for item in chunk]
    lower_wicks = [float(item.get("lower_wick_pct", 0.0) or 0.0) for item in chunk]
    green_votes = sum(1 for item in chunk if float(item.get("candle_color_green", 0.0) or 0.0) >= 0.5)
    red_votes = max(0, len(chunk) - green_votes)
    mean_body = float(np.mean(np.array(body_pcts, dtype=np.float32))) if body_pcts else 0.0
    max_wick = max(max(upper_wicks or [0.0]), max(lower_wicks or [0.0]))
    mixed = green_votes > 0 and red_votes > 0
    if mean_body <= 0.20:
        return "balance"
    if max_wick >= 0.62 and len(chunk) <= 3:
        return "reversal_base"
    if mixed and mean_body <= 0.28:
        return "balance"
    if len(chunk) >= 3 and mean_body >= 0.26:
        return "impulse"
    if len(chunk) >= 2:
        return "pullback"
    return "balance"


def _build_box_history(
    candles: Sequence[dict[str, Any]],
    image_width: float,
    image_height: float,
) -> list[dict[str, Any]]:
    if not candles:
        return []
    ordered = sorted(
        [dict(c) for c in candles],
        key=lambda item: float(cast(list[float], item.get("bbox", [0.0, 0.0, 0.0, 0.0]))[0]) if len(cast(list[float], item.get("bbox", []))) == 4 else 0.0,
    )
    segments: list[tuple[int, int]] = []
    start = 0
    prev_dir = "BUY" if float(ordered[0].get("candle_color_green", 0.0) or 0.0) >= 0.5 else "SELL"
    prev_small = float(ordered[0].get("body_height_pct", 0.0) or 0.0) <= 0.22
    for idx in range(1, len(ordered)):
        item = ordered[idx]
        cur_dir = "BUY" if float(item.get("candle_color_green", 0.0) or 0.0) >= 0.5 else "SELL"
        cur_small = float(item.get("body_height_pct", 0.0) or 0.0) <= 0.22
        run_len = idx - start
        boundary = False
        if cur_dir != prev_dir and run_len >= 2:
            boundary = True
        elif cur_small != prev_small and run_len >= 4:
            boundary = True
        elif run_len >= 5:
            boundary = True
        if boundary:
            segments.append((start, idx))
            start = idx
        prev_dir = cur_dir
        prev_small = cur_small
    segments.append((start, len(ordered)))

    merged: list[tuple[int, int]] = []
    for seg in segments:
        if merged and (seg[1] - seg[0]) == 1:
            last_start, _last_end = merged[-1]
            merged[-1] = (last_start, seg[1])
        else:
            merged.append(seg)

    boxes: list[dict[str, Any]] = []
    for seq_idx, (seg_start, seg_end) in enumerate(merged, start=1):
        chunk: list[dict[str, Any]] = list(ordered[seg_start:seg_end])
        bbox = _safe_bbox_union(chunk, [0.0, 0.0, image_width, image_height])
        buy_count = sum(1 for item in chunk if float(item.get("candle_color_green", 0.0) or 0.0) >= 0.5)
        sell_count = max(0, len(chunk) - buy_count)
        direction = "BUY" if buy_count >= sell_count else "SELL"
        body_pcts = [float(item.get("body_height_pct", 0.0) or 0.0) for item in chunk]
        upper_wicks = [float(item.get("upper_wick_pct", 0.0) or 0.0) for item in chunk]
        lower_wicks = [float(item.get("lower_wick_pct", 0.0) or 0.0) for item in chunk]
        parse_vals = [float(item.get("parse_conf", 0.0) or 0.0) for item in chunk]
        box_type = _classify_box_type(chunk)
        width_px = max(float(bbox[2]) - float(bbox[0]), 1.0)
        height_px = max(float(bbox[3]) - float(bbox[1]), 1.0)
        colors = ["green" if float(item.get("candle_color_green", 0.0) or 0.0) >= 0.5 else "red" for item in chunk]
        color_flips = sum(1 for left, right in zip(colors[:-1], colors[1:]) if left != right)
        color_flip_rate = float(color_flips / max(len(colors) - 1, 1)) if len(colors) >= 2 else 0.0
        small_body_ratio = float(sum(1 for value in body_pcts if value <= 0.24) / max(len(body_pcts), 1)) if body_pcts else 0.0
        body_std = float(np.std(np.array(body_pcts, dtype=np.float32))) if len(body_pcts) >= 2 else 0.0
        range_tightness = float(np.clip(1.0 - (height_px / max(image_height * 0.24, 1.0)), 0.0, 1.0))
        wick_balance = float(np.clip(1.0 - abs(float(np.mean(np.array(upper_wicks, dtype=np.float32) if upper_wicks else np.array([0.0], dtype=np.float32))) - float(np.mean(np.array(lower_wicks, dtype=np.float32) if lower_wicks else np.array([0.0], dtype=np.float32)))), 0.0, 1.0))
        dominant_ratio = float(max(buy_count, sell_count) / max(len(chunk), 1))
        consolidation_score = float(
            np.clip(
                0.34 * small_body_ratio
                + 0.24 * color_flip_rate
                + 0.18 * range_tightness
                + 0.12 * wick_balance
                + 0.12 * float(np.clip(1.0 - min(body_std / 0.18, 1.0), 0.0, 1.0))
                + (0.08 if box_type == "balance" else 0.0),
                0.0,
                1.0,
            )
        )
        contains_consolidation = bool(box_type == "balance" or consolidation_score >= 0.52)
        dominant_wick = "balanced"
        if upper_wicks or lower_wicks:
            mean_upper = float(np.mean(np.array(upper_wicks, dtype=np.float32) if upper_wicks else np.array([0.0], dtype=np.float32)))
            mean_lower = float(np.mean(np.array(lower_wicks, dtype=np.float32) if lower_wicks else np.array([0.0], dtype=np.float32)))
            if abs(mean_upper - mean_lower) > 0.04:
                dominant_wick = "upper" if mean_upper > mean_lower else "lower"
        confidence = float(np.clip(0.45 + 0.25 * np.mean(np.array(parse_vals, dtype=np.float32) if parse_vals else np.array([0.0], dtype=np.float32)) + 0.15 * min(len(chunk), 4) / 4.0, 0.15, 0.98))
        boxes.append({
            "sequence_index": seq_idx,
            "box_type": box_type,
            "direction": direction,
            "shape": "rectangular" if box_type in {"balance", "pullback"} else "expanding",
            "confidence": confidence,
            "bbox": [float(v) for v in bbox],
            "candle_count": int(len(chunk)),
            "start_idx": int(seg_start),
            "end_idx": int(max(seg_end - 1, seg_start)),
            "height_pct": float(np.clip(height_px / max(image_height, 1.0), 0.0, 1.0)),
            "width_pct": float(np.clip(width_px / max(image_width, 1.0), 0.0, 1.0)),
            "price_span": float(height_px),
            "mean_body_pct": float(np.mean(np.array(body_pcts, dtype=np.float32))) if body_pcts else 0.0,
            "maturity": float(np.clip(len(chunk) / 5.0, 0.0, 1.0)),
            "color": [0, 220, 120] if direction == "BUY" else [255, 165, 0],
            "dominant_ratio": dominant_ratio,
            "color_flip_rate": color_flip_rate,
            "small_body_ratio": small_body_ratio,
            "body_std_pct": body_std,
            "consolidation_score": consolidation_score,
            "contains_consolidation": contains_consolidation,
            "dominant_wick": dominant_wick,
            "sequence_signature": _compress_color_runs(colors),
            "internal_sequence": _candle_sequence_tokens(chunk),
            "center": [float(v) for v in _bbox_center(bbox)],
        })
    return boxes


def _direction_sign(direction: str) -> float:
    normalized = str(direction).upper()
    if normalized == "BUY":
        return 1.0
    if normalized == "SELL":
        return -1.0
    return 0.0


def _sign_to_direction(score: float, *, default: str = "BUY") -> str:
    if score > 0.08:
        return "BUY"
    if score < -0.08:
        return "SELL"
    normalized_default = str(default).upper()
    return normalized_default if normalized_default in {"BUY", "SELL"} else "BUY"


def _opposite_direction(direction: str) -> str:
    return "SELL" if str(direction).upper() == "BUY" else "BUY"


def _weighted_box_bias(boxes: Sequence[Mapping[str, Any]]) -> float:
    total_weight = 0.0
    weighted_score = 0.0
    for idx, box in enumerate(boxes):
        direction = str(box.get("direction", "HOLD")).upper()
        sign = _direction_sign(direction)
        if sign == 0.0:
            continue
        confidence = float(np.clip(box.get("confidence", 0.0), 0.0, 1.0))
        maturity = float(np.clip(box.get("maturity", 0.0), 0.0, 1.0))
        price_span = float(max(0.0, box.get("price_span", 0.0) or 0.0))
        normalized_span = float(np.clip(price_span / 180.0, 0.0, 1.0))
        recency = 0.70 + 0.12 * float(idx + 1)
        weight = recency * (0.40 + 0.60 * confidence) * (0.45 + 0.55 * maturity) * (0.55 + 0.45 * normalized_span)
        total_weight += weight
        weighted_score += sign * weight
    if total_weight <= 1e-9:
        return 0.0
    return float(np.clip(weighted_score / total_weight, -1.0, 1.0))


def _classify_swing_state(
    box_history: Sequence[dict[str, Any]],
    current_box: Mapping[str, Any],
    market_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    recent_boxes = [dict(box) for box in box_history[-6:]]
    if not recent_boxes and current_box:
        recent_boxes = [dict(current_box)]
    current_direction = str(current_box.get("direction", "BUY")).upper() if current_box else "BUY"
    current_type = str(current_box.get("box_type", "balance")).lower() if current_box else "balance"
    macro_trend = str((market_state or {}).get("macro_trend", "BULL")).upper()
    macro_direction = "SELL" if macro_trend == "BEAR" else "BUY"

    macro_bias_score = _weighted_box_bias(recent_boxes)
    recent_bias_score = _weighted_box_bias(recent_boxes[-3:])
    recent_swing_direction = _sign_to_direction(recent_bias_score, default=current_direction)
    macro_swing_direction = _sign_to_direction(macro_bias_score, default=macro_direction)
    recent_strength = float(np.clip(abs(recent_bias_score), 0.0, 1.0))
    macro_strength = float(np.clip(abs(macro_bias_score), 0.0, 1.0))
    turn_detected = (
        len(recent_boxes) >= 2
        and str(recent_boxes[-2].get("direction", current_direction)).upper() != current_direction
    )

    if recent_strength < 0.14 and macro_strength < 0.14:
        swing_phase = "compression"
    elif current_type == "reversal_base" and (turn_detected or current_direction != recent_swing_direction):
        swing_phase = "counter_macro_reversal"
    elif recent_swing_direction == macro_swing_direction:
        swing_phase = "with_macro_push"
    elif recent_strength <= macro_strength + 0.08:
        swing_phase = "macro_pullback"
    else:
        swing_phase = "counter_macro_reversal"

    projected_role = "continuation"
    if current_type == "reversal_base":
        projected_role = "reversal_release"
    elif current_type == "pullback":
        projected_role = "pullback_release"
    elif current_type == "balance":
        projected_role = "breakout_watch"

    return {
        "macro_trend": macro_trend,
        "macro_direction": macro_direction,
        "macro_swing_direction": macro_swing_direction,
        "macro_bias_score": macro_bias_score,
        "recent_swing_direction": recent_swing_direction,
        "recent_bias_score": recent_bias_score,
        "recent_swing_strength": recent_strength,
        "macro_swing_strength": macro_strength,
        "turn_detected": bool(turn_detected),
        "swing_phase": swing_phase,
        "current_direction": current_direction,
        "projected_role": projected_role,
        "macro_alignment": float(1.0 if current_direction == macro_direction else 0.0),
        "summary": f"{swing_phase}:{recent_swing_direction}->{macro_swing_direction}",
    }


def _build_next_box_hypotheses(
    box_history: Sequence[dict[str, Any]],
    sequence_state: Mapping[str, Any],
    chart_geometry: Mapping[str, Any],
    *,
    market_state: Mapping[str, Any] | None = None,
    memory_summary: Mapping[str, Any] | None = None,
    memory_episode_matches: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not box_history:
        return []
    current = box_history[-1]
    bbox = cast(list[float], current.get("bbox", [0.0, 0.0, 0.0, 0.0]))
    img_w = float(chart_geometry.get("plot_bbox", [0.0, 0.0, 1.0, 1.0])[2] or 1.0)
    img_h = float(chart_geometry.get("plot_bbox", [0.0, 0.0, 1.0, 1.0])[3] or 1.0)
    recent_boxes = [dict(box) for box in box_history[-4:]]
    recent_widths = [max(1.0, float(cast(list[float], box.get("bbox", bbox))[2]) - float(cast(list[float], box.get("bbox", bbox))[0])) for box in recent_boxes]
    recent_heights = [max(1.0, float(cast(list[float], box.get("bbox", bbox))[3]) - float(cast(list[float], box.get("bbox", bbox))[1])) for box in recent_boxes]
    width = max(float(np.mean(np.array(recent_widths, dtype=np.float32))) if recent_widths else float(bbox[2]) - float(bbox[0]), max(img_w * 0.08, 20.0))
    height = max(float(np.mean(np.array(recent_heights, dtype=np.float32))) if recent_heights else float(bbox[3]) - float(bbox[1]), max(img_h * 0.08, 20.0))
    x1 = min(max(float(bbox[2]) + 6.0, 0.0), max(0.0, img_w - width - 2.0))
    x2 = min(img_w - 2.0, x1 + width)
    base_y1 = float(bbox[1])
    direction = str(current.get("direction", "BUY")).upper()
    current_type = str(current.get("box_type", "balance"))
    current_consolidation = float(np.clip(current.get("consolidation_score", 0.0), 0.0, 1.0))
    recent_consolidation = float(
        np.mean(
            np.array(
                [float(np.clip(box.get("consolidation_score", 0.0), 0.0, 1.0)) for box in recent_boxes],
                dtype=np.float32,
            )
        )
    ) if recent_boxes else current_consolidation
    continue_prob = float(np.clip(sequence_state.get("continuation_probability", 0.25), 0.0, 1.0))
    pullback_prob = float(np.clip(sequence_state.get("pullback_probability", 0.25), 0.0, 1.0))
    reversal_prob = float(np.clip(sequence_state.get("reversal_probability", 0.25), 0.0, 1.0))
    fakeout_prob = float(np.clip(sequence_state.get("fakeout_probability", 0.25), 0.0, 1.0))
    has_active_consolidation = bool(
        bool(current.get("contains_consolidation", False))
        or current_type == "balance"
        or recent_consolidation >= 0.52
    )
    direction_votes = [str(box.get("direction", direction)).upper() for box in recent_boxes]
    buy_votes = sum(1 for item in direction_votes if item == "BUY")
    sell_votes = sum(1 for item in direction_votes if item == "SELL")
    dominant_direction = "BUY" if buy_votes >= sell_votes else "SELL"
    direction_persistence = float(max(buy_votes, sell_votes) / max(len(direction_votes), 1))
    swing_state = _classify_swing_state(box_history, current, market_state=market_state)
    reversal_context = bool(
        current_type == "reversal_base"
        or (
            bool(swing_state.get("turn_detected", False))
            and str(swing_state.get("recent_swing_direction", direction)) != direction
        )
    )
    path_clarity = float(
        np.clip(
            0.30 * direction_persistence
            + 0.24 * (1.0 - fakeout_prob)
            + 0.18 * max(current_consolidation, recent_consolidation)
            + 0.16 * float(reversal_context)
            + 0.12 * float(np.clip(abs(float(swing_state.get("recent_bias_score", 0.0))), 0.0, 1.0)),
            0.0,
            1.0,
        )
    )

    memory_view = cast(dict[str, Any], memory_summary or {})
    memory_direction = str(memory_view.get("dominant_label", "HOLD")).upper()
    memory_consensus = float(np.clip(memory_view.get("consensus_ratio", 0.0), 0.0, 1.0))
    memory_ambiguity = float(np.clip(memory_view.get("ambiguity", 0.0), 0.0, 1.0))
    memory_similarity = float(np.clip(memory_view.get("top_similarity", 0.0), 0.0, 1.0))
    memory_support = float(
        np.clip(memory_similarity * memory_consensus * (1.0 - 0.60 * memory_ambiguity), 0.0, 1.0)
    )
    episode_support = 0.0
    if memory_episode_matches:
        match_scores = [
            float(np.clip(match.get("similarity", 0.0), 0.0, 1.0))
            for match in memory_episode_matches
            if str(match.get("label", "")).upper() in {"BUY", "SELL"}
        ]
        if match_scores:
            episode_support = float(np.clip(float(np.mean(np.array(match_scores, dtype=np.float32))), 0.0, 1.0))
            memory_support = max(memory_support, 0.85 * episode_support)

    if has_active_consolidation:
        primary_type = "impulse"
        primary_dir = direction if direction in {"BUY", "SELL"} else dominant_direction
        primary_trigger = "consolidation_breakout"
    elif current_type == "pullback":
        primary_type = "impulse"
        primary_dir = direction
        primary_trigger = "pullback_release"
    elif current_type == "impulse":
        if reversal_prob >= max(pullback_prob + 0.04, continue_prob + 0.02):
            primary_type = "reversal_base"
            primary_dir = _opposite_direction(direction)
            primary_trigger = "impulse_exhaustion"
        else:
            primary_type = "impulse" if continue_prob >= pullback_prob else "pullback"
            primary_dir = direction
            primary_trigger = "impulse_chain" if primary_type == "impulse" else "pause_reset"
    elif current_type == "reversal_base":
        primary_type = "impulse"
        primary_dir = direction
        primary_trigger = "reversal_release"
    else:
        primary_type = "impulse" if continue_prob >= max(reversal_prob, fakeout_prob) else "balance"
        primary_dir = direction if direction in {"BUY", "SELL"} else dominant_direction
        primary_trigger = "continuation_projection" if primary_type == "impulse" else "pause_reset"

    if current_type == "impulse" and reversal_prob >= max(pullback_prob, 0.26):
        secondary_type = "reversal_base"
        secondary_dir = _opposite_direction(direction)
        secondary_trigger = "counter_swing_reversal"
    elif current_type == "reversal_base":
        secondary_type = "balance"
        secondary_dir = primary_dir
        secondary_trigger = "reversal_pause"
    elif has_active_consolidation:
        secondary_type = "balance"
        secondary_dir = primary_dir
        secondary_trigger = "range_extend"
    elif current_type == "pullback" and reversal_prob >= max(fakeout_prob, 0.22):
        secondary_type = "reversal_base"
        secondary_dir = _opposite_direction(direction)
        secondary_trigger = "pullback_fail"
    else:
        secondary_type = "balance"
        secondary_dir = primary_dir if secondary_type == "balance" else dominant_direction
        secondary_trigger = "pause_reset"

    tertiary_type = "fakeout"
    tertiary_dir = _opposite_direction(primary_dir)
    tertiary_trigger = "counter_fakeout"

    def _project_bbox(target_dir: str, *, shift_scale: float, height_scale: float) -> list[float]:
        target_height = max(18.0, height * height_scale)
        shift = y_shift * shift_scale
        signed_shift = -shift if target_dir == "BUY" else shift
        top = float(np.clip(base_y1 + signed_shift, 0.0, max(0.0, img_h - target_height - 2.0)))
        bottom = float(np.clip(top + target_height, top + 6.0, img_h - 2.0))
        return [x1, top, x2, bottom]

    y_shift = height * (0.12 + 0.12 * max(continue_prob, reversal_prob))
    primary_bbox = _project_bbox(primary_dir, shift_scale=0.72, height_scale=0.96 if primary_type == "impulse" else 0.90)
    secondary_bbox = _project_bbox(
        secondary_dir,
        shift_scale=0.18 if secondary_type == "balance" else 0.48,
        height_scale=0.84 if secondary_type == "balance" else 0.92,
    )
    tertiary_bbox = _project_bbox(tertiary_dir, shift_scale=0.46, height_scale=0.96)
    history_points = [[float(v) for v in _bbox_center(cast(list[float], box.get("bbox", bbox)))] for box in recent_boxes]

    def _score_candidate(candidate_dir: str, candidate_type: str) -> dict[str, float]:
        candidate_sign = _direction_sign(candidate_dir)
        recent_alignment = float(np.clip(0.5 + 0.5 * candidate_sign * float(swing_state.get("recent_bias_score", 0.0)), 0.0, 1.0))
        if candidate_type == "impulse":
            if current_type == "reversal_base" and candidate_dir == direction:
                transition_fit = 0.42 + 0.38 * continue_prob + 0.20 * reversal_prob
            elif candidate_dir == direction:
                transition_fit = 0.26 + 0.60 * continue_prob + 0.14 * max(current_consolidation, recent_consolidation)
            else:
                transition_fit = 0.18 + 0.52 * reversal_prob + 0.18 * fakeout_prob
        elif candidate_type == "pullback":
            transition_fit = 0.22 + 0.62 * pullback_prob + 0.16 * max(current_consolidation, recent_consolidation)
        elif candidate_type == "reversal_base":
            transition_fit = 0.24 + 0.62 * reversal_prob + 0.14 * (1.0 - direction_persistence)
        elif candidate_type == "balance":
            transition_fit = 0.26 + 0.34 * pullback_prob + 0.40 * max(current_consolidation, recent_consolidation)
        else:
            transition_fit = 0.18 + 0.70 * fakeout_prob + 0.12 * (1.0 - direction_persistence)
        transition_fit = float(np.clip(transition_fit, 0.0, 1.0))

        if current_type == "reversal_base":
            if candidate_dir == direction:
                sequence_fit = (
                    0.48
                    + 0.22 * float(direction != str(swing_state.get("recent_swing_direction", direction)))
                    + 0.16 * float(bool(swing_state.get("turn_detected", False)))
                    + 0.14 * direction_persistence
                )
            else:
                sequence_fit = 0.18 + 0.20 * (1.0 - direction_persistence) + 0.18 * (1.0 - recent_alignment)
        elif candidate_type == "balance":
            sequence_fit = 0.28 + 0.34 * max(current_consolidation, recent_consolidation) + 0.20 * (1.0 - direction_persistence) + 0.18 * (1.0 - fakeout_prob)
        elif candidate_type == "reversal_base":
            sequence_fit = 0.24 + 0.32 * float(candidate_dir != str(swing_state.get("recent_swing_direction", direction))) + 0.24 * (1.0 - direction_persistence) + 0.20 * float(bool(swing_state.get("turn_detected", False)))
        else:
            sequence_fit = 0.30 + 0.34 * float(candidate_dir == direction) + 0.20 * recent_alignment + 0.16 * direction_persistence
        sequence_fit = float(np.clip(sequence_fit, 0.0, 1.0))

        swing_phase = str(swing_state.get("swing_phase", "compression"))
        macro_swing_direction = str(swing_state.get("macro_swing_direction", direction)).upper()
        macro_direction = str(swing_state.get("macro_direction", direction)).upper()
        if swing_phase == "with_macro_push":
            swing_fit = 0.30 + 0.70 * float(candidate_dir == macro_swing_direction)
        elif swing_phase == "macro_pullback":
            if current_type == "reversal_base" and candidate_dir == direction:
                swing_fit = 0.66
            else:
                swing_fit = 0.32 + 0.40 * float(candidate_dir == macro_swing_direction) + 0.28 * float(candidate_dir == direction)
        elif swing_phase == "counter_macro_reversal":
            swing_fit = 0.34 + 0.42 * float(candidate_dir == direction) + 0.24 * float(candidate_dir != macro_direction)
        else:
            swing_fit = 0.52 if candidate_type == "balance" else 0.44
        swing_fit = float(np.clip(swing_fit, 0.0, 1.0))

        if memory_direction in {"BUY", "SELL"} and memory_support > 0.0:
            if candidate_dir == memory_direction:
                memory_fit = 0.36 + 0.64 * memory_support
            else:
                memory_fit = 0.24 + 0.36 * (1.0 - memory_support)
        else:
            memory_fit = 0.50
        memory_fit = float(np.clip(memory_fit, 0.0, 1.0))

        macro_fit = 0.58 if candidate_dir == macro_direction else 0.42
        if current_type == "reversal_base" and candidate_dir == direction and candidate_dir != macro_direction:
            macro_fit = 0.50
        macro_fit = float(np.clip(macro_fit, 0.0, 1.0))

        score = float(
            np.clip(
                (
                    0.28 * transition_fit
                    + 0.24 * sequence_fit
                    + 0.18 * swing_fit
                    + 0.16 * memory_fit
                    + 0.08 * macro_fit
                    + 0.06 * path_clarity
                ) * (0.78 + 0.22 * path_clarity),
                0.0,
                1.0,
            )
        )
        return {
            "transition": transition_fit,
            "sequence": sequence_fit,
            "swing": swing_fit,
            "memory": memory_fit,
            "macro": macro_fit,
            "score": score,
        }

    def _candidate_row(
        *,
        candidate_type: str,
        candidate_dir: str,
        trigger: str,
        candidate_bbox: Sequence[float],
    ) -> dict[str, Any]:
        score_breakdown = _score_candidate(candidate_dir, candidate_type)
        explanation = (
            f"{candidate_type}:{candidate_dir} via {trigger}; "
            f"seq={score_breakdown['sequence']:.2f} trans={score_breakdown['transition']:.2f} "
            f"swing={str(swing_state.get('swing_phase', 'compression'))}"
        )
        if memory_direction in {"BUY", "SELL"}:
            explanation += f" memory={memory_direction}:{memory_support:.2f}"
        return {
            "rank": 0,
            "box_type": candidate_type,
            "direction": candidate_dir,
            "shape": "projected",
            "confidence": float(np.clip(0.18 + 0.78 * score_breakdown["score"], 0.18, 0.96)),
            "bbox": [float(v) for v in candidate_bbox],
            "empty_projection": True,
            "trigger": trigger,
            "path_points": history_points + [[float(v) for v in _bbox_center(candidate_bbox)]],
            "path_clarity": path_clarity,
            "score_breakdown": score_breakdown,
            "swing_state": swing_state,
            "memory_direction": memory_direction,
            "memory_support": memory_support,
            "explanation": explanation,
        }

    hypotheses = [
        _candidate_row(
            candidate_type=primary_type,
            candidate_dir=primary_dir,
            trigger=primary_trigger,
            candidate_bbox=primary_bbox,
        ),
        _candidate_row(
            candidate_type=secondary_type,
            candidate_dir=secondary_dir,
            trigger=secondary_trigger,
            candidate_bbox=secondary_bbox,
        ),
        _candidate_row(
            candidate_type=tertiary_type,
            candidate_dir=tertiary_dir,
            trigger=tertiary_trigger,
            candidate_bbox=tertiary_bbox,
        ),
    ]
    hypotheses = sorted(hypotheses, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
    for idx, hypothesis in enumerate(hypotheses):
        next_conf = float(hypotheses[idx + 1].get("confidence", 0.0)) if idx + 1 < len(hypotheses) else 0.0
        hypothesis["rank"] = idx + 1
        hypothesis["dominance_gap"] = float(np.clip(float(hypothesis.get("confidence", 0.0)) - next_conf, 0.0, 1.0))
        hypothesis["directional_bias"] = float(np.clip(0.70 * float(hypothesis.get("confidence", 0.0)) + 0.30 * float(hypothesis.get("dominance_gap", 0.0)), 0.0, 1.0))
    return hypotheses


def draw_overlay(
    image: Image.Image,
    detections: list[dict[str, Any]],
    sr_levels: list[dict[str, Any]],
    user_zones: list[dict[str, Any]] | None = None,
    overlay_mode: str = 'debug-all',
    min_conf_global: float = 0.42,
    min_conf_latest: float = 0.50,
    chart_structure: Mapping[str, Any] | None = None,
    history_limit: int | None = None,
    label_budget: int = 14,
    projection_confidence_floor: float = 0.0,
) -> Image.Image:
    source_mode = image.mode
    out = image.convert("RGBA")
    draw = ImageDraw.Draw(out, "RGBA")
    filtered = _filter_detections_for_overlay(
        detections=detections,
        overlay_mode=overlay_mode,
        min_conf_global=min_conf_global,
        min_conf_latest=min_conf_latest,
    )
    geometry = _extract_chart_geometry_from_detections(filtered or detections)
    if chart_structure:
        geometry = {**geometry, **cast(dict[str, Any], chart_structure.get('chart_geometry', {}))}
    plot_bbox = cast(list[float], geometry.get('plot_bbox', [0.0, 0.0, float(out.width), float(out.height)]))
    inner_bbox = cast(list[float], geometry.get('plot_inner_bbox', plot_bbox))
    latest_seq_bbox = cast(list[float], geometry.get('latest_sequence_bbox', inner_bbox))
    draw.rectangle([(plot_bbox[0], plot_bbox[1]), (plot_bbox[2], plot_bbox[3])], outline=(80, 180, 255), width=2)
    draw.rectangle([(inner_bbox[0], inner_bbox[1]), (inner_bbox[2], inner_bbox[3])], outline=(0, 180, 120), width=1)
    draw.rectangle([(latest_seq_bbox[0], latest_seq_bbox[1]), (latest_seq_bbox[2], latest_seq_bbox[3])], outline=(255, 180, 0), width=1)

    mode = overlay_mode.strip().lower()
    box_history = cast(list[dict[str, Any]], (chart_structure or {}).get('box_history', []))
    current_box = cast(dict[str, Any], (chart_structure or {}).get('current_box', {}))
    next_boxes = cast(list[dict[str, Any]], (chart_structure or {}).get('next_box_hypotheses', []))
    fallback_projected_candles = cast(list[dict[str, Any]], (chart_structure or {}).get("projected_candle_candidates", []))

    def _box_outline(box: Mapping[str, Any], dashed: bool = False, current: bool = False) -> None:
        bbox = cast(list[float], box.get('bbox', []))
        if len(bbox) != 4:
            return
        direction = str(box.get('direction', 'BUY')).upper()
        box_type = str(box.get('box_type', 'balance')).lower()
        if box_type == 'balance':
            color = (255, 170, 0)
        elif box_type == 'reversal_base':
            color = (255, 90, 90)
        elif direction == 'BUY':
            color = (0, 255, 0)
        else:
            color = (255, 120, 0)
        x1, y1, x2, y2 = [float(v) for v in bbox]
        width = 3 if current else 2
        if dashed:
            step = 8.0
            x = x1
            while x < x2:
                x_end = min(x + step * 0.55, x2)
                draw.line([(x, y1), (x_end, y1)], fill=color, width=width)
                draw.line([(x, y2), (x_end, y2)], fill=color, width=width)
                x += step
            y = y1
            while y < y2:
                y_end = min(y + step * 0.55, y2)
                draw.line([(x1, y), (x1, y_end)], fill=color, width=width)
                draw.line([(x2, y), (x2, y_end)], fill=color, width=width)
                y += step
        else:
            draw.rectangle([(x1, y1), (x2, y2)], outline=color, width=width)
        seq_idx = int(box.get('sequence_index', 0) or 0)
        signature = str(box.get('sequence_signature', '')).strip()
        prefix = f"#{seq_idx} " if seq_idx > 0 else ""
        label = f"{prefix}{box_type} {direction[:1]} {float(box.get('confidence', 0.0) or 0.0):.2f}"
        if signature:
            label += f" {signature[:12]}"
        draw.text((x1 + 2.0, max(4.0, y1 - 14.0)), label, fill=color)

    def _draw_projected_candle(candle: Mapping[str, Any], *, highlight: bool = False) -> None:
        body_bbox = cast(list[float], candle.get("body_bbox", []))
        if len(body_bbox) != 4:
            return
        direction = str(candle.get("direction", "BUY")).upper()
        confidence = float(np.clip(candle.get("confidence", 0.0), 0.0, 1.0))
        center_x = float(candle.get("center_x", 0.5 * (body_bbox[0] + body_bbox[2])))
        wick_top = float(candle.get("wick_top", body_bbox[1]))
        wick_bottom = float(candle.get("wick_bottom", body_bbox[3]))
        alpha = int(np.clip(88 + 112 * confidence + (16 if highlight else 0), 72, 232))
        if direction == "BUY":
            body_fill = (72, 236, 142, alpha)
            outline = (198, 255, 226, min(255, alpha + 16))
        else:
            body_fill = (255, 142, 88, alpha)
            outline = (255, 228, 208, min(255, alpha + 16))
        draw.line([(center_x, wick_top), (center_x, wick_bottom)], fill=outline, width=2 if highlight else 1)
        draw.rectangle(
            [(float(body_bbox[0]), float(body_bbox[1])), (float(body_bbox[2]), float(body_bbox[3]))],
            fill=body_fill,
            outline=outline,
            width=2 if highlight else 1,
        )
        if highlight:
            pattern_family = str(candle.get("pattern_family", "")).strip()
            if pattern_family:
                draw.text(
                    (float(body_bbox[0]), max(6.0, float(body_bbox[1]) - 14.0)),
                    f"{pattern_family} {confidence:.2f}",
                    fill=outline,
                )

    active_history = box_history[-history_limit:] if history_limit and history_limit > 0 else box_history
    visible_next_boxes = [
        hypothesis
        for hypothesis in next_boxes
        if float(hypothesis.get('confidence', 0.0) or 0.0) >= float(projection_confidence_floor)
    ]

    if mode in {'debug-all', 'history-boxes', 'history-plus-projection'}:
        for box in active_history:
            is_current = bool(current_box) and int(box.get('sequence_index', -1)) == int(current_box.get('sequence_index', -999))
            _box_outline(box, dashed=False, current=is_current)
        if mode in {'debug-all', 'history-plus-projection'}:
            for hyp_idx, hyp in enumerate(visible_next_boxes[:3]):
                _box_outline(hyp, dashed=True, current=False)
                projected_candles = cast(list[dict[str, Any]], hyp.get("projected_candles", []))
                if not projected_candles and hyp_idx == 0:
                    projected_candles = fallback_projected_candles
                for candle in projected_candles:
                    _draw_projected_candle(candle, highlight=(hyp_idx == 0))
            path_points = cast(list[list[float]], (visible_next_boxes[0] if visible_next_boxes else {}).get('path_points', []))
            if len(path_points) >= 2:
                line_points = [(float(point[0]), float(point[1])) for point in path_points if len(point) == 2]
                if len(line_points) >= 2:
                    draw.line(line_points, fill=(120, 210, 255), width=2)
                    for idx, point in enumerate(line_points):
                        radius = 3 if idx < len(line_points) - 1 else 5
                        draw.ellipse(
                            [
                                (point[0] - radius, point[1] - radius),
                                (point[0] + radius, point[1] + radius),
                            ],
                            outline=(120, 210, 255),
                            fill=(12, 24, 28),
                            width=1,
                        )

    detections_sorted = sorted(filtered, key=lambda d: float(d.get('overlay_confidence', d.get('confidence', 0.0)) or 0.0), reverse=True)
    label_budget = int(max(0, label_budget))
    for detection in detections_sorted:
        x1, y1, x2, y2 = cast(list[float], detection['bbox'])
        conf = float(detection.get('overlay_confidence', detection.get('confidence', 0.0)) or 0.0)
        name = str(detection.get('pattern', ''))
        color = (0, 255, 0) if conf >= 0.6 else (255, 165, 0)
        draw.rectangle([(x1, y1), (x2, y2)], outline=color, width=2)
        for point in cast(list[list[float]], detection.get('focus_points', [])):
            px, py = float(point[0]), float(point[1])
            draw.ellipse([(px - 2, py - 2), (px + 2, py + 2)], fill=(255, 80, 80))
        if label_budget > 0 and conf >= 0.45:
            draw.text((x1, max(5.0, y1 - 14.0)), f'{name} {conf:.2f}', fill=color)
            label_budget -= 1

    visible_min = geometry.get('visible_price_min')
    visible_max = geometry.get('visible_price_max')
    for level in sr_levels:
        kind = str(level.get('type', 'support')).lower()
        color = (0, 255, 0) if kind == 'support' else (255, 0, 0)
        if isinstance(visible_min, (float, int)) and isinstance(visible_max, (float, int)):
            price = float(level.get('price', 0.0) or 0.0)
            denom = max(float(visible_max) - float(visible_min), 1e-9)
            norm = float(np.clip((float(visible_max) - price) / denom, 0.0, 1.0))
            y = int(inner_bbox[1] + norm * max(inner_bbox[3] - inner_bbox[1], 1.0))
        else:
            price = float(level.get('price', 0.0) or 0.0)
            frac = float(abs(price) % 1.0)
            y = int(inner_bbox[1] + frac * max(inner_bbox[3] - inner_bbox[1], 1.0))
        draw.line([(inner_bbox[0], y), (inner_bbox[2], y)], fill=color, width=1)
    for zone in user_zones or []:
        bbox = cast(list[float], zone.get("bbox", []))
        if len(bbox) != 4:
            continue
        kind = str(zone.get("kind", "reaction")).lower()
        label = str(zone.get("label", kind.title()))
        score = float(zone.get("score", zone.get("strength", 0.0)) or 0.0)
        if kind == "support":
            color = (88, 218, 123)
        elif kind == "resistance":
            color = (225, 107, 95)
        else:
            color = (215, 166, 90)
        x1, y1, x2, y2 = [float(v) for v in bbox]
        draw.rectangle([(x1, y1), (x2, y2)], outline=color, width=2)
        draw.text((x1 + 3.0, min(max(5.0, y1 + 3.0), max(5.0, y2 - 16.0))), f"{label} {score:.2f}", fill=color)
    return out if source_mode == "RGBA" else out.convert(source_mode)


def build_prob_gauge(prob: float, action: str) -> Any:
    fig: Any = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=prob * 100.0,
            title={"text": f"{action} Confidence (%)"},
            gauge={"axis": {"range": [0, 100]}},
        )
    )
    fig.update_layout(template="plotly_dark", margin=dict(l=10, r=10, t=40, b=10), height=260)
    return fig


def build_cv_debug_payload(
    result: dict[str, Any],
    render_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    detections = cast(list[dict[str, Any]], result.get("detections", []))
    global_patterns, latest_patterns, synthetic_patterns = _group_debug_detections(detections, limit=10)
    visible_global_patterns: list[dict[str, Any]] = []
    visible_latest_patterns: list[dict[str, Any]] = []
    visible_synthetic_patterns: list[dict[str, Any]] = []
    visible_detection_count = 0
    if render_config:
        filtered = _filter_detections_for_overlay(
            detections=detections,
            overlay_mode=str(render_config.get("overlay_mode", "history-plus-projection")),
            min_conf_global=float(render_config.get("min_conf_global", 0.42) or 0.42),
            min_conf_latest=float(render_config.get("min_conf_latest", 0.50) or 0.50),
        )
        visible_detection_count = len(filtered)
        visible_global_patterns, visible_latest_patterns, visible_synthetic_patterns = _group_debug_detections(
            filtered,
            limit=int(render_config.get("label_density", 10) or 10),
        )

    return {
        "latest_parse_quality": float(result.get("latest_parse_quality", 0.0) or 0.0),
        "latest_candle_confidence": float(result.get("latest_candle_confidence", 0.0) or 0.0),
        "latest_candle_conflict": bool(result.get("latest_candle_conflict", False)),
        "geometry_reference_direction": str(result.get("geometry_reference_direction", "HOLD")),
        "geometry_conflict": bool(result.get("geometry_conflict", False)),
        "strict_cv_fail_closed": bool(result.get("strict_cv_fail_closed", False)),
        "module_reliability": cast(dict[str, float], result.get("module_reliability", {})),
        "projection": cast(dict[str, Any], result.get("projection", {})),
        "memory": {
            "similarity": float(result.get("memory_similarity", 0.0) or 0.0),
            "direction": str(result.get("memory_direction", "HOLD")),
            "recall_count": int(result.get("memory_recall_count", 0) or 0),
            "threshold_used": float(result.get("memory_threshold_used", 0.0) or 0.0),
        },
        'reasoning_trace': cast(dict[str, Any], result.get('cv_reasoning_trace', {})),
        'sequence_transition_probabilities': cast(dict[str, float], result.get('sequence_transition_probabilities', {})),
        'memory_episode_matches': cast(list[dict[str, Any]], result.get('memory_episode_matches', [])),
        'memory_ambiguity_summary': cast(dict[str, Any], result.get('memory_ambiguity_summary', {})),
        'chart_geometry': cast(dict[str, Any], result.get('chart_geometry', {})),
        'sequence_state': cast(dict[str, Any], result.get('sequence_state', {})),
        'box_history': cast(list[dict[str, Any]], result.get('box_history', [])),
        'current_box': cast(dict[str, Any], result.get('current_box', {})),
        'next_box_hypotheses': cast(list[dict[str, Any]], result.get('next_box_hypotheses', [])),
        'chart_state': cast(dict[str, Any], result.get('chart_state', {})),
        'council_sequence_summary': cast(dict[str, Any], result.get('council_sequence_summary', {})),
        'projected_candle_candidates': cast(list[dict[str, Any]], result.get('projected_candle_candidates', [])),
        'projection_support': bool(result.get('projection_support', False)),
        'local_ensemble': cast(dict[str, Any], result.get('local_ensemble', {})),
        'global_detections_top': global_patterns,
        'latest_branch_top': latest_patterns,
        'synthetic_signals_top': synthetic_patterns,
        'active_render_controls': dict(render_config or {}),
        'visible_detection_count': int(visible_detection_count),
        'overlay_visible_global_top': visible_global_patterns,
        'overlay_visible_latest_top': visible_latest_patterns,
        'overlay_visible_synthetic_top': visible_synthetic_patterns,
    }


def explain_cv_debug_payload(cv_debug: dict[str, Any], result: dict[str, Any] | None = None) -> str:
    market_state = cast(dict[str, Any], cast(dict[str, Any], cv_debug.get("reasoning_trace", {})).get("market_state", {}))
    transitions = cast(dict[str, float], cv_debug.get("sequence_transition_probabilities", {}))
    memory = cast(dict[str, Any], cv_debug.get("memory", {}))
    module_rel = cast(dict[str, float], cv_debug.get("module_reliability", {}))
    projection = cast(dict[str, Any], cv_debug.get("projection", {}))
    current_box = cast(dict[str, Any], cv_debug.get("current_box", {}))
    next_boxes = cast(list[dict[str, Any]], cv_debug.get("next_box_hypotheses", []))

    macro = str(market_state.get("macro_trend", "unknown"))
    local = str(market_state.get("local_phase", "unknown"))
    intent = str(market_state.get("intent_next", "unknown"))
    conflict = str(market_state.get("conflict_type", "unknown"))

    if transitions:
        best_transition = max(transitions.keys(), key=lambda k: float(transitions.get(k, 0.0)))
        best_prob = float(transitions.get(best_transition, 0.0))
        transition_txt = f"Top transition: {best_transition} ({best_prob:.2f})"
    else:
        transition_txt = "Top transition: unavailable"

    mem_dir = str(memory.get("direction", "HOLD"))
    mem_sim = float(memory.get("similarity", 0.0) or 0.0)
    parse_q = float(cv_debug.get("latest_parse_quality", 0.0) or 0.0)
    latest_conf = float(cv_debug.get("latest_candle_confidence", 0.0) or 0.0)
    conflict_flag = bool(cv_debug.get("geometry_conflict", False))
    strict_closed = bool(cv_debug.get("strict_cv_fail_closed", False))
    ensemble_view = cast(dict[str, Any], cast(dict[str, Any], cv_debug.get("local_ensemble", {})).get("ensemble", {}))
    ensemble_label = str(ensemble_view.get("predicted_label", "HOLD"))
    ensemble_prob = float(ensemble_view.get("confidence", 0.0) or 0.0)
    champion = str(ensemble_view.get("champion_model", ""))
    confirmer = str(ensemble_view.get("confirmer_model", ""))

    # If reasoning trace is unavailable, fallback to top pattern view.
    if not market_state:
        global_top = cast(list[dict[str, Any]], cv_debug.get("global_detections_top", []))
        latest_top = cast(list[dict[str, Any]], cv_debug.get("latest_branch_top", []))
        g_txt = "none"
        l_txt = "none"
        if global_top:
            g = global_top[0]
            g_txt = f"{g.get('pattern', 'unknown')} ({float(g.get('confidence', 0.0) or 0.0):.2f})"
        if latest_top:
            l = latest_top[0]
            l_txt = f"{l.get('pattern', 'unknown')} ({float(l.get('confidence', 0.0) or 0.0):.2f})"
        best_action = str((result or {}).get("action", "HOLD"))
        best_conf = float((result or {}).get("confidence", 0.0) or 0.0)
        return (
            "Overall: unavailable\n"
            f"Local: {l_txt}\n"
            f"Next move: follow strongest latest branch signal\n"
            f"Risk: memory={mem_dir} ({mem_sim:.3f}), geometry_conflict={conflict_flag}, strict_fail_closed={strict_closed}\n"
            f"Best action: {best_action} ({best_conf:.2f})\n\n"
            "Notes:\n"
            f"- Local ensemble={ensemble_label} ({ensemble_prob:.2f}) via {champion or 'n/a'} / {confirmer or 'n/a'}\n"
            f"- Global pattern: {g_txt}\n"
            f"- Parse quality={parse_q:.2f}, latest confidence={latest_conf:.2f}"
        )

    cv_quality = float(module_rel.get("cv_quality", 0.0) or 0.0)
    structure_consistency = float(
        module_rel.get("structure_consistency", module_rel.get("cv_quality", 0.0)) or 0.0
    )
    current_box_txt = "none"
    if current_box:
        current_box_txt = (
            f"{str(current_box.get('box_type', 'balance'))}:{str(current_box.get('direction', 'HOLD')).upper()} "
            f"seq={str(current_box.get('sequence_signature', '')) or 'n/a'} "
            f"consol={float(current_box.get('consolidation_score', 0.0) or 0.0):.2f}"
        )
    next_box_txt = "none"
    if next_boxes:
        projected = next_boxes[0]
        next_box_txt = (
            f"{str(projected.get('box_type', 'balance'))}:{str(projected.get('direction', 'HOLD')).upper()} "
            f"conf={float(projected.get('confidence', 0.0) or 0.0):.2f} "
            f"gap={float(projected.get('dominance_gap', 0.0) or 0.0):.2f}"
        )
    projection_txt = "Projection detail: unavailable"
    if projection:
        projection_txt = (
            f"Projection detail: {str(projection.get('direction', 'HOLD')).upper()} "
            f"{str(projection.get('box_type', 'balance'))} "
            f"conf={float(projection.get('confidence', 0.0) or 0.0):.2f} "
            f"dom={float(projection.get('dominance', 0.0) or 0.0):.2f} "
            f"setup={str(projection.get('structure_setup', 'none'))}"
        )
        explanation = str(projection.get("explanation", "")).strip()
        if explanation:
            projection_txt += f" | {explanation}"
    best_action = str((result or {}).get("action", "HOLD"))
    best_conf = float((result or {}).get("confidence", 0.0) or 0.0)

    return (
        f"Overall: {macro}\n"
        f"Local: {local}\n"
        f"Next move: {intent} ({transition_txt})\n"
        f"Structure: current={current_box_txt} -> projected={next_box_txt}\n"
        f"{projection_txt}\n"
        f"Risk: {conflict}, geometry_conflict={conflict_flag}, memory={mem_dir} ({mem_sim:.3f})\n"
        f"Best action: {best_action} ({best_conf:.2f})\n\n"
        "Notes:\n"
        f"- Local ensemble={ensemble_label} ({ensemble_prob:.2f}) via {champion or 'n/a'} / {confirmer or 'n/a'}\n"
        f"- Quality parse={parse_q:.2f}, latest={latest_conf:.2f}, cv_quality={cv_quality:.2f}, structure_consistency={structure_consistency:.2f}\n"
        f"- Strict fail-closed={strict_closed}"
    )


def _interpreter_memory_match_quality(result: Mapping[str, Any]) -> str:
    memory_summary = cast(dict[str, Any], result.get("memory_ambiguity_summary", {}))
    similarity = float(np.clip(result.get("memory_similarity", 0.0) or 0.0, 0.0, 1.0))
    ambiguity = float(np.clip(memory_summary.get("ambiguity", 0.0) or 0.0, 0.0, 1.0))
    consensus_ratio = float(np.clip(memory_summary.get("consensus_ratio", 0.0) or 0.0, 0.0, 1.0))
    if similarity >= 0.87 and ambiguity <= 0.18 and consensus_ratio >= 0.70:
        return "high"
    if similarity >= 0.72 and ambiguity <= 0.30:
        return "medium"
    if similarity > 0.0:
        return "low"
    return "none"


def _interpreter_structure_summary(result: Mapping[str, Any]) -> str:
    chart_state = cast(dict[str, Any], result.get("chart_state", {}))
    current_box = cast(dict[str, Any], result.get("current_box", {}))
    next_boxes = cast(list[dict[str, Any]], result.get("next_box_hypotheses", []))
    latest_direction = str(result.get("geometry_reference_direction", "HOLD")).upper()
    latest_confidence = float(np.clip(result.get("latest_candle_confidence", 0.0) or 0.0, 0.0, 1.0))
    path_clarity = float(np.clip(chart_state.get("path_clarity", 0.0) or 0.0, 0.0, 1.0))

    parts: list[str] = []
    if current_box:
        parts.append(
            "current "
            f"{str(current_box.get('box_type', 'balance')).replace('_', ' ')} "
            f"{str(current_box.get('direction', 'HOLD')).upper()} "
            f"conf={float(current_box.get('confidence', 0.0) or 0.0):.2f}"
        )
    if next_boxes:
        projected = next_boxes[0]
        parts.append(
            "projected "
            f"{str(projected.get('box_type', 'balance')).replace('_', ' ')} "
            f"{str(projected.get('direction', 'HOLD')).upper()} "
            f"conf={float(projected.get('confidence', 0.0) or 0.0):.2f}"
        )
    if not parts:
        parts.append(
            f"setup={str(chart_state.get('structure_setup', 'none')).replace('_', ' ')} "
            f"path={path_clarity:.2f}"
        )
    parts.append(f"latest={latest_direction} {latest_confidence:.2f}")
    parts.append(f"path={path_clarity:.2f}")
    return " -> ".join(parts)


def _interpreter_gate_watchlists(
    result: Mapping[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    gate_details = cast(list[dict[str, Any]], result.get("gate_details", []))
    support_gate_details = cast(list[dict[str, Any]], result.get("support_gate_details", []))
    gate_blockers = [
        _alias_gate_name(gate.get("name", "gate"))
        for gate in gate_details
        if not bool(gate.get("pass_fail", False))
    ][:4]
    support_blockers: list[str] = []
    for gate in support_gate_details:
        name = _alias_gate_name(gate.get("name", "support_gate"))
        raw_name = str(gate.get("name", "")).strip().lower()
        passed = bool(gate.get("pass_fail", False))
        if raw_name == "opposition_strength":
            if passed:
                support_blockers.append(name)
            continue
        if not passed:
            support_blockers.append(name)

    risk_factors: list[str] = []
    if bool(result.get("latest_candle_conflict", False)):
        risk_factors.append("Latest candle disagrees with memory direction.")
    if bool(result.get("geometry_conflict", False)):
        risk_factors.append("Geometry is conflicting with the recalled regime.")
    if bool(result.get("opposition_alert", False)):
        risk_factors.append("Counterforce is elevated against the active bias.")
    if not bool(result.get("execution_guard_ok", True)):
        risk_factors.append("Execution guard is not cleared yet.")
    forecast_debug = cast(dict[str, Any], result.get("forecast_debug", {}))
    if bool(forecast_debug.get("force_hold", False)):
        risk_factors.append("Forecast interval is wide enough to keep the desk cautious.")
    if gate_blockers:
        risk_factors.append(f"Primary gate blockers: {', '.join(gate_blockers[:3])}.")
    if support_blockers:
        risk_factors.append(f"Support checks watching: {', '.join(support_blockers[:3])}.")
    return gate_blockers, support_blockers, risk_factors


def _interpreter_invalidation_condition(
    result: Mapping[str, Any],
    gate_blockers: Sequence[str],
    support_blockers: Sequence[str],
) -> str:
    projection = cast(dict[str, Any], result.get("projection", {}))
    action = str(result.get("action", "HOLD")).upper()
    latest_confidence = float(np.clip(result.get("latest_candle_confidence", 0.0) or 0.0, 0.0, 1.0))
    pieces: list[str] = []
    if action in {"BUY", "SELL"}:
        pieces.append(f"stand down if projection flips away from {action}")
    if latest_confidence < 0.50:
        pieces.append("latest-candle confirmation stays weak")
    if bool(result.get("geometry_conflict", False)):
        pieces.append("geometry remains in conflict with memory")
    if gate_blockers:
        pieces.append(f"core blockers persist ({', '.join(gate_blockers[:2])})")
    if support_blockers:
        pieces.append(f"support checks stay unresolved ({', '.join(support_blockers[:2])})")
    if not pieces:
        projection_direction = str(projection.get("direction", action)).upper()
        pieces.append(f"wait if structure loses confirmation or projection drifts away from {projection_direction}")
    return "; ".join(pieces) + "."


def _build_interpreter_fusion_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    chart_state = cast(dict[str, Any], result.get("chart_state", {}))
    projection = cast(dict[str, Any], result.get("projection", {}))
    memory_summary = cast(dict[str, Any], result.get("memory_ambiguity_summary", {}))
    forecast_debug = cast(dict[str, Any], result.get("forecast_debug", {}))
    rl_policy = cast(dict[str, Any], result.get("rl_policy", {}))
    local_ensemble = cast(dict[str, Any], result.get("local_ensemble", {}))
    ensemble_view = cast(dict[str, Any], local_ensemble.get("ensemble", {}))
    quantile_range = cast(list[float], result.get("quantile_range", [0.0, 0.0]))
    q05 = float(quantile_range[0]) if quantile_range else 0.0
    q95 = float(quantile_range[1]) if len(quantile_range) > 1 else q05
    gate_blockers, support_blockers, risk_factors = _interpreter_gate_watchlists(result)
    invalidation_condition = _interpreter_invalidation_condition(result, gate_blockers, support_blockers)

    return {
        "cv": {
            "setup": str(chart_state.get("structure_setup", "none")),
            "structure": _interpreter_structure_summary(result),
            "notes": str(result.get("explanation", "")),
            "parse_quality": float(np.clip(result.get("latest_parse_quality", 0.0) or 0.0, 0.0, 1.0)),
            "latest_direction": str(result.get("geometry_reference_direction", "HOLD")).upper(),
            "latest_confidence": float(np.clip(result.get("latest_candle_confidence", 0.0) or 0.0, 0.0, 1.0)),
        },
        "memory": {
            "match_quality": _interpreter_memory_match_quality(result),
            "similarity": float(np.clip(result.get("memory_similarity", 0.0) or 0.0, 0.0, 1.0)),
            "direction": str(result.get("memory_direction", "HOLD")).upper(),
            "ambiguity": float(np.clip(memory_summary.get("ambiguity", 0.0) or 0.0, 0.0, 1.0)),
            "consensus_ratio": float(np.clip(memory_summary.get("consensus_ratio", 0.0) or 0.0, 0.0, 1.0)),
            "dominant_label": str(memory_summary.get("dominant_label", result.get("memory_direction", "HOLD"))).upper(),
            "recall_count": int(result.get("memory_recall_count", 0) or 0),
        },
        "forecast": {
            "direction": str(projection.get("direction", "HOLD")).upper(),
            "magnitude": float(result.get("expected_3min_move_pct", 0.0) or 0.0),
            "q05": q05,
            "q95": q95,
            "execution_readiness": float(np.clip(forecast_debug.get("execution_readiness", 0.0) or 0.0, 0.0, 1.0)),
            "force_hold": bool(forecast_debug.get("force_hold", False)),
            "structure_trade_ready": bool(forecast_debug.get("structure_trade_ready", False)),
            "structure_setup": str(forecast_debug.get("structure_setup", chart_state.get("structure_setup", "none"))),
        },
        "rl": {
            "action": str(rl_policy.get("policy_action", result.get("trade_bias", result.get("action", "HOLD")))).upper(),
            "probs": cast(dict[str, float], rl_policy.get("probs", result.get("probabilities", {}))),
            "blend_weight": float(np.clip(rl_policy.get("blend_weight", 0.0) or 0.0, 0.0, 1.0)),
        },
        "gates": {
            "passing": int(result.get("gates_passing", 0) or 0),
            "total": len(cast(list[dict[str, Any]], result.get("gate_details", []))),
            "blockers": gate_blockers,
            "support_ok": bool(result.get("support_gates_ok", True)),
            "support_blockers": support_blockers,
            "risk": "elevated" if risk_factors else "contained",
        },
        "ensemble": {
            "action": str(result.get("action", "HOLD")).upper(),
            "trade_bias": str(result.get("trade_bias", result.get("action", "HOLD"))).upper(),
            "decision_state": str(result.get("decision_state", "UNCERTAIN")).upper(),
            "execution_permission": str(result.get("execution_permission", "WAIT_FOR_CONFIRMATION")).upper(),
            "confidence": float(np.clip(result.get("confidence", 0.0) or 0.0, 0.0, 1.0)),
            "consensus_ok": bool(result.get("consensus_ok", False)),
            "probabilities": cast(dict[str, float], result.get("probabilities", {})),
            "local_council_direction": str(ensemble_view.get("predicted_label", "HOLD")).upper(),
            "local_council_confidence": float(np.clip(ensemble_view.get("confidence", 0.0) or 0.0, 0.0, 1.0)),
        },
        "context": {
            "projection_direction": str(projection.get("direction", "HOLD")).upper(),
            "zone_bias": str(cast(dict[str, Any], result.get("zone_learning", {})).get("preferred_action", "HOLD")).upper(),
            "multi_timeframe_summary": str(cast(dict[str, Any], result.get("multi_timeframe", {})).get("summary", "")),
            "risk_factors": risk_factors,
            "invalidation": invalidation_condition,
        },
    }




# ------------------------------------------------------------------
# Core inference pipeline
# ------------------------------------------------------------------

def fused_feature_vector(
    detections: list[dict[str, Any]],
    forecast: Mapping[str, Any],
    style_vec: NDArray[np.float32],
) -> NDArray[np.float32]:
    d_conf = np.array([d["confidence"] for d in detections], dtype=np.float32)
    d_stats = np.array(
        [
            float(d_conf.mean()) if d_conf.size else 0.0,
            float(d_conf.max()) if d_conf.size else 0.0,
            float(len(detections)) / 50.0,
        ],
        dtype=np.float32,
    )
    # Use neutral/default chart-structure priors in the fused feature vector.
    momentum_vec = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    forecast_vec = np.array([
        forecast.get("q05", 0.0),
        forecast.get("q50", 0.0),
        forecast.get("q95", 0.0)
    ], dtype=np.float32)
    base = np.concatenate([d_stats, momentum_vec, forecast_vec, style_vec[:55]], axis=0)
    macro_local_agreement = 1.0  # Default to agreement
    memory_label_entropy = float(forecast.get("memory_label_entropy", 0.0))
    memory_regime_agreement = float(forecast.get("memory_regime_agreement", 0.0))
    contradiction_score = float(forecast.get("contradiction_score", 0.0))
    execution_readiness = float(forecast.get("execution_readiness", 0.0))
    new_feats = np.array([
        macro_local_agreement,
        memory_label_entropy,
        memory_regime_agreement,
        contradiction_score,
        execution_readiness,
    ], dtype=np.float32)
    fused = np.concatenate([base, new_feats], axis=0)
    if fused.size < 64:
        fused = np.pad(fused, (0, 64 - fused.size), mode="constant")
    return fused[:64].astype(np.float32)

def _extract_chart_structure(cv_engine: Any, image_rgb: Image.Image) -> tuple[dict[str, Any], dict[str, Any]]:
    geom = cast(dict[str, Any], cv_engine._extract_candle_geometry(image_rgb))
    visible_candles = cast(
        list[dict[str, Any]],
        cv_engine._select_recent_candles(
            cv_engine._extract_candle_candidates(image_rgb, max_candidates=96),
            max_count=96,
        ),
    )
    recent_candles = visible_candles[-18:]
    x_centers: list[float] = []
    colors: list[str] = []
    body_pcts: list[float] = []
    upper_wicks: list[float] = []
    lower_wicks: list[float] = []
    for candle in recent_candles:
        bbox = cast(list[float], candle.get("bbox", [0.0, 0.0, 0.0, 0.0]))
        x_centers.append(0.5 * (float(bbox[0]) + float(bbox[2])))
        colors.append("green" if float(candle.get("candle_color_green", 0.0) or 0.0) >= 0.5 else "red")
        body_pcts.append(float(candle.get("body_height_pct", 0.0) or 0.0))
        upper_wicks.append(float(candle.get("upper_wick_pct", 0.0) or 0.0))
        lower_wicks.append(float(candle.get("lower_wick_pct", 0.0) or 0.0))

    spacing_consistency = 0.0
    if len(x_centers) >= 3:
        diffs = np.diff(np.array(x_centers, dtype=np.float32))
        mean_diff = float(np.mean(np.abs(diffs)))
        if mean_diff > 1e-6:
            spacing_consistency = float(np.clip(1.0 - (float(np.std(diffs)) / mean_diff), 0.0, 1.0))

    color_flip_rate = 0.0
    if len(colors) >= 2:
        flips = sum(1 for left, right in zip(colors[:-1], colors[1:]) if left != right)
        color_flip_rate = float(flips / max(len(colors) - 1, 1))
    mean_body = float(np.mean(np.array(body_pcts, dtype=np.float32))) if body_pcts else 0.0
    body_std = float(np.std(np.array(body_pcts, dtype=np.float32))) if len(body_pcts) >= 2 else 0.0
    small_body_ratio = float(sum(1 for value in body_pcts if value <= 0.28) / max(len(body_pcts), 1)) if body_pcts else 0.0

    chart_geometry: dict[str, Any] = {
        "geometry_confidence": float(np.clip(float(geom.get("parse_conf", 0.0) or 0.0), 0.0, 1.0)),
        "body_height_pct": float(geom.get("body_height_pct", 0.0) or 0.0),
        "upper_wick_pct": float(geom.get("upper_wick_pct", 0.0) or 0.0),
        "lower_wick_pct": float(geom.get("lower_wick_pct", 0.0) or 0.0),
        "close_pos_in_range": float(geom.get("close_pos_in_range", 0.5) or 0.5),
        "candle_color_green": float(geom.get("candle_color_green", 0.0) or 0.0),
        "latest_candle_bbox": cast(list[float], geom.get("bbox", [0.0, 0.0, 0.0, 0.0])),
        "recent_candle_count": int(len(recent_candles)),
        "visible_candle_count": int(len(visible_candles)),
        "plot_bbox": [0.0, 0.0, float(image_rgb.width), float(image_rgb.height)],
        "plot_inner_bbox": [0.0, 0.0, float(image_rgb.width), float(image_rgb.height)],
        "latest_sequence_bbox": cast(list[float], geom.get("bbox", [0.0, 0.0, 0.0, 0.0])),
    }
    box_history = _build_box_history(visible_candles, float(image_rgb.width), float(image_rgb.height))
    current_box: dict[str, Any] = dict(box_history[-1]) if box_history else {
        "sequence_index": 0,
        "box_type": "balance",
        "direction": "BUY" if colors and colors[-1] == "green" else "SELL",
        "shape": "rectangular",
        "confidence": 0.0,
        "bbox": cast(list[float], geom.get("bbox", [0.0, 0.0, 0.0, 0.0])),
        "candle_count": 0,
        "start_idx": 0,
        "end_idx": 0,
        "height_pct": 0.0,
        "width_pct": 0.0,
        "price_span": 0.0,
        "mean_body_pct": mean_body,
        "maturity": 0.0,
        "color": [255, 215, 0],
        "consolidation_score": 0.0,
        "contains_consolidation": False,
        "sequence_signature": "",
        "internal_sequence": [],
        "center": [0.0, 0.0],
    }

    continuation_probability = 0.34
    pullback_probability = 0.24
    reversal_probability = 0.20
    fakeout_probability = 0.22
    current_type = str(current_box.get("box_type", "balance"))
    current_consolidation = float(np.clip(current_box.get("consolidation_score", 0.0), 0.0, 1.0))
    recent_boxes = box_history[-3:]
    recent_box_consolidation = float(
        np.mean(
            np.array(
                [float(np.clip(box.get("consolidation_score", 0.0), 0.0, 1.0)) for box in recent_boxes],
                dtype=np.float32,
            )
        )
    ) if recent_boxes else current_consolidation
    if current_type == "pullback":
        continuation_probability, pullback_probability, reversal_probability, fakeout_probability = 0.54, 0.18, 0.14, 0.14
    elif current_type == "impulse":
        continuation_probability, pullback_probability, reversal_probability, fakeout_probability = 0.46, 0.28, 0.12, 0.14
    elif current_type == "reversal_base":
        continuation_probability, pullback_probability, reversal_probability, fakeout_probability = 0.26, 0.16, 0.40, 0.18
    elif current_type == "balance":
        continuation_probability, pullback_probability, reversal_probability, fakeout_probability = 0.30, 0.22, 0.22, 0.26
    if current_box.get("contains_consolidation", False):
        continuation_probability = float(np.clip(continuation_probability + 0.10 + 0.10 * current_consolidation, 0.0, 1.0))
        fakeout_probability = float(np.clip(fakeout_probability - 0.04 * current_consolidation, 0.0, 1.0))
    elif current_type == "impulse" and recent_box_consolidation < 0.42:
        pullback_probability = float(np.clip(pullback_probability + 0.06, 0.0, 1.0))
    if colors[-3:].count("green") >= 2 and str(current_box.get("direction", "BUY")).upper() == "BUY":
        continuation_probability = float(np.clip(continuation_probability + 0.08, 0.0, 1.0))
    elif colors[-3:].count("red") >= 2 and str(current_box.get("direction", "SELL")).upper() == "SELL":
        continuation_probability = float(np.clip(continuation_probability + 0.08, 0.0, 1.0))
    total = max(continuation_probability + pullback_probability + reversal_probability + fakeout_probability, 1e-9)
    continuation_probability /= total
    pullback_probability /= total
    reversal_probability /= total
    fakeout_probability /= total
    next_box_hypotheses = _build_next_box_hypotheses(box_history, {
        "continuation_probability": continuation_probability,
        "pullback_probability": pullback_probability,
        "reversal_probability": reversal_probability,
        "fakeout_probability": fakeout_probability,
    }, chart_geometry)
    primary_next_box = dict(next_box_hypotheses[0]) if next_box_hypotheses else {}
    path_clarity = float(np.clip(primary_next_box.get("path_clarity", 0.0), 0.0, 1.0))
    box_sequence_agreement = float(
        np.clip(
            0.54 * float(current_box.get("dominant_ratio", 0.5) or 0.5)
            + 0.26 * (1.0 - color_flip_rate)
            + 0.20 * (1.0 - min(body_std / 0.20, 1.0)),
            0.0,
            1.0,
        )
    )
    has_active_consolidation = bool(
        bool(current_box.get("contains_consolidation", False))
        or current_type == "balance"
        or recent_box_consolidation >= 0.52
    )

    sequence_state: dict[str, Any] = {
        "recent_candle_count": int(len(recent_candles)),
        "visible_candle_count": int(len(visible_candles)),
        "spacing_consistency": spacing_consistency,
        "recent_colors": colors[-8:],
        "recent_body_pcts": body_pcts[-8:],
        "recent_upper_wicks": upper_wicks[-8:],
        "recent_lower_wicks": lower_wicks[-8:],
        "color_flip_rate": color_flip_rate,
        "small_body_ratio": small_body_ratio,
        "body_mean_pct": mean_body,
        "body_std_pct": body_std,
        "continuation_probability": float(continuation_probability),
        "pullback_probability": float(pullback_probability),
        "reversal_probability": float(reversal_probability),
        "fakeout_probability": float(fakeout_probability),
        "box_history": box_history,
        "current_box": current_box,
        "next_box_hypotheses": next_box_hypotheses,
        "primary_next_box": primary_next_box,
        "path_clarity": path_clarity,
        "box_sequence_agreement": box_sequence_agreement,
        "recent_box_consolidation": recent_box_consolidation,
        "has_active_consolidation": has_active_consolidation,
        "all_visible_candles": visible_candles,
    }
    return chart_geometry, sequence_state


def _estimate_implied_move_pct(
    detections: Sequence[dict[str, Any]],
    direction_probability: float,
    entry_body_pct: float,
) -> float:
    move_targets = {
        "next_move_small": 0.10,
        "next_move_medium": 0.22,
        "next_move_large": 0.40,
    }
    strongest = 0.0
    strongest_conf = 0.0
    for detection in detections:
        pattern = str(detection.get("pattern", "")).lower().strip().replace(" ", "_")
        conf = float(detection.get("confidence", 0.0) or 0.0)
        if pattern not in move_targets:
            continue
        if conf > strongest_conf:
            strongest_conf = conf
            strongest = move_targets[pattern]
    body_prior = float(np.clip(entry_body_pct * 1.6, 0.05, 1.00))
    move = strongest if strongest_conf > 0.0 else body_prior
    scaled = move * float(np.clip(0.70 + 0.60 * direction_probability, 0.40, 1.40))
    return float(np.clip(scaled, 0.03, 1.20))


def _derive_proxy_price_series(sequence_state: Mapping[str, Any], limit: int = 12) -> list[float]:
    candles = cast(list[dict[str, Any]], sequence_state.get("all_visible_candles", []))
    if not candles:
        return []
    value = 1.0
    series: list[float] = []
    for candle in candles[-max(4, limit):]:
        body = float(np.clip(candle.get("body_height_pct", 0.0), 0.0, 1.0))
        close_pos = float(np.clip(candle.get("close_pos_in_range", 0.5), 0.0, 1.0))
        green_bias = float(np.clip(candle.get("candle_color_green", 0.0), 0.0, 1.0))
        direction = 1.0 if green_bias >= 0.5 else -1.0
        delta = 0.05 + 0.55 * body + 0.20 * abs(close_pos - 0.5)
        value += direction * delta
        series.append(round(value, 6))
    return series


def _build_sequence_model_summary(
    sequence_state: Mapping[str, Any],
    chart_geometry: Mapping[str, Any],
    *,
    market_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    market_state = market_state or {}
    recent_colors = cast(list[str], sequence_state.get("recent_colors", []))
    box_history = cast(list[dict[str, Any]], sequence_state.get("box_history", []))
    current_box = cast(dict[str, Any], sequence_state.get("current_box", {}))
    next_box = cast(dict[str, Any], sequence_state.get("primary_next_box", {}))
    continuation_probability = float(np.clip(sequence_state.get("continuation_probability", 0.0), 0.0, 1.0))
    reversal_probability = float(np.clip(sequence_state.get("reversal_probability", 0.0), 0.0, 1.0))
    fakeout_probability = float(np.clip(sequence_state.get("fakeout_probability", 0.0), 0.0, 1.0))
    box_sequence_agreement = float(np.clip(sequence_state.get("box_sequence_agreement", 0.0), 0.0, 1.0))
    path_clarity = float(np.clip(sequence_state.get("path_clarity", 0.0), 0.0, 1.0))
    spacing_consistency = float(np.clip(sequence_state.get("spacing_consistency", 0.0), 0.0, 1.0))
    color_flip_rate = float(np.clip(sequence_state.get("color_flip_rate", 0.0), 0.0, 1.0))
    body_std_pct = float(np.clip(float(sequence_state.get("body_std_pct", 0.0) or 0.0) / 0.25, 0.0, 1.0))
    small_body_ratio = float(np.clip(sequence_state.get("small_body_ratio", 0.0), 0.0, 1.0))
    latest_body_pct = float(np.clip(chart_geometry.get("body_height_pct", 0.0), 0.0, 1.0))
    geometry_confidence = float(np.clip(chart_geometry.get("geometry_confidence", 0.0), 0.0, 1.0))
    macro_trend = str(market_state.get("macro_trend", "")).upper()

    candle_weights = np.linspace(0.55, 1.0, num=max(len(recent_colors), 1), dtype=np.float32)
    candle_buy = 0.0
    candle_sell = 0.0
    for idx, color in enumerate(recent_colors):
        weight = float(candle_weights[min(idx, len(candle_weights) - 1)])
        if color == "green":
            candle_buy += weight
        elif color == "red":
            candle_sell += weight
    candle_total = max(candle_buy + candle_sell, 1e-8)
    candle_buy_pressure = float(np.clip(candle_buy / candle_total, 0.0, 1.0))
    candle_sell_pressure = float(np.clip(candle_sell / candle_total, 0.0, 1.0))

    box_buy = 0.0
    box_sell = 0.0
    for idx, box in enumerate(box_history[-4:]):
        weight = 0.55 + 0.15 * idx
        confidence = float(np.clip(box.get("confidence", 0.0), 0.0, 1.0))
        maturity = float(np.clip(box.get("maturity", 0.0), 0.0, 1.0))
        direction = str(box.get("direction", "HOLD")).upper()
        magnitude = weight * (0.45 + 0.30 * confidence + 0.25 * maturity)
        if direction == "BUY":
            box_buy += magnitude
        elif direction == "SELL":
            box_sell += magnitude
    box_total = max(box_buy + box_sell, 1e-8)
    box_buy_pressure = float(np.clip(box_buy / box_total, 0.0, 1.0))
    box_sell_pressure = float(np.clip(box_sell / box_total, 0.0, 1.0))

    current_direction = str(current_box.get("direction", "HOLD")).upper()
    next_direction = str(next_box.get("direction", current_direction)).upper()
    current_confidence = float(np.clip(current_box.get("confidence", 0.0), 0.0, 1.0))
    next_confidence = float(np.clip(next_box.get("confidence", 0.0), 0.0, 1.0))
    projected_impulse = float(str(next_box.get("box_type", "")).lower() == "impulse")

    buy_pressure = float(
        np.clip(
            0.24 * candle_buy_pressure
            + 0.20 * box_buy_pressure
            + 0.16 * continuation_probability * float(current_direction == "BUY")
            + 0.12 * projected_impulse * float(next_direction == "BUY") * next_confidence
            + 0.10 * current_confidence * float(current_direction == "BUY")
            + 0.10 * geometry_confidence
            + 0.08 * float(macro_trend == "BULL"),
            0.0,
            1.0,
        )
    )
    sell_pressure = float(
        np.clip(
            0.24 * candle_sell_pressure
            + 0.20 * box_sell_pressure
            + 0.16 * continuation_probability * float(current_direction == "SELL")
            + 0.12 * projected_impulse * float(next_direction == "SELL") * next_confidence
            + 0.10 * current_confidence * float(current_direction == "SELL")
            + 0.10 * geometry_confidence
            + 0.08 * float(macro_trend == "BEAR"),
            0.0,
            1.0,
        )
    )
    continuation_readiness = float(
        np.clip(
            0.34 * continuation_probability
            + 0.22 * path_clarity
            + 0.18 * box_sequence_agreement
            + 0.14 * spacing_consistency
            + 0.12 * (1.0 - fakeout_probability),
            0.0,
            1.0,
        )
    )
    reversal_pressure = float(
        np.clip(
            0.34 * reversal_probability
            + 0.22 * fakeout_probability
            + 0.16 * color_flip_rate
            + 0.14 * small_body_ratio
            + 0.14 * max(0.0, 1.0 - continuation_probability),
            0.0,
            1.0,
        )
    )
    history_coherence = float(
        np.clip(
            0.32 * box_sequence_agreement
            + 0.24 * spacing_consistency
            + 0.22 * (1.0 - color_flip_rate)
            + 0.22 * (1.0 - body_std_pct),
            0.0,
            1.0,
        )
    )
    compression_score = float(
        np.clip(
            0.38 * small_body_ratio
            + 0.24 * color_flip_rate
            + 0.20 * max(0.0, 1.0 - latest_body_pct)
            + 0.18 * float(bool(sequence_state.get("has_active_consolidation", False))),
            0.0,
            1.0,
        )
    )
    transition_energy = float(
        np.clip(
            0.36 * max(buy_pressure, sell_pressure)
            + 0.24 * continuation_readiness
            + 0.20 * path_clarity
            + 0.20 * next_confidence,
            0.0,
            1.0,
        )
    )
    direction = "HOLD"
    if buy_pressure > sell_pressure * 1.04:
        direction = "BUY"
    elif sell_pressure > buy_pressure * 1.04:
        direction = "SELL"
    direction_confidence = float(
        np.clip(
            abs(buy_pressure - sell_pressure)
            + 0.18 * max(buy_pressure, sell_pressure)
            + 0.12 * continuation_readiness,
            0.0,
            1.0,
        )
    )
    uncertainty = float(
        np.clip(
            0.34 * (1.0 - direction_confidence)
            + 0.22 * color_flip_rate
            + 0.20 * (1.0 - history_coherence)
            + 0.14 * fakeout_probability
            + 0.10 * (1.0 - geometry_confidence),
            0.0,
            1.0,
        )
    )
    return {
        "direction": direction,
        "direction_confidence": direction_confidence,
        "buy_pressure": buy_pressure,
        "sell_pressure": sell_pressure,
        "continuation_readiness": continuation_readiness,
        "reversal_pressure": reversal_pressure,
        "history_coherence": history_coherence,
        "compression_score": compression_score,
        "transition_energy": transition_energy,
        "uncertainty": uncertainty,
    }


def _build_local_ensemble_routing_context(
    *,
    chart_state: Mapping[str, Any],
    sequence_state: Mapping[str, Any],
    grounded_chart: Mapping[str, Any],
    reasoning_trace: Mapping[str, Any],
    memory_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "chart_state": dict(chart_state),
        "sequence_state": dict(sequence_state),
        "grounded_chart": dict(grounded_chart),
        "reasoning_trace": dict(reasoning_trace),
        "memory_summary": dict(memory_summary or {}),
    }


def _ensemble_base_probs(
    local_ensemble: Mapping[str, Any],
    *,
    chart_state: Mapping[str, Any] | None = None,
    memory_summary: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    ensemble_view = cast(dict[str, Any], local_ensemble.get("ensemble", {}))
    buy_prob = float(ensemble_view.get("buy_prob", 0.5) or 0.5)
    sell_prob = float(ensemble_view.get("sell_prob", 0.5) or 0.5)
    predicted_label = str(ensemble_view.get("predicted_label", "HOLD")).upper()
    margin = float(abs(buy_prob - sell_prob))
    disagreement = float(ensemble_view.get("disagreement", 0.0) or 0.0)
    consensus_ratio = float(ensemble_view.get("consensus_ratio", 0.5) or 0.5)
    state = cast(dict[str, Any], chart_state or {})
    path_clarity = float(np.clip(state.get("path_clarity", 0.0), 0.0, 1.0))
    continuation_prob = float(np.clip(state.get("continuation_probability", 0.0), 0.0, 1.0))
    active_consolidation = bool(state.get("has_active_consolidation", False))
    structure_trade_ready = bool(state.get("structure_trade_ready", False))
    projected_next_box = cast(dict[str, Any], state.get("projected_next_box", {}))
    projected_direction = str(projected_next_box.get("direction", predicted_label)).upper()
    projected_confidence = float(np.clip(projected_next_box.get("confidence", 0.0), 0.0, 1.0))
    projection_bias_confidence = float(
        np.clip(state.get("projection_bias_confidence", projected_confidence), 0.0, 1.0)
    )
    projection_dominance = float(np.clip(state.get("projection_dominance", 0.0), 0.0, 1.0))
    projection_alignment = float(
        1.0 if predicted_label in {"BUY", "SELL"} and projected_direction == predicted_label else 0.0
    )
    memory_view = cast(dict[str, Any], memory_summary or {})
    memory_consensus = float(np.clip(memory_view.get("consensus_ratio", 0.0), 0.0, 1.0))
    memory_alignment = float(
        1.0
        if predicted_label in {"BUY", "SELL"}
        and not bool(memory_view.get("mixed_labels", False))
        and str(memory_view.get("dominant_label", "HOLD")).upper() == predicted_label
        else 0.0
    )
    if projected_direction in {"BUY", "SELL"}:
        projection_shift = float(
            np.clip(
                0.16 * projection_bias_confidence
                + 0.10 * projection_dominance
                + 0.06 * path_clarity
                + 0.04 * float(1.0 if structure_trade_ready else 0.0),
                0.0,
                0.32,
            )
        )
        if projected_direction == "BUY":
            buy_prob = float(np.clip(buy_prob + projection_shift, 0.0, 1.0))
            sell_prob = float(np.clip(sell_prob * max(0.0, 1.0 - 0.55 * projection_shift), 0.0, 1.0))
        else:
            sell_prob = float(np.clip(sell_prob + projection_shift, 0.0, 1.0))
            buy_prob = float(np.clip(buy_prob * max(0.0, 1.0 - 0.55 * projection_shift), 0.0, 1.0))
    directional_total = max(buy_prob + sell_prob, 1e-8)
    raw_hold = (
        0.04
        + 0.24 * (1.0 - margin)
        + 0.10 * disagreement
        + 0.10 * (1.0 - consensus_ratio)
        - 0.10 * float(1.0 if structure_trade_ready else 0.0)
        - 0.08 * float(1.0 if active_consolidation else 0.0)
        - 0.06 * path_clarity
        - 0.10 * projection_bias_confidence
        - 0.08 * projection_dominance
        - 0.06 * projected_confidence * projection_alignment
        - 0.06 * continuation_prob * projection_alignment
        - 0.05 * memory_consensus * memory_alignment
    )
    hold_floor = 0.05 if (structure_trade_ready or projection_bias_confidence >= 0.60) else 0.08
    hold_cap = 0.42 if structure_trade_ready else 0.48
    hold_prob = float(
        np.clip(
            max(raw_hold, 0.18 if predicted_label == "HOLD" else raw_hold),
            hold_floor,
            hold_cap,
        )
    )
    remaining = max(1.0 - hold_prob, 1e-8)
    buy_scaled = float(remaining * buy_prob / directional_total)
    sell_scaled = float(remaining * sell_prob / directional_total)
    total = max(buy_scaled + sell_scaled + hold_prob, 1e-8)
    return {
        "BUY": float(buy_scaled / total),
        "SELL": float(sell_scaled / total),
        "HOLD": float(hold_prob / total),
    }


def _detection_confidence(
    detections: Sequence[Mapping[str, Any]],
    *patterns: str,
) -> float:
    normalized = {str(pattern).strip().lower() for pattern in patterns if str(pattern).strip()}
    best = 0.0
    for row in detections:
        pattern = str(row.get("pattern", "")).strip().lower()
        if pattern in normalized:
            best = max(best, float(np.clip(row.get("confidence", 0.0), 0.0, 1.0)))
    return float(best)


def _build_council_sequence_summary(local_ensemble: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw_consensus = cast(
        Mapping[str, Any],
        cast(dict[str, Any], local_ensemble.get("ensemble", {})).get("sequence_task_consensus", {}),
    )
    summary: dict[str, dict[str, Any]] = {}
    for task_name, raw_payload in raw_consensus.items():
        payload = cast(Mapping[str, Any], raw_payload)
        value = str(payload.get("value", "")).strip()
        if not value:
            continue
        summary[str(task_name)] = {
            "value": value,
            "confidence": float(np.clip(payload.get("confidence", 0.0), 0.0, 1.0)),
            "support": float(max(0.0, float(payload.get("support", 0.0) or 0.0))),
            "n_models": int(max(0, int(payload.get("n_models", 0) or 0))),
        }
    return summary


def _projected_pattern_family(
    *,
    direction: str,
    body_class: str,
    wick_bias: str,
    projected_role: str,
    trigger: str,
) -> str:
    normalized_direction = str(direction).upper()
    normalized_body = str(body_class).lower()
    normalized_wick = str(wick_bias).lower()
    normalized_role = str(projected_role).lower()
    normalized_trigger = str(trigger).lower()
    if normalized_body == "small" and normalized_wick == "balanced":
        return "doji"
    if normalized_body == "small" and normalized_wick == "lower" and normalized_direction == "BUY":
        return "hammer"
    if normalized_body == "small" and normalized_wick == "upper" and normalized_direction == "SELL":
        return "shooting_star"
    if "reversal" in normalized_role or "reversal" in normalized_trigger:
        return "reversal_release"
    if normalized_body == "expansion":
        return "expansion_continuation"
    if "breakout" in normalized_trigger:
        return "breakout_drive"
    return "continuation_push"


def _build_projected_candle_candidates(
    *,
    projected_box: Mapping[str, Any],
    detections: Sequence[Mapping[str, Any]],
    chart_state: Mapping[str, Any],
    sequence_state: Mapping[str, Any],
    local_ensemble: Mapping[str, Any],
) -> list[dict[str, Any]]:
    bbox = cast(list[float], projected_box.get("bbox", []))
    if len(bbox) != 4:
        return []
    x1, y1, x2, y2 = [float(v) for v in bbox]
    width = max(x2 - x1, 12.0)
    height = max(y2 - y1, 18.0)
    projected_direction = str(projected_box.get("direction", chart_state.get("projection_bias_direction", "HOLD"))).upper()
    if projected_direction not in {"BUY", "SELL"}:
        projected_direction = str(chart_state.get("direction", "BUY")).upper()
        if projected_direction not in {"BUY", "SELL"}:
            projected_direction = "BUY"
    projected_confidence = float(
        np.clip(
            projected_box.get("confidence", chart_state.get("projection_bias_confidence", 0.0)),
            0.0,
            1.0,
        )
    )
    projected_type = str(projected_box.get("box_type", chart_state.get("structure_setup", "balance"))).lower()
    trigger = str(projected_box.get("trigger", "")).lower()
    council_sequence = _build_council_sequence_summary(local_ensemble)
    council_direction = str(cast(dict[str, Any], council_sequence.get("projection_direction", {})).get("value", projected_direction)).upper()
    council_dir_conf = float(np.clip(cast(dict[str, Any], council_sequence.get("projection_direction", {})).get("confidence", 0.0), 0.0, 1.0))
    if council_direction in {"BUY", "SELL"} and council_dir_conf >= 0.56:
        projected_direction = council_direction

    next_buy = _detection_confidence(detections, "next_candle_buy")
    next_sell = _detection_confidence(detections, "next_candle_sell")
    wick_upper = _detection_confidence(detections, "wick_dominance_upper")
    wick_lower = _detection_confidence(detections, "wick_dominance_lower")
    move_small = _detection_confidence(detections, "next_move_small")
    move_medium = _detection_confidence(detections, "next_move_medium")
    move_large = _detection_confidence(detections, "next_move_large")

    projected_role = str(projected_box.get("trigger", "")).lower()
    swing_state = cast(dict[str, Any], projected_box.get("swing_state", {}))
    if not projected_role:
        projected_role = str(swing_state.get("projected_role", "")).lower()
    if not projected_role:
        projected_role = str(cast(dict[str, Any], council_sequence.get("projected_role", {})).get("value", "")).lower()

    if projected_type == "balance":
        candle_count = 3 if width >= 90.0 else 2
        body_classes = ["small"] * candle_count
    elif projected_type == "pullback":
        candle_count = 2
        body_classes = ["small", "medium"]
    elif projected_type == "reversal_base":
        candle_count = 2
        body_classes = ["small", "expansion"]
    else:
        candle_count = 2 if move_small >= max(move_medium, move_large) and projected_confidence < 0.74 else 1
        body_classes = ["expansion"] + (["medium"] if candle_count > 1 else [])

    if move_large >= max(move_medium, move_small):
        body_classes = ["expansion" if idx == 0 else "medium" for idx in range(candle_count)]
    elif move_medium >= max(move_large, move_small):
        body_classes = ["medium" for _ in range(candle_count)]

    wick_bias = "balanced"
    if wick_lower > wick_upper + 0.06:
        wick_bias = "lower"
    elif wick_upper > wick_lower + 0.06:
        wick_bias = "upper"
    elif projected_type == "reversal_base":
        wick_bias = "lower" if projected_direction == "BUY" else "upper"

    direction_hint_conf = max(
        projected_confidence,
        next_buy if projected_direction == "BUY" else next_sell,
        council_dir_conf,
    )
    slot_gap = max(4.0, width * 0.06)
    usable_width = max(width - slot_gap * max(candle_count - 1, 0), 12.0)
    slot_width = max(8.0, usable_width / max(candle_count, 1))
    candidates: list[dict[str, Any]] = []
    for idx in range(candle_count):
        progress = float(idx / max(candle_count - 1, 1)) if candle_count > 1 else 0.5
        body_class = body_classes[min(idx, len(body_classes) - 1)]
        body_scale = 0.26 if body_class == "small" else (0.44 if body_class == "medium" else 0.66)
        candle_span = height * (0.50 if projected_type == "balance" else 0.72)
        if body_class == "expansion":
            candle_span = height * 0.82
        center_y = y1 + height * (
            0.64 - 0.22 * progress if projected_direction == "BUY" else 0.36 + 0.22 * progress
        )
        body_height = max(8.0, candle_span * body_scale)
        body_top = float(np.clip(center_y - body_height * 0.5, y1 + 2.0, y2 - 6.0))
        body_bottom = float(np.clip(body_top + body_height, body_top + 4.0, y2 - 2.0))
        slot_left = x1 + idx * (slot_width + slot_gap)
        slot_right = min(x2, slot_left + slot_width)
        center_x = 0.5 * (slot_left + slot_right)
        body_width = max(6.0, slot_width * (0.42 if body_class == "small" else (0.54 if body_class == "medium" else 0.66)))
        body_x1 = float(np.clip(center_x - body_width * 0.5, x1 + 1.0, x2 - 7.0))
        body_x2 = float(np.clip(body_x1 + body_width, body_x1 + 4.0, x2 - 1.0))
        upper_wick = candle_span * (0.18 if wick_bias == "lower" else (0.36 if wick_bias == "upper" else 0.26))
        lower_wick = candle_span * (0.18 if wick_bias == "upper" else (0.36 if wick_bias == "lower" else 0.26))
        wick_top = float(np.clip(body_top - upper_wick, y1 + 1.0, body_top))
        wick_bottom = float(np.clip(body_bottom + lower_wick, body_bottom, y2 - 1.0))
        pattern_family = _projected_pattern_family(
            direction=projected_direction,
            body_class=body_class,
            wick_bias=wick_bias,
            projected_role=projected_role,
            trigger=trigger,
        )
        candidate_conf = float(
            np.clip(
                0.42 * direction_hint_conf
                + 0.18 * projected_confidence
                + 0.14 * (1.0 if body_class == "expansion" and projected_type == "impulse" else 0.0)
                + 0.10 * max(move_small, move_medium, move_large)
                + 0.08 * float(cast(dict[str, Any], council_sequence.get("next_box_type", {})).get("value", "") == projected_type)
                + 0.08 * float(cast(dict[str, Any], council_sequence.get("next_box_direction", {})).get("value", "") == projected_direction),
                0.0,
                1.0,
            )
        )
        candidates.append(
            {
                "index": idx + 1,
                "direction": projected_direction,
                "body_class": body_class,
                "wick_bias": wick_bias,
                "pattern_family": pattern_family,
                "confidence": candidate_conf,
                "center_x": center_x,
                "body_bbox": [body_x1, body_top, body_x2, body_bottom],
                "wick_top": wick_top,
                "wick_bottom": wick_bottom,
            }
        )
    return candidates


def _enrich_next_box_hypotheses_with_projected_candles(
    next_box_hypotheses: Sequence[Mapping[str, Any]],
    *,
    detections: Sequence[Mapping[str, Any]],
    chart_state: Mapping[str, Any],
    sequence_state: Mapping[str, Any],
    local_ensemble: Mapping[str, Any],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for hypothesis in next_box_hypotheses:
        row = dict(hypothesis)
        projected_candles = _build_projected_candle_candidates(
            projected_box=row,
            detections=detections,
            chart_state=chart_state,
            sequence_state=sequence_state,
            local_ensemble=local_ensemble,
        )
        row["projected_candles"] = projected_candles
        if projected_candles:
            row["projected_candle_summary"] = {
                "count": int(len(projected_candles)),
                "pattern_family": str(projected_candles[0].get("pattern_family", "")),
                "direction": str(projected_candles[0].get("direction", row.get("direction", "HOLD"))).upper(),
                "confidence": float(np.mean(np.asarray([candle.get("confidence", 0.0) for candle in projected_candles], dtype=np.float32))),
            }
        enriched.append(row)
    return enriched


def _build_chart_state(
    *,
    detections: Sequence[dict[str, Any]],
    local_ensemble: Mapping[str, Any],
    reasoning_trace: dict[str, Any],
    chart_geometry: Mapping[str, Any],
    sequence_state: Mapping[str, Any],
    grounded_chart: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    grounded_chart = grounded_chart or {}
    grounded_structure = cast(dict[str, Any], grounded_chart.get("structure_summary", {}))
    sequence_model = cast(dict[str, Any], sequence_state.get("sequence_model", {}))
    ensemble_view = cast(dict[str, Any], local_ensemble.get("ensemble", {}))
    market_state = cast(dict[str, Any], reasoning_trace.get("market_state", {}))
    direction = str(ensemble_view.get("predicted_label", "HOLD")).upper()
    direction_probability = float(ensemble_view.get("confidence", 0.5) or 0.5)
    local_phase = str(market_state.get("local_phase", "continuation_base"))
    macro_trend = str(market_state.get("macro_trend", "BULL"))
    recent_colors = cast(list[str], sequence_state.get("recent_colors", []))
    box_history = cast(list[dict[str, Any]], sequence_state.get("box_history", []))

    continuation_prob = float(np.clip(sequence_state.get("continuation_probability", 0.0), 0.0, 1.0))
    reversal_prob = float(np.clip(sequence_state.get("reversal_probability", 0.0), 0.0, 1.0))
    fakeout_prob = float(np.clip(sequence_state.get("fakeout_probability", 0.0), 0.0, 1.0))
    color_flip_rate = float(np.clip(sequence_state.get("color_flip_rate", 0.0), 0.0, 1.0))
    small_body_ratio = float(np.clip(sequence_state.get("small_body_ratio", 0.0), 0.0, 1.0))
    current_box = cast(dict[str, Any], sequence_state.get("current_box", {}))
    projected_next_box = cast(dict[str, Any], sequence_state.get("primary_next_box", {}))
    current_box_type = str(current_box.get("box_type", "balance")).lower()
    current_box_dir = str(current_box.get("direction", direction)).upper()
    current_box_consolidation = float(np.clip(current_box.get("consolidation_score", 0.0), 0.0, 1.0))
    recent_box_consolidation = float(np.clip(sequence_state.get("recent_box_consolidation", current_box_consolidation), 0.0, 1.0))
    box_sequence_agreement = float(np.clip(sequence_state.get("box_sequence_agreement", 0.0), 0.0, 1.0))
    path_clarity = float(np.clip(sequence_state.get("path_clarity", 0.0), 0.0, 1.0))
    has_active_consolidation = bool(sequence_state.get("has_active_consolidation", False))
    projected_direction = str(projected_next_box.get("direction", current_box_dir)).upper()
    projected_box_type = str(projected_next_box.get("box_type", current_box_type)).lower()
    projection_alignment = float(1.0 if projected_direction == direction else 0.0)
    projected_box_confidence = float(np.clip(projected_next_box.get("confidence", 0.0), 0.0, 1.0))
    projection_dominance = float(np.clip(projected_next_box.get("dominance_gap", 0.0), 0.0, 1.0))
    projection_bias_confidence = float(np.clip(0.68 * projected_box_confidence + 0.32 * projection_dominance, 0.0, 1.0))
    projection_explanation = str(projected_next_box.get("explanation", "")).strip()
    swing_state = cast(
        dict[str, Any],
        projected_next_box.get(
            "swing_state",
            _classify_swing_state(box_history, current_box, market_state),
        ),
    )
    current_box_confidence = float(np.clip(current_box.get("confidence", 0.0), 0.0, 1.0))
    current_box_maturity = float(np.clip(current_box.get("maturity", 0.0), 0.0, 1.0))

    entry_type = "reversal" if local_phase in {"counter_trend_spike", "reversal_base"} or current_box_type == "reversal_base" else "continuation"
    reversal_signal = "wick_rejection" if entry_type == "reversal" else "none"
    continuation_signal = "breakout" if local_phase in {"with_trend_push", "continuation_base"} or current_box_type == "impulse" else (
        "impulse_pause" if local_phase == "with_trend_pause" or current_box_type == "pullback" else "none"
    )
    if has_active_consolidation and projected_box_type == "impulse":
        continuation_signal = "breakout"
    if continuation_signal == "none" and local_phase == "counter_trend_pullback" and continuation_prob >= 0.45 and reversal_prob <= 0.30:
        continuation_signal = "impulse_pause"
    if current_box_type == "balance" and fakeout_prob >= 0.24:
        continuation_signal = "range_break_watch"
    if current_box_type == "reversal_base" and projected_box_type == "impulse" and projected_direction == current_box_dir:
        continuation_signal = "reversal_release"
    momentum_bias = "bullish" if (macro_trend == "BULL" or current_box_dir == "BUY" and continuation_prob >= reversal_prob) else "bearish"
    entry_color = "green" if direction == "BUY" else ("red" if direction == "SELL" else "neutral")
    body_pct = float(chart_geometry.get("body_height_pct", 0.0) or 0.0)

    buy_prob = float(ensemble_view.get("buy_prob", 0.5) or 0.5)
    sell_prob = float(ensemble_view.get("sell_prob", 0.5) or 0.5)
    implied_move = _estimate_implied_move_pct(detections, direction_probability, body_pct)

    champion = str(ensemble_view.get("champion_model", ""))
    confirmer = str(ensemble_view.get("confirmer_model", ""))
    disagreement = float(ensemble_view.get("disagreement", 0.0) or 0.0)
    grounded_confidence = float(np.clip(grounded_chart.get("grounded_confidence", 0.0), 0.0, 1.0))
    artifact_score = float(
        np.clip(
            cast(dict[str, Any], grounded_chart.get("artifact_summary", {})).get("artifact_score", 0.0),
            0.0,
            1.0,
        )
    )
    sequence_buy_pressure = float(np.clip(sequence_model.get("buy_pressure", 0.0), 0.0, 1.0))
    sequence_sell_pressure = float(np.clip(sequence_model.get("sell_pressure", 0.0), 0.0, 1.0))
    continuation_readiness = float(np.clip(sequence_model.get("continuation_readiness", continuation_prob), 0.0, 1.0))
    reversal_pressure = float(np.clip(sequence_model.get("reversal_pressure", reversal_prob), 0.0, 1.0))
    history_coherence = float(np.clip(sequence_model.get("history_coherence", box_sequence_agreement), 0.0, 1.0))
    sequence_uncertainty = float(np.clip(sequence_model.get("uncertainty", 0.0), 0.0, 1.0))
    sequence_bias_direction = str(sequence_model.get("direction", "HOLD")).upper()
    sequence_bias_confidence = float(np.clip(sequence_model.get("direction_confidence", 0.0), 0.0, 1.0))
    support_strength = float(np.clip(grounded_structure.get("support_strength", 0.0), 0.0, 1.0))
    resistance_strength = float(np.clip(grounded_structure.get("resistance_strength", 0.0), 0.0, 1.0))
    breakout_strength = float(np.clip(grounded_structure.get("breakout_strength", 0.0), 0.0, 1.0))
    pullback_strength = float(np.clip(grounded_structure.get("pullback_strength", 0.0), 0.0, 1.0))
    structure_buy_pressure = float(np.clip(grounded_structure.get("buy_pressure", 0.0), 0.0, 1.0))
    structure_sell_pressure = float(np.clip(grounded_structure.get("sell_pressure", 0.0), 0.0, 1.0))
    structure_bias_direction = str(grounded_structure.get("structure_bias_direction", "HOLD")).upper()
    structure_bias_confidence = float(np.clip(grounded_structure.get("structure_bias_confidence", 0.0), 0.0, 1.0))

    consolidation_score = float(np.clip(
        0.24 * small_body_ratio
        + 0.16 * color_flip_rate
        + 0.14 * (1.0 - continuation_prob)
        + 0.12 * min(1.0, disagreement / 0.25)
        + 0.18 * max(current_box_consolidation, recent_box_consolidation)
        + 0.08 * path_clarity
        + 0.08 * (1.0 - box_sequence_agreement),
        0.0,
        1.0,
    ))
    if local_phase in {"with_trend_push", "continuation_base"} and continuation_prob >= 0.45:
        consolidation_score *= 0.60
    elif local_phase == "counter_trend_spike":
        consolidation_score *= 0.75
    if has_active_consolidation:
        consolidation_score = float(np.clip(max(consolidation_score, 0.52 + 0.25 * max(current_box_consolidation, recent_box_consolidation)), 0.0, 1.0))
    consolidation_streak = int(round(consolidation_score * max(0, min(len(recent_colors), 6)) + (2 if has_active_consolidation else 0)))
    consolidation_type = "tight" if consolidation_score >= 0.55 and disagreement <= 0.18 else (
        "loose" if consolidation_score >= 0.40 else "none"
    )
    consolidation_breakout_ready = bool(
        has_active_consolidation
        and projected_box_type == "impulse"
        and path_clarity >= 0.55
    )
    impulse_chain_ready = bool(
        current_box_type == "impulse"
        and projected_box_type == "impulse"
        and current_box_dir == direction
        and projected_direction == direction
        and continuation_prob >= max(0.54, reversal_prob + 0.22, fakeout_prob + 0.22)
        and path_clarity >= 0.68
        and box_sequence_agreement >= 0.45
        and current_box_confidence >= 0.74
        and current_box_maturity >= 0.55
        and body_pct >= 0.45
    )
    reversal_release_ready = bool(
        current_box_type == "reversal_base"
        and projected_box_type == "impulse"
        and projected_direction == current_box_dir
        and projected_box_confidence >= 0.54
        and projection_dominance >= 0.05
        and path_clarity >= 0.52
        and box_sequence_agreement >= 0.40
        and current_box_confidence >= 0.70
        and (
            continuation_prob >= fakeout_prob
            or str(swing_state.get("swing_phase", "")) == "counter_macro_reversal"
        )
    )
    structure_setup = (
        "consolidation_breakout"
        if consolidation_breakout_ready
        else ("impulse_chain" if impulse_chain_ready else ("reversal_release" if reversal_release_ready else "none"))
    )
    structure_trade_ready = bool(structure_setup != "none")

    return {
        "entry_type": entry_type,
        "direction": direction,
        "direction_probability": direction_probability,
        "macro_trend": macro_trend,
        "local_phase": local_phase,
        "candle_count_up": int(sum(1 for color in recent_colors if color == "green")),
        "candle_count_down": int(sum(1 for color in recent_colors if color == "red")),
        "consolidation_streak": consolidation_streak,
        "consolidation_type": consolidation_type,
        "consolidation_score": consolidation_score,
        "color_flip_rate": color_flip_rate,
        "small_body_ratio": small_body_ratio,
        "entry_candle": {
            "body_pct": body_pct,
            "upper_wick_pct": float(chart_geometry.get("upper_wick_pct", 0.0) or 0.0),
            "lower_wick_pct": float(chart_geometry.get("lower_wick_pct", 0.0) or 0.0),
            "color": entry_color,
        },
        "pre_entry_sequence": recent_colors[-8:],
        "implied_price_target": 0.0,
        "support_price": 0.0,
        "resistance_price": 0.0,
        "reversal_signal": reversal_signal,
        "continuation_signal": continuation_signal,
        "continuation_probability": continuation_prob,
        "reversal_probability": reversal_prob,
        "fakeout_probability": fakeout_prob,
        "implied_3min_move_pct": implied_move,
        "momentum_bias": momentum_bias,
        "box_sequence_agreement": box_sequence_agreement,
        "current_box_consolidation": current_box_consolidation,
        "recent_box_consolidation": recent_box_consolidation,
        "has_active_consolidation": has_active_consolidation,
        "path_clarity": path_clarity,
        "sequence_model": sequence_model,
        "sequence_bias_direction": sequence_bias_direction,
        "sequence_bias_confidence": sequence_bias_confidence,
        "sequence_buy_pressure": sequence_buy_pressure,
        "sequence_sell_pressure": sequence_sell_pressure,
        "continuation_readiness": continuation_readiness,
        "reversal_pressure": reversal_pressure,
        "history_coherence": history_coherence,
        "sequence_uncertainty": sequence_uncertainty,
        "projection_alignment": projection_alignment,
        "projection_bias_direction": projected_direction,
        "projection_bias_confidence": projection_bias_confidence,
        "projection_dominance": projection_dominance,
        "projection_explanation": projection_explanation,
        "structure_trade_ready": structure_trade_ready,
        "structure_setup": structure_setup,
        "projected_next_box": projected_next_box,
        "swing_state": swing_state,
        "grounded_confidence": grounded_confidence,
        "grounded_structure": grounded_structure,
        "support_strength": support_strength,
        "resistance_strength": resistance_strength,
        "breakout_strength": breakout_strength,
        "pullback_strength": pullback_strength,
        "structure_buy_pressure": structure_buy_pressure,
        "structure_sell_pressure": structure_sell_pressure,
        "structure_bias_direction": structure_bias_direction,
        "structure_bias_confidence": structure_bias_confidence,
        "grounded_objects": cast(list[dict[str, Any]], grounded_chart.get("objects", [])),
        "grounded_zones": cast(list[dict[str, Any]], grounded_chart.get("zones", [])),
        "style_signature": cast(dict[str, Any], grounded_chart.get("style_signature", {})),
        "artifact_score": artifact_score,
        "mcts": {
            "buy_prob": buy_prob,
            "sell_prob": sell_prob,
            "n_sims": int(len(cast(dict[str, Any], local_ensemble.get("models", {})))),
            "value": float(buy_prob - sell_prob),
        },
        "raw_description": (
            f"local_ensemble direction={direction} prob={direction_probability:.3f} "
            f"phase={local_phase} macro={macro_trend} champion={champion} "
            f"confirmer={confirmer} disagreement={disagreement:.3f} "
            f"consol={consolidation_score:.3f} cont={continuation_prob:.3f} "
            f"path={path_clarity:.3f} seq={sequence_bias_direction}:{sequence_bias_confidence:.3f} "
            f"struct={structure_bias_direction}:{structure_bias_confidence:.3f} setup={structure_setup} "
            f"projected={projected_box_type}:{projected_direction}:{projected_box_confidence:.3f} "
            f"swing={str(swing_state.get('summary', 'unknown'))}"
        ),
    }


def run_inference(
    file_path: str,
    annotation_text: str = "",
    overlay_mode: str = "history-plus-projection",
    min_conf_global: float = 0.42,
    min_conf_latest: float = 0.50,
    history_depth: int = 8,
    label_density: int = 10,
    projection_focus: float = 0.35,
    side_effect_free: bool = False,
    use_local_ensemble: bool | None = None,
) -> tuple[dict[str, Any], Image.Image | None, Any, Any]:
    cv_engine = _get_cv_engine()
    forecast_engine = _get_forecast_engine()
    gates_engine = _get_gates_engine()
    moe = _get_moe()
    ensemble = _get_ensemble()
    rl_engine = _get_rl_engine()
    personal = _get_personal()
    tta_manager = _get_tta_manager() if RUNTIME.enable_test_time_adaptation else None
    ood_detector = _get_ood_detector() if RUNTIME.enable_open_set_guard else None
    continual_learning = _get_continual_learning()
    local_ensemble_requested = bool(
        (
            bool(getattr(RUNTIME, "enable_local_ensemble", True))
            or bool(getattr(RUNTIME, "auto_model_council_on_inference", False))
        )
        if use_local_ensemble is None
        else use_local_ensemble
    )
    local_ensemble_runtime = (
        cast(Any, getattr(cv_engine, "ensemble_cv", None))
        if local_ensemble_requested and bool(getattr(RUNTIME, "enable_local_ensemble", True))
        else None
    )
    bank = _get_memory_bank()

    img_raw, meta = load_any_file_as_image(file_path)
    img_rgb = img_raw.convert("RGB")
    tta_summary: dict[str, Any]
    if RUNTIME.enable_test_time_adaptation and tta_manager is not None:
        tta_summary = dict(cast(Mapping[str, Any], tta_manager.select_view(img_rgb, cv_engine)))
        selected_image = tta_summary.get("selected_image")
        inference_img = selected_image if isinstance(selected_image, Image.Image) else img_rgb
    else:
        tta_summary = {
            "selected_view": "raw",
            "style_signature": {},
            "artifact_summary": {},
            "candidates": [],
        }
        inference_img = img_rgb

    detections: list[dict[str, Any]] = cv_engine.detect(inference_img)
    reasoning_trace: dict[str, Any] = cv_engine.build_reasoning_trace(detections, image_rgb=inference_img)
    chart_geometry, sequence_state = _extract_chart_structure(cv_engine, inference_img)
    sequence_state["sequence_model"] = _build_sequence_model_summary(
        sequence_state,
        chart_geometry,
        market_state=cast(dict[str, Any], reasoning_trace.get("market_state", {})),
    )
    grounded_chart: dict[str, Any]
    if RUNTIME.enable_grounded_chart_parsing:
        from phoenixguard.runtime.adaptive_runtime import build_grounded_chart

        grounded_chart = dict(
            cast(
                Mapping[str, Any],
                build_grounded_chart(
                    inference_img,
                    detections=detections,
                    chart_geometry=chart_geometry,
                    sequence_state=sequence_state,
                ),
            )
        )
    else:
        grounded_chart = {
            "grounded_confidence": 0.0,
            "objects": [],
            "zones": [],
            "style_signature": dict(cast(dict[str, Any], tta_summary.get("style_signature", {}))),
            "artifact_summary": dict(cast(dict[str, Any], tta_summary.get("artifact_summary", {}))),
        }
    pre_context_key = continual_learning.derive_context_key(
        cast(dict[str, float], grounded_chart.get("style_signature", {})),
        sequence_state=sequence_state,
    )
    adaptation_profile = (
        continual_learning.adapter_profile_for_context(pre_context_key)
        if RUNTIME.enable_replay_continual_learning
        else None
    )
    local_ensemble: dict[str, Any] = _neutral_local_ensemble_prediction("uninitialized")
    local_ensemble_source = "none"
    if local_ensemble_requested and bool(getattr(RUNTIME, "enable_local_ensemble", True)) and local_ensemble_runtime is None:
        try:
            if not side_effect_free:
                local_ensemble_runtime = _get_local_ensemble(block=False)
                if local_ensemble_runtime is not None:
                    cv_engine.ensemble_cv = local_ensemble_runtime
        except Exception as exc:
            if not side_effect_free:
                logger.warning("Local ensemble runtime unavailable during inference: %s", exc)
            local_ensemble_runtime = None

    if local_ensemble_runtime is None:
        warm_status = _local_ensemble_status()
        if local_ensemble_requested and not bool(getattr(RUNTIME, "enable_local_ensemble", True)):
            try:
                local_ensemble = _predict_with_model_council_daemon(
                    inference_img,
                    adaptation_profile=adaptation_profile,
                )
                local_ensemble_source = "daemon"
            except Exception as exc:
                reason = f"daemon_failed:{exc}"
                logger.warning("Model council daemon unavailable during inference: %s", exc)
                local_ensemble = _neutral_local_ensemble_prediction(reason)
        elif not local_ensemble_requested:
            reason = "lazy_tab_loading"
            local_ensemble = _neutral_local_ensemble_prediction(reason)
        elif not bool(getattr(RUNTIME, "enable_local_ensemble", True)):
            reason = f"disabled_by_profile:{str(getattr(RUNTIME, 'runtime_profile', 'FAST')).lower()}"
            local_ensemble = _neutral_local_ensemble_prediction(reason)
        else:
            if side_effect_free:
                reason = "missing_saved_bundles"
            elif warm_status == "warming_up":
                reason = "warming_up"
            elif warm_status.startswith("warmup_failed:"):
                reason = warm_status
            else:
                reason = "runtime_unavailable"
            local_ensemble = _neutral_local_ensemble_prediction(reason)
    else:
        local_ensemble = cast(
            dict[str, Any],
            local_ensemble_runtime.predict(inference_img, adaptation_profile=adaptation_profile),
        )
        local_ensemble_source = "inline"

    daemon_cached = bool(local_ensemble.pop("_daemon_cached", False))
    daemon_runtime_status = cast(dict[str, Any], local_ensemble.pop("_daemon_status", {}))
    if daemon_cached and local_ensemble_source == "daemon":
        local_ensemble_source = "daemon_cache"

    chart_state = _build_chart_state(
        detections=detections,
        local_ensemble=local_ensemble,
        reasoning_trace=reasoning_trace,
        chart_geometry=chart_geometry,
        sequence_state=sequence_state,
        grounded_chart=grounded_chart,
    )
    if local_ensemble_runtime is not None:
        local_ensemble = cast(
            dict[str, Any],
            local_ensemble_runtime.reroute_prediction(
                local_ensemble,
                routing_context=_build_local_ensemble_routing_context(
                    chart_state=chart_state,
                    sequence_state=sequence_state,
                    grounded_chart=grounded_chart,
                    reasoning_trace=reasoning_trace,
                ),
                adaptation_profile=adaptation_profile,
            ),
        )
        chart_state = _build_chart_state(
            detections=detections,
            local_ensemble=local_ensemble,
            reasoning_trace=reasoning_trace,
            chart_geometry=chart_geometry,
            sequence_state=sequence_state,
            grounded_chart=grounded_chart,
        )
    current_box = cast(dict[str, Any], sequence_state.get("current_box", {}))
    next_box_hypotheses = cast(list[dict[str, Any]], sequence_state.get("next_box_hypotheses", []))
    box_history = cast(list[dict[str, Any]], sequence_state.get("box_history", []))

    recall_results: list[Any] = []
    memory_episode_matches: list[dict[str, Any]] = []
    sequence_transition_probabilities: dict[str, float] = {}
    memory_direction = "HOLD"
    memory_top1_sim = 0.0
    memory_summary: dict[str, Any] = {
        "top_similarity": 0.0,
        "ambiguity": 0.0,
        "label_entropy": 0.0,
        "consensus_ratio": 0.0,
        "mixed_labels": False,
        "dominant_label": "HOLD",
        "recall_count": 0,
    }

    if bank is not None:
        from phoenixguard.memory.memory_features import (
            build_late_interaction_tokens,
            build_metric_profile,
            build_trajectory_signature,
        )

        query_embed = bank.embed_description(chart_state, image=inference_img)
        market_state = cast(dict[str, Any], reasoning_trace.get("market_state", {}))
        query_context: dict[str, Any] = {
            "late_interaction_tokens": build_late_interaction_tokens(
                chart_state,
                combined_embed=query_embed.tolist(),
                style_signature=cast(dict[str, float], grounded_chart.get("style_signature", {})),
                sequence_state=sequence_state,
                metric_profile=build_metric_profile(
                    chart_state,
                    sequence_state=sequence_state,
                    grounded_chart=grounded_chart,
                ),
            ),
            "trajectory_signature": build_trajectory_signature(chart_state, sequence_state=sequence_state),
            "style_signature": cast(dict[str, float], grounded_chart.get("style_signature", {})),
            "metric_profile": build_metric_profile(
                chart_state,
                sequence_state=sequence_state,
                grounded_chart=grounded_chart,
            ),
        }
        recall_results = cast(
            list[Any],
            bank.search(
                query_embed,
                top_k=5,
                macro_trend=str(market_state.get("macro_trend", "")),
                local_phase=str(market_state.get("local_phase", "")),
                query_context=query_context,
            ),
        )
        if recall_results:
            memory_top1_sim = float(getattr(recall_results[0], "similarity", 0.0) or 0.0)
            memory_direction = str(getattr(recall_results[0], "label", "HOLD")).upper()
            sequence_transition_probabilities = bank.summarize_transition_probabilities(recall_results)
            memory_episode_matches = bank.episode_summary(recall_results)
            memory_summary = _summarize_memory_ambiguity(recall_results)
            enriched_matches: list[dict[str, Any]] = []
            for idx, match in enumerate(memory_episode_matches):
                copied = dict(match)
                if not str(copied.get("episode_id", "")).strip():
                    copied["episode_id"] = f"episode_{idx + 1}"
                if not isinstance(copied.get("sequence_index"), int) or int(copied.get("sequence_index", 0)) <= 0:
                    copied["sequence_index"] = idx + 1
                enriched_matches.append(copied)
            memory_episode_matches = enriched_matches

    detections = _reconcile_memory_bias_detections(detections, memory_summary, memory_direction)
    fused_transition_probabilities = _fuse_transition_probabilities(
        reasoning_trace,
        sequence_transition_probabilities,
        sequence_state=sequence_state,
    )
    transition_summary = _build_transition_summary(fused_transition_probabilities)
    reasoning_trace = _update_reasoning_trace_with_fused_transitions(
        reasoning_trace,
        fused_transition_probabilities,
        memory_episode_matches,
    )
    sequence_state.update(
        {
            "continuation_probability": float(fused_transition_probabilities.get("continue", 0.25)),
            "pullback_probability": float(fused_transition_probabilities.get("pullback", 0.25)),
            "reversal_probability": float(fused_transition_probabilities.get("reversal_attempt", 0.25)),
            "fakeout_probability": float(fused_transition_probabilities.get("fakeout", 0.25)),
        }
    )
    sequence_state["next_box_hypotheses"] = _build_next_box_hypotheses(
        cast(list[dict[str, Any]], sequence_state.get("box_history", [])),
        sequence_state,
        chart_geometry,
        market_state=cast(dict[str, Any], reasoning_trace.get("market_state", {})),
        memory_summary=memory_summary,
        memory_episode_matches=memory_episode_matches,
    )
    next_box_hypotheses = cast(list[dict[str, Any]], sequence_state.get("next_box_hypotheses", []))
    sequence_state["primary_next_box"] = dict(next_box_hypotheses[0]) if next_box_hypotheses else {}
    sequence_state["path_clarity"] = float(np.clip(cast(dict[str, Any], sequence_state.get("primary_next_box", {})).get("path_clarity", sequence_state.get("path_clarity", 0.0)), 0.0, 1.0))
    sequence_state["sequence_model"] = _build_sequence_model_summary(
        sequence_state,
        chart_geometry,
        market_state=cast(dict[str, Any], reasoning_trace.get("market_state", {})),
    )
    reasoning_trace["sequence_state"] = sequence_state
    chart_state = _build_chart_state(
        detections=detections,
        local_ensemble=local_ensemble,
        reasoning_trace=reasoning_trace,
        chart_geometry=chart_geometry,
        sequence_state=sequence_state,
        grounded_chart=grounded_chart,
    )
    council_sequence_summary = _build_council_sequence_summary(local_ensemble)
    chart_state["council_sequence_summary"] = council_sequence_summary
    enriched_next_box_hypotheses = _enrich_next_box_hypotheses_with_projected_candles(
        next_box_hypotheses,
        detections=detections,
        chart_state=chart_state,
        sequence_state=sequence_state,
        local_ensemble=local_ensemble,
    )
    sequence_state["next_box_hypotheses"] = enriched_next_box_hypotheses
    next_box_hypotheses = cast(list[dict[str, Any]], sequence_state.get("next_box_hypotheses", []))
    sequence_state["primary_next_box"] = dict(next_box_hypotheses[0]) if next_box_hypotheses else {}
    chart_state["projected_next_box"] = cast(dict[str, Any], sequence_state.get("primary_next_box", {}))
    chart_state["council_sequence_summary"] = council_sequence_summary
    current_box = cast(dict[str, Any], sequence_state.get("current_box", {}))
    box_history = cast(list[dict[str, Any]], sequence_state.get("box_history", []))
    projection_view = cast(dict[str, Any], chart_state.get("projected_next_box", {}))
    context_key = continual_learning.derive_context_key(
        cast(dict[str, float], grounded_chart.get("style_signature", {})),
        chart_state=chart_state,
        sequence_state=sequence_state,
    )
    context_descriptor = (
        f"{context_key}|view={str(tta_summary.get('selected_view', 'raw'))}|"
        f"direction={str(chart_state.get('direction', 'HOLD')).upper()}|"
        f"structure={str(chart_state.get('structure_setup', 'none'))}"
    )
    memory_reference: dict[str, Any] = (
        dict(cast(Mapping[str, Any], bank.reference_style_profile()))
        if bank is not None and hasattr(bank, "reference_style_profile")
        else {"mean": {}, "std": {}, "count": 0}
    )
    ood_summary: dict[str, Any] = (
        dict(
            cast(
                Mapping[str, Any],
                ood_detector.assess(
                    style_signature=cast(dict[str, float], grounded_chart.get("style_signature", {})),
                    artifact_summary=cast(dict[str, float], grounded_chart.get("artifact_summary", {})),
                    chart_geometry=chart_geometry,
                    sequence_state=sequence_state,
                    local_ensemble=local_ensemble,
                    memory_reference=memory_reference,
                    memory_summary=memory_summary,
                ),
            )
        )
        if RUNTIME.enable_open_set_guard and ood_detector is not None
        else {
            "ood_score": 0.0,
            "style_novelty": 0.0,
            "artifact_score": 0.0,
            "parse_penalty": 0.0,
            "structure_penalty": 0.0,
            "disagreement": 0.0,
            "entropy": 0.0,
            "flags": [],
            "force_hold": False,
        }
    )

    latest_signal_state = _extract_latest_signal_state(detections)
    latest_parse_quality = float(latest_signal_state["latest_parse_quality"])
    latest_candle_confidence = float(latest_signal_state["latest_candle_confidence"])
    latest_candle_direction = str(latest_signal_state["latest_candle_direction"])
    current_box_direction = str(current_box.get("direction", "HOLD")).upper()
    projected_box_view = cast(dict[str, Any], chart_state.get("projected_next_box", {}))
    projected_direction = str(projected_box_view.get("direction", current_box_direction)).upper()
    projected_confidence = float(np.clip(projected_box_view.get("confidence", 0.0), 0.0, 1.0))
    structure_trade_ready = bool(chart_state.get("structure_trade_ready", False))
    structure_direction = (
        projected_direction
        if structure_trade_ready or projected_confidence >= 0.58
        else current_box_direction
    )
    detections = _apply_memory_ambiguity_to_detections(detections, memory_summary)
    detections = _cap_directional_detections(detections, latest_candle_confidence)
    detections = _apply_parse_quality_cap_to_detections(
        detections,
        latest_candle_confidence,
        chart_geometry,
        sequence_state,
    )
    latest_parse_quality = _cap_parse_quality_value(
        latest_parse_quality,
        latest_candle_confidence,
        chart_geometry,
        sequence_state,
    )
    latest_signal_state = _extract_latest_signal_state(detections)
    latest_parse_quality = float(
        min(
            latest_parse_quality,
            float(latest_signal_state["latest_parse_quality"]),
        )
    )
    latest_candle_confidence = float(latest_signal_state["latest_candle_confidence"])
    latest_candle_direction = str(latest_signal_state["latest_candle_direction"])
    latest_candle_conflict = (
        memory_direction in ("BUY", "SELL")
        and latest_candle_direction in ("BUY", "SELL")
        and memory_direction != latest_candle_direction
    )
    geometry_reference_direction = structure_direction if structure_direction in ("BUY", "SELL") else latest_candle_direction
    geometry_conflict = (
        memory_direction in ("BUY", "SELL")
        and geometry_reference_direction in ("BUY", "SELL")
        and memory_direction != geometry_reference_direction
    )
    hold_veto_relaxation = _should_relax_hold_veto(
        local_ensemble=local_ensemble,
        memory_direction=memory_direction,
        memory_summary=memory_summary,
        fused_transition_probabilities=fused_transition_probabilities,
        latest_candle_confidence=latest_candle_confidence,
        latest_candle_direction=latest_candle_direction,
        reasoning_trace=reasoning_trace,
        chart_state=chart_state,
    )
    parse_threshold = float(os.getenv("PHOENIXGUARD_LATEST_PARSE_MIN_CONF", "0.20") or 0.20)
    strict_cv_fail_closed = bool(
        (latest_parse_quality < parse_threshold and not hold_veto_relaxation)
        or bool(ood_summary.get("force_hold", False))
    )

    forecast: Forecast3MOutput = forecast_engine.forecast_3m(
        chart_state,
        quantiles=RUNTIME.quantiles,
        detections=detections,
        memory_similarity=memory_top1_sim,
        memory_direction=memory_direction,
        transition_summary=fused_transition_probabilities,
        memory_summary=memory_summary,
    )
    if hold_veto_relaxation and bool(forecast.get("force_hold", False)):
        interval = float(forecast.get("interval", 0.0) or 0.0)
        hold_threshold_used = float(forecast.get("hold_threshold_used", getattr(forecast_engine, "max_interval_pct", 0.40)) or getattr(forecast_engine, "max_interval_pct", 0.40))
        if interval <= hold_threshold_used * 1.10:
            forecast["force_hold"] = False
            forecast["force_hold_relaxed"] = True

    if side_effect_free:
        style_vec = np.asarray(personal.style_vector, dtype=np.float32).copy()
    else:
        style_vec = personal.update_style("", annotation_text)
    if RUNTIME.enable_fast_personalization:
        style_vec, personalization_context = personal.adapt_style_for_context(
            style_vec,
            context_key,
            context_descriptor,
        )
    else:
        personalization_context: dict[str, Any] = {
            "context_key": context_key,
            "context_descriptor": context_descriptor,
            "applied": False,
            "usage": 0.0,
            "scale": 0.0,
        }
    fused = fused_feature_vector(detections, forecast, style_vec)

    base_probs = _ensemble_base_probs(
        local_ensemble,
        chart_state=chart_state,
        memory_summary=memory_summary,
    )
    ensemble_view = cast(dict[str, Any], local_ensemble.get("ensemble", {}))
    ensemble_conf = float(ensemble_view.get("confidence", 0.5) or 0.5)
    ensemble_disagreement = float(ensemble_view.get("disagreement", 0.0) or 0.0)
    cv_quality = float(
        np.clip(
            0.34 * ensemble_conf
            + 0.24 * latest_parse_quality
            + 0.20 * latest_candle_confidence
            + 0.22 * (1.0 - ensemble_disagreement),
            0.0,
            1.0,
        )
    )
    ood_penalty = float(np.clip(ood_summary.get("ood_score", 0.0), 0.0, 1.0))
    cv_quality = float(np.clip(cv_quality * (1.0 - 0.35 * ood_penalty), 0.0, 1.0))
    structure_consistency = float(
        np.clip(
            0.46 * (1.0 - ensemble_disagreement)
            + 0.28 * latest_parse_quality
            + 0.16 * float(np.clip(sequence_state.get("box_sequence_agreement", 0.0), 0.0, 1.0))
            + 0.10 * float(np.clip(chart_state.get("path_clarity", 0.0), 0.0, 1.0))
            + 0.26 * (0.0 if geometry_conflict else 1.0),
            0.0,
            1.0,
        )
    )
    structure_consistency = float(np.clip(structure_consistency * (1.0 - 0.30 * ood_penalty), 0.0, 1.0))
    sequence_clarity = float(
        np.clip(
            0.52 * float(np.clip(sequence_state.get("box_sequence_agreement", 0.0), 0.0, 1.0))
            + 0.48 * float(np.clip(chart_state.get("path_clarity", 0.0), 0.0, 1.0)),
            0.0,
            1.0,
        )
    )
    sequence_clarity = float(np.clip(sequence_clarity * (1.0 - 0.20 * ood_penalty), 0.0, 1.0))
    consolidation_quality = float(
        np.clip(
            0.65 * float(np.clip(chart_state.get("consolidation_score", 0.0), 0.0, 1.0))
            + 0.35 * float(1.0 if chart_state.get("has_active_consolidation", False) else 0.0),
            0.0,
            1.0,
        )
    )
    memory_novelty = float(np.clip(max(1.0 - memory_top1_sim, float(ood_summary.get("style_novelty", 0.0))), 0.0, 1.0))
    module_reliability = {
        "cv_quality": cv_quality,
        "structure_consistency": structure_consistency,
        "sequence_clarity": sequence_clarity,
        "consolidation_quality": consolidation_quality,
        "memory_novelty": memory_novelty,
    }
    dominant_memory_direction = str(memory_summary.get("dominant_label", memory_direction)).upper()
    rl_result = rl_engine.infer(
        fused,
        memory_recall_top1_sim=memory_top1_sim,
        memory_recall_direction=dominant_memory_direction,
        prior_probs=base_probs,
        module_reliability=module_reliability,
    )
    rl_probs = dict(rl_result.probs)
    chart_state["mcts"] = {
        "buy_prob": float(rl_probs.get("BUY", 1.0 / 3.0)),
        "sell_prob": float(rl_probs.get("SELL", 1.0 / 3.0)),
        "hold_prob": float(rl_probs.get("HOLD", 1.0 / 3.0)),
        "n_sims": int(RUNTIME.mcts_sims),
        "value": float(rl_result.mcts_value),
        "blend_weight": float(rl_result.blend_weight),
        "policy_action": str(rl_result.policy_action),
        "feedback_count": int(rl_result.feedback_count),
        "online_update_count": int(rl_result.online_update_count),
    }
    chart_state["rl_policy"] = {
        "probs": rl_probs,
        "prior_probs": dict(rl_result.prior_probs),
        "policy_probs": dict(rl_result.policy_probs),
        "blend_weight": float(rl_result.blend_weight),
        "boost_applied": bool(rl_result.boost_applied),
        "boosted_action": str(rl_result.boosted_action),
        "policy_action": str(rl_result.policy_action),
        "feedback_count": int(rl_result.feedback_count),
        "online_update_count": int(rl_result.online_update_count),
    }
    explanation_text = str(chart_state.get("raw_description", ""))
    _, cleaned_expl = indicator_regex_filter(explanation_text)
    extracted_prices = extract_price_floats(annotation_text)
    if len(extracted_prices) < 4:
        extracted_prices = _derive_proxy_price_series(sequence_state)
    sub_signals = [(float(detection["confidence"]), str(detection["pattern"])) for detection in detections]
    module_logits = np.array(
        [float(rl_probs["BUY"]), float(rl_probs["SELL"]), float(rl_probs["HOLD"])],
        dtype=np.float32,
    )
    gate_outputs = gates_engine.run_all(
        probs=rl_probs,
        q05=float(forecast["q05"]),
        q95=float(forecast["q95"]),
        momentum_bias=str(chart_state.get("momentum_bias", "neutral")),
        explanation=cleaned_expl,
        sub_signals=sub_signals,
        module_logits=module_logits,
        recent_feedback_count=personal.recent_feedback_count(),
        queue_depth=0,
        gpu_mem_ok=torch.cuda.is_available(),
        has_dashboard=True,
        risk_ethical_ok=True,
        chart_state=chart_state,
        prices=extracted_prices,
        direction_prob=float(chart_state.get("direction_probability", 0.5) or 0.5),
        mcts=cast(dict[str, Any], chart_state.get("mcts", {})),
        memory_sim=memory_top1_sim,
        latest_candle_confidence=latest_candle_confidence,
        geometry_conflict=geometry_conflict,
    )

    feat_pad = np.pad(fused, (0, max(0, 16 - fused.size)), mode="constant")[:16].astype(np.float32)
    route_w = np.asarray(moe.route_weights(feat_pad), dtype=np.float32)
    for i, gate in enumerate(gate_outputs):
        gate.detail["moe_weight"] = float(route_w[min(i, len(route_w) - 1)])

    support_gate_outputs = gates_engine.run_support_gates(
        chart_state=chart_state,
        market_state=cast(dict[str, Any], reasoning_trace.get("market_state", {})),
        forecast=cast(dict[str, Any], forecast),
        transition_summary=dict(transition_summary),
        memory_summary=memory_summary,
        ood_summary=ood_summary,
        memory_similarity=memory_top1_sim,
        memory_label=str(memory_summary.get("dominant_label", memory_direction)).upper(),
        latest_candle_confidence=latest_candle_confidence,
        geometry_conflict=geometry_conflict,
        reliability=cv_quality,
        use_execution_permission=RUNTIME.use_execution_permission,
        use_macro_local_alignment=RUNTIME.use_macro_local_alignment_gate,
        use_opposition_strength=RUNTIME.use_opposition_strength_gate,
    )

    decision: dict[str, Any] = ensemble.infer(
        rl_probs,
        forecast,
        gate_outputs,
        memory_bank_similarity=memory_top1_sim,
        force_hold=bool(
            (forecast.get("force_hold", False) and not hold_veto_relaxation)
            or strict_cv_fail_closed
            or bool(ood_summary.get("force_hold", False))
        ),
        module_reliability=module_reliability,
        memory_summary=memory_summary,
        latest_candle_confidence=latest_candle_confidence,
        transition_summary=transition_summary,
        support_gate_outputs=support_gate_outputs,
    )

    if reasoning_trace:
        reasoning_trace["final_trade_bias"] = str(decision.get("trade_bias", decision.get("action", "HOLD")))
        market_state = dict(cast(dict[str, Any], reasoning_trace.get("market_state", {})))
        if market_state:
            best_transition_key = max(
                fused_transition_probabilities.items(),
                key=lambda item: float(item[1]),
            )[0]
            market_state["intent_next"] = best_transition_key
            reasoning_trace["market_state"] = market_state
        memory_weight_value = float(decision.get("memory_weight", 0.0) or 0.0)
        projection_conf = float(chart_state.get("projection_bias_confidence", projection_view.get("confidence", 0.0)) or 0.0)
        projection_gap = float(chart_state.get("projection_dominance", projection_view.get("dominance_gap", 0.0)) or 0.0)
        swing_state = cast(dict[str, Any], chart_state.get("swing_state", {}))
        reasoning_trace["explanation"] = (
            f"ensemble={ensemble_view.get('predicted_label', 'HOLD')}({ensemble_conf:.2f}) "
            f"rl={str(rl_result.policy_action).upper()} blend={float(rl_result.blend_weight):.2f} "
            f"macro={market_state.get('macro_trend', 'unknown') if market_state else 'unknown'} "
            f"local={market_state.get('local_phase', 'unknown') if market_state else 'unknown'} "
            f"projection={str(chart_state.get('projection_bias_direction', 'HOLD')).upper()}({projection_conf:.2f}) "
            f"dom={projection_gap:.2f} "
            f"swing={str(swing_state.get('swing_phase', 'unknown'))} "
            f"continue={fused_transition_probabilities.get('continue', 0.0):.2f} "
            f"reversal={fused_transition_probabilities.get('reversal_attempt', 0.0):.2f} "
            f"memory_w={memory_weight_value:.2f}"
        )

    if not side_effect_free and recall_results and not RUNTIME.pause_rl_updates:
        rl_engine.record_recall_and_maybe_update()

    action = str(decision["action"])
    probs = cast(dict[str, float], decision["calibrated_probs"])

    result: dict[str, Any] = {
        "timestamp": utc_now_iso(),
        "meta": meta,
        "action": action,
        "trade_bias": str(decision.get("trade_bias", action)),
        "decision_state": str(decision.get("decision_state", "UNCERTAIN")),
        "execution_permission": str(decision.get("execution_permission", "WAIT_FOR_CONFIRMATION")),
        "confidence": float(decision["confidence"]),
        "probabilities": probs,
        "expected_3min_move_pct": float(decision["expected_move_pct"]),
        "quantile_range": cast(list[float], decision["quantile_range"]),
        "position_size_pct": float(decision["position_size_pct"]),
        "gates_passing": int(decision["gates_passing"]),
        "consensus_ok": bool(decision["consensus_ok"]),
        "confidence_ok": bool(decision.get("confidence_ok", False)),
        "gates_ok": bool(decision.get("gates_ok", False)),
        "memory_ok": bool(decision.get("memory_ok", False)),
        "interval_ok": bool(decision.get("interval_ok", False)),
        "projection_bias_ready": bool(decision.get("projection_bias_ready", False)),
        "gate_scores": cast(dict[str, float], decision["gate_scores"]),
        "support_gate_scores": cast(dict[str, float], decision.get("support_gate_scores", {})),
        "shap_contributions": cast(dict[str, float], decision["shap_contributions"]),
        "memory_similarity": memory_top1_sim,
        "memory_direction": memory_direction,
        "memory_recall_count": len(recall_results),
        "memory_ambiguity_summary": memory_summary,
        "memory_threshold_used": float(decision.get("memory_threshold_used", 0.0) or 0.0),
        "latest_parse_quality": latest_parse_quality,
        "latest_candle_confidence": latest_candle_confidence,
        "latest_candle_conflict": latest_candle_conflict,
        "geometry_reference_direction": geometry_reference_direction,
        "geometry_conflict": geometry_conflict,
        "strict_cv_fail_closed": strict_cv_fail_closed,
        "projection_support": bool(decision.get("projection_support", False)),
        "support_gates_ok": bool(decision.get("support_gates_ok", True)),
        "execution_guard_ok": bool(decision.get("execution_guard_ok", True)),
        "opposition_alert": bool(decision.get("opposition_alert", False)),
        "ad_indicator": float(decision["ad_indicator"]),
        "poly_slope": float(decision["poly_slope"]),
        "forecast_debug": dict(forecast),
        "module_reliability": cast(dict[str, float], decision.get("module_reliability", {})),
        "cv_reasoning_trace": reasoning_trace,
        "sequence_transition_probabilities": fused_transition_probabilities,
        "transition_summary": dict(transition_summary),
        "memory_effective_weight": float(decision.get("memory_weight", 0.0) or 0.0),
        "branch_weights": cast(dict[str, float], decision.get("branch_weights", {})),
        "gate_details": [
            {
                "name": str(gate.name),
                "score": float(gate.score),
                "pass_fail": bool(gate.pass_fail),
                "detail": dict(gate.detail),
            }
            for gate in gate_outputs
        ],
        "support_gate_details": [
            {
                "name": str(gate.name),
                "score": float(gate.score),
                "pass_fail": bool(gate.pass_fail),
                "detail": dict(gate.detail),
            }
            for gate in support_gate_outputs
        ],
        "memory_episode_matches": memory_episode_matches,
        "test_time_adaptation": {
            "selected_view": str(tta_summary.get("selected_view", "raw")),
            "candidates": cast(list[dict[str, Any]], tta_summary.get("candidates", [])),
        },
        "grounded_chart": grounded_chart,
        "ood_summary": ood_summary,
        "personalization_context": personalization_context,
        "chart_geometry": chart_geometry,
        "sequence_state": sequence_state,
        "box_history": box_history,
        "current_box": current_box,
        "next_box_hypotheses": next_box_hypotheses,
        "chart_state": chart_state,
        "rl_policy": {
            "probs": rl_probs,
            "prior_probs": dict(rl_result.prior_probs),
            "policy_probs": dict(rl_result.policy_probs),
            "blend_weight": float(rl_result.blend_weight),
            "boost_applied": bool(rl_result.boost_applied),
            "boosted_action": str(rl_result.boosted_action),
            "policy_action": str(rl_result.policy_action),
            "mcts_value": float(rl_result.mcts_value),
            "feedback_count": int(rl_result.feedback_count),
            "online_update_count": int(rl_result.online_update_count),
        },
        "projection": {
            "direction": str(chart_state.get("projection_bias_direction", projection_view.get("direction", "HOLD"))).upper(),
            "box_type": str(projection_view.get("box_type", "balance")),
            "confidence": float(np.clip(chart_state.get("projection_bias_confidence", projection_view.get("confidence", 0.0)), 0.0, 1.0)),
            "dominance": float(np.clip(chart_state.get("projection_dominance", projection_view.get("dominance_gap", 0.0)), 0.0, 1.0)),
            "structure_setup": str(chart_state.get("structure_setup", "none")),
            "explanation": str(chart_state.get("projection_explanation", projection_view.get("explanation", ""))),
            "swing_state": cast(dict[str, Any], chart_state.get("swing_state", {})),
            "next_box": projection_view,
            "projected_candles": cast(list[dict[str, Any]], projection_view.get("projected_candles", [])),
        },
        "local_ensemble": local_ensemble,
        "council_sequence_summary": council_sequence_summary,
        "projected_candle_candidates": cast(list[dict[str, Any]], projection_view.get("projected_candles", [])),
        "model_council": {
            "requested": bool(local_ensemble_requested),
            "loaded": bool(cast(dict[str, Any], local_ensemble.get("models", {}))),
            "source": local_ensemble_source,
            "status": (
                "cached"
                if daemon_cached
                else str(cast(dict[str, Any], cast(dict[str, Any], local_ensemble.get("ensemble", {})).get("failed_models", {})).get("runtime", "ready"))
            ),
            "cache_entries": int(daemon_runtime_status.get("cache_entries", 0) or 0),
        },
        "analysis_profile": "model_council_refined" if bool(local_ensemble_requested) else str(getattr(RUNTIME, "runtime_profile", "FAST")).lower(),
        "explanation": str(reasoning_trace.get("explanation", chart_state.get("raw_description", ""))),
        "detections": detections,
    }
    if not side_effect_free and RUNTIME.enable_replay_continual_learning:
        continual_learning.record_inference_context(
            image_hash=str(meta.get("sha256", "")),
            context_key=context_key,
            context_descriptor=context_descriptor,
            local_ensemble=local_ensemble,
            predicted_action=action,
            confidence=float(decision["confidence"]),
            style_signature=cast(dict[str, float], grounded_chart.get("style_signature", {})),
            ood_summary=ood_summary,
            source_path=str(file_path),
            selected_view=str(tta_summary.get("selected_view", "raw")),
            snapshot_image=inference_img.copy(),
        )
    if not side_effect_free and not RUNTIME.pause_rl_updates:
        rl_engine.record_inference_context(
            image_hash=str(meta.get("sha256", "")),
            state_vec=fused,
            prior_probs=base_probs,
            policy_result=rl_result,
            predicted_action=action,
            memory_recall_top1_sim=memory_top1_sim,
            memory_recall_direction=dominant_memory_direction,
            module_reliability=module_reliability,
        )
    result = _apply_zone_memory_to_result(result)
    interpret_module = importlib.import_module("phoenixguard.interpreter")
    interpret = cast(Callable[[Mapping[str, Any]], dict[str, Any]], getattr(interpret_module, "interpret"))
    interpreter_fusion = _build_interpreter_fusion_payload(result)
    result["interpreter_fusion"] = interpreter_fusion
    result["interpreter"] = interpret(interpreter_fusion)

    overlay = _build_overlay_image(
        img_rgb,
        result,
        overlay_mode=overlay_mode,
        min_conf_global=min_conf_global,
        min_conf_latest=min_conf_latest,
        history_limit=int(np.clip(int(history_depth), 1, 24)),
        label_budget=int(np.clip(int(label_density), 2, 24)),
        projection_confidence_floor=float(np.clip(float(projection_focus), 0.0, 0.95)),
    )

    append_hash_chain(RUNTIME.logs_dir / SECURITY.log_hash_chain_file, result)

    gauge = _build_decision_gauge_from_result(result)
    skill_fig = _build_skill_figure(personal, result)

    del fused
    _maybe_run_post_inference_cleanup()
    return result, overlay, gauge, skill_fig


# ------------------------------------------------------------------
# Human-readable summary
# ------------------------------------------------------------------

def human_readable_summary(result: dict[str, Any]) -> str:
    a = result["action"]
    conf = result["confidence"] * 100.0
    mv = result["expected_3min_move_pct"]
    ql, qh = result["quantile_range"]
    ps = result["position_size_pct"]
    expl = result.get("explanation", "No explanation produced.")
    mem_sim = result.get("memory_similarity", 0.0)
    mem_dir = result.get("memory_direction", "N/A")
    g_ok = result.get("gates_passing", 0)
    ad = result.get("ad_indicator", 0.0)
    consensus = "YES" if result.get("consensus_ok") else "NO"
    decision_state = str(result.get("decision_state", "UNCERTAIN")).upper()
    execution_permission = str(result.get("execution_permission", "WAIT_FOR_CONFIRMATION")).upper()
    trade_bias = str(result.get("trade_bias", a)).upper()
    projection = cast(dict[str, Any], result.get("projection", {}))
    multi_timeframe = cast(dict[str, Any], result.get("multi_timeframe", {}))
    interpreter = cast(dict[str, Any], result.get("interpreter", {}))
    interpreter_human = str(interpreter.get("human", "")).strip()
    projection_line = "Projection: unavailable"
    if projection:
        projection_line = (
            f"Projection: {str(projection.get('direction', 'HOLD')).upper()} "
            f"{str(projection.get('box_type', 'balance'))} "
            f"conf={float(projection.get('confidence', 0.0) or 0.0):.2f} "
            f"dom={float(projection.get('dominance', 0.0) or 0.0):.2f}"
        )
    mtf_line = ""
    if multi_timeframe:
        mtf_line = (
            "MTF Gate: "
            f"{str(multi_timeframe.get('gate_state', 'watch')).upper()} "
            f"(entry_allowed={'YES' if bool(multi_timeframe.get('entry_allowed', False)) else 'NO'})\n"
            f"MTF Summary: {str(multi_timeframe.get('summary', ''))}\n"
        )
    return (
        f"Signal: {a}\n"
        f"Decision State: {decision_state}  [Execution: {execution_permission}]  [Trade Bias: {trade_bias}]\n"
        f"Conformal Probability: {conf:.2f}%  [Consensus: {consensus}]\n"
        f"Expected 3m Move: {mv:+.3f}% (q05={ql:+.3f}, q95={qh:+.3f})\n"
        f"{projection_line}\n"
        f"{mtf_line}"
        f"Suggested Position Size: {ps:.2f}% of equity\n"
        f"Gates Passing: {g_ok}/12\n"
        f"Memory Recall: sim={mem_sim:.3f}  recalled_dir={mem_dir}\n"
        f"A/D Indicator: {ad:+.3f}\n\n"
        f"Reasoning:\n{expl}"
        + (f"\n\nInterpreter:\n{interpreter_human}" if interpreter_human else "")
    )


UI_CSS = """
:root {
  --pg-bg: #081019;
  --pg-bg-soft: #101a24;
  --pg-panel: rgba(10, 18, 26, 0.84);
  --pg-panel-strong: rgba(7, 13, 20, 0.92);
  --pg-panel-elevated: rgba(12, 21, 31, 0.96);
  --pg-stroke: rgba(135, 159, 184, 0.18);
  --pg-stroke-strong: rgba(191, 208, 221, 0.16);
  --pg-text: #f5f0e6;
  --pg-muted: #98a7b4;
  --pg-muted-strong: #c3d1da;
  --pg-amber: #d8a55b;
  --pg-teal: #62c9b4;
  --pg-buy: #48c679;
  --pg-sell: #e67b6f;
  --pg-hold: #7e8ea1;
  --pg-shadow: 0 18px 60px rgba(0, 0, 0, 0.26);
}
html, body {
  background: #081019;
}
html {
  scroll-behavior: smooth;
}
.gradio-container {
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(circle at top left, rgba(98, 201, 180, 0.14), transparent 26%),
    radial-gradient(circle at top right, rgba(216, 165, 91, 0.13), transparent 22%),
    radial-gradient(circle at 50% -12%, rgba(77, 133, 219, 0.12), transparent 34%),
    linear-gradient(180deg, #071017 0%, #0b131c 38%, #101a24 100%);
  color: var(--pg-text);
  font-family: "Space Grotesk", "IBM Plex Sans", "Segoe UI", sans-serif;
  max-width: 1640px !important;
  padding: 16px !important;
}
.gradio-container::before {
  content: "";
  position: fixed;
  inset: -12% -10% auto -10%;
  height: 58vh;
  background:
    radial-gradient(circle at 20% 26%, rgba(98, 201, 180, 0.18), transparent 32%),
    radial-gradient(circle at 76% 14%, rgba(216, 165, 91, 0.16), transparent 28%),
    radial-gradient(circle at 50% 18%, rgba(84, 138, 220, 0.12), transparent 24%);
  filter: blur(42px);
  opacity: 0.95;
  pointer-events: none;
  z-index: 0;
  animation: pgDrift 18s ease-in-out infinite alternate;
}
.gradio-container::after {
  content: "";
  position: fixed;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.022) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.02) 1px, transparent 1px);
  background-size: 118px 118px;
  opacity: 0.2;
  pointer-events: none;
  z-index: 0;
  mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.84), transparent 90%);
}
.gradio-container > * {
  position: relative;
  z-index: 1;
}
@keyframes pgDrift {
  from {
    transform: translate3d(-1.5%, 0, 0) scale(1);
  }
  to {
    transform: translate3d(1.5%, 2.5%, 0) scale(1.04);
  }
}
.pg-reveal {
  opacity: 0;
  transform: translateY(18px) scale(0.99);
  transition:
    opacity 0.55s ease,
    transform 0.7s cubic-bezier(0.22, 1, 0.36, 1);
}
.pg-reveal.is-visible {
  opacity: 1;
  transform: none;
}
.pg-hero {
  position: relative;
  border: 1px solid var(--pg-stroke);
  border-radius: 30px;
  padding: 30px;
  background:
    linear-gradient(135deg, rgba(8, 16, 24, 0.98), rgba(13, 22, 32, 0.92)),
    radial-gradient(circle at top right, rgba(98, 201, 180, 0.14), transparent 40%);
  box-shadow: var(--pg-shadow);
  margin-bottom: 16px;
  overflow: hidden;
}
.pg-hero::before {
  content: "";
  position: absolute;
  inset: 0;
  background:
    radial-gradient(circle at 18% 18%, rgba(98, 201, 180, 0.14), transparent 30%),
    radial-gradient(circle at 82% 14%, rgba(216, 165, 91, 0.12), transparent 28%);
  opacity: 0.95;
  pointer-events: none;
}
.pg-hero::after {
  content: "";
  position: absolute;
  inset: 18px;
  border-radius: 24px;
  border: 1px solid rgba(255, 255, 255, 0.06);
  pointer-events: none;
}
.pg-kicker {
  color: var(--pg-amber);
  text-transform: uppercase;
  letter-spacing: 0.16em;
  font-size: 11px;
  font-weight: 700;
}
.pg-hero-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.28fr) minmax(320px, 0.98fr);
  gap: 24px;
  align-items: center;
}
.pg-hero-copy,
.pg-hero-visual {
  position: relative;
  z-index: 1;
}
.pg-hero-brand {
  margin-top: 10px;
  font-size: clamp(2rem, 4vw, 3.1rem);
  line-height: 0.95;
  letter-spacing: -0.05em;
  font-weight: 800;
}
.pg-hero h1 {
  margin: 14px 0 10px 0;
  font-size: clamp(1.12rem, 2vw, 1.52rem);
  line-height: 1.22;
  font-weight: 600;
  color: var(--pg-muted-strong);
  max-width: 26ch;
}
.pg-hero p {
  margin: 0;
  color: var(--pg-muted);
  font-size: 14px;
  line-height: 1.7;
  max-width: 62ch;
}
.pg-hero-actions,
.pg-inline-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}
.pg-inline-button {
  appearance: none;
  border: 1px solid rgba(129, 159, 184, 0.24);
  border-radius: 999px;
  padding: 10px 14px;
  background: rgba(255, 255, 255, 0.05);
  color: var(--pg-text);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition:
    transform 0.18s ease,
    background 0.18s ease,
    border-color 0.18s ease,
    box-shadow 0.18s ease;
}
.pg-inline-button:hover,
.pg-inline-button:focus-visible {
  transform: translateY(-1px);
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(98, 201, 180, 0.32);
  box-shadow: 0 12px 24px rgba(0, 0, 0, 0.18);
  outline: none;
}
.pg-inline-button[data-tone="secondary"] {
  background: rgba(255, 255, 255, 0.03);
  border-color: rgba(150, 166, 179, 0.22);
  color: var(--pg-muted-strong);
}
.pg-vision-stage {
  position: relative;
  min-height: 312px;
  border-radius: 26px;
  border: 1px solid rgba(154, 173, 193, 0.16);
  padding: 18px;
  overflow: hidden;
  background:
    linear-gradient(160deg, rgba(15, 26, 37, 0.94), rgba(8, 15, 22, 0.96)),
    radial-gradient(circle at top right, rgba(98, 201, 180, 0.12), transparent 36%);
}
.pg-vision-stage::before {
  content: "";
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.028) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.024) 1px, transparent 1px);
  background-size: 56px 56px;
  opacity: 0.22;
}
.pg-vision-stage::after {
  content: "";
  position: absolute;
  inset: 12% 10%;
  border-radius: 999px;
  border: 1px solid rgba(98, 201, 180, 0.18);
  box-shadow:
    0 0 0 34px rgba(98, 201, 180, 0.04),
    0 0 0 92px rgba(216, 165, 91, 0.03);
  opacity: 0.7;
}
.pg-vision-core {
  position: relative;
  z-index: 1;
  display: grid;
  place-items: center;
  min-height: 132px;
  border-radius: 22px;
  border: 1px solid rgba(164, 182, 201, 0.16);
  background: linear-gradient(145deg, rgba(15, 26, 37, 0.74), rgba(8, 16, 24, 0.88));
  backdrop-filter: blur(14px);
  text-align: center;
}
.pg-vision-core span {
  color: var(--pg-muted);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
.pg-vision-core strong {
  display: block;
  margin-top: 8px;
  font-size: 22px;
  line-height: 1.1;
}
.pg-vision-core p {
  margin-top: 8px;
  max-width: 30ch;
  color: var(--pg-muted);
  font-size: 12px;
  line-height: 1.55;
}
.pg-hero-stat-grid {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 16px;
}
.pg-hero-stat {
  border: 1px solid rgba(166, 184, 204, 0.12);
  border-radius: 18px;
  padding: 13px 14px;
  background: rgba(255, 255, 255, 0.035);
  backdrop-filter: blur(12px);
}
.pg-hero-stat span {
  display: block;
  color: var(--pg-muted);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.pg-hero-stat strong {
  display: block;
  margin-top: 6px;
  font-size: 13px;
  line-height: 1.5;
}
.pg-help-dialog {
  width: min(960px, calc(100% - 24px));
  border: 1px solid var(--pg-stroke);
  border-radius: 26px;
  padding: 0;
  background: linear-gradient(180deg, rgba(10, 18, 26, 0.98), rgba(7, 12, 19, 0.98));
  color: var(--pg-text);
  box-shadow: 0 28px 90px rgba(0, 0, 0, 0.35);
}
.pg-help-dialog::backdrop {
  background: rgba(2, 7, 12, 0.72);
  backdrop-filter: blur(10px);
}
.pg-help-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 14px;
  padding: 22px 22px 0 22px;
}
.pg-help-title {
  margin-top: 10px;
  font-size: 26px;
  font-weight: 800;
  line-height: 1.05;
}
.pg-help-close {
  appearance: none;
  border: 1px solid rgba(150, 166, 179, 0.24);
  border-radius: 999px;
  padding: 10px 14px;
  background: rgba(255, 255, 255, 0.04);
  color: var(--pg-text);
  cursor: pointer;
}
.pg-help-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 12px;
  padding: 18px 22px 22px 22px;
}
.pg-help-section {
  border: 1px solid rgba(166, 184, 204, 0.12);
  border-radius: 18px;
  padding: 14px;
  background: rgba(255, 255, 255, 0.03);
}
.pg-panel {
  position: relative;
  border: 1px solid var(--pg-stroke);
  border-radius: 22px;
  padding: 16px 18px;
  background:
    linear-gradient(180deg, rgba(11, 19, 27, 0.96), rgba(8, 14, 21, 0.9)),
    radial-gradient(circle at top right, rgba(98, 201, 180, 0.07), transparent 32%);
  box-shadow: var(--pg-shadow);
  backdrop-filter: blur(16px);
}
.pg-panel::before,
.pg-live-panel::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.03), transparent 30%);
  pointer-events: none;
}
.pg-control-intro {
  position: relative;
  border: 1px solid rgba(166, 184, 204, 0.14);
  border-radius: 18px;
  padding: 14px;
  background:
    linear-gradient(160deg, rgba(17, 30, 42, 0.88), rgba(12, 20, 28, 0.94));
}
.pg-section-title {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--pg-muted);
  margin-bottom: 10px;
  font-weight: 700;
}
.pg-action-row, .pg-inline-row, .pg-card-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}
.pg-action-label {
  font-size: 40px;
  font-weight: 800;
  line-height: 0.98;
  margin: 4px 0 8px 0;
  letter-spacing: -0.04em;
}
.pg-buy { color: var(--pg-buy); }
.pg-sell { color: var(--pg-sell); }
.pg-hold { color: var(--pg-hold); }
.pg-amber { color: var(--pg-amber); }
.pg-teal { color: var(--pg-teal); }
.pg-muted {
  color: var(--pg-muted);
  font-size: 13px;
  line-height: 1.55;
}
.pg-confidence-pill {
  min-width: 118px;
  padding: 15px 16px;
  border-radius: 20px;
  border: 1px solid var(--pg-stroke);
  background: linear-gradient(180deg, rgba(10, 17, 24, 0.98), rgba(14, 22, 31, 0.92));
  text-align: right;
}
.pg-confidence-pill strong {
  display: block;
  font-size: 30px;
  line-height: 1;
}
.pg-confidence-pill span {
  color: var(--pg-muted);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.pg-signal-overview {
  background:
    radial-gradient(circle at top right, rgba(98, 201, 180, 0.13), transparent 28%),
    linear-gradient(135deg, rgba(15, 25, 35, 0.98), rgba(9, 15, 22, 0.94));
}
.pg-signal-shell {
  display: grid;
  grid-template-columns: minmax(320px, 1.7fr) minmax(340px, 1.3fr);
  gap: 18px;
  align-items: start;
}
.pg-signal-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.pg-signal-head {
  gap: 18px;
}
.pg-signal-primary {
  flex: 1 1 auto;
  min-width: 0;
}
.pg-signal-explanation {
  max-width: 68ch;
}
.pg-signal-chips {
  margin-bottom: 0;
}
.pg-signal-metrics {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  align-content: start;
}
.pg-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 12px 0 14px 0;
}
.pg-chip {
  border-radius: 999px;
  padding: 7px 11px;
  font-size: 12px;
  font-weight: 700;
  border: 1px solid var(--pg-stroke);
  background: rgba(255, 255, 255, 0.03);
  backdrop-filter: blur(10px);
}
.pg-chip.buy { color: var(--pg-buy); }
.pg-chip.sell { color: var(--pg-sell); }
.pg-chip.hold { color: var(--pg-hold); }
.pg-chip.teal { color: var(--pg-teal); }
.pg-chip.amber { color: var(--pg-amber); }
.pg-chip.soft { color: var(--pg-text); }
.pg-metric-grid, .pg-model-grid, .pg-gate-grid, .pg-memory-grid, .pg-evidence-grid, .pg-status-grid, .pg-guidance-grid {
  display: grid;
  gap: 12px;
}
.pg-metric-grid { grid-template-columns: repeat(auto-fit, minmax(136px, 1fr)); }
.pg-model-grid { grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
.pg-gate-grid { grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); }
.pg-memory-grid, .pg-evidence-grid { grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
.pg-status-grid { grid-template-columns: repeat(auto-fit, minmax(148px, 1fr)); margin: 12px 0; }
.pg-guidance-grid { grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); margin-top: 14px; }
.pg-tile, .pg-model-card, .pg-gate-card, .pg-memory-card, .pg-evidence-card, .pg-debug-card, .pg-status-card, .pg-guidance-card, .pg-compare-card, .pg-hotspot-card {
  border: 1px solid var(--pg-stroke);
  border-radius: 18px;
  padding: 13px 14px;
  background: rgba(255, 255, 255, 0.03);
}
.pg-tile-label, .pg-card-label {
  color: var(--pg-muted);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.pg-tile-value {
  margin-top: 7px;
  font-size: 24px;
  font-weight: 800;
}
.pg-tile-sub {
  margin-top: 4px;
  color: var(--pg-muted);
  font-size: 12px;
}
.pg-meter {
  margin-top: 10px;
  height: 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.07);
  overflow: hidden;
}
.pg-meter > span {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--pg-teal), var(--pg-amber));
}
.pg-card-title {
  font-size: 18px;
  font-weight: 700;
  margin: 4px 0 2px 0;
}
.pg-card-note {
  color: var(--pg-muted);
  font-size: 12px;
  line-height: 1.55;
}
.pg-status-value {
  margin-top: 7px;
  font-size: 22px;
  line-height: 1.04;
  font-weight: 800;
  letter-spacing: -0.03em;
}
.pg-card-kv {
  margin-top: 10px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 10px;
}
.pg-card-kv span {
  color: var(--pg-muted);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.07em;
}
.pg-card-kv strong {
  display: block;
  margin-top: 2px;
  font-size: 14px;
  color: var(--pg-text);
}
.pg-pass { color: var(--pg-buy); }
.pg-fail { color: var(--pg-sell); }
.pg-brief textarea {
  font-family: "IBM Plex Mono", "Consolas", monospace !important;
  font-size: 12px !important;
  line-height: 1.6 !important;
}
.pg-tab-wrap {
  margin-top: 10px;
}
.pg-controls .gr-button,
.pg-feedback .gr-button,
.pg-zone-studio .gr-button {
  border-radius: 16px !important;
  min-height: 44px;
  font-weight: 700 !important;
}
.pg-controls .gr-button {
  background: linear-gradient(135deg, #143943, #24616a) !important;
  border: 1px solid rgba(98, 201, 180, 0.34) !important;
}
.pg-control-board {
  position: sticky;
  top: 16px;
  display: grid;
  gap: 12px;
  overflow: hidden;
}
.pg-controls .gradio-accordion,
.pg-controls details {
  border: 1px solid var(--pg-stroke);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.025);
}
.pg-controls .gradio-accordion summary,
.pg-controls details summary {
  color: var(--pg-text);
}
.pg-live-panel {
  position: relative;
  margin-top: 14px;
  border: 1px solid var(--pg-stroke);
  border-radius: 18px;
  padding: 12px 14px;
  background:
    linear-gradient(135deg, rgba(19, 34, 40, 0.92), rgba(15, 23, 31, 0.92));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03), var(--pg-shadow);
}
.pg-debug-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin-top: 14px;
}
.pg-debug-list {
  margin: 10px 0 0 16px;
  padding: 0;
  color: var(--pg-muted);
  font-size: 12px;
  line-height: 1.5;
}
.pg-debug-list li + li {
  margin-top: 4px;
}
.pg-feedback .gr-button {
  background: linear-gradient(135deg, #41311c, #7a5631) !important;
  border: 1px solid rgba(215, 166, 90, 0.36) !important;
}
.pg-stage-media,
.pg-stage-media > div,
.pg-stage-media img,
.pg-zone-editor,
.pg-zone-editor > div,
.pg-zone-editor canvas {
  border-radius: 22px;
}
.pg-stage-media img {
  transition: transform 0.22s ease;
}
.pg-stage-media:hover img {
  transform: scale(1.01);
}
.pg-compare-controls {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin-top: 14px;
}
.pg-compare-controls label {
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: var(--pg-muted);
  font-size: 12px;
}
.pg-compare-controls input[type="range"] {
  width: 100%;
}
.pg-compare-controls button {
  border-radius: 14px;
  border: 1px solid var(--pg-stroke);
  background: rgba(255, 255, 255, 0.04);
  color: var(--pg-text);
  padding: 10px 12px;
  cursor: pointer;
}
.pg-compare-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
  margin-top: 14px;
}
.pg-compare-card {
  border: 1px solid var(--pg-stroke);
  border-radius: 18px;
  padding: 13px 14px;
  background: rgba(255, 255, 255, 0.03);
}
.pg-heatmap-root {
  display: grid;
  gap: 14px;
}
.pg-heatmap-controls {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 10px;
  margin-top: 12px;
}
.pg-heatmap-controls label {
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: var(--pg-muted);
  font-size: 12px;
}
.pg-heatmap-controls input[type="range"] {
  width: 100%;
}
.pg-heatmap-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.pg-heatmap-actions button {
  border-radius: 14px;
  border: 1px solid var(--pg-stroke);
  background: rgba(255, 255, 255, 0.04);
  color: var(--pg-text);
  padding: 10px 12px;
  cursor: pointer;
}
.pg-heatmap-toggle-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin-top: 10px;
}
.pg-heatmap-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--pg-stroke);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.03);
  color: var(--pg-text);
  font-size: 12px;
}
.pg-heatmap-toggle input {
  accent-color: var(--pg-amber);
}
.pg-heat-stage {
  position: relative;
  border-radius: 18px;
  overflow: hidden;
  background: rgba(0, 0, 0, 0.24);
  min-height: 220px;
  border: 1px solid var(--pg-stroke);
}
.pg-heat-stage-inner {
  position: relative;
  width: 100%;
  transform-origin: center center;
  transition: transform 140ms ease;
}
.pg-heat-base,
.pg-heat-layer {
  display: block;
  width: 100%;
  height: auto;
}
.pg-heat-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
  transition: opacity 140ms ease;
}
.pg-heatmap-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) minmax(260px, 0.9fr);
  gap: 14px;
  align-items: start;
}
.pg-heatmap-side {
  display: grid;
  gap: 12px;
}
.pg-hotspot-list {
  display: grid;
  gap: 10px;
}
.pg-hotspot-card {
  border: 1px solid var(--pg-stroke);
  border-radius: 16px;
  padding: 12px 13px;
  background: rgba(255, 255, 255, 0.03);
}
.pg-hotspot-rank {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 26px;
  height: 26px;
  border-radius: 999px;
  background: rgba(215, 166, 90, 0.18);
  border: 1px solid rgba(215, 166, 90, 0.34);
  color: var(--pg-text);
  font-weight: 700;
  margin-right: 8px;
}
.pg-heat-legend {
  display: grid;
  gap: 8px;
}
.pg-heat-legend-row {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--pg-muted);
  font-size: 12px;
}
.pg-heat-swatch {
  width: 14px;
  height: 14px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.12);
}
.pg-transform-frame {
  margin-top: 10px;
  border-radius: 16px;
  overflow: hidden;
  background: rgba(0, 0, 0, 0.24);
  min-height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.pg-transform-frame img {
  width: 100%;
  height: auto;
  transition: transform 140ms ease, opacity 140ms ease;
  transform-origin: center center;
}
.pg-session-stack {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}
.pg-session-card {
  display: grid;
  grid-template-columns: 160px minmax(0, 1fr);
  gap: 14px;
  align-items: center;
  border: 1px solid var(--pg-stroke);
  border-radius: 18px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.03);
}
.pg-session-thumb,
.pg-pattern-thumb {
  border-radius: 14px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.04);
}
.pg-session-thumb img,
.pg-pattern-thumb img {
  display: block;
  width: 100%;
  height: auto;
}
.pg-session-copy {
  min-width: 0;
}
.pg-lab-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.8fr) minmax(300px, 1fr);
  gap: 14px;
  align-items: start;
}
.pg-zone-studio .gr-button {
  background: linear-gradient(135deg, #19453e, #3a7f73) !important;
  border: 1px solid rgba(76, 165, 154, 0.34) !important;
}
html[data-pg-mode="operator"] .pg-guided-only,
html[data-pg-mode="compact"] .pg-guided-only {
  display: none !important;
}
html[data-pg-mode="compact"] .pg-panel,
html[data-pg-mode="compact"] .pg-live-panel {
  padding: 14px 15px;
  border-radius: 18px;
}
html[data-pg-mode="compact"] .pg-chip-row {
  margin: 10px 0 12px 0;
}
html[data-pg-mode="compact"] .pg-card-note,
html[data-pg-mode="compact"] .pg-muted {
  font-size: 11px;
}
html[data-pg-mode="compact"] .pg-action-label {
  font-size: 34px;
}
html[data-pg-mode="compact"] .pg-hero {
  padding: 22px;
}
@media (max-width: 1180px) {
  .gradio-container {
    max-width: 100% !important;
    padding: 12px !important;
  }
  .pg-control-board {
    position: static;
  }
  .pg-hero-grid,
  .pg-signal-shell {
    grid-template-columns: 1fr;
  }
  .pg-heatmap-grid {
    grid-template-columns: 1fr;
  }
  .pg-hero-brand {
    font-size: 42px;
  }
  .pg-signal-metrics {
    grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
  }
}
@media (max-width: 820px) {
  .pg-hero {
    padding: 20px;
    border-radius: 22px;
  }
  .pg-hero-brand {
    font-size: 34px;
  }
  .pg-action-row, .pg-inline-row, .pg-card-top {
    flex-direction: column;
  }
  .pg-confidence-pill {
    width: 100%;
    text-align: left;
  }
  .pg-debug-grid,
  .pg-model-grid,
  .pg-gate-grid,
  .pg-memory-grid,
  .pg-evidence-grid,
  .pg-metric-grid,
  .pg-compare-grid,
  .pg-session-card,
  .pg-lab-grid,
  .pg-status-grid,
  .pg-guidance-grid,
  .pg-hero-stat-grid {
    grid-template-columns: 1fr;
  }
  .pg-help-grid {
    grid-template-columns: 1fr;
  }
}
@media (prefers-reduced-motion: reduce) {
  .gradio-container::before,
  .pg-reveal,
  .pg-inline-button,
  .pg-stage-media img,
  .pg-transform-frame img,
  .pg-heat-stage-inner,
  .pg-heat-layer {
    animation: none !important;
    transition: none !important;
  }
}
"""


UI_HEAD = """
<script>
(() => {
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const syncRoot = (root) => {
    if (!root) return;
    const zoom = parseFloat(root.querySelector('.pg-compare-zoom')?.value || '1');
    const panX = parseFloat(root.querySelector('.pg-compare-pan-x')?.value || '0');
    const panY = parseFloat(root.querySelector('.pg-compare-pan-y')?.value || '0');
    const opacity = parseFloat(root.querySelector('.pg-compare-opacity')?.value || '1');
    root.querySelectorAll('.pg-transform-target').forEach((img) => {
      img.style.transform = `translate(${panX}%, ${panY}%) scale(${zoom})`;
    });
    root.querySelectorAll('.pg-overlay-target').forEach((img) => {
      img.style.opacity = `${opacity}`;
    });
  };
  document.addEventListener('input', (event) => {
    const root = event.target.closest('.pg-compare-root');
    if (root) syncRoot(root);
  });
  document.addEventListener('click', (event) => {
    const button = event.target.closest('.pg-compare-reset');
    if (!button) return;
    const root = button.closest('.pg-compare-root');
    if (!root) return;
    const zoom = root.querySelector('.pg-compare-zoom');
    const panX = root.querySelector('.pg-compare-pan-x');
    const panY = root.querySelector('.pg-compare-pan-y');
    const opacity = root.querySelector('.pg-compare-opacity');
    if (zoom) zoom.value = '1.08';
    if (panX) panX.value = '0';
    if (panY) panY.value = '0';
    if (opacity) opacity.value = '0.94';
    syncRoot(root);
  });

  const closeDialog = (dialog) => {
    if (!dialog) return;
    if (typeof dialog.close === 'function') {
      dialog.close();
    } else {
      dialog.removeAttribute('open');
    }
  };

  const openDialog = (dialogId) => {
    const dialog = document.querySelector(`.pg-help-dialog[data-help-dialog="${dialogId}"]`);
    if (!dialog) return;
    if (typeof dialog.showModal === 'function') {
      dialog.showModal();
    } else {
      dialog.setAttribute('open', 'open');
    }
  };

  const bindDialogs = () => {
    document.querySelectorAll('.pg-help-dialog').forEach((dialog) => {
      if (dialog.dataset.bound === '1') return;
      dialog.dataset.bound = '1';
      dialog.addEventListener('click', (event) => {
        const rect = dialog.getBoundingClientRect();
        const inside =
          rect.top <= event.clientY &&
          event.clientY <= rect.top + rect.height &&
          rect.left <= event.clientX &&
          event.clientX <= rect.left + rect.width;
        if (!inside) closeDialog(dialog);
      });
    });
  };

  const syncHeatRoot = (root) => {
    if (!root) return;
    const zoom = parseFloat(root.querySelector('.pg-heat-zoom')?.value || '1');
    const opacity = parseFloat(root.querySelector('.pg-heat-opacity')?.value || '0.92');
    const inner = root.querySelector('.pg-heat-stage-inner');
    if (inner) inner.style.transform = `scale(${zoom})`;
    root.querySelectorAll('.pg-heat-layer').forEach((img) => {
      const key = img.dataset.layer || '';
      const toggle = root.querySelector(`.pg-heat-toggle-input[data-layer="${key}"]`);
      const visible = !toggle || !!toggle.checked;
      img.style.display = visible ? 'block' : 'none';
      const isContour = img.dataset.overlayRole === 'contours';
      const isMarker = img.dataset.overlayRole === 'markers';
      const localOpacity = isContour || isMarker ? Math.min(1, opacity + 0.08) : opacity;
      img.style.opacity = `${localOpacity}`;
    });
  };
  document.addEventListener('input', (event) => {
    const root = event.target.closest('.pg-heatmap-root');
    if (root) syncHeatRoot(root);
  });
  document.addEventListener('change', (event) => {
    const root = event.target.closest('.pg-heatmap-root');
    if (root) syncHeatRoot(root);
  });
  document.addEventListener('click', (event) => {
    const showAll = event.target.closest('.pg-heat-show-all');
    if (showAll) {
      const root = showAll.closest('.pg-heatmap-root');
      if (!root) return;
      root.querySelectorAll('.pg-heat-toggle-input').forEach((toggle) => {
        toggle.checked = true;
      });
      syncHeatRoot(root);
      return;
    }
    const reset = event.target.closest('.pg-heat-reset');
    if (!reset) return;
    const root = reset.closest('.pg-heatmap-root');
    if (!root) return;
    const zoom = root.querySelector('.pg-heat-zoom');
    const opacity = root.querySelector('.pg-heat-opacity');
    if (zoom) zoom.value = '1';
    if (opacity) opacity.value = '0.92';
    root.querySelectorAll('.pg-heat-toggle-input').forEach((toggle) => {
      const key = toggle.dataset.layer || '';
      toggle.checked = ['fused', 'contours', 'markers'].includes(key);
    });
    syncHeatRoot(root);
  });

  const syncDeskMode = () => {
    const modeRoot = document.querySelector('#pg_desk_mode');
    if (!modeRoot) return;
    const checked = modeRoot.querySelector('input[type="radio"]:checked');
    const rawValue =
      checked?.value ||
      checked?.getAttribute('value') ||
      checked?.closest('label')?.textContent ||
      'Guided';
    const normalized = String(rawValue || 'Guided')
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-');
    document.documentElement.dataset.pgMode = normalized || 'guided';
  };

  const revealObserver = prefersReducedMotion
    ? null
    : new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            entry.target.classList.add('is-visible');
            revealObserver?.unobserve(entry.target);
          });
        },
        { threshold: 0.08 }
      );

  const registerRevealTargets = () => {
    document.querySelectorAll('.pg-panel, .pg-live-panel, .pg-hero, .pg-control-intro').forEach((node) => {
      if (node.dataset.revealBound === '1') return;
      node.dataset.revealBound = '1';
      node.classList.add('pg-reveal');
      if (prefersReducedMotion || !revealObserver) {
        node.classList.add('is-visible');
        return;
      }
      revealObserver.observe(node);
    });
  };

  const attachMutationObserver = () => {
    if (!document.body || document.body.dataset.pgObserverBound === '1') return;
    document.body.dataset.pgObserverBound = '1';
    new MutationObserver(() => {
      bootstrapSurface();
    }).observe(document.body, { childList: true, subtree: true });
  };

  const bootstrapSurface = () => {
    bindDialogs();
    syncDeskMode();
    registerRevealTargets();
    document.querySelectorAll('.pg-compare-root').forEach(syncRoot);
    document.querySelectorAll('.pg-heatmap-root').forEach(syncHeatRoot);
    attachMutationObserver();
  };

  document.addEventListener('click', (event) => {
    const helpOpen = event.target.closest('[data-help-open]');
    if (helpOpen) {
      event.preventDefault();
      openDialog(helpOpen.getAttribute('data-help-open'));
      return;
    }
    const helpClose = event.target.closest('[data-help-close]');
    if (helpClose) {
      event.preventDefault();
      closeDialog(helpClose.closest('dialog'));
      return;
    }
  });
  document.addEventListener('change', (event) => {
    if (event.target.closest('#pg_desk_mode')) {
      syncDeskMode();
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('.pg-help-dialog[open]').forEach((dialog) => closeDialog(dialog));
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrapSurface, { once: true });
  } else {
    bootstrapSurface();
  }
})();
</script>
"""


def _escape_html(value: Any) -> str:
    return html.escape(str(value))


def _truncate_text(value: Any, limit: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _fmt_pct01(value: Any, digits: int = 1) -> str:
    return f"{float(value or 0.0) * 100.0:.{digits}f}%"


def _fmt_signed_pct(value: Any, digits: int = 3) -> str:
    return f"{float(value or 0.0):+.{digits}f}%"


def _fmt_num(value: Any, digits: int = 2) -> str:
    return f"{float(value or 0.0):.{digits}f}"


def _tone_class_for_action(action: str) -> str:
    normalized = str(action or "HOLD").upper()
    if normalized == "BUY":
        return "buy"
    if normalized == "SELL":
        return "sell"
    return "hold"


def _tone_text_class(tone: str) -> str:
    normalized = str(tone or "").strip().lower()
    if normalized in {"buy", "sell", "hold", "amber", "teal"}:
        return f" pg-{normalized}"
    return ""


def _metric_tile(label: str, value: str, subtext: str = "") -> str:
    escaped_subtext = _escape_html(subtext)
    return (
        "<div class='pg-tile'>"
        f"<div class='pg-tile-label'>{_escape_html(label)}</div>"
        f"<div class='pg-tile-value'>{_escape_html(value)}</div>"
        f"<div class='pg-tile-sub'>{escaped_subtext}</div>"
        "</div>"
    )


def _chip(label: str, tone: str = "soft") -> str:
    return f"<span class='pg-chip {tone}'>{_escape_html(label)}</span>"


def _placeholder_panel(title: str, body: str) -> str:
    return (
        "<div class='pg-panel'>"
        f"<div class='pg-section-title'>{_escape_html(title)}</div>"
        f"<div class='pg-muted'>{_escape_html(body)}</div>"
        "</div>"
    )


def _build_help_dialog(
    dialog_id: str,
    eyebrow: str,
    title: str,
    intro: str,
    sections: Sequence[tuple[str, str]],
) -> str:
    section_html = "".join(
        (
            "<div class='pg-help-section'>"
            f"<div class='pg-card-label'>{_escape_html(label)}</div>"
            f"<div class='pg-card-note'>{_escape_html(body)}</div>"
            "</div>"
        )
        for label, body in sections
    )
    return (
        f"<dialog class='pg-help-dialog' data-help-dialog='{_escape_html(dialog_id)}'>"
        "<div class='pg-help-header'>"
        "<div>"
        f"<div class='pg-kicker'>{_escape_html(eyebrow)}</div>"
        f"<div class='pg-help-title'>{_escape_html(title)}</div>"
        f"<div class='pg-card-note'>{_escape_html(intro)}</div>"
        "</div>"
        "<button type='button' class='pg-help-close' data-help-close='true'>Close</button>"
        "</div>"
        f"<div class='pg-help-grid'>{section_html}</div>"
        "</dialog>"
    )


def _build_help_dialogs_html() -> str:
    workflow_dialog = _build_help_dialog(
        "workflow",
        "Workflow Guide",
        "How to move through the desk",
        "This workspace is designed to keep the higher timeframe, the trigger chart, and the evidence trail in one place.",
        [
            ("1. Upload the pair", "Add exactly two chart images, with the higher timeframe first and the lower or trigger timeframe second. You can reorder them before running."),
            ("2. Read the overview", "The signal overview tells you the direction, confidence, execution state, and the next panel that will add the most clarity."),
            ("3. Validate before acting", "Use Compare Desk, Evidence, Gate Matrix, and Scenario Lab when the desk signals watchfulness instead of conviction."),
            ("4. Teach the system", "Zone Studio and the feedback loop keep local visual memory attached to what you learned from the chart."),
        ],
    )
    read_signal_dialog = _build_help_dialog(
        "read-signal",
        "Reading Guide",
        "How to interpret the decision surface",
        "Each panel answers a different question so the desk stays explainable instead of acting like a black box.",
        [
            ("Signal Overview", "Treat the action as the current bias, the confidence as its strength, and the execution state as the permission rail."),
            ("Forecast and Risk", "Use the quantile range, execution readiness, and projection bias to decide whether the setup is tight, mixed, or defensive."),
            ("Memory and Gates", "Memory Recall shows resemblance to past cases. Gate Matrix shows whether the structural checks agree strongly enough."),
            ("Adaptive Guidance", "When you are unsure where to go next, follow the recommended panel. It is chosen from the live result rather than a fixed tutorial."),
        ],
    )
    security_dialog = _build_help_dialog(
        "security",
        "Security and Trust",
        "What keeps this desk trustworthy",
        "The frontend should make the runtime easier to trust, so the desk surfaces where data stays and how inspection works.",
        [
            ("Local inspection", "Compare Desk zoom, pan, and overlay opacity are browser-side controls, so visual inspection stays local and immediate."),
            ("Encrypted preferences", "Operator preferences are backed by the encrypted preference store instead of plain-text settings."),
            ("Audit visibility", "Audit JSON and session history keep the rendered decision state inspectable instead of hidden behind one score."),
            ("Explicit feedback saves", "Result images are only stored when you intentionally submit feedback with an attached image for learning."),
        ],
    )
    personalize_dialog = _build_help_dialog(
        "personalize",
        "Personalization",
        "Ways to tailor the workstation",
        "Gradio makes it easy to keep one pipeline while letting different operators work in the mode that matches their pace.",
        [
            ("Desk Mode", "Guided keeps onboarding cues visible, Operator trims the training copy, and Compact tightens spacing for fast scanning."),
            ("Scenario Lab", "Rehearse alternate thresholds without touching the live baseline so you can explore edge cases safely."),
            ("Zone Studio", "Save support, resistance, and reaction zones to build chart-specific teaching memory that carries forward."),
            ("Feedback Loop", "Verdicts and marked-up result images help the personalization and reinforcement layers learn from what actually happened."),
        ],
    )
    return workflow_dialog + read_signal_dialog + security_dialog + personalize_dialog


def _build_hero_shell_html() -> str:
    chips = "".join(
        [
            _chip("Dual timeframe workflow", "teal"),
            _chip("Client-side compare desk", "soft"),
            _chip("Audit-ready review", "amber"),
            _chip("Adaptive guidance", "teal"),
        ]
    )
    stat_tiles = "".join(
        [
            (
                "<div class='pg-hero-stat'>"
                "<span>Trust rails</span>"
                "<strong>Encrypted local preferences and audit-ready state review.</strong>"
                "</div>"
            ),
            (
                "<div class='pg-hero-stat'>"
                "<span>Decision flow</span>"
                "<strong>Upload, inspect, compare, rehearse, then teach the desk.</strong>"
                "</div>"
            ),
            (
                "<div class='pg-hero-stat'>"
                "<span>Vision loop</span>"
                "<strong>Higher timeframe context, lower timeframe trigger, one explainable surface.</strong>"
                "</div>"
            ),
            (
                "<div class='pg-hero-stat'>"
                "<span>Operator memory</span>"
                "<strong>Zone Studio and feedback keep local learning attached to the chart.</strong>"
                "</div>"
            ),
        ]
    )
    return (
        "<section class='pg-hero'>"
        "<div class='pg-hero-grid'>"
        "<div class='pg-hero-copy'>"
        "<div class='pg-kicker'>Vision Workspace</div>"
        f"<div class='pg-hero-brand'>{_escape_html(UI_BRAND_NAME)}</div>"
        f"<h1>{_escape_html(UI_BRAND_SUBTITLE)}</h1>"
        "<p>See structure sooner, verify the decision path faster, and keep every chart review explainable from upload to feedback.</p>"
        f"<div class='pg-chip-row pg-hero-chips'>{chips}</div>"
        "<div class='pg-hero-actions'>"
        "<button type='button' class='pg-inline-button' data-help-open='workflow'>Workflow Guide</button>"
        "<button type='button' class='pg-inline-button' data-help-open='read-signal'>Read A Signal</button>"
        "<button type='button' class='pg-inline-button' data-help-open='security'>Security Notes</button>"
        "<button type='button' class='pg-inline-button' data-tone='secondary' data-help-open='personalize'>Personalize The Desk</button>"
        "</div>"
        "</div>"
        "<div class='pg-hero-visual' aria-hidden='true'>"
        "<div class='pg-vision-stage'>"
        "<div class='pg-vision-core'>"
        "<span>Vision Desk</span>"
        "<strong>Context -> Trigger -> Evidence</strong>"
        "<p>Built for explainable chart review instead of single-number automation.</p>"
        "</div>"
        f"<div class='pg-hero-stat-grid'>{stat_tiles}</div>"
        "</div>"
        "</div>"
        "</div>"
        f"{_build_help_dialogs_html()}"
        "</section>"
    )


def _build_mission_control_intro_html() -> str:
    return (
        "<div class='pg-control-intro pg-guided-only'>"
        "<div class='pg-card-label'>Quick Start</div>"
        "<div class='pg-card-title'>Two charts. One explainable desk.</div>"
        "<div class='pg-card-note'>Upload the higher timeframe first and the lower or trigger timeframe second. Run the desk, then follow Adaptive Guidance if you are not sure which panel matters next.</div>"
        "<div class='pg-inline-actions'>"
        "<button type='button' class='pg-inline-button' data-help-open='workflow'>Workflow</button>"
        "<button type='button' class='pg-inline-button' data-tone='secondary' data-help-open='read-signal'>Read The Panels</button>"
        "</div>"
        "</div>"
    )


def _status_card(label: str, value: str, note: str = "", tone: str = "soft") -> str:
    tone_class = _tone_text_class(tone)
    return (
        "<div class='pg-status-card'>"
        f"<div class='pg-card-label'>{_escape_html(label)}</div>"
        f"<div class='pg-status-value{tone_class}'>{_escape_html(value)}</div>"
        f"<div class='pg-card-note'>{_escape_html(note)}</div>"
        "</div>"
    )


def _recommended_panel_details(result: Mapping[str, Any]) -> tuple[str, str, str]:
    zone_learning = cast(dict[str, Any], result.get("zone_learning", {}))
    multi_timeframe = cast(dict[str, Any], result.get("multi_timeframe", {}))
    recommended_panel = "Evidence"
    rationale = "Pattern evidence is the clearest next stop for this signal."
    tone = "soft"
    if multi_timeframe and not bool(multi_timeframe.get("aligned", False)):
        recommended_panel = "Compare Desk"
        rationale = "The higher and trigger timeframes are disagreeing, so a side-by-side review is the fastest way to resolve the conflict."
        tone = "amber"
    elif bool(result.get("geometry_conflict", False)) or bool(result.get("strict_cv_fail_closed", False)):
        recommended_panel = "Diagnostics"
        rationale = "Runtime structure is conflicted, so the diagnostics cockpit should be opened before trusting the visible overlay."
        tone = "amber"
    elif int(zone_learning.get("match_count", 0) or 0) > 0:
        recommended_panel = "Zone Studio"
        rationale = "Saved teaching zones are intersecting the active structure, so your zone context is the highest-value follow-up."
        tone = "teal"
    elif 0.45 <= float(result.get("confidence", 0.0) or 0.0) <= 0.68:
        recommended_panel = "Scenario Lab"
        rationale = "The signal has a directional lean but not a decisive edge, which makes alternate threshold rehearsal the safest next move."
        tone = "amber"
    elif float(result.get("memory_similarity", 0.0) or 0.0) >= 0.84:
        recommended_panel = "Pattern Browser"
        rationale = "This run resembles prior desk cases closely, so reviewing comparable visuals can sharpen execution."
        tone = "teal"
    return recommended_panel, rationale, tone


def _signal_coaching_copy(result: Mapping[str, Any]) -> tuple[str, str, str]:
    action = str(result.get("action", "HOLD")).upper()
    execution = str(result.get("execution_permission", "WAIT_FOR_CONFIRMATION")).upper()
    confidence = float(result.get("confidence", 0.0) or 0.0)
    consensus_ok = bool(result.get("consensus_ok", False))
    if action == "HOLD":
        return (
            "The desk is leaning toward patience",
            "No directional edge is clear enough yet, so treat the current run as observation first and wait for cleaner structure or clearer confirmation.",
            "amber",
        )
    if execution == "EXECUTE" and consensus_ok and confidence >= 0.82:
        return (
            "Execution rails are largely open",
            "Bias, consensus, and confidence are aligned strongly enough that the desk is behaving more like a confirmatory operator surface than a watchlist.",
            "teal",
        )
    if execution == "EXECUTE":
        return (
            "Actionable bias, but still worth validating visually",
            "The desk is permissive enough to act, yet Compare Desk or Evidence should still be the final confirmation before trust is extended to the setup.",
            "teal",
        )
    return (
        "Bias exists, confirmation is still pending",
        "Treat the direction as a thesis instead of a command. Use the next recommended panel to confirm that structure, memory, and execution rails agree.",
        "amber",
    )


def _resolve_focus_crop_bbox(
    result: Mapping[str, Any],
    source_image: Image.Image | None,
) -> tuple[int, int, int, int] | None:
    if source_image is None:
        return None
    preferred_bboxes: list[list[float]] = []
    current_box = cast(dict[str, Any], result.get("current_box", {}))
    projection_next = cast(dict[str, Any], cast(dict[str, Any], result.get("projection", {})).get("next_box", {}))
    for candidate in [
        cast(list[float], current_box.get("bbox", [])),
        cast(list[float], projection_next.get("bbox", [])),
        cast(list[float], cast(dict[str, Any], result.get("chart_geometry", {})).get("latest_sequence_bbox", [])),
        cast(list[float], cast(dict[str, Any], result.get("chart_geometry", {})).get("plot_inner_bbox", [])),
    ]:
        if len(candidate) == 4:
            preferred_bboxes.append([float(v) for v in candidate])
    bbox = preferred_bboxes[0] if preferred_bboxes else [0.0, 0.0, float(source_image.width), float(source_image.height)]
    x1, y1, x2, y2 = bbox
    pad_x = max((x2 - x1) * 0.18, 28.0)
    pad_y = max((y2 - y1) * 0.22, 28.0)
    return _clamp_bbox(
        [x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y],
        float(source_image.width),
        float(source_image.height),
    )


def _build_focus_crop_image(result: Mapping[str, Any], source_image: Image.Image | None) -> Image.Image | None:
    if source_image is None:
        return None
    clamped = _resolve_focus_crop_bbox(result, source_image)
    if clamped is None:
        return source_image.copy()
    return source_image.crop(clamped)


def _build_overlay_image(
    source_image: Image.Image | None,
    result: Mapping[str, Any],
    *,
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_limit: int | None = None,
    label_budget: int | None = None,
    projection_confidence_floor: float | None = None,
) -> Image.Image | None:
    if source_image is None:
        return None
    user_zones = cast(list[dict[str, Any]], cast(dict[str, Any], result.get("zone_learning", {})).get("visible_zones", []))
    draw_kwargs: dict[str, Any] = {
        "user_zones": user_zones,
        "overlay_mode": overlay_mode,
        "min_conf_global": min_conf_global,
        "min_conf_latest": min_conf_latest,
        "chart_structure": {
            "chart_geometry": cast(dict[str, Any], result.get("chart_geometry", {})),
            "sequence_state": cast(dict[str, Any], result.get("sequence_state", {})),
            "box_history": cast(list[dict[str, Any]], result.get("box_history", [])),
            "current_box": cast(dict[str, Any], result.get("current_box", {})),
            "next_box_hypotheses": cast(list[dict[str, Any]], result.get("next_box_hypotheses", [])),
            "chart_state": cast(dict[str, Any], result.get("chart_state", {})),
            "projected_candle_candidates": cast(list[dict[str, Any]], result.get("projected_candle_candidates", [])),
            "council_sequence_summary": cast(dict[str, Any], result.get("council_sequence_summary", {})),
        },
    }
    if history_limit is not None:
        draw_kwargs["history_limit"] = history_limit
    if label_budget is not None:
        draw_kwargs["label_budget"] = label_budget
    if projection_confidence_floor is not None:
        draw_kwargs["projection_confidence_floor"] = projection_confidence_floor
    return draw_overlay(
        source_image,
        cast(list[dict[str, Any]], result.get("detections", [])),
        [],
        **draw_kwargs,
    )


def _build_decision_gauge_from_result(result: Mapping[str, Any]) -> Any:
    action = str(result.get("action", "HOLD"))
    probs = cast(dict[str, float], result.get("probabilities", {}))
    return build_prob_gauge(float(probs.get(action, 0.0) or 0.0), action)


def _build_skill_figure(
    personal: Any,
    result: Mapping[str, Any],
) -> Any:
    gate_scores = {
        _alias_gate_name(name): float(score)
        for name, score in cast(dict[str, Any], result.get("gate_scores", {})).items()
    }
    shap_contributions = {
        _alias_gate_name(name): float(score)
        for name, score in cast(dict[str, Any], result.get("shap_contributions", {})).items()
    }
    return personal.build_plotly_dashboard(
        gate_scores=gate_scores,
        shap_contributions=shap_contributions,
        candle_accuracy=None,
    )


def _bump_capture_status_token() -> None:
    _capture_runtime_state["status_token"] = int(_capture_runtime_state.get("status_token", 0) or 0) + 1


def _maybe_run_post_inference_cleanup() -> None:
    _runtime_maintenance_state["inference_runs"] = int(_runtime_maintenance_state.get("inference_runs", 0) or 0) + 1
    inference_runs = int(_runtime_maintenance_state["inference_runs"])

    gc_collect_every = int(getattr(RUNTIME, "gc_collect_every", 0) or 0)
    if gc_collect_every > 0 and inference_runs % gc_collect_every == 0:
        gc.collect()

    if not torch.cuda.is_available():
        return

    should_clear_cache = False
    cuda_cache_clear_every = int(getattr(RUNTIME, "cuda_cache_clear_every", 0) or 0)
    if cuda_cache_clear_every > 0 and inference_runs % cuda_cache_clear_every == 0:
        should_clear_cache = True

    cuda_cache_clear_reserved_gb = float(getattr(RUNTIME, "cuda_cache_clear_reserved_gb", 0.0) or 0.0)
    if cuda_cache_clear_reserved_gb > 0.0:
        try:
            reserved_gb = float(torch.cuda.memory_reserved()) / float(1024 ** 3)
            if reserved_gb >= cuda_cache_clear_reserved_gb:
                should_clear_cache = True
        except Exception:
            pass

    if should_clear_cache:
        torch.cuda.empty_cache()


def _blur_heat_array(arr: NDArray[np.float32], radius: float) -> NDArray[np.float32]:
    image = Image.fromarray(np.uint8(np.clip(arr, 0.0, 1.0) * 255.0), mode="L")
    if radius > 1e-6:
        image = image.filter(ImageFilter.GaussianBlur(radius=radius))
    return np.asarray(image, dtype=np.float32) / 255.0


def _normalize_heat_array(arr: NDArray[Any], percentile: float = 98.5) -> NDArray[np.float32]:
    positive = arr[arr > 1e-6]
    if positive.size == 0:
        return np.zeros_like(arr, dtype=np.float32)
    scale = float(np.percentile(positive, percentile))
    return np.clip(arr / max(scale, 1e-6), 0.0, 1.0).astype(np.float32, copy=False)


def _extract_heatmap_hotspots(
    fused_heat: NDArray[np.float32],
    layer_arrays: Mapping[str, NDArray[np.float32]],
) -> list[dict[str, Any]]:
    if not fused_heat.size or float(fused_heat.max()) <= 1e-6:
        return []
    positive = fused_heat[fused_heat > 1e-6]
    if positive.size == 0:
        return []
    height, width = fused_heat.shape
    threshold = max(0.34, min(0.86, float(np.percentile(positive, 91.0))))
    ys, xs = np.where(fused_heat >= threshold)
    if ys.size == 0:
        flat_index = int(np.argmax(fused_heat))
        ys = np.asarray([flat_index // width], dtype=np.int32)
        xs = np.asarray([flat_index % width], dtype=np.int32)
    scores = fused_heat[ys, xs]
    order = np.argsort(scores)[::-1]
    min_distance = max(12.0, float(min(height, width)) * 0.11)
    selected: list[dict[str, Any]] = []
    chosen_points: list[tuple[int, int]] = []
    label_map = {
        "detections": "Detection-led confluence",
        "corridor": "Projection-led confluence",
        "zones": "Zone-led confluence",
    }
    for flat_pos in order.tolist():
        y = int(ys[flat_pos])
        x = int(xs[flat_pos])
        score = float(scores[flat_pos])
        if selected and score < 0.28:
            break
        if any((x - px) ** 2 + (y - py) ** 2 < min_distance * min_distance for px, py in chosen_points):
            continue
        layer_scores = {
            name: float(np.clip(layer[y, x], 0.0, 1.0))
            for name, layer in layer_arrays.items()
            if layer.shape == fused_heat.shape
        }
        ordered_layers = sorted(layer_scores.items(), key=lambda item: item[1], reverse=True)
        if not ordered_layers:
            dominant_layer = "fused"
            hotspot_label = "Confidence confluence"
        elif len(ordered_layers) > 1 and ordered_layers[0][1] - ordered_layers[1][1] < 0.08:
            dominant_layer = "mixed"
            hotspot_label = "Mixed confluence"
        else:
            dominant_layer = ordered_layers[0][0]
            hotspot_label = label_map.get(dominant_layer, "Confidence confluence")
        selected.append(
            {
                "rank": len(selected) + 1,
                "x": x,
                "y": y,
                "x_pct": float(x) / float(max(width - 1, 1)) * 100.0,
                "y_pct": float(y) / float(max(height - 1, 1)) * 100.0,
                "score": score,
                "dominant_layer": dominant_layer,
                "label": hotspot_label,
                "layer_scores": layer_scores,
            }
        )
        chosen_points.append((x, y))
        if len(selected) >= 5:
            break
    return selected


def _render_heat_layer_overlay(
    heat_arr: NDArray[np.float32],
    *,
    low_color: tuple[int, int, int],
    high_color: tuple[int, int, int],
    gamma: float = 1.0,
    alpha_scale: float = 210.0,
) -> Image.Image:
    height, width = heat_arr.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    hot = np.clip(np.power(np.clip(heat_arr, 0.0, 1.0), gamma), 0.0, 1.0)
    for channel, (low, high) in enumerate(zip(low_color, high_color)):
        rgba[..., channel] = np.uint8(np.clip(low + hot * float(high - low), 0.0, 255.0))
    alpha = np.clip(np.power(hot, 1.20) * alpha_scale, 0.0, 236.0)
    rgba[..., 3] = np.uint8(alpha)
    return Image.fromarray(rgba, mode="RGBA")


def _render_fused_heat_overlay(heat_arr: NDArray[np.float32]) -> Image.Image:
    height, width = heat_arr.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    hot = np.clip(heat_arr, 0.0, 1.0)
    red = 78.0 + hot * 172.0
    green = 18.0 + np.power(hot, 0.92) * 162.0
    blue = 42.0 + np.power(1.0 - hot, 1.35) * 74.0
    core_mask = hot >= 0.74
    red = np.where(core_mask, 210.0 + hot * 42.0, red)
    green = np.where(core_mask, 148.0 + hot * 46.0, green)
    blue = np.where(core_mask, 70.0 + hot * 24.0, blue)
    alpha = np.clip(np.power(hot, 1.55) * 228.0, 0.0, 236.0)
    rgba[..., 0] = np.uint8(np.clip(red, 0.0, 255.0))
    rgba[..., 1] = np.uint8(np.clip(green, 0.0, 255.0))
    rgba[..., 2] = np.uint8(np.clip(blue, 0.0, 255.0))
    rgba[..., 3] = np.uint8(alpha)
    return Image.fromarray(rgba, mode="RGBA")


def _build_heat_contour_overlay(
    fused_heat: NDArray[np.float32],
    contour_levels: Sequence[float],
) -> Image.Image:
    height, width = fused_heat.shape
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    palette = [
        (84, 229, 214),
        (255, 200, 92),
        (255, 142, 72),
        (255, 238, 214),
    ]
    for idx, level in enumerate(contour_levels):
        mask = fused_heat >= float(level)
        if np.count_nonzero(mask) < 4 or height < 3 or width < 3:
            continue
        eroded = (
            mask[:-2, :-2]
            & mask[:-2, 1:-1]
            & mask[:-2, 2:]
            & mask[1:-1, :-2]
            & mask[1:-1, 1:-1]
            & mask[1:-1, 2:]
            & mask[2:, :-2]
            & mask[2:, 1:-1]
            & mask[2:, 2:]
        )
        boundary = np.zeros_like(mask, dtype=bool)
        boundary[1:-1, 1:-1] = mask[1:-1, 1:-1] & ~eroded
        if not np.any(boundary):
            continue
        boundary_img = Image.fromarray(np.uint8(boundary) * 255, mode="L").filter(ImageFilter.MaxFilter(size=3))
        boundary_alpha = np.asarray(boundary_img, dtype=np.float32) / 255.0
        color = palette[min(idx, len(palette) - 1)]
        alpha = np.clip(boundary_alpha * (118.0 + idx * 28.0), 0.0, 220.0)
        for channel, value in enumerate(color):
            rgba[..., channel] = np.maximum(rgba[..., channel], np.uint8(boundary_alpha * float(value)))
        rgba[..., 3] = np.maximum(rgba[..., 3], np.uint8(alpha))
    return Image.fromarray(rgba, mode="RGBA")


def _build_heat_hotspot_overlay(
    hotspots: Sequence[Mapping[str, Any]],
    size: tuple[int, int],
) -> Image.Image:
    width, height = size
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    radius = max(8, int(min(width, height) * 0.016))
    for hotspot in hotspots:
        x = int(hotspot.get("x", 0) or 0)
        y = int(hotspot.get("y", 0) or 0)
        rank_text = str(int(hotspot.get("rank", 0) or 0))
        score_text = str(int(round(float(hotspot.get("score", 0.0) or 0.0) * 100.0)))
        halo_box = (x - radius * 2, y - radius * 2, x + radius * 2, y + radius * 2)
        ring_box = (x - radius, y - radius, x + radius, y + radius)
        draw.ellipse(halo_box, outline=(255, 170, 90, 92), width=2)
        draw.ellipse(ring_box, fill=(18, 18, 22, 196), outline=(255, 236, 214, 235), width=2)
        rank_bbox = draw.textbbox((0, 0), rank_text)
        rank_w = rank_bbox[2] - rank_bbox[0]
        rank_h = rank_bbox[3] - rank_bbox[1]
        draw.text((x - rank_w / 2.0, y - rank_h / 2.0 - 1.0), rank_text, fill=(255, 244, 226, 240))
        score_bbox = draw.textbbox((0, 0), score_text)
        score_w = score_bbox[2] - score_bbox[0]
        score_h = score_bbox[3] - score_bbox[1]
        score_x = min(max(4.0, x + radius + 6.0), float(max(width - score_w - 8, 4)))
        score_y = min(max(4.0, y - radius - score_h / 2.0), float(max(height - score_h - 8, 4)))
        label_box = (score_x - 4.0, score_y - 2.0, score_x + score_w + 4.0, score_y + score_h + 2.0)
        draw.rounded_rectangle(label_box, radius=6, fill=(14, 18, 24, 212), outline=(255, 182, 96, 220), width=1)
        draw.text((score_x, score_y), score_text, fill=(255, 236, 210, 240))
    return overlay


def _build_confidence_heatmap_payload(
    result: Mapping[str, Any],
    source_image: Image.Image | None,
) -> dict[str, Any] | None:
    if source_image is None:
        return None
    width = int(source_image.width)
    height = int(source_image.height)
    context_heat = np.zeros((height, width), dtype=np.float32)
    precision_heat = np.zeros((height, width), dtype=np.float32)
    path_heat = np.zeros((height, width), dtype=np.float32)
    detection_heat = np.zeros((height, width), dtype=np.float32)
    zone_heat = np.zeros((height, width), dtype=np.float32)

    def add_spot(
        target: NDArray[np.float32],
        center_x: float,
        center_y: float,
        radius_x: float,
        radius_y: float,
        weight: float,
    ) -> None:
        value = float(max(weight, 0.0))
        if value <= 1e-6:
            return
        rx = max(float(radius_x), 2.0)
        ry = max(float(radius_y), 2.0)
        left = max(0, int(np.floor(center_x - 3.2 * rx)))
        right = min(width, int(np.ceil(center_x + 3.2 * rx)))
        top = max(0, int(np.floor(center_y - 3.2 * ry)))
        bottom = min(height, int(np.ceil(center_y + 3.2 * ry)))
        if right - left < 2 or bottom - top < 2:
            return
        x = (np.arange(left, right, dtype=np.float32) - float(center_x)) / rx
        y = (np.arange(top, bottom, dtype=np.float32) - float(center_y)) / ry
        yy, xx = np.meshgrid(y, x, indexing="ij")
        patch = np.exp(-0.5 * (xx * xx + yy * yy), dtype=np.float32)
        target[top:bottom, left:right] += patch * value

    def add_soft_bbox(
        bbox: Sequence[Any],
        weight: float,
        *,
        context_scale: float = 1.0,
        precision_scale: float = 1.0,
        audit_target: NDArray[np.float32] | None = None,
        audit_scale: float = 0.82,
        audit_radius_scale: float = 1.0,
    ) -> tuple[float, float] | None:
        clamped = _clamp_bbox(bbox, float(width), float(height))
        if clamped is None:
            return None
        left, top, right, bottom = clamped
        if right - left < 2 or bottom - top < 2:
            return None
        box_w = float(right - left)
        box_h = float(bottom - top)
        center_x = float(left + right) / 2.0
        center_y = float(top + bottom) / 2.0
        base_rx = max(4.0, box_w * 0.22)
        base_ry = max(4.0, box_h * 0.22)
        value = float(max(weight, 0.0))
        add_spot(
            context_heat,
            center_x,
            center_y,
            base_rx * 1.7,
            base_ry * 1.6,
            value * 0.58 * float(context_scale),
        )
        add_spot(
            precision_heat,
            center_x,
            center_y,
            base_rx * 0.90,
            base_ry * 0.88,
            value * 0.88 * float(precision_scale),
        )
        if audit_target is not None:
            add_spot(
                audit_target,
                center_x,
                center_y,
                base_rx * (1.05 * float(audit_radius_scale)),
                base_ry * (1.02 * float(audit_radius_scale)),
                value * float(audit_scale),
            )
        return center_x, center_y

    def add_projection_corridor(
        start_bbox: Sequence[Any],
        end_bbox: Sequence[Any],
        weight: float,
        *,
        rank_scale: float = 1.0,
    ) -> None:
        start = _clamp_bbox(start_bbox, float(width), float(height))
        end = _clamp_bbox(end_bbox, float(width), float(height))
        if start is None or end is None:
            return
        sx = float(start[0] + start[2]) / 2.0
        sy = float(start[1] + start[3]) / 2.0
        ex = float(end[0] + end[2]) / 2.0
        ey = float(end[1] + end[3]) / 2.0
        corridor_rx = max(4.0, abs(ex - sx) * 0.04 + max(start[2] - start[0], end[2] - end[0]) * 0.10)
        corridor_ry = max(3.0, (start[3] - start[1] + end[3] - end[1]) * 0.08)
        steps = max(8, int(max(abs(ex - sx), abs(ey - sy)) / max(6.0, corridor_rx * 0.70)))
        for t in np.linspace(0.0, 1.0, steps, dtype=np.float32):
            px = float(sx + (ex - sx) * t)
            py = float(sy + (ey - sy) * t)
            center_falloff = 0.72 + 0.28 * (1.0 - abs(float(t) - 0.5) * 1.4)
            local_weight = float(max(weight, 0.0)) * float(rank_scale) * center_falloff / max(steps / 1.8, 1.0)
            add_spot(path_heat, px, py, corridor_rx, corridor_ry, local_weight)

    detections = cast(list[dict[str, Any]], result.get("detections", []))
    for detection in detections:
        conf = float(detection.get("overlay_confidence", detection.get("confidence", 0.0)) or 0.0)
        bonus = 0.12 if _is_latest_branch_pattern(str(detection.get("pattern", ""))) else 0.0
        add_soft_bbox(
            cast(list[float], detection.get("bbox", [])),
            conf * 0.74 + bonus,
            context_scale=1.0,
            precision_scale=1.15 if bonus > 0.0 else 1.0,
            audit_target=detection_heat,
            audit_scale=0.86,
        )
    current_box = cast(dict[str, Any], result.get("current_box", {}))
    current_bbox = cast(list[float], current_box.get("bbox", []))
    add_soft_bbox(
        current_bbox,
        float(current_box.get("confidence", 0.0) or 0.0) + 0.24,
        context_scale=1.05,
        precision_scale=1.22,
        audit_target=detection_heat,
        audit_scale=0.92,
    )
    next_boxes = cast(list[dict[str, Any]], result.get("next_box_hypotheses", []))
    for rank, box in enumerate(next_boxes[:3]):
        rank_weight = float(box.get("confidence", 0.0) or 0.0) * max(0.42, 0.88 - rank * 0.18)
        box_bbox = cast(list[float], box.get("bbox", []))
        add_soft_bbox(
            box_bbox,
            rank_weight,
            context_scale=max(0.62, 0.94 - rank * 0.15),
            precision_scale=max(0.56, 0.90 - rank * 0.12),
            audit_target=detection_heat,
            audit_scale=max(0.52, 0.80 - rank * 0.10),
            audit_radius_scale=max(0.90, 1.12 - rank * 0.08),
        )
        add_projection_corridor(
            current_bbox,
            box_bbox,
            rank_weight * 0.95,
            rank_scale=max(0.52, 0.95 - rank * 0.18),
        )
    focus_bbox = _resolve_focus_crop_bbox(result, source_image)
    if focus_bbox is not None:
        add_soft_bbox(
            list(focus_bbox),
            0.18,
            context_scale=0.85,
            precision_scale=0.32,
            audit_target=detection_heat,
            audit_scale=0.18,
            audit_radius_scale=1.18,
        )
    zone_learning = cast(dict[str, Any], result.get("zone_learning", {}))
    for zone in cast(list[dict[str, Any]], zone_learning.get("matching_zones", []))[:6]:
        add_soft_bbox(
            cast(list[float], zone.get("bbox", [])),
            0.30 + float(zone.get("score", 0.0) or 0.0) * 0.34,
            context_scale=1.18,
            precision_scale=0.52,
            audit_target=zone_heat,
            audit_scale=0.96,
            audit_radius_scale=1.12,
        )

    raw_heat = 0.34 * context_heat + 0.96 * precision_heat + 0.56 * path_heat + 0.18 * zone_heat
    if not raw_heat.size or float(raw_heat.max()) <= 1e-6:
        return {
            "layers": {
                "fused": np.zeros((height, width), dtype=np.float32),
                "detections": np.zeros((height, width), dtype=np.float32),
                "corridor": np.zeros((height, width), dtype=np.float32),
                "zones": np.zeros((height, width), dtype=np.float32),
            },
            "hotspots": [],
            "contour_levels": [0.38, 0.54, 0.70, 0.86],
            "coverage_pct": 0.0,
            "core_pct": 0.0,
        }

    gray = np.asarray(source_image.convert("L"), dtype=np.float32) / 255.0
    grad_y, grad_x = np.gradient(gray)
    edge_prior = np.sqrt(grad_x * grad_x + grad_y * grad_y, dtype=np.float32)
    if float(edge_prior.max()) > 1e-6:
        edge_prior = edge_prior / float(edge_prior.max())
    edge_image = Image.fromarray(np.uint8(np.clip(edge_prior, 0.0, 1.0) * 255.0), mode="L").filter(
        ImageFilter.GaussianBlur(radius=max(1.0, min(width, height) * 0.0026))
    )
    edge_prior = np.asarray(edge_image, dtype=np.float32) / 255.0
    raw_heat = raw_heat * (0.48 + 0.52 * edge_prior) + 0.18 * path_heat
    normalized = _normalize_heat_array(raw_heat, percentile=98.5)
    context_norm = _normalize_heat_array(context_heat, percentile=98.0)
    precision_norm = _normalize_heat_array(precision_heat + 0.75 * path_heat, percentile=98.6)
    path_norm = _normalize_heat_array(path_heat, percentile=98.0)
    detection_norm = _normalize_heat_array(detection_heat, percentile=98.0)
    zone_norm = _normalize_heat_array(zone_heat, percentile=98.0)
    context_blur = _blur_heat_array(context_norm, radius=max(8.0, min(width, height) * 0.012))
    sharp_blur = _blur_heat_array(precision_norm, radius=max(2.0, min(width, height) * 0.0036))
    path_blur = _blur_heat_array(path_norm, radius=max(1.4, min(width, height) * 0.0024))
    detection_layer = np.clip(
        0.52 * _blur_heat_array(detection_norm, radius=max(3.0, min(width, height) * 0.0042))
        + 0.48 * np.power(detection_norm, 1.06),
        0.0,
        1.0,
    ).astype(np.float32, copy=False)
    corridor_layer = np.clip(
        0.44 * path_blur + 0.56 * np.power(path_norm, 1.08),
        0.0,
        1.0,
    ).astype(np.float32, copy=False)
    zone_layer = np.clip(
        0.58 * _blur_heat_array(zone_norm, radius=max(4.0, min(width, height) * 0.0056))
        + 0.42 * np.power(zone_norm, 1.02),
        0.0,
        1.0,
    ).astype(np.float32, copy=False)
    fused_heat = np.clip(
        0.22 * context_blur
        + 0.78 * np.power(sharp_blur, 1.18)
        + 0.28 * np.power(corridor_layer, 1.03)
        + 0.16 * np.power(zone_layer, 1.04)
        + 0.14 * np.power(detection_layer, 1.10)
        + 0.24 * np.power(normalized, 1.08),
        0.0,
        1.0,
    )
    fused_heat = np.clip(fused_heat * (0.72 + 0.28 * edge_prior), 0.0, 1.0).astype(np.float32, copy=False)
    contour_levels = [0.38, 0.54, 0.70, 0.86]
    hotspots = _extract_heatmap_hotspots(
        fused_heat,
        {
            "detections": detection_layer,
            "corridor": corridor_layer,
            "zones": zone_layer,
        },
    )
    return {
        "layers": {
            "fused": fused_heat,
            "detections": detection_layer,
            "corridor": corridor_layer,
            "zones": zone_layer,
        },
        "hotspots": hotspots,
        "contour_levels": contour_levels,
        "coverage_pct": float(np.count_nonzero(fused_heat >= 0.36)) / float(max(fused_heat.size, 1)) * 100.0,
        "core_pct": float(np.count_nonzero(fused_heat >= 0.72)) / float(max(fused_heat.size, 1)) * 100.0,
    }


def _compose_confidence_heatmap_image(
    heatmap_payload: Mapping[str, Any] | None,
    source_image: Image.Image | None,
) -> Image.Image | None:
    if source_image is None or not heatmap_payload:
        return source_image.copy() if source_image is not None else None
    layers = cast(dict[str, NDArray[np.float32]], heatmap_payload.get("layers", {}))
    fused_heat = layers.get("fused")
    if fused_heat is None or not fused_heat.size or float(fused_heat.max()) <= 1e-6:
        return source_image.copy()
    overlay = _render_fused_heat_overlay(fused_heat)
    blended = Image.alpha_composite(source_image.convert("RGBA"), overlay)
    return blended.convert("RGB")


def _build_confidence_heatmap_image(result: Mapping[str, Any], source_image: Image.Image | None) -> Image.Image | None:
    heatmap_payload = _build_confidence_heatmap_payload(result, source_image)
    return _compose_confidence_heatmap_image(heatmap_payload, source_image)


def _build_heatmap_layer_audit_html(
    heatmap_payload: Mapping[str, Any],
    source_image: Image.Image,
) -> str:
    layers = cast(dict[str, NDArray[np.float32]], heatmap_payload.get("layers", {}))
    fused_heat = layers.get("fused", np.zeros((source_image.height, source_image.width), dtype=np.float32))
    contour_levels = [float(level) for level in cast(list[float], heatmap_payload.get("contour_levels", [0.38, 0.54, 0.70, 0.86]))]
    hotspots = cast(list[dict[str, Any]], heatmap_payload.get("hotspots", []))
    source_uri = _image_to_data_uri(source_image, max_width=760, max_height=520, fmt="PNG")
    overlay_specs = [
        (
            "fused",
            "Final Fused Heat",
            "Combined confidence field",
            True,
            _render_fused_heat_overlay(layers.get("fused", fused_heat)),
            "",
        ),
        (
            "detections",
            "Detections",
            "Observed structure and hypothesis footprints",
            False,
            _render_heat_layer_overlay(
                layers.get("detections", np.zeros_like(fused_heat)),
                low_color=(82, 22, 124),
                high_color=(255, 120, 222),
                gamma=0.98,
                alpha_scale=218.0,
            ),
            "",
        ),
        (
            "corridor",
            "Projection Corridor",
            "Forward path concentration",
            False,
            _render_heat_layer_overlay(
                layers.get("corridor", np.zeros_like(fused_heat)),
                low_color=(122, 56, 18),
                high_color=(255, 196, 88),
                gamma=1.02,
                alpha_scale=210.0,
            ),
            "",
        ),
        (
            "zones",
            "Zones",
            "Saved teaching-zone overlap",
            False,
            _render_heat_layer_overlay(
                layers.get("zones", np.zeros_like(fused_heat)),
                low_color=(24, 78, 98),
                high_color=(118, 242, 224),
                gamma=0.96,
                alpha_scale=206.0,
            ),
            "",
        ),
        (
            "contours",
            "Contour Rings",
            "Explicit isolines for intensity bands",
            True,
            _build_heat_contour_overlay(fused_heat, contour_levels),
            "contours",
        ),
        (
            "markers",
            "Hotspot Markers",
            "Ranked numeric peaks",
            True,
            _build_heat_hotspot_overlay(hotspots, source_image.size),
            "markers",
        ),
    ]
    toggle_html = "".join(
        (
            "<label class='pg-heatmap-toggle'>"
            f"<input class='pg-heat-toggle-input' type='checkbox' data-layer='{_escape_html(key)}' {'checked' if checked else ''}>"
            f"<span><strong>{_escape_html(label)}</strong><br>{_escape_html(description)}</span>"
            "</label>"
        )
        for key, label, description, checked, _image, _overlay_role in overlay_specs
    )
    overlay_html = "".join(
        (
            f"<img class='pg-heat-layer' data-layer='{_escape_html(key)}' data-overlay-role='{_escape_html(overlay_role)}' "
            f"src='{_image_to_data_uri(image, max_width=760, max_height=520, fmt='PNG')}' "
            f"alt='{_escape_html(label)}' style='display:{'block' if checked else 'none'};opacity:0.92;'>"
        )
        for key, label, _description, checked, image, overlay_role in overlay_specs
    )
    hotspot_cards = "".join(
        (
            "<div class='pg-hotspot-card'>"
            f"<div><span class='pg-hotspot-rank'>{int(hotspot.get('rank', 0) or 0)}</span><strong>Hotspot {int(hotspot.get('rank', 0) or 0)}</strong></div>"
            f"<div class='pg-muted'>{_escape_html(str(hotspot.get('label', 'Confidence confluence')))} | x={_fmt_num(hotspot.get('x_pct', 0.0), 1)}% y={_fmt_num(hotspot.get('y_pct', 0.0), 1)}%</div>"
            "<div class='pg-chip-row'>"
            + _chip(f"Heat {_fmt_num(hotspot.get('score', 0.0), 2)}", "amber")
            + _chip(f"Det {_fmt_num(cast(dict[str, float], hotspot.get('layer_scores', {})).get('detections', 0.0), 2)}", "soft")
            + _chip(f"Path {_fmt_num(cast(dict[str, float], hotspot.get('layer_scores', {})).get('corridor', 0.0), 2)}", "soft")
            + _chip(f"Zone {_fmt_num(cast(dict[str, float], hotspot.get('layer_scores', {})).get('zones', 0.0), 2)}", "soft")
            + "</div>"
            + "</div>"
        )
        for hotspot in hotspots
    ) or (
        "<div class='pg-hotspot-card'>"
        "<strong>Top Hotspots</strong>"
        "<div class='pg-muted'>No explicit hotspot peak was strong enough to rank on this pass.</div>"
        "</div>"
    )
    contour_label = " / ".join(f"{level:.2f}" for level in contour_levels)
    legend_rows = "".join(
        [
            "<div class='pg-heat-legend-row'><span class='pg-heat-swatch' style='background:linear-gradient(135deg,#5c1822,#ffae5e);'></span>Final fused heat</div>",
            "<div class='pg-heat-legend-row'><span class='pg-heat-swatch' style='background:linear-gradient(135deg,#52167c,#ff78de);'></span>Detections</div>",
            "<div class='pg-heat-legend-row'><span class='pg-heat-swatch' style='background:linear-gradient(135deg,#7a3812,#ffc458);'></span>Projection corridor</div>",
            "<div class='pg-heat-legend-row'><span class='pg-heat-swatch' style='background:linear-gradient(135deg,#184e62,#76f2e0);'></span>Zones</div>",
            f"<div class='pg-heat-legend-row'><span class='pg-heat-swatch' style='background:linear-gradient(135deg,#54e5d6,#fff2d6);'></span>Contour Rings {contour_label}</div>",
            "<div class='pg-heat-legend-row'><span class='pg-heat-swatch' style='background:linear-gradient(135deg,#ffb15a,#fff0d6);'></span>Hotspot Markers ranked by peak score</div>",
        ]
    )
    return (
        "<div class='pg-heatmap-controls'>"
        "<label>Zoom<input class='pg-heat-zoom' type='range' min='1' max='2.3' step='0.02' value='1'></label>"
        "<label>Layer Opacity<input class='pg-heat-opacity' type='range' min='0.35' max='1' step='0.01' value='0.92'></label>"
        "</div>"
        "<div class='pg-heatmap-actions'>"
        "<button class='pg-heat-show-all' type='button'>Show All Layers</button>"
        "<button class='pg-heat-reset' type='button'>Reset View</button>"
        "</div>"
        f"<div class='pg-heatmap-toggle-grid'>{toggle_html}</div>"
        "<div class='pg-heatmap-grid'>"
        "<div class='pg-heat-stage'>"
        "<div class='pg-heat-stage-inner' style='transform:scale(1);'>"
        f"<img class='pg-heat-base' src='{source_uri}' alt='Base chart'>"
        f"{overlay_html}"
        "</div>"
        "</div>"
        "<div class='pg-heatmap-side'>"
        "<div>"
        "<div class='pg-section-title'>Top Hotspots</div>"
        f"<div class='pg-hotspot-list'>{hotspot_cards}</div>"
        "</div>"
        "<div>"
        "<div class='pg-section-title'>Layer Legend</div>"
        f"<div class='pg-heat-legend'>{legend_rows}</div>"
        "</div>"
        "</div>"
        "</div>"
    )


def _build_heatmap_summary_html(
    result: Mapping[str, Any],
    source_image: Image.Image | None = None,
    *,
    heatmap_payload: Mapping[str, Any] | None = None,
) -> str:
    if not result:
        return _placeholder_panel("Confidence Heatmap", "Heat concentration will appear after the first inference.")
    zone_learning = cast(dict[str, Any], result.get("zone_learning", {}))
    projection = cast(dict[str, Any], result.get("projection", {}))
    chart_state = cast(dict[str, Any], result.get("chart_state", {}))
    payload = heatmap_payload if heatmap_payload is not None else _build_confidence_heatmap_payload(result, source_image)
    hotspots = cast(list[dict[str, Any]], payload.get("hotspots", [])) if payload else []
    coverage_pct = float(payload.get("coverage_pct", 0.0) or 0.0) if payload else 0.0
    core_pct = float(payload.get("core_pct", 0.0) or 0.0) if payload else 0.0
    chips = "".join(
        [
            _chip(f"Action {str(result.get('action', 'HOLD')).upper()}", _tone_class_for_action(str(result.get("action", "HOLD")).upper())),
            _chip(f"Projection {str(projection.get('direction', 'HOLD')).upper()}", _tone_class_for_action(str(projection.get("direction", "HOLD")).upper())),
            _chip(f"Zone matches {int(zone_learning.get('match_count', 0) or 0)}", "amber" if int(zone_learning.get("match_count", 0) or 0) else "soft"),
            _chip(f"Alignment {_fmt_num(zone_learning.get('alignment_score', 0.0), 2)}", "teal"),
            _chip(f"Path {_fmt_num(chart_state.get('path_clarity', 0.0), 2)}", "soft"),
            _chip(f"Parse {_fmt_num(result.get('latest_parse_quality', 0.0), 2)}", "soft"),
            _chip(f"Hotspots {len(hotspots)}", "amber" if hotspots else "soft"),
            _chip(f"Core {coverage_pct:.1f}%/{core_pct:.1f}%", "soft"),
        ]
    )
    top_zones = "".join(
        f"<li>{_escape_html(str(zone.get('kind', 'zone')).title())}: {_escape_html(_truncate_text(zone.get('label', 'Teaching zone'), 42))} | score={_fmt_num(zone.get('score', 0.0), 2)}</li>"
        for zone in cast(list[dict[str, Any]], zone_learning.get("matching_zones", []))[:4]
    ) or "<li>No taught zones intersected this chart.</li>"
    audit_html = (
        _build_heatmap_layer_audit_html(payload, source_image)
        if payload is not None and source_image is not None
        else "<div class='pg-muted'>Layer audit tools appear when the source chart is available.</div>"
    )
    return (
        "<div class='pg-panel pg-heatmap-root'>"
        "<div class='pg-section-title'>Confidence Heatmap</div>"
        f"<div class='pg-chip-row'>{chips}</div>"
        "<div class='pg-muted'>The heatmap now exposes separable detection, projection corridor, zone, and fused layers, with contour rings and ranked hotspot markers so confidence is numerically auditable instead of just color-based.</div>"
        f"{audit_html}"
        "<div class='pg-section-title'>Zone Intersections</div>"
        f"<ul class='pg-debug-list'>{top_zones}</ul>"
        "</div>"
    )


def _build_zone_library_html(result: Mapping[str, Any] | None = None) -> str:
    zones = _load_zone_memory()
    if not zones:
        return _placeholder_panel("Zone Library", "Save support, resistance, or reaction zones in Zone Studio to build your teaching memory.")
    kind_counts = {
        "support": sum(1 for zone in zones if str(zone.get("kind", "")).lower() == "support"),
        "resistance": sum(1 for zone in zones if str(zone.get("kind", "")).lower() == "resistance"),
        "reaction": sum(1 for zone in zones if str(zone.get("kind", "")).lower() == "reaction"),
    }
    cards: list[str] = []
    for zone in list(reversed(zones))[:8]:
        kind = str(zone.get("kind", "reaction")).lower()
        tone = "buy" if kind == "support" else "sell" if kind == "resistance" else "amber"
        strength_chip = _chip(f"strength {_fmt_num(zone.get('strength', 0.7), 2)}", tone)
        source_chip = _chip(_truncate_text(zone.get("source_file", "manual"), 22), "soft")
        cards.append(
            "<div class='pg-memory-card'>"
            f"<div class='pg-card-label'>{_escape_html(kind.title())}</div>"
            f"<div class='pg-card-title'>{_escape_html(_truncate_text(zone.get('label', 'Zone'), 48))}</div>"
            f"<div class='pg-card-note'>{_escape_html(_truncate_text(zone.get('notes', 'No notes supplied.'), 86))}</div>"
            f"<div class='pg-chip-row' style='margin-top:10px;'>{strength_chip}{source_chip}</div>"
            "</div>"
        )
    active_matches = 0
    if result:
        active_matches = int(cast(dict[str, Any], result.get("zone_learning", {})).get("match_count", 0) or 0)
    chips = "".join(
        [
            _chip(f"Support {kind_counts['support']}", "buy"),
            _chip(f"Resistance {kind_counts['resistance']}", "sell"),
            _chip(f"Reaction {kind_counts['reaction']}", "amber"),
            _chip(f"Active matches {active_matches}", "teal"),
        ]
    )
    return (
        "<div class='pg-panel'>"
        "<div class='pg-section-title'>Zone Library</div>"
        f"<div class='pg-chip-row'>{chips}</div>"
        f"<div class='pg-memory-grid'>{''.join(cards)}</div>"
        "</div>"
    )


def _build_adaptive_guidance_html(result: Mapping[str, Any] | None) -> str:
    if not result:
        return (
            "<div class='pg-live-panel'>"
            "<div class='pg-section-title'>Adaptive Guidance</div>"
            "<div class='pg-status-grid'>"
            f"{_status_card('Next panel', 'Awaiting run', 'The desk will recommend the highest-value follow-up panel after the first chart pair is analyzed.', 'soft')}"
            f"{_status_card('Need help?', 'Open workflow guide', 'If the desk feels dense, start with the guided workflow and the signal reading notes.', 'teal')}"
            "</div>"
            "<div class='pg-inline-actions pg-guided-only'>"
            "<button type='button' class='pg-inline-button' data-help-open='workflow'>Workflow Guide</button>"
            "<button type='button' class='pg-inline-button' data-tone='secondary' data-help-open='read-signal'>Read A Signal</button>"
            "</div>"
            "</div>"
        )
    recommended_panel, rationale, tone = _recommended_panel_details(result)
    coaching_title, coaching_body, coaching_tone = _signal_coaching_copy(result)
    return (
        "<div class='pg-live-panel'>"
        "<div class='pg-section-title'>Adaptive Guidance</div>"
        "<div class='pg-status-grid'>"
        f"{_status_card('Open next', recommended_panel, rationale, tone)}"
        f"{_status_card('Interpretation', coaching_title, coaching_body, coaching_tone)}"
        "</div>"
        "<div class='pg-inline-actions pg-guided-only'>"
        "<button type='button' class='pg-inline-button' data-help-open='read-signal'>Why this panel</button>"
        "<button type='button' class='pg-inline-button' data-tone='secondary' data-help-open='workflow'>Workflow help</button>"
        "</div>"
        "</div>"
    )


def _build_session_timeline_html() -> str:
    snapshot = _get_session_snapshot()
    entries = cast(list[dict[str, Any]], snapshot.get("entries", []))
    if not entries:
        return _placeholder_panel("Session Timeline", "Analyzed captures will stack here in chronological order during the trading session.")
    cards: list[str] = []
    for entry in reversed(entries[-12:]):
        thumb = _session_entry_thumbnail_uri(entry)
        tone = _tone_class_for_action(str(entry.get("action", "HOLD")).upper())
        multi = " | MTF" if bool(entry.get("multi_timeframe", False)) else ""
        confidence_chip = _chip(f"conf {_fmt_pct01(entry.get('confidence', 0.0))}", tone)
        move_chip = _chip(f"move {_fmt_signed_pct(entry.get('expected_move_pct', 0.0))}", "soft")
        image_block = (
            f"<div class='pg-session-thumb'><img src='{thumb}' alt='session thumbnail' /></div>"
            if thumb
            else ""
        )
        cards.append(
            "<div class='pg-session-card'>"
            f"{image_block}"
            "<div class='pg-session-copy'>"
            f"<div class='pg-card-label'>{_escape_html(str(entry.get('timestamp', 'unknown')))}</div>"
            f"<div class='pg-card-title pg-{tone}'>{_escape_html(str(entry.get('action', 'HOLD')).upper())}</div>"
            f"<div class='pg-card-note'>{_escape_html(_truncate_text(str(entry.get('file_name', 'capture')), 42))}{_escape_html(multi)}</div>"
            f"<div class='pg-chip-row' style='margin-top:8px;'>{confidence_chip}{move_chip}</div>"
            "</div>"
            "</div>"
        )
    return (
        "<div class='pg-panel'>"
        f"<div class='pg-section-title'>Session Timeline</div>"
        f"<div class='pg-muted'>session={_escape_html(str(snapshot.get('session_id', 'live')))} | entries={len(entries)}</div>"
        f"<div class='pg-session-stack'>{''.join(cards)}</div>"
        "</div>"
    )


def _build_pattern_memory_browser_html(result: Mapping[str, Any] | None) -> str:
    snapshot = _get_session_snapshot()
    entries = cast(list[dict[str, Any]], snapshot.get("entries", []))
    if not entries:
        return _placeholder_panel("Pattern Browser", "As the session grows, similar cases from this desk will appear here visually.")
    if not result:
        return _placeholder_panel("Pattern Browser", "Run a chart to compare it against visual cases from the current session.")

    projection_direction = str(cast(dict[str, Any], result.get("projection", {})).get("direction", "HOLD")).upper()
    current_action = str(result.get("action", "HOLD")).upper()
    current_conf = float(result.get("confidence", 0.0) or 0.0)
    current_memory = float(result.get("memory_similarity", 0.0) or 0.0)

    def _similarity(entry: Mapping[str, Any]) -> float:
        score = 0.0
        if str(entry.get("action", "HOLD")).upper() == current_action:
            score += 0.34
        if str(entry.get("projection_direction", "HOLD")).upper() == projection_direction:
            score += 0.22
        score += max(0.0, 0.20 - abs(float(entry.get("confidence", 0.0) or 0.0) - current_conf) * 0.35)
        score += max(0.0, 0.16 - abs(float(entry.get("memory_similarity", 0.0) or 0.0) - current_memory) * 0.25)
        if bool(entry.get("multi_timeframe", False)) == bool(cast(dict[str, Any], result.get("multi_timeframe", {}))):
            score += 0.08
        return float(score)

    ranked = sorted(entries[:-1] if len(entries) > 1 else entries, key=_similarity, reverse=True)[:6]
    if not ranked:
        return _placeholder_panel("Pattern Browser", "No comparable cases are available yet in this session.")
    cards: list[str] = []
    for entry in ranked:
        thumb = _session_entry_thumbnail_uri(entry)
        tone = _tone_class_for_action(str(entry.get("action", "HOLD")).upper())
        projection_chip = _chip(f"projection {entry.get('projection_direction', 'HOLD')}", "soft")
        memory_chip = _chip(f"memory {_fmt_num(entry.get('memory_similarity', 0.0), 2)}", "teal")
        thumb_block = (
            f"<div class='pg-pattern-thumb'><img src='{thumb}' alt='pattern memory thumbnail' /></div>"
            if thumb
            else ""
        )
        cards.append(
            "<div class='pg-memory-card'>"
            f"{thumb_block}"
            f"<div class='pg-card-label'>{_escape_html(str(entry.get('timestamp', 'unknown')))}</div>"
            f"<div class='pg-card-title pg-{tone}'>{_escape_html(str(entry.get('action', 'HOLD')).upper())}</div>"
            f"<div class='pg-card-note'>{_escape_html(_truncate_text(entry.get('file_name', 'capture'), 42))}</div>"
            f"<div class='pg-chip-row' style='margin-top:8px;'>{projection_chip}{memory_chip}</div>"
            "</div>"
        )
    return (
        "<div class='pg-panel'>"
        "<div class='pg-section-title'>Pattern Browser</div>"
        "<div class='pg-muted'>Visual cases are ranked from the live session using action, projection, confidence, and memory-profile similarity.</div>"
        f"<div class='pg-memory-grid' style='margin-top:14px;'>{''.join(cards)}</div>"
        "</div>"
    )


def _build_compare_desk_html(
    result: Mapping[str, Any],
    source_image: Image.Image | None,
    overlay_image: Image.Image | None,
    heatmap_image: Image.Image | None,
    render_config: Mapping[str, Any] | None = None,
) -> str:
    if source_image is None or overlay_image is None:
        return _placeholder_panel("Compare Desk", "Raw, focused, and annotated compare views will appear here after a run.")
    cache_prefix = _compare_cache_prefix(result)
    render_state = dict(render_config or {})
    overlay_signature = (
        f"{str(render_state.get('overlay_mode', 'history-plus-projection'))}:"
        f"{float(render_state.get('min_conf_global', 0.42) or 0.42):.2f}:"
        f"{float(render_state.get('min_conf_latest', 0.50) or 0.50):.2f}:"
        f"{int(render_state.get('history_depth', 8) or 8)}:"
        f"{int(render_state.get('label_density', 10) or 10)}:"
        f"{float(render_state.get('projection_focus', 0.35) or 0.35):.2f}"
    )
    focus_bbox = _resolve_focus_crop_bbox(result, source_image)
    raw_uri = _cached_compare_frame_uri(
        f"{cache_prefix}:raw:720x430",
        lambda: _image_to_data_uri(source_image, max_width=720, max_height=430),
    )
    focus_cache_key = (
        f"{cache_prefix}:focus:{','.join(str(v) for v in focus_bbox)}:720x430"
        if focus_bbox is not None
        else f"{cache_prefix}:focus:full:720x430"
    )
    focus_uri = _cached_compare_frame_uri(
        focus_cache_key,
        lambda: _image_to_data_uri(_build_focus_crop_image(result, source_image), max_width=720, max_height=430),
    )
    overlay_uri = _cached_compare_frame_uri(
        f"{cache_prefix}:overlay:{overlay_signature}:720x430",
        lambda: _image_to_data_uri(overlay_image, max_width=720, max_height=430),
    )
    heatmap_uri = _cached_compare_frame_uri(
        f"{cache_prefix}:heatmap:720x430",
        lambda: _image_to_data_uri(heatmap_image, max_width=720, max_height=430),
    )
    compare_id = f"pg-compare-{uuid4().hex[:8]}"

    def _frame_card(title: str, subtitle: str, image_uri: str, overlay_target: bool = False) -> str:
        overlay_class = " pg-overlay-target" if overlay_target else ""
        default_style = "transform: translate(0%, 0%) scale(1.08);"
        if overlay_target:
            default_style += " opacity: 0.94;"
        return (
            "<div class='pg-compare-card'>"
            f"<div class='pg-card-label'>{_escape_html(title)}</div>"
            f"<div class='pg-card-note'>{_escape_html(subtitle)}</div>"
            "<div class='pg-transform-frame'>"
            f"<img class='pg-transform-target{overlay_class}' src='{image_uri}' alt='{_escape_html(title)}' style='{default_style}' />"
            "</div>"
            "</div>"
        )

    baseline_cards = "".join(
        [
            _frame_card("Raw Chart", "Original source image", raw_uri),
            _frame_card("Captured Focus", "Auto-cropped decision region", focus_uri),
            _frame_card("Annotated Output", "Live overlay and projection layer", overlay_uri, overlay_target=True),
            _frame_card("Confidence Heatmap", "Engine certainty concentration", heatmap_uri, overlay_target=True),
        ]
    )

    multi_html = ""
    multi_timeframe = cast(dict[str, Any], result.get("multi_timeframe", {}))
    entries = cast(list[dict[str, Any]], multi_timeframe.get("entries", []))
    if entries:
        mtf_cards: list[str] = []
        for entry in entries[:4]:
            frame_title = f"{str(entry.get('label', 'Timeframe')).title()} | {str(entry.get('action', 'HOLD')).upper()}"
            frame_subtitle = (
                f"projection={str(entry.get('projection_direction', 'HOLD')).upper()} "
                f"| bias={str(entry.get('bias_direction', 'HOLD')).upper()} "
                f"{float(entry.get('bias_strength', 0.0) or 0.0):.2f} "
                f"| setup={str(entry.get('setup', 'none')).replace('_', ' ')}"
            )
            frame_uri = _image_uri_from_file(
                str(entry.get("overlay_asset_path", "")) or str(entry.get("raw_asset_path", "")),
                max_width=520,
                max_height=300,
            )
            mtf_cards.append(_frame_card(frame_title, frame_subtitle, frame_uri, overlay_target=True))
        multi_html = (
            "<div class='pg-section-title' style='margin-top:18px;'>Split Compare</div>"
            f"<div class='pg-muted'>{_escape_html(str(multi_timeframe.get('summary', 'Higher and trigger timeframe compare desk.')))}</div>"
            f"<div class='pg-muted' style='margin-top:6px;'>{_escape_html(str(multi_timeframe.get('gate_explanation', '')))}</div>"
            f"<div class='pg-compare-grid' style='margin-top:14px;'>{''.join(mtf_cards)}</div>"
        )

    return (
        f"<div class='pg-panel pg-compare-root' id='{compare_id}'>"
        "<div class='pg-section-title'>Compare Desk</div>"
        "<div class='pg-muted'>Before/after review stays local in the browser. Zoom, pan, and overlay opacity are client-side so inspection feels instant.</div>"
        "<div class='pg-compare-controls'>"
        "<label>Zoom <input class='pg-compare-zoom' type='range' min='1' max='2.4' step='0.02' value='1.08' /></label>"
        "<label>Pan X <input class='pg-compare-pan-x' type='range' min='-24' max='24' step='1' value='0' /></label>"
        "<label>Pan Y <input class='pg-compare-pan-y' type='range' min='-24' max='24' step='1' value='0' /></label>"
        "<label>Overlay Opacity <input class='pg-compare-opacity' type='range' min='0.25' max='1' step='0.01' value='0.94' /></label>"
        "<button type='button' class='pg-compare-reset'>Reset</button>"
        "</div>"
        f"<div class='pg-compare-grid'>{baseline_cards}</div>"
        f"{multi_html}"
        "</div>"
    )


def _build_timeframe_overlay_gallery_html(result: Mapping[str, Any]) -> str:
    multi_timeframe = cast(dict[str, Any], result.get("multi_timeframe", {}))
    entries = cast(list[dict[str, Any]], multi_timeframe.get("entries", []))
    if not entries:
        return _placeholder_panel("Timeframe Overlays", "Higher and lower timeframe overlays will appear here after a run.")

    cards: list[str] = []
    for entry in entries[:2]:
        label = str(entry.get("label", "Timeframe")).title()
        action = str(entry.get("action", "HOLD")).upper()
        projection = str(entry.get("projection_direction", "HOLD")).upper()
        setup = str(entry.get("setup", "none")).replace("_", " ").title()
        image_uri = _image_uri_from_file(
            str(entry.get("overlay_asset_path", "")) or str(entry.get("raw_asset_path", "")),
            max_width=720,
            max_height=420,
        )
        if not image_uri:
            continue
        chips = "".join(
            [
                _chip(f"Action {action}", _tone_class_for_action(action)),
                _chip(f"Projection {projection}", "amber" if projection in {"BUY", "SELL"} else "soft"),
                _chip(f"Confidence {_fmt_pct01(entry.get('confidence', 0.0))}", "soft"),
            ]
        )
        subtitle = f"{setup} | {str(entry.get('file_name', 'chart')).strip() or 'chart'}"
        cards.append(
            "<div class='pg-compare-card'>"
            f"<div class='pg-card-label'>{_escape_html(label)}</div>"
            f"<div class='pg-card-note'>{_escape_html(subtitle)}</div>"
            f"<div class='pg-chip-row' style='margin-top:8px;'>{chips}</div>"
            "<div class='pg-transform-frame'>"
            f"<img src='{image_uri}' alt='{_escape_html(label)} overlay' style='transform: translate(0%, 0%) scale(1.02); opacity: 0.96;' />"
            "</div>"
            "</div>"
        )

    if not cards:
        return _placeholder_panel("Timeframe Overlays", "Overlay snapshots are not available for the current run yet.")

    gate_state = str(multi_timeframe.get("gate_state", "watch") or "watch").lower()
    gate_tone = "teal" if gate_state == "confirmed" else "amber" if gate_state == "blocked" else "soft"
    gate_explanation = str(multi_timeframe.get("gate_explanation", "") or "").strip()
    gate_html = (
        f"<div class='pg-muted' style='margin-top:6px;'>{_escape_html(gate_explanation)}</div>"
        if gate_explanation
        else ""
    )
    return (
        "<div class='pg-panel'>"
        "<div class='pg-section-title'>Timeframe Overlays</div>"
        f"<div class='pg-muted'>{_escape_html(str(multi_timeframe.get('summary', 'Higher and lower timeframe overlays.')))}</div>"
        f"<div class='pg-chip-row'>{_chip(f'Gate {gate_state.upper()}', gate_tone)}</div>"
        f"{gate_html}"
        f"<div class='pg-compare-grid'>{''.join(cards)}</div>"
        "</div>"
    )


def build_control_status_html(
    result: dict[str, Any] | None,
    render_config: Mapping[str, Any] | None = None,
) -> str:
    config = dict(render_config or {})
    capture_runtime = _get_capture_runtime_snapshot()
    session_snapshot = _get_session_snapshot()
    zone_count = len(_load_zone_memory())
    hotkey_label = str(capture_runtime.get("active_hotkey", "") or capture_runtime.get("requested_hotkey", "F4"))
    capture_status = str(capture_runtime.get("status", "Hotkey capture offline."))
    capture_file = str(capture_runtime.get("last_capture_file", ""))
    capture_time = str(capture_runtime.get("last_capture_time", ""))
    capture_error = str(capture_runtime.get("last_error", ""))
    pending_count = int(capture_runtime.get("pending_bundle_count", 0) or 0)
    bundle_size = int(capture_runtime.get("bundle_size", max(1, RUNTIME.capture_bundle_size)) or max(1, RUNTIME.capture_bundle_size))
    bundle_chip = _chip(f"Capture {pending_count}/{bundle_size}", "amber" if pending_count else "soft")
    session_chip = _chip(f"Session {len(cast(list[Any], session_snapshot.get('entries', [])))}", "soft")
    zone_chip = _chip(f"Zones {zone_count}", "teal" if zone_count else "soft")
    if not result:
        error_block = (
            f"<div class='pg-muted' style='margin-top:8px;'>{_escape_html(capture_error)}</div>"
            if capture_error
            else ""
        )
        status_cards = "".join(
            [
                _status_card("Hotkey", hotkey_label, "Ready for live capture once the chart pair is available.", "teal"),
                _status_card("Capture buffer", f"{pending_count}/{bundle_size}", "Timeframe pairing buffer for live hotkey capture.", "amber" if pending_count else "soft"),
                _status_card("Session review", str(len(cast(list[Any], session_snapshot.get("entries", [])))), "Reviewed chart pairs stored in the current session timeline.", "soft"),
                _status_card("Trust rails", "Audit ready", "Compare controls are browser-side and preferences are stored with encryption support.", "teal"),
            ]
        )
        return (
            "<div class='pg-live-panel'>"
            "<div class='pg-section-title'>Live Preview Status</div>"
            f"<div class='pg-chip-row'>{_chip(f'Hotkey {hotkey_label}', 'teal')}{bundle_chip}{session_chip}{zone_chip}</div>"
            f"<div class='pg-status-grid'>{status_cards}</div>"
            f"<div class='pg-muted' style='margin-top:10px;'>{_escape_html(capture_status)}</div>"
            f"{error_block}"
            + "<div class='pg-muted' style='margin-top:8px;'>Upload exactly two chart images to enable manual runs and live control updates.</div>"
            + "<div class='pg-inline-actions pg-guided-only'><button type='button' class='pg-inline-button' data-help-open='workflow'>How the desk works</button><button type='button' class='pg-inline-button' data-tone='secondary' data-help-open='security'>Security notes</button></div>"
            "</div>"
        )

    chips = "".join(
        [
            _chip(f"Hotkey {hotkey_label}", "teal"),
            _chip("Cached Preview Ready", "teal"),
            bundle_chip,
            session_chip,
            zone_chip,
            _chip(f"Overlay {str(config.get('overlay_mode', 'history-plus-projection')).replace('-', ' ')}", "soft"),
            _chip(f"Global > {_fmt_num(config.get('min_conf_global', 0.42), 2)}", "soft"),
            _chip(f"Latest > {_fmt_num(config.get('min_conf_latest', 0.50), 2)}", "soft"),
            _chip(f"History {int(config.get('history_depth', 8) or 8)}", "amber"),
            _chip(f"Labels {int(config.get('label_density', 10) or 10)}", "soft"),
            _chip(f"Projection floor {_fmt_num(config.get('projection_focus', 0.35), 2)}", "amber"),
            _chip(f"Debug depth {int(config.get('debug_depth', 6) or 6)}", "soft"),
        ]
    )
    summary = (
        f"Last inference: {_truncate_text(result.get('timestamp', 'unknown'), 40)} | "
        f"Action {str(result.get('action', 'HOLD')).upper()} | "
        f"Confidence {_fmt_pct01(result.get('confidence', 0.0))}"
    )
    status_cards = "".join(
        [
            _status_card("Action", str(result.get("action", "HOLD")).upper(), "Current directional bias surfaced by the desk.", _tone_class_for_action(str(result.get("action", "HOLD")).upper())),
            _status_card("Confidence", _fmt_pct01(result.get("confidence", 0.0)), "Strength of the surfaced bias after consensus and control rails.", "teal" if float(result.get("confidence", 0.0) or 0.0) >= 0.7 else "amber"),
            _status_card("Capture buffer", f"{pending_count}/{bundle_size}", "How far the live hotkey workflow is through the paired timeframe bundle.", "amber" if pending_count else "soft"),
            _status_card("Session review", str(len(cast(list[Any], session_snapshot.get("entries", [])))), "Saved reviewed chart pairs in the current session.", "soft"),
        ]
    )
    capture_note = capture_status
    if capture_file:
        capture_note += f" | source={_truncate_text(capture_file, 42)}"
    if capture_time:
        capture_note += f" | at {capture_time}"
    if pending_count:
        capture_note += f" | waiting for timeframe {pending_count + 1} of {bundle_size}"
    error_block = (
        f"<div class='pg-muted' style='margin-top:8px;'>{_escape_html(capture_error)}</div>"
        if capture_error
        else ""
    )
    return (
        "<div class='pg-live-panel'>"
        "<div class='pg-section-title'>Live Preview Status</div>"
        f"<div class='pg-chip-row'>{chips}</div>"
        f"<div class='pg-status-grid'>{status_cards}</div>"
        f"<div class='pg-muted'>{_escape_html(summary)}</div>"
        f"<div class='pg-muted' style='margin-top:8px;'>{_escape_html(capture_note)}</div>"
        f"{error_block}"
        "<div class='pg-inline-actions pg-guided-only'><button type='button' class='pg-inline-button' data-help-open='security'>Security notes</button><button type='button' class='pg-inline-button' data-tone='secondary' data-help-open='read-signal'>Reading guide</button></div>"
        "</div>"
    )


def build_signal_overview_html(result: dict[str, Any]) -> str:
    if not result:
        return _placeholder_panel("Signal Overview", "Run an inference to see the live decision card.")

    action = str(result.get("action", "HOLD")).upper()
    tone = _tone_class_for_action(action)
    decision_state = str(result.get("decision_state", "UNCERTAIN")).replace("_", " ").title()
    execution_permission = str(result.get("execution_permission", "WAIT_FOR_CONFIRMATION")).replace("_", " ").title()
    explanation = _truncate_text(result.get("explanation", "No explanation produced."), limit=240)
    chart_state = cast(dict[str, Any], result.get("chart_state", {}))
    module_rel = cast(dict[str, float], result.get("module_reliability", {}))
    projection = cast(dict[str, Any], result.get("projection", {}))
    zone_learning = cast(dict[str, Any], result.get("zone_learning", {}))
    multi_timeframe = cast(dict[str, Any], result.get("multi_timeframe", {}))
    recommended_panel, rationale, recommended_tone = _recommended_panel_details(result)
    coaching_title, coaching_body, coaching_tone = _signal_coaching_copy(result)

    chip_rows = [
        _chip(f"Consensus {'OK' if bool(result.get('consensus_ok', False)) else 'Watch'}", "teal" if bool(result.get("consensus_ok", False)) else "amber"),
        _chip(f"Memory {str(result.get('memory_direction', 'HOLD')).upper()}", _tone_class_for_action(str(result.get("memory_direction", "HOLD")).upper())),
        _chip(f"Phase {str(chart_state.get('entry_type', 'continuation')).replace('_', ' ').title()}", "soft"),
        _chip(f"Bias {str(chart_state.get('momentum_bias', 'neutral')).title()}", "soft"),
        _chip(f"State {decision_state}", "soft"),
        _chip(
            f"Execution {execution_permission}",
            "teal" if str(result.get("execution_permission", "WAIT_FOR_CONFIRMATION")).upper() == "EXECUTE" else "amber",
        ),
        _chip(
            f"Projection {str(projection.get('direction', chart_state.get('projection_bias_direction', 'HOLD'))).upper()} {_fmt_num(projection.get('confidence', chart_state.get('projection_bias_confidence', 0.0)), 2)}",
            _tone_class_for_action(str(projection.get("direction", chart_state.get("projection_bias_direction", "HOLD"))).upper()),
        ),
        _chip(f"Setup {str(chart_state.get('structure_setup', 'none')).replace('_', ' ')}", "soft"),
    ]
    if int(zone_learning.get("match_count", 0) or 0):
        chip_rows.append(
            _chip(
                f"Zone bias {str(zone_learning.get('preferred_action', 'HOLD')).upper()} {_fmt_num(zone_learning.get('alignment_score', 0.0), 2)}",
                "teal" if str(zone_learning.get("preferred_action", "HOLD")).upper() == action else "amber",
            )
        )
    if multi_timeframe:
        gate_state = str(multi_timeframe.get("gate_state", "watch") or "watch").lower()
        gate_label = (
            "Confirmed"
            if gate_state == "confirmed"
            else ("Blocked" if gate_state == "blocked" else "Watch")
        )
        chip_rows.append(
            _chip(
                f"MTF {gate_label}",
                "teal" if gate_state == "confirmed" else "amber",
            )
        )
    chips = "".join(chip_rows)

    metric_tiles = "".join(
        [
            _metric_tile("Confidence", _fmt_pct01(result.get("confidence", 0.0))),
            _metric_tile("Expected Move", _fmt_signed_pct(result.get("expected_3min_move_pct", 0.0))),
            _metric_tile("Position Size", f"{_fmt_num(result.get('position_size_pct', 0.0))}%"),
            _metric_tile("Gates Passing", f"{int(result.get('gates_passing', 0))}/12"),
            _metric_tile("Memory Similarity", _fmt_num(result.get("memory_similarity", 0.0), 3)),
            _metric_tile("Parse Quality", _fmt_num(result.get("latest_parse_quality", 0.0), 2), f"cv_quality={_fmt_num(module_rel.get('cv_quality', 0.0), 2)}"),
        ]
    )

    return (
        "<div class='pg-panel pg-signal-overview'>"
        "<div class='pg-section-title'>Signal Overview</div>"
        "<div class='pg-signal-shell'>"
        "<div class='pg-signal-main'>"
        "<div class='pg-action-row pg-signal-head'>"
        "<div class='pg-signal-primary'>"
        "<div class='pg-card-label'>808Fx Direction</div>"
        f"<div class='pg-action-label pg-{tone}'>{_escape_html(action)}</div>"
        f"<div class='pg-muted pg-signal-explanation'>{_escape_html(explanation)}</div>"
        "</div>"
        f"<div class='pg-confidence-pill pg-{tone}'><strong>{_fmt_pct01(result.get('confidence', 0.0))}</strong><span>confidence</span></div>"
        "</div>"
        f"<div class='pg-chip-row pg-signal-chips'>{chips}</div>"
        "<div class='pg-guidance-grid pg-guided-only'>"
        f"<div class='pg-guidance-card'><div class='pg-card-label'>What This Means</div><div class='pg-card-title{_tone_text_class(coaching_tone)}'>{_escape_html(coaching_title)}</div><div class='pg-card-note'>{_escape_html(coaching_body)}</div></div>"
        f"<div class='pg-guidance-card'><div class='pg-card-label'>Open Next</div><div class='pg-card-title{_tone_text_class(recommended_tone)}'>{_escape_html(recommended_panel)}</div><div class='pg-card-note'>{_escape_html(rationale)}</div><div class='pg-inline-actions' style='margin-top:10px;'><button type='button' class='pg-inline-button' data-help-open='read-signal'>Read the signal</button><button type='button' class='pg-inline-button' data-tone='secondary' data-help-open='workflow'>Workflow guide</button></div></div>"
        "</div>"
        "</div>"
        f"<div class='pg-metric-grid pg-signal-metrics'>{metric_tiles}</div>"
        "</div>"
    )


def build_model_council_html(result: dict[str, Any]) -> str:
    local_ensemble = cast(dict[str, Any], result.get("local_ensemble", {}))
    ensemble_view = cast(dict[str, Any], local_ensemble.get("ensemble", {}))
    models = cast(dict[str, dict[str, Any]], local_ensemble.get("models", {}))
    council_meta = cast(dict[str, Any], result.get("model_council", {}))
    if not models:
        source = str(council_meta.get("source", "none")).strip().lower()
        status = str(council_meta.get("status", "lazy_tab_loading")).strip()
        if source == "none" and status == "lazy_tab_loading":
            return _placeholder_panel("Model Council", "Open this tab to lazy-load the council worker and run the heavyweight ensemble once for the current static image.")
        return _placeholder_panel("Model Council", f"Model council is unavailable right now: {status}")

    rows = sorted(
        models.values(),
        key=lambda row: (
            not bool(row.get("live_enabled", False)),
            -float(row.get("dynamic_weight", row.get("shadow_weight", 0.0)) or 0.0),
        ),
    )
    cards: list[str] = []
    for row in rows:
        predicted_label = str(row.get("predicted_label", "HOLD")).upper()
        tone = _tone_class_for_action(predicted_label)
        live_enabled = bool(row.get("live_enabled", False))
        role = str(row.get("role", "generalist")).replace("_", " ").title()
        weight_value = float(row.get("dynamic_weight", row.get("shadow_weight", 0.0)) or 0.0)
        chips = _chip("LIVE", "teal") if live_enabled else _chip("SHADOW", "amber")
        cards.append(
            "<div class='pg-model-card'>"
            "<div class='pg-card-top'>"
            "<div>"
            f"<div class='pg-card-label'>{_escape_html(role)}</div>"
            f"<div class='pg-card-title'>{_escape_html(str(row.get('name', 'model')).upper())}</div>"
            f"<div class='pg-card-note'>{_escape_html(str(row.get('predicted_label', 'HOLD')).upper())} vote</div>"
            "</div>"
            f"<div>{chips}</div>"
            "</div>"
            "<div class='pg-meter'><span style='width:"
            f"{min(max(float(row.get('confidence', 0.0) or 0.0) * 100.0, 0.0), 100.0):.1f}%'></span></div>"
            "<div class='pg-card-kv'>"
            f"<div><span>Confidence</span><strong class='pg-{tone}'>{_fmt_pct01(row.get('confidence', 0.0))}</strong></div>"
            f"<div><span>Weight</span><strong>{_fmt_num(weight_value, 3)}</strong></div>"
            f"<div><span>Threshold</span><strong>{_fmt_num(row.get('decision_threshold', 0.5), 2)}</strong></div>"
            f"<div><span>Entropy</span><strong>{_fmt_num(row.get('entropy', 0.0), 2)}</strong></div>"
            "</div>"
            "</div>"
        )

    header_chips = "".join(
        [
            _chip(f"Lead Node {str(ensemble_view.get('champion_model', 'n/a')).upper()}", "teal"),
            _chip(f"Confirm Node {str(ensemble_view.get('confirmer_model', 'n/a')).upper()}", "soft"),
            _chip(f"Disagreement {_fmt_num(ensemble_view.get('disagreement', 0.0), 3)}", "amber"),
            _chip(f"Consensus {_fmt_pct01(ensemble_view.get('consensus_ratio', 0.0))}", "soft"),
            _chip(f"Source {str(council_meta.get('source', 'inline')).upper()}", "soft"),
            _chip(f"Status {str(council_meta.get('status', 'ready')).upper()}", "soft"),
        ]
    )
    return (
        "<div class='pg-panel'>"
        "<div class='pg-section-title'>Model Council</div>"
        f"<div class='pg-chip-row'>{header_chips}</div>"
        "<div class='pg-muted' style='margin-bottom:14px;'>The council compares specialist votes, live or shadow weighting, and disagreement so you can judge whether the heavyweight ensemble is reinforcing or questioning the active desk bias.</div>"
        f"<div class='pg-model-grid'>{''.join(cards)}</div>"
        "</div>"
    )


def build_forecast_panel_html(result: dict[str, Any]) -> str:
    if not result:
        return _placeholder_panel("Forecast & Risk", "Forecast distribution and system-health metrics will appear here.")

    forecast = cast(dict[str, Any], result.get("forecast_debug", {}))
    quantile_range = cast(list[float], result.get("quantile_range", [0.0, 0.0]))
    q05 = float(quantile_range[0]) if quantile_range else 0.0
    q95 = float(quantile_range[1]) if len(quantile_range) > 1 else q05
    module_rel = cast(dict[str, float], result.get("module_reliability", {}))
    branch_weights = cast(dict[str, float], result.get("branch_weights", {}))
    projection = cast(dict[str, Any], result.get("projection", {}))
    execution_readiness = float(forecast.get("execution_readiness", 0.0) or 0.0)
    if bool(forecast.get("force_hold", False)):
        posture_label = "Fail-safe holding"
        posture_note = "The forecast branch is still defensive, so patience is being preferred over a forced directional read."
        posture_tone = "amber"
    elif execution_readiness >= 0.78:
        posture_label = "Execution posture supportive"
        posture_note = "Forecast spread and readiness are supportive enough to treat the setup as actionable if the structural panels agree."
        posture_tone = "teal"
    elif execution_readiness >= 0.52:
        posture_label = "Forecast is mixed"
        posture_note = "There is directional lean, but the range still deserves a visual check in Compare Desk or rehearsal in Scenario Lab."
        posture_tone = "soft"
    else:
        posture_label = "Forecast is defensive"
        posture_note = "Movement is visible, but the risk posture is still too mixed to trust this panel on its own."
        posture_tone = "amber"

    tiles = "".join(
        [
            _metric_tile("q05", _fmt_signed_pct(q05)),
            _metric_tile("q95", _fmt_signed_pct(q95)),
            _metric_tile("A/D Indicator", _fmt_num(result.get("ad_indicator", 0.0), 3)),
            _metric_tile("Poly Slope", _fmt_num(result.get("poly_slope", 0.0), 3)),
            _metric_tile("Execution Ready", _fmt_num(forecast.get("execution_readiness", 0.0), 2)),
            _metric_tile("Force Hold", "YES" if bool(forecast.get("force_hold", False)) else "NO"),
            _metric_tile(
                "Projection Bias",
                f"{str(projection.get('direction', 'HOLD')).upper()} {_fmt_num(projection.get('confidence', 0.0), 2)}",
                f"dom={_fmt_num(projection.get('dominance', 0.0), 2)}",
            ),
        ]
    )
    chips = "".join(
        [
            _chip(posture_label, posture_tone),
            _chip(f"cv_quality {_fmt_num(module_rel.get('cv_quality', 0.0), 2)}", "teal"),
            _chip(f"structure {_fmt_num(module_rel.get('structure_consistency', 0.0), 2)}", "soft"),
            _chip(f"memory_novelty {_fmt_num(module_rel.get('memory_novelty', 0.0), 2)}", "amber"),
            _chip(f"memory_weight {_fmt_num(result.get('memory_effective_weight', 0.0), 2)}", "soft"),
        ]
    )
    branch_text = ", ".join(
        f"{str(name).replace('_', ' ')}={_fmt_num(value, 2)}" for name, value in sorted(branch_weights.items())
    ) or "No branch weighting reported."

    return (
        "<div class='pg-panel'>"
        "<div class='pg-section-title'>Forecast & Risk</div>"
        f"<div class='pg-chip-row'>{chips}</div>"
        f"<div class='pg-muted' style='margin-bottom:14px;'>{_escape_html(posture_note)}</div>"
        f"<div class='pg-metric-grid'>{tiles}</div>"
        f"<div class='pg-muted' style='margin-top:14px;'>Branch weights: {_escape_html(branch_text)}</div>"
        "</div>"
    )


def build_memory_panel_html(result: dict[str, Any], detail_depth: int = 4) -> str:
    if not result:
        return _placeholder_panel("Memory Recall", "Top recalled episodes and ambiguity controls will appear here.")

    memory_summary = cast(dict[str, Any], result.get("memory_ambiguity_summary", {}))
    episode_matches = cast(list[dict[str, Any]], result.get("memory_episode_matches", []))
    ambiguity = float(memory_summary.get("ambiguity", 0.0) or 0.0)
    consensus_ratio = float(memory_summary.get("consensus_ratio", 0.0) or 0.0)
    recall_count = int(result.get("memory_recall_count", 0) or 0)
    if recall_count == 0:
        memory_posture = "Novel setup"
        memory_note = "The memory bank did not surface a strong prior case, so the current chart should be treated as relatively fresh."
        memory_tone = "amber"
    elif consensus_ratio >= 0.74 and ambiguity <= 0.32:
        memory_posture = "Memory aligned"
        memory_note = "Past cases are pointing in a similar direction with relatively low ambiguity."
        memory_tone = "teal"
    else:
        memory_posture = "Memory is mixed"
        memory_note = "Recalled cases exist, but their directional agreement is not strong enough to treat memory as a clean confirmer."
        memory_tone = "amber"
    header_chips = "".join(
        [
            _chip(memory_posture, memory_tone),
            _chip(f"Direction {str(result.get('memory_direction', 'HOLD')).upper()}", _tone_class_for_action(str(result.get("memory_direction", "HOLD")).upper())),
            _chip(f"Recall {recall_count}", "soft"),
            _chip(f"Consensus {_fmt_pct01(consensus_ratio)}", "soft"),
        ]
    )
    tiles = "".join(
        [
            _metric_tile("Top Similarity", _fmt_num(result.get("memory_similarity", 0.0), 3)),
            _metric_tile("Direction", str(result.get("memory_direction", "HOLD")).upper()),
            _metric_tile("Recall Count", str(int(result.get("memory_recall_count", 0) or 0))),
            _metric_tile("Ambiguity", _fmt_num(memory_summary.get("ambiguity", 0.0), 2)),
            _metric_tile("Entropy", _fmt_num(memory_summary.get("label_entropy", 0.0), 2)),
            _metric_tile("Consensus", _fmt_pct01(memory_summary.get("consensus_ratio", 0.0))),
        ]
    )
    cards: list[str] = []
    for match in episode_matches[: max(1, detail_depth)]:
        title = str(match.get("episode_id", match.get("entry_id", "episode"))).strip() or "episode"
        label = str(match.get("label", match.get("dominant_label", "N/A"))).upper()
        local_phase = str(match.get("local_phase", match.get("phase", "unknown"))).replace("_", " ")
        intent = str(match.get("intent_next", match.get("intent", "unknown"))).replace("_", " ")
        similarity = float(match.get("similarity", match.get("top_similarity", match.get("score", 0.0))) or 0.0)
        cards.append(
            "<div class='pg-memory-card'>"
            f"<div class='pg-card-label'>{_escape_html(label)}</div>"
            f"<div class='pg-card-title'>{_escape_html(title)}</div>"
            f"<div class='pg-card-note'>phase={_escape_html(local_phase)} | intent={_escape_html(intent)}</div>"
            f"<div class='pg-meter'><span style='width:{min(max(similarity * 100.0, 0.0), 100.0):.1f}%'></span></div>"
            f"<div class='pg-card-note' style='margin-top:8px;'>similarity {_fmt_num(similarity, 3)}</div>"
            "</div>"
        )

    if not cards:
        cards.append(
            "<div class='pg-memory-card'><div class='pg-card-title'>No recall episodes</div>"
            "<div class='pg-card-note'>The memory bank did not surface a close match for this chart.</div></div>"
        )

    return (
        "<div class='pg-panel'>"
        "<div class='pg-section-title'>Memory Recall</div>"
        f"<div class='pg-chip-row'>{header_chips}</div>"
        f"<div class='pg-muted' style='margin-bottom:14px;'>{_escape_html(memory_note)}</div>"
        f"<div class='pg-metric-grid'>{tiles}</div>"
        f"<div class='pg-memory-grid' style='margin-top:14px;'>{''.join(cards)}</div>"
        "</div>"
    )


def build_gate_matrix_html(result: dict[str, Any]) -> str:
    gate_details = cast(list[dict[str, Any]], result.get("gate_details", []))
    if not gate_details:
        return _placeholder_panel("Gate Matrix", "Twelve gate outputs will appear here after a signal run.")

    shap = cast(dict[str, float], result.get("shap_contributions", {}))
    header_chips = "".join(
        [
            _chip(f"Passing {int(result.get('gates_passing', 0))}/12", "teal"),
            _chip(
                f"Consensus {'OK' if bool(result.get('consensus_ok', False)) else 'No'}",
                "soft" if bool(result.get("consensus_ok", False)) else "amber",
            ),
            _chip(
                f"Support {'OK' if bool(result.get('support_gates_ok', True)) else 'Watch'}",
                "soft" if bool(result.get("support_gates_ok", True)) else "amber",
            ),
        ]
    )
    cards: list[str] = []
    for gate in gate_details:
        score = float(gate.get("score", 0.0) or 0.0)
        pass_fail = bool(gate.get("pass_fail", False))
        detail = cast(dict[str, Any], gate.get("detail", {}))
        detail_preview = ", ".join(
            f"{str(key).replace('_', ' ')}={_truncate_text(value, 32)}"
            for key, value in list(detail.items())[:3]
        ) or "No detail payload."
        name = str(gate.get("name", "gate")).replace("_", " ").title()
        shap_value = float(shap.get(str(gate.get("name", "")), 0.0) or 0.0)
        cards.append(
            "<div class='pg-gate-card'>"
            "<div class='pg-card-top'>"
            "<div>"
            f"<div class='pg-card-label'>{_escape_html('Pass' if pass_fail else 'Watch')}</div>"
            f"<div class='pg-card-title'>{_escape_html(name)}</div>"
            "</div>"
            f"<div class='pg-card-title {'pg-pass' if pass_fail else 'pg-fail'}'>{_fmt_pct01(score)}</div>"
            "</div>"
            f"<div class='pg-meter'><span style='width:{min(max(score * 100.0, 0.0), 100.0):.1f}%'></span></div>"
            "<div class='pg-card-kv'>"
            f"<div><span>SHAP</span><strong>{_fmt_num(shap_value, 3)}</strong></div>"
            f"<div><span>Status</span><strong class='{'pg-pass' if pass_fail else 'pg-fail'}'>{'PASS' if pass_fail else 'WAIT'}</strong></div>"
            "</div>"
            f"<div class='pg-card-note' style='margin-top:10px;'>{_escape_html(detail_preview)}</div>"
            "</div>"
        )

    support_gate_details = cast(list[dict[str, Any]], result.get("support_gate_details", []))
    support_cards = ""
    if support_gate_details:
        support_rows: list[str] = []
        for gate in support_gate_details:
            score = float(gate.get("score", 0.0) or 0.0)
            pass_fail = bool(gate.get("pass_fail", False))
            label = str(gate.get("name", "support gate")).replace("_", " ").title()
            support_rows.append(
                "<div class='pg-card-kv'>"
                f"<div><span>{_escape_html(label)}</span><strong class='{'pg-pass' if pass_fail else 'pg-fail'}'>{'PASS' if pass_fail else 'WATCH'} {_fmt_pct01(score)}</strong></div>"
                "</div>"
            )
        support_cards = (
            "<div class='pg-panel' style='margin-top:16px;'>"
            "<div class='pg-section-title'>Support Checks</div>"
            f"{''.join(support_rows)}"
            "</div>"
        )

    gates_passing = int(result.get("gates_passing", 0) or 0)
    if gates_passing >= 9 and bool(result.get("consensus_ok", False)):
        summary_text = "Most structural rails are open, so this panel is behaving more like confirmation than rejection."
    elif gates_passing >= 6:
        summary_text = "The desk has directional evidence, but several gates are still working as caution rails."
    else:
        summary_text = "The gate system is still rejecting or heavily filtering the setup, so patience is the safer read."

    return (
        "<div class='pg-panel'>"
        "<div class='pg-section-title'>Gate Matrix</div>"
        f"<div class='pg-chip-row'>{header_chips}</div>"
        f"<div class='pg-muted' style='margin-bottom:14px;'>{_escape_html(summary_text)}</div>"
        f"<div class='pg-gate-grid'>{''.join(cards)}</div>"
        "</div>"
        f"{support_cards}"
    )


def _render_pattern_group(title: str, detections: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for detection in detections[:6]:
        pattern = str(detection.get("pattern", "unknown")).replace("_", " ")
        confidence = float(detection.get("confidence", 0.0) or 0.0)
        role = str(detection.get("sequence_role", "global")).replace("_", " ")
        rows.append(
            "<div class='pg-evidence-card'>"
            f"<div class='pg-card-label'>{_escape_html(role)}</div>"
            f"<div class='pg-card-title'>{_escape_html(pattern.title())}</div>"
            f"<div class='pg-card-note'>confidence {_fmt_pct01(confidence)}</div>"
            f"<div class='pg-meter'><span style='width:{min(max(confidence * 100.0, 0.0), 100.0):.1f}%'></span></div>"
            "</div>"
        )
    if not rows:
        rows.append(
            "<div class='pg-evidence-card'><div class='pg-card-title'>No signals</div>"
            "<div class='pg-card-note'>Nothing surfaced in this evidence branch.</div></div>"
        )
    return (
        "<div>"
        f"<div class='pg-section-title'>{_escape_html(title)}</div>"
        f"<div class='pg-evidence-grid'>{''.join(rows)}</div>"
        "</div>"
    )


def build_evidence_panel_html(
    cv_debug: dict[str, Any],
    result: dict[str, Any],
    detail_depth: int = 6,
) -> str:
    if not cv_debug:
        return _placeholder_panel("Evidence Panel", "Pattern and transition evidence will appear here after a run.")

    transitions = cast(dict[str, float], cv_debug.get("sequence_transition_probabilities", {}))
    projection = cast(dict[str, Any], cv_debug.get("projection", {}))
    transition_chips = "".join(
        _chip(f"{str(name).replace('_', ' ')} {_fmt_pct01(value)}", "soft")
        for name, value in sorted(transitions.items(), key=lambda item: float(item[1]), reverse=True)[:5]
    ) or _chip("No transition summary", "amber")
    current_box = cast(dict[str, Any], cv_debug.get("current_box", {}))
    next_boxes = cast(list[dict[str, Any]], cv_debug.get("next_box_hypotheses", []))
    if current_box:
        transition_chips += _chip(
            f"Current {str(current_box.get('box_type', 'balance')).title()} {str(current_box.get('direction', 'HOLD')).upper()}",
            "teal" if bool(current_box.get("contains_consolidation", False)) else "soft",
        )
    if next_boxes:
        projected = next_boxes[0]
        transition_chips += _chip(
            f"Projected {str(projected.get('box_type', 'balance')).title()} {str(projected.get('direction', 'HOLD')).upper()}",
            "amber",
        )
    if projection:
        transition_chips += _chip(
            f"Projection Bias {str(projection.get('direction', 'HOLD')).upper()} {_fmt_num(projection.get('confidence', 0.0), 2)}",
            _tone_class_for_action(str(projection.get("direction", "HOLD")).upper()),
        )

    summary_text = human_readable_summary(result)
    global_rows = cast(list[dict[str, Any]], cv_debug.get("overlay_visible_global_top", [])) or cast(list[dict[str, Any]], cv_debug.get("global_detections_top", []))
    latest_rows = cast(list[dict[str, Any]], cv_debug.get("overlay_visible_latest_top", [])) or cast(list[dict[str, Any]], cv_debug.get("latest_branch_top", []))
    synthetic_rows = cast(list[dict[str, Any]], cv_debug.get("overlay_visible_synthetic_top", [])) or cast(list[dict[str, Any]], cv_debug.get("synthetic_signals_top", []))
    evidence_groups = "".join(
        [
            _render_pattern_group("Global Detections", global_rows[: max(1, detail_depth)]),
            _render_pattern_group("Latest Branch", latest_rows[: max(1, detail_depth)]),
            _render_pattern_group("Synthetic Signals", synthetic_rows[: max(1, detail_depth)]),
        ]
    )
    return (
        "<div class='pg-panel'>"
        "<div class='pg-section-title'>Evidence Panel</div>"
        f"<div class='pg-chip-row'>{transition_chips}</div>"
        f"<div class='pg-muted' style='margin-bottom:14px;'>{_escape_html(_truncate_text(summary_text, 320))}</div>"
        f"{evidence_groups}"
        "</div>"
    )


def build_debug_console_html(
    result: dict[str, Any],
    cv_debug: dict[str, Any],
    render_config: Mapping[str, Any] | None = None,
) -> str:
    if not result:
        return _placeholder_panel("Diagnostics Cockpit", "Runtime diagnostics will appear here after the first signal pass.")

    config = dict(render_config or {})
    detail_depth = int(config.get("debug_depth", 6) or 6)
    chart_state = cast(dict[str, Any], result.get("chart_state", {}))
    memory_summary = cast(dict[str, Any], result.get("memory_ambiguity_summary", {}))
    transitions = cast(dict[str, float], cv_debug.get("sequence_transition_probabilities", {}))
    current_box = cast(dict[str, Any], cv_debug.get("current_box", {}))
    next_boxes = cast(list[dict[str, Any]], cv_debug.get("next_box_hypotheses", []))
    geometry = cast(dict[str, Any], cv_debug.get("chart_geometry", {}))
    branch_weights = cast(dict[str, float], result.get("branch_weights", {}))
    gate_details = cast(list[dict[str, Any]], result.get("gate_details", []))
    failing_gates = [gate for gate in gate_details if not bool(gate.get("pass_fail", False))]
    transitions_sorted = sorted(transitions.items(), key=lambda item: float(item[1]), reverse=True)[: max(3, detail_depth)]

    top_box_rows = "".join(
        (
            "<li>"
            f"{_escape_html(str(box.get('box_type', 'balance')).replace('_', ' '))} "
            f"{_escape_html(str(box.get('direction', 'HOLD')).upper())} "
            f"conf={_fmt_num(box.get('confidence', 0.0), 2)} "
            f"dom={_fmt_num(box.get('dominance_gap', 0.0), 2)}"
            "</li>"
        )
        for box in next_boxes[: max(1, detail_depth)]
    ) or "<li>No projected boxes reported.</li>"
    transition_rows = "".join(
        f"<li>{_escape_html(str(name).replace('_', ' '))}: {_fmt_pct01(value)}</li>"
        for name, value in transitions_sorted
    ) or "<li>No transition probabilities reported.</li>"
    branch_rows = "".join(
        f"<li>{_escape_html(str(name).replace('_', ' '))}: {_fmt_num(value, 3)}</li>"
        for name, value in sorted(branch_weights.items(), key=lambda item: float(item[1]), reverse=True)
    ) or "<li>No branch weighting available.</li>"
    failing_rows = "".join(
        (
            "<li>"
            f"{_escape_html(str(gate.get('name', 'gate')).replace('_', ' '))} "
            f"score={_fmt_pct01(gate.get('score', 0.0))}"
            "</li>"
        )
        for gate in failing_gates[: max(1, detail_depth)]
    ) or "<li>No failing gates in the current pass.</li>"

    chips = "".join(
        [
            _chip(f"Action {str(result.get('action', 'HOLD')).upper()}", _tone_class_for_action(str(result.get("action", "HOLD")).upper())),
            _chip(f"Consensus {'OK' if bool(result.get('consensus_ok', False)) else 'Watch'}", "teal" if bool(result.get("consensus_ok", False)) else "amber"),
            _chip(f"Geometry conflict {'YES' if bool(result.get('geometry_conflict', False)) else 'NO'}", "amber" if bool(result.get("geometry_conflict", False)) else "soft"),
            _chip(f"Strict fail-closed {'YES' if bool(result.get('strict_cv_fail_closed', False)) else 'NO'}", "amber" if bool(result.get("strict_cv_fail_closed", False)) else "soft"),
            _chip(f"Visible detections {int(cv_debug.get('visible_detection_count', 0) or 0)}", "soft"),
        ]
    )
    tiles = "".join(
        [
            _metric_tile("Run Timestamp", _truncate_text(result.get("timestamp", "unknown"), 24)),
            _metric_tile("Confidence", _fmt_pct01(result.get("confidence", 0.0))),
            _metric_tile("Parse Quality", _fmt_num(result.get("latest_parse_quality", 0.0), 3)),
            _metric_tile("Latest Candle", _fmt_num(result.get("latest_candle_confidence", 0.0), 3)),
            _metric_tile("Memory Similarity", _fmt_num(result.get("memory_similarity", 0.0), 3)),
            _metric_tile("Recall Count", str(int(result.get("memory_recall_count", 0) or 0))),
            _metric_tile("Projection Conf", _fmt_num(cast(dict[str, Any], result.get("projection", {})).get("confidence", 0.0), 3)),
            _metric_tile("Controls", f"{str(config.get('overlay_mode', 'history-plus-projection')).replace('-', ' ')} / d{detail_depth}"),
        ]
    )

    cards = "".join(
        [
            (
                "<div class='pg-debug-card'>"
                "<div class='pg-card-label'>Current Box</div>"
                f"<div class='pg-card-title'>{_escape_html(str(current_box.get('box_type', 'balance')).replace('_', ' ').title())}</div>"
                f"<div class='pg-card-note'>{_escape_html(str(current_box.get('direction', 'HOLD')).upper())} | conf {_fmt_num(current_box.get('confidence', 0.0), 2)}</div>"
                "<ul class='pg-debug-list'>"
                f"<li>sequence index: {int(current_box.get('sequence_index', 0) or 0)}</li>"
                f"<li>path clarity: {_fmt_num(chart_state.get('path_clarity', 0.0), 2)}</li>"
                f"<li>setup: {_escape_html(str(chart_state.get('structure_setup', 'none')).replace('_', ' '))}</li>"
                "</ul>"
                "</div>"
            ),
            (
                "<div class='pg-debug-card'>"
                "<div class='pg-card-label'>Projected Boxes</div>"
                f"<div class='pg-card-title'>{len(next_boxes)} candidates</div>"
                f"<ul class='pg-debug-list'>{top_box_rows}</ul>"
                "</div>"
            ),
            (
                "<div class='pg-debug-card'>"
                "<div class='pg-card-label'>Transitions</div>"
                f"<div class='pg-card-title'>{_escape_html(str(chart_state.get('projection_bias_direction', 'HOLD')).upper())} bias</div>"
                f"<ul class='pg-debug-list'>{transition_rows}</ul>"
                "</div>"
            ),
            (
                "<div class='pg-debug-card'>"
                "<div class='pg-card-label'>Memory Ambiguity</div>"
                f"<div class='pg-card-title'>{_escape_html(str(memory_summary.get('dominant_label', result.get('memory_direction', 'HOLD'))).upper())}</div>"
                "<ul class='pg-debug-list'>"
                f"<li>ambiguity: {_fmt_num(memory_summary.get('ambiguity', 0.0), 3)}</li>"
                f"<li>entropy: {_fmt_num(memory_summary.get('label_entropy', 0.0), 3)}</li>"
                f"<li>consensus ratio: {_fmt_pct01(memory_summary.get('consensus_ratio', 0.0))}</li>"
                f"<li>mixed labels: {'yes' if bool(memory_summary.get('mixed_labels', False)) else 'no'}</li>"
                "</ul>"
                "</div>"
            ),
            (
                "<div class='pg-debug-card'>"
                "<div class='pg-card-label'>Chart Geometry</div>"
                f"<div class='pg-card-title'>{int(geometry.get('recent_candle_count', 0) or 0)} candles</div>"
                "<ul class='pg-debug-list'>"
                f"<li>geometry conf: {_fmt_num(geometry.get('geometry_confidence', 0.0), 3)}</li>"
                f"<li>body pct: {_fmt_num(geometry.get('body_height_pct', 0.0), 3)}</li>"
                f"<li>upper wick pct: {_fmt_num(geometry.get('upper_wick_pct', 0.0), 3)}</li>"
                f"<li>lower wick pct: {_fmt_num(geometry.get('lower_wick_pct', 0.0), 3)}</li>"
                "</ul>"
                "</div>"
            ),
            (
                "<div class='pg-debug-card'>"
                "<div class='pg-card-label'>Branch Weights</div>"
                f"<div class='pg-card-title'>{int(result.get('gates_passing', 0) or 0)}/12 gates passing</div>"
                f"<ul class='pg-debug-list'>{branch_rows}</ul>"
                "</div>"
            ),
            (
                "<div class='pg-debug-card'>"
                "<div class='pg-card-label'>Gate Watchlist</div>"
                f"<div class='pg-card-title'>{len(failing_gates)} pending gates</div>"
                f"<ul class='pg-debug-list'>{failing_rows}</ul>"
                "</div>"
            ),
        ]
    )

    return (
        "<div class='pg-panel'>"
        "<div class='pg-section-title'>Diagnostics Cockpit</div>"
        f"<div class='pg-chip-row'>{chips}</div>"
        f"<div class='pg-metric-grid'>{tiles}</div>"
        f"<div class='pg-debug-grid'>{cards}</div>"
        "</div>"
    )


def build_runtime_audit_payload(
    result: dict[str, Any],
    cv_debug: dict[str, Any],
    render_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    session_snapshot = _get_session_snapshot()
    session_entries = cast(list[dict[str, Any]], session_snapshot.get("entries", []))[-20:]
    session_snapshot["entries"] = [
        {key: value for key, value in entry.items() if key not in {"thumbnail_uri", "thumbnail_path"}}
        for entry in session_entries
    ]
    return {
        "generated_at": utc_now_iso(),
        "ui_render_state": dict(render_config or {}),
        "session": session_snapshot,
        "zone_memory_count": len(_load_zone_memory()),
        "cv_debug": cv_debug,
        "result": result,
    }


def _empty_audit_payload() -> dict[str, Any]:
    return {}


def _empty_heatmap_outputs() -> tuple[Any, str]:
    return (
        None,
        _placeholder_panel("Confidence Heatmap", "Heat concentration will appear here after the first inference."),
    )


def _empty_compare_desk_html() -> str:
    return _placeholder_panel("Compare Desk", "Raw, focused, and annotated compare views will appear here.")


# ------------------------------------------------------------------
# Gradio callbacks
# ------------------------------------------------------------------


def _empty_workspace_outputs() -> tuple[Any, ...]:
    empty = _placeholder_panel("Awaiting Chart Pair", "Upload exactly two chart images, with the higher timeframe first and the lower timeframe second.")
    return (
        None,
        None,
        None,
        empty,
        _placeholder_panel("Model Council", "Open this tab to lazy-load the council worker and run the heavyweight ensemble once for the active static image."),
        _placeholder_panel("Forecast & Risk", "Forecast distribution and system reliability will appear here."),
        _placeholder_panel("Memory Recall", "Local memory retrieval will appear here."),
        _placeholder_panel("Gate Matrix", "Gate pass/fail structure will appear here."),
        _placeholder_panel("Evidence Panel", "Chart evidence and transition traces will appear here."),
        _placeholder_panel("Diagnostics Cockpit", "Runtime diagnostics will appear here after the first signal pass."),
        {},
        _placeholder_panel("Timeframe Overlays", "Higher and lower timeframe overlays will appear here after a run."),
        build_control_status_html(None, None),
        _placeholder_panel("Adaptive Guidance", "The desk will recommend the next best workspace after the first run."),
        None,
        _placeholder_panel("Confidence Heatmap", "Heat concentration will appear here after the first inference."),
        _placeholder_panel("Compare Desk", "Raw, focused, and annotated compare views will appear here."),
        _placeholder_panel("Zone Library", "Saved support, resistance, and reaction zones will appear here."),
        _placeholder_panel("Session Timeline", "Session captures will appear here in order."),
        _placeholder_panel("Pattern Browser", "Similar session cases will appear here once enough charts have been reviewed."),
        None,
        "Upload exactly two chart images: higher timeframe first, lower timeframe second.",
        {},
        None,
        "",
    )


def _render_workspace_from_result(
    result: dict[str, Any],
    source_image_state: Any,
    render_config: Mapping[str, Any],
    *,
    precomputed_overlay: Any = None,
    precomputed_gauge: Any = None,
    precomputed_skill_fig: Any = None,
    include_audit: bool = True,
    include_heatmap: bool = True,
    include_compare: bool = True,
) -> tuple[Any, ...]:
    personal = _get_personal()
    source_image = _image_from_state(source_image_state)
    if source_image is None:
        return _empty_workspace_outputs()[:-3]
    display_result = _sanitize_result_for_ui(result)
    overlay = cast(
        Any,
        precomputed_overlay
        if precomputed_overlay is not None
        else _build_overlay_image(
            source_image,
            result,
            overlay_mode=str(render_config.get("overlay_mode", "history-plus-projection")),
            min_conf_global=float(render_config.get("min_conf_global", 0.42) or 0.42),
            min_conf_latest=float(render_config.get("min_conf_latest", 0.50) or 0.50),
            history_limit=int(render_config.get("history_depth", 8) or 8),
            label_budget=int(render_config.get("label_density", 10) or 10),
            projection_confidence_floor=float(render_config.get("projection_focus", 0.35) or 0.35),
        ),
    )
    cv_debug = pg_main.build_cv_debug_payload(display_result, render_config=render_config)
    gauge = precomputed_gauge if precomputed_gauge is not None else _build_decision_gauge_from_result(display_result)
    skill_fig = precomputed_skill_fig if precomputed_skill_fig is not None else _build_skill_figure(personal, display_result)
    heatmap_payload = (
        _build_confidence_heatmap_payload(display_result, source_image)
        if include_heatmap or include_compare
        else None
    )
    heatmap_image = _compose_confidence_heatmap_image(heatmap_payload, source_image) if heatmap_payload is not None else None
    zone_learning = cast(dict[str, Any], display_result.get("zone_learning", {}))
    multi_timeframe = cast(dict[str, Any], display_result.get("multi_timeframe", {}))
    heatmap_output, heatmap_summary = (
        (heatmap_image, _build_heatmap_summary_html(display_result, source_image, heatmap_payload=heatmap_payload))
        if include_heatmap
        else _empty_heatmap_outputs()
    )
    compare_output = (
        _build_compare_desk_html(display_result, source_image, overlay, heatmap_image, render_config=render_config)
        if include_compare and heatmap_image is not None
        else _empty_compare_desk_html()
    )
    timeframe_overlay_output = _build_timeframe_overlay_gallery_html(display_result)
    analyst_brief = (
        human_readable_summary(display_result)
        + "\n\nZone Learning:\n"
        + (
            f"matches={int(zone_learning.get('match_count', 0) or 0)} "
            f"preferred={str(zone_learning.get('preferred_action', 'HOLD')).upper()} "
            f"alignment={float(zone_learning.get('alignment_score', 0.0) or 0.0):.2f}"
        )
        + (
            "\n\nMulti-Timeframe:\n" + str(multi_timeframe.get("summary", "single-timeframe run"))
            if multi_timeframe
            else ""
        )
        + "\n\nStructured CV Read:\n"
        + explain_cv_debug_payload(cv_debug, result=display_result)
        + "\n\nActive View Controls:\n"
        + (
            f"overlay_mode={render_config.get('overlay_mode', 'history-plus-projection')} "
            f"global_min={float(render_config.get('min_conf_global', 0.42) or 0.42):.2f} "
            f"latest_min={float(render_config.get('min_conf_latest', 0.50) or 0.50):.2f} "
            f"history_depth={int(render_config.get('history_depth', 8) or 8)} "
            f"label_density={int(render_config.get('label_density', 10) or 10)} "
            f"projection_floor={float(render_config.get('projection_focus', 0.35) or 0.35):.2f} "
            f"debug_depth={int(render_config.get('debug_depth', 6) or 6)}"
        )
    )
    return (
        overlay,
        gauge,
        skill_fig,
        build_signal_overview_html(display_result),
        build_model_council_html(display_result),
        build_forecast_panel_html(display_result),
        build_memory_panel_html(display_result, detail_depth=max(2, int(render_config.get("debug_depth", 6) or 6) - 1)),
        build_gate_matrix_html(display_result),
        build_evidence_panel_html(cv_debug, display_result, detail_depth=int(render_config.get("label_density", 10) or 10)),
        build_debug_console_html(display_result, cv_debug, render_config=render_config),
        build_runtime_audit_payload(display_result, cv_debug, render_config=render_config)
        if include_audit
        else _empty_audit_payload(),
        timeframe_overlay_output,
        build_control_status_html(display_result, render_config=render_config),
        _build_adaptive_guidance_html(display_result),
        heatmap_output,
        heatmap_summary,
        compare_output,
        _build_zone_library_html(display_result),
        _build_session_timeline_html(),
        _build_pattern_memory_browser_html(display_result),
        _build_zone_editor_value(source_image_state, base_image=overlay if isinstance(overlay, Image.Image) else None),
        analyst_brief,
    )


def _render_live_preview_from_result(
    result_state: dict[str, Any],
    source_image_state: Any,
    render_config: Mapping[str, Any],
    *,
    include_compare: bool = False,
) -> tuple[Any, ...]:
    source_image = _image_from_state(source_image_state)
    if source_image is None:
        return (
            None,
            _placeholder_panel("Memory Recall", "Local memory retrieval will appear here."),
            _placeholder_panel("Evidence Panel", "Chart evidence and transition traces will appear here."),
            _placeholder_panel("Diagnostics Cockpit", "Runtime diagnostics will appear here after the first signal pass."),
            build_control_status_html(None, None),
            _gr_skip(),
        )

    display_result = _sanitize_result_for_ui(result_state)
    overlay = _build_overlay_image(
        source_image,
        result_state,
        overlay_mode=str(render_config.get("overlay_mode", "history-plus-projection")),
        min_conf_global=float(render_config.get("min_conf_global", 0.42) or 0.42),
        min_conf_latest=float(render_config.get("min_conf_latest", 0.50) or 0.50),
        history_limit=int(render_config.get("history_depth", 8) or 8),
        label_budget=int(render_config.get("label_density", 10) or 10),
        projection_confidence_floor=float(render_config.get("projection_focus", 0.35) or 0.35),
    )
    cv_debug = pg_main.build_cv_debug_payload(display_result, render_config=render_config)
    compare_output: Any = _gr_skip()
    if include_compare:
        heatmap_image = _build_confidence_heatmap_image(display_result, source_image)
        compare_output = _build_compare_desk_html(
            display_result,
            source_image,
            overlay,
            heatmap_image,
            render_config=render_config,
        )
    return (
        overlay,
        build_memory_panel_html(display_result, detail_depth=max(2, int(render_config.get("debug_depth", 6) or 6) - 1)),
        build_evidence_panel_html(cv_debug, display_result, detail_depth=int(render_config.get("label_density", 10) or 10)),
        build_debug_console_html(display_result, cv_debug, render_config=render_config),
        build_control_status_html(display_result, render_config=render_config),
        compare_output,
    )


def load_audit_tab(
    result_state: dict[str, Any] | None,
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
    debug_depth: float,
) -> tuple[dict[str, Any], bool]:
    if not result_state:
        return _empty_audit_payload(), True
    render_config = _build_render_config(
        overlay_mode=overlay_mode,
        min_conf_global=min_conf_global,
        min_conf_latest=min_conf_latest,
        history_depth=history_depth,
        label_density=label_density,
        projection_focus=projection_focus,
        debug_depth=debug_depth,
    )
    display_result = _sanitize_result_for_ui(result_state)
    cv_debug = pg_main.build_cv_debug_payload(display_result, render_config=render_config)
    return build_runtime_audit_payload(display_result, cv_debug, render_config=render_config), True


def load_heatmap_tab(
    result_state: dict[str, Any] | None,
    source_image_state: Any,
) -> tuple[Any, str, bool]:
    source_image = _image_from_state(source_image_state)
    if not result_state or source_image is None:
        heatmap_image, heatmap_summary = _empty_heatmap_outputs()
        return heatmap_image, heatmap_summary, True
    display_result = _sanitize_result_for_ui(result_state)
    heatmap_payload = _build_confidence_heatmap_payload(display_result, source_image)
    return (
        _compose_confidence_heatmap_image(heatmap_payload, source_image),
        _build_heatmap_summary_html(display_result, source_image, heatmap_payload=heatmap_payload),
        True,
    )


def load_compare_desk_tab(
    result_state: dict[str, Any] | None,
    source_image_state: Any,
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
    debug_depth: float,
) -> tuple[str, bool]:
    source_image = _image_from_state(source_image_state)
    if not result_state or source_image is None:
        return _empty_compare_desk_html(), True
    render_config = _build_render_config(
        overlay_mode=overlay_mode,
        min_conf_global=min_conf_global,
        min_conf_latest=min_conf_latest,
        history_depth=history_depth,
        label_density=label_density,
        projection_focus=projection_focus,
        debug_depth=debug_depth,
    )
    display_result = _sanitize_result_for_ui(result_state)
    overlay = _build_overlay_image(
        source_image,
        result_state,
        overlay_mode=str(render_config.get("overlay_mode", "history-plus-projection")),
        min_conf_global=float(render_config.get("min_conf_global", 0.42) or 0.42),
        min_conf_latest=float(render_config.get("min_conf_latest", 0.50) or 0.50),
        history_limit=int(render_config.get("history_depth", 8) or 8),
        label_budget=int(render_config.get("label_density", 10) or 10),
        projection_confidence_floor=float(render_config.get("projection_focus", 0.35) or 0.35),
    )
    heatmap_image = _build_confidence_heatmap_image(display_result, source_image)
    return _build_compare_desk_html(
        display_result,
        source_image,
        overlay,
        heatmap_image,
        render_config=render_config,
    ), True


def load_model_council_tab(
    result_state: dict[str, Any] | None,
    source_image_state: Any,
    active_file_path_state: str,
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
    debug_depth: float,
    audit_tab_loaded: bool,
    heatmap_tab_loaded: bool,
    compare_tab_loaded: bool,
) -> tuple[Any, ...]:
    if not result_state:
        return _empty_workspace_outputs()

    render_config = _build_render_config(
        overlay_mode=overlay_mode,
        min_conf_global=min_conf_global,
        min_conf_latest=min_conf_latest,
        history_depth=history_depth,
        label_density=label_density,
        projection_focus=projection_focus,
        debug_depth=debug_depth,
    )
    source_image = _image_from_state(source_image_state)
    if source_image is None:
        return _empty_workspace_outputs()

    local_ensemble = cast(dict[str, Any], result_state.get("local_ensemble", {}))
    existing_models = cast(dict[str, Any], local_ensemble.get("models", {}))
    if existing_models:
        rendered = _render_workspace_from_result(
            result_state,
            source_image_state,
            render_config,
            include_audit=bool(audit_tab_loaded),
            include_heatmap=bool(heatmap_tab_loaded),
            include_compare=bool(compare_tab_loaded),
        )
        return (*rendered, result_state, source_image_state, str(active_file_path_state or ""))

    multi_timeframe = cast(dict[str, Any], result_state.get("multi_timeframe", {}))
    entries = cast(list[dict[str, Any]], multi_timeframe.get("entries", []))
    bundle_paths = [
        str(entry.get("file_path", "") or "").strip()
        for entry in entries
        if str(entry.get("file_path", "") or "").strip()
    ]
    active_file_path = str(active_file_path_state or "").strip()
    if not bundle_paths and active_file_path:
        bundle_paths = [active_file_path]
    if not bundle_paths:
        rendered = _render_workspace_from_result(
            result_state,
            source_image_state,
            render_config,
            include_audit=bool(audit_tab_loaded),
            include_heatmap=bool(heatmap_tab_loaded),
            include_compare=bool(compare_tab_loaded),
        )
        return (*rendered, result_state, source_image_state, active_file_path)

    labels = ["Higher TF", "Lower TF", "Frame 3", "Frame 4"]
    analyzed: list[dict[str, Any]] = []
    try:
        for index, file_path in enumerate(bundle_paths):
            refined_result, overlay_image, _gauge_unused, _skill_unused = pg_main.run_inference(
                file_path,
                annotation_text="",
                overlay_mode=str(render_config["overlay_mode"]),
                min_conf_global=float(render_config["min_conf_global"]),
                min_conf_latest=float(render_config["min_conf_latest"]),
                history_depth=int(render_config["history_depth"]),
                label_density=int(render_config["label_density"]),
                projection_focus=float(render_config["projection_focus"]),
                side_effect_free=True,
                use_local_ensemble=True,
            )
            refined_source_state = _source_image_to_state(file_path)
            analyzed.append(
                {
                    "result": refined_result,
                    "file_path": file_path,
                    "source_image_state": refined_source_state,
                    "compare_entry": _build_timeframe_compare_entry(
                        refined_result,
                        refined_source_state,
                        file_path,
                        labels[min(index, len(labels) - 1)],
                        overlay_image=overlay_image,
                        render_config=render_config,
                    ),
                }
            )
    except Exception as exc:
        logger.exception("Lazy model council load failed: %s", exc)
        raise gr.Error(f"Model Council lazy load failed: {exc}") from exc

    refined_bundle_result = (
        _build_multi_timeframe_result(analyzed)
        if len(analyzed) > 1
        else cast(dict[str, Any], analyzed[0]["result"])
    )
    refined_source_state = analyzed[-1]["source_image_state"]
    refined_file_path = str(analyzed[-1]["file_path"])
    rendered = _render_workspace_from_result(
        refined_bundle_result,
        refined_source_state,
        render_config,
        include_audit=bool(audit_tab_loaded),
        include_heatmap=bool(heatmap_tab_loaded),
        include_compare=bool(compare_tab_loaded),
    )
    return (*rendered, refined_bundle_result, refined_source_state, refined_file_path)


def _render_scenario_lab_from_result(
    result_state: dict[str, Any],
    source_image_state: Any,
    render_config: Mapping[str, Any],
) -> tuple[Any, str]:
    source_image = _image_from_state(source_image_state)
    if source_image is None:
        return (
            None,
            _placeholder_panel("Scenario Lab", "Clone the active chart here and explore alternate thresholds without touching the main desk."),
        )

    display_result = _sanitize_result_for_ui(result_state)
    overlay = _build_overlay_image(
        source_image,
        result_state,
        overlay_mode=str(render_config.get("overlay_mode", "history-plus-projection")),
        min_conf_global=float(render_config.get("min_conf_global", 0.42) or 0.42),
        min_conf_latest=float(render_config.get("min_conf_latest", 0.50) or 0.50),
        history_limit=int(render_config.get("history_depth", 8) or 8),
        label_budget=int(render_config.get("label_density", 10) or 10),
        projection_confidence_floor=float(render_config.get("projection_focus", 0.35) or 0.35),
    )
    cv_debug = pg_main.build_cv_debug_payload(display_result, render_config=render_config)
    visible_count = int(cv_debug.get("visible_detection_count", 0) or 0)
    overlay_chip = _chip(
        f"overlay {str(render_config.get('overlay_mode', 'history-plus-projection')).replace('-', ' ')}",
        "soft",
    )
    projection_chip = _chip(f"projection floor {_fmt_num(render_config.get('projection_focus', 0.35), 2)}", "amber")
    return (
        overlay,
        (
            "<div class='pg-panel'>"
            "<div class='pg-section-title'>Scenario Lab</div>"
            "<div class='pg-muted'>This sandbox rerender is isolated from the main decision desk, so you can rehearse alternate visibility thresholds and overlay density without overwriting the live baseline.</div>"
            f"<div class='pg-chip-row'>{_chip(f'visible detections {visible_count}', 'teal')}{overlay_chip}{projection_chip}</div>"
            f"<div class='pg-muted'>Signal remains {str(display_result.get('action', 'HOLD')).upper()} at {_fmt_pct01(display_result.get('confidence', 0.0))}. Scenario changes here only alter the surfaced view and diagnostics emphasis.</div>"
            "</div>"
        ),
    )


def run_signal_workstation(
    file_obj: Any,
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
    debug_depth: float,
    audit_tab_loaded: bool,
    heatmap_tab_loaded: bool,
    compare_tab_loaded: bool,
) -> tuple[Any, ...]:
    upload_paths = _uploaded_file_paths(file_obj)
    if not upload_paths:
        return _empty_workspace_outputs()
    if len(upload_paths) != 2:
        raise gr.Error("Upload exactly two chart images: higher timeframe first and lower timeframe second.")

    render_config = _build_render_config(
        overlay_mode=overlay_mode,
        min_conf_global=min_conf_global,
        min_conf_latest=min_conf_latest,
        history_depth=history_depth,
        label_density=label_density,
        projection_focus=projection_focus,
        debug_depth=debug_depth,
    )
    labels = ["Higher TF", "Lower TF"]
    analyzed: list[dict[str, Any]] = []
    for index, file_path in enumerate(upload_paths[:2]):
        result, overlay_image, _gauge_unused, _skill_unused = pg_main.run_inference(
            file_path,
            annotation_text="",
            overlay_mode=str(render_config["overlay_mode"]),
            min_conf_global=float(render_config["min_conf_global"]),
            min_conf_latest=float(render_config["min_conf_latest"]),
            history_depth=int(render_config["history_depth"]),
            label_density=int(render_config["label_density"]),
            projection_focus=float(render_config["projection_focus"]),
        )
        source_image_state = _source_image_to_state(file_path)
        analyzed.append(
            {
                "result": result,
                "file_path": file_path,
                "source_image_state": source_image_state,
                "compare_entry": _build_timeframe_compare_entry(
                    result,
                    source_image_state,
                    file_path,
                    labels[index],
                    overlay_image=overlay_image,
                    render_config=render_config,
                ),
            }
        )
    result = _build_multi_timeframe_result(analyzed)
    source_image_state = analyzed[-1]["source_image_state"]
    file_path = str(analyzed[-1]["file_path"])
    _append_session_entry(_build_session_entry(result, source_image_state, file_path, source="manual-multi-timeframe"))
    rendered = _render_workspace_from_result(
        result,
        source_image_state,
        render_config,
        include_audit=bool(audit_tab_loaded),
        include_heatmap=bool(heatmap_tab_loaded),
        include_compare=bool(compare_tab_loaded),
    )
    return (*rendered, result, source_image_state, file_path)


def refresh_live_preview(
    result_state: dict[str, Any] | None,
    source_image_state: Any,
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
    debug_depth: float,
    compare_tab_loaded: bool,
) -> tuple[Any, ...]:
    if not result_state or source_image_state is None:
        return (
            None,
            _placeholder_panel("Memory Recall", "Local memory retrieval will appear here."),
            _placeholder_panel("Evidence Panel", "Chart evidence and transition traces will appear here."),
            _placeholder_panel("Diagnostics Cockpit", "Runtime diagnostics will appear here after the first signal pass."),
            build_control_status_html(None, None),
            _gr_skip(),
        )

    render_config = _build_render_config(
        overlay_mode=overlay_mode,
        min_conf_global=min_conf_global,
        min_conf_latest=min_conf_latest,
        history_depth=history_depth,
        label_density=label_density,
        projection_focus=projection_focus,
        debug_depth=debug_depth,
    )
    return _render_live_preview_from_result(
        result_state,
        source_image_state,
        render_config,
        include_compare=bool(compare_tab_loaded),
    )


def render_scenario_lab(
    result_state: dict[str, Any] | None,
    source_image_state: Any,
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
    debug_depth: float,
) -> tuple[Any, str]:
    if not result_state or source_image_state is None:
        return (
            None,
            _placeholder_panel("Scenario Lab", "Clone the active chart here and explore alternate thresholds without touching the main desk."),
        )
    render_config = _build_render_config(
        overlay_mode=overlay_mode,
        min_conf_global=min_conf_global,
        min_conf_latest=min_conf_latest,
        history_depth=history_depth,
        label_density=label_density,
        projection_focus=projection_focus,
        debug_depth=debug_depth,
    )
    return _render_scenario_lab_from_result(result_state, source_image_state, render_config)


def refresh_zone_canvas(
    result_state: dict[str, Any] | None,
    source_image_state: Any,
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
    debug_depth: float,
) -> Any:
    source_image = _image_from_state(source_image_state)
    if not result_state or source_image is None:
        return None
    render_config = _build_render_config(
        overlay_mode=overlay_mode,
        min_conf_global=min_conf_global,
        min_conf_latest=min_conf_latest,
        history_depth=history_depth,
        label_density=label_density,
        projection_focus=projection_focus,
        debug_depth=debug_depth,
    )
    overlay = _build_overlay_image(
        source_image,
        result_state,
        overlay_mode=str(render_config.get("overlay_mode", "history-plus-projection")),
        min_conf_global=float(render_config.get("min_conf_global", 0.42) or 0.42),
        min_conf_latest=float(render_config.get("min_conf_latest", 0.50) or 0.50),
        history_limit=int(render_config.get("history_depth", 8) or 8),
        label_budget=int(render_config.get("label_density", 10) or 10),
        projection_confidence_floor=float(render_config.get("projection_focus", 0.35) or 0.35),
    )
    return _build_zone_editor_value(source_image_state, base_image=overlay)


def save_zone_annotation(
    editor_value: Any,
    zone_kind: str,
    zone_label: str,
    zone_notes: str,
    zone_strength: float,
    active_file_path: str,
    result_state: dict[str, Any] | None,
    source_image_state: Any,
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
    debug_depth: float,
    audit_tab_loaded: bool,
    heatmap_tab_loaded: bool,
    compare_tab_loaded: bool,
) -> tuple[str, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any]:
    success, message = _save_zone_teaching(
        editor_value,
        zone_kind,
        zone_label,
        zone_notes,
        zone_strength,
        active_file_path,
        result_state,
    )
    if not success or not result_state or source_image_state is None:
        return (
            message,
            _build_zone_library_html(result_state),
            _gr_skip(),
            _gr_skip(),
            _gr_skip(),
            _gr_skip(),
            _gr_skip(),
            _gr_skip(),
            _gr_skip(),
            _gr_skip(),
            _gr_skip(),
            _gr_skip(),
        )
    updated_result = _apply_zone_memory_to_result(result_state)
    render_config = _build_render_config(
        overlay_mode=overlay_mode,
        min_conf_global=min_conf_global,
        min_conf_latest=min_conf_latest,
        history_depth=history_depth,
        label_density=label_density,
        projection_focus=projection_focus,
        debug_depth=debug_depth,
    )
    rendered = _render_workspace_from_result(
        updated_result,
        source_image_state,
        render_config,
        include_audit=bool(audit_tab_loaded),
        include_heatmap=bool(heatmap_tab_loaded),
        include_compare=bool(compare_tab_loaded),
    )
    return (
        message,
        rendered[17],
        rendered[0],
        rendered[14],
        rendered[15],
        rendered[16],
        rendered[3],
        rendered[13],
        rendered[12],
        updated_result,
        rendered[18],
        rendered[19],
    )


def poll_capture_updates(
    capture_token_state: int,
    capture_status_token_state: int,
    current_result_state: dict[str, Any] | None,
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
    debug_depth: float,
    audit_tab_loaded: bool,
    heatmap_tab_loaded: bool,
    compare_tab_loaded: bool,
) -> tuple[Any, ...]:
    def _capture_poll_payload(
        next_capture_token: int,
        next_status_token: int,
        *,
        control_status_value: Any = None,
    ) -> tuple[Any, ...]:
        outputs: list[Any] = [_gr_skip()] * 25
        if control_status_value is not None:
            outputs[12] = control_status_value
        outputs.extend([next_capture_token, next_status_token])
        return tuple(outputs)

    render_config = _build_render_config(
        overlay_mode=overlay_mode,
        min_conf_global=min_conf_global,
        min_conf_latest=min_conf_latest,
        history_depth=history_depth,
        label_density=label_density,
        projection_focus=projection_focus,
        debug_depth=debug_depth,
    )
    runtime_snapshot = _get_capture_runtime_snapshot()
    latest_status_token = int(runtime_snapshot.get("status_token", 0) or 0)
    latest_token, latest_result, latest_source_image_state, latest_file_path, latest_overlay, latest_gauge, latest_skill_fig = _get_latest_capture_payload()
    if latest_token <= int(capture_token_state or 0) or latest_result is None or latest_source_image_state is None:
        if latest_status_token <= int(capture_status_token_state or 0):
            return _capture_poll_payload(
                int(capture_token_state or 0),
                int(capture_status_token_state or 0),
            )
    return _capture_poll_payload(
        int(capture_token_state or 0),
        latest_status_token,
        control_status_value=build_control_status_html(
            current_result_state,
                render_config=render_config,
            ),
        )

    rendered = _render_workspace_from_result(
        latest_result,
        latest_source_image_state,
        render_config,
        precomputed_overlay=latest_overlay,
        precomputed_gauge=latest_gauge,
        precomputed_skill_fig=latest_skill_fig,
        include_audit=bool(audit_tab_loaded),
        include_heatmap=bool(heatmap_tab_loaded),
        include_compare=bool(compare_tab_loaded),
    )
    return (
        *rendered,
        latest_result,
        latest_source_image_state,
        latest_file_path,
        latest_token,
        latest_status_token,
    )


def _feedback_assets_dir() -> Path:
    path = RUNTIME.data_dir / "feedback_assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _feedback_feed_path() -> Path:
    return RUNTIME.data_dir / "feedback_feed.jsonl"


def _project_relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(RUNTIME.project_root)).replace("\\", "/")
    except Exception:
        return str(path)


def _coerce_feedback_image(feedback_image: Any) -> Image.Image | None:
    if feedback_image is None:
        return None
    if isinstance(feedback_image, Image.Image):
        return feedback_image.convert("RGB")
    if isinstance(feedback_image, np.ndarray):
        arr = cast(NDArray[Any], feedback_image)
        if arr.dtype != np.uint8:
            arr_uint8: NDArray[np.uint8] = np.clip(arr, 0, 255).astype(np.uint8)
        else:
            arr_uint8 = cast(NDArray[np.uint8], arr)
        return Image.fromarray(arr_uint8).convert("RGB")
    return None


def _save_feedback_result_image(image_hash: str, verdict: str, feedback_image: Any) -> dict[str, Any]:
    image_rgb = _coerce_feedback_image(feedback_image)
    if image_rgb is None:
        return {}
    try:
        verdict_slug = re.sub(r"[^a-z0-9]+", "-", str(verdict).strip().lower()).strip("-") or "hold"
        asset_dir = _feedback_assets_dir() / verdict_slug
        asset_dir.mkdir(parents=True, exist_ok=True)
        buffer = io.BytesIO()
        image_rgb.save(buffer, format="PNG", optimize=True)
        raw = buffer.getvalue()
        image_sha256 = hashlib.sha256(raw).hexdigest()
        asset_name = f"{str(image_hash).strip()[:16]}_{image_sha256[:12]}_{uuid4().hex[:8]}.png"
        asset_path = asset_dir / asset_name
        asset_path.write_bytes(raw)
        return {
            "path": str(asset_path),
            "relative_path": _project_relative_path(asset_path),
            "sha256": image_sha256,
            "width": int(image_rgb.width),
            "height": int(image_rgb.height),
            "size_bytes": int(len(raw)),
            "format": "PNG",
            "verdict": str(verdict).upper(),
        }
    except Exception as exc:
        logger.warning("Saving feedback result image failed: %s", exc)
        return {"error": str(exc)}


def _build_feedback_annotation_text(feedback_asset: Mapping[str, Any] | None) -> str:
    if not feedback_asset:
        return ""
    if str(feedback_asset.get("error", "")).strip():
        return "feedback_result_image save_failed"
    parts = ["feedback_result_image"]
    relative_path = str(feedback_asset.get("relative_path", "")).strip()
    if relative_path:
        parts.append(f"path={relative_path}")
    sha256 = str(feedback_asset.get("sha256", "")).strip()
    if sha256:
        parts.append(f"sha256={sha256[:16]}")
    width = int(feedback_asset.get("width", 0) or 0)
    height = int(feedback_asset.get("height", 0) or 0)
    if width > 0 and height > 0:
        parts.append(f"size={width}x{height}")
    return " ".join(parts)


def on_feedback(last_file: Any, verdict: str, reason: str, feedback_image: Any | None = None) -> str:
    personal = _get_personal()
    continual_learning = _get_continual_learning()
    rl_engine = _get_rl_engine()

    if last_file is None or not str(last_file).strip():
        return "No prior file to attach feedback."
    file_path = last_file.name if hasattr(last_file, "name") else str(last_file)
    _img_unused, meta = load_any_file_as_image(file_path)
    chosen = verdict.upper()
    rejected = "SELL" if chosen == "BUY" else "BUY"
    feedback_asset = _save_feedback_result_image(meta["sha256"], chosen, feedback_image)
    feedback_image_path = str(feedback_asset.get("path", "") or "").strip()
    annotation_text = _build_feedback_annotation_text(feedback_asset)
    personal.record_feedback(meta["sha256"], chosen, rejected, reason, annotation_text)
    replay_item: dict[str, Any] = (
        cast(
            dict[str, Any],
            continual_learning.record_feedback(
                meta["sha256"],
                chosen,
                reason,
                feedback_image_path=feedback_image_path,
                feedback_image_sha256=str(feedback_asset.get("sha256", "") or "").strip(),
                feedback_image_meta=dict(feedback_asset),
            ),
        )
        if RUNTIME.enable_replay_continual_learning
        else {}
    )
    rl_feedback: dict[str, Any] = (
        rl_engine.record_feedback(
            meta["sha256"],
            chosen,
            reason,
            feedback_image_path=feedback_image_path,
            feedback_image_sha256=str(feedback_asset.get("sha256", "") or "").strip(),
            feedback_image_meta=dict(feedback_asset),
        )
        if not RUNTIME.pause_rl_updates
        else {}
    )
    if replay_item:
        personal.record_context_feedback(
            str(replay_item.get("context_key", "default")),
            str(replay_item.get("context_descriptor", "")),
            chosen,
            reason,
            annotation_text,
        )

    # Refresh style EMA from memory bank DPO pairs
    bank = _get_memory_bank()
    if bank is not None:
        try:
            dpo_pairs = personal.generate_dpo_pairs(memory_bank=bank, n=50)
            _: NDArray[np.float32] = personal.update_style_from_memory_bank(dpo_pairs)
        except Exception:
            pass

    _append_jsonl(
        _feedback_feed_path(),
        {
            "ts": utc_now_iso(),
            "source_path": file_path,
            "source_image_hash": str(meta.get("sha256", "")),
            "verdict": chosen,
            "rejected": rejected,
            "reason": str(reason),
            "feedback_image": dict(feedback_asset),
            "learning_snapshot_path": str(replay_item.get("snapshot_path", feedback_image_path)),
            "continual_learning_updated": bool(replay_item),
            "continual_learning_success": bool(replay_item.get("success", False)) if replay_item else False,
            "rl_feedback_updated": bool(rl_feedback),
            "rl_online_updated": bool(rl_feedback.get("updated", False)) if rl_feedback else False,
        },
    )

    image_status = ""
    if feedback_image_path:
        image_status = f" Result image saved to {_project_relative_path(Path(feedback_image_path))}."
    elif str(feedback_asset.get("error", "")).strip():
        image_status = " Result image could not be saved."

    if rl_feedback:
        if bool(rl_feedback.get("updated", False)):
            return (
                "Feedback captured and RL updated "
                f"(loss={float(rl_feedback.get('loss', 0.0) or 0.0):.4f})."
                f"{image_status}"
            ).strip()
        return f"Feedback captured and queued for learning.{image_status}".strip()
    return f"Feedback captured for learning.{image_status}".strip()


async def watch_inbox_loop(stop_evt: threading.Event):
    while not stop_evt.is_set():
        files = sorted(
            [
                p
                for p in RUNTIME.screenshots_inbox.glob("*")
                if p.is_file() and not p.name.lower().startswith("hotkey_capture_")
            ]
        )
        for path in files:
            try:
                process_capture_file(str(path), source="inbox")
            except Exception as e:
                logger.exception("Background inbox inference failed for %s: %s", path, e)
        await asyncio.sleep(RUNTIME.watch_interval_sec)


# ------------------------------------------------------------------
# Gradio UI
# ------------------------------------------------------------------

def launch_ui():
    with gr.Blocks(title=UI_BRAND_NAME, fill_width=True) as demo:
        result_state = gr.State(value={})
        source_image_state = gr.State(value=None)
        active_file_path_state = gr.State(value="")
        capture_token_state = gr.State(value=0)
        capture_status_token_state = gr.State(value=0)
        audit_tab_loaded_state = gr.State(value=False)
        heatmap_tab_loaded_state = gr.State(value=False)
        compare_tab_loaded_state = gr.State(value=False)
        capture_timer = gr.Timer(value=float(RUNTIME.capture_poll_interval_sec), active=True)
        gr.HTML(_build_hero_shell_html())
        with gr.Row():
            with gr.Column(scale=3):
                with gr.Group(elem_classes=["pg-panel", "pg-controls", "pg-control-board"]):
                    gr.Markdown("### Mission Control")
                    gr.HTML(_build_mission_control_intro_html())
                    _desk_mode = gr.Radio(
                        choices=["Guided", "Operator", "Compact"],
                        value="Guided",
                        label="Desk Mode",
                        info="Guided keeps onboarding copy visible. Operator trims it down. Compact tightens density for fast scanning.",
                        elem_id="pg_desk_mode",
                    )
                    file_input = gr.File(
                        label="Upload Exactly Two Chart Images",
                        file_types=["image"],
                        file_count="multiple",
                        allow_reordering=True,
                    )
                    with gr.Accordion("Overlay And Confidence Controls", open=True):
                        with gr.Row():
                            with gr.Column(scale=1):
                                overlay_mode = gr.Dropdown(
                                    choices=["debug-all", "latest-only", "global-only", "history-boxes", "history-plus-projection"],
                                    value="history-plus-projection",
                                    label="Overlay Mode",
                                    info="Choose whether the desk emphasizes full history, the latest branch, or projected structure.",
                                )
                                min_conf_global = gr.Slider(
                                    minimum=0.2,
                                    maximum=0.95,
                                    value=0.42,
                                    step=0.01,
                                    label="Global Min Confidence",
                                    info="Hide weaker detections from the overall historical overlay.",
                                )
                                history_depth = gr.Slider(
                                    minimum=1,
                                    maximum=18,
                                    value=8,
                                    step=1,
                                    label="Sequence History Depth",
                                    info="How many historical structure boxes stay visible on the chart.",
                                )
                            with gr.Column(scale=1):
                                min_conf_latest = gr.Slider(
                                    minimum=0.2,
                                    maximum=0.95,
                                    value=0.50,
                                    step=0.01,
                                    label="Latest-Branch Min Confidence",
                                    info="Hide weaker labels on the newest decision branch so the trigger area stays readable.",
                                )
                                label_density = gr.Slider(
                                    minimum=2,
                                    maximum=18,
                                    value=10,
                                    step=1,
                                    label="Overlay Label Density",
                                    info="Controls how many labels and annotations the chart can show at once.",
                                )
                                projection_focus = gr.Slider(
                                    minimum=0.0,
                                    maximum=0.9,
                                    value=0.35,
                                    step=0.01,
                                    label="Projection Visibility Floor",
                                    info="Future boxes are only drawn when the projection confidence clears this floor.",
                                )
                        debug_depth = gr.Slider(
                            minimum=3,
                            maximum=12,
                            value=6,
                            step=1,
                            label="Diagnostics Depth",
                            info="Raises or lowers how much detail the diagnostics, evidence, and memory panels expose.",
                        )
                    run_btn = gr.Button("Analyze Both Charts", variant="primary")
                    control_status_html = gr.HTML(value=build_control_status_html(None, None))
                    adaptive_guidance_html = gr.HTML(value=_build_adaptive_guidance_html(None))
            with gr.Column(scale=9):
                signal_html = gr.HTML(value=_placeholder_panel("Signal Overview", f"Upload exactly two chart images to activate {UI_BRAND_NAME}."))
                with gr.Row():
                    with gr.Column(scale=7):
                        overlay_img = gr.Image(
                            label="Annotated Chart",
                            type="pil",
                            height=560,
                            buttons=["download", "fullscreen"],
                            elem_classes=["pg-stage-media"],
                        )
                        timeframe_overlay_html = gr.HTML(
                            value=_placeholder_panel("Timeframe Overlays", "Higher and lower timeframe overlays will appear here after a run.")
                        )
                    with gr.Column(scale=5):
                        confidence_gauge = gr.Plot(label="Decision Gauge")
                        forecast_html = gr.HTML(value=_placeholder_panel("Forecast & Risk", "Forecast distribution and risk posture will appear here."))

        with gr.Tabs(elem_classes=["pg-tab-wrap"]):
            with gr.Tab("Analysis"):
                analysis_brief_box = gr.Textbox(
                    label="Analyst Brief",
                    lines=10,
                    interactive=False,
                    info="A plain-language operator brief that combines the live result, CV read, and current view controls.",
                    elem_classes=["pg-brief"],
                    buttons=["copy"],
                )
                with gr.Tabs(elem_classes=["pg-tab-wrap"]):
                    with gr.Tab("Model Council") as model_council_tab:
                        model_council_html = gr.HTML(value=_placeholder_panel("Model Council", "Open this tab to lazy-load the council worker and run the heavyweight ensemble once for the active static image."))
                    with gr.Tab("Memory Recall"):
                        memory_html = gr.HTML(value=_placeholder_panel("Memory Recall", "Top recalled episodes and ambiguity control will appear here."))
                    with gr.Tab("Gate Matrix"):
                        gate_matrix_html = gr.HTML(value=_placeholder_panel("Gate Matrix", "Gate pass/fail structure will appear here."))
                    with gr.Tab("Evidence"):
                        evidence_html = gr.HTML(value=_placeholder_panel("Evidence Panel", "Pattern evidence and transition traces will appear here."))
                    with gr.Tab("Diagnostics"):
                        diagnostics_html = gr.HTML(value=_placeholder_panel("Diagnostics Cockpit", "Runtime diagnostics will appear here after the first signal pass."))
                    with gr.Tab("Attribution"):
                        skill_plot = gr.Plot(label="Skill Contribution Dashboard")
                    with gr.Tab("Audit JSON") as audit_tab:
                        audit_json = gr.JSON(label="Runtime Audit JSON", open=False, buttons=["copy"], height=460)
            with gr.Tab("Visual Lab"):
                with gr.Tabs(elem_classes=["pg-tab-wrap"]):
                    with gr.Tab("Compare Desk") as compare_tab:
                        compare_desk_html = gr.HTML(value=_placeholder_panel("Compare Desk", "Raw, focused, and annotated compare views will appear here."))
                    with gr.Tab("Confidence Heatmap") as heatmap_tab:
                        with gr.Row():
                            with gr.Column(scale=6):
                                heatmap_img = gr.Image(label="Confidence Heatmap", type="pil", height=470, buttons=["download", "fullscreen"], elem_classes=["pg-stage-media"])
                            with gr.Column(scale=4):
                                heatmap_summary_html = gr.HTML(value=_placeholder_panel("Confidence Heatmap", "Heat concentration will appear here after the first inference."))
                    with gr.Tab("Scenario Lab"):
                        with gr.Group(elem_classes=["pg-panel"]):
                            gr.Markdown("### Scenario Lab")
                            gr.Markdown("Clone the active chart into a separate threshold sandbox. This never overwrites the live desk.")
                            with gr.Row():
                                with gr.Column(scale=4):
                                    scenario_overlay_mode = gr.Dropdown(
                                        choices=["debug-all", "latest-only", "global-only", "history-boxes", "history-plus-projection"],
                                        value="history-plus-projection",
                                        label="Scenario Overlay Mode",
                                        info="Use a different overlay emphasis here without touching the live chart view.",
                                    )
                                    scenario_min_conf_global = gr.Slider(minimum=0.2, maximum=0.95, value=0.42, step=0.01, label="Scenario Global Min Confidence", info="Hide weaker global detections in the sandbox view.")
                                    scenario_min_conf_latest = gr.Slider(minimum=0.2, maximum=0.95, value=0.50, step=0.01, label="Scenario Latest Min Confidence", info="Trim weaker labels from the active branch only in the sandbox.")
                                    scenario_history_depth = gr.Slider(minimum=1, maximum=18, value=8, step=1, label="Scenario History Depth", info="How much historical structure stays visible in the sandbox.")
                                    scenario_label_density = gr.Slider(minimum=2, maximum=18, value=10, step=1, label="Scenario Label Density", info="Reduce this if the view gets visually crowded.")
                                    scenario_projection_focus = gr.Slider(minimum=0.0, maximum=0.9, value=0.35, step=0.01, label="Scenario Projection Floor", info="Raise this to demand more confidence before projected boxes appear.")
                                    scenario_debug_depth = gr.Slider(minimum=3, maximum=12, value=6, step=1, label="Scenario Debug Depth", info="Controls how much supporting detail this scenario summary keeps.")
                                with gr.Column(scale=8):
                                    scenario_overlay_img = gr.Image(label="Scenario View", type="pil", height=470, buttons=["download", "fullscreen"], elem_classes=["pg-stage-media"])
                                    scenario_summary_html = gr.HTML(value=_placeholder_panel("Scenario Lab", "Clone the active chart here and explore alternate thresholds without touching the main desk."))
                    with gr.Tab("Zone Studio"):
                        with gr.Group(elem_classes=["pg-panel", "pg-zone-studio"]):
                            gr.Markdown("### Zone Studio")
                            gr.Markdown("Paint support, resistance, or reaction zones on the live chart. Saved zones become persistent teaching memory for future runs.")
                            with gr.Row():
                                with gr.Column(scale=6):
                                    zone_canvas = gr.ImageEditor(
                                        label="Zone Teaching Canvas",
                                        type="pil",
                                        height=520,
                                        image_mode="RGBA",
                                        buttons=["download", "fullscreen"],
                                        brush=gr.Brush(
                                            colors=["#58da7b", "#df6b5f", "#d7a65a"],
                                            default_color="#58da7b",
                                            color_mode="defaults",
                                        ),
                                        eraser=False,
                                        layers=True,
                                        elem_classes=["pg-zone-editor"],
                                    )
                                with gr.Column(scale=4):
                                    zone_kind = gr.Dropdown(
                                        choices=["support", "resistance", "reaction"],
                                        value="support",
                                        label="Zone Type",
                                        info="Label the painted area by the behavior you want the desk to remember.",
                                    )
                                    zone_label = gr.Textbox(label="Zone Label", value="Operator Zone", info="Use a short label that will still make sense later in the Zone Library.")
                                    zone_notes = gr.Textbox(label="Teaching Notes", lines=4, placeholder="What should the engine learn from this area?", info="Describe why this area mattered so future runs inherit the context.")
                                    zone_strength = gr.Slider(minimum=0.1, maximum=1.0, value=0.72, step=0.01, label="Teaching Strength", info="Raise this when the zone is especially important to future chart review.")
                                    with gr.Row():
                                        zone_save_btn = gr.Button("Save Zone Teaching")
                                        zone_reset_btn = gr.Button("Reset Canvas")
                                    zone_status = gr.Textbox(label="Zone Status", lines=2, interactive=False)
                                    zone_library_html = gr.HTML(value=_build_zone_library_html())
            with gr.Tab("History"):
                with gr.Tabs(elem_classes=["pg-tab-wrap"]):
                    with gr.Tab("Session Timeline"):
                        session_timeline_html = gr.HTML(value=_placeholder_panel("Session Timeline", "Session captures will appear here in order."))
                    with gr.Tab("Pattern Browser"):
                        pattern_browser_html = gr.HTML(value=_placeholder_panel("Pattern Browser", "Similar session cases will appear here once enough charts have been reviewed."))
            with gr.Tab("Feedback"):
                with gr.Group(elem_classes=["pg-panel", "pg-feedback"]):
                    gr.Markdown("### Outcome Feedback")
                    gr.Markdown("Upload the result image you marked up so the learning feed keeps the visual evidence with your notes.")
                    verdict = gr.Dropdown(choices=["BUY", "SELL", "HOLD", "WRONG"], value="HOLD", label="Verdict", info="Choose the outcome that best describes what actually happened.")
                    feedback_result_image = gr.Image(label="Result Image For Learning", type="pil", height=320, buttons=["download", "fullscreen"], elem_classes=["pg-stage-media"])
                    reason = gr.Textbox(label="Reason", lines=3, placeholder="Why are you submitting this feedback?", info="Capture the lesson in plain language so future review stays explainable.")
                    fb_btn = gr.Button("Submit Feedback")
                    fb_status = gr.Textbox(label="Feedback Status", lines=1, interactive=False)

        full_run_inputs: list[Any] = [
            file_input,
            overlay_mode,
            min_conf_global,
            min_conf_latest,
            history_depth,
            label_density,
            projection_focus,
            debug_depth,
            audit_tab_loaded_state,
            heatmap_tab_loaded_state,
            compare_tab_loaded_state,
        ]
        full_run_outputs: list[Any] = [
            overlay_img,
            confidence_gauge,
            skill_plot,
            signal_html,
            model_council_html,
            forecast_html,
            memory_html,
            gate_matrix_html,
            evidence_html,
            diagnostics_html,
            audit_json,
            timeframe_overlay_html,
            control_status_html,
            adaptive_guidance_html,
            heatmap_img,
            heatmap_summary_html,
            compare_desk_html,
            zone_library_html,
            session_timeline_html,
            pattern_browser_html,
            zone_canvas,
            analysis_brief_box,
            result_state,
            source_image_state,
            active_file_path_state,
        ]
        scenario_inputs: list[Any] = [
            result_state,
            source_image_state,
            scenario_overlay_mode,
            scenario_min_conf_global,
            scenario_min_conf_latest,
            scenario_history_depth,
            scenario_label_density,
            scenario_projection_focus,
            scenario_debug_depth,
        ]
        scenario_outputs: list[Any] = [
            scenario_overlay_img,
            scenario_summary_html,
        ]
        live_preview_inputs: list[Any] = [
            result_state,
            source_image_state,
            overlay_mode,
            min_conf_global,
            min_conf_latest,
            history_depth,
            label_density,
            projection_focus,
            debug_depth,
            compare_tab_loaded_state,
        ]
        live_preview_outputs: list[Any] = [
            overlay_img,
            memory_html,
            evidence_html,
            diagnostics_html,
            control_status_html,
            compare_desk_html,
        ]
        capture_poll_inputs: list[Any] = [
            capture_token_state,
            capture_status_token_state,
            result_state,
            overlay_mode,
            min_conf_global,
            min_conf_latest,
            history_depth,
            label_density,
            projection_focus,
            debug_depth,
            audit_tab_loaded_state,
            heatmap_tab_loaded_state,
            compare_tab_loaded_state,
        ]
        capture_poll_outputs: list[Any] = [
            overlay_img,
            confidence_gauge,
            skill_plot,
            signal_html,
            model_council_html,
            forecast_html,
            memory_html,
            gate_matrix_html,
            evidence_html,
            diagnostics_html,
            audit_json,
            timeframe_overlay_html,
            control_status_html,
            adaptive_guidance_html,
            heatmap_img,
            heatmap_summary_html,
            compare_desk_html,
            zone_library_html,
            session_timeline_html,
            pattern_browser_html,
            zone_canvas,
            analysis_brief_box,
            result_state,
            source_image_state,
            active_file_path_state,
            capture_token_state,
            capture_status_token_state,
        ]

        run_btn.click(
            run_signal_workstation,
            inputs=full_run_inputs,
            outputs=full_run_outputs,
        )
        file_input.change(
            run_signal_workstation,
            inputs=full_run_inputs,
            outputs=full_run_outputs,
        )
        result_state.change(
            render_scenario_lab,
            inputs=scenario_inputs,
            outputs=scenario_outputs,
            queue=False,
        )
        model_council_tab.select(
            load_model_council_tab,
            inputs=[
                result_state,
                source_image_state,
                active_file_path_state,
                overlay_mode,
                min_conf_global,
                min_conf_latest,
                history_depth,
                label_density,
                projection_focus,
                debug_depth,
                audit_tab_loaded_state,
                heatmap_tab_loaded_state,
                compare_tab_loaded_state,
            ],
            outputs=full_run_outputs,
        )
        audit_tab.select(
            load_audit_tab,
            inputs=[
                result_state,
                overlay_mode,
                min_conf_global,
                min_conf_latest,
                history_depth,
                label_density,
                projection_focus,
                debug_depth,
            ],
            outputs=[audit_json, audit_tab_loaded_state],
            queue=False,
        )
        heatmap_tab.select(
            load_heatmap_tab,
            inputs=[result_state, source_image_state],
            outputs=[heatmap_img, heatmap_summary_html, heatmap_tab_loaded_state],
            queue=False,
        )
        compare_tab.select(
            load_compare_desk_tab,
            inputs=[
                result_state,
                source_image_state,
                overlay_mode,
                min_conf_global,
                min_conf_latest,
                history_depth,
                label_density,
                projection_focus,
                debug_depth,
            ],
            outputs=[compare_desk_html, compare_tab_loaded_state],
            queue=False,
        )
        overlay_mode.change(
            refresh_live_preview,
            inputs=live_preview_inputs,
            outputs=live_preview_outputs,
            queue=False,
        )
        min_conf_global.input(
            refresh_live_preview,
            inputs=live_preview_inputs,
            outputs=live_preview_outputs,
            queue=False,
        )
        min_conf_latest.input(
            refresh_live_preview,
            inputs=live_preview_inputs,
            outputs=live_preview_outputs,
            queue=False,
        )
        history_depth.input(
            refresh_live_preview,
            inputs=live_preview_inputs,
            outputs=live_preview_outputs,
            queue=False,
        )
        label_density.input(
            refresh_live_preview,
            inputs=live_preview_inputs,
            outputs=live_preview_outputs,
            queue=False,
        )
        projection_focus.input(
            refresh_live_preview,
            inputs=live_preview_inputs,
            outputs=live_preview_outputs,
            queue=False,
        )
        debug_depth.input(
            refresh_live_preview,
            inputs=live_preview_inputs,
            outputs=live_preview_outputs,
            queue=False,
        )
        capture_timer.tick(
            poll_capture_updates,
            inputs=capture_poll_inputs,
            outputs=capture_poll_outputs,
            queue=False,
        )
        scenario_controls: list[Any] = [
            scenario_overlay_mode,
            scenario_min_conf_global,
            scenario_min_conf_latest,
            scenario_history_depth,
            scenario_label_density,
            scenario_projection_focus,
            scenario_debug_depth,
        ]
        for component in scenario_controls:
            component.input(
                render_scenario_lab,
                inputs=scenario_inputs,
                outputs=scenario_outputs,
                queue=False,
            )
        zone_save_btn.click(
            save_zone_annotation,
            inputs=[
                zone_canvas,
                zone_kind,
                zone_label,
                zone_notes,
                zone_strength,
                active_file_path_state,
                result_state,
                source_image_state,
                overlay_mode,
                min_conf_global,
                min_conf_latest,
                history_depth,
                label_density,
                projection_focus,
                debug_depth,
                audit_tab_loaded_state,
                heatmap_tab_loaded_state,
                compare_tab_loaded_state,
            ],
            outputs=[
                zone_status,
                zone_library_html,
                overlay_img,
                heatmap_img,
                heatmap_summary_html,
                compare_desk_html,
                signal_html,
                adaptive_guidance_html,
                control_status_html,
                result_state,
                session_timeline_html,
                pattern_browser_html,
            ],
        )
        zone_reset_btn.click(
            refresh_zone_canvas,
            inputs=[
                result_state,
                source_image_state,
                overlay_mode,
                min_conf_global,
                min_conf_latest,
                history_depth,
                label_density,
                projection_focus,
                debug_depth,
            ],
            outputs=[zone_canvas],
            queue=False,
        )
        fb_btn.click(
            on_feedback,
            inputs=[active_file_path_state, verdict, reason, feedback_result_image],
            outputs=[fb_status],
        )

    logger.info(
        "Runtime profile=%s device=%s preload_memory=%s local_ensemble=%s foundation_grounding=%s",
        str(getattr(RUNTIME, "runtime_profile", "FAST")).upper(),
        RUNTIME.device_preference,
        bool(getattr(RUNTIME, "preload_memory_bank_on_launch", False)),
        bool(getattr(RUNTIME, "enable_local_ensemble", True)),
        bool(getattr(RUNTIME, "prefer_foundation_grounding", True)),
    )
    if bool(getattr(RUNTIME, "background_warmup_on_launch", True)):
        _start_background_warmup()
    elif bool(getattr(RUNTIME, "preload_memory_bank_on_launch", False)):
        _get_memory_bank()

    stop_evt = threading.Event()
    thread = threading.Thread(target=lambda: asyncio.run(watch_inbox_loop(stop_evt)), daemon=True)
    thread.start()
    _start_capture_hotkey_listener()

    demo.queue(default_concurrency_limit=2)
    demo.launch(
        server_name=RUNTIME.ui_host,
        server_port=RUNTIME.ui_port,
        share=RUNTIME.ui_share,
        theme="default",
        css=UI_CSS,
        head=UI_HEAD,
    )
    stop_evt.set()


if __name__ == "__main__":
    launch_ui()
