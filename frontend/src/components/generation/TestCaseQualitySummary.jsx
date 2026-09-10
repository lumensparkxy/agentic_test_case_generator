import { CircleAlert, CircleCheck } from "lucide-react";

export default function TestCaseQualitySummary({ review, meta, exportLocked }) {
	if (!review) return null;
	const Icon = review.approved ? CircleCheck : CircleAlert;
	const findings = [...new Set([...(review.blocking_issues || []), ...(review.unmet_criteria || [])])];
	return (
		<section className={`test-quality-summary ${review.approved ? "approved" : "attention"}`} aria-label="Test case quality">
			<Icon size={28} aria-hidden="true" />
			<div>
				<strong>{review.approved ? "Machine quality check passed" : "Needs refinement"}</strong>
				<p>
					{meta.scoreLabel}
					{meta.thresholdLabel ? ` · ${meta.thresholdLabel}` : ""}
					{exportLocked ? " · Export is blocked." : ""}
				</p>
				<details className="test-quality-findings">
					<summary>View findings</summary>
					<p>{review.summary || "No review summary available."}</p>
					{findings.length > 0 && (
						<ul>
							{findings.map((issue) => (
								<li key={issue}>{issue}</li>
							))}
						</ul>
					)}
				</details>
			</div>
		</section>
	);
}
