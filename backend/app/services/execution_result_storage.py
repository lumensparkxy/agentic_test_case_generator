"""Store oversized execution results with their snapshot's revision commit."""

import hashlib
import json
import logging
import zlib
from typing import Any

from google.api_core.exceptions import GoogleAPICallError


INLINE_RESULT_BYTES = 384 * 1024
RESULT_PART_BYTES = 256 * 1024
EXACT_RESULT_BYTES = 4 * 1024 * 1024
EXACT_COMPRESSED_BYTES = 2 * 1024 * 1024
SAVED_DIAGNOSTIC_BYTES = 2 * 1024 * 1024
PARTS_FIELD = "execution_result_parts"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _bounded_diagnostics(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    affected = sum(bool(result.get("issues")) for result in results)
    if not affected:
        return results

    per_case = SAVED_DIAGNOSTIC_BYTES // affected
    bounded = []
    for result in results:
        issues = result.get("issues") or []
        if not issues:
            bounded.append(result)
            continue
        kept = min(len(issues), max(1, per_case // 512), 4)
        omitted = len(issues) - kept
        per_issue = per_case // kept
        retained = []
        for index, issue in enumerate(issues[:kept]):
            message = issue["message"].encode("utf-8")
            suffix = f"\n[{omitted} additional diagnostics omitted from saved evidence.]" if omitted and index == kept - 1 else ""
            if len(message) + len(suffix) > per_issue:
                suffix = "\n[Saved diagnostic shortened.]" + suffix
                message = message[: max(0, per_issue - len(suffix))]
            retained.append({**issue, "message": message.decode("utf-8", errors="ignore") + suffix})
        bounded.append({**result, "issues": retained})
    return bounded


def execution_result_writes(snapshot_ref: Any, snapshot: dict[str, Any]) -> list[tuple[Any, dict[str, Any], bool]]:
    results = snapshot["payload"].get("results")
    if not isinstance(results, list):
        return []

    encoded = _json_bytes(results)
    if len(encoded) <= INLINE_RESULT_BYTES:
        return []
    compressed = zlib.compress(encoded, level=3) if len(encoded) <= EXACT_RESULT_BYTES else None
    if compressed is None or len(compressed) > EXACT_COMPRESSED_BYTES:
        compressed = zlib.compress(_json_bytes(_bounded_diagnostics(results)), level=3)
    parts = [compressed[offset : offset + RESULT_PART_BYTES] for offset in range(0, len(compressed), RESULT_PART_BYTES)]
    snapshot[PARTS_FIELD] = {
        "format": "json-zlib-v1",
        "count": len(parts),
        "result_count": len(results),
        "sha256": hashlib.sha256(compressed).hexdigest(),
    }
    del snapshot["payload"]["results"]
    collection = snapshot_ref.collection(PARTS_FIELD)
    return [(collection.document(f"{index:04d}"), {"index": index, "data": part}, False) for index, part in enumerate(parts)]


def restore_execution_results(snapshot_ref: Any, snapshot: dict[str, Any]) -> None:
    if PARTS_FIELD not in snapshot:
        return
    payload = dict(snapshot["payload"])
    snapshot["payload"] = payload
    payload.pop("results", None)
    manifest = snapshot[PARTS_FIELD]
    try:
        if manifest["format"] != "json-zlib-v1":
            raise ValueError("Unknown execution result format")
        parts = [doc.to_dict() for doc in snapshot_ref.collection(PARTS_FIELD).stream()]
        parts.sort(key=lambda part: part["index"])
        if len(parts) != manifest["count"] or any(part["index"] != index for index, part in enumerate(parts)):
            raise ValueError("Incomplete execution result parts")
        compressed = b"".join(part["data"] for part in parts)
        if hashlib.sha256(compressed).hexdigest() != manifest["sha256"]:
            raise ValueError("Execution result checksum mismatch")
        results = json.loads(zlib.decompress(compressed))
        if not isinstance(results, list) or len(results) != manifest["result_count"]:
            raise ValueError("Execution result count mismatch")
    except (GoogleAPICallError, KeyError, TypeError, ValueError, zlib.error) as error:
        logging.warning("Execution results unavailable for snapshot %s (%s)", snapshot["snapshot_id"], type(error).__name__)
        return
    payload["results"] = results
