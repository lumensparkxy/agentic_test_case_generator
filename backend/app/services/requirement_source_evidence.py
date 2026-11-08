"""Bind extraction claims to uploaded text without treating model prose as a quote."""

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import re

from ..contracts.requirements import Requirement, RequirementSourceFinding, RequirementSourceReference
from ..utils.inclusive_ranges import InclusiveRangeSpan, inclusive_range_spans


MAX_EXCERPT_CHARS = 8000
MAX_SOURCE_MATCHES = 32
# Explicit source labels, not normalized model IDs or arbitrary numbers in prose.
SOURCE_ID = re.compile(
    r"(?m)^\s*(?:#{1,6}\s+|[-*]\s+)?(?:\*\*)?\[?"
    r"(?P<id>[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)*[-_]\d+(?:\.\d+)*)"
    r"\]?(?=\s|:|\*|$)"
)


def _sections(text):
    matches = list(SOURCE_ID.finditer(text))
    return [(match.start(), matches[index + 1].start() if index + 1 < len(matches) else len(text), match.group("id")) for index, match in enumerate(matches)]


@dataclass(frozen=True)
class _Document:
    name: str
    label: str
    text: str
    sections: list[tuple[int, int, str]]
    version: str
    ranges: list[InclusiveRangeSpan]

    def original_ids(self, start, end):
        return list(dict.fromkeys(identifier for left, right, identifier in self.sections if left < end and start < right))

    def lines(self, start, end):
        first = self.text.count("\n", 0, start) + 1
        last = self.text.count("\n", 0, end) + 1
        return f"Lines {first}-{last}"


@dataclass(frozen=True)
class BoundDocumentEvidence:
    requirements: list[Requirement]
    findings: list[RequirementSourceFinding]


def _check_range_claims(requirement, matched, references, source_label, covered):
    evidence = {}
    for document, start, end in matched:
        for span in document.ranges:
            if start <= span.start and span.end <= end:
                evidence.setdefault(span.bounds, set()).add((document.label, span.start))
    findings = []
    unique_reference = references[0] if len(references) == 1 else None
    claims = list(inclusive_range_spans(requirement.text))
    claim_counts = Counter(claim.bounds for claim in claims)
    for claim in claims:
        candidates = evidence.get(claim.bounds, set())
        if len(candidates) == 1 and claim_counts[claim.bounds] == 1:
            covered.update(candidates)
            continue
        findings.append(
            RequirementSourceFinding(
                kind="ambiguous_range" if candidates else "unverified_range",
                source_label=unique_reference.label if unique_reference else source_label,
                source_section=unique_reference.source_section if unique_reference else None,
                original_requirement_ids=list(dict.fromkeys(identifier for ref in references for identifier in ref.original_requirement_ids)),
                requirement_id=requirement.id,
                excerpt=requirement.text[claim.start : claim.end],
            )
        )
    return findings


def bind_document_evidence(requirements, documents):
    """Whitespace may differ; spelling, punctuation and case must match the source."""
    prepared = []
    occurrences = Counter()
    totals = Counter(name for name, _ in documents)
    for name, text in documents:
        occurrences[name] += 1
        identity = name if totals[name] == 1 else f"{name} [upload {occurrences[name]}]"
        prepared.append(_Document(name, identity, text, _sections(text), sha256(text.encode()).hexdigest(), list(inclusive_range_spans(text))))
    result = []
    findings = []
    covered = set()
    for requirement in requirements:
        quote = requirement.source_excerpt or ""
        references = []
        matched = []
        ambiguous = False
        hint = (requirement.source_path or "").split(" > ", 1)[0]
        source_label = hint if hint in totals else ", ".join(totals)
        if quote.strip() and len(quote) <= MAX_EXCERPT_CHARS:
            pattern = re.compile(r"\s+".join(re.escape(word) for word in quote.split()))
            # A recognized document hint disambiguates repeated quotations. With no
            # recognized hint, retain all actual matches as separate source evidence.
            documents_to_search = [doc for doc in prepared if doc.name == hint] or prepared
            for document in documents_to_search:
                for match in pattern.finditer(document.text):
                    if len(references) == MAX_SOURCE_MATCHES:
                        ambiguous = True
                        break
                    start, end = match.span()
                    matched.append((document, start, end))
                    references.append(
                        RequirementSourceReference(
                            source_id=sha256(f"file:{document.label}".encode()).hexdigest(),
                            source_version=document.version,
                            import_id="pending",
                            label=document.label,
                            source_system="file",
                            source_section=document.lines(start, end),
                            original_requirement_ids=document.original_ids(start, end),
                            excerpt=document.text[start:end],
                            excerpt_verified=True,
                        )
                    )
                if ambiguous:
                    references = []
                    matched = []
                    break
        flags = list(requirement.quality_flags)
        if ambiguous:
            flags.append("Source quotation is ambiguous: provide a longer excerpt with at most 32 matches.")
        if not references:
            flags.append("Source excerpt unavailable: reimport with a verbatim supporting quotation.")
        verified_ids = list(dict.fromkeys(identifier for source in references for identifier in source.original_requirement_ids))
        if set(requirement.original_requirement_ids) - set(verified_ids):
            flags.append("An extracted original requirement ID could not be verified against its supporting source section.")
        claim_findings = _check_range_claims(requirement, matched, references, source_label, covered)
        findings.extend(claim_findings)
        for finding in claim_findings:
            reason = "could not be matched to one distinct source clause" if finding.kind == "ambiguous_range" else "is absent from the supporting quotation"
            flags.append(f"Inclusive range “{finding.excerpt}” {reason}. Review the exact source before approval.")
        result.append(
            requirement.model_copy(
                update={
                    "source_system": "file",
                    "source_issue_key": None,
                    "source_issue_type": None,
                    "source_parent_key": None,
                    "source_parent_title": None,
                    "source_issue_url": None,
                    "source_issue_updated_at": None,
                    "sync_target_issue_key": None,
                    "sources": references,
                    "original_requirement_ids": verified_ids,
                    "source_excerpt": references[0].excerpt if references else None,
                    "source_path": references[0].label if references else source_label,
                    "source_section": references[0].source_section if references else None,
                    "source_hierarchy": [source.label for source in references] if references else list(totals),
                    "artifact_set_id": None,
                    "artifact_item_id": None,
                    "artifact_version_id": None,
                    "artifact_version_number": None,
                    "quality_flags": list(dict.fromkeys(flags)),
                }
            )
        )
    for document in prepared:
        for span in document.ranges:
            if (document.label, span.start) in covered:
                continue
            start = max(span.clause_start, span.start - 160)
            end = min(span.clause_end, span.end + 160)
            findings.append(
                RequirementSourceFinding(
                    kind="uncovered_range",
                    source_label=document.label,
                    source_section=document.lines(span.start, span.end),
                    original_requirement_ids=document.original_ids(span.start, span.end),
                    excerpt=document.text[start:end].strip(),
                )
            )
    return BoundDocumentEvidence(result, findings)
