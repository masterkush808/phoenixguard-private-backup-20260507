"""
PhoenixGuard SIGE-VLA 3.0 - Personalization Engine
===================================================
Strictly typed personalization layer with optional realtime controls.
"""
from __future__ import annotations

import json
import importlib
import os
from pathlib import Path
from typing import Any, Mapping, Protocol, cast

import numpy as np
from numpy.typing import NDArray

from phoenixguard.core.utils import can_import_sentence_transformers_safely, utc_now_iso


class PreferenceStore(Protocol):
    def insert_preference(self, row: dict[str, str], /) -> None: ...
    def fetch_recent(self, limit: int = 200, /) -> list[dict[str, str]]: ...


class PersonalizationEngine:
    def __init__(
        self,
        style_model_name: str,
        pref_store: PreferenceStore,
        logger: Any,
        meta_profile_path: str | Path | None = None,
    ) -> None:
        self.style_model_name = style_model_name
        self.pref_store = pref_store
        self.logger = logger
        self.embedder: Any | None = None
        self._embedder_load_attempted = False
        self.style_vector: NDArray[np.float32] = np.zeros((384,), dtype=np.float32)
        self.meta_profile_path = Path(meta_profile_path) if meta_profile_path is not None else None
        self._context_profiles: dict[str, dict[str, Any]] = {}
        self._memory_bank_stats: dict[str, Any] = {}
        self._runtime_controls: dict[str, float] = {
            'memory_weight': 1.0,
            'macro_weight': 1.0,
            'local_weight': 1.0,
            'risk_tolerance': 0.5,
            'overlay_threshold': 0.5,
        }
        self._load_context_profiles()

    def _load_context_profiles(self) -> None:
        if self.meta_profile_path is None or not self.meta_profile_path.exists():
            return
        try:
            raw = json.loads(self.meta_profile_path.read_text(encoding='utf-8'))
            if isinstance(raw, dict):
                self._context_profiles = dict(cast(Mapping[str, dict[str, Any]], raw))
        except Exception:
            self._context_profiles = {}

    def _save_context_profiles(self) -> None:
        if self.meta_profile_path is None:
            return
        try:
            self.meta_profile_path.parent.mkdir(parents=True, exist_ok=True)
            self.meta_profile_path.write_text(
                json.dumps(self._context_profiles, ensure_ascii=True, indent=2, sort_keys=True),
                encoding='utf-8',
            )
        except Exception as exc:
            self.logger.warning('Context profile save failed: %s', exc)

    def _ensure_embedder(self) -> Any | None:
        if self._embedder_load_attempted:
            return self.embedder
        self._embedder_load_attempted = True
        if not can_import_sentence_transformers_safely():
            self.embedder = None
            self.logger.warning('Style embedder unavailable, zero-vector fallback: runtime probe failed')
            return self.embedder
        try:
            from sentence_transformers import SentenceTransformer
            allow_remote_bootstrap = str(
                os.getenv("PHOENIXGUARD_TEXT_EMBEDDER_ALLOW_REMOTE_BOOTSTRAP", "0") or "0"
            ).strip().lower() in {"1", "true", "yes", "on"}
            force_download = str(
                os.getenv("PHOENIXGUARD_TEXT_EMBEDDER_FORCE_DOWNLOAD", "0") or "0"
            ).strip().lower() in {"1", "true", "yes", "on"}
            embedder_kwargs: dict[str, Any] = {
                "local_files_only": bool(not allow_remote_bootstrap and not force_download),
            }
            self.embedder = SentenceTransformer(self.style_model_name, **embedder_kwargs)
            self.logger.info('Loaded style embedder: %s', self.style_model_name)
        except Exception as exc:
            self.embedder = None
            self.logger.warning('Style embedder unavailable, zero-vector fallback: %s', exc)
        return self.embedder

    def _encode(self, text: str) -> NDArray[np.float32]:
        if not text:
            return np.zeros_like(self.style_vector, dtype=np.float32)
        self._ensure_embedder()
        if self.embedder is None:
            raw = np.frombuffer(text.encode('utf-8'), dtype=np.uint8).astype(np.float32)
            out = np.zeros_like(self.style_vector, dtype=np.float32)
            if raw.size > 0:
                upper = min(int(out.size), int(raw.size))
                out[:upper] = raw[:upper] / 255.0
            return out
        embedding = cast(np.ndarray[Any, Any], self.embedder.encode([text], normalize_embeddings=True))[0]
        return np.asarray(embedding, dtype=np.float32)

    def update_style(self, user_text: str, annotation_text: str = '') -> NDArray[np.float32]:
        merged = (user_text + ' ' + annotation_text).strip()
        vec = self._encode(merged)
        self.style_vector = (0.85 * self.style_vector + 0.15 * vec).astype(np.float32)
        return self.style_vector

    def adapt_style_for_context(
        self,
        base_vector: NDArray[np.float32],
        context_key: str,
        context_descriptor: str,
    ) -> tuple[NDArray[np.float32], dict[str, Any]]:
        profile = dict(self._context_profiles.get(str(context_key), {}))
        residual_raw = np.asarray(profile.get('residual', []), dtype=np.float32).reshape(-1)
        residual = np.zeros_like(base_vector, dtype=np.float32)
        if residual_raw.size > 0:
            upper = min(int(base_vector.size), int(residual_raw.size))
            residual[:upper] = residual_raw[:upper]
        usage = float(np.clip(profile.get('usage', 0.0), 0.0, 32.0))
        scale = float(np.clip(0.06 + 0.02 * usage, 0.0, 0.18))
        adapted = (base_vector + scale * residual).astype(np.float32)
        norm = float(np.linalg.norm(adapted))
        if norm > 1e-8:
            adapted = (adapted / norm).astype(np.float32)
        return adapted, {
            'context_key': str(context_key),
            'context_descriptor': str(context_descriptor),
            'applied': bool(residual_raw.size > 0),
            'usage': usage,
            'scale': scale,
        }

    def record_context_feedback(
        self,
        context_key: str,
        context_descriptor: str,
        chosen: str,
        reason: str,
        annotation_text: str = '',
    ) -> None:
        merged = ' '.join(part for part in [context_key, context_descriptor, chosen, reason, annotation_text] if part).strip()
        vec = self._encode(merged)
        profile = dict(self._context_profiles.get(str(context_key), {}))
        residual_raw = np.asarray(profile.get('residual', []), dtype=np.float32).reshape(-1)
        residual = np.zeros_like(self.style_vector, dtype=np.float32)
        if residual_raw.size > 0:
            upper = min(int(self.style_vector.size), int(residual_raw.size))
            residual[:upper] = residual_raw[:upper]
        residual = (0.82 * residual + 0.18 * vec).astype(np.float32)
        profile.update(
            {
                'residual': residual.astype(np.float32).tolist(),
                'usage': int(profile.get('usage', 0) or 0) + 1,
                'descriptor': str(context_descriptor),
                'last_choice': str(chosen),
                'last_reason': str(reason),
            }
        )
        self._context_profiles[str(context_key)] = profile
        self._save_context_profiles()

    def update_style_from_memory_bank(self, dpo_pairs: list[dict[str, Any]]) -> NDArray[np.float32]:
        if not dpo_pairs:
            return self.style_vector
        chosen_texts = [str(pair.get('chosen', '')) for pair in dpo_pairs if str(pair.get('chosen', '')).strip()]
        if not chosen_texts:
            return self.style_vector
        self._ensure_embedder()
        if self.embedder is not None:
            try:
                vectors = cast(np.ndarray[Any, Any], self.embedder.encode(chosen_texts, normalize_embeddings=True, batch_size=32))
                mean_vec = np.mean(np.asarray(vectors, dtype=np.float32), axis=0).astype(np.float32)
            except Exception:
                mean_vec = np.mean(np.stack([self._encode(text) for text in chosen_texts], axis=0), axis=0).astype(np.float32)
        else:
            mean_vec = np.mean(np.stack([self._encode(text) for text in chosen_texts], axis=0), axis=0).astype(np.float32)
        self.style_vector = (0.70 * self.style_vector + 0.30 * mean_vec).astype(np.float32)
        self.logger.info('Style vector updated from memory bank: %d DPO pairs', len(dpo_pairs))
        return self.style_vector

    def set_runtime_controls(self, **kwargs: float) -> dict[str, float]:
        for key, value in kwargs.items():
            if key in self._runtime_controls:
                self._runtime_controls[key] = float(np.clip(value, 0.0, 2.0))
        return dict(self._runtime_controls)

    def runtime_controls_snapshot(self) -> dict[str, float]:
        return dict(self._runtime_controls)

    def style_prefix_prompt(self) -> str:
        anchors = self.style_vector[:16]
        anchor_str = ','.join(f'{value:.4f}' for value in anchors)
        stats = self._memory_bank_stats
        controls = self._runtime_controls
        bank_summary = ''
        if stats:
            bank_summary = (
                f" | MemoryBank: {stats.get('total_entries', 0)} entries, "
                f"archetypes={stats.get('archetype_count', 0)}, "
                f"top_label={stats.get('dominant_label', 'N/A')}"
            )
        control_summary = (
            f" | Controls: memory={controls['memory_weight']:.2f}, macro={controls['macro_weight']:.2f}, "
            f"local={controls['local_weight']:.2f}, risk={controls['risk_tolerance']:.2f}"
        )
        return f'User trading style embedding anchors: [{anchor_str}]{bank_summary}{control_summary}'

    def update_memory_bank_stats(self, stats: dict[str, Any]) -> None:
        self._memory_bank_stats = dict(stats)

    def record_feedback(self, image_hash: str, chosen: str, rejected: str, reason: str, annotation_text: str) -> None:
        self.pref_store.insert_preference(
            {
                'ts': utc_now_iso(),
                'image_hash': image_hash,
                'chosen': chosen,
                'rejected': rejected,
                'reason': reason,
                'annotation_text': annotation_text,
            }
        )
        self.update_style(reason, annotation_text)

    def generate_dpo_pairs(self, memory_bank: Any | None = None, n: int = 50) -> list[dict[str, Any]]:
        if memory_bank is not None and hasattr(memory_bank, 'generate_dpo_pairs'):
            pairs = cast(list[dict[str, Any]], memory_bank.generate_dpo_pairs(n=n))
            self.logger.info('Generated %d DPO pairs from memory bank', len(pairs))
            return pairs
        recent = self.pref_store.fetch_recent(n * 2)
        return [
            {
                'chosen': str(row.get('chosen', '')),
                'rejected': str(row.get('rejected', '')),
                'reason': str(row.get('reason', '')),
                'ts': str(row.get('ts', '')),
            }
            for row in recent[:n]
        ]

    def recent_feedback_count(self) -> int:
        return len(self.pref_store.fetch_recent(10_000))

    def build_online_batch(self, state_vec: NDArray[np.float32], predicted_action: str, user_correct: bool) -> list[dict[str, Any]]:
        target = predicted_action if user_correct else ('HOLD' if predicted_action != 'HOLD' else 'BUY')
        return [{'state': state_vec.astype(np.float32), 'target_action': target}]

    def build_plotly_dashboard(
        self,
        gate_scores: dict[str, float],
        shap_contributions: dict[str, float] | None = None,
        candle_accuracy: float | None = None,
    ) -> Any | None:
        try:
            go = cast(Any, importlib.import_module('plotly.graph_objects'))
            make_subplots = cast(Any, importlib.import_module('plotly.subplots')).make_subplots
        except ImportError:
            return None

        gate_names = list(gate_scores.keys())
        gate_values = [float(gate_scores[name]) for name in gate_names]
        shap = shap_contributions or {name: 0.0 for name in gate_names}
        shap_vals = [float(shap.get(name, 0.0)) for name in gate_names]

        fig = make_subplots(
            rows=1,
            cols=2,
            specs=[[{'type': 'polar'}, {'type': 'xy'}]],
            subplot_titles=['Skill Gate Scores (Radar)', 'Approx. SHAP Contributions'],
        )
        fig.add_trace(
            go.Scatterpolar(
                r=gate_values + [gate_values[0] if gate_values else 0.0],
                theta=gate_names + [gate_names[0] if gate_names else 'none'],
                fill='toself',
                name='Gate Scores',
                line_color='#00ff88',
            ),
            row=1,
            col=1,
        )
        sorted_idx = np.argsort(np.asarray(shap_vals, dtype=np.float32))[::-1]
        sorted_names = [gate_names[int(index)] for index in sorted_idx] if gate_names else []
        sorted_shap = [float(shap_vals[int(index)]) for index in sorted_idx] if shap_vals else []
        fig.add_trace(
            go.Bar(
                x=sorted_shap,
                y=sorted_names,
                orientation='h',
                marker_color='#ff8800',
                name='SHAP',
            ),
            row=1,
            col=2,
        )
        title_suffix = f" | Candle Accuracy: {candle_accuracy * 100.0:.1f}%" if candle_accuracy is not None else ''
        fig.update_layout(
            template='plotly_dark',
            title_text=f'PhoenixGuard 3.0 — Skill Contribution Dashboard{title_suffix}',
            height=500,
            margin=dict(l=20, r=20, t=60, b=20),
        )
        return fig
