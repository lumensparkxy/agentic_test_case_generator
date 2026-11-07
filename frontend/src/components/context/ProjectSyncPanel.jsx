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

export function ProjectSyncStatus({ project, request, onUpdated }) {
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");
	const sync = project?.project_sync;
	if (!sync) return null;
	return (
		<Surface as="section" className="project-sync-summary" aria-label="Project update status">
			<h3>
				{sync.status === "ready_for_review"
					? "Updated drafts are ready for review"
					: sync.status === "running"
						? "Updating project drafts"
						: sync.status === "superseded"
							? "Project sources have changed"
							: "Project update needs attention"}
			</h3>
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
	const [selectedId, setSelectedId] = useState("");
	const [decision, setDecision] = useState("");
	const [corrections, setCorrections] = useState({});
	const [resolution, setResolution] = useState("");
	const [replace, setReplace] = useState(false);
	const [error, setError] = useState("");
	const [busy, setBusy] = useState(false);
	const attempt = useRef(null);
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
			<Button variant="plain" onClick={load}>
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
					<p>Source snapshot: {selected.source_snapshot_id}</p>
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
