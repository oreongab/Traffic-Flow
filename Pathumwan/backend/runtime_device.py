"""
Runtime compute-device detection for PyTorch-backed workloads.

The backend uses this in two places:
- Stable-Baselines3 RL models
- YOLO/Ultralytics inference

Keep detection centralized so the terminal shows one clear answer at startup
and every model uses the same device preference.
"""

from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def get_torch_device() -> tuple[str, bool, str]:
    """Return ``(device, use_half, reason)`` for torch workloads.

    Device preference is CUDA -> MPS -> CPU. Set ``AI_DEVICE`` or
    ``TORCH_DEVICE`` to ``cpu``, ``cuda``, ``cuda:0``, or ``mps`` to override.
    FP16 is enabled only for CUDA inference/training paths that support it.
    """
    requested = (os.getenv("AI_DEVICE") or os.getenv("TORCH_DEVICE") or "auto").strip().lower()

    try:
        import torch  # type: ignore[import-not-found]
    except Exception as exc:
        return "cpu", False, f"PyTorch import failed: {exc}"

    def cuda_reason() -> str:
        try:
            name = torch.cuda.get_device_name(0)
            count = torch.cuda.device_count()
            return f"CUDA available: {name} ({count} device{'s' if count != 1 else ''})"
        except Exception:
            return "CUDA available"

    if requested not in {"", "auto"}:
        if requested.startswith("cuda"):
            try:
                if torch.cuda.is_available():
                    return requested, True, f"Forced {requested}; {cuda_reason()}"
                return "cpu", False, f"Requested {requested}, but CUDA is not available"
            except Exception as exc:
                return "cpu", False, f"Requested {requested}, but CUDA check failed: {exc}"
        if requested == "mps":
            try:
                if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
                    return "mps", False, "Forced MPS"
                return "cpu", False, "Requested MPS, but MPS is not available"
            except Exception as exc:
                return "cpu", False, f"Requested MPS, but MPS check failed: {exc}"
        if requested == "cpu":
            return "cpu", False, "Forced CPU by AI_DEVICE/TORCH_DEVICE"
        return "cpu", False, f"Unknown AI_DEVICE/TORCH_DEVICE={requested!r}; using CPU"

    try:
        if torch.cuda.is_available():
            return "cuda", True, cuda_reason()
    except Exception as exc:
        return "cpu", False, f"CUDA check failed: {exc}"

    try:
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return "mps", False, "MPS available"
    except Exception:
        pass

    return "cpu", False, "No CUDA/MPS GPU detected"


def print_device_banner(prefix: str = "AI") -> None:
    """Print a one-line device status for terminal visibility."""
    device, use_half, reason = get_torch_device()
    print(f"🧠 {prefix} compute device: {device} (half={use_half}) — {reason}")
