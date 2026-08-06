"""Hades standalone-plugin entry point for Sedna."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sedna.plugin import register

__all__ = ["register"]
