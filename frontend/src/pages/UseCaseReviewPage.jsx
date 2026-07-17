import RouteLink from "../app/RouteLink";
import { PROJECT_DESTINATIONS, buildProjectPath } from "../app/workflowRoutes";
import UseCaseReviewWorkbench from "../components/reviews/UseCaseReviewWorkbench";
import useUseCaseReview from "../hooks/useUseCaseReview";

export default function UseCaseReviewPage({ project, identity, request, navigate, onDecisionCommitted, onReloadLatest }) {
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
			className="use-case-review-page"
			aria-labelledby="use-case-review-title"
			aria-busy={review.isSubmitting || review.isReloading || undefined}
		>
			<header className="use-case-review-page-header">
				<div>
					<span className="use-case-page-kicker">{project?.name || "Project"}</span>
					<h1 id="use-case-review-title">Use Cases</h1>
					<p>Review the current scenario artifact and record a durable human decision.</p>
				</div>
				{snapshot ? (
					<div className="use-case-page-status-actions">
						<span className={`use-case-page-freshness ${reviewStatus.tone}`} role="status" aria-label="Current human review status">
							{reviewStatus.label}
						</span>
						<a className="use-case-skip-review-link" href="#use-case-review-decision">
							Skip to review decision
						</a>
					</div>
				) : null}
			</header>

			{snapshot ? (
				<UseCaseReviewWorkbench project={project} snapshot={snapshot} stageState={stageState} review={review} />
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
		</main>
	);
}
