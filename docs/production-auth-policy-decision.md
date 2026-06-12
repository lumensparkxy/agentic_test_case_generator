# Production Auth Policy Decision

Status: accepted for implementation by #51.

Issue: [#50](https://github.com/lumensparkxy/agentic_test_case_generator/issues/50)

Date: 2026-06-12

## Decision

Production protected endpoints must accept Firebase ID tokens only. Backend-issued
JWTs and the `/auth/google/login` backend-token exchange are compatibility paths
for local development, deterministic E2E tests, and migration only.

The implementation issue should add one explicit backend environment variable:

```text
AUTH_TOKEN_MODE=firebase-only
AUTH_TOKEN_MODE=firebase-or-backend-jwt
```

`firebase-only` is the production mode and the safe runtime default. In this
mode:

- `get_current_user` verifies bearer tokens as Firebase ID tokens through the
  Firebase Admin SDK.
- Backend-issued JWTs are rejected on protected endpoints with 401 responses.
- `/auth/google/login` does not mint backend JWTs and returns a clear disabled
  response.
- Firebase UID is the canonical user subject for protected workflows.
- Revocation checks stay enabled for Firebase ID tokens.

`firebase-or-backend-jwt` is an explicit local/test compatibility mode. In this
mode:

- Firebase ID tokens remain accepted.
- Backend-issued JWTs remain accepted for local scripts and Playwright E2E.
- `/auth/google/login` may continue to exchange a Google credential for a
  backend-issued JWT.
- This mode must be documented as unsuitable for production deployments.

Do not add a production `backend-jwt-only` mode. Backend JWT support exists only
to keep local and test workflows deterministic while Firebase-backed production
auth is enforced.

## Evidence

The current backend already has both token paths:

- `backend/app/auth/firebase_auth.py` verifies Firebase ID tokens with
  `firebase_admin.auth.verify_id_token(..., check_revoked=True)`.
- `backend/app/auth/jwt_auth.py` currently tries backend JWT decoding first and
  then falls back to Firebase ID token verification.
- `backend/app/routers/auth.py` exposes `/auth/google/login`, which verifies a
  Google credential and mints a backend JWT.
- `frontend/src/App.jsx` obtains `firebaseUser.getIdToken()` when Firebase Auth
  is configured, verifies it with `/auth/me`, and attaches it to authenticated
  API requests.
- `frontend/src/App.jsx`, `frontend/e2e/support/auth.js`,
  `scripts/e2e_api_verify.py`, and `scripts/e2e_playwright_workflow.py` can use
  stored or minted backend JWTs for local deterministic sessions.

Firebase documentation supports this direction: the Admin SDK verifies Firebase
ID token signatures, expiry, and issuer/audience claims, revocation checks are a
separate server-side option, and Firebase session cookies are available as an
optional browser-session pattern. This project should keep bearer Firebase ID
tokens as the production policy and treat session cookies as a future browser
optimization, not a requirement for #51.

References:

- [Firebase Admin: Verify ID Tokens](https://firebase.google.com/docs/auth/admin/verify-id-tokens)
- [Firebase Admin: Manage User Sessions](https://firebase.google.com/docs/auth/admin/manage-sessions)
- [Firebase Admin: Manage Session Cookies](https://firebase.google.com/docs/auth/admin/manage-cookies)

## Required Code Changes For #51

`backend/app/config.py`

- Add `auth_token_mode` to `AuthSettings`.
- Parse `AUTH_TOKEN_MODE`.
- Accept only `firebase-only` and `firebase-or-backend-jwt`.
- Default to `firebase-only` when the variable is omitted or invalid.

`backend/app/auth/jwt_auth.py`

- Make `get_current_user` branch on `AuthSettings.auth_token_mode`.
- In `firebase-only`, call `verify_firebase_access_token` directly and do not
  attempt backend JWT decoding.
- In `firebase-or-backend-jwt`, preserve compatibility with backend JWTs and
  Firebase ID tokens.
- Keep expired backend JWTs as hard failures only in compatibility mode.
- Add focused tests for Firebase-only rejection of backend JWTs and compatibility
  acceptance of backend JWTs.

`backend/app/auth/firebase_auth.py`

- Keep direct Firebase Admin SDK verification.
- Keep `check_revoked=True`.
- Preserve current claim normalization into `AuthUser`.

`backend/app/routers/auth.py`

- Gate `/auth/google/login` by `AUTH_TOKEN_MODE`.
- In `firebase-only`, return a clear disabled response instead of minting a
  backend JWT.
- In `firebase-or-backend-jwt`, preserve the existing Google credential exchange
  behavior for local/test compatibility.
- Keep `/auth/me` as the verification endpoint used by the frontend session
  flow.

`frontend/src/App.jsx`

- Keep Firebase provider sign-in as the production path.
- Continue sending Firebase ID tokens from `firebaseAuth.currentUser.getIdToken()`
  when Firebase Auth is configured.
- Treat stored local JWT session restoration as compatibility behavior that only
  works when the backend is running `firebase-or-backend-jwt`.
- Keep the 401 path clearing local auth state when stored compatibility tokens no
  longer pass backend verification.

E2E and script changes:

- `frontend/e2e/support/auth.js` should keep local JWT minting but document that
  the backend under test must be in `AUTH_TOKEN_MODE=firebase-or-backend-jwt`.
- `scripts/e2e_api_verify.py` and `scripts/e2e_playwright_workflow.py` should
  fail early or print a clear setup message when no `AUTH_TOKEN` is provided and
  the backend is not running compatibility mode.
- Mocked frontend E2E specs that route `/auth/me` can stay unchanged unless they
  switch to a real backend.

## Documentation Changes For #51

Update these documents when enforcement is implemented:

- `README.md`: describe `AUTH_TOKEN_MODE`, production `firebase-only`, local/E2E
  compatibility, and the fact that `/auth/google/login` is compatibility-only.
- `.env.example`: show the local developer value explicitly and warn against
  using it in production, or default to `firebase-only` and add an E2E setup
  note.
- `scripts/deploy_cloud_run.sh`: set or require `AUTH_TOKEN_MODE=firebase-only`
  for Cloud Run deployments.
- `docs/codebase/STACK.md`: list `AUTH_TOKEN_MODE`.
- `docs/codebase/INTEGRATIONS.md`: mark backend-issued JWT and
  `/auth/google/login` as compatibility-only.
- `docs/codebase/TESTING.md`: document the local/E2E compatibility requirement.

## Migration Behavior

1. Merge this decision record.
2. Update #51 to reference `AUTH_TOKEN_MODE` and remove its blocker.
3. Implement `firebase-only` as the safe runtime default.
4. Keep local/E2E workflows green by setting `AUTH_TOKEN_MODE=firebase-or-backend-jwt`
   only in local/test contexts.
5. Deploy production with `AUTH_TOKEN_MODE=firebase-only`.
6. After real deployments no longer call `/auth/google/login`, consider a future
   cleanup issue to remove the route and backend JWT login exchange entirely.

## Non-Goals

- Do not change token validation behavior in #50.
- Do not add Firebase session cookies in #51 unless a later issue explicitly
  chooses that browser-session model.
- Do not store real user tokens or operational credentials in docs, tests, or
  fixtures.
