from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable, cast

from PIL import Image
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

PROJECT_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_BOOTSTRAP))

from phoenixguard.decision.lstm_candle_sequence_contributor_v3 import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_METRICS_PATH,
    DEFAULT_MODEL_PATH,
    DEFAULT_SEQUENCE_LENGTH,
    FEATURE_SCHEMA,
    LSTM_CANDLE_SEQUENCE_VERSION,
    create_lstm_candle_sequence_model,
    phase_value,
)
from phoenixguard.paths import PROJECT_ROOT


DEFAULT_SEQUENCE_MANIFEST = PROJECT_ROOT / "data_splits" / "sequence_teacher_manifest.jsonl"
DEFAULT_SPLIT_MANIFEST = PROJECT_ROOT / "data_splits" / "split_manifest.csv"
DEFAULT_FEEDBACK_ROOT = PROJECT_ROOT / "data" / "feedback_assets"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
SIDE_TO_INDEX = {"BUY": 0, "SELL": 1}
INDEX_TO_SIDE = {0: "BUY", 1: "SELL"}
PLAY_TO_INDEX = {"CONTINUATION": 0, "REVERSAL": 1, "PULLBACK": 2}


def _side(value: Any, default: str = "") -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BUYS", "BULL", "BULLISH", "CALL"}:
        return "BUY"
    if text in {"SELL", "SELLS", "BEAR", "BEARISH", "PUT"}:
        return "SELL"
    return default


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _tensor_list(value: torch.Tensor) -> Any:
    tolist = cast(Callable[[], Any], getattr(value, "tolist"))
    return tolist()


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        value_map = cast(Mapping[str, Any], value)
        value = value_map.get("sequences") or value_map.get("rows") or []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(cast(Mapping[str, Any], item)) for item in cast(Sequence[Any], value) if isinstance(item, Mapping)]


def _load_json_dataset(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, Mapping):
                    rows.append(dict(cast(Mapping[str, Any], value)))
        return rows
    return _rows(json.loads(path.read_text(encoding="utf-8")))


def _play_label(*values: Any) -> str:
    text = " ".join(str(value or "").lower() for value in values)
    if any(token in text for token in ("reversal", "reclaim", "fakeout")):
        return "REVERSAL"
    if any(token in text for token in ("pullback", "retest", "pause", "compression", "consolidation")):
        return "PULLBACK"
    return "CONTINUATION"


def _quality_ok(row: Mapping[str, Any], mode: str) -> bool:
    if mode == "all":
        return True
    labels = _mapping(row.get("teacher_task_labels"))
    quality = str(labels.get("label_quality") or "").strip().lower()
    review_required = bool(labels.get("review_required", False))
    bucket = str(labels.get("review_bucket") or "").strip().lower()
    if mode == "clean_only":
        return quality in {"clean", "verified", "trusted"} and not review_required
    if mode == "exclude_review_required":
        return not review_required
    if mode == "exclude_contradictory":
        return bucket not in {"projection_conflict", "direction_conflict", "contradictory"} and "conflict" not in bucket
    return True


def _existing_path(*values: Any) -> Path | None:
    for value in values:
        if not value:
            continue
        path = Path(str(value))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if path.exists() and path.is_file():
            return path
    return None


def _split_manifest_source_map(path: Path) -> dict[str, Path]:
    if not path.exists():
        return {}
    mapping: dict[str, Path] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            destination = row.get("destination_path") or ""
            source = row.get("source_path") or ""
            source_path = _existing_path(source)
            if destination and source_path is not None:
                mapping[str(Path(destination))] = source_path
                mapping[str(Path(destination).resolve())] = source_path
    return mapping


def image_to_sequence_features(
    image_path: Path,
    *,
    sequence_length: int = DEFAULT_SEQUENCE_LENGTH,
    phase: str = "",
) -> list[list[float]]:
    image = Image.open(image_path).convert("RGB")
    resized = image.resize((int(sequence_length), 96))
    width, height = resized.size
    pixels = resized.load()
    if pixels is None:
        raise RuntimeError(f"Unable to load pixels from {image_path}")
    phase_num = phase_value(phase)
    rows: list[list[float]] = []
    previous_price = 0.5
    for x in range(width):
        active_y: list[int] = []
        green_score = 0.0
        red_score = 0.0
        magenta_score = 0.0
        brightness_sum = 0.0
        for y in range(height):
            pixel = pixels[x, y]
            if not isinstance(pixel, tuple) or len(pixel) < 3:
                continue
            r, g, b = pixel[:3]
            brightness = (r + g + b) / 765.0
            saturation = (max(r, g, b) - min(r, g, b)) / 255.0
            is_market_pixel = saturation > 0.16 and brightness > 0.12
            if is_market_pixel:
                active_y.append(y)
                brightness_sum += brightness
                green_score += max(0.0, (g - max(r, b)) / 255.0)
                red_score += max(0.0, (r - g) / 255.0)
                magenta_score += max(0.0, (r + b - 2 * g) / 510.0)
        if active_y:
            top = min(active_y)
            bottom = max(active_y)
            center = sum(active_y) / len(active_y)
            range_norm = max(0.001, (bottom - top + 1) / height)
            price_location = max(0.0, min(1.0, 1.0 - center / height))
            body_norm = max(0.0, min(1.0, len(active_y) / height))
            upper_wick = max(0.0, min(1.0, (center - top) / max(1.0, bottom - top + 1)))
            lower_wick = max(0.0, min(1.0, (bottom - center) / max(1.0, bottom - top + 1)))
        else:
            price_location = previous_price
            range_norm = 0.001
            body_norm = 0.0
            upper_wick = 0.0
            lower_wick = 0.0
        color_edge = green_score - max(red_score, magenta_score)
        if abs(color_edge) >= 0.02:
            direction_value = 1.0 if color_edge > 0 else -1.0
        else:
            direction_value = 1.0 if price_location >= previous_price else -1.0
        previous_price = price_location
        rows.append(
            [
                round(body_norm, 6),
                round(upper_wick, 6),
                round(lower_wick, 6),
                round(direction_value, 6),
                round(range_norm, 6),
                round(price_location, 6),
                round(phase_num, 6),
            ]
        )
    return rows


def _dataset_rows_from_json(path: Path, sequence_length: int) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    for row in _load_json_dataset(path):
        candles = row.get("candles")
        if not isinstance(candles, Sequence) or isinstance(candles, (str, bytes, bytearray)):
            continue
        candle_values = cast(Sequence[Any], candles)
        if len(candle_values) < 8:
            continue
        next_1 = _side(row.get("label_next_1_direction"))
        next_2 = _side(row.get("label_next_2_direction"), next_1)
        if next_1 not in SIDE_TO_INDEX:
            continue
        matrix: list[list[float]] = []
        for candle in candle_values:
            if not isinstance(candle, Mapping):
                continue
            candle_map = cast(Mapping[str, Any], candle)
            direction = _side(candle_map.get("direction"))
            matrix.append(
                [
                    float(candle_map.get("body_norm", 0.0) or 0.0),
                    float(candle_map.get("upper_wick_norm", 0.0) or 0.0),
                    float(candle_map.get("lower_wick_norm", 0.0) or 0.0),
                    1.0 if direction == "BUY" else -1.0 if direction == "SELL" else 0.0,
                    float(candle_map.get("range_norm", 0.0) or 0.0),
                    float(candle_map.get("relative_price_location", 0.0) or 0.0),
                    phase_value(candle_map.get("phase", row.get("label_play", ""))),
                ]
            )
        matrix = ([[0.0] * len(FEATURE_SCHEMA)] * max(0, sequence_length - len(matrix))) + matrix[-sequence_length:]
        clean.append(
            {
                "sequence": matrix,
                "next_1": SIDE_TO_INDEX[next_1],
                "next_2": SIDE_TO_INDEX[next_2 if next_2 in SIDE_TO_INDEX else next_1],
                "play": PLAY_TO_INDEX[_play_label(row.get("label_play"))],
                "split": str(row.get("split") or "train").lower(),
                "source": str(path),
            }
        )
    return clean


def _manifest_rows(
    manifest_path: Path,
    *,
    split_manifest_path: Path,
    sequence_length: int,
    quality_mode: str,
    max_rows: int,
) -> list[dict[str, Any]]:
    if not manifest_path.exists():
        return []
    source_map = _split_manifest_source_map(split_manifest_path)
    rows: list[dict[str, Any]] = []
    for raw in _load_json_dataset(manifest_path):
        if max_rows > 0 and len(rows) >= max_rows:
            break
        if not _quality_ok(raw, quality_mode):
            continue
        image_path = _existing_path(raw.get("image_path"))
        if image_path is None and raw.get("image_path"):
            image_path = source_map.get(str(Path(str(raw.get("image_path")))))
        if image_path is None:
            image_path = _existing_path(raw.get("source_path"))
        if image_path is None:
            continue
        targets = _mapping(raw.get("sequence_targets"))
        teacher = _mapping(raw.get("teacher_task_labels"))
        next_1 = _side(
            targets.get("next_box_direction")
            or teacher.get("effective_next_box_direction_label")
            or raw.get("label")
        )
        next_2 = _side(
            targets.get("projection_direction")
            or teacher.get("effective_projection_direction_label")
            or next_1
        )
        if next_1 not in SIDE_TO_INDEX:
            continue
        play = _play_label(
            targets.get("trigger"),
            targets.get("projected_role"),
            targets.get("entry_type"),
            targets.get("local_phase"),
            targets.get("swing_phase"),
        )
        rows.append(
            {
                "sequence": image_to_sequence_features(
                    image_path,
                    sequence_length=sequence_length,
                    phase=str(targets.get("local_phase") or targets.get("trigger") or ""),
                ),
                "next_1": SIDE_TO_INDEX[next_1],
                "next_2": SIDE_TO_INDEX[next_2 if next_2 in SIDE_TO_INDEX else next_1],
                "play": PLAY_TO_INDEX[play],
                "split": str(raw.get("split") or "train").lower(),
                "source": str(image_path),
                "label": str(raw.get("label") or next_1).upper(),
            }
        )
    return rows


def _feedback_rows(root: Path, sequence_length: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for folder_name, side in (("buy", "BUY"), ("sell", "SELL"), ("BUY", "BUY"), ("SELL", "SELL")):
        folder = root / folder_name
        if not folder.exists():
            continue
        for image_path in sorted(folder.iterdir()):
            if not image_path.is_file() or image_path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            path_key = str(image_path.resolve()).casefold()
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            rows.append(
                {
                    "sequence": image_to_sequence_features(image_path, sequence_length=sequence_length, phase="feedback"),
                    "next_1": SIDE_TO_INDEX[side],
                    "next_2": SIDE_TO_INDEX[side],
                    "play": PLAY_TO_INDEX["CONTINUATION"],
                    "split": "train",
                    "source": str(image_path),
                    "label": side,
                }
            )
    return rows


class CandleSequenceDataset(Dataset[tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]]):
    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.rows = list(rows)

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        row = self.rows[index]
        return (
            torch.tensor(row["sequence"], dtype=torch.float32),
            torch.tensor(int(row["next_1"]), dtype=torch.long),
            torch.tensor(int(row["next_2"]), dtype=torch.long),
            torch.tensor(int(row["play"]), dtype=torch.long),
        )


def _split_rows(rows: list[dict[str, Any]], seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train_rows = [row for row in rows if str(row.get("split", "train")).lower() == "train"]
    val_rows = [row for row in rows if str(row.get("split", "")).lower() in {"val", "valid", "validation", "test"}]
    if train_rows and val_rows:
        return train_rows, val_rows
    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)
    val_count = max(1, int(len(shuffled) * 0.15)) if len(shuffled) >= 8 else 0
    return shuffled[val_count:], shuffled[:val_count]


def _balanced_accuracy(y_true: Sequence[int], y_pred: Sequence[int]) -> float:
    recalls: list[float] = []
    for label in (0, 1):
        total = sum(1 for item in y_true if item == label)
        correct = sum(1 for true, pred in zip(y_true, y_pred) if true == label and pred == label)
        if total:
            recalls.append(correct / total)
    return float(sum(recalls) / len(recalls)) if recalls else 0.0


def _binary_auc(y_true: Sequence[int], scores: Sequence[float]) -> float:
    positives = [score for label, score in zip(y_true, scores) if label == 1]
    negatives = [score for label, score in zip(y_true, scores) if label == 0]
    if not positives or not negatives:
        return 0.5
    wins = 0.0
    total = 0
    for pos in positives:
        for neg in negatives:
            total += 1
            if pos > neg:
                wins += 1.0
            elif pos == neg:
                wins += 0.5
    return wins / max(1, total)


def evaluate(model: Any, rows: Sequence[Mapping[str, Any]], batch_size: int) -> dict[str, Any]:
    if not rows:
        return {
            "next_1_direction_accuracy": 0.0,
            "next_2_direction_accuracy": 0.0,
            "balanced_accuracy": 0.0,
            "continuation_auc": 0.5,
            "reversal_auc": 0.5,
            "calibration_error": 1.0,
            "confusion_matrix": [[0, 0], [0, 0]],
        }
    loader = DataLoader(CandleSequenceDataset(rows), batch_size=batch_size, shuffle=False)
    next_1_true: list[int] = []
    next_1_pred: list[int] = []
    next_2_true: list[int] = []
    next_2_pred: list[int] = []
    play_true: list[int] = []
    continuation_scores: list[float] = []
    reversal_scores: list[float] = []
    confidence_errors: list[float] = []
    confusion = [[0, 0], [0, 0]]
    model.eval()
    with torch.inference_mode():
        for batch in loader:
            sequence, y1, y2, play = cast(tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor], batch)
            outputs = cast(Mapping[str, torch.Tensor], model(sequence))
            p1 = torch.softmax(outputs["next_1_logits"], dim=-1)
            p2 = torch.softmax(outputs["next_2_logits"], dim=-1)
            play_probs = torch.softmax(outputs["play_logits"], dim=-1)
            pred1 = torch.argmax(p1, dim=-1)
            pred2 = torch.argmax(p2, dim=-1)
            y1_values = cast(list[int], _tensor_list(y1))
            pred1_values = cast(list[int], _tensor_list(pred1))
            p1_values = cast(list[list[float]], _tensor_list(p1))
            for true, pred, probs in zip(y1_values, pred1_values, p1_values):
                next_1_true.append(int(true))
                next_1_pred.append(int(pred))
                confusion[int(true)][int(pred)] += 1
                confidence_errors.append(abs(float(max(probs)) - float(true == pred)))
            next_2_true.extend(int(item) for item in cast(list[int], _tensor_list(y2)))
            next_2_pred.extend(int(item) for item in cast(list[int], _tensor_list(pred2)))
            play_true.extend(int(item) for item in cast(list[int], _tensor_list(play)))
            play_prob_rows = cast(list[list[float]], _tensor_list(play_probs))
            continuation_scores.extend(float(row[0]) for row in play_prob_rows)
            reversal_scores.extend(float(row[1]) for row in play_prob_rows)
    next_1_acc = sum(int(a == b) for a, b in zip(next_1_true, next_1_pred)) / max(1, len(next_1_true))
    next_2_acc = sum(int(a == b) for a, b in zip(next_2_true, next_2_pred)) / max(1, len(next_2_true))
    continuation_auc = _binary_auc([1 if item == PLAY_TO_INDEX["CONTINUATION"] else 0 for item in play_true], continuation_scores)
    reversal_auc = _binary_auc([1 if item == PLAY_TO_INDEX["REVERSAL"] else 0 for item in play_true], reversal_scores)
    return {
        "next_1_direction_accuracy": round(next_1_acc, 4),
        "next_2_direction_accuracy": round(next_2_acc, 4),
        "balanced_accuracy": round(_balanced_accuracy(next_1_true, next_1_pred), 4),
        "continuation_auc": round(continuation_auc, 4),
        "reversal_auc": round(reversal_auc, 4),
        "calibration_error": round(sum(confidence_errors) / max(1, len(confidence_errors)), 4),
        "confusion_matrix": confusion,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train PhoenixGuard LSTM candle-sequence V3 from memory BUY/SELL images.")
    parser.add_argument("--dataset", type=Path, help="Optional JSON/JSONL observed candle-sequence dataset.")
    parser.add_argument("--sequence-manifest", type=Path, default=DEFAULT_SEQUENCE_MANIFEST)
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument("--feedback-root", type=Path, default=DEFAULT_FEEDBACK_ROOT)
    parser.add_argument("--include-feedback-assets", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--quality-mode", choices=["all", "exclude_contradictory", "exclude_review_required", "clean_only"], default="exclude_contradictory")
    parser.add_argument("--max-rows", type=int, default=0)
    parser.add_argument("--sequence-length", type=int, default=DEFAULT_SEQUENCE_LENGTH)
    parser.add_argument("--hidden-dim", type=int, default=48)
    parser.add_argument("--num-layers", type=int, default=1)
    parser.add_argument("--dropout", type=float, default=0.0)
    parser.add_argument("--epochs", type=int, default=14)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--metrics-path", type=Path, default=DEFAULT_METRICS_PATH)
    parser.add_argument("--normal-analysis", action="store_true", help="Allow this contributor to be used by normal analysis in addition to high-frequency study.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    random.seed(int(args.seed))
    manual_seed = cast(Callable[[int], Any], getattr(torch, "manual_seed"))
    manual_seed(int(args.seed))

    rows: list[dict[str, Any]] = []
    if args.dataset:
        rows.extend(_dataset_rows_from_json(args.dataset, int(args.sequence_length)))
    else:
        rows.extend(
            _manifest_rows(
                args.sequence_manifest,
                split_manifest_path=args.split_manifest,
                sequence_length=int(args.sequence_length),
                quality_mode=str(args.quality_mode),
                max_rows=int(args.max_rows),
            )
        )
    if bool(args.include_feedback_assets):
        rows.extend(_feedback_rows(args.feedback_root, int(args.sequence_length)))
    if not rows:
        print(json.dumps({"ok": False, "error": "no_training_rows", "sequence_manifest": str(args.sequence_manifest)}, indent=2))
        return 2

    train_rows, val_rows = _split_rows(rows, int(args.seed))
    if not train_rows:
        print(json.dumps({"ok": False, "error": "no_train_rows", "row_count": len(rows)}, indent=2))
        return 2
    model = create_lstm_candle_sequence_model(
        input_dim=len(FEATURE_SCHEMA),
        hidden_dim=int(args.hidden_dim),
        num_layers=int(args.num_layers),
        dropout=float(args.dropout),
    )
    optimizer: torch.optim.Optimizer = torch.optim.AdamW(model.parameters(), lr=float(args.lr), weight_decay=1e-4)
    train_loader = DataLoader(CandleSequenceDataset(train_rows), batch_size=int(args.batch_size), shuffle=True)
    history: list[dict[str, Any]] = []
    best_state: dict[str, Any] | None = None
    best_score = -1.0
    for epoch in range(1, int(args.epochs) + 1):
        model.train()
        total_loss = 0.0
        total_batches = 0
        for batch in train_loader:
            sequence, y1, y2, play = cast(tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor], batch)
            optimizer.zero_grad(set_to_none=True)
            outputs = cast(Mapping[str, torch.Tensor], model(sequence))
            loss: torch.Tensor = (
                F.cross_entropy(outputs["next_1_logits"], y1)
                + 0.75 * F.cross_entropy(outputs["next_2_logits"], y2)
                + 0.30 * F.cross_entropy(outputs["play_logits"], play)
            )
            backward = cast(Callable[[], Any], getattr(loss, "backward"))
            backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer_step = cast(Callable[[], Any], getattr(optimizer, "step"))
            optimizer_step()
            total_loss += float(loss.item())
            total_batches += 1
        metrics = evaluate(model, val_rows or train_rows, int(args.batch_size))
        score = float(metrics["balanced_accuracy"]) + 0.35 * float(metrics["next_2_direction_accuracy"])
        if score > best_score:
            best_score = score
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        history.append({"epoch": epoch, "loss": round(total_loss / max(1, total_batches), 5), **metrics})

    if best_state is not None:
        model.load_state_dict(best_state)
    final_metrics = evaluate(model, val_rows or train_rows, int(args.batch_size))
    production_ready = bool(
        val_rows
        and len(train_rows) >= 16
        and float(final_metrics["balanced_accuracy"]) >= 0.40
        and float(final_metrics["next_1_direction_accuracy"]) >= 0.40
    )
    args.model_path.parent.mkdir(parents=True, exist_ok=True)
    args.config_path.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "schema_version": "PG_LSTM_CANDLE_SEQUENCE_ARTIFACT_V3",
            "model_version": LSTM_CANDLE_SEQUENCE_VERSION,
            "state_dict": model.state_dict(),
            "feature_schema": list(FEATURE_SCHEMA),
            "index_to_side": INDEX_TO_SIDE,
            "play_to_index": PLAY_TO_INDEX,
        },
        args.model_path,
    )
    config: dict[str, Any] = {
        "schema_version": "PG_LSTM_CANDLE_SEQUENCE_CONFIG_V3",
        "model_version": LSTM_CANDLE_SEQUENCE_VERSION,
        "feature_schema": list(FEATURE_SCHEMA),
        "input_dim": len(FEATURE_SCHEMA),
        "sequence_length": int(args.sequence_length),
        "hidden_dim": int(args.hidden_dim),
        "num_layers": int(args.num_layers),
        "dropout": float(args.dropout),
        "dataset_path": str(args.dataset or args.sequence_manifest),
        "split_manifest_path": str(args.split_manifest),
        "training_rows": len(train_rows),
        "validation_rows": len(val_rows),
        "high_frequency_enabled": True,
        "normal_analysis_enabled": bool(args.normal_analysis),
        "default_usage": "HIGH_FREQUENCY",
        "production_ready": production_ready,
        "artifact_path": str(args.model_path),
    }
    metrics: dict[str, Any] = {
        "schema_version": "PG_LSTM_CANDLE_SEQUENCE_METRICS_V3",
        "model_version": LSTM_CANDLE_SEQUENCE_VERSION,
        "training_rows": len(train_rows),
        "validation_rows": len(val_rows),
        "source_rows": len(rows),
        **final_metrics,
        "production_ready": production_ready,
        "history": history,
    }
    args.config_path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
    args.metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "model_path": str(args.model_path),
                "config_path": str(args.config_path),
                "metrics_path": str(args.metrics_path),
                "training_rows": len(train_rows),
                "validation_rows": len(val_rows),
                "production_ready": production_ready,
                "metrics": final_metrics,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
