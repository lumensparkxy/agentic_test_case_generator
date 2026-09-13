import { useEffect, useRef, useState } from "react";
import { API_CONTRACT_ENDPOINTS } from "../api/generated/api-contracts";
import { createRequestId } from "../services/apiClient";

const idle = { status: "idle", message: "", committedRevision: null };

export default function useUseCaseGeneration({ project, identity, request, onGenerated }) {
	const revision = project?.current_revision;
	const projectId = project?.project_id;
	const scope = `${identity}:${projectId}:${revision}`;
	const scopeRef = useRef(scope);
	const mounted = useRef(false);
	const active = useRef(null);
	const callbacks = useRef({ request, onGenerated });
	const [outcome, setOutcome] = useState(idle);
	scopeRef.current = scope;
	callbacks.current = { request, onGenerated };
	useEffect(() => {
		mounted.current = true;
		return () => {
			mounted.current = false;
			active.current = null;
		};
	}, []);
	useEffect(() => {
		active.current = null;
		setOutcome((previous) =>
			previous.status === "success" &&
			previous.committedRevision === revision &&
			previous.projectId === projectId &&
			previous.identity === identity
				? previous
				: idle
		);
	}, [scope, revision, projectId, identity]);

	const generate = async () => {
		if (active.current || !projectId || !Number.isInteger(revision)) return;
		const operation = { scope, requestId: createRequestId() };
		active.current = operation;
		const current = () => mounted.current && scopeRef.current === operation.scope && active.current === operation;
		setOutcome({
			status: "pending",
			message: "Generating Use Cases from the approved requirements. Your current artifacts remain available.",
		});
		try {
			const response = await callbacks.current.request(
				API_CONTRACT_ENDPOINTS.projectUseCasesGenerate.path.replace("{project_id}", encodeURIComponent(projectId)),
				{
					method: "POST",
					headers: { "Content-Type": "application/json", "X-Request-ID": operation.requestId },
					body: JSON.stringify({ base_project_revision: revision }),
				}
			);
			const data = await response.json();
			if (!current()) return;
			if (!response.ok) {
				const message = typeof data?.detail === "string" ? data.detail : data?.detail?.message;
				throw new Error(message || "Use Cases generation failed. Your current snapshot is still available.");
			}
			if (
				data?.project_id !== projectId ||
				!Number.isInteger(data?.current_revision) ||
				data.current_revision <= revision ||
				!data?.current_snapshots?.use_cases
			) {
				throw new Error("The generation response did not match this project. Reload latest to check the saved result.");
			}
			setOutcome({
				status: "success",
				message: "New Use Cases are ready for human review. Existing test cases were preserved and marked stale.",
				committedRevision: data.current_revision,
				projectId,
				identity,
			});
			callbacks.current.onGenerated(data, { projectId, baseProjectRevision: revision });
		} catch (error) {
			if (current()) setOutcome({ status: "error", message: error.message });
		} finally {
			if (active.current === operation) active.current = null;
		}
	};
	return { ...outcome, isBusy: outcome.status === "pending", generate };
}
