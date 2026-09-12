import { useCallback, useEffect, useRef, useState } from "react";

export default function useRequirementImports(request, projectId, enabled, onApplied) {
	const requester = useRef(request);
	const applied = useRef(onApplied);
	const epoch = useRef(0);
	const lock = useRef(false);
	const attempt = useRef(null);
	const [preview, setPreview] = useState(null);
	const [pending, setPending] = useState([]);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");
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
			throw new Error(typeof detail === "string" ? detail : detail?.message || `Request failed (${response.status}).`);
		}
		return response.status === 204 ? null : response.json();
	}, []);
	const load = useCallback(async () => {
		if (!enabled || !projectId) return;
		const scope = epoch.current;
		try {
			const rows = await read(root);
			if (scope === epoch.current) setPending(Array.isArray(rows) ? rows : []);
		} catch (e) {
			if (scope === epoch.current) setError(e.message);
		}
	}, [enabled, projectId, read, root]);
	useEffect(() => {
		epoch.current++;
		setPreview(null);
		setPending([]);
		setError("");
		setMessage("");
		setBusy(false);
		lock.current = false;
		attempt.current = null;
		void load();
		return () => {
			epoch.current++;
		};
	}, [load]);
	const accept = (value) => {
		if (!value?.import_id || value.project_id !== projectId || !Array.isArray(value.candidates)) {
			throw new Error("Import review is unavailable. Update the API and UI together before importing.");
		}
		setPreview(value);
		setError("");
		setMessage("");
		attempt.current = null;
		setPending((rows) => [value, ...rows.filter((row) => row.import_id !== value.import_id)]);
	};
	const mutate = async (kind, payload) => {
		if (!preview || lock.current) return;
		const scope = epoch.current;
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
				if (scope !== epoch.current) return;
				await applied.current(project);
				if (scope !== epoch.current) return;
				const counts = project.current_snapshots?.requirements?.payload?.import_changes || {};
				setMessage(
					`${counts.added || 0} added · ${counts.updated || 0} updated · ${counts.retired || 0} retired · ${counts.active || 0} active requirements`
				);
				attempt.current = null;
				setPreview(null);
			} else if (kind === "compare") {
				const value = await read(`${root}/${preview.import_id}/compare`, { method: "POST" });
				if (scope !== epoch.current) return;
				accept(value);
				const project = await read(`/projects/${encodeURIComponent(projectId)}`);
				if (scope !== epoch.current) return;
				await applied.current(project, false);
			} else {
				await read(`${root}/${preview.import_id}`, { method: "DELETE" });
				if (scope !== epoch.current) return;
				setPreview(null);
				attempt.current = null;
			}
			await load();
		} catch (e) {
			if (scope === epoch.current) setError(e.message);
		} finally {
			if (scope === epoch.current) {
				lock.current = false;
				setBusy(false);
			}
		}
	};
	return { preview, pending, busy, error, message, accept, mutate, load, read };
}
