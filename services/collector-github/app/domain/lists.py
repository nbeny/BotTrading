"""Parsing des deux listes de référence. Pur, sans I/O.

Les deux README ne publient pas la même chose, et le registre le reflète tel
quel : ``best-of-crypto`` n'expose qu'un lien GitHub, ``awesome-crypto`` publie
en plus l'URL du site officiel sur une ligne dédiée. Déduire une homepage depuis
un lien GitHub (``owner.github.io``) fabriquerait une URL que personne n'a
publiée, donc ``homepage_url`` reste ``None`` pour toute la première liste.

Le parsing est tolérant mais jamais devinant : une entrée dont le lien ne se
laisse pas lire est comptée et ignorée. Le compteur remonte en métrique, parce
qu'un changement de format doit se voir plutôt que faire rétrécir le registre
sans bruit — un registre qui maigrit de 3 000 lignes ressemble exactement à un
registre correctement filtré.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: ``https://github.com/<owner>/<repo>``, sans capturer les sous-chemins. Les
#: blocs ``best-of`` contiennent aussi des liens ``/blob/…`` et ``/issues``, et
#: une alternance trop laxiste en ferait des dépôts fantômes que personne ne
#: pourrait interroger.
_REPO_URL = re.compile(
    r"https?://github\.com/([A-Za-z0-9][\w.-]*)/([\w.-]+?)(?:\.git)?(?=[/\s)\"'#]|$)"
)
#: ``### [nom](url)`` en tête d'entrée pour awesome-crypto.
_AWESOME_HEAD = re.compile(r"^###\s+\[([^\]]+)\]\((https?://[^)]+)\)", re.MULTILINE)
#: ``[url](url)`` sur une ligne isolée — la forme des lignes site et dépôt.
_BARE_LINK = re.compile(r"^\s*\[(https?://[^\]]+)\]\((https?://[^)]+)\)\s*$")
#: ``<a href="…">nom</a>`` dans un ``<summary>`` best-of.
_SUMMARY = re.compile(
    r"<summary>.*?<a href=\"(https?://github\.com/[^\"]+)\"[^>]*>\s*([^<]+?)\s*</a>",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class ListEntry:
    name: str
    owner: str
    repo: str
    homepage_url: str | None = None
    description: str | None = None
    source_list: str = ""

    @property
    def github_url(self) -> str:
        """Forme canonique, reconstruite plutôt que recopiée.

        C'est la clé de déduplication du registre : les deux listes citent le
        même dépôt sous des formes qui diffèrent par un ``.git`` final ou un
        sous-chemin, et deux graphies produiraient deux lignes pour un dépôt.
        """
        return f"https://github.com/{self.owner}/{self.repo}"


@dataclass(frozen=True, slots=True)
class ParseResult:
    entries: list[ListEntry]
    #: Blocs reconnus comme des entrées mais dont le lien n'a pas pu être lu.
    #: Exposé plutôt que journalisé : c'est la seule mesure qui distingue « la
    #: liste a rétréci » de « notre parseur ne la comprend plus ».
    unparsed: int


def parse_best_of_crypto(markdown: str) -> list[ListEntry]:
    return parse_best_of_crypto_full(markdown).entries


def parse_best_of_crypto_full(markdown: str) -> ParseResult:
    """Une entrée par bloc ``<details>``, le lien étant celui du ``<summary>``.

    Le corps du bloc répète la même URL sous ``- [GitHub](…)`` ; ne lire que le
    ``<summary>`` évite de compter chaque projet deux fois. La déduplication
    sur ``(owner, repo)`` reste nécessaire malgré cela : la liste range certains
    projets dans deux catégories.
    """
    entries: list[ListEntry] = []
    unparsed = 0
    seen: set[tuple[str, str]] = set()
    for block in markdown.split("<details>"):
        if "<summary>" not in block:
            continue
        match = _SUMMARY.search(block)
        if match is None:
            unparsed += 1
            continue
        repo_match = _REPO_URL.match(match.group(1))
        if repo_match is None:
            unparsed += 1
            continue
        owner, repo = repo_match.group(1), repo_match.group(2)
        if (owner, repo) in seen:
            continue
        seen.add((owner, repo))
        entries.append(
            ListEntry(
                name=match.group(2).strip(),
                owner=owner,
                repo=repo,
                homepage_url=None,  # la liste n'en publie pas
                source_list="best-of-crypto",
            )
        )
    return ParseResult(entries=entries, unparsed=unparsed)


def parse_awesome_crypto(markdown: str) -> list[ListEntry]:
    return parse_awesome_crypto_full(markdown).entries


def parse_awesome_crypto_full(markdown: str) -> ParseResult:
    """Une entrée par titre ``###``.

    La homepage est la première ligne-lien isolée qui ne pointe pas vers
    GitHub. Chercher « la ligne qui n'est pas GitHub » plutôt que compter les
    lignes est délibéré : la ligne site est absente pour une partie des projets
    (solana dans les fixtures), et un parseur positionnel y recopierait l'URL du
    dépôt comme site officiel.
    """
    entries: list[ListEntry] = []
    unparsed = 0
    heads = list(_AWESOME_HEAD.finditer(markdown))
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(markdown)
        body = markdown[head.end() : end]
        repo_match = _REPO_URL.match(head.group(2))
        if repo_match is None:
            unparsed += 1
            continue
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
                if homepage is None and "github.com" not in link.group(2):
                    homepage = link.group(2)
                continue
            # La description précède toujours les liens dans ce format. Après
            # le premier lien ne viennent que des métriques (« 5 stars per week
            # over 10 weeks », « 86,064 stars, 38,014 forks ») et la ligne de
            # tags — du balisage et des compteurs, jamais de l'éditorial.
            #
            # Sans cette borne, un projet dépourvu de description héritait de
            # sa ligne de métriques : le registre affichait « 5 stars per week
            # over 10 weeks » comme description du projet. Une valeur inventée
            # de plus, plausible et fausse, dans un champ que personne
            # n'aurait recoupé.
            if seen_link:
                continue
            if description is None:
                description = stripped
        entries.append(
            ListEntry(
                name=head.group(1).strip(),
                owner=repo_match.group(1),
                repo=repo_match.group(2),
                homepage_url=homepage,
                description=description,
                source_list="awesome-crypto",
            )
        )
    return ParseResult(entries=entries, unparsed=unparsed)
