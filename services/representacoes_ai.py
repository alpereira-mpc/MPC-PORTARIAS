"""Conservative matching of names extracted from a Representação PDF.

The extract is untrusted text. Only a unique full-name match may select a
person in an administrative form.
"""

from collections.abc import Iterable, Mapping
import re
import unicodedata


_PARTICLES = frozenset({"da", "das", "de", "do", "dos", "e"})


def _tokens(value: str | None) -> tuple[str, ...]:
    folded = unicodedata.normalize("NFKD", str(value or "").casefold())
    without_accents = "".join(
        char for char in folded if not unicodedata.combining(char)
    )
    words = re.findall(r"[a-z0-9]+", without_accents)
    return tuple(word for word in words if word not in _PARTICLES)


def _one_edit_apart(left: str, right: str) -> bool:
    """Accept one insertion, deletion or substitution in a long name token."""
    if min(len(left), len(right)) < 5 or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    short, long = sorted((left, right), key=len)
    for index in range(len(long)):
        if long[:index] + long[index + 1 :] == short:
            return True
    return False


def _similarity(query: tuple[str, ...], candidate: tuple[str, ...]) -> int | None:
    """Return edit count for a secure full-name candidate, or None."""
    if not query or len(query) != len(candidate):
        return None
    if query == candidate:
        return 0
    # Short names and initials are too easy to confuse with another person.
    if len(query) < 3 or any(len(token) < 2 for token in query + candidate):
        return None
    differences = [
        (left, right) for left, right in zip(query, candidate) if left != right
    ]
    if len(differences) > 2 or len(query) - len(differences) < 2:
        return None
    if not all(_one_edit_apart(left, right) for left, right in differences):
        return None
    return len(differences)


def _unique_match(name: str | None, options: Iterable[tuple[object, str]]):
    query = _tokens(name)
    if not query:
        return None
    candidates = []
    for key, candidate_name in options:
        score = _similarity(query, _tokens(candidate_name))
        if score is not None:
            candidates.append((score, key))
    if not candidates:
        return None
    best_score = min(score for score, _ in candidates)
    best = [key for score, key in candidates if score == best_score]
    return best[0] if len(best) == 1 else None


def match_person(name: str | None, options: Mapping[object, str]):
    """Return the ID of one safely matching person, otherwise None."""
    return _unique_match(name, options.items())


def match_people(names: Iterable[str] | None, options: Mapping[object, str]) -> list:
    """Match each full name, keeping unique IDs in source order."""
    matched = []
    for name in names or ():
        identifier = match_person(name, options)
        if identifier is not None and identifier not in matched:
            matched.append(identifier)
    return matched


def match_relator(name: str | None, options: Iterable[str]) -> str | None:
    """Return the sole matching value from the valid relator choices."""
    return _unique_match(name, ((item, item) for item in options))
