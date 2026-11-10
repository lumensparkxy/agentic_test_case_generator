import useReviewDraft from "../../hooks/useReviewDraft";
import DraftRecoveryNotice from "../reviews/DraftRecoveryNotice";
import BusinessCoverageSummary from "./BusinessCoverageSummary";
import ExecutionSetupSummary from "./ExecutionSetupSummary";
import { useId, useState, useEffect } from "react";
import { Button, Checkbox, Textarea } from "../ui/controls";
import { findingCaseIds, groupImprovementFindings, improvementFeedback, requiresIndependentReview } from "./improvementFindings";

function scopedDraft(value) {
	if (value.caseInstructions) return value;
	// Older drafts stored the open case's note in the suite instructions field.
	return {
		...value,
		instructions: value.caseId ? "" : value.instructions,
		caseInstructions: value.caseId ? { [value.caseId]: value.instructions } : {},
	};
}

function caseNote(draft, id) {
	return Object.hasOwn(draft.caseInstructions, id) ? draft.caseInstructions[id] : "";
}

export default function ImproveTestsPanel({
	review,
	findings,
	needsOnlySetup,
	identity,
	projectId,
	snapshotId,
	reportedCase,
	onClearReport,
	summary,
	assessment,
	grouped = false,
	savedSuite,
	savedSuiteStale,
	navigate,
	onOpenCitation,
	returnCitation,
	onCitationReturnHandled,
	canReviewSavedSuite,
	prerequisiteSnapshotId,
	browserHandoffWarnings,
	meta,
	testCases,
	busy,
	disabled,
	blockedReason,
	trustedRepairOnly = false,
	stale,
	onImpact,
	onApply,
	onReload,
	onViewCase,
	onReviewCases,
}) {
	const recovery = useReviewDraft({
		identity,
		projectId,
		snapshotId,
		name: "test-improvements",
		initialValue: { selected: [], instructions: "", caseId: "", caseInstructions: {} },
	});
	const draft = scopedDraft(recovery.value);
	const selected = draft.selected;
	const target = reportedCase || testCases.find((item) => item.id === draft.caseId);
	const instructions = target ? caseNote(draft, target.id) : draft.instructions;
	const setSelected = (next) => recovery.setValue((old) => ({ ...old, selected: typeof next === "function" ? next(old.selected) : next }));
	const setInstructions = (instructions) =>
		recovery.setValue((old) => {
			const current = scopedDraft(old);
			return target
				? { ...current, caseId: target.id, caseInstructions: { ...current.caseInstructions, [target.id]: instructions } }
				: { ...current, caseId: "", instructions };
		});
	useEffect(() => {
		if (reportedCase && draft.caseId !== reportedCase.id) recovery.setValue((old) => ({ ...scopedDraft(old), caseId: reportedCase.id }));
	}, [reportedCase?.id]);
	const reviewOnly = !target && findings.length === 0 && requiresIndependentReview(assessment);
	const evidence = target
		? [
				`Test ${target.id}: ${target.title}`,
				...(target.steps || []).map(
					(step, index) => `Step ${step.step ?? index + 1}: ${step.action} | Expected: ${step.expected || "Not specified"}`
				),
				`Overall expected: ${target.expected_result || "Not specified"}`,
			].join("\n")
		: "";
	const [error, setError] = useState("");
	const [conflict, setConflict] = useState(false);
	const [reloading, setReloading] = useState(false);
	const [saved, setSaved] = useState(false);
	const instructionsId = useId();
	const locked = busy || disabled || conflict || reloading;
	const chosen = findings.filter((finding) => selected.includes(finding));
	const canApply =
		!locked &&
		!reviewOnly &&
		testCases.length > 0 &&
		(target ? Boolean(instructions.trim()) : findings.length ? chosen.length > 0 : Boolean(instructions.trim()));
	const clearCurrentDraft = () => {
		recovery.clear({
			matches: (value) => {
				const current = scopedDraft(value);
				return target
					? caseNote(current, target.id) === instructions
					: current.instructions === instructions && JSON.stringify(current.selected) === JSON.stringify(selected);
			},
			retain: (value) => {
				const current = scopedDraft(value);
				const remaining = target
					? { ...current, caseId: current.caseId === target.id ? "" : current.caseId, caseInstructions: { ...current.caseInstructions } }
					: { ...current, selected: [], instructions: "" };
				if (target) delete remaining.caseInstructions[target.id];
				if (
					remaining.caseId ||
					remaining.instructions ||
					remaining.selected.length ||
					Object.values(remaining.caseInstructions).some(Boolean)
				)
					return remaining;
			},
		});
	};
	const apply = async () => {
		if (!canApply) return;
		setError("");
		setSaved(false);
		try {
			if (
				await onApply(
					improvementFeedback(target ? [] : chosen, target ? `${evidence}\nReported problem: ${instructions}` : instructions),
					target ? [target.id] : []
				)
			) {
				clearCurrentDraft();
				if (target) onClearReport?.(target.id);
				setSaved(true);
			}
		} catch (failure) {
			setConflict(failure.status === 409);
			setError(
				failure.status === 409
					? "The project changed. Reload the current findings before applying fixes."
					: failure.message || "The fixes could not be applied. Your selections and instructions are kept."
			);
		}
	};
	const savedAssessment = grouped ? savedSuite?.payload?.substantive_assessment : null;
	const displayedAssessment = savedAssessment || assessment;
	const findingGroups = grouped ? groupImprovementFindings(review, displayedAssessment, findings) : [];
	const renderFinding = (finding, editable) => (
		<div key={finding} className="improve-tests-finding">
			{editable ? (
				<label>
					<Checkbox
						checked={selected.includes(finding)}
						onChange={(event) =>
							setSelected((previous) => (event.target.checked ? [...previous, finding] : previous.filter((item) => item !== finding)))
						}
					/>{" "}
					<span>{finding}</span>
				</label>
			) : (
				<p>{finding}</p>
			)}
			{findingCaseIds(finding, testCases).map((id) => (
				<Button key={id} variant="secondary" size="compact" onClick={() => onViewCase(id)} aria-label={`View test case ${id}`}>
					View test case {id}
				</Button>
			))}
		</div>
	);
	const renderFindingGroups = (editable) =>
		findingGroups
			.filter((group) => editable || !group.recordedAssessment)
			.map(({ label, rows }) => (
				<section key={label} aria-label={label}>
					<h3>{label}</h3>
					{rows.map((finding) => renderFinding(finding, editable))}
				</section>
			));
	const showSelectedReport = Boolean(target && !trustedRepairOnly);
	const repairStatus = (
		<>
			{blockedReason && (!needsOnlySetup || target) && <p role="status">{blockedReason}</p>}
			{stale && (
				<Button variant="secondary" onClick={onImpact}>
					Review impact
				</Button>
			)}
			{error && (
				<div role="alert">
					<p>{error}</p>
					{conflict && (
						<Button
							disabled={reloading || busy}
							onClick={async () => {
								setReloading(true);
								try {
									if (await onReload()) {
										setConflict(false);
										setError("");
										clearCurrentDraft();
									} else setError("The project could not be reloaded. Try reloading again before applying fixes.");
								} finally {
									setReloading(false);
								}
							}}
						>
							Reload current findings
						</Button>
					)}
				</div>
			)}
		</>
	);
	const refinementFields = !trustedRepairOnly && !reviewOnly && (!needsOnlySetup || target) && (
		<>
			<label htmlFor={instructionsId}>{target ? "What is wrong with this test?" : "Additional instructions (optional)"}</label>
			<Textarea
				id={instructionsId}
				value={instructions}
				onChange={(event) => setInstructions(event.target.value)}
				disabled={locked}
				rows={4}
				placeholder="Describe any details the fixes should take into account…"
			/>
			<div className="button-row improve-tests-actions">
				<Button disabled={!canApply} onClick={() => void apply()}>
					{busy ? "Applying fixes…" : target ? "Repair selected test" : findings.length ? "Apply selected fixes" : "Apply improvements"}
				</Button>
				{showSelectedReport ? (
					<Button
						variant="secondary"
						onClick={() => {
							recovery.setValue((old) => ({ ...scopedDraft(old), caseId: "" }));
							onClearReport?.();
						}}
					>
						Return to suite improvements
					</Button>
				) : (
					<span>{chosen.length} selected</span>
				)}
			</div>
		</>
	);

	return (
		<section className="improve-tests" aria-label="Improve test quality">
			<DraftRecoveryNotice draft={recovery} />
			{summary && (
				<p role="status">
					{summary.changed} changed · {summary.added} added · {summary.preserved} preserved. Review changed tests before accepting.
				</p>
			)}
			{showSelectedReport && (
				<section className="selected-test-report" aria-labelledby={`${instructionsId}-report`}>
					<h3 id={`${instructionsId}-report`}>Report a problem · {target.id}</h3>
					<p>Only this test will be repaired. Its current evidence is included below.</p>
					<pre>{evidence}</pre>
					{repairStatus}
					{refinementFields}
				</section>
			)}
			<h2>
				{needsOnlySetup
					? "Execution setup needed"
					: review?.approved
						? "Machine quality check passed"
						: reviewOnly
							? "Independent review needed"
							: review
								? "What needs fixing"
								: "Review your test cases"}
			</h2>
			{(review?.summary || !trustedRepairOnly) && (
				<p>{review?.summary || "Describe the improvements you want to make to this test suite."}</p>
			)}
			<BusinessCoverageSummary
				assessment={displayedAssessment}
				grouped={grouped}
				projectId={projectId}
				savedSuite={savedAssessment ? savedSuite : null}
				savedSuiteStale={savedSuiteStale}
				open={recovery.value.sourceEvidenceOpen === true}
				onOpen={(sourceEvidenceOpen) => recovery.setValue((old) => ({ ...old, sourceEvidenceOpen }))}
				navigate={navigate}
				onOpenCitation={onOpenCitation}
				returnCitation={returnCitation}
				onCitationReturnHandled={onCitationReturnHandled}
				onViewCase={trustedRepairOnly && !savedSuiteStale ? onViewCase : undefined}
			/>
			{reviewOnly && <p>{canReviewSavedSuite ? "Use Review saved suite to check these cases." : assessment.reason}</p>}
			<ExecutionSetupSummary assessment={assessment} projectId={projectId} prerequisiteSnapshotId={prerequisiteSnapshotId} />
			{browserHandoffWarnings.length > 0 && (
				<div>
					<h3>Browser handoff</h3>
					<ul>
						{browserHandoffWarnings.map((warning) => (
							<li key={warning}>{warning}</li>
						))}
					</ul>
				</div>
			)}
			{review && (
				<p className="helper-text">
					{meta.scoreLabel}
					{meta.thresholdLabel ? ` · ${meta.thresholdLabel.replace("Approval threshold", "Required score")}` : ""}
				</p>
			)}
			{review?.approved && (
				<>
					<p>This machine check does not replace your review.</p>
					<Button variant="secondary" onClick={onReviewCases}>
						Review test cases
					</Button>
				</>
			)}
			{!showSelectedReport && repairStatus}
			{saved && <p role="status">Test cases updated. Review the latest quality result and remaining findings.</p>}
			{trustedRepairOnly && findings.length > 0 && (
				<div className="improve-tests-findings">
					<h3>Saved findings</h3>
					<p>Open a saved test to see if an observable check is available for selected repair.</p>
					{grouped ? renderFindingGroups(false) : findings.map((finding) => renderFinding(finding, false))}
				</div>
			)}
			{!trustedRepairOnly && !target && findings.length > 0 && (
				<fieldset disabled={locked} className="improve-tests-findings">
					<legend>Select findings to address</legend>
					<div className="button-row">
						<Button variant="secondary" onClick={() => setSelected(findings)}>
							Select all
						</Button>
						<Button variant="secondary" onClick={() => setSelected([])}>
							Clear selection
						</Button>
					</div>
					{grouped ? renderFindingGroups(true) : findings.map((finding) => renderFinding(finding, true))}
				</fieldset>
			)}
			{!showSelectedReport && refinementFields}
		</section>
	);
}
