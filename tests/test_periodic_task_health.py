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


async def _run_ticks(
    factory, *, name: str, ticks: int, critical: bool = True
) -> None:
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

    task = asyncio.create_task(
        runner.run_periodic(counted, 0.001, name=name, critical=critical)
    )
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
    """UNHEALTHY_AFTER est desormais un plancher, pas le seuil applique
    partout: threshold_for_interval() peut le depasser, jamais descendre en
    dessous. Trois reste la valeur pour un poll de 5 min (voir
    test_threshold_reproduces_the_five_minute_calibration)."""
    assert runner.UNHEALTHY_AFTER == 3


def test_threshold_reproduces_the_five_minute_calibration() -> None:
    """binance-futures-poll (5 min) est le calibrage de reference: 3 echecs
    doivent continuer a valoir 15 minutes, inchange par la derivation."""
    assert runner.threshold_for_interval(5 * 60) == 3


def test_threshold_tightens_a_fast_task() -> None:
    """Un seuil fixe de 3 laisserait sentiment-worker (10 s) alerter apres
    seulement 30 s -- trop court pour absorber un rate-limit transitoire.
    La derivation par cadence exige davantage d'echecs consecutifs pour une
    tache aussi rapide."""
    assert runner.threshold_for_interval(10) > runner.UNHEALTHY_AFTER


def test_threshold_never_drops_below_the_floor_for_a_slow_task() -> None:
    """github-lists tourne une fois par semaine: FENETRE/interval est bien en
    dessous de 1, mais le seuil ne doit jamais descendre sous le plancher --
    sinon un seul echec suffirait a declarer la tache en panne."""
    weekly_seconds = 7 * 24 * 3600
    assert runner.threshold_for_interval(weekly_seconds) == runner.UNHEALTHY_AFTER


def test_a_fast_task_alerts_sooner_in_wall_clock_time_than_a_slow_task() -> None:
    """A nombre d'echecs egal (chacun au plancher, puisque sentiment-compaction
    et binance-futures-poll y sont tous deux ramenes), la tache la plus
    rapide atteint ce compte plus tot en temps reel: l'alerte reste bornee
    par la cadence, pas par un compte absolu de ticks."""
    fast_interval = 5 * 60  # binance-futures-poll
    slow_interval = 6 * 3600  # sentiment-compaction
    fast_threshold = runner.threshold_for_interval(fast_interval)
    slow_threshold = runner.threshold_for_interval(slow_interval)
    assert fast_threshold == slow_threshold == runner.UNHEALTHY_AFTER
    assert fast_threshold * fast_interval < slow_threshold * slow_interval


def test_a_non_critical_task_past_its_threshold_is_counted_but_not_failing() -> None:
    """sentiment-compaction et github-lists passent critical=False: une panne
    y merite une metrique (consecutive_failures continue de monter), pas un
    conteneur declare mort alors que ses taches critiques tournent bien."""
    runner.TASK_HEALTH["sentiment-compaction"] = runner.TaskState(
        name="sentiment-compaction",
        consecutive_failures=runner.UNHEALTHY_AFTER + 5,
        critical=False,
    )
    assert "sentiment-compaction" not in runner.failing_tasks()
    assert (
        runner.TASK_HEALTH["sentiment-compaction"].consecutive_failures
        == runner.UNHEALTHY_AFTER + 5
    )


def test_a_critical_task_past_its_threshold_still_fails() -> None:
    """Le defaut de critical=True ne doit pas regresser: une tache critique
    en panne continue de faire basculer /health."""
    runner.TASK_HEALTH["binance-futures-poll"] = runner.TaskState(
        name="binance-futures-poll", consecutive_failures=runner.UNHEALTHY_AFTER
    )
    assert "binance-futures-poll" in runner.failing_tasks()


async def test_run_periodic_wires_critical_false_into_the_registry() -> None:
    """run_periodic doit propager critical=False jusqu'au TaskState, pas
    seulement l'accepter en parametre sans effet."""

    async def boom() -> None:
        raise RuntimeError("maintenance down")

    await _run_ticks(boom, name="maintenance-poll", ticks=3, critical=False)

    state = runner.TASK_HEALTH["maintenance-poll"]
    assert state.critical is False
    assert state.consecutive_failures >= 3
    assert "maintenance-poll" not in runner.failing_tasks()
