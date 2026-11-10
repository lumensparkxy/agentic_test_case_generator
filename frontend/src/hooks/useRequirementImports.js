import { useCallback, useEffect, useRef, useState } from "react";

export default function useRequirementImports(request, projectId, enabled, onApplied, identity) {
	const requester = useRef(request);
	const applied = useRef(onApplied);
	const scope = enabled && projectId ? JSON.stringify([identity, projectId]) : "";
	const activeScope = useRef(scope);
	activeScope.current = scope;
	const loadedScope = useRef(scope);
	const epoch = useRef(0);
	const pendingRead = useRef(null);
	const lock = useRef(false);
	const attempt = useRef(null);
	const [preview, setPreview] = useState(null);
	const [pending, setPending] = useState([]);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");
	const [extractionError, setExtractionError] = useState("");
	const [stalePreview, setStalePreview] = useState(false);
	const [message, setMessage] = useState("");
	useEffect(() => {
		requester.current = request;
		applied.current = onApplied;
	}, [request, onApplied]);
	const root = `/projects/${encodeURIComponent(projectId || "")}/requirement-imports`;
	const read = useCallback(async (path, options = {}) => {
		const response = await requester.current(path, options);
		if (!response.ok) {
			let detail;
			try {
				detail = (await response.json()).detail;
			} catch {
				/* Handle non-JSON gateway failures. */
			}
			throw Object.assign(new Error(typeof detail === "string" ? detail : detail?.message || `Request failed (${response.status}).`), {
				code: detail?.code,
			});
		}
		return response.status === 204 ? null : response.json();
	}, []);
	const load = useCallback(async () => {
		if (!scope || activeScope.current !== scope) return null;
		const version = epoch.current;
		pendingRead.current?.abort();
		const controller = new AbortController();
		pendingRead.current = controller;
		const isCurrent = () => activeScope.current === scope && version === epoch.current && !controller.signal.aborted;
		try {
			const rows = await read(root, { signal: controller.signal });
			if (isCurrent()) {
				const imports = Array.isArray(rows) ? rows : [];
				setPending(imports);
				return imports;
			}
		} catch (e) {
			if (isCurrent()) setError(e.message);
		} finally {
			if (pendingRead.current === controller) pendingRead.current = null;
		}
		return null;
	}, [scope, read, root]);
	useEffect(() => {
		epoch.current++;
		loadedScope.current = scope;
		setPreview(null);
		setPending([]);
		setError("");
		setExtractionError("");
		setStalePreview(false);
		setMessage("");
		setBusy(false);
		lock.current = false;
		attempt.current = null;
		void load();
		return () => {
			epoch.current++;
			pendingRead.current?.abort();
		};
	}, [load, scope]);
	const accept = (value) => {
		if (activeScope.current !== scope) return;
		if (!value?.import_id || value.project_id !== projectId || !Array.isArray(value.candidates)) {
			throw new Error("Import review is unavailable. Update the API and UI together before importing.");
		}
		pendingRead.current?.abort();
		setPreview(value);
		setError("");
		setExtractionError("");
		setStalePreview(false);
		setMessage("");
		attempt.current = null;
		setPending((rows) => [value, ...rows.filter((row) => row.import_id !== value.import_id)]);
	};
	const mutate = async (kind, payload) => {
		if (!preview || lock.current || activeScope.current !== scope) return;
		const version = epoch.current;
		const isCurrent = () => activeScope.current === scope && version === epoch.current;
		lock.current = true;
		setBusy(true);
		setError("");
		try {
			if (kind === "apply") {
				const fingerprint = JSON.stringify([preview.import_id, payload]);
				if (attempt.current?.fingerprint !== fingerprint) attempt.current = { fingerprint, key: crypto.randomUUID() };
				const project = await read(`${root}/${preview.import_id}/apply`, {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({ ...payload, idempotency_key: attempt.current.key }),
				});
				if (!isCurrent()) return;
				await applied.current(project);
				if (!isCurrent()) return;
				const counts = project.current_snapshots?.requirements?.payload?.import_changes || {};
				setMessage(
					`${counts.added || 0} added · ${counts.updated || 0} updated · ${counts.retired || 0} retired · ${counts.active || 0} active requirements`
				);
				attempt.current = null;
				setPreview(null);
			} else if (kind === "compare") {
				const value = await read(`${root}/${preview.import_id}/compare`, { method: "POST" });
				if (!isCurrent()) return;
				accept(value);
				const project = await read(`/projects/${encodeURIComponent(projectId)}`);
				if (!isCurrent()) return;
				await applied.current(project, false);
			} else {
				await read(`${root}/${preview.import_id}`, { method: "DELETE" });
				if (!isCurrent()) return;
				setPreview(null);
				attempt.current = null;
			}
			await load();
			return isCurrent();
		} catch (e) {
			if (isCurrent()) {
				setError(e.message);
				if (e.code === "stale_import") setStalePreview(true);
			}
		} finally {
			if (isCurrent()) {
				lock.current = false;
				setBusy(false);
			}
		}
	};
	const current = loadedScope.current === scope;
	return {
		preview: current ? preview : null,
		pending: current ? pending : [],
		busy: current && busy,
		error: current ? error : "",
		extractionError: current ? extractionError : "",
		setExtractionError,
		stalePreview: current && stalePreview,
		message: current ? message : "",
		accept,
		mutate,
		load,
		read,
	};
}
