"""Parsing des deux listes de référence. Pur, sans I/O.

Les deux README ne publient pas la même chose, et le registre le reflète tel
quel : ``best-of-crypto`` n'expose qu'un lien GitHub, ``awesome-crypto`` publie
en plus l'URL du site officiel sur une ligne dédiée. Déduire une homepage depuis
un lien GitHub (``owner.github.io``) fabriquerait une URL que personne n'a
publiée, donc ``homepage_url`` reste ``None`` pour toute la première liste.

Le parsing est tolérant mais jamais devinant : une entrée dont le lien ne se
laisse pas lire est comptée et ignorée.

**Un numérateur ne suffit pas à voir une dérive de format.** ``unparsed`` compte
les entrées reconnues puis illisibles ; il est structurellement aveugle au cas
où plus rien n'est reconnu. Si ``best-of`` cesse d'émettre ``<details>`` ou si
``awesome`` passe de ``###`` à ``##``, aucun bloc n'est vu : ``entries`` est
vide et ``unparsed`` vaut zéro — un registre tombé de 8 400 lignes à rien, avec
une métrique impeccable. ``ParseResult`` porte donc aussi le **dénominateur**
(``blocks_seen``) et les absences par champ, pour que l'appelant puisse alarmer
sur un ratio plutôt que sur un compte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: ``https://github.com/<owner>/<repo>``, sans capturer les sous-chemins. Les
#: blocs ``best-of`` contiennent aussi des liens ``/blob/…`` et ``/issues``, et
#: une alternance trop laxiste en ferait des dépôts fantômes que personne ne
#: pourrait interroger. Le lookahead sert deux choses distinctes : permettre le
#: retrait du ``.git`` final, et refuser les URL suffixées d'un ``?query``.
_REPO_URL = re.compile(
    r"https?://github\.com/([A-Za-z0-9][\w.-]*)/([\w.-]+?)(?:\.git)?(?=[/\s)\"'#?]|$)"
)
#: Chemins réservés de github.com : ce ne sont pas des dépôts, et les laisser
#: entrer produirait des 404 permanents dans la boucle de sondage plutôt que des
#: absences comptées.
_RESERVED_OWNERS = frozenset(
    {"sponsors", "orgs", "topics", "collections", "features", "settings", "apps"}
)
#: ``### [nom](url)`` en tête d'entrée pour awesome-crypto. Le niveau de titre
#: est fixé à trois : l'assouplir en ``#+`` ferait avaler les titres de section
#: comme des projets, et masquerait précisément le changement de gabarit qu'on
#: veut voir.
_AWESOME_HEAD = re.compile(r"^###\s+\[([^\]]+)\]\((https?://[^)]+)\)", re.MULTILINE)
#: ``[libellé](url)`` sur une ligne isolée. Le libellé est volontairement libre :
#: l'exiger sous forme d'URL rendait le parseur muet — et silencieusement — dès
#: que le générateur écrivait ``[bitcoincore.org](https://…)``.
_BARE_LINK = re.compile(r"^\s*\[[^\]]+\]\((https?://[^)]+)\)\s*$")
#: ``<a href="…">nom</a>``, cherché **dans le ``<summary>`` seul**.
_SUMMARY = re.compile(
    r"<a href=\"(https?://github\.com/[^\"]+)\"[^>]*>\s*([^<]+?)\s*</a>",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class ListEntry:
    name: str
    owner: str
    repo: str
    source_list: str
    homepage_url: str | None = None
    description: str | None = None

    @property
    def github_url(self) -> str:
        """URL du dépôt, casse d'origine préservée.

        Ce n'est **pas** la clé de déduplication : GitHub résout ``owner/repo``
        sans tenir compte de la casse, donc ``Bitcoin/Bitcoin`` et
        ``bitcoin/bitcoin`` sont un seul dépôt. Voir :attr:`dedup_key`.
        """
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def dedup_key(self) -> tuple[str, str]:
        """Clé d'identité, insensible à la casse.

        Deux lignes de registre pour un même dépôt coûteraient deux créneaux du
        round-robin — lequel est borné par le quota et balaie l'univers en 12 h —
        et gonfleraient ``repo_count`` dans l'agrégat.
        """
        return self.owner.casefold(), self.repo.casefold()


@dataclass(frozen=True, slots=True)
class ParseResult:
    entries: list[ListEntry]
    #: Blocs reconnus comme des entrées mais dont le lien n'a pas pu être lu.
    unparsed: int
    #: Blocs reconnus, lisibles ou non — le dénominateur. Sans lui, « la liste a
    #: rétréci » et « notre parseur ne la comprend plus » sont indiscernables.
    blocks_seen: int
    #: Entrées retenues dont le champ est resté absent. Une homepage est la
    #: moitié du livrable de la tâche 14 : si le gabarit change et qu'elles
    #: passent toutes à ``None``, seul ce compteur le dira.
    homepage_missing: int = 0
    description_missing: int = 0


def parse_best_of_crypto(markdown: str) -> ParseResult:
    """Une entrée par bloc ``<details>``, le lien étant celui du ``<summary>``.

    La recherche est **bornée au ``<summary>``**, pas au bloc entier. Un titre
    non lié dont le corps contient un ``<a href>`` GitHub produisait sinon un
    dépôt confiant et faux — le seul chemin du module capable de fabriquer une
    valeur plutôt que d'en perdre une, et sans faire bouger ``unparsed``.
    """
    entries: list[ListEntry] = []
    unparsed = 0
    blocks = 0
    seen: set[tuple[str, str]] = set()
    for block in markdown.split("<details>"):
        if "<summary>" not in block:
            continue
        blocks += 1
        summary = block.split("</summary>", 1)[0]
        match = _SUMMARY.search(summary)
        if match is None:
            unparsed += 1
            continue
        pair = _split_repo(match.group(1))
        if pair is None:
            unparsed += 1
            continue
        entry = ListEntry(
            name=match.group(2),
            owner=pair[0],
            repo=pair[1],
            homepage_url=None,  # la liste n'en publie pas
            source_list="best-of-crypto",
        )
        if entry.dedup_key in seen:
            continue
        seen.add(entry.dedup_key)
        entries.append(entry)
    return ParseResult(
        entries=entries,
        unparsed=unparsed,
        blocks_seen=blocks,
        homepage_missing=len(entries),  # aucune n'est publiée par cette liste
        description_missing=sum(1 for e in entries if e.description is None),
    )


def parse_awesome_crypto(markdown: str) -> ParseResult:
    """Une entrée par titre ``###``.

    La homepage est la première ligne-lien isolée qui ne pointe pas vers
    GitHub. Chercher « la ligne qui n'est pas GitHub » plutôt que compter les
    lignes est délibéré : la ligne site est absente pour une partie des projets,
    et un parseur positionnel y recopierait l'URL du dépôt comme site officiel.
    """
    entries: list[ListEntry] = []
    unparsed = 0
    seen: set[tuple[str, str]] = set()
    heads = list(_AWESOME_HEAD.finditer(markdown))
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(markdown)
        # Le corps commence à la ligne *suivante*. Découper à `head.end()`
        # laissait la fin de la ligne de titre dans le corps, si bien qu'un
        # suffixe « by [owner](…) » — une forme réelle de ces listes — devenait
        # la description du projet.
        newline = markdown.find("\n", head.end())
        body_start = end if newline == -1 else min(newline + 1, end)
        body = markdown[body_start:end]
        pair = _split_repo(head.group(2))
        if pair is None:
            unparsed += 1
            continue
        homepage, description = _read_body(body)
        entry = ListEntry(
            name=head.group(1).strip(),
            owner=pair[0],
            repo=pair[1],
            homepage_url=homepage,
            description=description,
            source_list="awesome-crypto",
        )
        if entry.dedup_key in seen:
            continue
        seen.add(entry.dedup_key)
        entries.append(entry)
    return ParseResult(
        entries=entries,
        unparsed=unparsed,
        blocks_seen=len(heads),
        homepage_missing=sum(1 for e in entries if e.homepage_url is None),
        description_missing=sum(1 for e in entries if e.description is None),
    )


def _read_body(body: str) -> tuple[str | None, str | None]:
    """``(homepage, description)`` d'un corps d'entrée awesome."""
    homepage: str | None = None
    description: str | None = None
    seen_link = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        link = _BARE_LINK.match(line)
        if link is not None:
            seen_link = True
            if homepage is None and "github.com" not in link.group(1):
                homepage = link.group(1)
            continue
        # La description précède toujours les liens dans ce format. Après le
        # premier lien ne viennent que des métriques (« 5 stars per week over
        # 10 weeks ») et la ligne de tags — des compteurs et du balisage.
        #
        # Sans cette borne, un projet dépourvu de description héritait de sa
        # ligne de métriques : le registre affichait « 5 stars per week over
        # 10 weeks » comme description du projet.
        if seen_link:
            continue
        if description is None:
            description = stripped
    return homepage, description


def _split_repo(url: str) -> tuple[str, str] | None:
    """``(owner, repo)`` d'une URL GitHub, ou ``None`` si ce n'en est pas une."""
    match = _REPO_URL.match(url)
    if match is None:
        return None
    owner, repo = match.group(1), match.group(2)
    if owner.casefold() in _RESERVED_OWNERS:
        return None
    # `[\w.-]` avale la ponctuation de fin de phrase ; un dépôt nommé « bar. »
    # serait un identifiant fabriqué.
    repo = repo.rstrip(".")
    return (owner, repo) if repo else None
