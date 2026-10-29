"""Deterministic local matching; never treat user search terms as regexes."""
import re
import unicodedata


def normalize(text):
    return " ".join(unicodedata.normalize("NFC", str(text or "")).split()).casefold()


def matches(text, term, kind="contains"):
    text, term = normalize(text), normalize(term)
    if not term:
        return False
    if kind == "field":
        return text == term
    if kind in {"word", "phrase"}:
        return bool(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text))
    return term in text


def match_lot(name, description, specification):
    combined = f"{name or ''}\n{description or ''}"
    if any(matches(combined, term, "contains") for term in specification.get("exclude", [])):
        return []
    kind = specification.get("kind", "contains")
    text = name if kind == "field" else combined
    included = specification["terms"]
    found = [term for term in included if matches(text, term, kind)]
    if specification.get("mode", "any") == "all" and len(found) != len(included):
        return []
    return found
