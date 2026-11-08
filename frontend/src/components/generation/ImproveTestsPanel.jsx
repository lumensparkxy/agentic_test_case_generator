import useReviewDraft from "../../hooks/useReviewDraft";
import DraftRecoveryNotice from "../reviews/DraftRecoveryNotice";
import BusinessCoverageSummary, { ExecutionSetupSummary } from "./BusinessCoverageSummary";
import { useId, useState, useEffect } from "react";
import { Button, Checkbox, Textarea } from "../ui/controls";
import { improvementFindings, findingCaseIds, improvementFeedback } from "./improvementFindings";

export default function ImproveTestsPanel({
	review,
	identity,
	projectId,
	snapshotId,
	reportedCase,
	onClearReport,
	summary,
	assessment,
	prerequisiteSnapshotId,
	browserHandoffWarnings,
	meta,
	testCases,
	busy,
	disabled,
	blockedReason,
	stale,
	onImpact,
	onApply,
	onReload,
	onViewCase,
	onReviewCases,
}) {
	const findings = improvementFindings(review, assessment);
	const needsOnlySetup = assessment?.design_status === "assessed_complete" && assessment.execution_status === "blocked" && !findings.length;
	const recovery = useReviewDraft({
		identity,
		projectId,
		snapshotId,
		name: "test-improvements",
		initialValue: { selected: [], instructions: "", caseId: "" },
	});
	const selected = recovery.value.selected;
	const instructions = recovery.value.instructions;
	const setSelected = (next) => recovery.setValue((old) => ({ ...old, selected: typeof next === "function" ? next(old.selected) : next }));
	useEffect(() => {
		if (reportedCase && recovery.value.caseId !== reportedCase.id)
			recovery.setValue({ selected: [], instructions: "", caseId: reportedCase.id });
	}, [reportedCase?.id]);
	const target = reportedCase || testCases.find((item) => item.id === recovery.value.caseId);
	const evidence = target
		? [
				`Test ${target.id}: ${target.title}`,
				...(target.steps || []).map((step, index) => `Step ${index + 1}: ${step.action} | Expected: ${step.expected}`),
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
		testCases.length > 0 &&
		(target ? Boolean(instructions.trim()) : findings.length ? chosen.length > 0 : Boolean(instructions.trim()));
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
				recovery.clear();
				onClearReport?.();
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
	return (
		<section className="improve-tests" aria-label="Improve test quality">
			<DraftRecoveryNotice draft={recovery} />
			{summary && (
				<p role="status">
					{summary.changed} changed · {summary.added} added · {summary.preserved} preserved. Review changed tests before accepting.
				</p>
			)}
			{target && (
				<div className="selected-test-report">
					<h3>Report a problem · {target.id}</h3>
					<p>Only this test will be repaired. Its current evidence is included below.</p>
					<pre>{evidence}</pre>
					<Button
						variant="secondary"
						onClick={() => {
							recovery.setValue((old) => ({ ...old, caseId: "" }));
							onClearReport?.();
						}}
					>
						Return to suite improvements
					</Button>
				</div>
			)}
			<h2>
				{needsOnlySetup
					? "Execution setup needed"
					: review?.approved
						? "Machine quality check passed"
						: review
							? "What needs fixing"
							: "Review your test cases"}
			</h2>
			<p>{review?.summary || "Describe the improvements you want to make to this test suite."}</p>
			<BusinessCoverageSummary assessment={assessment} />
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
			{blockedReason && <p role="status">{blockedReason}</p>}
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
										recovery.clear();
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
			{saved && <p role="status">Test cases updated. Review the latest quality result and remaining findings.</p>}
			{!target && findings.length > 0 && (
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
					{findings.map((finding) => (
						<div key={finding} className="improve-tests-finding">
							<label>
								<Checkbox
									checked={selected.includes(finding)}
									onChange={(event) =>
										setSelected((previous) => (event.target.checked ? [...previous, finding] : previous.filter((item) => item !== finding)))
									}
								/>{" "}
								<span>{finding}</span>
							</label>
							{findingCaseIds(finding, testCases).map((id) => (
								<Button key={id} variant="secondary" size="compact" onClick={() => onViewCase(id)} aria-label={`View test case ${id}`}>
									View test case {id}
								</Button>
							))}
						</div>
					))}
				</fieldset>
			)}
			{(!needsOnlySetup || target) && (
				<>
					<label htmlFor={instructionsId}>{target ? "What is wrong with this test?" : "Additional instructions (optional)"}</label>
					<Textarea
						id={instructionsId}
						value={instructions}
						onChange={(event) => recovery.setValue((old) => ({ ...old, instructions: event.target.value, caseId: target?.id || "" }))}
						disabled={locked}
						rows={4}
						placeholder="Describe any details the fixes should take into account…"
					/>
					<div className="button-row improve-tests-actions">
						<Button disabled={!canApply} onClick={() => void apply()}>
							{busy ? "Applying fixes…" : target ? "Repair selected test" : findings.length ? "Apply selected fixes" : "Apply improvements"}
						</Button>
						<span>{chosen.length} selected</span>
					</div>
				</>
			)}
		</section>
	);
}
