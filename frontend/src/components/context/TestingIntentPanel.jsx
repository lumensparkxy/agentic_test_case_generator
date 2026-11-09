import { useId, useRef, useState } from "react";
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

function TestingIntentForm({ project, identity, onSave, disabled }) {
	const headingId = useId();
	const savedIntent = project.testing_intent ?? DEFAULT_INTENT;
	const draft = useReviewDraft({
		identity,
		projectId: project.project_id,
		snapshotId: String(project.current_revision),
		name: "testing-intent",
		initialValue: savedIntent,
	});
	const [isSaving, setIsSaving] = useState(false);
	const [failure, setFailure] = useState(null);
	const saving = useRef(false);
	const intent = draft.value;
	const isDirty = !sameIntent(intent, savedIntent);
	const olderIntent = draft.stale?.value;
	const hasOlderDraft = olderIntent && !sameIntent(olderIntent, savedIntent);
	const objectiveError = Array.from(intent.objective).length > MAX_OBJECTIVE_LENGTH ? "Use 500 characters or fewer." : "";
	const locked = disabled || isSaving;
	const canSave = !locked && isDirty && !objectiveError && failure?.status !== 409;

	const submit = async (event) => {
		event.preventDefault();
		if (!canSave || saving.current) return;

		saving.current = true;
		setIsSaving(true);
		setFailure(null);
		try {
			await onSave(intent, project.current_revision);
			draft.clear({ keepVisible: true });
		} catch (error) {
			setFailure(error);
		} finally {
			saving.current = false;
			setIsSaving(false);
		}
	};

	let status = "Using the default testing intent.";
	if (isSaving) status = "Saving testing intent…";
	else if (isDirty) status = draft.error ? "Unsaved changes in this tab." : "Unsaved changes on this device.";
	else if (project.testing_intent) status = "Testing intent saved to the project.";

	return (
		<Surface className="panel" aria-labelledby={headingId}>
			<h2 id={headingId} className="panel-title">
				Testing intent
			</h2>
			<p className="panel-description">Describe what the tests should establish. Keep product rules and evidence in Sources or Context.</p>
			{draft.error && <p role="alert">{draft.error}</p>}
			{hasOlderDraft && (
				<Disclosure className="draft-recovery-notice">
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
					<Button variant="secondary" disabled={locked} onClick={draft.discardStale}>
						Discard older draft
					</Button>
				</Disclosure>
			)}
			<form className="panel-form" onSubmit={submit}>
				<Field label="Testing objective" hint="Optional, up to 500 characters." error={objectiveError}>
					<Textarea
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
			{failure && (
				<p role="alert">
					{failure.status === 409
						? "The project changed. Reload it to compare your local draft with the latest testing intent."
						: failure.message || "Testing intent could not be saved. Your draft is available for retry."}
				</p>
			)}
		</Surface>
	);
}

export default function TestingIntentPanel({ project, identity, onSave, disabled }) {
	if (!identity || !project?.project_id || !Number.isInteger(project.current_revision)) return null;

	return (
		<TestingIntentForm
			key={JSON.stringify([identity, project.project_id, project.current_revision])}
			project={project}
			identity={identity}
			onSave={onSave}
			disabled={disabled}
		/>
	);
}
