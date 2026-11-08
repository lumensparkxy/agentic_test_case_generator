import { useEffect, useRef, useState } from "react";
import { Button, Checkbox, Select, Textarea } from "../ui/controls";
import { Disclosure, Surface } from "../ui/surfaces";

const words = (value) => value?.replaceAll("_", " ");
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
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");
	const sync = project?.project_sync;
	if (!sync) return null;
	const title =
		sync.status === "ready_for_review"
			? "Updated drafts are ready for review"
			: sync.status === "running"
				? "Updating project drafts"
				: sync.status === "superseded"
					? "Project sources have changed"
					: "Project update needs attention";
	const details = (
		<>
			<p>Accepted facts are shared across agents. Refreshed drafts still need review before approval, export or execution.</p>
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
			{(sync.error || error) && <p role="alert">{error || sync.error}</p>}
			{["pending", "partial", "running"].includes(sync.status) && (
				<Button
					disabled={busy}
					onClick={async () => {
						setBusy(true);
						setError("");
						try {
							await read(request, `/projects/${project.project_id}/changes/resume`, { change_id: sync.id });
							await onUpdated?.();
						} catch (e) {
							setError(e.message);
						} finally {
							setBusy(false);
						}
					}}
				>
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
			if (alive.current) setFindings(value.findings || []);
		} catch (e) {
			if (alive.current) setError(e.message);
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
			<p>Confirm the business rule once. Agents will refresh affected scenarios, tests and existing script drafts together.</p>
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
