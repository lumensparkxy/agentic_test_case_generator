import { useEffect, useRef, useState } from "react";
import { Button, Input, Select, Textarea } from "../ui/controls";
import { Surface } from "../ui/surfaces";

const categories = [
	"unknown",
	"missing_data",
	"access",
	"environment",
	"ambiguous_requirement",
	"unsupported_assumption",
	"script_defect",
	"application_defect",
];
const kinds = ["test_data", "credential", "role", "environment", "setup", "cleanup"];
const words = (value) => value.replaceAll("_", " ");
const ids = (value) =>
	value
		.split(",")
		.map((s) => s.trim())
		.filter(Boolean);

export default function QualityFeedbackPanel({ request, projectId, onUpdated }) {
	const [data, setData] = useState(null);
	const [project, setProject] = useState(null);
	const [error, setError] = useState("");
	const [notice, setNotice] = useState("");
	const [busy, setBusy] = useState(false);
	const [selected, setSelected] = useState(null);
	const [references, setReferences] = useState({});
	const [lesson, setLesson] = useState("");
	const [classification, setClassification] = useState("unknown");
	const [newPrerequisite, setNewPrerequisite] = useState({ name: "", kind: "test_data", description: "" });
	const [sourceId, setSourceId] = useState("");
	const [form, setForm] = useState({
		category: "missing_data",
		summary: "",
		expected_behavior: "",
		test_case_ids: "",
		environment: "default",
		name: "",
		kind: "test_data",
		description: "",
	});
	const alive = useRef(true);
	const inFlight = useRef(false);
	const attempt = useRef(null);
	useEffect(() => {
		alive.current = true;
		return () => {
			alive.current = false;
		};
	}, []);
	async function read(path, options) {
		const response = await request(path, options);
		const value = await response.json();
		if (!response.ok)
			throw new Error(
				typeof value.detail === "string" ? value.detail : "Feedback could not be saved. Check the fields or reload the project."
			);
		return value;
	}
	async function load() {
		const [findings, current] = await Promise.all([read(`/projects/${projectId}/quality-findings`), read(`/projects/${projectId}`)]);
		if (alive.current) {
			setData(findings);
			setProject(current);
			setSourceId((previous) =>
				Object.values(current.current_snapshots || {}).some((snapshot) => snapshot.snapshot_id === previous) ? previous : ""
			);
		}
	}
	useEffect(() => {
		load().catch((e) => {
			if (alive.current) setError(e.message);
		});
		// Component is keyed by project; request identities may change on render.
	}, [projectId]);
	async function save(path, payload) {
		if (inFlight.current) return;
		inFlight.current = true;
		const fingerprint = JSON.stringify([path, payload]);
		if (attempt.current?.fingerprint !== fingerprint) attempt.current = { fingerprint, requestId: crypto.randomUUID() };
		setBusy(true);
		setError("");
		setNotice("");
		try {
			await read(path, {
				method: "POST",
				headers: { "Content-Type": "application/json", "X-Request-ID": attempt.current.requestId },
				body: JSON.stringify(payload),
			});
			if (!alive.current) return;
			setNotice("Feedback saved. Applied corrections require review and a verified rerun.");
			setSelected(null);
			await load();
			if (alive.current) await onUpdated?.();
			attempt.current = null;
		} catch (e) {
			if (alive.current) setError(e.message);
		} finally {
			inFlight.current = false;
			if (alive.current) setBusy(false);
		}
	}
	const change = (key) => (event) => setForm((old) => ({ ...old, [key]: event.target.value }));
	const source =
		project?.current_snapshots?.execution ||
		project?.current_snapshots?.test_cases ||
		project?.current_snapshots?.use_cases ||
		project?.current_snapshots?.requirements;
	return (
		<Surface as="section" className="result-section" aria-labelledby="quality-feedback-heading">
			<h3 id="quality-feedback-heading">Quality feedback</h3>
			<p>Capture findings from any stage. Correct missing prerequisites, preserve business assertions, and learn from verified results.</p>
			{error && <p role="alert">{error}</p>}
			{notice && <p role="status">{notice}</p>}
			<Button className="secondary" disabled={busy} onClick={() => load().catch((e) => setError(e.message))}>
				Refresh findings
			</Button>
			{data && <p>{data.findings.filter((f) => f.status !== "verified").length} findings awaiting resolution or verification</p>}
			<form
				onSubmit={(event) => {
					event.preventDefault();
					save(`/projects/${projectId}/quality-findings`, {
						base_project_revision: project.current_revision,
						source_snapshot_id: sourceId || source.snapshot_id,
						category: form.category,
						summary: form.summary,
						expected_behavior: form.expected_behavior,
						test_case_ids: ids(form.test_case_ids),
						environment: form.environment,
						prerequisites: form.name ? [{ name: form.name, kind: form.kind, description: form.description, status: "missing" }] : [],
					});
				}}
			>
				<fieldset disabled={busy || !source} className="panel-form">
					<legend>Record a finding</legend>
					<label>
						Evidence snapshot
						<Select value={sourceId || source?.snapshot_id || ""} onChange={(event) => setSourceId(event.target.value)}>
							{Object.entries(project?.current_snapshots || {}).map(([stage, snapshot]) => (
								<option key={snapshot.snapshot_id} value={snapshot.snapshot_id}>
									{words(stage)} — version {snapshot.version}
								</option>
							))}
						</Select>
					</label>
					<label>
						Finding type
						<Select value={form.category} onChange={change("category")}>
							{categories.map((c) => (
								<option key={c} value={c}>
									{words(c)}
								</option>
							))}
						</Select>
					</label>
					<label>
						Observed problem
						<Textarea required maxLength={1000} value={form.summary} onChange={change("summary")} />
					</label>
					<label>
						Expected behavior
						<Textarea required maxLength={1000} value={form.expected_behavior} onChange={change("expected_behavior")} />
					</label>
					<label>
						Affected test case IDs
						<Input value={form.test_case_ids} onChange={change("test_case_ids")} placeholder="TC-001, TC-002" />
					</label>
					<label>
						Environment
						<Input required maxLength={120} value={form.environment} onChange={change("environment")} />
					</label>
					<label>
						Missing prerequisite name
						<Input value={form.name} onChange={change("name")} placeholder="supervisor_login" pattern="[A-Za-z][A-Za-z0-9_.-]*" />
					</label>
					<label>
						Prerequisite kind
						<Select value={form.kind} onChange={change("kind")}>
							{kinds.map((k) => (
								<option key={k} value={k}>
									{words(k)}
								</option>
							))}
						</Select>
					</label>
					<label>
						Prerequisite description
						<Input required={Boolean(form.name)} value={form.description} onChange={change("description")} maxLength={500} />
					</label>
					<p>Describe the required role or fixture. Keep passwords and tokens in execution configuration.</p>
					<Button type="submit">Record finding</Button>
				</fieldset>
			</form>
			<ul>
				{(data?.findings || []).map((finding) => (
					<li key={finding.id}>
						<p>
							<strong>{finding.summary}</strong> — {words(finding.status)} ({words(finding.category)})
						</p>
						<p>{finding.expected_behavior}</p>
						<Button
							disabled={busy}
							className="secondary"
							onClick={() => {
								setSelected(finding);
								setClassification(finding.category);
								setReferences(Object.fromEntries(finding.prerequisites.map((p) => [p.name, p.reference || ""])));
								setLesson("");
							}}
						>
							Review finding
						</Button>
					</li>
				))}
			</ul>
			{selected && (
				<fieldset disabled={busy} className="panel-form">
					<legend>Resolve: {selected.summary}</legend>
					<p>
						Environment: {selected.environment}. Affected cases: {(selected.test_case_ids || []).join(", ") || "Not specified"}. Evidence:{" "}
						{words(selected.source_stage || "recorded snapshot")} revision {selected.source_project_revision ?? "recorded"}.
					</p>
					{selected.status === "open" && (
						<div className="panel-form">
							<label>
								Additional prerequisite name
								<Input value={newPrerequisite.name} onChange={(e) => setNewPrerequisite({ ...newPrerequisite, name: e.target.value })} />
							</label>
							<label>
								Additional prerequisite kind
								<Select value={newPrerequisite.kind} onChange={(e) => setNewPrerequisite({ ...newPrerequisite, kind: e.target.value })}>
									{kinds.map((kind) => (
										<option key={kind} value={kind}>
											{words(kind)}
										</option>
									))}
								</Select>
							</label>
							<label>
								Additional prerequisite description
								<Input
									value={newPrerequisite.description}
									onChange={(e) => setNewPrerequisite({ ...newPrerequisite, description: e.target.value })}
								/>
							</label>
							<Button
								disabled={!newPrerequisite.name || !newPrerequisite.description}
								onClick={() => {
									setSelected({
										...selected,
										prerequisites: [
											...selected.prerequisites.filter((p) => p.name !== newPrerequisite.name),
											{ ...newPrerequisite, status: "missing" },
										],
									});
									setNewPrerequisite({ name: "", kind: "test_data", description: "" });
								}}
							>
								Add prerequisite to classification
							</Button>
						</div>
					)}
					{selected.status === "open" && (
						<>
							<label>
								Classify finding
								<Select value={classification} onChange={(e) => setClassification(e.target.value)}>
									{categories.map((c) => (
										<option key={c} value={c}>
											{words(c)}
										</option>
									))}
								</Select>
							</label>
							<Button
								onClick={() =>
									save(`/projects/${projectId}/quality-findings/${selected.id}`, {
										action: "classify",
										category: classification,
										prerequisites: selected.prerequisites,
										base_project_revision: project.current_revision,
									})
								}
							>
								Save classification
							</Button>
						</>
					)}
					{selected.prerequisites.map((p) => (
						<label key={p.name}>
							{p.name} reference
							<Input
								value={references[p.name] || ""}
								onChange={(e) => setReferences({ ...references, [p.name]: e.target.value })}
								placeholder="env:PETF_TEST_SUPERVISOR_LOGIN"
							/>
						</label>
					))}
					{selected.prerequisites.length > 0 &&
						selected.status === "open" &&
						["missing_data", "access", "environment"].includes(selected.category) && (
							<Button
								onClick={() =>
									save(`/projects/${projectId}/quality-findings/${selected.id}`, {
										action: "apply",
										base_project_revision: project.current_revision,
										prerequisites: selected.prerequisites.map((p) => ({ ...p, reference: references[p.name], status: "configured" })),
									})
								}
							>
								Apply prerequisite correction
							</Button>
						)}
					<label>
						Reusable lesson
						<Textarea value={lesson} onChange={(e) => setLesson(e.target.value)} maxLength={1000} />
					</label>
					<p>
						Propose a method relevant to requirements, scenarios, cases and automation. It awaits separate approval in Project knowledge.
					</p>
					<Button
						disabled={!lesson.trim()}
						onClick={() =>
							save(`/projects/${projectId}/quality-findings/${selected.id}`, {
								action: "propose_lesson",
								base_project_revision: project.current_revision,
								lesson: { text: lesson, stages: ["requirements", "use_cases", "test_cases", "automation"] },
							})
						}
					>
						Propose lesson
					</Button>
					<Button
						disabled={!project?.current_snapshots?.execution || selected.status === "verified"}
						onClick={() =>
							save(`/projects/${projectId}/quality-findings/${selected.id}`, {
								action: "verify",
								base_project_revision: project.current_revision,
								verification_snapshot_id: project.current_snapshots.execution.snapshot_id,
							})
						}
					>
						Verify against latest execution
					</Button>
					<Button className="secondary" onClick={() => setSelected(null)}>
						Close finding review
					</Button>
				</fieldset>
			)}
		</Surface>
	);
}
