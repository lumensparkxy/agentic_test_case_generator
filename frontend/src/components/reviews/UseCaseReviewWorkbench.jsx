import useReviewDraft from "../../hooks/useReviewDraft";
import DraftRecoveryNotice from "./DraftRecoveryNotice";
import ScenarioReviewTable from "./ScenarioReviewTable";
import { Dialog } from "../ui/dialog";
import { ListItem, List } from "../ui/collections";
import { Disclosure, Alert } from "../ui/surfaces";
import { Button, Radio, Textarea, Input } from "../ui/controls";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { formatWorkspaceDate } from "../workspace/workspacePresentation";

const normalizeList = (value) => (Array.isArray(value) ? value.filter(Boolean) : []);
const normalizeText = (value) => `${value || ""}`.trim().toLowerCase();

const getHumanReview = (stageState, snapshotId) => {
	const review = stageState?.metadata?.latest_human_review;
	return review && review.snapshot_id === snapshotId ? review : null;
};

const getHumanReviewMeta = (review) => {
	if (review?.decision === "approve") {
		return { label: "Human approved", tone: "approved", description: "A reviewer approved this exact artifact version." };
	}
	if (review?.decision === "request_changes") {
		return { label: "Changes requested", tone: "changes", description: "A reviewer requested changes to this exact artifact version." };
	}
	return { label: "Pending human decision", tone: "pending", description: "No human decision is recorded for this artifact version." };
};

const getMachineReviewMeta = (review) => {
	const structural = review?.structural_checks || review;
	return structural
		? structural.approved === true
			? { label: "Structural checks passed", tone: "approved" }
			: { label: "Structural review requires attention", tone: "changes" }
		: { label: "Structural checks unavailable", tone: "neutral" };
};

const formatRatio = (value) => {
	const number = Number(value);
	if (!Number.isFinite(number)) {
		return null;
	}
	return `${Math.round(number * 100)}%`;
};

const formatCoverageCount = (covered, total) => {
	const coveredCount = Number(covered);
	const totalCount = Number(total);
	if (!Number.isInteger(coveredCount) || coveredCount < 0 || !Number.isInteger(totalCount) || totalCount < 0) {
		return null;
	}
	return `${coveredCount} / ${totalCount}`;
};

const getCoverageMetrics = (coverageMetrics, scenarioTotal, groupTotal, mustHaveTotal) => {
	const metrics = [
		{ label: "Planned scenarios", value: scenarioTotal },
		{ label: "Requirement groups", value: groupTotal },
		{ label: "Must-have scenarios", value: mustHaveTotal },
	];
	const planCoverage = formatRatio(coverageMetrics?.use_case_plan_coverage_ratio);
	const requirementsAnalyzed = formatCoverageCount(coverageMetrics?.requirements_with_analysis, coverageMetrics?.requirements_total);
	const requirementsPlanned = formatCoverageCount(coverageMetrics?.requirements_with_coverage_plan, coverageMetrics?.requirements_total);
	const scenarioCoverage = formatRatio(coverageMetrics?.scenario_coverage_ratio);
	const mustHaveCoverage = formatRatio(coverageMetrics?.must_have_scenario_coverage_ratio);
	if (planCoverage) {
		metrics.push({ label: "Structural Use Case plan coverage", value: planCoverage });
	}
	if (requirementsAnalyzed) {
		metrics.push({ label: "Requirements analyzed", value: requirementsAnalyzed });
	}
	if (requirementsPlanned) {
		metrics.push({ label: "Requirements planned", value: requirementsPlanned });
	}
	if (scenarioCoverage) {
		metrics.push({ label: "Linked scenario coverage", value: scenarioCoverage });
	}
	if (mustHaveCoverage) {
		metrics.push({ label: "Linked must-have coverage", value: mustHaveCoverage });
	}
	return metrics;
};

const getAnalysisSearchText = (analysis) =>
	[
		analysis?.requirement_id,
		analysis?.requirement_text,
		...normalizeList(analysis?.field_constraints).flatMap((item) => [item.field_name, item.description]),
		...normalizeList(analysis?.risk_signals).flatMap((item) => [item.title, item.rationale, item.severity]),
		...normalizeList(analysis?.dependencies),
	]
		.map(normalizeText)
		.join(" ");

const filterGroups = (coveragePlan, analysisByRequirement, query) => {
	const term = normalizeText(query);
	return coveragePlan.flatMap((group) => {
		const analysis = analysisByRequirement.get(group.requirement_id) || null;
		const scenarios = normalizeList(group.scenarios);
		if (!term) {
			return [{ ...group, scenarios, analysis }];
		}
		const groupMatches = [group.requirement_id, group.requirement_text, getAnalysisSearchText(analysis)]
			.map(normalizeText)
			.some((value) => value.includes(term));
		const matchingScenarios = scenarios.filter((scenario) =>
			[scenario.id, scenario.title, scenario.objective, scenario.scenario_type, scenario.priority]
				.map(normalizeText)
				.some((value) => value.includes(term))
		);
		if (!groupMatches && matchingScenarios.length === 0) {
			return [];
		}
		return [{ ...group, scenarios: groupMatches ? scenarios : matchingScenarios, analysis }];
	});
};

function ArtifactSummary({ project, snapshot, stageState, scenarioTotal, groupTotal, machineReview, humanReview }) {
	const machineMeta = getMachineReviewMeta(machineReview);
	const structural = machineReview?.structural_checks || machineReview;
	const semantic = machineReview?.semantic_assessment;
	const humanMeta = getHumanReviewMeta(humanReview);
	return (
		<div className="use-case-review-summary-grid">
			<section className="use-case-artifact-summary" aria-labelledby="use-case-artifact-summary-title">
				<div>
					<span className="use-case-section-kicker">Current artifact</span>
					<h2 id="use-case-artifact-summary-title">
						{scenarioTotal} scenario{scenarioTotal === 1 ? "" : "s"}
					</h2>
					<p>
						Across {groupTotal} requirement group{groupTotal === 1 ? "" : "s"} in {project.name}.
					</p>
				</div>
				<div className="use-case-artifact-facts" aria-label="Artifact status">
					<span>Use Cases v{snapshot.version ?? stageState?.version ?? "—"}</span>
					<span className={`use-case-freshness ${stageState?.stale ? "stale" : "current"}`}>
						{stageState?.stale ? "Stale artifact" : "Current artifact"}
					</span>
					{snapshot.created_at ? <time dateTime={snapshot.created_at}>Generated {formatWorkspaceDate(snapshot.created_at)}</time> : null}
				</div>
			</section>

			<section className="use-case-review-state-card machine" aria-label="Machine quality review">
				<span className="use-case-section-kicker">Structural checks</span>
				<strong className={`use-case-review-state ${machineMeta.tone}`}>{machineMeta.label}</strong>
				{machineReview ? (
					<>
						<p>Checks of requirement groups, scenario categories and identifiers. These do not establish behavioral coverage.</p>
						<span className="use-case-score">
							Structural score {structural.score ?? "—"} / threshold {structural.threshold ?? "—"}
						</span>
					</>
				) : (
					<p>No structural result is stored with this artifact.</p>
				)}
				<span className="use-case-section-kicker">Semantic assessment</span>
				<strong
					className={`use-case-review-state ${semantic?.status === "passed" ? "approved" : semantic?.status === "needs_review" ? "changes" : "neutral"}`}
				>
					{semantic?.status === "passed"
						? "Semantic checks passed"
						: semantic?.status === "needs_review"
							? "Semantic review requires attention"
							: "Semantic assessment unavailable"}
				</strong>
				<p>
					{semantic?.summary ||
						"No semantic assessment is stored for this artifact. Review triggers, observable outcomes, distinct behavior and unsupported assumptions."}
				</p>
			</section>

			<section className="use-case-review-state-card human" aria-label="Human review status">
				<span className="use-case-section-kicker">Human review</span>
				<strong className={`use-case-review-state ${humanMeta.tone}`}>{humanMeta.label}</strong>
				<p>{humanMeta.description}</p>
				{humanReview?.reviewed_at ? (
					<time dateTime={humanReview.reviewed_at}>Decided {formatWorkspaceDate(humanReview.reviewed_at)}</time>
				) : null}
				{humanReview ? (
					<span className="use-case-reviewer">Reviewed by {humanReview.reviewer_name || "an authenticated reviewer"}</span>
				) : null}
				{humanReview?.comment ? <blockquote>{humanReview.comment}</blockquote> : null}
			</section>
		</div>
	);
}

function ReviewDecisionPanel({ stageState, snapshot, review, scenarioTotal, groupTotal, hasDrafts, generateOnApproval, approvalRecovery }) {
	const [dialogOpen, setDialogOpen] = useState(false);
	useEffect(() => {
		if (review.status === "success") setDialogOpen(false);
	}, [review.status]);
	const formRef = useRef(null);
	const commentRef = useRef(null);
	const storedHumanReview = getHumanReview(review.response?.use_cases_state || stageState, snapshot.snapshot_id);
	const responseReview = review.response?.review?.snapshot_id === snapshot.snapshot_id ? review.response.review : null;
	const humanReview = responseReview
		? { ...storedHumanReview, ...responseReview, reviewed_at: responseReview.decided_at || storedHumanReview?.reviewed_at }
		: storedHumanReview;
	const humanMeta = getHumanReviewMeta(humanReview);
	const isBusy = review.isSubmitting || review.isReloading;
	const refreshRequired = review.status === "refresh_error";
	const commentRequired = review.decision === "request_changes";
	const commentInvalid = commentRequired && !review.comment.trim() && review.status === "error";
	const snapshotMatches = stageState?.current_snapshot_id === snapshot.snapshot_id;
	const approvalBlocked = Boolean(stageState?.stale);
	const formDisabled = isBusy || !snapshotMatches || refreshRequired || hasDrafts;
	const generateTests = generateOnApproval && review.decision === "approve";
	const decisionBlocked = approvalRecovery && !generateTests;
	const focusDecisionPanel = () => window.requestAnimationFrame(() => formRef.current?.focus());
	const handleReload = async () => {
		await review.reloadLatest();
		focusDecisionPanel();
	};
	const handleRetry = async () => {
		await review.retry();
		focusDecisionPanel();
	};
	const openDecision = (decision) => {
		review.setDecision(decision);
		setDialogOpen(true);
	};
	const closeDecision = () => {
		if (isBusy) return;
		review.dismissDecision();
		setDialogOpen(false);
	};
	return (
		<>
			<section id="use-case-review-decision" tabIndex={-1} className="use-case-review-bar" aria-label="Use Cases review actions">
				<div>
					<strong>{humanMeta.label}</strong>
					<p>
						One decision for all {scenarioTotal} scenarios · {groupTotal} requirement groups
					</p>
					<div className="use-case-review-announcement" role="status" aria-label="Review outcome" aria-live="polite" aria-atomic="true">
						{review.announcement}
					</div>
				</div>
				<div className="use-case-review-bar-actions">
					{humanReview ? (
						<Button variant="plain" onClick={() => setDialogOpen(true)}>
							Review details
						</Button>
					) : null}
					<Button
						variant="secondary"
						onClick={() => openDecision("request_changes")}
						disabled={isBusy || hasDrafts || !scenarioTotal || approvalRecovery}
					>
						Request changes
					</Button>
					<Button
						variant={hasDrafts || humanReview?.decision === "approve" || approvalBlocked ? "secondary" : "primary"}
						onClick={() => openDecision("approve")}
						disabled={isBusy || approvalBlocked || hasDrafts || !scenarioTotal || (approvalRecovery && !generateOnApproval)}
					>
						Approve all
					</Button>
				</div>
				{hasDrafts ? <p role="note">Save or discard row edits before making a whole-version decision.</p> : null}
				{approvalBlocked ? <p role="note">Stale version — regenerate before approval.</p> : null}
			</section>
			{dialogOpen ? (
				<div className="auth-dialog-overlay">
					<Dialog
						manageFocus
						initialFocusRef={commentRef}
						onClose={closeDecision}
						className="use-case-review-dialog"
						aria-labelledby="use-case-review-dialog-title"
						aria-describedby="use-case-review-scope"
					>
						<form
							ref={formRef}
							className="use-case-decision-panel"
							aria-label="Human review decision"
							aria-busy={isBusy || undefined}
							tabIndex={-1}
							noValidate
							onSubmit={(event) => {
								event.preventDefault();
								if (decisionBlocked) return;
								if (commentRequired && !review.comment.trim()) {
									void review.submit();
									commentRef.current?.focus();
									return;
								}
								void review.submit({ generateTests });
							}}
						>
							<div className="use-case-decision-heading">
								<div>
									<span className="use-case-section-kicker">Human decision</span>
									<h2 id="use-case-review-dialog-title">Review all Use Cases</h2>
									<p id="use-case-review-scope">
										Your decision applies to all {scenarioTotal} scenarios across {groupTotal} requirement groups in Use Cases v
										{snapshot.version}, including scenarios hidden by search. It does not apply to a single group.
									</p>
									<p>
										{review.decision === "approve"
											? "Approve all sets every scenario to Approved, including scenarios currently marked Changes requested."
											: "Request changes marks every scenario as Changes requested."}
									</p>
								</div>
							</div>

							{humanReview ? (
								<div className="use-case-recorded-decision">
									<strong>Latest recorded human decision</strong>
									<p>
										{humanReview.reviewer_name
											? `Reviewed by ${humanReview.reviewer_name}.`
											: "Recorded by an authenticated reviewer. Reviewer provenance is available in Details."}
									</p>
									{humanReview.comment ? <blockquote>{humanReview.comment}</blockquote> : null}
								</div>
							) : null}

							{!snapshotMatches ? (
								<Alert as="div" tone="warning" className="use-case-review-alert conflict" role="alert">
									<strong>The loaded artifact is no longer the project’s current Use Cases version.</strong>
									<p>Reload the latest project state before making a decision.</p>
									<Button type="button" className="secondary" onClick={() => void handleReload()} disabled={isBusy}>
										Reload latest
									</Button>
								</Alert>
							) : null}

							{stageState?.stale ? (
								<div className="use-case-review-alert warning" role="note">
									<strong>This artifact is stale.</strong>
									<p>
										{stageState.stale_reason || "Upstream project inputs changed after this artifact was generated."} Approval is
										unavailable until it is regenerated.
									</p>
								</div>
							) : null}

							<fieldset className="use-case-decision-options" disabled={formDisabled}>
								<legend>Choose a decision</legend>
								<label className={review.decision === "approve" ? "selected" : ""}>
									<Radio
										type="radio"
										name="use-case-review-decision"
										value="approve"
										checked={review.decision === "approve"}
										onChange={() => review.setDecision("approve")}
										disabled={approvalBlocked}
									/>
									<span>
										<strong>Approve</strong>
										<small>Accept this exact artifact for downstream work.</small>
									</span>
								</label>
								<label className={review.decision === "request_changes" ? "selected" : ""}>
									<Radio
										type="radio"
										name="use-case-review-decision"
										value="request_changes"
										checked={review.decision === "request_changes"}
										onChange={() => review.setDecision("request_changes")}
									/>
									<span>
										<strong>Request changes</strong>
										<small>Return actionable feedback without modifying the stored artifact.</small>
									</span>
								</label>
							</fieldset>

							<div className="use-case-comment-field">
								<label htmlFor="use-case-review-comment">
									Review comment {commentRequired ? <span>Required</span> : <span>Optional</span>}
								</label>
								<Textarea
									id="use-case-review-comment"
									ref={commentRef}
									value={review.comment}
									onChange={(event) => review.setComment(event.target.value)}
									maxLength={4000}
									rows={4}
									disabled={formDisabled}
									required={commentRequired}
									aria-required={commentRequired}
									aria-invalid={commentInvalid || undefined}
									aria-errormessage={commentInvalid ? "use-case-review-error" : undefined}
									aria-describedby="use-case-review-comment-help"
								/>
								<div id="use-case-review-comment-help" className="use-case-comment-help">
									<span>
										{review.decision === "request_changes"
											? "Explain what must change before approval."
											: "Add decision context for the audit trail."}
									</span>
									<span>{review.comment.length}/4000</span>
								</div>
							</div>

							{review.status === "conflict" ? (
								<Alert as="div" tone="warning" className="use-case-review-alert conflict" role="alert">
									<strong>Reload required</strong>
									<p>{review.error}</p>
									<Button type="button" className="secondary" onClick={() => void handleReload()} disabled={isBusy}>
										{review.isReloading ? "Reloading…" : "Reload latest"}
									</Button>
								</Alert>
							) : review.status === "refresh_error" ? (
								<Alert as="div" tone="warning" className="use-case-review-alert warning" role="alert">
									<strong>Decision saved; refresh required</strong>
									<p>{review.error}</p>
									<Button type="button" className="secondary" onClick={() => void handleReload()} disabled={isBusy}>
										{review.isReloading ? "Reloading…" : "Reload latest"}
									</Button>
								</Alert>
							) : review.status === "error" ? (
								<Alert as="div" tone="danger" id="use-case-review-error" className="use-case-review-alert error" role="alert">
									<strong>
										{commentInvalid ? "Comment required" : approvalRecovery ? "Approval outcome unconfirmed" : "Decision not saved"}
									</strong>
									<p>{review.error}</p>
									{commentInvalid ? null : (
										<Button type="button" className="secondary" onClick={() => void handleRetry()} disabled={isBusy || formDisabled}>
											Retry
										</Button>
									)}
								</Alert>
							) : null}

							<div className="use-case-decision-actions">
								<p>
									{review.decision === "request_changes"
										? "Your feedback will be recorded with the decision."
										: generateTests
											? "Approval queues test generation from this exact scenario plan."
											: "Approval records this version as ready for downstream work."}
								</p>
								<Button type="button" variant="secondary" disabled={isBusy} onClick={closeDecision}>
									Cancel
								</Button>
								<Button
									type="submit"
									disabled={!review.decision || formDisabled || decisionBlocked || (review.decision === "approve" && approvalBlocked)}
								>
									{review.isSubmitting
										? "Saving decision…"
										: !review.decision
											? "Choose a decision"
											: review.decision === "request_changes"
												? "Request changes"
												: generateTests
													? "Approve scenarios and generate tests"
													: "Approve Use Cases"}
								</Button>
							</div>
						</form>
					</Dialog>
				</div>
			) : null}
		</>
	);
}

export default function UseCaseReviewWorkbench({
	identity,
	project,
	snapshot,
	stageState,
	review,
	onContinue,
	generateOnApproval,
	approvalRecovery,
}) {
	const searchId = useId();
	const searchRef = useRef(null);
	const recovery = useReviewDraft({
		identity,
		projectId: project.project_id,
		snapshotId: snapshot.snapshot_id,
		name: "scenario-review",
		initialValue: { query: "", items: {}, revision: project.current_revision, statusFilter: "", flagFilters: [] },
	});
	const { query, items: drafts } = recovery.value;
	const setQuery = (query) => recovery.setValue((old) => ({ ...old, query }));
	const setDrafts = (next) =>
		recovery.setValue((old) => ({
			...old,
			revision: Object.keys(old.items).length ? old.revision : project.current_revision,
			items: typeof next === "function" ? next(old.items) : next,
		}));
	const payload = snapshot.payload || {};
	const coveragePlan = normalizeList(payload.coverage_plan);
	const requirementAnalysis = normalizeList(payload.requirement_analysis);
	const analysisByRequirement = useMemo(
		() => new Map(requirementAnalysis.map((analysis) => [analysis.requirement_id, analysis])),
		[requirementAnalysis]
	);
	const filteredGroups = useMemo(
		() => filterGroups(coveragePlan, analysisByRequirement, query),
		[analysisByRequirement, coveragePlan, query]
	);
	const updateQuery = setQuery;
	const scenarioTotal = coveragePlan.reduce((total, group) => total + normalizeList(group.scenarios).length, 0);
	const mustHaveTotal = coveragePlan.reduce(
		(total, group) => total + normalizeList(group.scenarios).filter((scenario) => scenario.must_have).length,
		0
	);
	const machineReview = payload.review || null;
	const requirementFindings = normalizeList(machineReview?.semantic_assessment?.requirement_findings);
	const findingsTitleId = useId();
	const effectiveStageState = review.response?.use_cases_state || stageState;
	const storedHumanReview = getHumanReview(effectiveStageState, snapshot.snapshot_id);
	const responseReview = review.response?.review?.snapshot_id === snapshot.snapshot_id ? review.response.review : null;
	const humanReview = responseReview
		? { ...storedHumanReview, ...responseReview, reviewed_at: responseReview.decided_at || storedHumanReview?.reviewed_at }
		: storedHumanReview;
	const coverageMetrics = getCoverageMetrics(payload.coverage_metrics, scenarioTotal, coveragePlan.length, mustHaveTotal);
	const machineIssues = [
		...new Set(
			[...normalizeList(machineReview?.blocking_issues), ...normalizeList(machineReview?.unmet_criteria)]
				.map((issue) => `${issue || ""}`.trim())
				.filter(Boolean)
		),
	];

	useEffect(() => {
		const focusSearch = (event) => {
			if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
				const target = event.target;
				if (target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) {
					return;
				}
				event.preventDefault();
				searchRef.current?.focus();
			}
		};
		window.addEventListener("keydown", focusSearch);
		return () => window.removeEventListener("keydown", focusSearch);
	}, []);

	return (
		<div className="use-case-review-workbench">
			<DraftRecoveryNotice draft={recovery} />
			<DraftRecoveryNotice draft={review.recovery} />
			{requirementFindings.length > 0 && (
				<Alert as="section" tone="warning" aria-labelledby={findingsTitleId}>
					<h2 id={findingsTitleId}>Accepted boundary coverage needs review</h2>
					<p>Review the exact inputs below and add scenarios that verify the source-defined acceptance outcome.</p>
					<List>
						{requirementFindings.map((finding) => (
							<ListItem key={finding.range_id}>
								<strong>{finding.requirement_id}</strong> · “{finding.source_excerpt}” · Inputs without verified coverage{" "}
								<strong>{finding.missing_endpoints.join(", ")}</strong>
							</ListItem>
						))}
					</List>
				</Alert>
			)}
			<section className="use-case-collection" aria-labelledby="use-case-collection-title">
				<h2 className="sr-only" id="use-case-collection-title">
					Use case scenarios
				</h2>
				<ScenarioReviewTable
					key={snapshot.snapshot_id}
					groups={filteredGroups}
					allGroups={coveragePlan}
					assessment={machineReview?.semantic_assessment}
					state={effectiveStageState}
					snapshot={snapshot}
					revision={project.current_revision}
					review={review}
					drafts={drafts}
					recovery={recovery}
					setDrafts={setDrafts}
					onContinue={onContinue}
					search={
						<Input
							id={searchId}
							type="search"
							aria-label="Search use cases"
							value={query}
							ref={searchRef}
							onChange={(event) => updateQuery(event.target.value)}
							placeholder="Search use cases"
						/>
					}
				/>
			</section>

			<div className="use-case-meta-row">
				<div className="use-case-compact-summary">
					<strong>{scenarioTotal} scenarios</strong>
					<span>{coveragePlan.length} requirement groups</span>
					<span>Use Cases v{snapshot.version ?? stageState?.version ?? "—"}</span>
					<span>{effectiveStageState?.stale ? "Stale artifact" : "Current artifact"}</span>
				</div>
				<Disclosure className="use-case-supporting-details">
					<summary>Quality, coverage and review history</summary>
					<ArtifactSummary
						project={project}
						snapshot={snapshot}
						stageState={effectiveStageState}
						scenarioTotal={scenarioTotal}
						groupTotal={coveragePlan.length}
						machineReview={machineReview}
						humanReview={humanReview}
					/>

					<section className="use-case-coverage-metrics" aria-label="Coverage metrics">
						{coverageMetrics.map((metric) => (
							<div key={metric.label}>
								<span>{metric.label}</span>
								<strong>{metric.value}</strong>
							</div>
						))}
					</section>

					{machineIssues.length ? (
						<section className="use-case-machine-issues" aria-labelledby="use-case-machine-issues-title">
							<h2 id="use-case-machine-issues-title">Machine review findings</h2>
							<List>
								{machineIssues.map((issue) => (
									<ListItem key={issue}>{issue}</ListItem>
								))}
							</List>
						</section>
					) : null}
				</Disclosure>
			</div>
			<Disclosure className="use-case-provenance">
				<summary aria-label="Artifact details">Details</summary>
				<dl>
					<div>
						<dt>Snapshot ID</dt>
						<dd>{snapshot.snapshot_id}</dd>
					</div>
					<div>
						<dt>Artifact project revision</dt>
						<dd>{snapshot.project_revision ?? "—"}</dd>
					</div>
					<div>
						<dt>Current project revision</dt>
						<dd>{project.current_revision ?? "—"}</dd>
					</div>
					<div>
						<dt>Operation</dt>
						<dd>{snapshot.operation || "—"}</dd>
					</div>
					{snapshot.metadata?.agent_contract_version ? (
						<div>
							<dt>Agent contract</dt>
							<dd>{snapshot.metadata.agent_contract_version}</dd>
						</div>
					) : null}
					{humanReview?.reviewer_user_id ? (
						<div>
							<dt>Reviewer ID</dt>
							<dd>{humanReview.reviewer_user_id}</dd>
						</div>
					) : null}
				</dl>
			</Disclosure>
			<ReviewDecisionPanel
				stageState={effectiveStageState}
				snapshot={snapshot}
				review={review}
				scenarioTotal={scenarioTotal}
				groupTotal={coveragePlan.length}
				hasDrafts={Object.keys(drafts).length > 0}
				generateOnApproval={generateOnApproval}
				approvalRecovery={approvalRecovery}
			/>
		</div>
	);
}
