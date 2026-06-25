# Agent 4 Developer Migration Report

## CLEAR ANSWER

Developer-only training, model-export, data-split, sequence-teacher, and diagnostic tools were moved to `Developer`.

## CONFIDENCE LEVEL

0.90

## KEY CAVEATS

Some developer scripts still import runtime modules. That is valid: development code consumes the backend package through `Backend/src`, it does not define runtime authority.

## FILES STUDIED

- `Developer/model_training`
- `Developer/model_exports`
- `Developer/datasets`
- `Developer/sequence_teacher`
- `Developer/developer_tools`
- Training-related tests

## FIXES APPLIED

- Moved `train_*.py` into `Developer/model_training`.
- Moved `export_inference_bundles.py` into `Developer/model_exports`.
- Moved `build_clean_split.py` into `Developer/datasets`.
- Moved `build_sequence_teacher_manifest.py` into `Developer/sequence_teacher`.
- Moved `hf_model_check.py`, `run_skill_gates_only.py`, and `quick_start_tracker_test.py` into `Developer/developer_tools`.
- Updated tests/imports that referenced moved developer modules.

## TESTS RUN

Developer module import paths are covered by Pyright and pytest collection.

## REMAINING RISKS

Heavy model-training execution was not run; this migration verified importability and test discovery, not a training job.
