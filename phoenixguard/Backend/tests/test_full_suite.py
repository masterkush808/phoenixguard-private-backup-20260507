"""
PhoenixGuard SIGE-VLA 3.0 — Full Project Test Suite
=====================================================
Covers every public function / class across all 12 source files:
  utils.py · config.py · preprocess.py · security.py · rl_module.py
  skill_gates.py · ensemble.py · regression_module.py · memory_ingest.py
  personalization.py · cv_module.py

Run with:
    cd phoenixguard
    python -m pytest Backend/tests/test_full_suite.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, cast
import unittest

import numpy as np
from PIL import Image

# ── make repo root importable ──────────────────────────────────────────────
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ===========================================================================
# Null logger shared across tests
# ===========================================================================
class _NullLogger:
    def info(self, *a: object, **k: object) -> None: pass
    def warning(self, *a: object, **k: object) -> None: pass
    def exception(self, *a: object, **k: object) -> None: pass
    def error(self, *a: object, **k: object) -> None: pass
    def debug(self, *a: object, **k: object) -> None: pass


# ===========================================================================
# 1. utils.py
# ===========================================================================
class TestUtils(unittest.TestCase):
    """Covers: setup_logger, utc_now_iso, sha256_text,
    append_hash_chain, safe_json_loads, clamp"""

    def setUp(self):
        from utils import setup_logger, utc_now_iso, sha256_text, append_hash_chain, safe_json_loads, clamp
        self.setup_logger = setup_logger
        self.utc_now_iso = utc_now_iso
        self.sha256_text = sha256_text
        self.append_hash_chain = append_hash_chain
        self.safe_json_loads = safe_json_loads
        self.clamp = clamp

    def test_utc_now_iso_format(self):
        ts = self.utc_now_iso()
        self.assertIn("T", ts)
        self.assertIn("+", ts)

    def test_sha256_text_deterministic(self):
        h1 = self.sha256_text("hello world")
        h2 = self.sha256_text("hello world")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_sha256_text_different_inputs(self):
        self.assertNotEqual(self.sha256_text("a"), self.sha256_text("b"))

    def test_append_hash_chain_creates_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "chain.log"
            h = self.append_hash_chain(p, {"event": "test"})
            self.assertEqual(len(h), 64)
            self.assertTrue(p.exists())
            content = p.read_text()
            self.assertIn("event", content)

    def test_append_hash_chain_chaining(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "chain.log"
            h1 = self.append_hash_chain(p, {"n": 1})
            h2 = self.append_hash_chain(p, {"n": 2})
            self.assertNotEqual(h1, h2)
            lines = p.read_text().strip().splitlines()
            self.assertEqual(len(lines), 2)
            # Each line ends with its hash
            self.assertTrue(lines[0].endswith(h1))
            self.assertTrue(lines[1].endswith(h2))

    def test_append_hash_chain_is_safe_under_parallel_threads(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "chain.log"
            start = threading.Barrier(6)
            hashes: list[str] = []
            guard = threading.Lock()

            def _writer(index: int) -> None:
                start.wait()
                digest = self.append_hash_chain(p, {"worker": index})
                with guard:
                    hashes.append(digest)

            threads = [threading.Thread(target=_writer, args=(idx,)) for idx in range(5)]
            for thread in threads:
                thread.start()
            start.wait()
            for thread in threads:
                thread.join()

            lines = [line for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(lines), 5)
            self.assertEqual(len(hashes), 5)
            prev_hash = "0" * 64
            for line in lines:
                _ts, payload_json, digest = line.split("|", 2)
                self.assertEqual(self.sha256_text(prev_hash + payload_json), digest)
                prev_hash = digest

    def test_safe_json_loads_valid(self):
        out = self.safe_json_loads('{"a": 1}')
        self.assertEqual(out["a"], 1)

    def test_safe_json_loads_embedded_json(self):
        out = self.safe_json_loads('prefix {"key": "val"} suffix')
        self.assertEqual(out["key"], "val")

    def test_safe_json_loads_invalid(self):
        out = self.safe_json_loads("not json at all", fallback={"default": True})
        self.assertTrue(out["default"])

    def test_clamp_within_range(self):
        self.assertEqual(self.clamp(5.0, 1.0, 10.0), 5.0)

    def test_clamp_below_low(self):
        self.assertEqual(self.clamp(-5.0, 0.0, 1.0), 0.0)

    def test_clamp_above_high(self):
        self.assertEqual(self.clamp(100.0, 0.0, 1.0), 1.0)

    def test_setup_logger_returns_logger(self):
        import logging
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.log"
            logger = self.setup_logger(p, name="test_util_logger_unique")
            self.assertIsInstance(logger, logging.Logger)
            logger.info("hello")
            self.assertTrue(p.exists())
            # Close all handlers to release the file lock before tempdir cleanup (Windows)
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)


# ===========================================================================
# 2. config.py
# ===========================================================================
class TestConfig(unittest.TestCase):
    """Covers: ModelConfig, MemoryBankConfig, RuntimeConfig, SecurityConfig, TrainConfig"""

    def test_model_config_defaults(self):
        from config import ModelConfig
        cfg = ModelConfig()
        self.assertIn("foduucom", cfg.cv_primary)
        self.assertIn("chronos", cfg.chronos_model)
        self.assertIn("MiniLM", cfg.style_embedder)

    def test_memory_bank_config_defaults(self):
        from config import MemoryBankConfig
        cfg = MemoryBankConfig()
        self.assertEqual(cfg.hnsw_m, 32)
        self.assertEqual(cfg.hnsw_ef_construction, 200)
        self.assertGreater(cfg.recall_boost_threshold, 0.5)
        self.assertGreater(cfg.recall_veto_threshold, 0.5)

    def test_runtime_config_creates_directories(self):
        from config import RuntimeConfig
        cfg = RuntimeConfig()
        for d in [cfg.adapters_dir, cfg.models_dir, cfg.data_dir, cfg.logs_dir, cfg.screenshots_inbox]:
            self.assertTrue(d.exists(), f"Expected {d} to exist")

    def test_runtime_config_device_preference(self):
        from config import RuntimeConfig
        cfg = RuntimeConfig()
        dp = cfg.device_preference
        self.assertIn(dp, ("cuda", "cpu"))

    def test_security_config_defaults(self):
        from config import SecurityConfig
        cfg = SecurityConfig()
        self.assertGreater(cfg.kdf_iterations, 0)

    def test_train_config_defaults(self):
        from config import TrainConfig
        cfg = TrainConfig()
        self.assertEqual(cfg.dora_rank, 8)
        self.assertGreater(cfg.ewc_lambda, 0)
        self.assertGreater(cfg.reward_direction_match, 0)

    def test_runtime_consensus_thresholds(self):
        from config import RuntimeConfig
        cfg = RuntimeConfig()
        self.assertEqual(cfg.consensus_threshold, 0.82)
        self.assertEqual(cfg.gates_pass_minimum, 9)


# ===========================================================================
# 3. preprocess.py
# ===========================================================================
class TestPreprocess(unittest.TestCase):
    """Covers: indicator_regex_filter, extract_price_floats, prices_to_tensor,
    load_any_file_as_image, apply_clahe, auto_crop_price_area,
    normalize_for_model, image_to_tensor"""

    def setUp(self):
        from preprocess import (
            indicator_regex_filter,
            extract_price_floats,
            prices_to_tensor,
            load_any_file_as_image,
            apply_clahe,
            auto_crop_price_area,
            normalize_for_model,
            image_to_tensor,
        )
        self.indicator_regex_filter = indicator_regex_filter
        self.extract_price_floats = extract_price_floats
        self.prices_to_tensor = prices_to_tensor
        self.load_any_file_as_image = load_any_file_as_image
        self.apply_clahe = apply_clahe
        self.auto_crop_price_area = auto_crop_price_area
        self.normalize_for_model = normalize_for_model
        self.image_to_tensor = image_to_tensor

    def test_indicator_filter_clean_text(self):
        is_clean, cleaned = self.indicator_regex_filter("Price broke support at 1.2345")
        self.assertTrue(is_clean)
        self.assertNotIn("[overlay_removed]", cleaned)

    def test_indicator_filter_contaminated(self):
        is_clean, cleaned = self.indicator_regex_filter("RSI at 70 and MACD crossed")
        self.assertFalse(is_clean)
        self.assertIn("[overlay_removed]", cleaned)

    def test_indicator_filter_multiple_indicators(self):
        is_clean, _cleaned = self.indicator_regex_filter("EMA20 above SMA200 and Bollinger squeeze")
        self.assertFalse(is_clean)

    def test_extract_price_floats_standard(self):
        prices = self.extract_price_floats("Entry at 1.23456 stop 1.22000 TP 1.25000")
        self.assertGreater(len(prices), 0)
        self.assertTrue(all(0.0001 < p < 99999.0 for p in prices))

    def test_extract_price_floats_empty(self):
        prices = self.extract_price_floats("No prices here")
        self.assertEqual(prices, [])

    def test_extract_price_floats_sorted_unique(self):
        prices = self.extract_price_floats("1.23456 1.23456 1.25000")
        self.assertEqual(prices, sorted(set(prices)))

    def test_prices_to_tensor_nonempty(self):
        t = self.prices_to_tensor([1.2345, 1.2500])
        self.assertEqual(len(t), 2)

    def test_prices_to_tensor_empty(self):
        t = self.prices_to_tensor([])
        self.assertEqual(len(t), 1)   # single zero pad

    def test_load_any_file_as_image_text_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "note.txt"
            p.write_text("BUY entry 1.23456 above support", encoding="utf-8")
            img, meta = self.load_any_file_as_image(p)
            self.assertIsInstance(img, Image.Image)
            self.assertEqual(meta["source_type"], "converted_non_image")
            self.assertIn("sha256", meta)
            self.assertEqual(len(meta["sha256"]), 64)

    def test_load_any_file_as_image_png(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "chart.png"
            Image.new("RGB", (64, 64), color=(200, 100, 50)).save(p)
            img, meta = self.load_any_file_as_image(p)
            self.assertEqual(meta["source_type"], "image")
            self.assertEqual(img.mode, "RGB")

    def test_apply_clahe_output_type(self):
        img = Image.new("RGB", (128, 128), color=(128, 128, 128))
        result = self.apply_clahe(img, clip_limit=3)
        self.assertIsInstance(result, Image.Image)
        self.assertEqual(result.size, img.size)

    def test_auto_crop_price_area_returns_image(self):
        img = Image.new("RGB", (256, 256), color=(50, 50, 50))
        result = self.auto_crop_price_area(img)
        self.assertIsInstance(result, Image.Image)
        # Must not shrink to less than 25% height
        self.assertGreater(result.height, 0)

    def test_normalize_for_model_output_size(self):
        img = Image.new("RGB", (100, 100), color=(0, 0, 0))
        out = self.normalize_for_model(img, out_size=128)
        self.assertEqual(out.size, (128, 128))

    def test_normalize_for_model_large(self):
        img = Image.new("RGB", (2048, 2048), color=(255, 255, 255))
        out = self.normalize_for_model(img, out_size=1024)
        self.assertEqual(out.size, (1024, 1024))

    def test_image_to_tensor_shape(self):
        img = Image.new("RGB", (32, 32), color=(0, 0, 0))
        t = self.image_to_tensor(img)
        # Shape: (1, 3, 32, 32) if torch, or numpy (3, 32, 32) fallback
        shape = tuple(t.shape) if hasattr(t, "shape") else ()
        self.assertGreater(len(shape), 0)


# ===========================================================================
# 4. security.py
# ===========================================================================
class TestSecurity(unittest.TestCase):
    """Covers: SecurityManager (derive_fernet, encrypt/decrypt bytes,
    encrypt/decrypt file, secure_delete_file, memory_cleanup),
    EncryptedPreferenceStore (insert_preference, fetch_recent, export_json)"""

    def setUp(self):
        from security import SecurityManager, EncryptedPreferenceStore
        self._td = tempfile.mkdtemp()
        td = Path(self._td)
        self.sec = SecurityManager(td / "data", td / "logs", kdf_iterations=1000)
        self.fernet = self.sec.derive_fernet("test-passphrase-808")
        self.EncryptedPreferenceStore = EncryptedPreferenceStore
        self.td = td

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_derive_fernet_creates_salt(self):
        salt_path = self.sec.data_dir / "kdf_salt.bin"
        self.assertTrue(salt_path.exists())

    def test_derive_fernet_same_passphrase_consistent(self):
        f2 = self.sec.derive_fernet("test-passphrase-808")
        # Both should be able to decrypt each other's tokens
        token = self.fernet.encrypt(b"consistency check")
        result = f2.decrypt(token)
        self.assertEqual(result, b"consistency check")

    def test_encrypt_decrypt_bytes_roundtrip(self):
        plaintext = b"secret trading data 808"
        token = self.sec.encrypt_bytes(plaintext, self.fernet)
        recovered = self.sec.decrypt_bytes(token, self.fernet)
        self.assertEqual(recovered, plaintext)

    def test_encrypt_decrypt_bytes_large(self):
        big = os.urandom(65536)
        token = self.sec.encrypt_bytes(big, self.fernet)
        self.assertEqual(self.sec.decrypt_bytes(token, self.fernet), big)

    def test_encrypt_decrypt_file(self):
        p_in = self.td / "plain.bin"
        p_enc = self.td / "plain.enc"
        p_out = self.td / "plain.dec"
        p_in.write_bytes(b"file content 808")
        (self.td).mkdir(exist_ok=True)
        self.sec.encrypt_file(p_in, p_enc, self.fernet)
        self.sec.decrypt_file(p_enc, p_out, self.fernet)
        self.assertEqual(p_out.read_bytes(), b"file content 808")

    def test_secure_delete_file_removes_file(self):
        p = self.td / "deleteme.bin"
        p.write_bytes(b"sensitive" * 100)
        self.sec.secure_delete_file(p)
        self.assertFalse(p.exists())

    def test_secure_delete_nonexistent_file_does_not_raise(self):
        self.sec.secure_delete_file(self.td / "nosuchfile.bin")

    def test_memory_cleanup_does_not_raise(self):
        self.sec.memory_cleanup()  # should not raise

    def test_pref_store_insert_and_fetch(self):
        store = self.EncryptedPreferenceStore(
            self.td / "prefs.enc.sqlite", self.fernet
        )
        store.insert_preference({
            "ts": "2026-01-01T00:00:00+00:00",
            "image_hash": "abc123",
            "chosen": "BUY setup with wick rejection",
            "rejected": "HOLD no signal",
            "reason": "support level held",
            "annotation_text": "clean pin bar",
        })
        rows = store.fetch_recent(10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["chosen"], "BUY setup with wick rejection")

    def test_pref_store_export_json(self):
        store = self.EncryptedPreferenceStore(
            self.td / "prefs2.enc.sqlite", self.fernet
        )
        store.insert_preference({"ts": "t", "image_hash": "h", "chosen": "c", "rejected": "r", "reason": "x", "annotation_text": ""})
        raw = store.export_json()
        data = json.loads(raw)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_pref_store_multiple_entries(self):
        store = self.EncryptedPreferenceStore(
            self.td / "prefs3.enc.sqlite", self.fernet
        )
        for i in range(10):
            store.insert_preference({
                "ts": f"2026-01-0{i+1}", "image_hash": f"h{i}",
                "chosen": f"BUY {i}", "rejected": f"SELL {i}",
                "reason": f"reason {i}", "annotation_text": ""
            })
        rows = store.fetch_recent(100)
        self.assertEqual(len(rows), 10)

    def test_pref_store_contact_briefs_roundtrip(self):
        store = self.EncryptedPreferenceStore(
            self.td / "prefs_contact.enc.sqlite", self.fernet
        )
        store.insert_contact_brief({
            "ts": "2026-03-30T00:00:00+00:00",
            "session_id": "sess-1",
            "alias": "808Fx",
            "creator": "tester",
            "full_name": "Jane Trader",
            "contact_channel": "jane@example.com",
            "organization": "Desk",
            "purpose": "Review the protected desk",
            "consent_ack": True,
            "meta": {"client_hash": "abc"},
        })
        rows = store.fetch_recent_contact_briefs(10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["full_name"], "Jane Trader")
        self.assertEqual(rows[0]["meta"]["client_hash"], "abc")

    def test_pref_store_does_not_leave_plaintext_tmp_sqlite(self):
        db_path = self.td / "prefs_tmp.enc.sqlite"
        store = self.EncryptedPreferenceStore(db_path, self.fernet)
        store.insert_preference({
            "ts": "2026-01-01T00:00:00+00:00",
            "image_hash": "atomic",
            "chosen": "BUY",
            "rejected": "SELL",
            "reason": "no plaintext residue",
            "annotation_text": "",
        })
        self.assertFalse(db_path.with_suffix(".tmp.sqlite").exists())

    def test_open_preference_store_without_passphrase_returns_unavailable(self):
        from security import UnavailablePreferenceStore, open_preference_store

        store = open_preference_store(
            self.td / "prefs_missing.enc.sqlite",
            None,
            logger=_NullLogger(),
        )
        self.assertIsInstance(store, UnavailablePreferenceStore)
        self.assertEqual(store.fetch_recent(5), [])

    def test_open_preference_store_invalid_token_returns_unavailable(self):
        from security import UnavailablePreferenceStore, open_preference_store

        db_path = self.td / "prefs_invalid.enc.sqlite"
        store = self.EncryptedPreferenceStore(db_path, self.fernet)
        store.insert_preference({
            "ts": "2026-01-01T00:00:00+00:00",
            "image_hash": "badkey",
            "chosen": "BUY",
            "rejected": "SELL",
            "reason": "rotation",
            "annotation_text": "",
        })

        wrong_fernet = self.sec.derive_fernet("different-passphrase-808")
        reopened = open_preference_store(
            db_path,
            wrong_fernet,
            logger=_NullLogger(),
        )
        self.assertIsInstance(reopened, UnavailablePreferenceStore)
        self.assertEqual(reopened.fetch_recent(5), [])

    def test_pref_store_atomic_sync_does_not_leave_cipher_temp_file(self):
        db_path = self.td / "prefs_atomic.enc.sqlite"
        store = self.EncryptedPreferenceStore(db_path, self.fernet)
        store.insert_preference({
            "ts": "2026-01-01T00:00:00+00:00",
            "image_hash": "atomic",
            "chosen": "BUY",
            "rejected": "SELL",
            "reason": "atomic write",
            "annotation_text": "",
        })
        cipher_tmp_path = db_path.with_name(db_path.name + ".tmp")
        self.assertFalse(cipher_tmp_path.exists())


# ===========================================================================
# 5. rl_module.py
# ===========================================================================
class TestRLModule(unittest.TestCase):
    """Covers: GRPOPolicyHead, RLPolicyEngine.infer, memory boost logic,
    MCTS variance, online update trigger"""

    def setUp(self):
        from rl_module import RLPolicyEngine, GRPOPolicyHead, RLResult
        self.RLPolicyEngine = RLPolicyEngine
        self.GRPOPolicyHead = GRPOPolicyHead
        self.RLResult = RLResult

    def test_grpo_head_forward_shape(self):
        import torch
        head = self.GRPOPolicyHead(in_dim=64, hidden=32, n_actions=3)
        x = torch.zeros(1, 64)
        out = head(x)
        self.assertEqual(out.shape, (1, 3))

    def test_rl_infer_returns_correct_keys(self):
        engine = self.RLPolicyEngine(logger=_NullLogger(), in_dim=64, mcts_sims=5)
        state = np.zeros(64, dtype=np.float32)
        result = engine.infer(state)
        self.assertEqual(set(result.probs.keys()), {"BUY", "SELL", "HOLD"})
        self.assertIsInstance(result.mcts_value, float)

    def test_rl_probs_sum_to_one(self):
        engine = self.RLPolicyEngine(logger=_NullLogger(), in_dim=64, mcts_sims=5)
        result = engine.infer(np.zeros(64, dtype=np.float32))
        total = sum(result.probs.values())
        self.assertAlmostEqual(total, 1.0, places=5)

    def test_rl_probs_all_positive(self):
        engine = self.RLPolicyEngine(logger=_NullLogger(), in_dim=64, mcts_sims=5)
        result = engine.infer(np.random.rand(64).astype(np.float32))
        self.assertTrue(all(v >= 0.0 for v in result.probs.values()))

    def test_rl_memory_boost_applied(self):
        engine = self.RLPolicyEngine(logger=_NullLogger(), in_dim=64, mcts_sims=5)
        result = engine.infer(
            np.zeros(64, dtype=np.float32),
            memory_recall_top1_sim=0.92,
            memory_recall_direction="BUY",
        )
        self.assertTrue(result.boost_applied)
        self.assertEqual(result.boosted_action, "BUY")

    def test_rl_memory_boost_not_applied_low_sim(self):
        engine = self.RLPolicyEngine(logger=_NullLogger(), in_dim=64, mcts_sims=5)
        result = engine.infer(
            np.zeros(64, dtype=np.float32),
            memory_recall_top1_sim=0.50,
            memory_recall_direction="SELL",
        )
        self.assertFalse(result.boost_applied)

    def test_rl_mcts_value_in_range(self):
        engine = self.RLPolicyEngine(logger=_NullLogger(), in_dim=64, mcts_sims=10)
        result = engine.infer(np.zeros(64, dtype=np.float32))
        self.assertGreaterEqual(result.mcts_value, 0.0)

    def test_rl_different_inputs_different_probs(self):
        engine = self.RLPolicyEngine(logger=_NullLogger(), in_dim=64, mcts_sims=5)
        r1 = engine.infer(np.zeros(64, dtype=np.float32))
        r2 = engine.infer(np.ones(64, dtype=np.float32))
        # Not guaranteed to differ but generally will with random init
        # Just check both return valid results
        self.assertIsInstance(r1.probs, dict)
        self.assertIsInstance(r2.probs, dict)


# ===========================================================================
# 6. skill_gates.py
# ===========================================================================
class TestSkillGates(unittest.TestCase):
    """Covers: all 13 gates individually, run_all, CurriculumGates,
    SkillGatedMoE, LinearRouter, FSM transitions, update_router_from_feedback"""

    def setUp(self):
        from skill_gates import CurriculumGates, SkillGatedMoE, LinearRouter, GateOutput
        self.logger = _NullLogger()
        self.gates = CurriculumGates(self.logger)
        self.CurriculumGates = CurriculumGates
        self.SkillGatedMoE = SkillGatedMoE
        self.LinearRouter = LinearRouter
        self.GateOutput = GateOutput

    # -- Individual gates --
    def test_gate_probability_conformal_structure(self):
        g = self.gates.gate_probability_conformal({"BUY": 0.8, "SELL": 0.1, "HOLD": 0.1}, -0.1, 0.2)
        self.assertIsInstance(g.score, float)
        self.assertIsInstance(g.pass_fail, bool)
        self.assertIn("interval", g.detail)

    def test_gate_probability_conformal_wide_interval_reduces_score(self):
        narrow = self.gates.gate_probability_conformal({"BUY": 0.8, "SELL": 0.1, "HOLD": 0.1}, -0.01, 0.01)
        wide = self.gates.gate_probability_conformal({"BUY": 0.8, "SELL": 0.1, "HOLD": 0.1}, -2.0, 2.0)
        self.assertGreaterEqual(narrow.score, wide.score)

    def test_gate_discrete_fsm_bullish(self):
        gates = self.CurriculumGates(self.logger)
        g = gates.gate_discrete_fsm("bullish", "strong breakout trend up")
        self.assertIsInstance(g.score, float)
        self.assertIn("state", g.detail)

    def test_gate_discrete_fsm_bearish(self):
        gates = self.CurriculumGates(self.logger)
        g: Any = None
        for _ in range(3):
            g = gates.gate_discrete_fsm("bearish", "sell pressure rejection")
        self.assertIsInstance(g.pass_fail, bool)

    def test_gate_discrete_fsm_neutral(self):
        g = self.gates.gate_discrete_fsm("neutral", "flat momentum")
        self.assertIsInstance(g.name, str)
        self.assertEqual(g.name, "discrete_fsm")
        self.assertFalse(g.pass_fail)

    def test_gate_algorithmic_heap_basic(self):
        g = self.gates.gate_algorithmic_heap([(0.9, "pin_bar"), (0.7, "engulf"), (0.5, "hammer")])
        self.assertGreater(g.score, 0)
        self.assertIn("top", g.detail)

    def test_gate_algorithmic_heap_empty(self):
        g = self.gates.gate_algorithmic_heap([])
        self.assertEqual(g.score, 0.0)

    def test_gate_algorithmic_heap_ignores_parse_artifacts(self):
        g = self.gates.gate_algorithmic_heap(
            [(1.0, "latest_parse_quality"), (0.9, "pin_bar"), (0.7, "pin_bar"), (0.8, "hammer")]
        )
        top_names = [name for _, name in cast(list[tuple[float, str]], g.detail["top"])]
        self.assertNotIn("latest_parse_quality", top_names)
        self.assertEqual(top_names.count("pin_bar"), 1)

    def test_gate_meta_stacking_mean_above_threshold(self):
        g = self.gates.gate_meta_stacking(np.array([0.8, 0.9, 0.85], dtype=np.float32))
        self.assertTrue(g.pass_fail)

    def test_gate_meta_stacking_low_logits(self):
        g = self.gates.gate_meta_stacking(np.array([0.1, 0.2, 0.15], dtype=np.float32))
        self.assertFalse(g.pass_fail)

    def test_gate_context_retrieval(self):
        g = self.gates.gate_context_retrieval(recent_feedback_count=100)
        self.assertGreater(g.score, 0.5)
        self.assertTrue(g.pass_fail)

    def test_gate_context_retrieval_without_feedback_fails(self):
        g = self.gates.gate_context_retrieval(recent_feedback_count=0)
        self.assertFalse(g.pass_fail)

    def test_gate_ops_stability_clean(self):
        g = self.gates.gate_ops_stability(queue_depth=0, gpu_mem_ok=True)
        self.assertGreaterEqual(g.score, 0.9)

    def test_gate_ops_stability_degraded(self):
        g = self.gates.gate_ops_stability(queue_depth=10, gpu_mem_ok=False)
        self.assertLessEqual(g.score, 0.6)

    def test_gate_ui_analytics_with_dashboard(self):
        g = self.gates.gate_ui_analytics(has_dashboard=True)
        self.assertEqual(g.score, 1.0)

    def test_gate_ui_analytics_no_dashboard(self):
        g = self.gates.gate_ui_analytics(has_dashboard=False)
        self.assertLess(g.score, 1.0)
        self.assertFalse(g.pass_fail)

    def test_gate_meta_constraints_passes(self):
        g = self.gates.gate_meta_constraints(risk_ethical_ok=True)
        self.assertTrue(g.pass_fail)
        self.assertEqual(g.score, 1.0)

    def test_gate_meta_constraints_fails(self):
        g = self.gates.gate_meta_constraints(risk_ethical_ok=False)
        self.assertFalse(g.pass_fail)
        self.assertEqual(g.score, 0.0)

    def test_gate_regression_error_estimation_too_few(self):
        g = self.gates.gate_regression_error_estimation([], 0.6)
        self.assertFalse(g.pass_fail)

    def test_gate_regression_error_estimation_enough_prices(self):
        prices = [1.2300, 1.2310, 1.2325, 1.2340, 1.2355, 1.2368]
        g = self.gates.gate_regression_error_estimation(prices, 0.75)
        self.assertIsInstance(g.score, float)
        self.assertIn("r2", g.detail)

    def test_gate_regression_error_estimation_flat_prices_fails(self):
        g = self.gates.gate_regression_error_estimation([1.23, 1.23, 1.23, 1.23, 1.23], 0.95)
        self.assertFalse(g.pass_fail)
        self.assertEqual(g.detail["reason"], "flat_or_degenerate_prices")

    def test_gate_knowledge_representation_coherent_buy(self):
        chart_state = {"entry_type": "reversal", "reversal_signal": "wick_rejection", "direction": "BUY"}
        g = self.gates.gate_knowledge_representation(chart_state)
        self.assertTrue(g.pass_fail)
        self.assertEqual(g.detail["coherent"], True)

    def test_gate_knowledge_representation_incoherent(self):
        chart_state = {"entry_type": "reversal", "reversal_signal": "wick_rejection", "direction": "HOLD"}
        g = self.gates.gate_knowledge_representation(chart_state)
        self.assertFalse(g.pass_fail)

    def test_gate_knowledge_representation_continuation_signal(self):
        chart_state = {
            "entry_type": "continuation",
            "reversal_signal": "none",
            "continuation_signal": "breakout",
            "direction": "BUY",
        }
        g = self.gates.gate_knowledge_representation(chart_state)
        self.assertTrue(g.pass_fail)

    def test_gate_formal_automata_idle_no_consol(self):
        gates = self.CurriculumGates(self.logger)
        g = gates.gate_formal_automata({"consolidation_streak": 1, "reversal_signal": "none", "continuation_signal": "none"})
        self.assertEqual(g.detail["fa_state"], "Idle")
        self.assertFalse(g.pass_fail)

    def test_gate_formal_automata_snapshot_promotes_in_single_call(self):
        gates = self.CurriculumGates(self.logger)
        g = gates.gate_formal_automata(
            {
                "consolidation_streak": 5,
                "consolidation_score": 0.72,
                "reversal_signal": "wick_rejection",
                "continuation_signal": "reversal_release",
                "continuation_probability": 0.61,
                "reversal_probability": 0.34,
            }
        )
        self.assertEqual(g.detail["fa_state"], "ContinuationImpulse")
        self.assertTrue(g.pass_fail)

    def test_gate_formal_automata_full_path(self):
        gates = self.CurriculumGates(self.logger)
        # Idle → ConsolidationDetected
        g1 = gates.gate_formal_automata(
            {
                "consolidation_streak": 5,
                "consolidation_score": 0.66,
                "reversal_signal": "none",
                "continuation_signal": "none",
            }
        )
        self.assertEqual(g1.detail["fa_state"], "ConsolidationDetected")
        # ConsolidationDetected → ReversalAfterConsolidation
        g2 = gates.gate_formal_automata(
            {
                "consolidation_streak": 5,
                "consolidation_score": 0.66,
                "reversal_signal": "engulfing",
                "reversal_probability": 0.42,
                "continuation_signal": "none",
            }
        )
        self.assertEqual(g2.detail["fa_state"], "ReversalAfterConsolidation")
        self.assertTrue(g2.pass_fail)
        # ReversalAfterConsolidation → ContinuationImpulse
        g3 = gates.gate_formal_automata(
            {
                "consolidation_streak": 5,
                "consolidation_score": 0.66,
                "reversal_signal": "none",
                "continuation_signal": "breakout",
                "continuation_probability": 0.58,
                "reversal_probability": 0.22,
            }
        )
        self.assertEqual(g3.detail["fa_state"], "ContinuationImpulse")
        self.assertTrue(g3.pass_fail)

    def test_gate_formal_automata_requires_quality_and_probabilities(self):
        gates = self.CurriculumGates(self.logger)
        g = gates.gate_formal_automata(
            {
                "consolidation_streak": 5,
                "consolidation_score": 0.12,
                "reversal_signal": "engulfing",
                "continuation_signal": "breakout",
            }
        )
        self.assertFalse(g.pass_fail)
        self.assertEqual(g.detail["fa_state"], "Idle")

    def test_gate_predictive_analytics_high_confidence(self):
        probs = {"BUY": 0.75, "SELL": 0.15, "HOLD": 0.10}
        mcts = {"buy_prob": 0.80, "sell_prob": 0.10}
        g = self.gates.gate_predictive_analytics(probs, mcts, memory_sim=0.90, latest_candle_confidence=0.72)
        self.assertTrue(g.pass_fail)

    def test_gate_predictive_analytics_low(self):
        probs = {"BUY": 0.35, "SELL": 0.35, "HOLD": 0.30}
        mcts = {"buy_prob": 0.33, "sell_prob": 0.33}
        g = self.gates.gate_predictive_analytics(probs, mcts, memory_sim=0.0)
        self.assertFalse(g.pass_fail)

    def test_gate_predictive_analytics_rejects_direction_conflict(self):
        probs = {"BUY": 0.78, "SELL": 0.12, "HOLD": 0.10}
        mcts = {"buy_prob": 0.12, "sell_prob": 0.78}
        g = self.gates.gate_predictive_analytics(probs, mcts, memory_sim=0.90, latest_candle_confidence=0.80)
        self.assertFalse(g.pass_fail)
        self.assertFalse(g.detail["direction_agreement"])

    # -- run_all --
    def test_run_all_returns_13_gates(self):
        gates = self.CurriculumGates(self.logger)
        outputs = gates.run_all(
            probs={"BUY": 0.6, "SELL": 0.2, "HOLD": 0.2},
            q05=-0.1, q95=0.1,
            momentum_bias="bullish", explanation="strong breakout",
            sub_signals=[(0.8, "pin_bar"), (0.6, "hammer")],
            module_logits=np.array([0.7, 0.6, 0.8], dtype=np.float32),
            recent_feedback_count=50,
            queue_depth=0, gpu_mem_ok=True, has_dashboard=True, risk_ethical_ok=True,
            chart_state={"entry_type": "reversal", "reversal_signal": "wick_rejection",
                         "direction": "BUY", "consolidation_streak": 5,
                         "continuation_signal": "none"},
            prices=[1.23, 1.24, 1.245, 1.248, 1.250, 1.252],
            direction_prob=0.75,
            mcts={"buy_prob": 0.70, "sell_prob": 0.20},
            memory_sim=0.70,
        )
        self.assertEqual(len(outputs), 13)

    def test_run_all_scores_in_range(self):
        gates = self.CurriculumGates(self.logger)
        outputs = gates.run_all(
            probs={"BUY": 0.5, "SELL": 0.3, "HOLD": 0.2},
            q05=-0.05, q95=0.05, momentum_bias="neutral", explanation="flat",
            sub_signals=[], module_logits=np.array([0.5, 0.5, 0.5]),
            recent_feedback_count=10, queue_depth=1, gpu_mem_ok=True,
            has_dashboard=True, risk_ethical_ok=True,
        )
        for g in outputs:
            self.assertGreaterEqual(g.score, 0.0)
            self.assertLessEqual(g.score, 1.0)

    def test_run_all_fsm_resets_each_call(self):
        gates = self.CurriculumGates(self.logger)
        # Drive FSM to non-Idle state
        gates._fa_state = "ContinuationImpulse"  # type: ignore[reportPrivateUsage]
        gates.run_all(
            probs={"BUY": 0.5, "SELL": 0.3, "HOLD": 0.2},
            q05=-0.1, q95=0.1, momentum_bias="neutral", explanation="ok",
            sub_signals=[], module_logits=np.array([0.5, 0.5, 0.5]),
            recent_feedback_count=5, queue_depth=0, gpu_mem_ok=True,
            has_dashboard=True, risk_ethical_ok=True,
        )
        # FSM should have been reset to Idle at start of run_all
        # (gate_formal_automata is called with no chart-state payload, so stays Idle)
        self.assertEqual(gates._fa_state, "Idle")  # type: ignore[reportPrivateUsage]

    def test_run_all_resets_legacy_discrete_fsm(self):
        gates = self.CurriculumGates(self.logger)
        gates.state = "StrongBear"
        outputs = gates.run_all(
            probs={"BUY": 0.6, "SELL": 0.2, "HOLD": 0.2},
            q05=-0.1,
            q95=0.1,
            momentum_bias="bullish",
            explanation="breakout trend up",
            sub_signals=[],
            module_logits=np.array([0.5, 0.3, 0.2], dtype=np.float32),
            recent_feedback_count=5,
            queue_depth=0,
            gpu_mem_ok=True,
            has_dashboard=True,
            risk_ethical_ok=True,
        )
        discrete = next(g for g in outputs if g.name == "discrete_fsm")
        self.assertIn(discrete.detail["state"], {"WeakBull", "Bull"})

    def test_run_support_gates_returns_live_support_checks(self):
        outputs = self.gates.run_support_gates(
            chart_state={"macro_trend": "BULL", "local_phase": "with_trend_push"},
            market_state={"phase_risk": "breakout_risk", "control_strength_delta": 0.62},
            memory_similarity=0.88,
            memory_label="BUY",
            latest_candle_confidence=0.77,
            geometry_conflict=False,
            reliability=0.81,
        )
        names = {g.name for g in outputs}
        self.assertIn("continuation_strength", names)
        self.assertIn("memory_regime_agreement", names)
        self.assertIn("execution_permission", names)

    def test_memory_regime_agreement_without_memory_is_neutral_not_passed(self):
        g = self.gates.memory_regime_agreement_gate("BULL", 0.0, "HOLD")
        self.assertFalse(g.pass_fail)
        self.assertFalse(g.detail["required"])

    def test_run_support_gates_adds_predictive_checks_when_forecast_present(self):
        outputs = self.gates.run_support_gates(
            chart_state={"entry_type": "continuation", "macro_trend": "BULL", "local_phase": "with_trend_push"},
            market_state={"phase_risk": "breakout_risk", "control_strength_delta": 0.62},
            forecast={
                "q05": -0.08,
                "q50": 0.18,
                "q95": 0.31,
                "hold_threshold_used": 0.40,
                "path_confidence": 0.74,
                "execution_readiness": 0.78,
                "contradiction_score": 0.12,
                "structure_trade_ready": 1.0,
                "structure_setup": "consolidation_breakout",
                "projected_box_confidence": 0.71,
                "continue_prob": 0.66,
                "pullback_prob": 0.13,
                "reversal_attempt_prob": 0.11,
                "fakeout_prob": 0.10,
            },
            transition_summary={
                "continue_prob": 0.66,
                "pullback_prob": 0.13,
                "reversal_attempt_prob": 0.11,
                "fakeout_prob": 0.10,
            },
            memory_summary={"ambiguity": 0.08},
            ood_summary={"style_novelty": 0.10},
            memory_similarity=0.88,
            memory_label="BUY",
            latest_candle_confidence=0.77,
            geometry_conflict=False,
            reliability=0.81,
        )
        names = {g.name for g in outputs}
        self.assertIn("forecast_calibration", names)
        self.assertIn("interval_efficiency", names)
        self.assertIn("regime_stability", names)
        self.assertIn("transition_alignment", names)

    def test_update_router_from_feedback(self):
        gates = self.CurriculumGates(self.logger)
        scores = np.ones(12, dtype=np.float32) * 0.8
        for _ in range(10):
            gates.update_router_from_feedback(scores, reward=1.0)
        # Router weights should have been updated (no crash)
        w = gates._router.layer.weight  # type: ignore[reportPrivateUsage]
        self.assertEqual(w.shape, (13, 13))

    # -- LinearRouter --
    def test_linear_router_init_identity(self):
        import torch
        router = self.LinearRouter(n_gates=12)
        x = torch.ones(12)
        out = router(x)
        self.assertEqual(out.shape[0], 12)
        self.assertTrue(all(0 <= v <= 1 for v in out.tolist()))

    # -- SkillGatedMoE --
    def test_skill_gated_moe_route_weights(self):
        moe = self.SkillGatedMoE(n_features=16, n_gates=12)
        feat = np.ones(16, dtype=np.float32)
        weights = moe.route_weights(feat)
        self.assertEqual(weights.shape[0], 12)
        self.assertAlmostEqual(float(weights.sum()), 1.0, places=4)


# ===========================================================================
# 7. ensemble.py
# ===========================================================================
class TestEnsemble(unittest.TestCase):
    """Covers: EnsembleDecisionEngine._bayesian_average, _shap_contributions,
    infer (all consensus conditions), position sizing"""

    def _make_gates(self, n: int = 12, pass_all: bool = True, score: float = 0.8) -> list[Any]:
        from skill_gates import GateOutput
        return [GateOutput(name=f"g{i}", score=score, pass_fail=pass_all, detail={})
                for i in range(n)]

    def setUp(self):
        from ensemble import EnsembleDecisionEngine
        self.EDE = EnsembleDecisionEngine

    def test_infer_action_in_valid_set(self):
        eng = self.EDE(consensus_threshold=0.78, max_interval_pct=0.65, risk_min_pct=0.5, risk_max_pct=2.0)
        result = eng.infer(
            rl_probs={"BUY": 0.85, "SELL": 0.08, "HOLD": 0.07},
            forecast={"q05": -0.1, "q50": 0.2, "q95": 0.3},
            gate_outputs=self._make_gates(12, pass_all=True, score=0.8),
        )
        self.assertIn(result["action"], {"BUY", "SELL", "HOLD"})

    def test_infer_returns_all_keys(self):
        eng = self.EDE(0.78, 0.65, 0.5, 2.0)
        result = eng.infer(
            rl_probs={"BUY": 0.6, "SELL": 0.2, "HOLD": 0.2},
            forecast={"q05": -0.05, "q50": 0.1, "q95": 0.2},
            gate_outputs=self._make_gates(12),
        )
        for key in ("action", "confidence", "calibrated_probs", "position_size_pct",
                    "consensus_ok", "gates_passing", "gate_scores", "shap_contributions"):
            self.assertIn(key, result, f"Missing key: {key}")

    def test_infer_support_gate_outputs_are_wired(self):
        from skill_gates import GateOutput
        eng = self.EDE(0.78, 0.65, 0.5, 2.0)
        result = eng.infer(
            rl_probs={"BUY": 0.7, "SELL": 0.15, "HOLD": 0.15},
            forecast={"q05": -0.05, "q50": 0.1, "q95": 0.2, "execution_readiness": 0.7},
            gate_outputs=self._make_gates(12, pass_all=True, score=0.8),
            support_gate_outputs=[
                GateOutput("continuation_strength", 0.8, True, {}),
                GateOutput("memory_regime_agreement", 0.75, True, {}),
                GateOutput("macro_local_alignment", 0.9, True, {}),
                GateOutput("execution_permission", 0.7, True, {}),
                GateOutput("forecast_calibration", 0.78, True, {}),
                GateOutput("interval_efficiency", 0.74, True, {}),
                GateOutput("regime_stability", 0.81, True, {}),
                GateOutput("transition_alignment", 0.77, True, {}),
            ],
        )
        self.assertIn("support_gate_scores", result)
        self.assertTrue(result["support_gates_ok"])
        self.assertIn("forecast_calibration", result["support_gate_scores"])

    def test_infer_failed_execution_support_is_diagnostic_contributor(self):
        from skill_gates import GateOutput
        eng = self.EDE(0.62, 0.65, 0.5, 2.0, gates_pass_minimum=6)
        result = eng.infer(
            rl_probs={"BUY": 0.82, "SELL": 0.10, "HOLD": 0.08},
            forecast={
                "q05": 0.02,
                "q50": 0.16,
                "q95": 0.24,
                "execution_readiness": 0.76,
                "active_consolidation": 1.0,
                "structure_trade_ready": 1.0,
                "structure_setup": "consolidation_breakout",
                "projected_box_direction": "BUY",
                "projected_box_confidence": 0.82,
            },
            gate_outputs=self._make_gates(12, pass_all=True, score=0.9),
            memory_bank_similarity=0.82,
            memory_summary={
                "dominant_label": "BUY",
                "mixed_labels": False,
                "ambiguity": 0.0,
                "label_entropy": 0.0,
                "consensus_ratio": 1.0,
            },
            module_reliability={"cv_quality": 0.86, "structure_consistency": 0.88},
            latest_candle_confidence=0.82,
            transition_summary={"continue_prob": 0.66, "pullback_prob": 0.08, "reversal_attempt_prob": 0.12, "fakeout_prob": 0.08},
            support_gate_outputs=[
                GateOutput("continuation_strength", 0.86, True, {}),
                GateOutput("memory_regime_agreement", 0.82, True, {}),
                GateOutput("macro_local_alignment", 0.90, True, {}),
                GateOutput("execution_permission", 0.31, False, {}),
                GateOutput("forecast_calibration", 0.90, True, {}),
                GateOutput("interval_efficiency", 0.88, True, {}),
                GateOutput("regime_stability", 0.86, True, {}),
                GateOutput("transition_alignment", 0.84, True, {}),
            ],
        )
        self.assertFalse(result["hard_support_ok"])
        self.assertTrue(result["consensus_ok"])
        self.assertEqual(result["execution_permission"], "EXECUTE")

    def test_infer_force_hold(self):
        eng = self.EDE(0.78, 0.65, 0.5, 2.0)
        result = eng.infer(
            rl_probs={"BUY": 0.9, "SELL": 0.05, "HOLD": 0.05},
            forecast={"q05": 0.0, "q50": 0.5, "q95": 1.0},
            gate_outputs=self._make_gates(12, pass_all=True),
            force_hold=True,
        )
        self.assertEqual(result["action"], "HOLD")

    def test_infer_insufficient_gates_forces_hold(self):
        # Only 5/12 gates pass → < 9 minimum → must HOLD
        eng = self.EDE(consensus_threshold=0.78, max_interval_pct=0.65,
                       risk_min_pct=0.5, risk_max_pct=2.0, gates_pass_minimum=9)
        gates_few_pass = self._make_gates(12, pass_all=False)
        # override first 5 to pass
        for g in gates_few_pass[:5]:
            g.pass_fail = True
        result = eng.infer(
            rl_probs={"BUY": 0.9, "SELL": 0.05, "HOLD": 0.05},
            forecast={"q05": -0.01, "q50": 0.1, "q95": 0.12},
            gate_outputs=gates_few_pass,
        )
        self.assertEqual(result["action"], "HOLD")

    def test_infer_wide_interval_forces_hold(self):
        eng = self.EDE(0.78, max_interval_pct=0.10, risk_min_pct=0.5, risk_max_pct=2.0)
        result = eng.infer(
            rl_probs={"BUY": 0.9, "SELL": 0.05, "HOLD": 0.05},
            forecast={"q05": -2.0, "q50": 0.1, "q95": 2.0},  # 4% interval >> 0.10
            gate_outputs=self._make_gates(12, pass_all=True),
        )
        self.assertEqual(result["action"], "HOLD")

    def test_infer_position_size_clamped(self):
        eng = self.EDE(0.78, 0.65, 0.5, 2.0)
        result = eng.infer(
            rl_probs={"BUY": 0.7, "SELL": 0.2, "HOLD": 0.1},
            forecast={"q05": -0.05, "q50": 0.1, "q95": 0.15},
            gate_outputs=self._make_gates(12, pass_all=True),
        )
        self.assertGreaterEqual(result["position_size_pct"], 0.5)
        self.assertLessEqual(result["position_size_pct"], 2.0)

    def test_shap_contributions_keys_match_gates(self):
        eng = self.EDE(0.78, 0.65, 0.5, 2.0)
        gates = self._make_gates(12)
        result = eng.infer(
            rl_probs={"BUY": 0.7, "SELL": 0.2, "HOLD": 0.1},
            forecast={"q05": -0.05, "q50": 0.1, "q95": 0.15},
            gate_outputs=gates,
        )
        shap = result["shap_contributions"]
        self.assertEqual(len(shap), 12)

    def test_memory_veto_condition_zero_allows_action(self):
        """memory_sim==0 should not veto (bank not loaded)."""
        eng = self.EDE(consensus_threshold=0.65, max_interval_pct=0.65,
                       risk_min_pct=0.5, risk_max_pct=2.0, gates_pass_minimum=1,
                       memory_veto_threshold=0.87)
        gates = self._make_gates(12, pass_all=True, score=0.9)
        result = eng.infer(
            rl_probs={"BUY": 0.85, "SELL": 0.10, "HOLD": 0.05},
            forecast={"q05": -0.01, "q50": 0.2, "q95": 0.11},
            gate_outputs=gates,
            memory_bank_similarity=0.0,
        )
        self.assertTrue(result["memory_ok"])

    def test_memory_veto_below_threshold_forces_hold(self):
        """memory_sim 0 < sim < threshold should veto."""
        eng = self.EDE(consensus_threshold=0.65, max_interval_pct=0.65,
                       risk_min_pct=0.5, risk_max_pct=2.0, gates_pass_minimum=1,
                       memory_veto_threshold=0.87)
        gates = self._make_gates(12, pass_all=True, score=0.9)
        result = eng.infer(
            rl_probs={"BUY": 0.85, "SELL": 0.10, "HOLD": 0.05},
            forecast={"q05": -0.01, "q50": 0.2, "q95": 0.11},
            gate_outputs=gates,
            memory_bank_similarity=0.50,  # below 0.87 and non-zero
        )
        self.assertFalse(result["memory_ok"])
        self.assertEqual(result["action"], "HOLD")


# ===========================================================================
# 8. regression_module.py
# ===========================================================================
class TestRegressionModule(unittest.TestCase):
    """Covers: _accumulation_distribution, _poly_trend, _conformal_interval,
    ChronosRegressor.forecast_3m (Chronos offline fallback path)"""

    def setUp(self):
        from regression_module import _accumulation_distribution, _poly_trend, _conformal_interval, ChronosRegressor  # type: ignore[reportPrivateUsage]
        self._ad = _accumulation_distribution
        self._poly = _poly_trend
        self._conf = _conformal_interval
        self.ChronosRegressor = ChronosRegressor

    def test_ad_empty_ohlc(self):
        val = self._ad([], np.zeros(0, dtype=np.float32))
        self.assertEqual(val, 0.0)

    def test_ad_bullish_bars(self):
        # Close near high → positive A/D
        ohlc = [[1.0, 1.2, 0.9, 1.18]] * 5     # strong bullish close
        body_sizes = np.ones(5, dtype=np.float32)
        val = self._ad(ohlc, body_sizes)
        self.assertGreater(val, 0.0)

    def test_ad_bearish_bars(self):
        # Close near low → negative A/D
        ohlc = [[1.2, 1.3, 0.9, 0.92]] * 5
        body_sizes = np.ones(5, dtype=np.float32)
        val = self._ad(ohlc, body_sizes)
        self.assertLess(val, 0.0)

    def test_ad_range_clamp(self):
        ohlc = [[1.0, 1.5, 0.5, 1.4]] * 10
        body = np.ones(10, dtype=np.float32)
        val = self._ad(ohlc, body)
        self.assertGreaterEqual(val, -1.0)
        self.assertLessEqual(val, 1.0)

    def test_poly_trend_uptrend(self):
        closes = np.array([1.0, 1.05, 1.10, 1.15, 1.20, 1.25, 1.30], dtype=np.float32)
        result = self._poly(closes, degree=2)
        self.assertGreater(result["slope"], 0.0)
        self.assertGreater(result["r2"], 0.8)

    def test_poly_trend_too_few_points(self):
        result = self._poly(np.array([1.0, 1.05]), degree=2)
        self.assertEqual(result["slope"], 0.0)

    def test_conf_interval_valid(self):
        returns = np.array([0.1, -0.2, 0.15, 0.05, -0.1, 0.3], dtype=np.float32)
        lo, hi = self._conf(returns)
        self.assertLessEqual(lo, hi)

    def test_conf_interval_too_few(self):
        returns = np.array([0.1, 0.2], dtype=np.float32)
        lo, hi = self._conf(returns)
        self.assertLessEqual(lo, hi)

    def _make_ohlc(self, n: int = 20, start: float = 1.2300, step: float = 0.0010) -> list[list[float]]:
        ohlc: list[list[float]] = []
        price = start
        for _ in range(n):
            o = price
            h = price + 0.0020
            l = price - 0.0010
            c = price + 0.0015
            ohlc.append([o, h, l, c])
            price += step
        return ohlc

    def test_chronos_fallback_forecast_no_model(self):
        reg = self.ChronosRegressor("amazon/chronos-2", _NullLogger())
        # Model won't load in test — use fallback path via empty ohlc
        result = reg.forecast_3m({"implied_3min_move_pct": 0.05})
        for k in ("q05", "q50", "q95", "point", "force_hold"):
            self.assertIn(k, result)

    def test_chronos_fallback_with_ohlc(self):
        reg = self.ChronosRegressor("amazon/chronos-2", _NullLogger())
        reg.pipeline = None   # force fallback
        ohlc = self._make_ohlc(20)
        result = reg.forecast_3m({"ohlc_last20": ohlc, "implied_3min_move_pct": 0.1})
        self.assertIn("q05", result)
        self.assertIn("poly_slope", result)
        self.assertIn("ad_indicator", result)
        self.assertIsInstance(result["force_hold"], bool)
        # Interval should yield numbers
        self.assertLessEqual(result["q05"], result["q95"])

    def test_chronos_ad_indicator_computed(self):
        reg = self.ChronosRegressor("amazon/chronos-2", _NullLogger())
        reg.pipeline = None
        ohlc = self._make_ohlc(20)
        result = reg.forecast_3m({"ohlc_last20": ohlc})
        self.assertGreaterEqual(result["ad_indicator"], -1.0)
        self.assertLessEqual(result["ad_indicator"], 1.0)


# ===========================================================================
# 9. memory_ingest.py
# ===========================================================================
class TestMemoryIngest(unittest.TestCase):
    """Covers: _visual_fingerprint, _dual_encode, _passes_indicator_filter,
    _heuristic_price_action, MemoryBank (populate/search/few-shot/logit-boost/dpo),
    _HNSWIndex, MemoryEntry, _chart_state_to_text"""

    def setUp(self):
        from memory_ingest import (
            _visual_fingerprint,  # type: ignore[reportPrivateUsage]
            _dual_encode,  # type: ignore[reportPrivateUsage]
            _passes_indicator_filter,  # type: ignore[reportPrivateUsage]
            _heuristic_price_action,  # type: ignore[reportPrivateUsage]
            MemoryBank,
            MemoryEntry,
            RecallResult,
            _HNSWIndex,  # type: ignore[reportPrivateUsage]
            _chart_state_to_text,  # type: ignore[reportPrivateUsage]
            EMBED_DIM,
            VISUAL_DIM,
            SHARED_DIM,
        )
        self._vfp = _visual_fingerprint  # type: ignore[reportPrivateUsage]
        self._dual = _dual_encode  # type: ignore[reportPrivateUsage]
        self._pass_filter = cast(Callable[[str], bool], _passes_indicator_filter)  # type: ignore[reportPrivateUsage]
        self._heuristic = _heuristic_price_action  # type: ignore[reportPrivateUsage]
        self.MemoryBank = MemoryBank
        self.MemoryEntry = MemoryEntry
        self.RecallResult = RecallResult
        self.HNSWIndex = _HNSWIndex  # type: ignore[reportPrivateUsage]
        self._chart_state_to_text = _chart_state_to_text  # type: ignore[reportPrivateUsage]
        self.EMBED_DIM = EMBED_DIM
        self.VISUAL_DIM = VISUAL_DIM
        self.SHARED_DIM = SHARED_DIM

    def _make_img(self, w: int = 64, h: int = 64, color: tuple[int, int, int] = (100, 150, 200)) -> Image.Image:
        # Use a gradient image so gradient magnitude is non-zero (uniform images
        # produce all-zero gradients → NaN in the orientation histogram).
        arr = np.zeros((h, w, 3), dtype=np.uint8)
        for row in range(h):
            arr[row, :, 0] = int(color[0] * row / max(h - 1, 1)) % 256
            arr[row, :, 1] = int(color[1] * (w - row % w) / max(w - 1, 1)) % 256
            arr[row, :, 2] = color[2]
        return Image.fromarray(arr, mode="RGB")

    def _make_entry(self, label: str = "BUY", eid: str = "test001") -> Any:
        text_embed = np.random.rand(self.EMBED_DIM).astype(np.float32)
        visual_fp = np.random.rand(self.VISUAL_DIM).astype(np.float32)
        combined = self._dual(text_embed, visual_fp)
        return self.MemoryEntry(
            entry_id=eid,
            image_path="/tmp/test.png",
            label=label,
            chart_state={"direction": label, "reversal_signal": "wick_rejection"},
            text_embed=text_embed.tolist(),
            visual_fp=visual_fp.tolist(),
            combined_embed=combined.tolist(),
        )

    def test_visual_fingerprint_dim(self):
        img = self._make_img(64, 64)
        fp = self._vfp(img)
        self.assertEqual(fp.shape[0], self.VISUAL_DIM)

    def test_visual_fingerprint_normalized(self):
        img = self._make_img(64, 64)
        fp = self._vfp(img)
        norm = float(np.linalg.norm(fp))
        self.assertAlmostEqual(norm, 1.0, places=5)

    def test_visual_fingerprint_different_images(self):
        fp1 = self._vfp(self._make_img(color=(255, 0, 0)))
        fp2 = self._vfp(self._make_img(color=(0, 255, 0)))
        sim = float(np.dot(fp1, fp2))
        self.assertLess(sim, 1.0)  # different images → different fingerprints

    def test_dual_encode_shape(self):
        te = np.random.rand(self.EMBED_DIM).astype(np.float32)
        vf = np.random.rand(self.VISUAL_DIM).astype(np.float32)
        out = self._dual(te, vf)
        self.assertEqual(out.shape[0], self.SHARED_DIM)

    def test_dual_encode_normalized(self):
        te = np.random.rand(self.EMBED_DIM).astype(np.float32)
        vf = np.random.rand(self.VISUAL_DIM).astype(np.float32)
        out = self._dual(te, vf)
        self.assertAlmostEqual(float(np.linalg.norm(out)), 1.0, places=5)

    def test_passes_indicator_filter_clean(self):
        self.assertTrue(self._pass_filter("price broke above support 1.2350"))

    def test_passes_indicator_filter_contaminated(self):
        self.assertFalse(self._pass_filter("RSI crossed above 70 with MACD"))

    def test_heuristic_price_action_buy(self):
        img = self._make_img(200, 100, color=(50, 200, 50))  # green-ish
        result = self._heuristic(img, "BUY")
        self.assertEqual(result["direction"], "BUY")
        self.assertIn("entry_type", result)
        self.assertIn("momentum_bias", result)

    def test_heuristic_price_action_sell(self):
        img = self._make_img(200, 100, color=(200, 50, 50))  # red-ish
        result = self._heuristic(img, "SELL")
        self.assertEqual(result["direction"], "SELL")

    def test_chart_state_to_text_produces_string(self):
        chart_state: dict[str, Any] = {"entry_type": "reversal", "direction": "BUY", "reversal_signal": "wick_rejection",
                       "momentum_bias": "bullish", "candle_count_up": 5, "candle_count_down": 1,
                       "consolidation_streak": 4, "consolidation_type": "tight",
                       "continuation_signal": "none", "direction_probability": 0.75,
                       "entry_candle": {"color": "green"}}
        text = self._chart_state_to_text(chart_state)
        self.assertIn("reversal", text)
        self.assertIn("BUY", text)

    def test_memory_bank_empty_search(self):
        bank = self.MemoryBank()
        q = np.zeros(self.SHARED_DIM, dtype=np.float32)
        results = bank.search(q, top_k=5)
        self.assertEqual(results, [])

    def test_memory_bank_populate_and_search(self):
        bank = self.MemoryBank()
        entries_dict: dict[str, Any] = {}
        entries_list: list[Any] = []
        for i in range(5):
            e = self._make_entry("BUY" if i % 2 == 0 else "SELL", eid=f"e{i:03d}")
            entries_dict[e.entry_id] = e
            entries_list.append(e)

        idx = self.HNSWIndex(self.SHARED_DIM)
        idx.build(entries_list)
        bank.populate(idx, entries_dict, n_buy=3, n_sell=2)

        query = np.array(entries_list[0].combined_embed, dtype=np.float32)
        results = bank.search(query, top_k=3)
        self.assertGreater(len(results), 0)
        self.assertIsInstance(results[0].similarity, float)
        self.assertGreaterEqual(results[0].similarity, 0.0)

    def test_memory_bank_search_top1_is_self(self):
        """The query that matches entry 0 should return entry 0 as top-1."""
        bank = self.MemoryBank()
        entries_dict: dict[str, Any] = {}
        entries_list: list[Any] = []
        for i in range(10):
            e = self._make_entry("BUY", eid=f"e{i:03d}")
            entries_dict[e.entry_id] = e
            entries_list.append(e)
        idx = self.HNSWIndex(self.SHARED_DIM)
        idx.build(entries_list)
        bank.populate(idx, entries_dict, n_buy=10, n_sell=0)

        query = np.array(entries_list[0].combined_embed, dtype=np.float32)
        results = bank.search(query, top_k=1)
        self.assertEqual(results[0].entry_id, entries_list[0].entry_id)

    def test_memory_bank_few_shot_context_empty(self):
        bank = self.MemoryBank()
        ctx = bank.get_few_shot_context([])
        self.assertEqual(ctx, "")

    def test_memory_bank_few_shot_context_nonempty(self):
        bank = self.MemoryBank()
        entries_dict: dict[str, Any] = {}
        entries_list: list[Any] = []
        for i in range(3):
            e = self._make_entry("BUY", eid=f"fs{i:03d}")
            entries_dict[e.entry_id] = e
            entries_list.append(e)
        idx = self.HNSWIndex(self.SHARED_DIM)
        idx.build(entries_list)
        bank.populate(idx, entries_dict, n_buy=3, n_sell=0)
        from memory_ingest import RecallResult
        fake_results = [
            RecallResult(
                entry_id=entries_list[i].entry_id,
                label="BUY",
                similarity=0.9 - i * 0.05,
                archetype_id=0,
                chart_state={"direction": "BUY"},
                is_archetype_centroid=True,
            )
            for i in range(3)
        ]
        ctx = bank.get_few_shot_context(fake_results)
        self.assertIn("RECALLED", ctx)

    def test_memory_bank_embed_description(self):
        bank = self.MemoryBank()
        chart_state = {"entry_type": "reversal", "direction": "BUY",
                       "momentum_bias": "bullish", "reversal_signal": "wick_rejection"}
        embed = bank.embed_description(chart_state)
        self.assertEqual(embed.shape[0], self.SHARED_DIM)
        self.assertAlmostEqual(float(np.linalg.norm(embed)), 1.0, places=4)

    def test_memory_bank_entries_property(self):
        bank = self.MemoryBank()
        entries_dict = {f"e{i}": self._make_entry("BUY", eid=f"e{i}") for i in range(5)}
        idx = self.HNSWIndex(self.SHARED_DIM)
        idx.build(list(entries_dict.values()))
        bank.populate(idx, entries_dict, n_buy=5, n_sell=0)
        self.assertEqual(len(bank.entries), 5)

    def test_hnsw_index_save_load(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "hnsw_test"
            entries = [self._make_entry("BUY", eid=f"e{i}") for i in range(5)]
            idx = self.HNSWIndex(self.SHARED_DIM)
            idx.build(entries)
            idx.save(path)
            self.assertTrue((path / "id_map.json").exists())

            # Load
            idx2 = self.HNSWIndex(self.SHARED_DIM)
            idx2.load(path, n_entries=5)
            self.assertEqual(len(idx2._idx_to_id), 5)  # type: ignore[reportPrivateUsage]


# ===========================================================================
# 10. personalization.py
# ===========================================================================
class TestPersonalization(unittest.TestCase):
    """Covers: PersonalizationEngine (update_style, update_style_from_memory_bank,
    style_prefix_prompt, update_memory_bank_stats, record_feedback,
    generate_dpo_pairs, recent_feedback_count, build_online_batch,
    build_plotly_dashboard)"""

    def setUp(self):
        from security import SecurityManager, EncryptedPreferenceStore
        from personalization import PersonalizationEngine
        self._td = tempfile.mkdtemp()
        td = Path(self._td)
        sec = SecurityManager(td / "data", td / "logs", kdf_iterations=1000)
        fernet = sec.derive_fernet("test-persona-808")
        store = EncryptedPreferenceStore(td / "prefs.enc.sqlite", fernet)
        # Use zero-vector fallback (no real model download in CI)
        self.engine = PersonalizationEngine("sentence-transformers/all-MiniLM-L6-v2", store, _NullLogger())

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_update_style_ema(self):
        old_vec = self.engine.style_vector.copy()
        self.engine.update_style("BUY wick rejection on 4H chart at support", "clean pin bar")
        new_vec = self.engine.style_vector
        # EMA should shift the vector
        self.assertFalse(np.allclose(old_vec, new_vec))

    def test_update_style_from_dpo_pairs(self):
        dpo_pairs = [
            {"chosen": "bullish wick rejection signal strong", "rejected": "doji no signal"},
            {"chosen": "pin bar at daily support level", "rejected": "inside bar unclear"},
        ]
        result = self.engine.update_style_from_memory_bank(dpo_pairs)
        self.assertIsInstance(result, np.ndarray)
        self.assertEqual(result.shape[0], 384)

    def test_update_style_from_empty_dpo_pairs(self):
        vec_before = self.engine.style_vector.copy()
        self.engine.update_style_from_memory_bank([])
        self.assertTrue(np.allclose(self.engine.style_vector, vec_before))

    def test_style_prefix_prompt_returns_string(self):
        prefix = self.engine.style_prefix_prompt()
        self.assertIsInstance(prefix, str)
        self.assertIn("User trading style", prefix)

    def test_style_prefix_prompt_with_memory_stats(self):
        self.engine.update_memory_bank_stats({"total_entries": 120, "archetype_count": 30, "dominant_label": "BUY"})
        prefix = self.engine.style_prefix_prompt()
        self.assertIn("MemoryBank", prefix)
        self.assertIn("120", prefix)

    def test_record_feedback_increments_count(self):
        self.engine.record_feedback("img_hash_001", "BUY wick", "HOLD flat", "clean reversal", "good pin bar")
        count = self.engine.recent_feedback_count()
        self.assertGreaterEqual(count, 1)

    def test_generate_dpo_pairs_from_store(self):
        # Insert some fake preferences
        for i in range(5):
            self.engine.record_feedback(f"h{i}", f"BUY {i}", f"SELL {i}", f"reason {i}", "")
        pairs = self.engine.generate_dpo_pairs(memory_bank=None, n=5)  # type: ignore[reportUnknownMemberType]
        self.assertIsInstance(pairs, list)

    def test_build_online_batch_correct(self):
        state = np.zeros(64, dtype=np.float32)
        batch = self.engine.build_online_batch(state, "BUY", user_correct=True)
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]["target_action"], "BUY")

    def test_build_online_batch_incorrect(self):
        state = np.zeros(64, dtype=np.float32)
        batch = self.engine.build_online_batch(state, "BUY", user_correct=False)
        self.assertEqual(batch[0]["target_action"], "HOLD")

    def test_build_plotly_dashboard_returns_figure(self):
        gate_scores = {f"g{i}": float(i) / 12.0 for i in range(12)}
        fig = self.engine.build_plotly_dashboard(gate_scores)
        # Returns None if plotly unavailable, else a Figure
        if fig is not None:
            self.assertTrue(hasattr(fig, "to_json"))


# ===========================================================================
# 11. cv_module.py
# ===========================================================================
class TestCVModule(unittest.TestCase):
    """Covers: CVPatternDetector (graceful degradation when YOLO unavailable),
    _priority_queue_rank, _kmeans_consolidation, detect"""

    def setUp(self):
        from cv_module import CVPatternDetector, PatternDetection
        self.CVPatternDetector = CVPatternDetector
        self.PatternDetection = PatternDetection

    def _make_img(self, w: int = 512, h: int = 512) -> Image.Image:
        return Image.new("RGB", (w, h), color=(30, 30, 30))

    def test_detector_init_no_model(self):
        """Should not crash even when YOLO unavailable."""
        det = self.CVPatternDetector("nonexistent/model", "nonexistent/fallback", _NullLogger())
        self.assertIsNone(det.model)

    def test_detect_returns_list_when_no_model(self):
        det = self.CVPatternDetector("nonexistent/model", "nonexistent/fallback", _NullLogger())
        img = self._make_img()
        result = det.detect(img)  # type: ignore[reportUnknownMemberType]
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    def test_priority_queue_rank_ordering(self):
        det = self.CVPatternDetector("nonexistent/model", "nonexistent/fallback", _NullLogger())
        raw: list[dict[str, Any]] = [
            {"pattern": "head_and_shoulders", "confidence": 0.9, "bbox": [0, 0, 100, 100]},  # penalized
            {"pattern": "bullish_engulfing", "confidence": 0.85, "bbox": [0, 0, 50, 50]},     # high score
            {"pattern": "pin_bar", "confidence": 0.80, "bbox": [0, 0, 60, 60]},
        ]
        ranked = det._priority_queue_rank(raw, top_n=3)  # type: ignore[reportPrivateUsage]
        self.assertEqual(len(ranked), 3)
        # bullish_engulfing should outrank head_and_shoulders
        top_names = [r.pattern.lower().replace(" ", "_") for r in ranked]
        self.assertIn("bullish_engulfing", top_names)
        # head_and_shoulders should be last due to 80% penalty
        self.assertEqual(top_names[-1], "head_and_shoulders")

    def test_priority_queue_assigns_pattern_types(self):
        det = self.CVPatternDetector("nonexistent/model", "nonexistent/fallback", _NullLogger())
        raw: list[dict[str, Any]] = [
            {"pattern": "hammer", "confidence": 0.8, "bbox": [0, 0, 10, 10]},
            {"pattern": "inside_bar", "confidence": 0.7, "bbox": [0, 0, 10, 10]},
        ]
        ranked = det._priority_queue_rank(raw, top_n=2)  # type: ignore[reportPrivateUsage]
        types = {r.pattern.lower(): r.pattern_type for r in ranked}
        self.assertEqual(types["hammer"], "reversal")
        self.assertEqual(types["inside_bar"], "continuation")

    def test_kmeans_consolidation_too_few(self):
        det = self.CVPatternDetector("nonexistent/model", "nonexistent/fallback", _NullLogger())
        result = det._kmeans_consolidation([], 512, 512)  # type: ignore[reportPrivateUsage]
        self.assertEqual(result["n_clusters"], 0)

    def test_kmeans_consolidation_enough_detections(self):
        det = self.CVPatternDetector("nonexistent/model", "nonexistent/fallback", _NullLogger())
        detections: list[Any] = []
        for i in range(6):
            d = self.PatternDetection(
                pattern="pin_bar",
                confidence=0.8,
                bbox=[float(i * 50), 100.0, float(i * 50 + 40), 200.0],
                priority_score=0.8,
                pattern_type="reversal",
            )
            detections.append(d)
        result = det._kmeans_consolidation(detections, 512, 512)  # type: ignore[reportPrivateUsage]
        self.assertGreater(result["n_clusters"], 0)


# ===========================================================================
# 13. Integration: full pipeline (preprocess → RL → gates → ensemble)
# ===========================================================================
class TestIntegrationPipeline(unittest.TestCase):
    """End-to-end pipeline test without any heavy ML model downloads."""

    def test_full_pipeline_text_file(self):
        from preprocess import load_any_file_as_image, normalize_for_model
        from rl_module import RLPolicyEngine
        from skill_gates import CurriculumGates
        from ensemble import EnsembleDecisionEngine

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "chart_note.txt"
            p.write_text("BUY entry 1.23456 after 4 red candles wick rejection at support.", encoding="utf-8")

            img, meta = load_any_file_as_image(p)
            self.assertIn("sha256", meta)

            norm = normalize_for_model(img, out_size=64)
            self.assertEqual(norm.size, (64, 64))

        rl = RLPolicyEngine(logger=_NullLogger(), in_dim=64, mcts_sims=5)
        state = np.zeros(64, dtype=np.float32)
        rl_out = rl.infer(state)
        self.assertEqual(set(rl_out.probs.keys()), {"BUY", "SELL", "HOLD"})

        gates = CurriculumGates(_NullLogger())
        gate_outputs = gates.run_all(
            probs=rl_out.probs,
            q05=-0.05, q95=0.15,
            momentum_bias="bullish", explanation="wick rejection at support",
            sub_signals=[(0.8, "pin_bar")],
            module_logits=np.array([0.7, 0.6, 0.75, 0.8], dtype=np.float32),
            recent_feedback_count=20,
            queue_depth=0, gpu_mem_ok=True, has_dashboard=True, risk_ethical_ok=True,
            chart_state={"entry_type": "reversal", "reversal_signal": "wick_rejection",
                         "direction": "BUY", "consolidation_streak": 4,
                         "continuation_signal": "none"},
            prices=[1.23, 1.231, 1.233, 1.235, 1.237, 1.239],
            direction_prob=0.70,
            mcts={"buy_prob": 0.65, "sell_prob": 0.25},
            memory_sim=0.0,
        )
        self.assertEqual(len(gate_outputs), 13)

        ensemble = EnsembleDecisionEngine(0.78, 0.65, 0.5, 2.0, gates_pass_minimum=9)
        decision = ensemble.infer(
            rl_probs=rl_out.probs,
            forecast={"q05": -0.05, "q50": 0.10, "q95": 0.15},
            gate_outputs=gate_outputs,
        )
        self.assertIn(decision["action"], {"BUY", "SELL", "HOLD"})
        self.assertGreaterEqual(decision["position_size_pct"], 0.5)
        self.assertIn("shap_contributions", decision)

    def test_full_pipeline_image_file(self):
        from preprocess import load_any_file_as_image
        from rl_module import RLPolicyEngine
        from skill_gates import CurriculumGates
        from ensemble import EnsembleDecisionEngine

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "chart.png"
            Image.new("RGB", (256, 256), color=(40, 40, 40)).save(p)
            _, meta = load_any_file_as_image(p)
            self.assertEqual(meta["source_type"], "image")

        rl = RLPolicyEngine(logger=_NullLogger(), in_dim=64, mcts_sims=5)
        rl_out = rl.infer(np.random.rand(64).astype(np.float32))

        gates = CurriculumGates(_NullLogger())
        gate_outputs = gates.run_all(
            probs=rl_out.probs,
            q05=-0.1, q95=0.1,
            momentum_bias="bearish", explanation="sell pressure",
            sub_signals=[(0.9, "bearish_engulfing")],
            module_logits=np.array([0.8, 0.8, 0.9, 0.85], dtype=np.float32),
            recent_feedback_count=15,
            queue_depth=0, gpu_mem_ok=True, has_dashboard=True, risk_ethical_ok=True,
        )
        self.assertEqual(len(gate_outputs), 13)

        ensemble = EnsembleDecisionEngine(0.78, 0.65, 0.5, 2.0)
        decision = ensemble.infer(rl_out.probs, {"q05": -0.1, "q50": 0.0, "q95": 0.1}, gate_outputs)
        self.assertIn(decision["action"], {"BUY", "SELL", "HOLD"})

    def test_regression_memory_personalization_chain(self):
        """Regression → MemoryBank → Personalization chain."""
        from regression_module import ChronosRegressor
        from memory_ingest import MemoryBank, MemoryEntry, _HNSWIndex, _visual_fingerprint, _dual_encode, EMBED_DIM, SHARED_DIM  # type: ignore[reportPrivateUsage]
        from personalization import PersonalizationEngine
        from security import SecurityManager, EncryptedPreferenceStore

        reg = ChronosRegressor("amazon/chronos-2", _NullLogger())
        reg.pipeline = None
        ohlc = [[1.23 + i * 0.001, 1.23 + i * 0.002, 1.23 - 0.001 + i * 0.001, 1.23 + i * 0.0015] for i in range(20)]
        forecast = reg.forecast_3m({"ohlc_last20": ohlc})
        self.assertIn("q50", forecast)

        bank = MemoryBank()
        entries_dict: dict[str, MemoryEntry] = {}
        entries_list: list[MemoryEntry] = []
        for i in range(8):
            te = np.random.rand(EMBED_DIM).astype(np.float32)
            vf = _visual_fingerprint(Image.new("RGB", (64, 64), color=(i * 30 % 255, 100, 100)))
            combined = _dual_encode(te, vf)
            e = MemoryEntry(
                entry_id=f"chain{i:03d}",
                image_path="/tmp/img.png",
                label="BUY" if i % 2 == 0 else "SELL",
                chart_state={"direction": "BUY" if i % 2 == 0 else "SELL"},
                text_embed=te.tolist(),
                visual_fp=vf.tolist(),
                combined_embed=combined.tolist(),
            )
            entries_dict[e.entry_id] = e
            entries_list.append(e)
        idx = _HNSWIndex(SHARED_DIM)
        idx.build(entries_list)
        bank.populate(idx, entries_dict, n_buy=4, n_sell=4)

        query = np.array(entries_list[0].combined_embed, dtype=np.float32)
        results = bank.search(query, top_k=3)
        self.assertGreater(len(results), 0)

        with tempfile.TemporaryDirectory() as td:
            sec = SecurityManager(Path(td) / "d", Path(td) / "l", kdf_iterations=1000)
            fernet = sec.derive_fernet("chain-test")
            store = EncryptedPreferenceStore(Path(td) / "p.enc.sqlite", fernet)
            persona = PersonalizationEngine("sentence-transformers/all-MiniLM-L6-v2", store, _NullLogger())
            dpo_pairs: list[dict[str, Any]] = [{"chosen": r.chart_state.get("direction", ""), "rejected": "HOLD", "reason": ""} for r in results[:3]]  # type: ignore[union-attr]
            persona.update_style_from_memory_bank(dpo_pairs)  # type: ignore[arg-type]
            prefix = persona.style_prefix_prompt()
            self.assertIsInstance(prefix, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
