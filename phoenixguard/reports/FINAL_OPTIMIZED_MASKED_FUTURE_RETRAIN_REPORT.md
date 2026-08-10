# Final Optimized Masked-Future V3 Retrain Report

## Final recommendation

**ROOT_CAUSE_BLOCKED_BY_DATA_OR_LABEL_QUALITY**

Promotion eligible: **False**

This contributor remains hidden-state evidence. It cannot grant entry permission,
bypass source lock, construct PG_EXECUTION_PACKET_V3, or call the shooter.

## Dataset and leakage

- Independent near-duplicate families: 53
- All causal windows: 37930
- Eligible event windows: 19365
- Outer-fold prediction rows: 19365
- Leakage audit: PASS

## Model suite

- empirical_prior
- gradient_boosted_event
- sequence_gru
- patch_sequence_transformer
- prefix_geometry_fusion
- calibrated_meta_labeler

The vision-fusion member uses prefix-only candle geometry. Full screenshot
embeddings were excluded because unmasked screenshots contain future pixels.

## Out-of-sample results

| Metric | Result |
|---|---:|
| Event-conditioned direction accuracy | 43.51% |
| Target-before-invalidation precision | 50.00% |
| High-confidence selective precision | 53.85% |
| High-confidence coverage | 11.74% |
| Visible pullback resolution | 48.60% |
| Counter-move classification | 56.75% |
| Brier score | 0.226578 |
| Log loss | 0.657231 |
| Expected calibration error | 0.031250 |

## Event breakdown

| Event | Rows | Direction accuracy | Selected | Selected precision |
|---|---:|---:|---:|---:|
| BREAK_AND_HOLD | 4818 | 43.77% | 45 | 42.22% |
| CONTINUATION_PRESSURE | 1422 | 43.95% | 41 | 34.15% |
| FAILED_BREAKOUT | 259 | 43.24% | 23 | 43.48% |
| OPPOSING_FORCE_TOUCH | 2294 | 29.99% | 663 | 64.40% |
| PULLBACK_VISIBLE | 3331 | 48.60% | 1132 | 49.82% |
| RECLAIM_AFTER_SWEEP | 4039 | 44.59% | 140 | 42.86% |
| RESISTANCE_REACTION | 1603 | 47.60% | 136 | 61.76% |
| SUPPORT_REACTION | 1599 | 44.28% | 93 | 49.46% |

## Acceptance gates

| Gate | Result |
|---|---|
| leakage_audit_pass | PASS |
| grouped_validation_pass | PASS |
| old_baseline_beaten | PASS |
| visible_pullback_improves | FAIL |
| high_confidence_precision_at_least_70 | FAIL |
| coverage_at_least_20 | FAIL |
| target_before_invalidation_precision_at_least_65 | FAIL |
| brier_improves_over_prevalence | PASS |
| no_direct_execution_authority | PASS |

## Disk contract

- Free before: 46.844 GB
- Free after: 47.050 GB
- Required reserve: 45.000 GB
- Images duplicated: no
- Runtime artifact: .codex_runtime\optimized_masked_future\PG_OPTIMIZED_HIDDEN_STATE_MODEL_V3.json.gz
- Packaged artifact: not packaged because promotion gates failed

## Integration rule

If promoted, V3 may consume this as masked_future_optimized_v3,
opportunity_maturity, target_before_invalidation, and pullback-resolution evidence.
It remains incapable of direct execution authorization.
