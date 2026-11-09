import { useState } from "react";
import ClarificationScopeNotice from "../components/context/ClarificationScopeNotice";
import { generationRequirementRows, hasApprovedGenerationScope } from "../utils/requirementScope";
import useUseCaseGeneration from "../hooks/useUseCaseGeneration";
import GuidanceUsed from "../components/knowledge/GuidanceUsed";
import GenerationGuidance from "../components/knowledge/GenerationGuidance";
import KnowledgeSuggestion from "../components/knowledge/KnowledgeSuggestion";
import { Badge } from "../components/ui/surfaces";
import { Button, Link } from "../components/ui/controls";
import ProjectPageHeader from "../components/layout/ProjectPageHeader";
import RouteLink from "../app/RouteLink";
import { PROJECT_DESTINATIONS, buildProjectPath } from "../app/workflowRoutes";
import UseCaseReviewWorkbench from "../components/reviews/UseCaseReviewWorkbench";
import UseCaseRefreshDialog from "../components/reviews/UseCaseRefreshDialog";
import useUseCaseReview from "../hooks/useUseCaseReview";

export default function UseCaseReviewPage({
	project,
	identity,
	request,
	navigate,
	scopePath,
	onDecisionCommitted,
	onReloadLatest,
	onGenerated,
	generationDisabled = false,
	contextRefreshRequired = false,
	continuationPanel = null,
	continuation = null,
	generateOnApproval = false,
	onBack,
	onNext,
}) {
	const projectId = project?.project_id || "";
	const [refreshDialogScope, setRefreshDialogScope] = useState(null);
	const refreshScope = `${identity}:${projectId}:${project?.current_revision}`;
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
		continuation,
	});
	const requirementsSnapshot = project?.current_snapshots?.requirements || null;
	const requirementsState = project?.stage_state?.requirements || null;
	const requirementsReady = hasApprovedGenerationScope(project);
	const generationAllowed = requirementsReady && project?.status === "active" && !generationDisabled && !contextRefreshRequired;
	const responseStageState = review.response?.use_cases_state;
	const effectiveStageState = responseStageState?.current_snapshot_id === snapshot?.snapshot_id ? responseStageState : stageState;
	const latestHumanReview = effectiveStageState?.metadata?.latest_human_review;
	const matchingHumanDecision = latestHumanReview?.snapshot_id === snapshot?.snapshot_id ? latestHumanReview?.decision || "" : "";
	const contextSnapshot = project?.current_snapshots?.context;
	const contextState = project?.stage_state?.context;
	const contextReady = !contextSnapshot || (contextState?.current_snapshot_id === contextSnapshot.snapshot_id && !contextState.stale);
	const scopeAvailable =
		effectiveStageState?.stale &&
		effectiveStageState.approved &&
		matchingHumanDecision === "approve" &&
		(snapshot?.metadata?.source_requirements_snapshot_id || snapshot?.source_snapshot_id) &&
		contextReady;
	const generationBusy = generation.isBusy || review.isSubmitting || review.isReloading;
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
			className="use-case-review-page compact-workbench"
			aria-labelledby="use-case-review-title"
			aria-busy={generation.isBusy || review.isSubmitting || review.isReloading || undefined}
			tabIndex={-1}
		>
			<ProjectPageHeader
				title="Use Cases"
				compact
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
							{scopeAvailable && (
								<Button size="compact" onClick={() => setRefreshDialogScope(refreshScope)} disabled={!generationAllowed || generationBusy}>
									Review refresh scope
								</Button>
							)}
							<Button
								variant={effectiveStageState?.stale && !scopeAvailable ? "primary" : "secondary"}
								size="compact"
								onClick={() => void generation.generate()}
								disabled={!generationAllowed || generationBusy}
							>
								{generation.isBusy ? "Generating Use Cases…" : "Regenerate Use Cases"}
							</Button>
							{matchingHumanDecision !== "approve" && (
								<Link className="use-case-skip-review-link" href="#use-case-review-decision">
									Skip to review decision
								</Link>
							)}
						</div>
					) : null
				}
			/>
			{continuationPanel}
			{scopeAvailable && generationAllowed && refreshDialogScope === refreshScope && (
				<UseCaseRefreshDialog
					requirements={generationRequirementRows(requirementsSnapshot?.payload?.requirements || [])}
					priorPlan={snapshot.payload.coverage_plan || []}
					priorIdentities={snapshot.metadata?.source_requirement_identities}
					contextChanged={(snapshot.metadata?.source_context_snapshot_id || null) !== (contextSnapshot?.snapshot_id || null)}
					onClose={() => setRefreshDialogScope(null)}
					onConfirm={(ids) => {
						setRefreshDialogScope(null);
						void generation.generate(ids);
					}}
				/>
			)}

			<ClarificationScopeNotice project={project} onOpen={() => navigate(scopePath || buildProjectPath(projectId, "context"))} />
			{!requirementsReady && snapshot ? (
				<p>
					Approve the current requirements before regenerating.{" "}
					<RouteLink to={scopePath || buildProjectPath(projectId, PROJECT_DESTINATIONS.REQUIREMENTS)} navigate={navigate}>
						{scopePath ? "Review scope" : "Open Requirements"}
					</RouteLink>
				</p>
			) : null}
			{contextRefreshRequired && (
				<p role="status">
					Saved context is stale.{" "}
					<RouteLink to={buildProjectPath(projectId, PROJECT_DESTINATIONS.CONTEXT)} navigate={navigate}>
						Analyze context again
					</RouteLink>{" "}
					before generating scenarios.
				</p>
			)}
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
			) : generation.message ? (
				<p role="status" aria-live="polite">
					{generation.message}
				</p>
			) : null}

			{snapshot ? (
				<fieldset disabled={generation.isBusy} className="use-case-generation-review">
					<UseCaseReviewWorkbench
						identity={identity}
						key={`${identity}:${projectId}`}
						project={project}
						snapshot={snapshot}
						stageState={stageState}
						review={review}
						generateOnApproval={generateOnApproval}
						approvalRecovery={continuation?.unresolved}
						onContinue={matchingHumanDecision === "approve" && !effectiveStageState?.stale ? onNext : undefined}
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
							<Button onClick={() => void generation.generate()} disabled={!generationAllowed || generation.isBusy}>
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
			<div className="workbench-support">
				<GenerationGuidance
					key={`${identity}:${projectId}:${project?.current_revision}`}
					request={request}
					projectId={projectId}
					stage="use_cases"
					compact
				/>
				{snapshot ? (
					<GuidanceUsed
						key={`guidance:${snapshot.snapshot_id}`}
						manifest={snapshot.metadata?.guidance}
						request={request}
						projectId={projectId}
					/>
				) : null}
				<KnowledgeSuggestion
					key={`suggestion:${review.response?.review?.review_id || "none"}`}
					suggestion={review.response?.knowledge_suggestion}
					request={request}
					projectId={projectId}
				/>
			</div>
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
