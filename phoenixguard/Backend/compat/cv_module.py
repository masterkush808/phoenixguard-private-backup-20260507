from __future__ import annotations

from _pg_bootstrap import ensure_project_paths
ensure_project_paths()

from phoenixguard.vision.cv_module import (
    CVPatternDetector,
    HfHubDownloadCallable,
    KMeansCtor,
    KMeansLike,
    LoggerLike,
    LogisticRegCtor,
    MakePipelineCallable,
    PatternDetection,
    SklearnClassifierLike,
    StandardScalerCtor,
    StandardScalerLike,
    TrainTestSplitCallable,
)

__all__ = [
    "CVPatternDetector",
    "HfHubDownloadCallable",
    "KMeansCtor",
    "KMeansLike",
    "LoggerLike",
    "LogisticRegCtor",
    "MakePipelineCallable",
    "PatternDetection",
    "SklearnClassifierLike",
    "StandardScalerCtor",
    "StandardScalerLike",
    "TrainTestSplitCallable",
]
