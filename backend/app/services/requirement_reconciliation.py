"""Pure, source-independent comparison and reviewed baseline construction (#294)."""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher
from hashlib import sha256
import json
import re
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from ..contracts.requirements import Requirement, RequirementSourceReference
from ..contracts.requirement_imports import ImportCandidate, ImportMatch, RequirementImportPreview, ImportApplyInput


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def normalized(text):
    return re.sub(r"\s+", " ", text.strip())


def source_reference(req, import_id, label):
    location = str(req.source_issue_url or req.source_path or label)
    return RequirementSourceReference(
        source_id=digest([req.source_system, location, req.source_issue_key]),
        source_version=digest([req.text, str(req.source_issue_updated_at or "")]),
        import_id=import_id,
        label=req.source_path or label,
        source_system=req.source_system,
        source_issue_key=req.source_issue_key,
        source_issue_url=req.source_issue_url,
        excerpt=req.source_excerpt or req.text,
    )


def canonical_requirements(project_id, snapshot):
    """Lazy identity assignment is deterministic and never rewrites a legacy snapshot."""
    if not snapshot:
        return [], []
    payload = snapshot.get("payload", {})
    result = []
    for field in ("requirements", "retired_requirements"):
        rows = []
        for item in payload.get(field, []):
            req = Requirement.model_validate(item).model_copy(deep=True)
            seed = req.artifact_item_id or f"{snapshot['snapshot_id']}:{req.id}"
            req.requirement_uid = req.requirement_uid or str(uuid5(NAMESPACE_URL, f"{project_id}:{seed}"))
            if not req.sources:
                req.sources = [source_reference(req, snapshot["snapshot_id"], payload.get("source_name") or "Imported requirements")]
            req.lifecycle_status = "retired" if field == "retired_requirements" else "active"
            rows.append(req)
        result.append(rows)
    return tuple(result)


def compare_requirements(current, incoming, semantic_matches=None, semantic_failed=False, source_name="Imported requirements"):
    candidates = []
    semantic_matches = semantic_matches or {}
    for index, req in enumerate(incoming):
        cid = f"incoming-{index + 1}"
        exact = [old for old in current if normalized(old.text) == normalized(req.text)]
        reference = source_reference(req, "comparison", source_name)
        confirmed = [old for old in current if any((s.source_id, s.source_version) == (reference.source_id, reference.source_version) for s in old.sources)]

        scores = sorted(((SequenceMatcher(None, normalized(req.text), normalized(old.text)).ratio(), old) for old in current), key=lambda x: -x[0])
        suggestions = [
            ImportMatch(requirement_uid=old.requirement_uid, reason="Similar wording; verify behavior and constraints.")
            for score, old in scores[:3]
            if score >= 0.45
        ]
        for old in confirmed:
            if old.requirement_uid not in {s.requirement_uid for s in suggestions}:
                suggestions.append(
                    ImportMatch(
                        requirement_uid=old.requirement_uid,
                        reason="Previously reviewed mapping for this source content version. Check against the current content.",
                    )
                )
        valid_uids = {r.requirement_uid for r in current}
        for match in semantic_matches.get(cid, []):
            if match.requirement_uid in valid_uids and match.requirement_uid not in {s.requirement_uid for s in suggestions}:
                suggestions.append(match)
        if len(exact) == 1:
            kind, target, reason = "unchanged", exact[0].requirement_uid, "Identical requirement text; retain identity and approval, attach source evidence."
        elif len(exact) > 1:
            kind, target, reason = "needs_decision", None, "Multiple existing requirements have identical text. Choose the intended requirement."
            suggestions = [ImportMatch(requirement_uid=r.requirement_uid, reason="Identical text") for r in exact]
        elif suggestions:
            kind, target, reason = "needs_decision", None, "Possible revision or overlap. Review the existing requirement before choosing."
            if len(suggestions) == 1:
                kind, target = "updated", suggestions[0].requirement_uid
                reason = "Suggested revision; compare both versions and confirm or change the match."
        else:
            kind, target, reason = (
                ("needs_decision", None, "Automatic comparison was unavailable. Choose a match or add independently.")
                if semantic_failed and current
                else ("new", None, "No matching requirement found; add to the project.")
            )
        # Never accept model-extracted identity, approval or version fields as canonical.
        req = req.model_copy(update={"requirement_uid": None, "sources": [], "content_version": 1, "lifecycle_status": "active"})
        candidates.append(
            ImportCandidate(candidate_id=cid, requirement=req, classification=kind, target_requirement_uid=target, suggestions=suggestions, reason=reason)
        )
    texts = Counter(normalized(c.requirement.text) for c in candidates)
    counts = Counter(c.target_requirement_uid for c in candidates if c.target_requirement_uid)
    for candidate in candidates:
        if counts[candidate.target_requirement_uid] > 1 or texts[normalized(candidate.requirement.text)] > 1:
            candidate.classification = "needs_decision"
            candidate.reason = (
                "Incoming requirements duplicate each other or target the same existing requirement; choose one update or addition and resolve the others."
            )
            candidate.target_requirement_uid = None
    return candidates


def preview_counts(candidates, current_count):
    counts = dict(Counter(c.classification for c in candidates))
    return {**{k: counts.get(k, 0) for k in ("new", "updated", "unchanged", "needs_decision")}, "retiring": 0, "current_active": current_count}


def build_preview(project_id, revision, current, incoming, source_name, operation, import_id, semantic_matches=None, semantic_failed=False):
    candidates = compare_requirements(current, incoming, semantic_matches, semantic_failed, source_name)
    return RequirementImportPreview(
        import_id=import_id,
        project_id=project_id,
        base_project_revision=revision,
        source_name=source_name,
        operation=operation,
        created_at=datetime.now(timezone.utc),
        current_requirements=current,
        candidates=candidates,
        suggested_update_scope=[r.requirement_uid for r in current] if operation == "requirements.refine" else [],
        counts=preview_counts(candidates, len(current)),
        warnings=["Automatic comparison was incomplete. Review unmatched requirements manually."] if semantic_failed else [],
    )


def reconcile(preview: RequirementImportPreview, decision: ImportApplyInput, retired=None):
    current = {r.requirement_uid: r.model_copy(deep=True) for r in preview.current_requirements}
    candidates = {c.candidate_id: c for c in preview.candidates}
    if len(decision.decisions) != len(candidates) or {d.candidate_id for d in decision.decisions} != set(candidates):
        raise HTTPException(422, "Resolve every incoming requirement exactly once before applying.")
    scope = set(decision.update_scope)
    if not scope <= current.keys():
        raise HTTPException(422, "Update scope contains an unknown requirement.")
    targeted = set()
    resolved_ids = {}
    added_parents = []
    incoming_id_counts = Counter(c.requirement.id for c in preview.candidates)
    active = list(current.values())
    retired = deepcopy(retired or [])
    used_ids = {r.id for r in active + retired}
    next_number = max([int(r[4:]) for r in used_ids if re.fullmatch(r"REQ-\d+", r)] or [0]) + 1
    counts = {"added": 0, "updated": 0, "unchanged": 0, "retired": 0, "skipped": 0}
    for choice in decision.decisions:
        candidate = candidates[choice.candidate_id]
        incoming = candidate.requirement
        if choice.action == "skip":
            if choice.target_requirement_uid:
                raise HTTPException(422, "Skipped requirements cannot have a match.")
            counts["skipped"] += 1
            continue
        reference = source_reference(incoming, preview.import_id, preview.source_name)
        if choice.action == "add":
            if choice.target_requirement_uid:
                raise HTTPException(422, "Independent additions cannot have a match.")
            # Recovery preserves original baseline IDs; subsequent additions are allocated above them.
            uid = str(uuid5(NAMESPACE_URL, f"{preview.project_id}:{preview.import_id}:{choice.candidate_id}"))
            req = incoming.model_copy(
                deep=True,
                update={
                    "id": f"REQ-{next_number:03d}",
                    "requirement_uid": uid,
                    "content_version": 1,
                    "review_status": "Needs Review",
                    "lifecycle_status": "active",
                    "sources": [reference],
                    "artifact_set_id": None,
                    "artifact_item_id": None,
                    "artifact_version_id": None,
                    "artifact_version_number": None,
                    "parent_requirement_id": None,
                },
            )
            next_number += 1
            active.append(req)
            resolved_ids[incoming.id] = req.id
            added_parents.append((req, incoming.parent_requirement_id))
            counts["added"] += 1
            continue
        target = choice.target_requirement_uid
        if target not in current:
            raise HTTPException(422, "Choose an existing requirement for this update.")
        if target in targeted:
            raise HTTPException(422, "Two incoming requirements cannot update the same requirement in one import.")
        targeted.add(target)
        resolved_ids[incoming.id] = current[target].id
        req = current[target]
        changed = normalized(req.text) != normalized(incoming.text)
        if choice.action == "keep" and changed:
            raise HTTPException(422, "Keep unchanged requires identical text; update, add independently, or skip this candidate.")
        if changed:
            req.text = incoming.text
            req.content_version += 1
            req.review_status = "Needs Review"
            req.quality_flags = list(incoming.quality_flags)
            # A cross-source revision must not silently retarget an existing external sync.
            for field in (
                "source_system",
                "source_issue_key",
                "source_issue_url",
                "source_path",
                "source_excerpt",
                "source_section",
                "source_hierarchy",
                "source_issue_updated_at",
                "sync_target_issue_key",
            ):
                if getattr(req, field, None) in (None, "", []):
                    setattr(req, field, getattr(incoming, field))
        if (reference.source_id, reference.source_version) not in {(s.source_id, s.source_version) for s in req.sources}:
            req.sources.append(reference)
        counts["updated" if changed else "unchanged"] += 1
    # Parent links are interpreted within this extracted batch, then translated to stable display IDs.
    # They never establish a cross-import identity match.
    for req, parent_id in added_parents:
        if incoming_id_counts[parent_id] == 1 and resolved_ids.get(parent_id) != req.id:
            req.parent_requirement_id = resolved_ids.get(parent_id)
    retiring = scope - targeted
    if retiring and not decision.confirm_retirements:
        raise HTTPException(422, "Confirm the listed retirements within the update scope before applying.")
    for uid in current:
        if uid not in retiring:
            continue
        req = current[uid]
        req.lifecycle_status = "retired"
        retired.append(req)
    active = [r for r in active if r.requirement_uid not in retiring]
    counts["retired"] = len(retiring)
    counts["active"] = len(active)
    return active, retired, counts
