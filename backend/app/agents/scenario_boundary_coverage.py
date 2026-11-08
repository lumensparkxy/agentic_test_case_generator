"""Identify explicit valid input intervals and verify exact scenario evidence."""

from dataclasses import dataclass
import re

from ..utils.inclusive_ranges import NUMBER, inclusive_range_spans

_NUMERIC_TOKEN = re.compile(rf"(?<![\w./+\-]){NUMBER}(?!\w|\.\d)")
_RANGE_MENTION = re.compile(rf"{NUMBER}\s*(?:to\b|[-–—])\s*{NUMBER}|\bbetween\s+{NUMBER}\s+and\s+{NUMBER}", re.I)
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
    return [
        InclusiveInputRange(
            range_id=f"{requirement.id}:{span.clause_number}:{span.range_number}",
            requirement_id=requirement.id,
            source_excerpt=requirement.text[span.start : span.end],
            endpoints=span.endpoints,
        )
        for requirement in requirements
        for span in inclusive_range_spans(requirement.text)
    ]


def _contains_excerpt(text, quote):
    return bool(re.search(rf"(?<!\w){re.escape(quote)}(?!\w|\.\d)", text))


def verified_boundary_endpoints(scenario, evidence, ranges_by_id):
    if not scenario.get("must_have", True):
        return []
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
            or input_quote == outcome_quote
            or _NUMERIC_TOKEN.findall(input_quote) != [claim.endpoint]
            or not re.search(r"[^\W\d_]", input_quote)
            or _REJECTED_OUTCOME.search(outcome_quote)
        ):
            continue
        claims.setdefault(interval.range_id, {}).setdefault(claim.endpoint, claim)
    # Each explicit endpoint needs a distinct scenario. A combined range claim
    # cannot stand in for running the two input partitions independently.
    return [next(iter(values.values())) for values in claims.values() if len(values) == 1]
