from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os

from phoenixguard.paths import PROJECT_ROOT


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        return int(str(raw).strip())
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        return float(str(raw).strip())
    except Exception:
        return float(default)


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return str(default)
    value = str(raw).strip()
    return value or str(default)


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    if raw is None:
        return Path(default)
    candidate = str(raw).strip()
    if not candidate:
        return Path(default)
    return Path(candidate).expanduser()


def _runtime_profile_name() -> str:
    raw = str(os.getenv("PHOENIXGUARD_PROFILE", "FAST") or "FAST").strip().upper()
    if raw in {"FAST", "BALANCED", "FULL", "HEAVY_LAZY"}:
        return raw
    return "FAST"


@dataclass(slots=True)
class ModelConfig:
    cv_primary: str = 'hf://foduucom/stockmarket-pattern-detection-yolov8'
    cv_fallback: str = 'hf://foduucom/stockmarket-pattern-detection-yolov8'
    fin_dora_adapter: str = 'wangd12/financebench_llama_3_1_8b_8bits_r8_dora'
    chronos_model: str = 'amazon/chronos-2'
    style_embedder: str = 'sentence-transformers/all-MiniLM-L6-v2'

    # Optional advanced backbones / teachers / temporal sidecars.
    dinov2_backbone: str = 'facebook/dinov2-base'
    grounding_dino_model: str = 'IDEA-Research/grounding-dino-base'
    florence2_model: str = 'microsoft/Florence-2-base'
    sam2_model: str = 'facebook/sam2-hiera-small'
    chronos_bolt_model: str = 'amazon/chronos-bolt-base'
    timesfm_model: str = 'google/timesfm-1.0-200m'


@dataclass(slots=True)
class MemoryBankConfig:
    buys_dir: str = '808 Memory/BUYS-20260224T225615Z-1-001/BUYS'
    sells_dir: str = '808 Memory/SELLS-20260224T225719Z-1-001/SELLS'
    bank_output_dir: str = 'memory_bank'
    hnsw_m: int = 32
    hnsw_ef_construction: int = 200
    hnsw_ef_search: int = 100
    archetype_max_per_class: int = 60
    recall_boost_threshold: float = 0.85
    recall_logit_boost: float = 0.25
    recall_veto_threshold: float = 0.87
    few_shot_top_k: int = 3
    text_embed_dim: int = 384
    visual_fp_dim: int = 128
    shared_dim: int = 384
    dpo_pairs_per_refresh: int = 50


@dataclass(slots=True)
class RuntimeConfig:
    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)
    adapters_dir: Path = field(init=False)
    models_dir: Path = field(init=False)
    data_dir: Path = field(init=False)
    logs_dir: Path = field(init=False)
    screenshots_inbox: Path = field(init=False)
    memory_bank_dir: Path = field(init=False)
    session_log_path: Path = field(init=False)
    zone_memory_path: Path = field(init=False)
    session_thumbnails_dir: Path = field(init=False)
    compare_assets_dir: Path = field(init=False)
    replay_buffer_path: Path = field(init=False)
    adapter_bank_path: Path = field(init=False)
    pending_contexts_path: Path = field(init=False)
    personalization_profiles_path: Path = field(init=False)
    replay_snapshots_dir: Path = field(init=False)
    rl_policy_state_path: Path = field(init=False)
    rl_feedback_buffer_path: Path = field(init=False)
    rl_pending_contexts_path: Path = field(init=False)

    use_gpu: bool = True
    allow_offline_only: bool = False
    watch_interval_sec: int = 300
    loop_sleep_sec: int = 180
    inference_timeout_sec: int = 9

    consensus_threshold: float = 0.82
    gates_pass_minimum: int = 9
    conformal_max_interval_pct: float = 0.40
    risk_min_pct: float = 0.5
    risk_max_pct: float = 2.0
    quantiles: tuple[float, float, float] = (0.05, 0.5, 0.95)
    mcts_sims: int = 20
    memory_recall_update_every: int = 50

    ui_host: str = field(default_factory=lambda: str(os.getenv('PHOENIXGUARD_UI_HOST', '127.0.0.1') or '127.0.0.1').strip() or '127.0.0.1')
    ui_port: int = field(default_factory=lambda: _env_int('PHOENIXGUARD_UI_PORT', 7860))
    ui_share: bool = field(default_factory=lambda: _env_bool('PHOENIXGUARD_UI_SHARE', False))
    ui_open_browser: bool = field(default_factory=lambda: _env_bool('PHOENIXGUARD_UI_OPEN_BROWSER', False))
    ui_show_error: bool = field(default_factory=lambda: _env_bool('PHOENIXGUARD_UI_SHOW_ERROR', True))
    capture_hotkey: str = 'CTRL+V'
    capture_hotkey_fallback: str = 'CTRL+SHIFT+4'
    capture_poll_interval_sec: float = 2.5
    capture_bundle_size: int = 4
    capture_bundle_timeout_sec: int = 240
    cuda_cache_clear_every: int = 0
    cuda_cache_clear_reserved_gb: float = 0.0
    gc_collect_every: int = 0

    use_execution_permission: bool = True
    use_memory_rerank: bool = True
    use_macro_local_alignment_gate: bool = True
    use_opposition_strength_gate: bool = True
    downgrade_on_geometry_conflict: bool = True
    block_macro_flip_on_low_consistency: bool = True
    use_transition_fusion: bool = True
    use_memory_ambiguity_penalty: bool = True
    suppress_parser_artifacts: bool = True
    cap_direction_when_latest_uncertain: bool = True
    enable_late_interaction_memory: bool = True
    enable_episodic_trajectory_memory: bool = True
    enable_grounded_chart_parsing: bool = True
    enable_open_set_guard: bool = True
    enable_replay_continual_learning: bool = True
    enable_feedback_learning_feed: bool = True
    enable_test_time_adaptation: bool = True
    enable_fast_personalization: bool = True
    pause_rl_updates: bool = False
    prefer_foundation_grounding: bool = True
    runtime_profile: str = field(init=False)
    preload_memory_bank_on_launch: bool = field(init=False)
    background_warmup_on_launch: bool = field(init=False)
    enable_local_ensemble: bool = field(init=False)
    warm_local_ensemble_on_launch: bool = field(init=False)
    auto_model_council_on_inference: bool = field(init=False)
    force_full_council_on_cpu: bool = field(init=False)
    local_ensemble_max_loaded_models: int = field(init=False)
    model_council_cache_size: int = field(init=False)

    def __post_init__(self) -> None:
        self.adapters_dir = self.project_root / 'adapters'
        self.models_dir = self.project_root / 'models'
        self.data_dir = _env_path('PHOENIXGUARD_DATA_DIR', self.project_root / 'data')
        self.logs_dir = _env_path('PHOENIXGUARD_LOGS_DIR', self.project_root / 'logs')
        self.screenshots_inbox = self.data_dir / 'inbox'
        self.memory_bank_dir = self.project_root / 'memory_bank'
        self.session_log_path = self.data_dir / 'session_history.jsonl'
        self.zone_memory_path = self.data_dir / 'zone_memory.json'
        self.session_thumbnails_dir = self.data_dir / 'session_thumbs'
        self.compare_assets_dir = self.data_dir / 'compare_assets'
        self.replay_buffer_path = self.data_dir / 'replay_buffer.jsonl'
        self.adapter_bank_path = self.data_dir / 'adapter_bank.json'
        self.pending_contexts_path = self.data_dir / 'pending_contexts.json'
        self.personalization_profiles_path = self.data_dir / 'personalization_profiles.json'
        self.replay_snapshots_dir = self.data_dir / 'replay_snapshots'
        self.rl_policy_state_path = self.models_dir / 'rl_policy_state.pt'
        self.rl_feedback_buffer_path = self.data_dir / 'rl_feedback_buffer.jsonl'
        self.rl_pending_contexts_path = self.data_dir / 'rl_pending_contexts.json'
        for path in (
            self.adapters_dir,
            self.models_dir,
            self.data_dir,
            self.logs_dir,
            self.screenshots_inbox,
            self.memory_bank_dir,
            self.session_thumbnails_dir,
            self.compare_assets_dir,
            self.replay_snapshots_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        if self.allow_offline_only:
            os.environ.setdefault('HF_HUB_OFFLINE', '1')
            os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
            os.environ.setdefault('HF_DATASETS_OFFLINE', '1')
        else:
            os.environ.setdefault('HF_HUB_OFFLINE', '0')
            os.environ.setdefault('TRANSFORMERS_OFFLINE', '0')
            os.environ.setdefault('HF_DATASETS_OFFLINE', '0')
        os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1')

        self.runtime_profile = _runtime_profile_name()
        device_is_cuda = self.device_preference == 'cuda'

        if self.runtime_profile == 'FAST':
            self.enable_test_time_adaptation = False
            self.enable_replay_continual_learning = False
            self.pause_rl_updates = True
            self.prefer_foundation_grounding = False
            self.preload_memory_bank_on_launch = False
            self.background_warmup_on_launch = True
            self.enable_local_ensemble = False
            self.warm_local_ensemble_on_launch = False
            self.auto_model_council_on_inference = False
            self.force_full_council_on_cpu = False
        elif self.runtime_profile == 'BALANCED':
            self.enable_test_time_adaptation = False
            self.enable_replay_continual_learning = False
            self.prefer_foundation_grounding = False
            self.preload_memory_bank_on_launch = False
            self.background_warmup_on_launch = True
            self.enable_local_ensemble = True
            self.warm_local_ensemble_on_launch = False
            self.auto_model_council_on_inference = False
            self.force_full_council_on_cpu = False
        elif self.runtime_profile == 'HEAVY_LAZY':
            self.enable_test_time_adaptation = True
            self.enable_replay_continual_learning = True
            self.pause_rl_updates = False
            self.prefer_foundation_grounding = True
            self.preload_memory_bank_on_launch = False
            self.background_warmup_on_launch = False  # PATCH: Disable background warmup for debugging
            self.enable_local_ensemble = bool(device_is_cuda)
            self.warm_local_ensemble_on_launch = False
            self.auto_model_council_on_inference = True
            self.force_full_council_on_cpu = True
        else:
            self.preload_memory_bank_on_launch = True
            self.background_warmup_on_launch = True
            self.enable_local_ensemble = bool(device_is_cuda)
            self.warm_local_ensemble_on_launch = bool(device_is_cuda)
            self.auto_model_council_on_inference = False
            self.force_full_council_on_cpu = False

        self.local_ensemble_max_loaded_models = 3 if self.device_preference == 'cpu' else 6
        self.model_council_cache_size = 24

        env_overrides = {
            'PHOENIXGUARD_ENABLE_GROUNDED_CHART_PARSING': 'enable_grounded_chart_parsing',
            'PHOENIXGUARD_ENABLE_OPEN_SET_GUARD': 'enable_open_set_guard',
            'PHOENIXGUARD_ENABLE_REPLAY_CONTINUAL_LEARNING': 'enable_replay_continual_learning',
            'PHOENIXGUARD_ENABLE_FEEDBACK_LEARNING_FEED': 'enable_feedback_learning_feed',
            'PHOENIXGUARD_ENABLE_TEST_TIME_ADAPTATION': 'enable_test_time_adaptation',
            'PHOENIXGUARD_ENABLE_FAST_PERSONALIZATION': 'enable_fast_personalization',
            'PHOENIXGUARD_PAUSE_RL_UPDATES': 'pause_rl_updates',
            'PHOENIXGUARD_PREFER_FOUNDATION_GROUNDING': 'prefer_foundation_grounding',
            'PHOENIXGUARD_PRELOAD_MEMORY_BANK_ON_LAUNCH': 'preload_memory_bank_on_launch',
            'PHOENIXGUARD_BACKGROUND_WARMUP_ON_LAUNCH': 'background_warmup_on_launch',
            'PHOENIXGUARD_ENABLE_LOCAL_ENSEMBLE': 'enable_local_ensemble',
            'PHOENIXGUARD_WARM_LOCAL_ENSEMBLE_ON_LAUNCH': 'warm_local_ensemble_on_launch',
            'PHOENIXGUARD_AUTO_MODEL_COUNCIL_ON_INFERENCE': 'auto_model_council_on_inference',
            'PHOENIXGUARD_FORCE_FULL_COUNCIL_ON_CPU': 'force_full_council_on_cpu',
        }
        for env_name, attr_name in env_overrides.items():
            setattr(self, attr_name, _env_bool(env_name, bool(getattr(self, attr_name))))
        self.local_ensemble_max_loaded_models = max(
            1,
            _env_int('PHOENIXGUARD_LOCAL_ENSEMBLE_MAX_LOADED', self.local_ensemble_max_loaded_models),
        )
        self.model_council_cache_size = max(
            1,
            _env_int('PHOENIXGUARD_MODEL_COUNCIL_CACHE_SIZE', self.model_council_cache_size),
        )

    @property
    def device_preference(self) -> str:
        if not self.use_gpu:
            return 'cpu'
        try:
            import torch
            return 'cuda' if torch.cuda.is_available() else 'cpu'
        except Exception:
            return 'cpu'


@dataclass(slots=True)
class VoiceConfig:
    project_root: Path = field(default_factory=lambda: PROJECT_ROOT)
    enabled: bool = field(default_factory=lambda: _env_bool('PHOENIXGUARD_VOICE_ENABLED', True))
    listening_enabled_default: bool = field(default_factory=lambda: _env_bool('PHOENIXGUARD_VOICE_LISTENING_ENABLED', True))
    automatic_timer_enabled_default: bool = field(default_factory=lambda: _env_bool('PHOENIXGUARD_VOICE_AUTOMATIC_TIMER_ENABLED', False))
    allow_remote_model_downloads: bool = field(default_factory=lambda: _env_bool('PHOENIXGUARD_VOICE_ALLOW_REMOTE_DOWNLOADS', False))
    require_local_files_only: bool = field(default_factory=lambda: _env_bool('PHOENIXGUARD_VOICE_REQUIRE_LOCAL_FILES_ONLY', True))
    require_safetensors_for_heavy_models: bool = field(default_factory=lambda: _env_bool('PHOENIXGUARD_VOICE_REQUIRE_SAFETENSORS', True))
    forbid_cpu_offload_for_heavy_models: bool = field(default_factory=lambda: _env_bool('PHOENIXGUARD_VOICE_FORBID_CPU_OFFLOAD', True))
    max_cpu_memory_mb: int = field(default_factory=lambda: _env_int('PHOENIXGUARD_VOICE_MAX_CPU_MEMORY_MB', 1024))
    preferred_device: str = field(default_factory=lambda: _env_str('PHOENIXGUARD_VOICE_DEVICE', 'cuda'))
    wake_word: str = field(default_factory=lambda: _env_str('PHOENIXGUARD_VOICE_WAKE_WORD', 'Hey 808'))
    greeting_target_name: str = field(default_factory=lambda: _env_str('PHOENIXGUARD_VOICE_GREETING_TARGET', 'Master'))
    timezone_name: str = field(default_factory=lambda: _env_str('PHOENIXGUARD_VOICE_TIMEZONE', ''))
    low_latency_mode: bool = field(default_factory=lambda: _env_bool('PHOENIXGUARD_VOICE_LOW_LATENCY_MODE', True))
    remote_enabled: bool = field(default_factory=lambda: _env_bool('PHOENIXGUARD_VOICE_REMOTE_ENABLED', True))
    remote_base_url: str = field(default_factory=lambda: _env_str('PHOENIXGUARD_VOICE_REMOTE_BASE_URL', ''))
    remote_timeout_sec: int = field(default_factory=lambda: _env_int('PHOENIXGUARD_VOICE_REMOTE_TIMEOUT_SEC', 8))
    tracker_api_base_url: str = field(default_factory=lambda: _env_str('PHOENIXGUARD_VOICE_TRACKER_API_BASE_URL', f"http://127.0.0.1:{_env_int('PHOENIXGUARD_MOBILE_API_PORT', 8791)}"))
    tracker_session_id: str = field(default_factory=lambda: _env_str('PHOENIXGUARD_VOICE_TRACKER_SESSION_ID', 'pocket-live-8788'))
    tracker_interval_sec_default: float = field(default_factory=lambda: _env_float('PHOENIXGUARD_VOICE_TRACKER_INTERVAL_SEC', 3.0))
    sensitive_data_guard_enabled: bool = field(default_factory=lambda: _env_bool('PHOENIXGUARD_VOICE_SENSITIVE_GUARD', True))
    wake_word_bundle_name: str = field(default_factory=lambda: _env_str('PHOENIXGUARD_VOICE_WAKE_WORD_BUNDLE', 'openwakeword-local'))
    speech_to_text_bundle_name: str = field(default_factory=lambda: _env_str('PHOENIXGUARD_VOICE_STT_BUNDLE', 'whisper-large-v3-local'))
    brain_bundle_name: str = field(default_factory=lambda: _env_str('PHOENIXGUARD_VOICE_BRAIN_BUNDLE', 'qwen3-8b-instruct-local'))
    speech_bundle_name: str = field(default_factory=lambda: _env_str('PHOENIXGUARD_VOICE_TTS_BUNDLE', 'openvoice-v2-local'))
    bundle_root: Path = field(init=False)
    profile_root: Path = field(init=False)
    reference_clip_dir: Path = field(init=False)
    logs_dir: Path = field(init=False)
    cache_dir: Path = field(init=False)
    state_path: Path = field(init=False)
    command_history_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.bundle_root = self.project_root / 'models' / 'voice'
        self.profile_root = self.project_root / 'data' / 'voice_profiles'
        self.reference_clip_dir = self.profile_root / 'reference'
        self.logs_dir = self.project_root / 'logs' / 'voice'
        self.cache_dir = self.project_root / '.hf_cache' / 'voice'
        self.state_path = self.project_root / 'data' / 'voice_runtime_state.json'
        self.command_history_path = self.project_root / 'data' / 'voice_command_history.jsonl'
        normalized_device = str(self.preferred_device or 'cuda').strip().lower()
        if normalized_device not in {'cuda', 'cpu', 'auto'}:
            normalized_device = 'cuda'
        self.preferred_device = normalized_device
        self.max_cpu_memory_mb = max(0, int(self.max_cpu_memory_mb))
        self.remote_timeout_sec = max(1, int(self.remote_timeout_sec))
        self.tracker_interval_sec_default = min(10.0, max(0.5, float(self.tracker_interval_sec_default)))
        for path in (
            self.bundle_root,
            self.profile_root,
            self.reference_clip_dir,
            self.logs_dir,
            self.cache_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(slots=True)
class SecurityConfig:
    kdf_iterations: int = 600_000
    prefs_db_path: str = 'preferences.enc.sqlite'
    log_hash_chain_file: str = 'audit_hash_chain.log'
    salt_file: str = 'kdf_salt.bin'


@dataclass(slots=True)
class TrainConfig:
    dora_rank: int = 8
    dora_alpha: int = 16
    ewc_lambda: float = 0.35
    tta_entropy_steps: int = 2
    tta_step_ms_budget: int = 40
    grpo_batch_size: int = 4
    reward_direction_match: float = 1.0
    reward_candle_count_correct: float = 0.5
    reward_memory_recall: float = 1.0
    lwf_temperature: float = 2.0
    lwf_loss_weight: float = 0.30
    replay_buffer_size: int = 1500
    ewc_fisher_batches: int = 8
    lora_rank: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    rl_learning_rate: float = 3e-4
    rl_feedback_batch_size: int = 16
    rl_replay_window: int = 256
    rl_min_feedback_before_blend: int = 4
    rl_policy_blend_cap: float = 0.28
    rl_prior_kl_weight: float = 0.18
    rl_entropy_bonus: float = 0.01


MODELS = ModelConfig()
MEMORY_BANK = MemoryBankConfig()
RUNTIME = RuntimeConfig()
VOICE = VoiceConfig()
SECURITY = SecurityConfig()
TRAIN = TrainConfig()
