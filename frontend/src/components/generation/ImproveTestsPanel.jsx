import { useId, useState } from "react";
import { Button, Checkbox, Textarea } from "../ui/controls";
import { improvementFindings, findingCaseIds, improvementFeedback } from "./improvementFindings";

export default function ImproveTestsPanel({
	review,
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
	const findings = improvementFindings(review);
	const [selected, setSelected] = useState([]);
	const [instructions, setInstructions] = useState("");
	const [error, setError] = useState("");
	const [conflict, setConflict] = useState(false);
	const [reloading, setReloading] = useState(false);
	const [saved, setSaved] = useState(false);
	const instructionsId = useId();
	const locked = busy || disabled || conflict || reloading;
	const chosen = findings.filter((finding) => selected.includes(finding));
	const canApply = !locked && testCases.length > 0 && (findings.length ? chosen.length > 0 : Boolean(instructions.trim()));
	const apply = async () => {
		if (!canApply) return;
		setError("");
		setSaved(false);
		try {
			if (await onApply(improvementFeedback(chosen, instructions))) {
				setSelected([]);
				setInstructions("");
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
			<h2>{review?.approved ? "Machine quality check passed" : review ? "What needs fixing" : "Review your test cases"}</h2>
			<p>{review?.summary || "Describe the improvements you want to make to this test suite."}</p>
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
										setSelected([]);
										setInstructions("");
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
			{findings.length > 0 && (
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
			<label htmlFor={instructionsId}>Additional instructions (optional)</label>
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
					{busy ? "Applying fixes…" : findings.length ? "Apply selected fixes" : "Apply improvements"}
				</Button>
				<span>{chosen.length} selected</span>
			</div>
		</section>
	);
}
