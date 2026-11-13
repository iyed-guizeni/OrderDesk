"""Text normalisation shared by search and aliases, so 'Mitigeurs  Brossés' and 'mitigeur brosse' meet."""

import unicodedata

STOPWORDS = {"de", "du", "des", "la", "le", "les", "en", "pour", "a", "au", "et", "the", "of", "for", "in", "with"}
MIN_PLURAL_LENGTH = 4  # do not strip the "s" of short words


def normalize(text: str) -> str:
    """Lower case, no accents, single spaces."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


def tokens(text: str) -> set[str]:
    """Words without stopwords or plural "s": 'Colonnes de douche' -> {'colonne', 'douche'}."""
    words = "".join(ch if ch.isalnum() else " " for ch in normalize(text)).split()
    return {w[:-1] if w.endswith("s") and len(w) >= MIN_PLURAL_LENGTH else w for w in words if w not in STOPWORDS}


def code_key(code: str) -> str:
    """'pf 60375', 'PF-60375' -> 'PF60375'."""
    return "".join(ch for ch in code if ch.isalnum()).upper()
