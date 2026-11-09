import { useCallback, useEffect, useRef, useState } from "react";
import { API_CONTRACT_ENDPOINTS } from "../../api/generated/api-contracts";
import RouteLink from "../../app/RouteLink";
import { buildSavedCasePath } from "../../app/savedCaseLinks";
import useReviewDraft from "../../hooks/useReviewDraft";
import { createRequestId } from "../../services/apiClient";
import { hasApprovedGenerationScope } from "../../utils/requirementScope";
import { Button, Checkbox, Select, Textarea } from "../ui/controls";
import { Disclosure, Surface } from "../ui/surfaces";

const words = (value) => value?.replaceAll("_", " ");
const SYNC_RECOVERY =
	import.meta.env.VITE_PROJECT_WORKSPACES_V2 === "true" && import.meta.env.VITE_APPROVAL_CONTINUATION_ENABLED === "true";

async function read(request, path, body, requestId) {
	const response = await request(
		path,
		body
			? {
					method: "POST",
					headers: { "Content-Type": "application/json", "X-Request-ID": requestId || crypto.randomUUID() },
					body: JSON.stringify(body),
				}
			: undefined
	);
	const value = await response.json();
	if (!response.ok)
		throw new Error(
			typeof value.detail === "string" ? value.detail : "The project changed or could not be saved. Reload and compare your correction."
		);
	return value;
}

function rebindReceiptMatches(receipt, operation) {
	return Boolean(
		operation &&
		receipt?.request_id === operation.requestId &&
		receipt.change_id === operation.changeId &&
		receipt.base_project_revision === operation.revision &&
		receipt.project_revision === operation.revision + 1 &&
		receipt.requirements_snapshot_id === operation.requirementsId &&
		receipt.context_snapshot_id === operation.contextId &&
		receipt.previous_use_cases_snapshot_id === operation.planId &&
		receipt.previous_test_cases_snapshot_id === operation.suiteId &&
		receipt.use_cases_snapshot_id &&
		receipt.use_cases_snapshot_id !== operation.planId &&
		receipt.test_cases_snapshot_id &&
		receipt.test_cases_snapshot_id !== operation.suiteId
	);
}

export function ProjectSyncStatus({ project, request, onUpdated, compact = false, rebind = null }) {
	const projectId = project?.project_id;
	const sync = project?.project_sync;
	const revision = project?.current_revision;
	const rebindDraft = useReviewDraft({
		identity: SYNC_RECOVERY ? rebind?.identity : "",
		projectId,
		snapshotId: "project-sync-rebind-v1",
		name: "project-sync-rebind",
		initialValue: null,
	});
	const pendingRebind = rebindDraft.value;
	const rebindRequestId = rebind && pendingRebind?.requestId;
	const readAdmission = SYNC_RECOVERY && Boolean(projectId && sync);
	const requestRef = useRef(request);
	requestRef.current = request;
	const active = useRef(false);
	const pendingRead = useRef(null);
	const pendingAction = useRef(false);
	const [admission, setAdmission] = useState(null);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");
	const [rebindAction, setRebindAction] = useState(null);
	const current = admission?.projectId === projectId ? admission : null;

	const refreshAdmission = useCallback(
		async (requestId = rebindRequestId) => {
			if (!readAdmission || !active.current) return null;
			pendingRead.current?.abort();
			const controller = new AbortController();
			pendingRead.current = controller;
			try {
				const path = API_CONTRACT_ENDPOINTS.projectSyncAdmission.path.replace("{project_id}", encodeURIComponent(projectId));
				const query = requestId ? `?${new URLSearchParams({ request_id: requestId })}` : "";
				const data = await read((url) => requestRef.current(url, { method: "GET", signal: controller.signal }), `${path}${query}`);
				if (typeof data?.enabled !== "boolean") throw new Error("Draft refresh status is unavailable.");
				if (controller.signal.aborted || !active.current) return null;
				setAdmission({ projectId, revision, requestId, data, error: "" });
				return data;
			} catch {
				if (!controller.signal.aborted && active.current) {
					setAdmission((previous) => ({
						projectId,
						data: previous?.projectId === projectId ? previous.data : null,
						error: "Could not check draft refresh status. Check progress again.",
					}));
				}
				return null;
			} finally {
				if (pendingRead.current === controller) pendingRead.current = null;
			}
		},
		[projectId, readAdmission, rebindRequestId, revision]
	);

	useEffect(() => {
		active.current = true;
		return () => {
			active.current = false;
		};
	}, []);
	useEffect(() => {
		if (pendingAction.current && rebindRequestId) return;
		void refreshAdmission();
		const controller = pendingRead.current;
		return () => controller?.abort();
	}, [refreshAdmission, rebindRequestId]);

	if (!sync) return null;
	const useRecovery = readAdmission && current?.data?.enabled !== false;
	const attempt = useRecovery ? current?.data?.attempt : null;
	const waitingForDraft = useRecovery && current?.data?.blocked && !attempt;
	const refreshing = useRecovery ? attempt?.state === "running" && attempt.change_id === sync.id : sync.status === "running";
	const resumable = ["pending", "partial", "running"].includes(sync.status);
	const canResume = resumable && (!useRecovery || (current?.data?.enabled && !attempt && !waitingForDraft && !current.error && !error));
	const showRecovery = useRecovery && (resumable || attempt || !current?.data || current.error || error);
	const refreshStatus = async (action) => {
		if (pendingAction.current || (action === "resume" && !canResume) || (action === "release" && (!attempt || current?.error || error)))
			return;
		pendingAction.current = true;
		setBusy(true);
		setError("");
		let failure = "";
		try {
			if (action === "resume") {
				await read(request, `/projects/${encodeURIComponent(projectId)}/changes/resume`, { change_id: sync.id });
			} else if (action === "release") {
				const path = API_CONTRACT_ENDPOINTS.projectSyncAdmissionRelease.path
					.replace("{project_id}", encodeURIComponent(projectId))
					.replace("{operation_id}", encodeURIComponent(attempt.operation_id));
				const response = await read(request, path, { fence: attempt.fence });
				if (response?.released !== true) failure = "The release was not confirmed. Check the current attempt.";
			}
		} catch (e) {
			if (action === "release") failure = "The release was not confirmed. Check the current attempt.";
			else if (!useRecovery || !(e instanceof TypeError || e instanceof SyntaxError)) failure = e.message;
		}
		try {
			if (!active.current) return;
			if (!useRecovery) {
				if (!failure) await onUpdated?.();
			} else {
				const status = await refreshAdmission();
				if (!active.current) return;
				const latest = await onUpdated?.();
				if (!latest) failure = "Could not load the latest project update. Check progress again.";
				else if (status && ((action === "release" && !status.attempt) || latest.project_sync?.status === "ready_for_review")) failure = "";
			}
		} catch {
			failure = "Could not load the latest project update. Check progress again.";
		} finally {
			pendingAction.current = false;
			if (active.current) {
				setError(failure);
				setBusy(false);
			}
		}
	};
	const snapshots = project.current_snapshots || {};
	const states = project.stage_state || {};
	const projectedRebind = rebind && current?.data?.rebind;
	const rebindProjection = projectedRebind?.change_id && projectedRebind.change_id === sync.id ? projectedRebind : null;
	const freshRebindProjection = Boolean(current && current.revision === revision && !current.error);
	const acknowledged = rebindReceiptMatches(rebindAction?.receipt, pendingRebind) ? rebindAction.receipt : null;
	const rebindReceipt = [current?.data?.rebind_receipt, acknowledged, sync.review_rebind].find((receipt) =>
		rebindReceiptMatches(receipt, pendingRebind)
	);
	const awaitingRebind = Boolean(pendingRebind && !rebindReceipt);
	const exactPendingSource =
		awaitingRebind &&
		pendingRebind.changeId === sync.id &&
		pendingRebind.revision === revision &&
		pendingRebind.requirementsId === snapshots.requirements?.snapshot_id &&
		pendingRebind.contextId === snapshots.context?.snapshot_id &&
		pendingRebind.planId === snapshots.use_cases?.snapshot_id &&
		pendingRebind.suiteId === snapshots.test_cases?.snapshot_id;
	const boundPlan = snapshots.use_cases;
	const boundSuite = snapshots.test_cases;
	const currentReboundReceipt = [rebindReceipt, awaitingRebind ? null : sync.review_rebind].find((receipt) =>
		Boolean(
			rebind &&
			receipt?.change_id &&
			receipt.change_id === sync.id &&
			receipt.project_revision <= revision &&
			receipt.requirements_snapshot_id === snapshots.requirements?.snapshot_id &&
			receipt.context_snapshot_id === snapshots.context?.snapshot_id &&
			boundPlan?.snapshot_id &&
			boundPlan.project_id === projectId &&
			boundPlan.operation === "project.sync.rebind" &&
			boundPlan.snapshot_id === states.use_cases?.current_snapshot_id &&
			!states.use_cases.stale &&
			boundPlan.metadata?.change_id === sync.id &&
			boundPlan.snapshot_id === receipt.use_cases_snapshot_id &&
			boundSuite?.snapshot_id &&
			boundSuite.project_id === projectId &&
			boundSuite.snapshot_id === states.test_cases?.current_snapshot_id &&
			!states.test_cases.stale &&
			(boundSuite.operation === "project.sync.rebind"
				? boundSuite.snapshot_id === receipt.test_cases_snapshot_id &&
					boundSuite.source_snapshot_id === boundPlan.snapshot_id &&
					boundSuite.metadata?.change_id === sync.id
				: boundSuite.operation === "test_cases.review" &&
					boundSuite.source_snapshot_id === receipt.test_cases_snapshot_id &&
					boundSuite.metadata?.sync_review_binding?.use_cases_snapshot_id === boundPlan.snapshot_id)
		)
	);
	const currentReboundPlan = Boolean(currentReboundReceipt);
	const originalEvidenceReceipt = rebindReceipt || currentReboundReceipt;
	const originalCaseId = rebindReceipt
		? pendingRebind?.caseId
		: currentReboundReceipt && boundSuite.payload?.test_cases?.find((item) => item.id && item.status !== "Deprecated")?.id;

	const offerRebind = rebindProjection?.state === "available" && (!awaitingRebind || exactPendingSource);
	const canDismissRebind = Boolean(
		awaitingRebind &&
		!exactPendingSource &&
		freshRebindProjection &&
		current?.requestId === pendingRebind.requestId &&
		!current.data.rebind_receipt &&
		!current.data.blocked &&
		!attempt
	);

	const canRebind = Boolean(
		offerRebind &&
		freshRebindProjection &&
		current?.data?.enabled &&
		!current.data.blocked &&
		!attempt &&
		!busy &&
		project.status === "active" &&
		Number.isInteger(revision) &&
		hasApprovedGenerationScope(project) &&
		snapshots.requirements?.operation === "requirements.review" &&
		snapshots.context?.snapshot_id &&
		boundPlan?.snapshot_id &&
		boundSuite?.snapshot_id
	);
	const showRebind = Boolean(rebind && (rebindProjection || pendingRebind || currentReboundReceipt || rebind.material));
	const rebindStatus = rebindAction?.busy
		? "Checking draft rebind…"
		: currentReboundPlan
			? "Drafts rebound to the approved requirements"
			: rebindReceipt
				? rebindReceipt.project_revision > revision
					? "Draft rebind saved. Check status to load the current drafts."
					: "Draft rebind saved for an earlier revision"
				: awaitingRebind
					? exactPendingSource
						? "Could not confirm the draft rebind. Check status to recover the saved result."
						: "This draft rebind request uses earlier project inputs."
					: rebindProjection?.state === "needs_requirement_approval"
						? "Corrected requirements need approval"
						: rebindProjection?.state === "available"
							? "Approved correction ready to rebind"
							: !freshRebindProjection && !current?.error
								? "Checking draft rebind eligibility…"
								: "Saved drafts need verified current sources";

	const runRebind = async (submit = false) => {
		if (!rebind || pendingAction.current || (submit && !canRebind)) return;
		pendingAction.current = true;
		setBusy(true);
		setRebindAction({ busy: true, receipt: acknowledged });
		const operation =
			submit && !exactPendingSource
				? {
						requestId: createRequestId(),
						changeId: sync.id,
						revision,
						requirementsId: snapshots.requirements.snapshot_id,
						contextId: snapshots.context.snapshot_id,
						planId: boundPlan.snapshot_id,
						suiteId: boundSuite.snapshot_id,
						caseId: boundSuite.payload?.test_cases?.find((item) => item.id && item.status !== "Deprecated")?.id,
					}
				: pendingRebind;
		if (submit && operation !== pendingRebind) rebindDraft.setValue(operation);
		let receipt = rebindReceiptMatches(acknowledged, operation) ? acknowledged : null;
		let failure = "";
		try {
			if (submit) {
				const path = API_CONTRACT_ENDPOINTS.projectSyncRebindDrafts.path.replace("{project_id}", encodeURIComponent(projectId));
				const received = await read(
					rebind.request || request,
					path,
					{ change_id: operation.changeId, base_project_revision: operation.revision },
					operation.requestId
				);
				if (rebindReceiptMatches(received, operation)) receipt = received;
			}
		} catch (e) {
			if (!(e instanceof TypeError || e instanceof SyntaxError)) failure = e.message;
		}
		try {
			if (!active.current) return;
			const status = await refreshAdmission(operation?.requestId || "");
			if (!active.current) return;
			if (rebindReceiptMatches(status?.rebind_receipt, operation)) receipt = status.rebind_receipt;
			const latest = await rebind.onUpdated();
			if (!active.current) return;
			if (receipt)
				failure = latest?.current_revision >= receipt.project_revision ? "" : "Drafts were saved. Check rebind status to load the project.";
			else if (!status) failure ||= "Could not check draft rebind status. Check rebind status again.";
			else if (!latest) failure ||= "Could not load the latest project. Check rebind status again.";
			setRebindAction({ receipt, error: failure });
		} catch {
			if (active.current) setRebindAction({ receipt, error: "Could not load the latest project. Check rebind status again." });
		} finally {
			pendingAction.current = false;
			if (active.current) setBusy(false);
		}
	};

	const reviewedSuite = project.current_snapshots?.test_cases;
	const suiteState = project.stage_state?.test_cases;
	const reviewBinding = reviewedSuite?.metadata?.sync_review_binding;
	const currentAssessment = Boolean(
		SYNC_RECOVERY &&
		sync.status === "ready_for_review" &&
		reviewedSuite?.operation === "test_cases.review" &&
		reviewedSuite.project_id === projectId &&
		reviewedSuite.snapshot_id &&
		reviewedSuite.snapshot_id === suiteState?.current_snapshot_id &&
		!suiteState.stale &&
		reviewBinding?.change_id &&
		reviewBinding.change_id === sync.id &&
		reviewBinding.test_cases_snapshot_id === reviewedSuite.source_snapshot_id &&
		reviewBinding.use_cases_snapshot_id === project.current_snapshots?.use_cases?.snapshot_id
	);

	const title = currentAssessment
		? "Project update assessed"
		: sync.status === "ready_for_review"
			? "Updated drafts are ready for review"
			: sync.status === "superseded"
				? "Project sources have changed"
				: refreshing
					? "Updating project drafts"
					: "Project update needs attention";
	const details = (
		<>
			<p>
				{currentAssessment
					? "The original drafts and accepted correction remain available as saved evidence."
					: "Accepted facts are shared across agents. Refreshed drafts still need review before approval, export or execution."}
			</p>
			<div className="workflow-diagnostics-pills">
				{Object.entries(sync.stages || {}).map(([stage, state]) => (
					<span className="workflow-diagnostics-pill" key={stage}>
						{words(stage)}: {words(state)}
					</span>
				))}
			</div>
			{sync.review && (
				<p>
					{sync.review.changed} changed · {sync.review.added} added · {sync.review.preserved} preserved
				</p>
			)}
		</>
	);
	return (
		<Surface as="section" className="project-sync-summary" aria-label="Project update status">
			{compact ? (
				<Disclosure>
					<summary>{title}</summary>
					{details}
				</Disclosure>
			) : (
				<>
					<h3>{title}</h3>
					{details}
				</>
			)}
			{(sync.error || current?.error || error) && <p role="alert">{error || current?.error || sync.error}</p>}
			{showRecovery && (
				<>
					{!current?.data && !current?.error && <p role="status">Checking draft refresh status…</p>}
					{waitingForDraft && <p role="status">Correction saved. Another draft operation is active for this project.</p>}
					{attempt && (
						<>
							<p role="status">
								{attempt.state === "interrupted"
									? "The draft refresh was interrupted. The provider may already have received the request. Acknowledge and release this attempt before resuming."
									: "A draft refresh is running. Already issued provider requests may finish after cancellation. A cancelled attempt cannot publish further results."}
							</p>
							{(attempt.change_id !== sync.id || sync.status === "superseded") && (
								<p>This attempt uses an earlier correction or source revision.</p>
							)}
							<Disclosure>
								<summary>Draft refresh details</summary>
								<p>Stage {attempt.stage === "use_cases" ? "Scenarios" : "Test Cases"}</p>
								<p>
									Correction <code>{attempt.change_id}</code>
								</p>
								<p>
									Request <code>{attempt.request_id}</code>
								</p>
							</Disclosure>
						</>
					)}
					<div className="workbench-actions">
						<Button variant="secondary" size="compact" disabled={busy} onClick={() => void refreshStatus("check")}>
							Check refresh progress
						</Button>
						{attempt && (
							<Button
								variant="secondary"
								size="compact"
								disabled={Boolean(busy || current?.error || error)}
								onClick={() => void refreshStatus("release")}
							>
								{attempt.state === "interrupted" ? "Acknowledge and release" : "Cancel draft refresh"}
							</Button>
						)}
						{resumable && (
							<Button size="compact" disabled={busy || !canResume} onClick={() => void refreshStatus("resume")}>
								{busy ? "Checking project update…" : "Resume remaining stages"}
							</Button>
						)}
					</div>
				</>
			)}
			{showRebind && (
				<section className="generation-gate-card" aria-label="Project Sync draft rebind">
					<div>
						<strong role="status">{rebindStatus}</strong>
						{currentReboundPlan ? (
							<p>Review the rebound scenarios before assessing the saved suite. The original drafts and case versions remain available.</p>
						) : rebindProjection?.state === "needs_requirement_approval" && !awaitingRebind ? (
							<p>Approve the corrected requirements in Scope before rebinding the saved drafts.</p>
						) : rebindProjection?.state === "available" && !awaitingRebind ? (
							<p>
								Bind the saved scenario and test drafts to this exact requirements approval. The saved cases and their versions stay the
								same.
							</p>
						) : null}
						{rebindAction?.error && <p role="alert">{rebindAction.error}</p>}
						{current?.data?.blocked && !currentReboundPlan && (
							<p>Another draft operation is active. Check rebind status after it finishes.</p>
						)}
					</div>
					<div className="workbench-actions">
						{currentReboundPlan && (
							<RouteLink className="route-primary-link" to={rebind.scenariosPath} navigate={rebind.navigate}>
								Review rebound scenarios
							</RouteLink>
						)}
						{rebindProjection?.state === "needs_requirement_approval" && !rebind.scopeCurrent && !awaitingRebind && (
							<RouteLink to={rebind.scopePath} navigate={rebind.navigate}>
								Review corrected requirements
							</RouteLink>
						)}
						{offerRebind && (
							<Button size="compact" disabled={!canRebind} onClick={() => void runRebind(true)}>
								{awaitingRebind ? "Retry draft rebind" : "Rebind saved drafts"}
							</Button>
						)}
						{canDismissRebind && (
							<Button
								variant="secondary"
								size="compact"
								disabled={busy}
								onClick={() => {
									rebindDraft.clear();
									setRebindAction(null);
								}}
							>
								Dismiss earlier rebind request
							</Button>
						)}
						{(!currentReboundPlan || rebindAction?.error) && (
							<Button variant="secondary" size="compact" disabled={busy} onClick={() => void runRebind()}>
								Check rebind status
							</Button>
						)}
						{originalEvidenceReceipt?.previous_test_cases_snapshot_id && originalCaseId && (
							<RouteLink
								className="route-secondary-link"
								to={buildSavedCasePath(projectId, originalEvidenceReceipt.previous_test_cases_snapshot_id, originalCaseId)}
								navigate={rebind.navigate}
							>
								Open original saved case
							</RouteLink>
						)}
					</div>
				</section>
			)}
			{!useRecovery && resumable && (
				<Button disabled={busy} onClick={() => void refreshStatus("resume")}>
					{busy ? "Refreshing drafts…" : sync.status === "running" ? "Check refresh progress" : "Resume remaining stages"}
				</Button>
			)}
		</Surface>
	);
}

export default function ProjectSyncPanel({ project, request, onUpdated }) {
	const [findings, setFindings] = useState([]);
	const [question, setQuestion] = useState("");
	const [questionRequirement, setQuestionRequirement] = useState("");
	const [dispositionReason, setDispositionReason] = useState("");
	const [selectedId, setSelectedId] = useState("");
	const [decision, setDecision] = useState("");
	const [corrections, setCorrections] = useState({});
	const [resolution, setResolution] = useState("");
	const [replace, setReplace] = useState(false);
	const [error, setError] = useState("");
	const [busy, setBusy] = useState(false);
	const attempt = useRef(null);
	const queueRef = useRef(null);
	const alive = useRef(true);
	useEffect(() => {
		alive.current = true;
		return () => {
			alive.current = false;
		};
	}, []);
	const load = async () => {
		try {
			const value = await read(request, `/projects/${project.project_id}/quality-findings`);
			const rows = value.findings || [];
			if (alive.current) setFindings(rows);
			return rows;
		} catch (e) {
			if (alive.current) setError(e.message);
			return null;
		}
	};
	useEffect(() => {
		void load();
	}, [project.project_id, project.current_revision]);
	const sourceRequirements = project.current_snapshots?.requirements?.payload?.requirements || [];
	const clarifications = findings.filter((item) => ["ambiguous_requirement", "unsupported_assumption"].includes(item.category));
	const saveClarification = async (path, payload) => {
		if (busy) return;
		setBusy(true);
		setError("");
		try {
			await read(request, path, payload);
			await onUpdated?.();
			await load();
			setQuestion("");
			setDispositionReason("");
			setSelectedId("");
			window.requestAnimationFrame(() => queueRef.current?.focus({ preventScroll: true }));
		} catch (e) {
			setError(e.message);
		} finally {
			setBusy(false);
		}
	};
	const selected = findings.find((item) => item.id === selectedId);
	const cases = project.current_snapshots?.test_cases?.payload?.test_cases || [];
	const affected = new Set([
		...(selected?.requirement_ids || []),
		...cases.filter((item) => selected?.test_case_ids?.includes(item.id)).flatMap((item) => item.linked_requirement_ids || []),
	]);
	const requirements = (project.current_snapshots?.requirements?.payload?.requirements || []).filter((item) => affected.has(item.id));
	const overlaps = (project.project_decisions || []).filter((item) => item.requirement_ids.some((id) => affected.has(id)));
	return (
		<Surface as="section" className="project-sync-panel" aria-label="Shared project corrections">
			<h3>Clarifications and shared decisions</h3>
			<p>
				Missing or conflicting business rules stay out of generation until you answer them. Other requirements keep their existing review
				decisions.
			</p>
			<section ref={queueRef} tabIndex={-1} aria-label="Business clarifications" className="clarification-queue">
				{clarifications.length ? (
					<ul>
						{clarifications.map((item) => (
							<li key={item.id}>
								<strong>
									{item.requirement_ids?.join(", ")} · {words(item.status)}
								</strong>
								<p>{item.summary}</p>
								<Disclosure>
									<summary>Source and decision history</summary>
									<p>Source: {item.original_source_snapshot_id || item.source_snapshot_id}</p>
									{sourceRequirements
										.filter((r) => item.requirement_ids?.includes(r.id))
										.map((r) => (
											<blockquote key={r.id}>{r.text}</blockquote>
										))}
									{item.disposition_reason && <p>{item.disposition_reason}</p>}
								</Disclosure>
								{["open", "deferred"].includes(item.status) && (
									<Button
										variant="secondary"
										onClick={() => {
											setSelectedId(item.id);
											setDecision("");
											setCorrections({});
											setResolution("");
											setReplace(false);
										}}
									>
										Answer {item.requirement_ids?.join(", ") || "clarification"}
									</Button>
								)}
							</li>
						))}
					</ul>
				) : (
					<p>No business clarifications recorded.</p>
				)}
				<Disclosure>
					<summary>Ask about a missing business rule</summary>
					<label>
						Requirement needing clarification
						<Select value={questionRequirement} onChange={(e) => setQuestionRequirement(e.target.value)}>
							<option value="">Choose a requirement</option>
							{sourceRequirements.map((r) => (
								<option key={r.id} value={r.id}>
									{r.id} — {r.text}
								</option>
							))}
						</Select>
					</label>
					<label>
						What needs clarification?
						<Textarea value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={1000} />
					</label>
					<Button
						disabled={busy || !question.trim() || !questionRequirement}
						onClick={() =>
							saveClarification(`/projects/${project.project_id}/quality-findings`, {
								base_project_revision: project.current_revision,
								source_snapshot_id: project.current_snapshots.requirements.snapshot_id,
								category: "ambiguous_requirement",
								summary: question,
								expected_behavior: "Unresolved: a source-backed business decision is required.",
								requirement_ids: [questionRequirement],
							})
						}
					>
						Add clarification
					</Button>
				</Disclosure>
			</section>
			<h3>Resolve a finding across the project</h3>
			<p>
				{SYNC_RECOVERY
					? "Confirm the business rule once. Review affected scenario and test drafts after refresh."
					: "Confirm the business rule once. Agents will refresh affected scenarios, tests and existing script drafts together."}
			</p>
			{error && <p role="alert">{error}</p>}
			<label>
				Finding to resolve
				<Select
					value={selectedId}
					onChange={(event) => {
						setSelectedId(event.target.value);
						setCorrections({});
						setDecision("");
						setReplace(false);
						setResolution("");
					}}
				>
					<option value="">Choose an open finding</option>
					{findings
						.filter((item) => ["open", "deferred"].includes(item.status))
						.map((item) => (
							<option value={item.id} key={item.id}>
								{item.summary}
							</option>
						))}
				</Select>
			</label>
			<Button variant="secondary" size="compact" onClick={load}>
				Reload findings
			</Button>
			{selected && (
				<form
					onSubmit={async (event) => {
						event.preventDefault();
						if (busy) return;
						const payload = {
							base_project_revision: project.current_revision,
							finding_id: selected.id,
							decision,
							corrections: requirements
								.filter((r) => corrections[r.id] !== undefined && corrections[r.id] !== r.text)
								.map((r) => ({ requirement_id: r.id, previous_text: r.text, text: corrections[r.id] })),
							supersedes: replace ? overlaps.map((item) => item.id) : [],
							resolution,
						};
						const fingerprint = JSON.stringify(payload);
						if (attempt.current?.fingerprint !== fingerprint) attempt.current = { fingerprint, id: crypto.randomUUID() };
						setBusy(true);
						setError("");
						try {
							await read(request, `/projects/${project.project_id}/changes/accept`, payload, attempt.current.id);
							if (alive.current) {
								setSelectedId("");
								await onUpdated?.();
								attempt.current = null;
							}
						} catch (e) {
							if (SYNC_RECOVERY && alive.current) {
								try {
									const latest = await onUpdated?.();
									const rows = latest && alive.current ? await load() : null;
									const accepted = rows?.some(
										(item) =>
											item.id === payload.finding_id &&
											item.status === "accepted" &&
											item.decision === payload.decision &&
											item.change_id === latest.project_sync?.id
									);
									if (alive.current && accepted) {
										setSelectedId("");
										attempt.current = null;
										return;
									}
								} catch {
									// Keep the original request identity for a deliberate retry.
								}
							}
							if (alive.current) setError(e.message);
						} finally {
							if (alive.current) setBusy(false);
						}
					}}
				>
					<p>Source snapshot: {selected.original_source_snapshot_id || selected.source_snapshot_id}</p>
					{["ambiguous_requirement", "unsupported_assumption"].includes(selected.category) && (
						<Disclosure>
							<summary>Defer or exclude this scope</summary>
							<p>
								Defer keeps the question open. Exclude removes its requirements from new generation. Neither action invents an expected
								result.
							</p>
							<label>
								Reason for the scope decision
								<Textarea value={dispositionReason} onChange={(e) => setDispositionReason(e.target.value)} maxLength={1000} />
							</label>
							<div className="button-row">
								{["defer", "exclude"].map((action) => (
									<Button
										key={action}
										variant="secondary"
										disabled={busy || !dispositionReason.trim()}
										onClick={() =>
											saveClarification(`/projects/${project.project_id}/quality-findings/${selected.id}`, {
												action,
												base_project_revision: project.current_revision,
												reason: dispositionReason,
											})
										}
									>
										{action === "defer" ? "Defer affected scope" : "Exclude affected scope"}
									</Button>
								))}
							</div>
						</Disclosure>
					)}
					<blockquote>{selected.expected_behavior}</blockquote>
					<label>
						Accepted business decision
						<Textarea required maxLength={1000} value={decision} onChange={(event) => setDecision(event.target.value)} />
					</label>
					{requirements.map((requirement) => (
						<label key={requirement.id}>
							Corrected source · {requirement.id}
							<Textarea
								required
								value={corrections[requirement.id] ?? requirement.text}
								onChange={(event) => setCorrections((old) => ({ ...old, [requirement.id]: event.target.value }))}
							/>
						</label>
					))}
					{!requirements.length && <p>Link this finding to requirements or test cases in Quality feedback first.</p>}
					{overlaps.length > 0 && (
						<fieldset>
							<legend>Resolve overlapping decisions</legend>
							{overlaps.map((item) => (
								<blockquote key={item.id}>{item.text}</blockquote>
							))}
							<label>
								<Checkbox checked={replace} onChange={(event) => setReplace(event.target.checked)} />
								Replace these decisions with the consolidated rule above
							</label>
							<label>
								Why this resolves the conflict
								<Textarea required value={resolution} onChange={(event) => setResolution(event.target.value)} />
							</label>
						</fieldset>
					)}
					<Button type="submit" disabled={busy || !requirements.length || (overlaps.length > 0 && !replace)}>
						{busy ? "Updating affected drafts…" : "Accept correction and refresh drafts"}
					</Button>
				</form>
			)}
			{project.project_decisions?.length > 0 && (
				<Disclosure>
					<summary>Accepted project decisions ({project.project_decisions.length})</summary>
					{project.project_decisions.map((item) => (
						<p key={item.id}>
							<strong>{item.requirement_ids.join(", ")}</strong> — {item.text}
						</p>
					))}
				</Disclosure>
			)}
		</Surface>
	);
}
