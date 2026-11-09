import { List, ListItem } from "../ui/collections";
import { Button } from "../ui/controls";
import { Badge, Disclosure } from "../ui/surfaces";

const failureLabels = {
	provider_unavailable: "Review provider unavailable",
	review_deadline: "Review time limit reached",
	review_input_too_large: "Review input limit exceeded",
	invalid_evidence: "Review evidence could not be verified",
};

function groupStatus(group) {
	if (group.status !== "assessed_complete" && group.status !== "incomplete") return "Unverified";
	if (group.design_status === "incomplete") return "Design gaps";
	if (group.execution_status === "blocked") return "Execution setup needed";
	return group.status === "assessed_complete" ? "Assessed" : "Needs attention";
}

export default function ReviewGroupSummary({ groups, attempted = false, stale = false, onInspect }) {
	if (!Array.isArray(groups) || !groups.length) return null;
	const assessed = groups.filter((group) => groupStatus(group) !== "Unverified").length;
	const unverified = groups.length - assessed;
	return (
		<section className="test-review-groups" aria-label={attempted ? "Last review attempt" : "Independent review groups"}>
			<p>
				<strong>{attempted ? "Last review attempt" : "Independent review"}</strong>
				{" · "}
				{assessed} of {groups.length} groups assessed{unverified > 0 ? ` · ${unverified} unverified` : ""}
			</p>
			{stale && <p>Sources changed after this review.</p>}
			<Disclosure>
				<summary>Show review groups</summary>
				<List variant="grouped">
					{groups.map((group, index) => {
						const label = `Group ${index + 1}`;
						const status = groupStatus(group);
						const caseIds = group.case_ids || [];
						return (
							<ListItem key={group.input_hash || label} className="test-review-group">
								<div>
									<strong>{label}</strong> <Badge tone={status === "Assessed" ? "success" : "warning"}>{status}</Badge>
									{group.execution_status === "blocked" && status !== "Execution setup needed" && (
										<>
											{" "}
											<Badge tone="warning">Execution setup needed</Badge>
										</>
									)}
									<p>
										{(group.requirement_ids || []).join(", ")} · {caseIds.length} {caseIds.length === 1 ? "test case" : "test cases"}
									</p>
									{status === "Unverified" && failureLabels[group.failure_reason] && <p>{failureLabels[group.failure_reason]}</p>}
								</div>
								{caseIds.length > 0 && (
									<Button variant="secondary" size="compact" onClick={() => onInspect({ key: group.input_hash || label, label, caseIds })}>
										Inspect group {index + 1} cases
									</Button>
								)}
							</ListItem>
						);
					})}
				</List>
			</Disclosure>
		</section>
	);
}
