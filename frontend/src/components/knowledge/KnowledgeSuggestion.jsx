import { useState } from "react";
import { knowledgeRequest } from "../../services/knowledgeClient";
import { Alert } from "../ui/surfaces";
import { Button } from "../ui/controls";

export default function KnowledgeSuggestion({ suggestion, request, projectId }) {
	const [result, setResult] = useState(null);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");
	const current = result || suggestion;
	if (!current) return null;
	const retry = async () => {
		setBusy(true);
		setError("");
		try {
			setResult(
				await knowledgeRequest(request, `/projects/${encodeURIComponent(projectId)}/knowledge/suggestions/retry`, {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify(current.retry),
				})
			);
		} catch (e) {
			setError(e.message);
		} finally {
			setBusy(false);
		}
	};
	return (
		<Alert role="status" tone={current.status === "failed" ? "warning" : "info"}>
			<p>{current.message}</p>
			{current.status === "failed" && current.retry && (
				<Button variant="secondary" disabled={busy} onClick={retry}>
					{busy ? "Retrying…" : "Retry knowledge suggestion"}
				</Button>
			)}
			{error && <p role="alert">{error}</p>}
		</Alert>
	);
}
