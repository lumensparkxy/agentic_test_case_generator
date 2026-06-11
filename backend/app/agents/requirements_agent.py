from typing import List, Dict, Any, Optional
import logging
import re
from ..models import Requirement, WorkflowSettings
from ..config import get_settings
from ..adk_client import (
    DEFAULT_REQUIREMENT_MAX_ITERATIONS,
    DEFAULT_REQUIREMENT_THRESHOLD,
    run_requirement_extraction_workflow_sync,
    run_requirement_refinement_workflow_sync,
)
from ..utils.requirements_text import clean_requirement_text, normalize_requirement_text
from ..utils.workflow_diagnostics import public_workflow_diagnostics

MAX_ITERATIONS = DEFAULT_REQUIREMENT_MAX_ITERATIONS


def _get_model_settings_or_none() -> Any | None:
    try:
        return get_settings()
    except RuntimeError as exc:
        if "GEMINI_API_KEY" not in str(exc):
            raise
        logging.warning("Model credentials are unavailable; using deterministic requirement fallback.")
        return None


def extract_requirements(
    text: str,
    document_count: int = 1,
    workflow_settings: Optional[WorkflowSettings] = None,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Multi-agent ADK loop for requirement extraction:
    1. ExtractorAgent: Parses document and extracts candidate requirements
    2. ReviewerAgent: Validates quality and either approves or suggests improvements  
    3. RefinerAgent: Refines based on feedback or exits when approved
    Loop continues until requirements pass review or max iterations reached.
    """
    settings = _get_model_settings_or_none()
    if settings is None:
        candidates = _heuristic_extract(text)
        requirements = _finalize_requirements(candidates)
        fallback_workflow = _build_fallback_workflow(
            requirements=requirements,
            summary="Model credentials are unavailable. Heuristic fallback produced draft requirements that still need approval.",
            document_count=document_count,
        )
        return _build_workflow_response(requirements, fallback_workflow, document_count=document_count)
    
    logging.info("Starting ADK multi-agent requirement extraction loop...")
    
    # Run the ADK agent loop
    workflow = run_requirement_extraction_workflow_sync(
        document_text=text,
        model=settings.model_name,
        max_iterations=workflow_settings.max_iterations if workflow_settings and workflow_settings.max_iterations else MAX_ITERATIONS,
        document_count=document_count,
        workflow_settings=workflow_settings,
        actor_user_id=actor_user_id,
    )
    
    extracted = workflow.get("requirements", [])
    if extracted:
        requirements = _convert_to_requirements(extracted)
        logging.info(f"ADK loop extracted {len(requirements)} requirements successfully.")
        return _build_workflow_response(requirements, workflow, document_count=document_count)
    
    # Fallback to heuristic if ADK fails
    logging.warning("ADK extraction returned empty; using enhanced heuristic fallback.")
    candidates = _heuristic_extract(text)
    requirements = _finalize_requirements(candidates)
    fallback_workflow = _build_fallback_workflow(
        requirements=requirements,
        summary="Primary extraction workflow returned no requirements. Heuristic fallback produced draft requirements that still need approval.",
        document_count=document_count,
        existing_review=workflow.get("review"),
        existing_history=workflow.get("iteration_history"),
        existing_settings=workflow.get("workflow_settings"),
        existing_diagnostics=workflow.get("workflow_diagnostics"),
    )
    return _build_workflow_response(requirements, fallback_workflow, document_count=document_count)


def refine_requirements(
    existing_requirements: List[Dict[str, Any]],
    feedback: str,
    workflow_settings: Optional[WorkflowSettings] = None,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Refine existing requirements based on human feedback using ADK agent loop.
    """
    settings = _get_model_settings_or_none()
    if settings is None:
        requirements = _convert_to_requirements(existing_requirements)
        fallback_workflow = _build_fallback_workflow(
            requirements=requirements,
            summary="Model credentials are unavailable. The previous requirement draft was restored and needs re-review.",
            document_count=1,
        )
        return _build_workflow_response(requirements, fallback_workflow, document_count=1)
    
    logging.info(f"Refining {len(existing_requirements)} requirements with feedback: {feedback[:100]}...")
    
    # Run the ADK refinement agent
    workflow = run_requirement_refinement_workflow_sync(
        existing_requirements=existing_requirements,
        feedback=feedback,
        model=settings.model_name,
        max_iterations=workflow_settings.max_iterations if workflow_settings and workflow_settings.max_iterations else MAX_ITERATIONS,
        workflow_settings=workflow_settings,
        actor_user_id=actor_user_id,
    )
    
    refined = workflow.get("requirements", [])
    if refined:
        requirements = _convert_to_requirements(refined)
        logging.info(f"ADK refinement produced {len(requirements)} requirements.")
        return _build_workflow_response(requirements, workflow, document_count=1)
    
    # Fallback: return original requirements if refinement fails
    logging.warning("ADK refinement returned empty; returning original requirements.")
    requirements = _convert_to_requirements(existing_requirements)
    fallback_workflow = _build_fallback_workflow(
        requirements=requirements,
        summary="Requirement refinement returned no updated requirements. The previous approved draft was restored and needs re-review.",
        document_count=1,
        existing_review=workflow.get("review"),
        existing_history=workflow.get("iteration_history"),
        existing_settings=workflow.get("workflow_settings"),
        existing_diagnostics=workflow.get("workflow_diagnostics"),
    )
    return _build_workflow_response(requirements, fallback_workflow, document_count=1)


def _build_workflow_response(
    requirements: List[Requirement],
    workflow: Dict[str, Any],
    document_count: int,
) -> Dict[str, Any]:
    resolved_settings = dict(workflow.get("workflow_settings") or {})
    threshold = int(resolved_settings.get("approval_threshold") or DEFAULT_REQUIREMENT_THRESHOLD)
    coverage_metrics = dict(workflow.get("coverage_metrics") or {})
    if not coverage_metrics:
        coverage_metrics = _compute_requirement_coverage_metrics(requirements, document_count)

    review = dict(workflow.get("review") or {})
    if not review:
        review = {
            "approved": False,
            "score": 0,
            "threshold": threshold,
            "summary": "Requirement review data is unavailable.",
            "blocking_issues": ["No structured requirement review was captured."],
            "suggestions": ["Re-run the requirement workflow to regenerate structured review output."],
            "unmet_criteria": ["Structured review is required before progressing."],
        }

    return {
        "requirements": requirements,
        "approved": bool(workflow.get("approved", False)),
        "review": review,
        "iteration_history": list(workflow.get("iteration_history") or []),
        "coverage_metrics": coverage_metrics,
        "workflow_settings": resolved_settings,
        "workflow_diagnostics": public_workflow_diagnostics(dict(workflow.get("workflow_diagnostics") or {})),
    }


def _build_fallback_workflow(
    requirements: List[Requirement],
    summary: str,
    document_count: int,
    existing_review: Dict[str, Any] | None = None,
    existing_history: List[Dict[str, Any]] | None = None,
    existing_settings: Dict[str, Any] | None = None,
    existing_diagnostics: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    resolved_settings = dict(existing_settings or {})
    threshold = int(resolved_settings.get("approval_threshold") or DEFAULT_REQUIREMENT_THRESHOLD)
    blocking_issues = list((existing_review or {}).get("blocking_issues") or [])
    suggestions = list((existing_review or {}).get("suggestions") or [])
    unmet_criteria = list((existing_review or {}).get("unmet_criteria") or [])

    blocking_issues.append("Heuristic fallback was used instead of a completed review/refine loop.")
    suggestions.append("Review and refine the fallback requirements before moving to the next phase.")
    unmet_criteria.append("Approval threshold was not reached through the structured requirement loop.")

    review = {
        "approved": False,
        "score": max(0, min(int((existing_review or {}).get("score", 0) or 0), max(0, threshold - 5))),
        "threshold": threshold,
        "summary": summary,
        "blocking_issues": _dedupe_strings(blocking_issues),
        "suggestions": _dedupe_strings(suggestions),
        "unmet_criteria": _dedupe_strings(unmet_criteria),
    }

    coverage_metrics = _compute_requirement_coverage_metrics(requirements, document_count)
    history = list(existing_history or [])
    if not history:
        history.append(
            {
                "iteration": 1,
                "actor": "FallbackReview",
                "approved": False,
                "score": review["score"],
                "threshold": review["threshold"],
                "summary": review["summary"],
                "artifact_count": len(requirements),
                "artifact_ids": [req.id for req in requirements[:8]],
                "blocking_issues": review["blocking_issues"],
                "suggestions": review["suggestions"],
            }
        )

    diagnostics = dict(existing_diagnostics or {})
    diagnostics["status"] = "fallback"
    diagnostics["used_fallback"] = True
    diagnostics["failure_reason"] = diagnostics.get("failure_reason") or "fallback_generated_artifacts"
    warnings = list(diagnostics.get("warnings") or [])
    fallback_warning = "Requirement fallback produced draft artifacts that still require review approval."
    if fallback_warning not in warnings:
        warnings.append(fallback_warning)
    diagnostics["warnings"] = warnings

    return {
        "approved": False,
        "review": review,
        "iteration_history": history,
        "coverage_metrics": coverage_metrics,
        "workflow_settings": resolved_settings,
        "workflow_diagnostics": public_workflow_diagnostics(diagnostics),
    }


def _compute_requirement_coverage_metrics(requirements: List[Requirement], document_count: int) -> Dict[str, Any]:
    total = len(requirements)
    unique_count = len({req.text.strip().lower() for req in requirements if req.text})
    shall_format_count = sum(1 for req in requirements if req.text.strip().lower().startswith("the system shall"))
    average_word_count = round(sum(len(req.text.split()) for req in requirements) / total, 2) if total else 0.0

    return {
        "document_count": max(1, document_count),
        "total_requirements": total,
        "unique_requirements": unique_count,
        "duplicate_requirements": max(0, total - unique_count),
        "shall_format_count": shall_format_count,
        "shall_format_ratio": round(shall_format_count / total, 2) if total else 0.0,
        "average_word_count": average_word_count,
        "requirements_per_document": round(total / max(1, document_count), 2),
    }


def _dedupe_strings(items: List[str]) -> List[str]:
    seen = set()
    deduped: List[str] = []
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _convert_to_requirements(extracted: List[Dict[str, Any]]) -> List[Requirement]:
    """Convert extracted dicts to Requirement objects."""
    requirements: List[Requirement] = []
    seen = set()
    
    for i, item in enumerate(extracted):
        # Handle both dict format and string format
        if isinstance(item, dict):
            req_id = item.get("id", f"REQ-{i+1:03d}")
            text = item.get("text", "")
        else:
            req_id = f"REQ-{i+1:03d}"
            text = str(item)
        
        if not text:
            continue
        
        # Clean and deduplicate
        text = normalize_requirement_text(text)
        if not text or len(text) < 20:
            continue
        
        normalized = text.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        
        # Ensure proper ID format
        if not req_id.startswith("REQ-"):
            req_id = f"REQ-{len(requirements)+1:03d}"
        
        requirements.append(Requirement(id=req_id, text=text))
    
    # Re-number to ensure sequential IDs
    for i, req in enumerate(requirements):
        req.id = f"REQ-{i+1:03d}"
    
    return requirements


def _finalize_requirements(candidates: List[str]) -> List[Requirement]:
    """Clean up and format final requirements list."""
    requirements: List[Requirement] = []
    seen = set()
    
    for line in candidates:
        clean_text = normalize_requirement_text(line)
        if not clean_text:
            continue
        
        # Skip if too short or looks like noise
        if len(clean_text) < 20:
            continue
        if _is_noise(clean_text):
            continue
        
        # Deduplicate
        normalized = clean_text.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        
        requirements.append(Requirement(
            id=f"REQ-{len(requirements) + 1:03d}",
            text=clean_text
        ))
    
    return requirements


def _clean_requirement_text(text: str) -> str:
    """Clean up formatting artifacts from requirement text."""
    return clean_requirement_text(text)


def _is_noise(text: str) -> bool:
    """Check if text looks like noise rather than a requirement."""
    lower = text.lower()
    
    noise_patterns = [
        r'^(created|updated|author|version|date):?\s',
        r'^\d{4}[-/]\d{2}[-/]\d{2}',  # Dates
        r'^(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d',
        r'[│├└─]+',  # Tree structure characters
        r'^#\s',  # Markdown headings
        r'\.py$|\.js$|\.md$|\.json$',  # File extensions
        r'^[a-z_]+/$',  # Directory names
        r'^(purpose|overview|introduction|scope):?\s*$',
        r'^\*\*[^*]+\*\*:?\s*$',  # Just a bold heading
        r'^(note|notes|tip|warning):',
        r'\b(?:api[_-]?key|environment|config)\b',
        r'^\d+\.\s*\*\*[^:]+\*\*:',  # Numbered heading like "2. **Quality**:"
    ]
    
    for pattern in noise_patterns:
        if re.search(pattern, lower):
            return True
    
    return False


def _heuristic_extract(text: str) -> List[str]:
    """
    Enhanced heuristic extraction when agents fail.
    Uses semantic patterns to identify actual requirements.
    """
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    
    # Strong noise indicators - definitely NOT requirements
    noise_patterns = [
        r'^#+\s',  # Markdown headings
        r'^\d+\)\s*',  # Numbered steps like "1)"
        r'^(note|notes|tip|warning|important|caution):',
        r'`[^`]+`',  # Any inline code
        r'^(http|https)://',  # URLs
        r'[│├└─┌┐┘┴┬]+',  # Tree/box drawing characters
        r'^[A-Z][A-Z0-9_]{2,}',  # CONSTANTS or ENV_VARS
        r'\.(py|js|ts|md|json|txt|yml|yaml|env|sh)[\s:,)]?',  # File extensions
        r'^(created|updated|author|version|date|last\s+modified):',
        r'^\d{4}[-/]\d{2}[-/]\d{2}',  # Dates YYYY-MM-DD
        r'^(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d',
        r'^\*\*[^*]+\*\*:?\s*$',  # Just bold text (heading)
        r'^(purpose|overview|introduction|scope|background|context):?\s*$',
        r'api[_-]?key|secret',
        r'\(default:',  # Config defaults
        r'is a stub',  # Implementation notes
        r'^\s*(#|//|/\*)',  # Code comments
        r'^(install|setup|configure|run|start|build|deploy)\s+(the|a|your)',
        r'reports?/|src/|lib/|dist/|node_modules|__pycache__',  # Paths
        r'uvicorn|npm|pip|python|node',  # Commands
        r'in-memory|processed|stored',  # Implementation details
    ]
    noise_re = re.compile('|'.join(noise_patterns), re.IGNORECASE)
    
    # Strong requirement indicators
    requirement_verbs = [
        r'\b(shall|should|must|will|can|may)\s+(be\s+able\s+to|allow|enable|support|provide|display|show|generate|create|delete|update|save|load|send|receive|validate|verify|authenticate|authorize)',
        r'\buser\s+(can|shall|should|must|will)\b',
        r'\bsystem\s+(shall|should|must|will)\b',
    ]
    requirement_re = re.compile('|'.join(requirement_verbs), re.IGNORECASE)
    
    # Feature-like patterns (action verbs at start)
    feature_start_patterns = [
        r'^(allow|enable|support|provide|prevent|lock|keep|require|sort|upload|download|export|import|parse|extract|process|generate|create|add|view|display|show|save|load|send|validate|authenticate)',
    ]
    feature_start_re = re.compile('|'.join(feature_start_patterns), re.IGNORECASE)
    
    candidates: List[str] = []
    in_features_section = False
    
    for i, line in enumerate(raw_lines):
        lower = line.lower()
        
        # Track document sections
        if re.match(r'^#+\s*(?:functional\s+)?(?:features?|capabilities|functionality)\b', lower):
            in_features_section = True
            continue
        elif re.match(r'^#+\s', line):
            in_features_section = False
            continue
        
        # Clean the line
        cleaned = _clean_requirement_text(line)
        if not cleaned or len(cleaned) < 15:
            continue

        has_requirement_signal = bool(requirement_re.search(cleaned))
        has_feature_signal = bool(feature_start_re.match(cleaned))

        # Skip obvious noise unless the line is already a strong requirement.
        if noise_re.search(line) and not has_requirement_signal and not (in_features_section and has_feature_signal):
            continue

        # Skip very short or very long lines
        if len(line) < 15 or len(line) > 300:
            continue
        
        # Check if it's valid after cleaning
        if _is_noise(cleaned) and not has_requirement_signal:
            continue
        
        # Score this candidate
        score = 0
        
        # In features section = high score
        if in_features_section:
            score += 3
        
        # Has requirement verb patterns = high score
        if has_requirement_signal:
            score += 4
        
        # Starts with action verb = good feature candidate
        if has_feature_signal:
            score += 3
        
        # Is a bullet point in features section = boost
        if line.startswith(('-', '*', '•')) and in_features_section:
            score += 2
        
        if score >= 3:
            # Format as proper requirement
            formatted = _format_as_requirement(cleaned)
            if formatted:
                candidates.append((score, formatted))
    
    # Sort by score descending, take top results
    candidates.sort(key=lambda x: x[0], reverse=True)
    result = [c[1] for c in candidates[:15]]  # Limit to 15 requirements
    
    # Deduplicate
    seen = set()
    unique: List[str] = []
    for r in result:
        normalized = r.lower()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(r)
    
    return unique


def _format_as_requirement(text: str) -> Optional[str]:
    """Format text as a proper requirement statement."""
    if not text:
        return None
    
    # Already well-formed
    if re.match(r'^(the\s+system\s+(shall|should|must|will)|user\s+(can|shall|should))', text, re.IGNORECASE):
        return text
    
    # Starts with action verb - convert to "The system shall [verb]"
    action_match = re.match(r'^(upload|download|export|import|parse|extract|process|generate|create|add|view|display|show|save|load|send|validate|authenticate|allow|enable|support|provide|prevent|lock|keep|require|sort)\s+(.+)', text, re.IGNORECASE)
    if action_match:
        verb = action_match.group(1).lower()
        rest = action_match.group(2)
        return f"The system shall {verb} {rest}"
    
    # Starts with noun phrase describing capability
    if text[0].isupper() and not re.match(r'^(The|A|An)\s', text):
        # Check if it reads like a feature description
        if re.search(r'\b(support|capability|feature|function|ability)\b', text, re.IGNORECASE):
            return f"The system shall provide {text[0].lower()}{text[1:]}"
    
    return None
