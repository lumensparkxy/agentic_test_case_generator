import { List, ListItem } from "../ui/collections";
import { Disclosure } from "../ui/surfaces";
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
				<Disclosure className="test-quality-findings">
					<summary>View findings</summary>
					<p>{review.summary || "No review summary available."}</p>
					{findings.length > 0 && (
						<List>
							{findings.map((issue) => (
								<ListItem key={issue}>{issue}</ListItem>
							))}
						</List>
					)}
				</Disclosure>
			</div>
		</section>
	);
}
