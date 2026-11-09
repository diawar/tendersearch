"""Dictionary word forms for Russian phrase searches; no network at runtime."""
from functools import lru_cache
from os.path import commonprefix
import re


@lru_cache(maxsize=1)
def analyzer():
    from pymorphy3 import MorphAnalyzer
    return MorphAnalyzer(lang='ru')


@lru_cache(maxsize=12000)
def known_lemmas(word):
    if not re.fullmatch(r'[а-яё]{2,150}',word):return frozenset()
    return frozenset(p.normal_form for p in analyzer().parse(word) if p.is_known)


@lru_cache(maxsize=4096)
def phrase_candidate_prefix(word):
    """A necessary SQL condition across all inflections, never the final match."""
    forms={word}
    if re.fullmatch(r'[а-яё]{2,150}',word):
        for parsed in analyzer().parse(word):
            if parsed.is_known:forms.update(form.word for form in parsed.lexeme)
    prefix=commonprefix(sorted(forms))
    # Suppletive forms (человек/люди) can have no common prefix. In that case
    # leave this word unfiltered in SQL and defer to the exact phrase matcher.
    return prefix if len(prefix)>=2 else ''


class PhraseMatcher:
    def __init__(self,term,prefix_pattern):
        self.direct=re.compile(prefix_pattern,re.IGNORECASE)
        self.words=term.split()
        self.lemmas=[known_lemmas(word) for word in self.words]
        self.inflected=None
        if any(self.lemmas):
            pieces=[r'([а-яё]+)' if lemmas else '('+re.escape(word)+r'\w*)' for word,lemmas in zip(self.words,self.lemmas)]
            # Lookahead allows overlapping windows of adjacent words.
            self.inflected=re.compile(r'(?<!\w)(?=('+r'\s+'.join(pieces)+r')(?!\w))',re.IGNORECASE)

    def spans(self,text):
        spans={match.span() for match in self.direct.finditer(text)}
        if self.inflected:
            for match in self.inflected.finditer(text):
                candidates=match.groups()[1:]
                if all(candidate.casefold().startswith(word) or bool(lemmas & known_lemmas(candidate.casefold()))
                       for word,lemmas,candidate in zip(self.words,self.lemmas,candidates)):
                    spans.add(match.span(1))
        return sorted(spans)

    def search(self,text):
        if self.direct.search(text):return True
        if not self.inflected:return False
        for match in self.inflected.finditer(text):
            if all(candidate.casefold().startswith(word) or bool(lemmas & known_lemmas(candidate.casefold()))
                   for word,lemmas,candidate in zip(self.words,self.lemmas,match.groups()[1:])):
                return True
        return False
