import { List, ListItem } from "../ui/collections";
import { Disclosure } from "../ui/surfaces";
import { Link } from "../ui/controls";
import { buildProjectPath } from "../../app/workflowRoutes";

const PROJECT_WORKSPACES_V2 = import.meta.env.VITE_PROJECT_WORKSPACES_V2 === "true";

export default function BusinessCoverageSummary({ assessment }) {
	const designStatus = assessment?.design_status ?? assessment?.status;
	return (
		<section aria-label="Business coverage assessment">
			<p>
				Business coverage:{" "}
				{designStatus === "assessed_complete" ? "assessed complete" : designStatus === "incomplete" ? "incomplete" : "unverified"}. Human
				acceptance and execution have their own checks.
			</p>
			{assessment && (
				<Disclosure>
					<summary>Source obligations and prerequisites</summary>
					<p>{assessment.reason}</p>
					<List>
						{(assessment.obligations || []).map((item, index) => (
							<ListItem key={index}>
								<strong>
									{item.requirement_id}: {item.obligation} — {item.status}
								</strong>
								<p>Source: “{item.source_quote}”</p>
								<p>{item.reason}</p>
								{(item.evidence || []).map((step, i) => (
									<p key={i}>
										{step.test_case_id}, step {step.step}: {step.action}
										{step.expected ? ` → ${step.expected}` : ""}
									</p>
								))}
							</ListItem>
						))}
						{(assessment.case_grounding || []).map((item) => (
							<ListItem key={`grounding-${item.test_case_id}`}>
								<strong>{item.test_case_id}: Source grounding</strong>
								<p>{item.reason}</p>
							</ListItem>
						))}
						{(assessment.case_grounding || []).flatMap((item) =>
							(item.prerequisites || [])
								.filter((prerequisite) => !prerequisite.execution_prerequisite_name)
								.map((prerequisite, index) => (
									<ListItem key={`${item.test_case_id}-${index}`}>
										<strong>
											{item.test_case_id}: {prerequisite.description}
										</strong>
										<p>
											{prerequisite.status.replaceAll("_", " ")} · {prerequisite.required ? "Required" : "Optional exploration"}.{" "}
											{prerequisite.reason}
										</p>
									</ListItem>
								))
						)}
					</List>
				</Disclosure>
			)}
		</section>
	);
}

export function ExecutionSetupSummary({ assessment, projectId, prerequisiteSnapshotId }) {
	const executionStatus = assessment?.execution_status;
	if (executionStatus !== "blocked" && executionStatus !== "not_assessed") return null;
	const blockers = Array.isArray(assessment.execution_blockers) ? assessment.execution_blockers : [];
	return (
		<section aria-label="Execution setup">
			<h3>Execution setup</h3>
			<p>
				{executionStatus === "blocked"
					? PROJECT_WORKSPACES_V2
						? "Missing prerequisites block browser execution."
						: "Missing prerequisites block approval and execution."
					: "No missing prerequisites were recorded in this assessment. Verify execution in Automation."}
			</p>
			{blockers.length > 0 && (
				<List>
					{blockers.map(({ test_case_id: caseId, prerequisite_name: name }) => (
						<ListItem key={JSON.stringify([caseId, name])}>
							{projectId && prerequisiteSnapshotId ? (
								<Link
									href={`${buildProjectPath(projectId, "context")}?${new URLSearchParams({ snapshot: prerequisiteSnapshotId, case: caseId, prerequisite: name })}`}
								>
									Review prerequisite {name} for {caseId}
								</Link>
							) : (
								<>
									{name} for {caseId}
								</>
							)}
						</ListItem>
					))}
				</List>
			)}
		</section>
	);
}
