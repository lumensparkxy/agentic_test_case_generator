import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_CONTRACT_ENDPOINTS } from "../api/generated/api-contracts";

const statusError = "Could not check extraction status. Check status again before starting another extraction.";

export default function useRequirementExtractionStatus({ enabled, projectId, identity, request, loadPending, onReleased }) {
	const scope = enabled && projectId && identity ? JSON.stringify([identity, projectId]) : "";
	const owner = useMemo(() => ({ scope, read: null, action: null }), [scope]);
	const confirmedScopes = useRef(new Set());
	const activeOwner = useRef(owner);
	activeOwner.current = owner;
	const callbacks = useRef(null);
	callbacks.current = { request, loadPending, onReleased };
	const [statusState, setStatus] = useState(null);
	const [actionState, setAction] = useState(null);
	const current = statusState?.owner === owner ? statusState : null;
	const action = actionState?.owner === owner ? actionState : null;
	const data = current?.data;
	const attempt = data?.attempt || null;
	const managed = Boolean(scope && data?.enabled !== false);
	const canRelease = Boolean(
		managed &&
		attempt?.state === "interrupted" &&
		attempt.operation_id &&
		Number.isInteger(attempt.fence) &&
		!current?.error &&
		!action?.busy
	);

	const readStatus = useCallback(async () => {
		if (!scope || activeOwner.current !== owner) return null;
		owner.read?.abort();
		const controller = new AbortController();
		owner.read = controller;
		try {
			const endpoint = API_CONTRACT_ENDPOINTS.requirementExtractionStatus;
			const path = endpoint.path.replace("{project_id}", encodeURIComponent(projectId));
			const response = await callbacks.current.request(path, { method: endpoint.method, signal: controller.signal });
			const unsupported = response.status === 404 && !confirmedScopes.current.has(scope);
			if (!response.ok && !unsupported) throw new Error(statusError);
			const result = unsupported ? { enabled: false, blocked: false, attempt: null } : await response.json();
			if (typeof result?.enabled !== "boolean" || typeof result.blocked !== "boolean") throw new Error(statusError);
			if (activeOwner.current !== owner || controller.signal.aborted) return null;
			if (result.enabled) confirmedScopes.current.add(scope);
			else if (!unsupported) confirmedScopes.current.delete(scope);
			setStatus({ owner, data: result });
			return result;
		} catch {
			if (activeOwner.current === owner && !controller.signal.aborted) {
				setStatus((previous) => ({ owner, data: previous?.owner === owner ? previous.data : null, error: statusError }));
			}
			return null;
		} finally {
			if (owner.read === controller) owner.read = null;
		}
	}, [owner, projectId, scope]);

	useEffect(() => {
		void readStatus();
		return () => owner.read?.abort();
	}, [owner, readStatus]);

	const reconcile = async (release = null) => {
		if (!managed || activeOwner.current !== owner || owner.action || (release && (!canRelease || release !== attempt))) return;
		const operation = {};
		owner.action = operation;
		const releaseNote = action?.releaseNote || "";
		setAction({ owner, busy: true, releaseNote });
		let released = false;
		try {
			if (release) {
				const endpoint = API_CONTRACT_ENDPOINTS.releaseRequirementExtraction;
				const path = endpoint.path
					.replace("{project_id}", encodeURIComponent(projectId))
					.replace("{operation_id}", encodeURIComponent(release.operation_id));
				try {
					const response = await callbacks.current.request(path, {
						method: endpoint.method,
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ fence: release.fence }),
					});
					released = response.ok && (await response.json()).released === true;
				} catch {
					// The request can succeed even if its response is lost.
				}
			}
			if (activeOwner.current !== owner) return;
			const [status] = await Promise.all([readStatus(), callbacks.current.loadPending()]);
			if (activeOwner.current !== owner) return;
			if (release && (released || (status?.enabled && status.attempt?.operation_id !== release.operation_id))) {
				callbacks.current.onReleased(release);
				setAction({
					owner,
					releaseNote:
						"The interrupted attempt was released. Provider usage remains unconfirmed. Review any saved import before starting again.",
				});
			} else if (release) {
				setAction({ owner, releaseNote, error: "Could not confirm the release. Check extraction status again." });
			} else {
				setAction({ owner, releaseNote, note: "Review any saved import before starting again." });
			}
		} finally {
			if (owner.action === operation) owner.action = null;
		}
	};

	return {
		managed,
		attempt,
		ready: Boolean(data),
		blocked: managed && (!data || data.blocked || Boolean(attempt) || Boolean(current?.error) || Boolean(action?.busy)),
		sharedBlocked: managed && data?.blocked && !attempt,
		statusError: current?.error || "",
		actionError: action?.error || "",
		note: action?.note || "",
		releaseNote: action?.releaseNote || "",
		busy: Boolean(action?.busy),
		canRelease,
		refresh: readStatus,
		check: () => reconcile(),
		release: () => attempt && reconcile(attempt),
	};
}
