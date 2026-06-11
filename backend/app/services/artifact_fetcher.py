import ipaddress
import ssl
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import certifi

MAX_FETCH_BYTES = 512 * 1024
FETCH_TIMEOUT_SECONDS = 8


def is_safe_artifact_url(url: str) -> Tuple[bool, Optional[str]]:
    parsed = urlparse(str(url).strip())
    if parsed.scheme not in {"http", "https"}:
        return False, "Only http and https artifact URLs are allowed."

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False, "Artifact URL must include a hostname."

    if hostname in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or hostname.endswith(".local"):
        return False, "Local and loopback artifact URLs are blocked."

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None

    if address and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        return False, "Private or non-routable artifact URLs are blocked."

    return True, None


def fetch_artifact(
    url: str,
    *,
    timeout: int = FETCH_TIMEOUT_SECONDS,
    max_bytes: int = MAX_FETCH_BYTES,
) -> Dict[str, Any]:
    safe, reason = is_safe_artifact_url(url)
    if not safe:
        return {
            "url": url,
            "status": "Skipped",
            "content_type": None,
            "text": None,
            "error": reason,
        }

    request = Request(
        url,
        headers={
            "User-Agent": "AgenticTestCaseGenerator/1.0",
            "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.1",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=timeout, context=ssl.create_default_context(cafile=certifi.where())) as response:
            content_type_header = response.headers.get("Content-Type", "")
            content_type = content_type_header.split(";", 1)[0].strip().lower() or None
            payload = response.read(max_bytes + 1)
    except HTTPError as exc:
        return {
            "url": url,
            "status": "Unavailable",
            "content_type": None,
            "text": None,
            "error": f"HTTP {exc.code}",
        }
    except URLError as exc:
        return {
            "url": url,
            "status": "Unavailable",
            "content_type": None,
            "text": None,
            "error": str(exc.reason),
        }
    except Exception as exc:  # pragma: no cover - defensive catch for network stack errors
        return {
            "url": url,
            "status": "Unavailable",
            "content_type": None,
            "text": None,
            "error": str(exc),
        }

    if len(payload) > max_bytes:
        return {
            "url": url,
            "status": "Unavailable",
            "content_type": content_type,
            "text": None,
            "error": f"Artifact exceeded the {max_bytes} byte size limit.",
        }

    if content_type and (content_type.startswith("text/") or "json" in content_type or "xml" in content_type):
        text = payload.decode("utf-8", errors="replace")
    else:
        text = None

    return {
        "url": url,
        "status": "Analyzed",
        "content_type": content_type,
        "text": text,
        "error": None,
    }
