"""La liste d'axes existe en trois copies independantes, et rien ne les liait.

`decision-engine/app/scoring.py::WEIGHTS`, `api-gateway/app/dossier.py::AXIS_KEYS`
et `frontend/src/lib/types/dossier.ts::SCORE_AXES`. Aucune n'importe les autres
-- api-gateway ne doit pas dependre de decision-engine -- donc un huitieme axe
oublie dans l'une des trois serait simplement invisible dans le drawer /market,
sans erreur ni test rouge.

Les trois fichiers sont lus par analyse syntaxique plutot que par import: cela
evite de dependre d'un chemin de package, et surtout cela fonctionne pour le
fichier TypeScript, qui n'est importable par aucun test Python.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCORING = ROOT / "services/decision-engine/app/scoring.py"
DOSSIER_PY = ROOT / "services/api-gateway/app/dossier.py"
DOSSIER_TS = ROOT / "frontend/src/lib/types/dossier.ts"


def _assigned(path: Path, name: str) -> ast.expr:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node.value
        raise_if_last = None  # noqa: F841 — lisibilite
    raise AssertionError(f"{name} introuvable dans {path}")


def _weights_keys() -> list[str]:
    node = _assigned(SCORING, "WEIGHTS")
    return [k.value for k in node.keys if isinstance(k, ast.Constant)]


def _axis_keys() -> list[str]:
    node = _assigned(DOSSIER_PY, "AXIS_KEYS")
    return [e.value for e in node.elts if isinstance(e, ast.Constant)]


def _score_axes() -> list[str]:
    text = DOSSIER_TS.read_text(encoding="utf-8")
    block = re.search(r"export const SCORE_AXES\s*=\s*\[(.*?)\]", text, re.DOTALL)
    assert block, "SCORE_AXES introuvable"
    return re.findall(r"'([^']+)'", block.group(1))


def _axis_labels() -> set[str]:
    text = DOSSIER_TS.read_text(encoding="utf-8")
    block = re.search(r"export const AXIS_LABELS[^{]*\{(.*?)\}", text, re.DOTALL)
    assert block, "AXIS_LABELS introuvable"
    return set(re.findall(r"^\s*(\w+)\s*:", block.group(1), re.MULTILINE))


def test_the_three_copies_agree():
    weights, axes, ts = set(_weights_keys()), set(_axis_keys()), set(_score_axes())
    assert weights == axes, f"scoring.py vs dossier.py: {weights ^ axes}"
    assert weights == ts, f"scoring.py vs dossier.ts: {weights ^ ts}"


def test_display_order_is_identical_between_backend_and_frontend():
    """Le drawer affiche les axes dans l'ordre de la liste: deux ordres
    divergents donneraient deux lectures du meme score.

    WEIGHTS n'entre pas dans cette comparaison -- un dictionnaire de poids n'a
    pas vocation a porter un ordre d'affichage.
    """
    assert _axis_keys() == _score_axes()


def test_every_axis_has_a_label():
    """Un axe sans libelle s'afficherait sous sa cle technique."""
    assert set(_score_axes()) <= _axis_labels()


def test_no_label_survives_its_axis():
    """L'inverse compte aussi: un libelle orphelin signale un axe retire
    d'une seule des trois copies."""
    assert _axis_labels() <= set(_score_axes())


def test_developer_activity_is_present_everywhere():
    assert "developer_activity" in _weights_keys()
    assert "developer_activity" in _axis_keys()
    assert "developer_activity" in _score_axes()


def test_no_axis_is_listed_twice():
    """Un doublon fausserait axes_total et la renormalisation."""
    for name, keys in (
        ("WEIGHTS", _weights_keys()),
        ("AXIS_KEYS", _axis_keys()),
        ("SCORE_AXES", _score_axes()),
    ):
        assert len(keys) == len(set(keys)), name
