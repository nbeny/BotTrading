"""Un service dont une tache periodique est en panne cesse de se dire sain.

Le HEALTHCHECK des Dockerfile utilise `curl -fsS`, qui echoue sur tout code
>= 400: un 503 bascule le conteneur en `unhealthy` sans le redemarrer, puisque
`restart: unless-stopped` ne redemarre pas sur ce motif.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cmi_common import create_app, runner


@pytest.fixture(autouse=True)
def _clean_registry():
    runner.TASK_HEALTH.clear()
    yield
    runner.TASK_HEALTH.clear()


def test_health_is_ok_with_no_periodic_task() -> None:
    with TestClient(create_app("test-svc")) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_is_ok_below_the_threshold() -> None:
    runner.TASK_HEALTH["poll"] = runner.TaskState(
        name="poll", consecutive_failures=runner.UNHEALTHY_AFTER - 1
    )
    with TestClient(create_app("test-svc")) as client:
        response = client.get("/health")
    assert response.status_code == 200


def test_health_degrades_once_a_task_passes_the_threshold() -> None:
    runner.TASK_HEALTH["binance-futures-poll"] = runner.TaskState(
        name="binance-futures-poll",
        consecutive_failures=runner.UNHEALTHY_AFTER,
        last_error="DataError: invalid input for query argument $1",
    )
    with TestClient(create_app("test-svc")) as client:
        response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    failing = body["failing_tasks"]["binance-futures-poll"]
    assert failing["consecutive_failures"] == runner.UNHEALTHY_AFTER
    assert "DataError" in failing["last_error"]


def test_the_failing_task_name_reaches_the_body() -> None:
    """Un 503 sans le nom de la tache oblige a ouvrir les logs pour savoir
    laquelle -- ce qui est precisement l'etape que ce changement supprime."""
    runner.TASK_HEALTH["a"] = runner.TaskState(name="a", consecutive_failures=9)
    runner.TASK_HEALTH["b"] = runner.TaskState(name="b", consecutive_failures=0)
    with TestClient(create_app("test-svc")) as client:
        body = client.get("/health").json()
    assert set(body["failing_tasks"]) == {"a"}
