import { useCallback, useEffect, useRef, useState } from "react";
import { knowledgeRequest } from "../services/knowledgeClient";

export default function useKnowledge(request, projectId) {
	const path = projectId ? `/projects/${encodeURIComponent(projectId)}/knowledge` : "/me/knowledge";
	const requester = useRef(request);
	const epoch = useRef(0);
	const attempt = useRef(null);
	const [data, setData] = useState(null);
	const [error, setError] = useState("");
	const [loading, setLoading] = useState(true);
	const [busy, setBusy] = useState(false);
	useEffect(() => {
		requester.current = request;
	}, [request]);
	const load = useCallback(async () => {
		const current = ++epoch.current;
		setLoading(true);
		try {
			const value = await knowledgeRequest(requester.current, path);
			if (current === epoch.current) {
				setData(value);
				setError("");
			}
		} catch (e) {
			if (current === epoch.current) setError(e.message);
		} finally {
			if (current === epoch.current) setLoading(false);
		}
	}, [path]);
	useEffect(() => {
		void load();
		return () => {
			epoch.current++;
		};
	}, [load]);
	const mutate = async (payload) => {
		if (busy) return null;
		const current = epoch.current;
		const fingerprint = JSON.stringify([path, payload]);
		if (attempt.current?.fingerprint !== fingerprint) attempt.current = { fingerprint, id: crypto.randomUUID() };
		setBusy(true);
		setError("");
		try {
			const result = await knowledgeRequest(requester.current, path, {
				method: "POST",
				headers: { "Content-Type": "application/json", "X-Request-ID": attempt.current.id },
				body: JSON.stringify(payload),
			});
			if (current !== epoch.current) return null;
			attempt.current = null;
			await load();
			return result;
		} catch (e) {
			if (current === epoch.current) setError(e.message);
			return null;
		} finally {
			setBusy(false);
		}
	};
	return { data, error, loading, busy, load, mutate };
}
