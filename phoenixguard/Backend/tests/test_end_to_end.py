from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from typing import Any

import numpy as np
from PIL import Image

from phoenixguard.vision.preprocess import load_any_file_as_image, normalize_for_model
from phoenixguard.decision.ensemble import EnsembleDecisionEngine
from phoenixguard.decision.skill_gates import CurriculumGates
from phoenixguard.decision.rl_module import RLPolicyEngine
from phoenixguard.runtime.security import SecurityManager


class TestPhoenixGuardE2E(unittest.TestCase):
    def test_preprocess_non_image_and_image(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sample.txt"
            p.write_text("BUY breakout near support.", encoding="utf-8")
            img, meta = load_any_file_as_image(p)
            self.assertIn("sha256", meta)
            self.assertGreater(img.size[0], 0)

            p2 = Path(td) / "sample.png"
            Image.new("RGB", (32, 32), color=(0, 0, 0)).save(p2)
            img2, meta2 = load_any_file_as_image(p2)
            self.assertEqual(meta2["source_type"], "image")
            norm = normalize_for_model(img2, out_size=128)
            self.assertEqual(norm.size, (128, 128))

    def test_rl_gates_ensemble_path(self):
        rl = RLPolicyEngine(logger=_NullLogger(), in_dim=64, mcts_sims=5)
        state = np.zeros((64,), dtype=np.float32)
        out = rl.infer(state)
        self.assertEqual(set(out.probs.keys()), {"BUY", "SELL", "HOLD"})

        gates = CurriculumGates(_NullLogger())
        gate_outputs = gates.run_all(
            probs=out.probs,
            q05=-0.1,
            q95=0.2,
            momentum_bias="neutral",
            explanation="flat momentum",
            sub_signals=[(0.7, "triangle")],
            module_logits=np.array([0.3, 0.3, 0.4], dtype=np.float32),
            recent_feedback_count=5,
            queue_depth=0,
            gpu_mem_ok=True,
            has_dashboard=True,
            risk_ethical_ok=True,
        )
        # SIGE-VLA 3.0 uses the full 13-gate council.
        self.assertEqual(len(gate_outputs), 13)

        ensemble = EnsembleDecisionEngine(0.78, 0.65, 0.5, 2.0)
        decision = ensemble.infer(out.probs, {"q05": -0.1, "q50": 0.1, "q95": 0.2}, gate_outputs)
        self.assertIn(decision["action"], {"BUY", "SELL", "HOLD"})
        self.assertGreaterEqual(decision["position_size_pct"], 0.5)

    def test_security_encrypt_decrypt(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            sec = SecurityManager(d, d)
            fernet = sec.derive_fernet("unit-test-passphrase")
            token = sec.encrypt_bytes(b"hello", fernet)
            plain = sec.decrypt_bytes(token, fernet)
            self.assertEqual(plain, b"hello")


class _NullLogger:
    def info(self, *args: Any, **kwargs: Any) -> None:
        return None

    def warning(self, *args: Any, **kwargs: Any) -> None:
        return None

    def exception(self, *args: Any, **kwargs: Any) -> None:
        return None

    def error(self, *args: Any, **kwargs: Any) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
