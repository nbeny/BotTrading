"""Le cablage de deploiement, verifie hors ligne.

Deux ecarts possibles entre ce depot et ce qui tourne reellement, tous deux
silencieux jusqu'au deploiement:

* une image referencee par docker-compose.vps.yml que la CI ne construit
  jamais -- le build passe au vert et le `docker compose pull` echoue sur le
  VPS;
* un fichier de test absent de la liste blanche du job CI -- il est vert en
  local et ne s'execute jamais a l'integration.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/deploy.yml"
VPS_COMPOSE = ROOT / "docker-compose.vps.yml"

#: Les fichiers de ce chantier. La liste blanche du job CI en compte ~25 sur
#: les 130 du depot, ce qui est un probleme transverse: on ne le corrige pas
#: ici, mais on garantit au moins que ce qu'on vient d'ecrire tourne.
GITHUB_INGESTION_TESTS = (
    "tests/test_developer_event.py",
    "tests/test_github_activity.py",
    "tests/test_github_aggregate.py",
    "tests/test_github_lists.py",
    "tests/test_github_client.py",
    "tests/test_github_models.py",
    "tests/test_github_repo_map.py",
    "tests/test_github_collector.py",
    "tests/test_github_store.py",
    "tests/test_github_registry.py",
    "tests/test_haiku_developer_features.py",
    "tests/test_scoring_developer_axis.py",
    "tests/test_axis_parity.py",
)


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_every_vps_image_is_built_by_ci():
    """Une image jamais construite fait echouer le pull apres un build vert."""
    built = {
        entry["name"]
        for entry in _workflow()["jobs"]["images"]["strategy"]["matrix"]["include"]
    }
    services = yaml.safe_load(VPS_COMPOSE.read_text(encoding="utf-8"))["services"]
    referenced = {
        match.group(1)
        for svc in services.values()
        if (match := re.search(r"bottrading-([a-z0-9-]+):", str(svc.get("image", ""))))
    }
    assert referenced <= built, f"images non construites: {referenced - built}"


def test_collector_github_is_built():
    built = {
        entry["name"]
        for entry in _workflow()["jobs"]["images"]["strategy"]["matrix"]["include"]
    }
    assert "collector-github" in built


def test_this_features_tests_actually_run_in_ci():
    """Le job `test` enumere des chemins a la main plutot que d'appeler
    pytest tests/. Tout fichier oublie est vert en local et jamais execute."""
    steps = _workflow()["jobs"]["test"]["steps"]
    script = "\n".join(str(step.get("run", "")) for step in steps)
    missing = [path for path in GITHUB_INGESTION_TESTS if path not in script]
    assert not missing, f"absents de la liste blanche CI: {missing}"


def test_the_github_service_is_declared_in_both_compose_files():
    for path in (ROOT / "docker-compose.yml", VPS_COMPOSE):
        services = yaml.safe_load(path.read_text(encoding="utf-8"))["services"]
        assert "collector-github" in services, path.name


def test_the_token_is_never_committed_with_a_value():
    """.env.example doit porter la variable, jamais un secret."""
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("GITHUB_TOKEN"):
            assert line.strip() == "GITHUB_TOKEN=", line
            return
    raise AssertionError("GITHUB_TOKEN absent de .env.example")
