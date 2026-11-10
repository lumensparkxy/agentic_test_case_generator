import { buildDeclaredBrowserPrerequisitePath } from "../../app/declaredBrowserSetup";
import RouteLink from "../../app/RouteLink";
import { buildProjectPath } from "../../app/workflowRoutes";
import { List, ListItem } from "../ui/collections";
import { Link } from "../ui/controls";
import { Disclosure } from "../ui/surfaces";

const PROJECT_WORKSPACES_V2 = import.meta.env.VITE_PROJECT_WORKSPACES_V2 === "true";

export default function ExecutionSetupSummary({ assessment, projectId, prerequisiteSnapshotId, declaredSetup, navigate }) {
	const executionStatus = declaredSetup ? "blocked" : assessment?.execution_status;
	if (executionStatus !== "blocked" && executionStatus !== "not_assessed") return null;
	const heading = declaredSetup ? "Declared browser prerequisites" : "Execution setup";
	const blockers = declaredSetup
		? declaredSetup.blockers.map(({ testCase, prerequisite }) => ({ test_case_id: testCase.id, prerequisite_name: prerequisite.name }))
		: Array.isArray(assessment.execution_blockers)
			? assessment.execution_blockers
			: [];
	const blockerList = blockers.length > 0 && (
		<List>
			{blockers.map(({ test_case_id: caseId, prerequisite_name: name }) => (
				<ListItem key={JSON.stringify([caseId, name])}>
					{declaredSetup ? (
						<RouteLink to={buildDeclaredBrowserPrerequisitePath(projectId, declaredSetup.snapshotId, caseId, name)} navigate={navigate}>
							Review prerequisite {name} for {caseId}
						</RouteLink>
					) : projectId && prerequisiteSnapshotId ? (
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
	);
	return (
		<section aria-label={heading} className={declaredSetup ? "project-sync-summary declared-browser-prerequisites" : undefined}>
			<h3 className={declaredSetup ? "section-subtitle" : undefined}>{heading}</h3>
			<p>
				{executionStatus === "blocked"
					? PROJECT_WORKSPACES_V2
						? "Missing prerequisites block browser execution."
						: "Missing prerequisites block approval and execution."
					: "No missing prerequisites were recorded in this assessment. Verify execution in Automation."}
			</p>
			{declaredSetup && blockers.length > 6 ? (
				<Disclosure>
					<summary>Missing prerequisites ({blockers.length})</summary>
					{blockerList}
				</Disclosure>
			) : (
				blockerList
			)}
		</section>
	);
}
