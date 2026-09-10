import { Badge } from "../components/ui/surfaces";
import { Button, Link } from "../components/ui/controls";
import ProjectPageHeader from "../components/layout/ProjectPageHeader";
import RouteLink from "../app/RouteLink";
import { PROJECT_DESTINATIONS, buildProjectPath } from "../app/workflowRoutes";
import UseCaseReviewWorkbench from "../components/reviews/UseCaseReviewWorkbench";
import useUseCaseReview from "../hooks/useUseCaseReview";

export default function UseCaseReviewPage({ project, identity, request, navigate, onDecisionCommitted, onReloadLatest, onBack, onNext }) {
	const projectId = project?.project_id || "";
	const snapshot = project?.current_snapshots?.use_cases || null;
	const stageState = project?.stage_state?.use_cases || null;
	const review = useUseCaseReview({
		request,
		identity,
		projectId,
		snapshotId: snapshot?.snapshot_id || "",
		baseProjectRevision: Number.isInteger(project?.current_revision) ? project.current_revision : null,
		onCommitted: onDecisionCommitted,
		onReload: onReloadLatest,
	});
	const requirementsSnapshot = project?.current_snapshots?.requirements || null;
	const requirementsState = project?.stage_state?.requirements || null;
	const requirementsReady = Boolean(
		requirementsSnapshot &&
		requirementsState?.current_snapshot_id === requirementsSnapshot.snapshot_id &&
		requirementsState.approved &&
		!requirementsState.stale
	);
	const prerequisiteDestination = requirementsReady ? PROJECT_DESTINATIONS.TEST_CASES : PROJECT_DESTINATIONS.REQUIREMENTS;
	const responseStageState = review.response?.use_cases_state;
	const effectiveStageState = responseStageState?.current_snapshot_id === snapshot?.snapshot_id ? responseStageState : stageState;
	const latestHumanReview = effectiveStageState?.metadata?.latest_human_review;
	const matchingHumanDecision = latestHumanReview?.snapshot_id === snapshot?.snapshot_id ? latestHumanReview?.decision || "" : "";
	const reviewStatus = effectiveStageState?.stale
		? { label: "Regeneration needed", tone: "stale" }
		: matchingHumanDecision === "approve"
			? { label: "Human approved", tone: "approved" }
			: matchingHumanDecision === "request_changes"
				? { label: "Changes requested", tone: "changes" }
				: { label: "Awaiting human review", tone: "pending" };

	return (
		<main
			id="main-content"
			className="use-case-review-page"
			aria-labelledby="use-case-review-title"
			aria-busy={review.isSubmitting || review.isReloading || undefined}
			tabIndex={-1}
		>
			<ProjectPageHeader
				title="Use Cases"
				titleId="use-case-review-title"
				project={project}
				navigate={navigate}
				actions={
					snapshot ? (
						<div className="use-case-page-status-actions">
							<Badge
								tone={reviewStatus.tone === "approved" ? "success" : "warning"}
								className={`use-case-page-freshness ${reviewStatus.tone}`}
								role="status"
								aria-label="Current human review status"
							>
								{reviewStatus.label}
							</Badge>
							<Link className="use-case-skip-review-link" href="#use-case-review-decision">
								Skip to review decision
							</Link>
						</div>
					) : null
				}
			/>

			{snapshot ? (
				<UseCaseReviewWorkbench
					key={`${identity}:${projectId}`}
					project={project}
					snapshot={snapshot}
					stageState={stageState}
					review={review}
				/>
			) : (
				<section className="use-case-no-snapshot" aria-labelledby="use-case-no-snapshot-title">
					<span className="use-case-section-kicker">Prerequisite</span>
					<h2 id="use-case-no-snapshot-title">No Use Cases snapshot</h2>
					<p>
						{requirementsReady
							? "Generate the first test suite to create a reviewable Use Cases artifact from the approved requirements."
							: requirementsSnapshot
								? "Review and approve the current project requirements before generating a Use Cases artifact."
								: "Add and approve project requirements before generating a Use Cases artifact."}
					</p>
					<div className="use-case-no-snapshot-actions">
						<RouteLink className="route-primary-link" to={buildProjectPath(projectId, prerequisiteDestination)} navigate={navigate}>
							{requirementsReady ? "Open Test Cases" : "Open Requirements"}
						</RouteLink>
						<RouteLink className="route-secondary-link" to={buildProjectPath(projectId)} navigate={navigate}>
							Back to project overview
						</RouteLink>
					</div>
				</section>
			)}
			<nav className="panel-nav" aria-label="Workflow steps">
				<Button variant="secondary" onClick={onBack} title="Back to Context">
					Back
				</Button>
				<Button onClick={onNext} title="Next to Test Cases">
					Next
				</Button>
			</nav>
		</main>
	);
}
