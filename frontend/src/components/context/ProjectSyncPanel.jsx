import { useCallback, useEffect, useRef, useState } from "react";
import { API_CONTRACT_ENDPOINTS } from "../../api/generated/api-contracts";
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

export function ProjectSyncStatus({ project, request, onUpdated, compact = false }) {
	const projectId = project?.project_id;
	const sync = project?.project_sync;
	const readAdmission = SYNC_RECOVERY && Boolean(projectId && sync);
	const requestRef = useRef(request);
	requestRef.current = request;
	const active = useRef(false);
	const pendingRead = useRef(null);
	const pendingAction = useRef(false);
	const [admission, setAdmission] = useState(null);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");
	const current = admission?.projectId === projectId ? admission : null;

	const refreshAdmission = useCallback(async () => {
		if (!readAdmission || !active.current) return null;
		pendingRead.current?.abort();
		const controller = new AbortController();
		pendingRead.current = controller;
		try {
			const path = API_CONTRACT_ENDPOINTS.projectSyncAdmission.path.replace("{project_id}", encodeURIComponent(projectId));
			const data = await read((url) => requestRef.current(url, { method: "GET", signal: controller.signal }), path);
			if (typeof data?.enabled !== "boolean") throw new Error("Draft refresh status is unavailable.");
			if (controller.signal.aborted || !active.current) return null;
			setAdmission({ projectId, data, error: "" });
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
	}, [projectId, readAdmission]);
	useEffect(() => {
		active.current = true;
		return () => {
			active.current = false;
		};
	}, []);
	useEffect(() => {
		void refreshAdmission();
		return () => pendingRead.current?.abort();
	}, [refreshAdmission, project?.current_revision]);

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
