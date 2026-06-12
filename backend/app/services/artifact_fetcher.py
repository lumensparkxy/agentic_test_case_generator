import ipaddress
import socket
import ssl
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

import certifi

MAX_FETCH_BYTES = 512 * 1024
FETCH_TIMEOUT_SECONDS = 8
MAX_REDIRECTS = 3


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler, HTTPSHandler(context=_SSL_CONTEXT))


def _address_is_blocked(address: ipaddress._BaseAddress) -> bool:
    return bool(address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast or address.is_unspecified)


def _resolve_hostname_addresses(hostname: str, resolver: Optional[Any] = None) -> Tuple[list[ipaddress._BaseAddress], Optional[str]]:
    resolver_fn = resolver or socket.getaddrinfo
    try:
        resolved = resolver_fn(hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        return [], f"Could not resolve artifact hostname: {exc}"

    addresses: list[ipaddress._BaseAddress] = []
    for result in resolved:
        sockaddr = result[4]
        if not sockaddr:
            continue
        raw_address = sockaddr[0]
        try:
            addresses.append(ipaddress.ip_address(raw_address))
        except ValueError:
            continue

    if not addresses:
        return [], "Could not resolve artifact hostname to an IP address."
    return addresses, None


def is_safe_artifact_url(url: str, *, resolver: Optional[Any] = None) -> Tuple[bool, Optional[str]]:
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

    if address and _address_is_blocked(address):
        return False, "Private or non-routable artifact URLs are blocked."

    if not address:
        resolved_addresses, resolve_error = _resolve_hostname_addresses(hostname, resolver=resolver)
        if resolve_error:
            return False, resolve_error
        if any(_address_is_blocked(resolved_address) for resolved_address in resolved_addresses):
            return False, "Artifact hostname resolves to a private or non-routable address."

    return True, None


def fetch_artifact(
    url: str,
    *,
    timeout: int = FETCH_TIMEOUT_SECONDS,
    max_bytes: int = MAX_FETCH_BYTES,
) -> Dict[str, Any]:
    current_url = str(url)
    redirects_followed = 0

    try:
        while True:
            safe, reason = is_safe_artifact_url(current_url)
            if not safe:
                return {
                    "url": current_url,
                    "status": "Skipped",
                    "content_type": None,
                    "text": None,
                    "error": reason,
                }

            request = Request(
                current_url,
                headers={
                    "User-Agent": "AgenticTestCaseGenerator/1.0",
                    "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.1",
                },
                method="GET",
            )

            try:
                response = _NO_REDIRECT_OPENER.open(request, timeout=timeout)
            except HTTPError as exc:
                if exc.code in {301, 302, 303, 307, 308}:
                    location = exc.headers.get("Location")
                    if not location:
                        return {
                            "url": current_url,
                            "status": "Unavailable",
                            "content_type": None,
                            "text": None,
                            "error": f"HTTP {exc.code} redirect missing Location header",
                        }
                    redirects_followed += 1
                    if redirects_followed > MAX_REDIRECTS:
                        return {
                            "url": current_url,
                            "status": "Unavailable",
                            "content_type": None,
                            "text": None,
                            "error": f"Artifact exceeded the {MAX_REDIRECTS} redirect limit.",
                        }
                    current_url = urljoin(current_url, location)
                    continue
                raise

            with response:
                content_type_header = response.headers.get("Content-Type", "")
                content_type = content_type_header.split(";", 1)[0].strip().lower() or None
                payload = response.read(max_bytes + 1)
            break
    except HTTPError as exc:
        return {
            "url": current_url,
            "status": "Unavailable",
            "content_type": None,
            "text": None,
            "error": f"HTTP {exc.code}",
        }
    except URLError as exc:
        return {
            "url": current_url,
            "status": "Unavailable",
            "content_type": None,
            "text": None,
            "error": str(exc.reason),
        }
    except Exception as exc:  # pragma: no cover - defensive catch for network stack errors
        return {
            "url": current_url,
            "status": "Unavailable",
            "content_type": None,
            "text": None,
            "error": str(exc),
        }

    if len(payload) > max_bytes:
        return {
            "url": current_url,
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
        "url": current_url,
        "status": "Analyzed",
        "content_type": content_type,
        "text": text,
        "error": None,
    }
