import { useEffect, useEffectEvent, useId, useState } from "react";
import RouteLink from "../../app/RouteLink";
import { buildSavedCasePath, readExactSavedCaseEvidence } from "../../app/savedCaseLinks";
import { Button, Radio } from "../ui/controls";
import { Disclosure } from "../ui/surfaces";
import "./SelectedCaseRepair.css";

function TargetEvidence({ target }) {
	return (
		<div className="selected-repair-target">
			<p>{target.reason}</p>
			<blockquote>{target.source_quote}</blockquote>
			<p className="helper-text">
				{target.requirement_id} · {target.scenario_id}
				{target.step_numbers?.length > 0 &&
					` · Saved ${target.step_numbers.length === 1 ? "step" : "steps"} ${target.step_numbers.join(", ")}`}
			</p>
		</div>
	);
}

export function SelectedCaseRepair({ repair, disabled }) {
	const fieldName = useId();
	const [selectedTarget, setSelectedTarget] = useState("");
	if (!repair.enabled) return null;
	const selected =
		repair.targets.find((target) => target.target_id === (repair.pending ? repair.pendingTarget : selectedTarget)) ||
		(!repair.pending && repair.targets.length === 1 ? repair.targets[0] : null);
	const locked =
		disabled || repair.checking || repair.submitting || repair.blocked || repair.attempt || (repair.pending && !repair.canRetry);
	return (
		<section className="selected-repair-section" aria-label="Selected test repair">
			<h3>Selected test repair</h3>
			{!repair.statusFresh ? (
				<p>{repair.error ? "Check repair status to load verified findings." : "Checking this saved case for verified findings…"}</p>
			) : !repair.targets.length ? (
				<p>No verified missing check is available for this saved case.</p>
			) : (
				<>
					<p>Add one observable check using saved source evidence. Existing steps and other tests are preserved.</p>
					{repair.targets.length === 1 ? (
						<>
							<p>
								<strong>{repair.targets[0].obligation}</strong>
							</p>
							<TargetEvidence target={repair.targets[0]} />
						</>
					) : (
						<fieldset disabled={Boolean(locked)}>
							<legend>Choose a verified missing check</legend>
							{repair.targets.map((target) => (
								<div key={target.target_id} className="selected-repair-option">
									<label>
										<Radio
											name={fieldName}
											checked={selected?.target_id === target.target_id}
											onChange={() => setSelectedTarget(target.target_id)}
										/>{" "}
										{target.obligation}
									</label>
									<TargetEvidence target={target} />
								</div>
							))}
						</fieldset>
					)}
					<Button disabled={Boolean(locked || !selected)} onClick={() => void repair.submit(selected)}>
						{repair.pending && repair.canRetry ? "Retry selected repair" : "Add missing check"}
					</Button>
				</>
			)}
		</section>
	);
}

function repairMessage(repair, currentSnapshotId) {
	if (repair.checking) return "Checking selected repair status…";
	if (repair.attempt?.state === "interrupted")
		return "The repair was interrupted. A provider request may already have been issued. Acknowledge and release it before starting another repair.";
	if (repair.attempt?.state === "running") return "The selected repair is running. If you cancel, requests already issued can finish.";
	if (repair.submitting) return "Adding the selected check. You can check repair status while it runs.";
	if (repair.pending) return "Could not confirm the repair response. Check repair status to recover a saved result.";
	if (repair.result)
		return repair.result.result_snapshot_id === currentSnapshotId
			? "The selected repair is saved. Review the saved suite separately."
			: "The selected repair is saved in an earlier suite.";
	if (repair.blocked) return "Another project operation is in progress. Check status after it finishes.";
	return "Check the selected repair status.";
}

export function SelectedCaseRepairStatus({ repair, currentSnapshotId }) {
	if (!(repair.attempt || repair.pending || repair.result || repair.error || repair.blocked || repair.checking || repair.submitting))
		return null;
	return (
		<section className="generation-gate-card" aria-label="Selected repair status">
			<div>
				<h3>Selected repair status</h3>
				<p role="status">{repairMessage(repair, currentSnapshotId)}</p>
				{repair.attempt?.source_snapshot_id && repair.attempt.source_snapshot_id !== currentSnapshotId && (
					<p>This attempt uses an earlier saved suite.</p>
				)}
				{repair.error && <p role="alert">{repair.error}</p>}
			</div>
			<div className="workbench-actions">
				<Button variant="secondary" size="compact" disabled={repair.checking} onClick={() => void repair.check()}>
					Check repair status
				</Button>
				{repair.attempt && (
					<Button
						variant="secondary"
						size="compact"
						disabled={repair.checking || !repair.statusFresh}
						onClick={() => void repair.release()}
					>
						{repair.attempt.state === "interrupted" ? "Acknowledge interrupted repair" : "Cancel selected repair"}
					</Button>
				)}
				{repair.dismiss && (
					<Button variant="secondary" size="compact" disabled={repair.checking} onClick={repair.dismiss}>
						Dismiss unconfirmed request
					</Button>
				)}
			</div>
		</section>
	);
}

function SavedSteps({ heading, snapshotVersion, versionId, evidence }) {
	return (
		<section className="selected-repair-saved-case" aria-label={heading}>
			<h4>{heading}</h4>
			<p className="helper-text">
				Suite version {snapshotVersion} · Case version <code>{versionId}</code>
			</p>
			<p>
				<strong>{evidence.test_case.title}</strong>
			</p>
			<ol>
				{evidence.test_case.steps.map((step, index) => (
					<li key={index}>
						<p>{step.action}</p>
						{typeof step.test_data === "string" &&
							(step.test_data === "" ? <p>Test data is an empty string.</p> : <p className="test-step-data-value">{step.test_data}</p>)}
						<p>
							<strong>Expected</strong> {step.expected}
						</p>
					</li>
				))}
			</ol>
		</section>
	);
}

export function SavedRepairEvidence({ result, projectId, request, navigate }) {
	const [read, setRead] = useState(null);
	const [retry, setRetry] = useState(0);
	const {
		source_snapshot_id: sourceId,
		result_snapshot_id: resultId,
		case_id: caseId,
		before_case_version_id: originalVersionId,
		after_case_version_id: repairedVersionId,
	} = result;
	const getEvidence = useEffectEvent((snapshotId, signal) =>
		readExactSavedCaseEvidence({ projectId, snapshotId, caseId, request, signal })
	);
	useEffect(() => {
		const controller = new AbortController();
		void Promise.all([getEvidence(sourceId, controller.signal), getEvidence(resultId, controller.signal)])
			.then(([original, repaired]) => {
				if (!controller.signal.aborted)
					setRead(
						original?.test_case.artifact_version_id === originalVersionId && repaired?.test_case.artifact_version_id === repairedVersionId
							? { original, repaired }
							: { error: true }
					);
			})
			.catch(() => {
				if (!controller.signal.aborted) setRead({ error: true });
			});
		return () => controller.abort();
	}, [projectId, sourceId, resultId, caseId, originalVersionId, repairedVersionId, retry]);
	const originalPath = buildSavedCasePath(projectId, sourceId, caseId);
	const repairedPath = buildSavedCasePath(projectId, resultId, caseId);
	const preserved = result.preserved_case_count;
	return (
		<section className="selected-repair-section" aria-label="Saved repair evidence">
			<h3>Saved repair evidence · {caseId}</h3>
			<p>
				One test changed. {preserved} {preserved === 1 ? "other test was" : "other tests were"} preserved.
			</p>
			{read?.original ? (
				<>
					<div className="selected-repair-evidence-links">
						<RouteLink to={originalPath} navigate={navigate}>
							View original saved case
						</RouteLink>
						<RouteLink to={repairedPath} navigate={navigate}>
							View repaired saved case
						</RouteLink>
					</div>
					<Disclosure>
						<summary>Compare saved steps</summary>
						<div className="selected-repair-comparison">
							<SavedSteps
								heading="Original"
								snapshotVersion={read.original.suite_version}
								versionId={result.before_case_version_id}
								evidence={read.original}
							/>
							<SavedSteps
								heading="Repaired"
								snapshotVersion={read.repaired.suite_version}
								versionId={result.after_case_version_id}
								evidence={read.repaired}
							/>
						</div>
					</Disclosure>
				</>
			) : read?.error ? (
				<div role="alert">
					<p>Could not load both exact saved cases.</p>
					<Button
						variant="secondary"
						onClick={() => {
							setRead(null);
							setRetry((current) => current + 1);
						}}
					>
						Retry saved repair evidence
					</Button>
				</div>
			) : (
				<p role="status">Loading exact saved repair evidence…</p>
			)}
		</section>
	);
}
