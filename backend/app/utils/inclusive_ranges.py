"""Locate directly stated inclusive numeric input ranges in source text."""

from dataclasses import dataclass
from decimal import Decimal
from itertools import chain
import re


NUMBER = r"[+-]?\d+(?:\.\d+)?"
_INTERVAL = re.compile(
    rf"(?:\b(?:from|between|range\s+of)\s+)?(?<![\w./+\-])(?P<lower>{NUMBER})"
    rf"\s*(?:to\b|and\b|[-–—])\s*(?P<upper>{NUMBER})\s*,?\s*\(?inclusive\b\)?",
    re.I,
)
_CLAUSE_BREAK = re.compile(r"[;!?]|(?<!\d)\.|\.(?!\d)|\n(?=[ \t]*(?:\n|[#>*-][ \t]))")
_QUOTED = re.compile(r""""[^"\n]*"|“[^”\n]*”|(?<!\w)'[^'\n]*'(?!\w)|‘[^’\n]*’""")
_REJECTED = r"reject\w*|disallow\w*|den(?:y\w*|ies|ied)|forbid\w*|invalid\w*|exclude\w*"
_POLICY_VERB = re.compile(
    r"\b(?:accept\w*|allow\w*|permit\w*|require\w*|valid|support\w*|"
    r"(?:must|shall)\s+be|(?:is|are)\s+(?:a|an|limited|restricted|constrained)|"
    rf"{_REJECTED})\b",
    re.I,
)
_NEGATION = re.compile(r"\b(?:not|never|cannot)\b", re.I)


@dataclass(frozen=True)
class InclusiveRangeSpan:
    clause_number: int
    range_number: int
    start: int
    end: int
    clause_start: int
    clause_end: int
    endpoints: tuple[str, ...]

    @property
    def bounds(self):
        return tuple(Decimal(endpoint) for endpoint in self.endpoints)


def inclusive_range_spans(text):
    masked = _QUOTED.sub(lambda match: " " * len(match[0]), text)
    start = 0
    for index, boundary in enumerate(chain(_CLAUSE_BREAK.finditer(masked), (None,)), 1):
        end = boundary.start() if boundary else len(masked)
        clause = masked[start:end]
        for position, match in enumerate(_INTERVAL.finditer(clause), 1):
            policy = list(_POLICY_VERB.finditer(clause, 0, match.start()))
            if not policy or re.fullmatch(_REJECTED, policy[-1][0], re.I) or _NEGATION.search(clause[: match.start()]):
                continue
            lower, upper = match["lower"], match["upper"]
            if Decimal(lower) > Decimal(upper):
                continue
            yield InclusiveRangeSpan(
                index,
                position,
                start + match.start(),
                start + match.end(),
                start,
                end,
                (lower,) if Decimal(lower) == Decimal(upper) else (lower, upper),
            )
        start = boundary.end() if boundary else end
