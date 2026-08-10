# Trade-Quality Replay V3 Report

This report scores frozen grouped out-of-sample predictions. It is not a strategy
backtest and it does not infer slippage, spread, payout, or guaranteed profit.

| Measure | Result |
|---|---:|
| Eligible replay windows | 19365 |
| High-confidence selected | 2273 |
| Selection coverage | 11.74% |
| Selected target-before-invalidation precision | 53.85% |
| Mean MFE | 1.773 ranges |
| Mean MAE | 1.302 ranges |
| Drawdown-first | 12537 |
| Good waits | 11120 |
| Bad blocks / missed valid events | 5972 |
| Late-chase observations | 330 |

Outcomes: {"INVALIDATION_BEFORE_TARGET": 11468, "TARGET_BEFORE_INVALIDATION": 7196, "TIME_BARRIER_EXPIRED": 701}.

The replay ledger does not create an allowance package or PG_EXECUTION_PACKET_V3.
