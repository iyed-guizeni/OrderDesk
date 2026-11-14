"""Grounding checks for Extract: is what the LLM extracted really in what the client sent? Code only, no LLM.

What it catches: invented lines, quantities and codes; lines taken from a quoted earlier message.
What it cannot catch: a line that IS in the text but should not be an order line (an injection, a total row).
Those are covered by the Extract eval and the human review.
"""

import re
from collections.abc import Sequence

from orderdesk.catalogue.text import code_key, normalize
from orderdesk.domain.extraction import ExtractedOrder
from orderdesk.domain.grounding import GroundingIssue

MIN_WORD_SHARE = 0.8  # a reworded line still counts as found if this share of its words is in the source
QUOTE_PREFIX = ">"

# Quantities written as words (FR / EN). Anything else must appear as a number in the line.
NUMBER_WORDS = {
    "un": 1, "une": 1, "one": 1, "a": 1, "deux": 2, "two": 2, "trois": 3, "three": 3, "quatre": 4, "four": 4,
    "cinq": 5, "five": 5, "six": 6, "sept": 7, "seven": 7, "huit": 8, "eight": 8, "neuf": 9, "nine": 9,
    "dix": 10, "ten": 10, "douze": 12, "twelve": 12, "douzaine": 12, "dozen": 12, "demi douzaine": 6,
    "half a dozen": 6, "half dozen": 6, "vingtaine": 20, "cent": 100, "hundred": 100,
}
# "1 000", "1.000", "1,000", "1,5", "2.5", "12"; separators may be normal or non-breaking spaces
NUMBER = re.compile(r"\d+(?:[   .,]\d+)*")


def _flat(text: str) -> str:
    """Lower case, no accents, punctuation as spaces: '• 1,200 x Washer-Kit' -> '1 200 x washer kit'."""
    return " ".join("".join(ch if ch.isalnum() else " " for ch in normalize(text)).split())


def _split_quoted(body: str) -> tuple[str, str]:
    """(what the client wrote now, the quoted earlier messages: lines starting with '>')."""
    own, quoted = [], []
    for line in body.splitlines():
        (quoted if line.lstrip().startswith(QUOTE_PREFIX) else own).append(line)
    return "\n".join(own), "\n".join(quoted)


def _found(raw_text: str, source: str) -> bool:
    line, text = _flat(raw_text), _flat(source)
    if not line:
        return True
    if line in text:
        return True
    words = line.split()
    known = set(text.split())
    return sum(word in known for word in words) / len(words) >= MIN_WORD_SHARE


def _number_values(token: str) -> set[float]:
    """Every reading of a written number: '1,000' -> {1000, 1.0}, '1 000' -> {1000}, '1,5' -> {1.5, 15}."""
    plain = re.sub(r"[   ]", "", token)
    values = set()
    for thousands, decimal in ((",", "."), (".", ",")):  # English, then French
        candidate = plain.replace(thousands, "").replace(decimal, ".")
        if candidate.count(".") <= 1:
            values.add(float(candidate))
    return values


def _quantity_in(quantity: float, raw_text: str) -> bool:
    values = {v for token in NUMBER.findall(raw_text) for v in _number_values(token)}
    words = f" {_flat(raw_text)} "
    values |= {value for word, value in NUMBER_WORDS.items() if f" {word} " in words}
    return any(abs(value - quantity) < 1e-9 for value in values)


def check_grounding(order: ExtractedOrder, subject: str, body: str, attachment_texts: Sequence[str]) -> list[GroundingIssue]:
    """Issues for everything we cannot find in what the client sent. Empty list = fully grounded."""
    own_body, quoted = _split_quoted(body)
    own = "\n".join([subject, own_body, *attachment_texts])
    everything = f"{own}\n{quoted}"
    issues = []

    for index, line in enumerate(order.lines):
        if not _found(line.raw_text, own):
            kind = "line_only_in_quoted_text" if quoted and _found(line.raw_text, quoted) else "line_not_in_source"
            issues.append(GroundingIssue(kind=kind, line_index=index, value=line.raw_text))
        if line.quantity is not None and not _quantity_in(line.quantity, line.raw_text):
            issues.append(GroundingIssue(kind="quantity_not_in_line", line_index=index, value=f"{line.quantity:g}"))
        code = line.product.code_as_written
        if code and code_key(code) not in code_key(everything):
            issues.append(GroundingIssue(kind="code_not_in_source", line_index=index, value=code))

    if order.po_number and code_key(order.po_number) not in code_key(everything):
        issues.append(GroundingIssue(kind="po_not_in_source", value=order.po_number))
    return issues
