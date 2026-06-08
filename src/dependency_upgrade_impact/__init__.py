"""Offline dependency upgrade impact analysis."""

from .analyzer import analyze
from .models import AnalysisResult, AnalysisSummary, DependencyChange, DependencyRecord

__all__ = [
    "AnalysisResult",
    "AnalysisSummary",
    "DependencyChange",
    "DependencyRecord",
    "analyze",
]

__version__ = "0.2.0"
