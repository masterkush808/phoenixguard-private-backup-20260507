# 808Fx Standard Hybrid System

## Overview
808Fx Standard Hybrid System is an advanced chart-analysis workstation for financial signal review, memory-augmented reasoning, and multi-module decision support. It combines layered visual intelligence, historical recall, and consensus logic for robust trading signal generation.

## Features
- MemoryBank (HNSW few-shot recall + logit boost)
- 12-gate CurriculumGates (formal automata, ontology, regression, predictive)
- Live support checks for continuation strength, trend alignment, memory alignment, counterforce, execution permission, forecast calibration, interval efficiency, regime stability, and transition alignment
- 3-condition ensemble consensus
- Per-run Plotly skill-contribution dashboard
- Online RL update every 50 memory-bank recalls
- Reactive Gradio workstation with live overlay tuning
- Confidence heatmap, compare desk, and scenario lab
- Zone Studio with persistent support/resistance/reaction teaching memory
- Session timeline and pattern browser for in-session review
- Multi-timeframe hotkey capture workflow with floating HUD

## Project Layout
- `phoenixguard/core/`: shared config and utility helpers
- `phoenixguard/vision/`: preprocessing, CV reasoning, grounded parsing, and detector logic
- `phoenixguard/runtime/`: security, adaptation, local ensemble runtime, and continual adapters
- `phoenixguard/memory/`: memory bank ingestion and retrieval features
- `phoenixguard/decision/`: ensemble, regression, RL, gates, and personalization
- `phoenixguard/training/`: reusable training implementation
- Root `main.py`, `train_*.py`, and `hf_model_check.py`: entry scripts that now point into the organized package layout

## Setup Instructions

### 1. Clone the repository and enter the project directory
```
cd "c:/Users/thaba/OneDrive/Documents/The 808 Vision 2026/phoenixguard"
```

### 2. Create and activate the virtual environment
```
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies once
```
pip install -r requirements.txt
```

### 4. Fast launch
```
.\.venv\Scripts\Activate.ps1
.\start_phoenixguard.ps1
```

The launcher now defaults to the `FAST` runtime profile so startup avoids the heaviest warmups and optional CPU ensemble loading.

### 5. Optional runtime profiles
```
.\start_phoenixguard.ps1 -Profile FAST
.\.venv\Scripts\Activate.ps1
.\start_phoenixguard.ps1 -Profile BALANCED
.\start_phoenixguard.ps1 -Profile FULL
.\start_phoenixguard.ps1 -Profile HEAVY_LAZY
```

- `FAST`: quickest startup and lighter first inference. Disables test-time adaptation, replay continual learning, foundation grounded backends, and local ensemble auto-load.
- `BALANCED`: keeps a lighter runtime while leaving an upgrade path to the full stack.
- `FULL`: restores the heavier experience, including launch-time preloading and CUDA local-ensemble enablement when available.
- `HEAVY_LAZY`: keeps startup light, but automatically runs the heavyweight council on inference through the persistent worker. On CPU, it requests the full council lazily, keeps only a small resident model cache, and reuses cached results for static images.
- `Model Council` is now lazy-loaded from its tab. Opening that tab starts a persistent local worker, loads heavyweight council models on demand, and reuses the refined result for the current static image instead of front-loading that cost at app launch.

### 6. Optional bootstrap / validation path
```
.\start_phoenixguard.ps1 -Bootstrap -RunTests -CheckHF -Profile FULL
```

Use this when you intentionally want dependency refresh, tests, and Hugging Face validation. It is no longer part of the default launch path.

### 7. Optional hotkey capture workflow
- Press `F4` to open a drag-select capture overlay on Windows.
- Drag the chart region, then press `Enter` to confirm or `Esc` to cancel.
- If `F4` is unavailable, the app falls back to `Ctrl+Shift+4`.
- The first confirmed capture is staged as the higher timeframe.
- Switch to the trigger timeframe and press the hotkey again.
- After the second confirmation, the app runs multi-timeframe inference automatically and refreshes the open desk.

### 8. Visual Lab workflow
- `Compare Desk` shows raw, focused, annotated, and heatmap views with client-side zoom and pan controls.
- `Scenario Lab` clones the current chart into a threshold sandbox without overwriting the live desk.
- `Zone Studio` lets you paint support, resistance, and reaction zones that are saved into persistent teaching memory.

### 9. Session review workflow
- `Session Timeline` keeps the analyzed captures from the current session in order.
- `Pattern Browser` surfaces visually similar session cases based on action, projection, confidence, and memory profile.

### 10. Worldwide protected sharing
- Use `share_phoenixguard.py` or `.\start_phoenixguard_share.ps1` for remote access instead of exposing `main.py`.
- Quickest worldwide path:
```
$env:PHOENIXGUARD_SHARE_CREDENTIALS='you:StrongPass2026!,brother:BrotherPass2026!'
.\start_phoenixguard_share.ps1 -LaunchMode FAST -AccessMode TUNNEL
```
- `TUNNEL` keeps the app on `127.0.0.1` and lets Gradio generate a temporary public HTTPS link protected by login.
- `PUBLIC` binds to `0.0.0.0`, but that alone is still not worldwide. You also need port forwarding or a reverse proxy/tunnel.
- See `WORLDWIDE_SHARE.md` for the secure quick-share path and the Cloudflare Tunnel setup.

## Notes
- The pipeline uses a layered proprietary vision ensemble tuned locally on your chart images from `808 Memory/BUYS` and `808 Memory/SELLS`.
- Fine-tuning and model saving are fully automated; model assets are stored locally for fast, private inference.
- Runtime behavior can be tuned with `PHOENIXGUARD_PROFILE` and the `PHOENIXGUARD_*` overrides in `phoenixguard/core/config.py`.
- If you encounter missing module errors, install them with `pip install <module>`.
- For Ultralytics settings, see: https://docs.ultralytics.com/quickstart/#ultralytics-settings

## License
Proprietary. All rights reserved.
