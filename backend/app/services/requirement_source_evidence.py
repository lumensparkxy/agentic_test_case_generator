"""Bind extraction claims to uploaded text without treating model prose as a quote."""

from collections import Counter
from hashlib import sha256
import re

from ..contracts.requirements import RequirementSourceReference


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


def bind_document_evidence(requirements, documents):
    """Whitespace may differ; spelling, punctuation and case must match the source."""
    prepared = []
    occurrences = Counter()
    totals = Counter(name for name, _ in documents)
    for name, text in documents:
        occurrences[name] += 1
        identity = name if totals[name] == 1 else f"{name} [upload {occurrences[name]}]"
        prepared.append((name, identity, text, _sections(text), sha256(text.encode()).hexdigest()))
    result = []
    for requirement in requirements:
        quote = requirement.source_excerpt or ""
        references = []
        ambiguous = False
        hint = (requirement.source_path or "").split(" > ", 1)[0]
        source_label = hint if hint in totals else ", ".join(totals)
        if quote.strip() and len(quote) <= MAX_EXCERPT_CHARS:
            pattern = re.compile(r"\s+".join(re.escape(word) for word in quote.split()))
            # A recognized document hint disambiguates repeated quotations. With no
            # recognized hint, retain all actual matches as separate source evidence.
            documents_to_search = [doc for doc in prepared if doc[0] == hint] or prepared
            for name, identity, text, sections, version in documents_to_search:
                for match in pattern.finditer(text):
                    if len(references) == MAX_SOURCE_MATCHES:
                        ambiguous = True
                        break
                    ids = [identifier for start, end, identifier in sections if start < match.end() and match.start() < end]
                    first_line = text.count("\n", 0, match.start()) + 1
                    last_line = text.count("\n", 0, match.end()) + 1
                    references.append(
                        RequirementSourceReference(
                            source_id=sha256(f"file:{identity}".encode()).hexdigest(),
                            source_version=version,
                            import_id="pending",
                            label=identity,
                            source_system="file",
                            source_section=f"Lines {first_line}-{last_line}",
                            original_requirement_ids=list(dict.fromkeys(ids)),
                            excerpt=text[match.start() : match.end()],
                            excerpt_verified=True,
                        )
                    )
                if ambiguous:
                    references = []
                    break
        flags = list(requirement.quality_flags)
        if ambiguous:
            flags.append("Source quotation is ambiguous: provide a longer excerpt with at most 32 matches.")
        if not references:
            flags.append("Source excerpt unavailable: reimport with a verbatim supporting quotation.")
        verified_ids = list(dict.fromkeys(identifier for source in references for identifier in source.original_requirement_ids))
        if set(requirement.original_requirement_ids) - set(verified_ids):
            flags.append("An extracted original requirement ID could not be verified against its supporting source section.")
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
    return result
