from __future__ import annotations

__all__ = ["CaddyFullnessEstimator"]


def __getattr__(name: str):
    if name == "CaddyFullnessEstimator":
        from .estimator import CaddyFullnessEstimator

        return CaddyFullnessEstimator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
