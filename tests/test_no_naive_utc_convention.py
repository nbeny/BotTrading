"""Plus aucun site ne depouille le fuseau avant de toucher la base.

La convention « naive UTC » etait une consequence de declarations ORM qui
affirmaient TIMESTAMP la ou la colonne est timestamptz. Une fois les
declarations corrigees, elle devient nuisible: asyncpg encode un datetime
naif pour une colonne timestamptz en l'interpretant dans le fuseau *local du
conteneur*. La convention troquerait donc une erreur bruyante contre un
decalage silencieux.

Le test est syntaxique plutot que comportemental parce que le comportement
qu'il protege ne se manifeste que contre un vrai Postgres dans un fuseau non
UTC -- conditions qu'aucun test de cette suite ne reunit.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "services/api-gateway/app"
FILES = ["persister.py", "archiver.py", "read_api.py", "systems_pipeline.py"]


def test_no_module_strips_tzinfo_before_a_query() -> None:
    offenders = [
        f"{name}:{n}"
        for name in FILES
        for n, line in enumerate(
            (GATEWAY / name).read_text(encoding="utf-8").splitlines(), 1
        )
        if "replace(tzinfo=None)" in line
    ]
    assert not offenders, (
        f"depouillage du fuseau encore present: {offenders}. Les colonnes sont "
        "timestamptz et les modeles le declarent desormais; passer un datetime "
        "naif fait interpreter la valeur dans le fuseau local du conteneur."
    )


def test_the_helper_itself_is_gone() -> None:
    for name in ("persister.py", "archiver.py"):
        assert "_naive_utc" not in (GATEWAY / name).read_text(encoding="utf-8")
