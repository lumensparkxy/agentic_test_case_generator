from typing import List, Optional, Dict, Any
import logging
import re
from ..models import Requirement
from ..config import get_settings
from ..adk_client import run_requirement_extraction_loop_sync, run_requirement_refinement_sync

MAX_ITERATIONS = 3


def extract_requirements(text: str) -> List[Requirement]:
    """
    Multi-agent ADK loop for requirement extraction:
    1. ExtractorAgent: Parses document and extracts candidate requirements
    2. ReviewerAgent: Validates quality and either approves or suggests improvements  
    3. RefinerAgent: Refines based on feedback or exits when approved
    Loop continues until requirements pass review or max iterations reached.
    """
    settings = get_settings()
    
    logging.info("Starting ADK multi-agent requirement extraction loop...")
    
    # Run the ADK agent loop
    extracted = run_requirement_extraction_loop_sync(
        document_text=text,
        model=settings.model_name,
        max_iterations=MAX_ITERATIONS
    )
    
    if extracted:
        logging.info(f"ADK loop extracted {len(extracted)} requirements successfully.")
        return _convert_to_requirements(extracted)
    
    # Fallback to heuristic if ADK fails
    logging.warning("ADK extraction returned empty; using enhanced heuristic fallback.")
    candidates = _heuristic_extract(text)
    return _finalize_requirements(candidates)


def refine_requirements(existing_requirements: List[Dict[str, Any]], feedback: str) -> List[Requirement]:
    """
    Refine existing requirements based on human feedback using ADK agent loop.
    """
    settings = get_settings()
    
    logging.info(f"Refining {len(existing_requirements)} requirements with feedback: {feedback[:100]}...")
    
    # Run the ADK refinement agent
    refined = run_requirement_refinement_sync(
        existing_requirements=existing_requirements,
        feedback=feedback,
        model=settings.model_name,
    )
    
    if refined:
        logging.info(f"ADK refinement produced {len(refined)} requirements.")
        return _convert_to_requirements(refined)
    
    # Fallback: return original requirements if refinement fails
    logging.warning("ADK refinement returned empty; returning original requirements.")
    return _convert_to_requirements(existing_requirements)


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
        text = _clean_requirement_text(text)
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
        clean_text = _clean_requirement_text(line)
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
    if not text:
        return ""
    
    # Remove markdown bold/italic
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    
    # Remove leading markers
    text = re.sub(r'^[-*•│├└]\s*', '', text)
    text = re.sub(r'^\d+\.\s*', '', text)
    
    # Remove stub markers
    text = text.replace(" (stub)", "").replace("(stub)", "")
    
    # Remove leading/trailing colons
    text = text.strip().strip(':').strip()
    
    return text


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
        r'api[_-]?key|environment|config',
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
        r'api[_-]?key|secret|password|credential',
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
        r'^(upload|download|export|import|parse|extract|process|generate|create|add|view|display|show|save|load|send|validate|authenticate)',
    ]
    feature_start_re = re.compile('|'.join(feature_start_patterns), re.IGNORECASE)
    
    candidates: List[str] = []
    in_features_section = False
    
    for i, line in enumerate(raw_lines):
        lower = line.lower()
        
        # Track document sections
        if re.match(r'^#+\s*(features?|capabilities|functionality)', lower):
            in_features_section = True
            continue
        elif re.match(r'^#+\s', line):
            in_features_section = False
            continue
        
        # Skip obvious noise
        if noise_re.search(line):
            continue
        
        # Skip very short or very long lines
        if len(line) < 15 or len(line) > 300:
            continue
        
        # Clean the line
        cleaned = _clean_requirement_text(line)
        if not cleaned or len(cleaned) < 15:
            continue
        
        # Check if it's valid after cleaning
        if _is_noise(cleaned):
            continue
        
        # Score this candidate
        score = 0
        
        # In features section = high score
        if in_features_section:
            score += 3
        
        # Has requirement verb patterns = high score
        if requirement_re.search(cleaned):
            score += 4
        
        # Starts with action verb = good feature candidate
        if feature_start_re.match(cleaned):
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
    action_match = re.match(r'^(upload|download|export|import|parse|extract|process|generate|create|add|view|display|show|save|load|send|validate|authenticate|allow|enable|support|provide)\s+(.+)', text, re.IGNORECASE)
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
