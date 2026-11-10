import { useEffect, useId, useRef, useState } from "react";
import useReviewDraft from "../../hooks/useReviewDraft";
import { Button, Field, Select, Textarea } from "../ui/controls";
import { Disclosure, Surface } from "../ui/surfaces";

const DEFAULT_INTENT = { objective: "", risk_emphasis: "balanced" };
const MAX_OBJECTIVE_LENGTH = 500;
const RISK_OPTIONS = {
	balanced: "Balanced",
	business_critical: "Business-critical behavior",
	edge_cases: "Edge cases",
	regression: "Regression",
};

const sameIntent = (left, right) => left?.objective === right.objective && left?.risk_emphasis === right.risk_emphasis;

function TestingIntentForm({ project, identity, onSave, onReload, recovery, disabled }) {
	const { project_id: projectId, current_revision: revision } = project;
	const headingId = useId();
	const heading = useRef(null);
	const comparison = useRef(null);
	const objectiveField = useRef(null);
	const savedIntent = project.testing_intent ?? DEFAULT_INTENT;
	const draft = useReviewDraft({
		identity,
		projectId,
		snapshotId: String(revision),
		name: "testing-intent",
		initialValue: savedIntent,
		perTab: true,
	});
	const [action, setAction] = useState(null);
	const [failure, setFailure] = useState(null);
	const inFlight = useRef(false);
	const isSaving = action === "save";
	const isReloading = action === "reload";
	const intent = draft.value;
	const isDirty = !sameIntent(intent, savedIntent);
	const olderIntent = draft.stale?.value;
	const hasOlderDraft = olderIntent && !sameIntent(olderIntent, savedIntent);
	const objectiveError = Array.from(intent.objective).length > MAX_OBJECTIVE_LENGTH ? "Use 500 characters or fewer." : "";
	const locked = disabled || Boolean(action);
	const conflict = Number.isInteger(failure?.latestRevision);
	const canSave = !locked && isDirty && !objectiveError && !conflict;

	useEffect(() => {
		const previous = recovery.current;
		if (!previous || previous.identity !== identity || previous.projectId !== projectId || previous.revision >= revision) return;
		recovery.current = null;
		const details = comparison.current;
		if (details) {
			details.open = true;
			details.querySelector("summary")?.focus();
		} else {
			heading.current?.focus();
		}
	}, [identity, projectId, recovery, revision]);

	const submit = async (event) => {
		event.preventDefault();
		if (!canSave || inFlight.current) return;

		inFlight.current = true;
		setAction("save");
		setFailure(null);
		try {
			await onSave(intent, revision);
			draft.clear({ keepVisible: true, matches: (value) => sameIntent(value, intent) });
		} catch (error) {
			setFailure({
				latestRevision: error.latestRevision,
				message: Number.isInteger(error.latestRevision)
					? "The project changed. Reload the latest testing intent to compare it with your draft."
					: error.message,
			});
		} finally {
			inFlight.current = false;
			setAction(null);
		}
	};
	const reload = async () => {
		if (locked || !conflict || draft.error || inFlight.current) return;
		inFlight.current = true;
		const attempt = { identity, projectId, revision };
		recovery.current = attempt;
		setAction("reload");
		try {
			await onReload(revision);
		} catch (error) {
			if (recovery.current === attempt) recovery.current = null;
			setFailure({ ...failure, message: error.message });
		} finally {
			inFlight.current = false;
			setAction(null);
		}
	};

	let status = "Using the default testing intent.";
	if (isSaving) status = "Saving testing intent…";
	else if (isReloading) status = "Loading the latest testing intent…";
	else if (isDirty) status = draft.error ? "Unsaved changes in this tab." : "Unsaved changes on this device.";
	else if (project.testing_intent) status = "Testing intent saved to the project.";

	return (
		<Surface className="panel" aria-labelledby={headingId}>
			<h2 id={headingId} className="panel-title" ref={heading} tabIndex={-1}>
				Testing intent
			</h2>
			<p className="panel-description">Describe what the tests should establish. Keep product rules and evidence in Sources or Context.</p>
			{draft.error && <p role="alert">{draft.error}</p>}
			{hasOlderDraft && (
				<Disclosure className="draft-recovery-notice" ref={comparison}>
					<summary>Draft from an earlier revision</summary>
					<p>Compare these local edits with the current values below before saving.</p>
					<dl>
						<dt>Earlier testing objective</dt>
						<dd>
							<pre>{olderIntent.objective || "No objective set"}</pre>
						</dd>
						<dt>Earlier risk emphasis</dt>
						<dd>{RISK_OPTIONS[olderIntent.risk_emphasis]}</dd>
					</dl>
					<div className="workbench-actions">
						{!isDirty && (
							<Button
								variant="secondary"
								disabled={locked}
								onClick={() => {
									// Move the recovered values into the current local draft.
									draft.setValue(olderIntent, true);
									objectiveField.current?.focus();
								}}
							>
								Use earlier draft
							</Button>
						)}
						<Button variant="secondary" disabled={locked} onClick={draft.discardStale}>
							Discard older draft
						</Button>
					</div>
				</Disclosure>
			)}
			<form className="panel-form" onSubmit={submit}>
				<Field label="Testing objective" hint="Optional, up to 500 characters." error={objectiveError}>
					<Textarea
						ref={objectiveField}
						rows={3}
						value={intent.objective}
						disabled={locked}
						onChange={(event) => draft.setValue({ ...intent, objective: event.target.value })}
					/>
				</Field>
				<Field label="Risk emphasis">
					<Select
						value={intent.risk_emphasis}
						disabled={locked}
						onChange={(event) => draft.setValue({ ...intent, risk_emphasis: event.target.value })}
					>
						{Object.entries(RISK_OPTIONS).map(([value, label]) => (
							<option key={value} value={value}>
								{label}
							</option>
						))}
					</Select>
				</Field>
				<div className="button-row">
					<Button type="submit" busy={isSaving} disabled={!canSave}>
						Save testing intent
					</Button>
				</div>
			</form>
			<p role="status" className="helper-text">
				{status}
			</p>
			{conflict ? (
				<div>
					<p role="alert">{failure.message}</p>
					<Button variant="secondary" disabled={locked || Boolean(draft.error)} busy={isReloading} onClick={reload}>
						Reload latest
					</Button>
				</div>
			) : (
				failure && <p role="alert">{failure.message || "Testing intent could not be saved. Your draft is available for retry."}</p>
			)}
		</Surface>
	);
}

export default function TestingIntentPanel({ project, identity, onSave, onReload, disabled }) {
	const recovery = useRef(null);
	if (!identity || !project?.project_id || !Number.isInteger(project.current_revision)) return null;

	return (
		<TestingIntentForm
			key={JSON.stringify([identity, project.project_id, project.current_revision])}
			project={project}
			identity={identity}
			onSave={onSave}
			onReload={onReload}
			recovery={recovery}
			disabled={disabled}
		/>
	);
}
