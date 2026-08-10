# Masked-Future V3 Model Comparison

| Measure | Empirical V3 | Optimized selective V3 |
|---|---:|---:|
| Broad 13/21 accuracy | 52.39% | Event conditioned |
| Visible pullback | 57.64% | 48.60% |
| Target-before-invalidation precision | Not labelled | 50.00% |
| High-confidence precision | Not measured | 53.85% |
| High-confidence coverage | Not measured | 11.74% |
| Brier | broad horizons | 0.226578 |
| Recommendation | Hidden-state prior | ROOT_CAUSE_BLOCKED_BY_DATA_OR_LABEL_QUALITY |

The optimized score cannot replace the broad empirical model. It is a calibrated
event and trade-path contributor layered on top of that prior.
