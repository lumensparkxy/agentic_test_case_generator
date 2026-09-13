import useUseCaseGeneration from "../hooks/useUseCaseGeneration";
import GuidanceUsed from "../components/knowledge/GuidanceUsed";
import KnowledgeSuggestion from "../components/knowledge/KnowledgeSuggestion";
import { Badge } from "../components/ui/surfaces";
import { Button, Link } from "../components/ui/controls";
import ProjectPageHeader from "../components/layout/ProjectPageHeader";
import RouteLink from "../app/RouteLink";
import { PROJECT_DESTINATIONS, buildProjectPath } from "../app/workflowRoutes";
import UseCaseReviewWorkbench from "../components/reviews/UseCaseReviewWorkbench";
import useUseCaseReview from "../hooks/useUseCaseReview";

export default function UseCaseReviewPage({
	project,
	identity,
	request,
	navigate,
	onDecisionCommitted,
	onReloadLatest,
	onGenerated,
	generationDisabled = false,
	onBack,
	onNext,
}) {
	const projectId = project?.project_id || "";
	const generation = useUseCaseGeneration({ project, identity, request, onGenerated });
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
	const generationAllowed = requirementsReady && project?.status === "active" && !generationDisabled;
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
			aria-busy={generation.isBusy || review.isSubmitting || review.isReloading || undefined}
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
							<Button
								onClick={generation.generate}
								disabled={!generationAllowed || generation.isBusy || review.isSubmitting || review.isReloading}
							>
								{generation.isBusy ? "Generating Use Cases…" : "Regenerate Use Cases"}
							</Button>
							<Link className="use-case-skip-review-link" href="#use-case-review-decision">
								Skip to review decision
							</Link>
						</div>
					) : null
				}
			/>

			<p>
				Generate fresh scenarios from the current approved requirements. Existing test cases stay available and become stale after
				generation succeeds.
			</p>
			{!requirementsReady && snapshot ? (
				<p>
					Approve the current requirements before regenerating.{" "}
					<RouteLink to={buildProjectPath(projectId, PROJECT_DESTINATIONS.REQUIREMENTS)} navigate={navigate}>
						Open Requirements
					</RouteLink>
				</p>
			) : null}
			{requirementsReady && generationDisabled ? (
				<p>Use Cases generation is unavailable while generation access is locked or another workflow is busy.</p>
			) : null}
			{generation.status === "error" ? (
				<div role="alert">
					<p>{generation.message}</p>
					<Button variant="secondary" onClick={onReloadLatest}>
						Reload latest
					</Button>
				</div>
			) : (
				<p role="status" aria-live="polite">
					{generation.message}
				</p>
			)}

			<GuidanceUsed key={snapshot?.snapshot_id || "none"} manifest={snapshot?.metadata?.guidance} request={request} projectId={projectId} />
			<KnowledgeSuggestion
				key={review.response?.review?.review_id || "none"}
				suggestion={review.response?.knowledge_suggestion}
				request={request}
				projectId={projectId}
			/>
			{snapshot ? (
				<fieldset disabled={generation.isBusy} className="use-case-generation-review">
					<UseCaseReviewWorkbench
						key={`${identity}:${projectId}`}
						project={project}
						snapshot={snapshot}
						stageState={stageState}
						review={review}
					/>
				</fieldset>
			) : (
				<section className="use-case-no-snapshot" aria-labelledby="use-case-no-snapshot-title">
					<span className="use-case-section-kicker">Prerequisite</span>
					<h2 id="use-case-no-snapshot-title">No Use Cases snapshot</h2>
					<p>
						{requirementsReady
							? "Generate Use Cases from the approved requirements, then review the scenarios before updating test cases."
							: requirementsSnapshot
								? "Review and approve the current project requirements before generating a Use Cases artifact."
								: "Add and approve project requirements before generating a Use Cases artifact."}
					</p>
					<div className="use-case-no-snapshot-actions">
						{requirementsReady ? (
							<Button onClick={generation.generate} disabled={!generationAllowed || generation.isBusy}>
								{generation.isBusy ? "Generating Use Cases…" : "Generate Use Cases"}
							</Button>
						) : (
							<RouteLink
								className="route-primary-link"
								to={buildProjectPath(projectId, PROJECT_DESTINATIONS.REQUIREMENTS)}
								navigate={navigate}
							>
								Open Requirements
							</RouteLink>
						)}
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
