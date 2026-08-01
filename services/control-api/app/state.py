# services/control-api/app/state.py
"""Re-export the shared StateReader (moved to cmi_common.state)."""

from __future__ import annotations

from cmi_common.state import (  # noqa: F401
    PENDING_SET,
    POSITIONS_SET,
    RUNTIME_KEY,
    StateReader,
)
