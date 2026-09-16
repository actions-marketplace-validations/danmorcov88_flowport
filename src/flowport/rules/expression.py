"""Find attribute/variable references in NiFi Expression Language text.

Only the parts of the language that matter for migration are modelled: the
subject of an expression (``${subject...}``), whether it is quoted, whether
functions are chained onto it, and nesting. Escaping follows the Expression
Language Guide: ``$$`` is a literal dollar sign, so an expression starts only
where an odd number of ``$`` precedes ``{``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Characters that end an unquoted subject.
_SUBJECT_END = frozenset("}:( \t\r\n")
_PARAMETER_REFERENCE = re.compile(r"(?<!#)#\{([A-Za-z0-9 ._-]+)\}")


@dataclass(frozen=True)
class Reference:
    """One ``${...}`` expression whose subject is an attribute or variable name."""

    name: str
    start: int  # index of the '$' that opens the expression
    subject_start: int  # index of the first character of the subject
    subject_end: int  # index after the last character of the subject
    quoted: bool
    has_function: bool


def find_references(text: str) -> list[Reference]:
    """Return every expression subject that names an attribute or variable.

    Subjects followed directly by ``(`` are functions without a subject
    (``${now()}``, ``${literal('x')}``) and are skipped. Nested expressions are
    reported too.
    """
    refs: list[Reference] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "$":
            i += 1
            continue
        run = i
        while run < n and text[run] == "$":
            run += 1
        dollars = run - i
        if run < n and text[run] == "{" and dollars % 2 == 1:
            ref = _parse_subject(text, run + 1, start=run - 1)
            if ref is not None:
                refs.append(ref)
            i = run + 1
        else:
            i = run
    return refs


def _parse_subject(text: str, pos: int, *, start: int) -> Reference | None:
    n = len(text)
    while pos < n and text[pos] in " \t\r\n":
        pos += 1
    if pos >= n:
        return None
    quoted = False
    if text[pos] in "'\"":
        quote = text[pos]
        end = text.find(quote, pos + 1)
        if end < 0:
            return None
        name = text[pos + 1 : end]
        subject_start, subject_end = pos + 1, end
        after = end + 1
        quoted = True
    else:
        end = pos
        while end < n and text[end] not in _SUBJECT_END:
            end += 1
        name = text[pos:end]
        subject_start, subject_end = pos, end
        after = end
        if not name or name.startswith("$"):
            return None
    while after < n and text[after] in " \t\r\n":
        after += 1
    if after < n and text[after] == "(":
        return None  # function call without a subject
    has_function = after < n and text[after] == ":"
    return Reference(
        name=name,
        start=start,
        subject_start=subject_start,
        subject_end=subject_end,
        quoted=quoted,
        has_function=has_function,
    )


def find_parameter_references(text: str) -> list[str]:
    """Names referenced as ``#{name}`` (``##{name}`` is an escaped literal)."""
    return _PARAMETER_REFERENCE.findall(text)
