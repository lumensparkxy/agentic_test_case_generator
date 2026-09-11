"""Resolve guidance before endpoint side effects; leave request validation to FastAPI."""

import json
from hashlib import sha256
from fastapi import Depends, Request
from fastapi.concurrency import run_in_threadpool
from ..auth.jwt_auth import get_current_user
from ..models import AuthUser
from .guidance_runtime import prepare_run
from .guidance_service import guidance_scope, enabled


def generation_guidance(stage):
    async def dependency(request: Request, actor: AuthUser = Depends(get_current_user)):
        if "multipart/form-data" in request.headers.get("content-type", ""):
            form = await request.form()
            payload = {k: str(v) for k, v in form.multi_items() if not hasattr(v, "filename")}
            uploads = []
            if enabled("memory", stage):
                for _, value in form.multi_items():
                    if hasattr(value, "filename"):
                        digest = sha256()
                        try:
                            while chunk := await value.read(1024 * 1024):
                                digest.update(chunk)
                        finally:
                            await value.seek(0)
                        uploads.append([value.filename, digest.hexdigest()])
            payload["uploads"] = uploads
            try:
                requirements = json.loads(payload.get("existing_requirements") or "[]")
            except ValueError, TypeError:
                requirements = []  # The endpoint retains its existing validation response.
        else:
            try:
                payload = await request.json()
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            requirements = payload.get("requirements") or []
        requirement_ids = [r.get("id") for r in requirements if isinstance(r, dict) and r.get("id")] if isinstance(requirements, list) else []
        if not requirement_ids and isinstance(payload.get("test_cases"), list):
            requirement_ids = sorted(
                {r for case in payload["test_cases"] if isinstance(case, dict) for r in (case.get("linked_requirement_ids") or []) if isinstance(r, str)}
            )
        manifest = await run_in_threadpool(
            prepare_run,
            stage,
            actor,
            payload.get("project_id") or request.path_params.get("project_id"),
            request.state.request_id,
            requirement_ids=requirement_ids,
            memory_bypass=request.headers.get("X-Knowledge-Bypass", "").lower() == "true",
            input_fingerprint=sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest(),
        )
        request.state.guidance = manifest
        with guidance_scope(manifest):
            yield manifest

    return dependency
