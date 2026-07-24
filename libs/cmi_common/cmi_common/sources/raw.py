"""RawItem — the normalized unit every provider yields.

One social post or one news article. Persisted verbatim to ``raw_content`` and
later scored by the sentiment worker. Deliberately provider-agnostic: each
provider maps its API payload onto these fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RawItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    kind: Literal["social", "news"]
    # Provider-stable id used for cross-restart dedup via (source, external_id).
    external_id: str
    text: str = ""
    title: str | None = None
    url: str | None = None
    author: str | None = None
    symbols: list[str] = Field(default_factory=list)
    engagement: float | None = None
    lang: str | None = None
    published_at: datetime | None = None
