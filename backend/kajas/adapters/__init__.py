"""Tool adapters.

An adapter is a small object that knows how to start a subprocess (or,
in the case of ``fake``, simulate one), stream its output as a sequence
of normalized events, and stop it. The orchestration code in
``kajas.runs`` knows nothing about Codex or Pi specifically - it just
calls ``start`` and consumes ``NormalizedEvent`` objects off the
returned ``AdapterProcess``.
"""

from __future__ import annotations

from .base import (
    Adapter,
    AdapterProcess,
    Capabilities,
    HealthResult,
    NormalizedEvent,
    Stage,
    load_registry,
)
from .fake import FakeAdapter
from .codex import CodexAdapter
from .pi import PiAdapter

__all__ = [
    "Adapter",
    "AdapterProcess",
    "Capabilities",
    "CodexAdapter",
    "FakeAdapter",
    "HealthResult",
    "NormalizedEvent",
    "PiAdapter",
    "Stage",
    "load_registry",
]
