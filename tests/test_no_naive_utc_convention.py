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

Le balayage porte sur tout `services/api-gateway/app/*.py` plutot que sur une
liste de fichiers nommes en dur. Une liste nommee ne protege que les fichiers
auxquels on a deja pense -- c'est exactement ce qui a laisse passer un
sixieme site (`events_api.py::assume_utc`) lors de la premiere version de ce
test, qui n'en listait que quatre.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "services/api-gateway/app"


def _module_files() -> list[Path]:
    return sorted(p for p in GATEWAY.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_module_strips_tzinfo_before_a_query() -> None:
    offenders = [
        f"{p.relative_to(GATEWAY)}:{n}"
        for p in _module_files()
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "replace(tzinfo=None)" in line
    ]
    assert not offenders, (
        f"depouillage du fuseau encore present: {offenders}. Les colonnes sont "
        "timestamptz et les modeles le declarent desormais; passer un datetime "
        "naif fait interpreter la valeur dans le fuseau local du conteneur."
    )


def test_the_helper_itself_is_gone() -> None:
    offenders = [
        str(p.relative_to(GATEWAY))
        for p in _module_files()
        if "_naive_utc" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"reference residuelle a une fonction supprimee: {offenders}. Un nom de "
        "fonction disparue qui subsiste dans un commentaire ou une docstring est "
        "une piste morte pour le prochain lecteur."
    )
