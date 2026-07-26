"""Curseur de pagination composite.

Un curseur sur le seul horodatage saute ou répète des lignes dès que deux
événements partagent la même milliseconde — ce qui arrive constamment : 120
analyses par heure pour un seul symbole ont été mesurées en production.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from service_modules import load_service_module

cur = load_service_module("api-gateway", "events_cursor")

T = datetime(2026, 7, 26, 12, 0, 0, 123456, tzinfo=UTC)


def test_round_trips() -> None:
    encoded = cur.encode(T, "evt-1")
    assert cur.decode(encoded) == (T, "evt-1")


def test_microseconds_survive() -> None:
    """Tronquer à la seconde ferait sauter des lignes entre deux pages."""
    a, b = cur.encode(T, "x"), cur.encode(T.replace(microsecond=123457), "x")
    assert a != b


def test_an_event_id_containing_the_separator_still_round_trips() -> None:
    """Le séparateur ne doit pas dépendre de l'absence de ce caractère dans les
    identifiants — sinon un id inhabituel corrompt la pagination en silence."""
    encoded = cur.encode(T, "evt_with_underscores_1")
    assert cur.decode(encoded) == (T, "evt_with_underscores_1")


def test_a_malformed_cursor_is_rejected_not_guessed() -> None:
    """Mieux vaut une erreur qu'une page arbitraire."""
    for bad in ("", "pasdedate", "2026-07-26T12:00:00", "___"):
        with pytest.raises(ValueError):
            cur.decode(bad)


def test_decoded_time_is_timezone_aware() -> None:
    """Comparé à une colonne timestamptz : un datetime naïf ici serait une
    source de décalage silencieux."""
    assert cur.decode(cur.encode(T, "e"))[0].tzinfo is not None
