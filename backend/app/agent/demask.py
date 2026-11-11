"""Customer codes back to real ids for people, and typed real ids to codes for the model.

Everything here runs on the server with the tenant's own PseudonymMap. The model only ever sees
codes; people see real ids in streamed text, stored answers, evidence and exports.
"""

import re
from typing import Any

from app.core.pseudonyms import CODE_PATTERN, DASHES, PseudonymMap, canonical_code, normalise

# A trailing piece of text that may still grow into a code in the next delta.
_PARTIAL = re.compile(rf"C(?:U(?:S(?:T(?:[{DASHES}][0-9A-HJKMNP-TV-Z]{{0,10}})?)?)?)?$")
_PIECE = re.compile(r"[^\s/#:=,;|]+")
_EDGE_PUNCTUATION = "?.,;:!()[]{}\"'«»،؟"
MAX_WORDS_IN_ID = 3
CUSTOMER_CUES = frozenset(
    {
        "customer",
        "customers",
        "client",
        "clients",
        "buyer",
        "buyers",
        "id",
        "ids",
        "account",
        "cust",
        "no",
        "number",
        "عميل",
        "العميل",
        "عملاء",
        "زبون",
        "الزبون",
        "رقم",
        "معرف",
    }
)


def display_map_for(
    db: Any, tenant_id: Any, version: Any, settings: Any, *, allow_pii: bool
) -> PseudonymMap | None:
    """Return the map people see through: real ids, or the pages' pseudonyms when hidden.

    A customer id column flagged as personal data shows the same `cust_` pseudonym as the
    segments and churn pages unless the company opted in to personal data.
    """
    from app.core.pseudonyms import load_pseudonym_map
    from app.db.repository import ColumnProfileRepository

    mapping = load_pseudonym_map(db, tenant_id, version, settings)
    if mapping is None:
        return None
    profiles = ColumnProfileRepository(db, tenant_id).for_version(version.id)
    flagged = any(p.name == mapping.column and p.pii_flag for p in profiles)
    return mapping.for_display(hidden=flagged and not allow_pii)


def demask_text(mapping: PseudonymMap | None, text: str) -> str:
    """Replace every known code in `text` by its real id; unknown codes stay as they are."""
    if mapping is None or not text or "CUST" not in text:
        return text
    return CODE_PATTERN.sub(lambda m: mapping.shown(canonical_code(m.group(0))) or m.group(0), text)


def demask_evidence(mapping: PseudonymMap | None, value: Any) -> Any:
    """Return a copy of evidence (rows, SQL, stdout, nested results) with real ids."""
    if mapping is None:
        return value
    if isinstance(value, str):
        return demask_text(mapping, value)
    if isinstance(value, dict):
        return {
            (demask_text(mapping, k) if isinstance(k, str) else k): demask_evidence(mapping, v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [demask_evidence(mapping, v) for v in value]
    if isinstance(value, tuple):
        return tuple(demask_evidence(mapping, v) for v in value)
    return value


class StreamDemasker:
    """Demask streamed text, holding back a tail that may be the start of a split code."""

    def __init__(self, mapping: PseudonymMap | None) -> None:
        """Keep the map; without one, text passes straight through."""
        self.mapping = mapping
        self._pending = ""

    def feed(self, delta: str) -> str:
        """Return the text that is safe to show now."""
        if self.mapping is None:
            return delta
        text = self._pending + delta
        tail = _PARTIAL.search(text)
        cut = tail.start() if tail else len(text)
        self._pending = text[cut:]
        return demask_text(self.mapping, text[:cut])

    def flush(self) -> str:
        """Return whatever was held back once the stream ends."""
        rest, self._pending = self._pending, ""
        return demask_text(self.mapping, rest)


def _core(span: str) -> tuple[int, int]:
    """Return the start and end of `span` without surrounding punctuation or a trailing 's."""
    start, end = 0, len(span)
    while start < end and span[start] in _EDGE_PUNCTUATION:
        start += 1
    while end > start and span[end - 1] in _EDGE_PUNCTUATION:
        end -= 1
    if span[start:end].lower().endswith(("'s", "’s")) and end - start > 2:
        end -= 2
    return start, end


def _numeric(token: str) -> bool:
    return normalise(token).isdigit()


def _has_cue(text: str, start: int) -> bool:
    """Return whether a customer cue comes right before position `start`.

    A bare number is only taken as a customer id after `#`, `=`, `:` or a word such as
    "customer", "client" or "id", so amounts, years and counts in a question stay numbers.
    """
    before = text[:start]
    if before.endswith(("#", "=", ":")):
        return True
    words = before.split()
    if not words:
        return False
    return words[-1].strip(_EDGE_PUNCTUATION + "#").casefold() in CUSTOMER_CUES


def pseudonymise_question(mapping: PseudonymMap | None, text: str) -> str:
    """Replace real customer ids the user typed (1 to 3 words, any case) by their codes.

    Ids are found as whole words or as pieces between `/ # : = , ; |` (so `#7003`, `id=7003`
    and `C-001/C-002` work); Arabic-Indic digits match too. A purely numeric id is replaced only
    after a customer cue (see `_has_cue`).
    """
    if mapping is None or not text:
        return text
    pieces = list(_PIECE.finditer(text))
    out: list[str] = []
    position = 0
    index = 0
    while index < len(pieces):
        replaced = False
        for size in range(min(MAX_WORDS_IN_ID, len(pieces) - index), 0, -1):
            first, last = pieces[index], pieces[index + size - 1]
            span = text[first.start() : last.end()]
            start, end = _core(span)
            token = span[start:end]
            at = first.start() + start
            listed = position > 0 and not text[position:at].strip(" /,;|&")  # "C-001/7003"
            if end <= start or (_numeric(token) and not (listed or _has_cue(text, at))):
                continue
            code = mapping.lookup_token(token)
            if code is None:
                continue
            out.append(text[position : first.start() + start])
            out.append(code)
            position = first.start() + end
            index += size
            replaced = True
            break
        if not replaced:
            index += 1
    out.append(text[position:])
    return "".join(out)
