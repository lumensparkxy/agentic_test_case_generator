"""Identify explicit valid input intervals and verify exact scenario evidence."""

from dataclasses import dataclass
from decimal import Decimal
import re


_NUMBER = r"[+-]?\d+(?:\.\d+)?"
_NUMERIC_TOKEN = re.compile(rf"(?<![\w./+\-]){_NUMBER}(?!\w|\.\d)")
_INTERVAL = re.compile(
    rf"(?:\b(?:from|between|range\s+of)\s+)?(?<![\w./+\-])(?P<lower>{_NUMBER})"
    rf"\s*(?:to\b|and\b|[-–—])\s*(?P<upper>{_NUMBER})\s*,?\s*\(?inclusive\b\)?",
    re.I,
)
_RANGE_MENTION = re.compile(rf"{_NUMBER}\s*(?:to\b|[-–—])\s*{_NUMBER}|\bbetween\s+{_NUMBER}\s+and\s+{_NUMBER}", re.I)
_CLAUSE_BREAK = re.compile(r"[;!?\n]|(?<!\d)\.|\.(?!\d)")
_QUOTED = re.compile(r""""[^"\n]*"|“[^”\n]*”|(?<!\w)'[^'\n]*'(?!\w)|‘[^’\n]*’""")
_POLICY_VERB = re.compile(
    r"\b(?:accept\w*|allow\w*|permit\w*|require\w*|valid|support\w*|"
    r"(?:must|shall)\s+be|(?:is|are)\s+(?:a|an|limited|restricted|constrained)|"
    r"reject\w*|disallow\w*|deny\w*|forbid\w*|invalid\w*|exclude\w*)\b",
    re.I,
)
_REJECTION = re.compile(r"\b(?:reject\w*|disallow\w*|den(?:y\w*|ies|ied)|forbid\w*|invalid\w*|exclude\w*)\b", re.I)
_NEGATION = re.compile(r"\b(?:not|never|cannot)\b", re.I)
_REJECTED_OUTCOME = re.compile(
    r"\b(?:is rejected|are rejected|is denied|are denied|"
    r"(?:displays?|shows?) (?:an? |the )?(?:inline |validation )?error|"
    r"(?:error|rejection) (?:appears|is shown|is displayed|is returned)|"
    r"(?:creates?|saves?) no|(?:is|are) not (?:created|saved)|without (?:creating|saving))\b",
    re.I,
)


@dataclass(frozen=True)
class InclusiveInputRange:
    range_id: str
    requirement_id: str
    source_excerpt: str
    endpoints: tuple[str, ...]


def inclusive_input_ranges(requirements):
    ranges = []
    for requirement in requirements:
        source = _QUOTED.sub(lambda match: " " * len(match[0]), requirement.text)
        for index, clause in enumerate(_CLAUSE_BREAK.split(source)):
            for position, match in enumerate(_INTERVAL.finditer(clause)):
                policy = list(_POLICY_VERB.finditer(clause, 0, match.start()))
                if not policy or _REJECTION.fullmatch(policy[-1][0]) or _NEGATION.search(clause[: match.start()]):
                    continue
                lower, upper = match["lower"], match["upper"]
                if Decimal(lower) > Decimal(upper):
                    continue
                ranges.append(
                    InclusiveInputRange(
                        range_id=f"{requirement.id}:{index + 1}:{position + 1}",
                        requirement_id=requirement.id,
                        source_excerpt=match[0],
                        endpoints=(lower,) if Decimal(lower) == Decimal(upper) else (lower, upper),
                    )
                )
    return ranges


def _contains_excerpt(text, quote):
    return bool(re.search(rf"(?<!\w){re.escape(quote)}(?!\w|\.\d)", text))


def verified_boundary_endpoints(scenario, evidence, ranges_by_id):
    if not scenario.get("must_have", True):
        return set()
    text = " ".join(f"{scenario.get('title', '')} {scenario.get('objective', '')}".split())
    inputs = _RANGE_MENTION.sub("…", text)
    claims = {}
    for claim in evidence:
        interval = ranges_by_id.get(claim.range_id)
        if not interval or claim.endpoint not in interval.endpoints:
            continue
        input_quote = " ".join(claim.input_quote.split())
        outcome_quote = " ".join(claim.outcome_quote.split())
        if (
            not _contains_excerpt(inputs, input_quote)
            or not _contains_excerpt(text, outcome_quote)
            or outcome_quote in input_quote
            or input_quote in outcome_quote
            or _NUMERIC_TOKEN.findall(input_quote) != [claim.endpoint]
            or not re.search(r"[^\W\d_]", input_quote)
            or _REJECTED_OUTCOME.search(outcome_quote)
        ):
            continue
        claims.setdefault(interval.range_id, set()).add(claim.endpoint)
    # Each explicit endpoint needs a distinct scenario. A combined range claim
    # cannot stand in for running the two input partitions independently.
    return {(range_id, next(iter(values))) for range_id, values in claims.items() if len(values) == 1}
