"""Deterministic local matching; never treat user search terms as regexes."""
import re
import unicodedata
from functools import lru_cache


def normalize(text):
    return " ".join(unicodedata.normalize("NFC", str(text or "")).split()).casefold()


def phrase_pattern(term):
    """Adjacent word prefixes, with no match starting inside a word."""
    return r"(?<!\w)" + r"\s+".join(re.escape(word) + r"\w*" for word in term.split())


@lru_cache(maxsize=2048)
def prepared_phrase(term):
    from .morphology import PhraseMatcher
    return PhraseMatcher(term,phrase_pattern(term))


def matches(text, term, kind="contains"):
    text, term = normalize(text), normalize(term)
    if not term:
        return False
    if kind == "field":
        return text == term
    if kind == "phrase":
        return prepared_phrase(term).search(text)
    if kind == "word":
        return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text))
    if kind == "substring":
        return term in text
    # A keyword may continue into an ending, but cannot start inside a word.
    return bool(re.search(r"(?<!\w)" + re.escape(term), text))


class PreparedMatcher:
    """Per-search immutable conditions; normalize each candidate only once."""

    def __init__(self, specification):
        self.kind = specification.get('kind', 'contains')
        self.require_all = specification.get('mode', 'any') == 'all'
        self.excluded = tuple(value for term in specification.get('exclude', [])
                              if (value := normalize(term)))
        included = []
        for original in specification['terms']:
            term = normalize(original)
            pattern = None
            if term and self.kind not in {'field', 'substring'}:
                if self.kind == 'phrase':
                    pattern = prepared_phrase(term)
                elif self.kind == 'word':
                    expression = r'(?<!\w)' + re.escape(term) + r'(?!\w)'
                else:
                    expression = r'(?<!\w)' + re.escape(term)
                if self.kind!='phrase':pattern = re.compile(expression)
            included.append((original, term, pattern))
        self.included = tuple(included)
        filters = specification.get('filters', {})
        self.number_only = not included and any(filters.get(key) for key in ('lot_number', 'announcement_number'))

    def __call__(self, name, description):
        if self.kind == 'field' and not self.excluded:
            text = normalize(name)
        else:
            text = normalize(f"{name or ''}\n{description or ''}")
            if any(term in text for term in self.excluded):
                return []
            if self.kind == 'field':
                text = normalize(name)
        # Number filters are checked against the row by the search service.
        # Keep exclusion checking above, even for a number-only search.
        if self.number_only:
            return ['По номеру']
        found = []
        for original, term, pattern in self.included:
            matched = bool(term) and (text == term if self.kind == 'field' else
                       term in text if self.kind == 'substring' else bool(pattern.search(text)))
            if matched:
                found.append(original)
            elif self.require_all:
                return []
        return found


def match_lot(name, description, specification):
    matcher = specification if isinstance(specification, PreparedMatcher) else PreparedMatcher(specification)
    return matcher(name, description)
