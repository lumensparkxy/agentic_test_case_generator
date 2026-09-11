import { useEffect, useRef, useState } from "react";
import { knowledgeRequest } from "../../services/knowledgeClient";
import { Disclosure, Alert } from "../ui/surfaces";
import { Button } from "../ui/controls";

export default function GuidanceUsed({ manifest, request, projectId }) {
	const [details, setDetails] = useState(null);
	const [error, setError] = useState("");
	const [loading, setLoading] = useState(false);
	const mounted = useRef(true);
	useEffect(() => {
		mounted.current = true;
		return () => {
			mounted.current = false;
		};
	}, []);
	if (!manifest) return <p className="guidance-summary">Guidance not recorded</p>;
	const load = async () => {
		if (!projectId || !request || loading) return;
		setLoading(true);
		setError("");
		try {
			const [latest, ...memories] = await Promise.all([
				knowledgeRequest(request, `/projects/${encodeURIComponent(projectId)}/knowledge`),
				...(manifest.memories || []).map((m) =>
					knowledgeRequest(
						request,
						`/projects/${encodeURIComponent(projectId)}/knowledge/${encodeURIComponent(m.id)}/versions/${m.revision}`
					)
				),
			]);
			if (mounted.current) setDetails({ latest, memories });
		} catch (e) {
			if (mounted.current) setError(e.message);
		} finally {
			if (mounted.current) setLoading(false);
		}
	};
	return (
		<Disclosure
			className="guidance-summary"
			onToggle={(e) => {
				if (e.currentTarget.open && !details) void load();
			}}
		>
			<summary>
				Guidance used · {manifest.skills?.length || 0} skills · {manifest.memories?.length || 0} knowledge entries
			</summary>
			<p>
				Model: {manifest.model}.{" "}
				{manifest.memory_bypassed
					? "Run without remembered guidance was explicitly selected."
					: "Guidance was fixed when this run started."}
			</p>
			{details &&
				((manifest.knowledge_revision && details.latest.revision !== manifest.knowledge_revision) ||
					manifest.skills?.some((used) =>
						details.latest.skills?.some((skill) => skill.id === used.id && skill.content_hash !== used.content_hash)
					)) && <Alert>Newer guidance available. Existing artifact decisions are unchanged; updates apply on the next run.</Alert>}
			{manifest.skills?.map((s) => (
				<p key={s.id}>
					<strong>{s.id}</strong> v{s.version} · {s.content_hash}
				</p>
			))}
			{manifest.memories?.map((m, i) => (
				<p key={m.id}>
					<strong>{m.source}</strong> · revision {m.revision}
					<br />
					{details?.memories[i]?.unavailable
						? "This saved guidance was deleted or is unavailable."
						: details?.memories[i]?.text || (loading ? "Loading saved wording…" : m.id)}
				</p>
			))}
			{manifest.omitted?.length > 0 && (
				<p>Excluded: {manifest.omitted.map((m) => `${m.id}: ${m.reason.replaceAll("_", " ")}`).join("; ")}</p>
			)}
			{error && (
				<Alert tone="danger" role="alert">
					{error}
					<Button variant="secondary" onClick={load}>
						Retry guidance details
					</Button>
				</Alert>
			)}
		</Disclosure>
	);
}
