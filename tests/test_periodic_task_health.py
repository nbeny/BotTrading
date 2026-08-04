"""run_periodic tient un registre d'echecs consecutifs.

Sans lui, une tache qui rate 100% de ses cycles est indiscernable d'une tache
saine: l'exception est journalisee puis avalee, et /health repond 200. C'est
la mecanique qui a laisse collector-binance-futures se declarer healthy
pendant 28 heures sans produire une seule lecture.
"""

from __future__ import annotations

import asyncio

import pytest

from cmi_common import runner


@pytest.fixture(autouse=True)
def _clean_registry():
    runner.TASK_HEALTH.clear()
    yield
    runner.TASK_HEALTH.clear()


async def _run_ticks(factory, *, name: str, ticks: int) -> None:
    """Laisse run_periodic executer au moins `ticks` fois, puis l'annule."""
    calls = 0
    done = asyncio.Event()

    async def counted() -> None:
        nonlocal calls
        calls += 1
        try:
            await factory()
        finally:
            if calls >= ticks:
                done.set()

    task = asyncio.create_task(runner.run_periodic(counted, 0.001, name=name))
    await asyncio.wait_for(done.wait(), timeout=2.0)
    # Un dernier passage de boucle pour que l'issue du tick soit enregistree:
    # done.set() se declenche dans le corps du tick, avant que run_periodic
    # n'ait note le resultat.
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_a_failing_task_accumulates_consecutive_failures() -> None:
    async def boom() -> None:
        raise RuntimeError("upstream down")

    await _run_ticks(boom, name="boom-poll", ticks=3)

    state = runner.TASK_HEALTH["boom-poll"]
    assert state.consecutive_failures >= 3
    assert "upstream down" in state.last_error
    assert state.last_success is None


async def test_a_success_resets_the_counter() -> None:
    outcomes = [RuntimeError("x"), RuntimeError("x")]

    async def flaky() -> None:
        if outcomes:
            raise outcomes.pop(0)

    await _run_ticks(flaky, name="flaky-poll", ticks=3)

    state = runner.TASK_HEALTH["flaky-poll"]
    assert state.consecutive_failures == 0
    assert state.last_success is not None


def test_failing_tasks_reports_only_those_past_the_threshold() -> None:
    """Teste _record directement plutot que la boucle: avec un intervalle de
    1 ms, le nombre exact de ticks executes avant l'annulation n'est pas
    controlable, et un test de seuil qui ne controle pas son compte ne teste
    pas un seuil."""
    for _ in range(runner.UNHEALTHY_AFTER - 1):
        runner._record("two-fails", error=RuntimeError("nope"))
    assert runner.failing_tasks() == {}

    runner._record("two-fails", error=RuntimeError("nope"))
    assert "two-fails" in runner.failing_tasks()


def test_one_success_clears_a_task_past_the_threshold() -> None:
    for _ in range(runner.UNHEALTHY_AFTER + 2):
        runner._record("recovering", error=RuntimeError("nope"))
    assert "recovering" in runner.failing_tasks()

    runner._record("recovering", error=None)
    assert runner.failing_tasks() == {}


def test_the_threshold_is_three() -> None:
    """Sur un cycle de 5 min, trois echecs valent 15 minutes de panne avant
    l'alerte: un rate-limit transitoire ne fait pas clignoter, et on ne
    reproduit pas les 28 heures."""
    assert runner.UNHEALTHY_AFTER == 3
