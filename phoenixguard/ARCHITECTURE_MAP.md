# PhoenixGuard One-Page Architecture Map

## 1) Input
- User provides image/file (chart screenshot or equivalent).
- Entry path: `run_inference(...)` in `main.py`.

## 2) Model Layer (ordered execution)
1. **Preprocess**
   - `load_any_file_as_image` -> `normalize_for_model` (`preprocess.py`).
2. **CV model (HF endpoint or YOLO fallback path)**
   - `cv_engine.detect(img)` (`cv_module.py`).
3. **Structured chart-state extraction**
   - Heuristic/CV-native chart-state payload is built from live chart structure.
4. **Memory retrieval + context injection**
   - `_get_memory_bank()` load (`main.py` + `memory_ingest.py`).
   - `embed_description(chart_state, image=img)` -> `search(top_k=5)`.
   - Build few-shot context + compute memory boost.
5. **Regression forecasting**
   - `chronos.forecast_3m(chart_state, quantiles)` (`regression_module.py`).
6. **Style personalization update**
   - `personal.update_style(... )` (`personalization.py`).
7. **RL policy inference**
   - `rl_engine.infer(fused_features, memory_recall_top1_sim, memory_recall_direction)` (`rl_module.py`).

## 3) Feature Fusion Layer
- Fused vector combines:
  - CV detection stats,
  - chart-structure priors,
  - Chronos quantiles,
  - personalization style vector.
- Built by `fused_feature_vector(...)` in `main.py`.

## 4) Gate Layer (12-gate curriculum)
- Executed by `gates_engine.run_all(...)` in `skill_gates.py`.
- Gates include:
  1. Probability + conformal calibration
  2. Discrete FSM
  3. Algorithmic heap signal ranking
  4. Meta stacking
  5. Context retrieval
  6. Ops stability
  7. UI analytics
  8. Meta constraints
  9. Regression error estimation
  10. Knowledge representation (ontology coherence)
  11. Formal automata state progression
  12. Predictive analytics fusion
- Supporting checks:
  - continuation strength,
  - macro/local alignment,
  - memory regime agreement,
  - opposition pressure,
  - execution permission,
  - forecast calibration,
  - interval efficiency,
  - regime stability,
  - transition alignment.
- Router weighting:
  - `LinearRouter(12->12)` scales gate scores.
  - MoE route weights are added as metadata for explainability.

## 5) Ensemble Decision Layer
- `ensemble.infer(...)` in `ensemble.py` fuses:
  - RL calibrated probabilities,
  - gate scores,
  - memory similarity,
  - forecast interval constraints.
- Consensus rules enforce:
  - confidence threshold,
  - minimum gates passing,
  - memory similarity requirement (or no-memory mode),
  - interval safety.

## 6) Final Action + Outputs
- Final action: `BUY` / `SELL` / `HOLD`.
- Position sizing from confidence + expected move.
- Explainability artifacts:
  - gate scores,
  - approximate SHAP contributions,
  - memory similarity,
  - forecast stats.
- Visualization:
  - chart overlay,
  - confidence gauge,
  - optional skill dashboard.

## 7) Online Adaptation Loop
- If memory recall exists, RL receives recall-driven update batches.
- Router weights update from feedback history.
- Personalization vector evolves from user feedback + memory-bank DPO pairs.
