import { Dialog } from "../ui/dialog";
import { ListItem, CollectionState, List, CollectionToolbar } from "../ui/collections";
import { Disclosure, Alert } from "../ui/surfaces";
import { Button, Radio, Textarea, Input } from "../ui/controls";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { formatWorkspaceDate, formatWorkspaceLabel } from "../workspace/workspacePresentation";

const USE_CASE_GROUP_AUTO_EXPAND_LIMIT = 3;

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
	if (!review || typeof review !== "object") {
		return { label: "Machine review unavailable", tone: "neutral" };
	}
	return review.approved
		? { label: "Machine quality check passed", tone: "approved" }
		: { label: "Machine review requires attention", tone: "changes" };
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
		metrics.push({ label: "Use Case plan coverage", value: planCoverage });
	}
	if (requirementsAnalyzed) {
		metrics.push({ label: "Requirements analyzed", value: requirementsAnalyzed });
	}
	if (requirementsPlanned) {
		metrics.push({ label: "Requirements planned", value: requirementsPlanned });
	}
	if (scenarioCoverage) {
		metrics.push({ label: "Scenario coverage", value: scenarioCoverage });
	}
	if (mustHaveCoverage) {
		metrics.push({ label: "Must-have coverage", value: mustHaveCoverage });
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
				<span className="use-case-section-kicker">Machine quality review</span>
				<strong className={`use-case-review-state ${machineMeta.tone}`}>{machineMeta.label}</strong>
				{machineReview ? (
					<>
						<p>{machineReview.summary || "The automated reviewer did not provide a summary."}</p>
						<span className="use-case-score">
							Score {machineReview.score ?? "—"} / threshold {machineReview.threshold ?? "—"}
						</span>
					</>
				) : (
					<p>No automated quality decision is stored with this artifact.</p>
				)}
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

function ScenarioCard({ scenario }) {
	return (
		<ListItem className="use-case-scenario-card">
			<Disclosure className="scenario-details">
				<summary>
					<span className="scenario-summary-copy">
						<span className="use-case-scenario-type">{scenario.id || formatWorkspaceLabel(scenario.scenario_type, "Scenario")}</span>
						<strong>{scenario.title || scenario.objective || "Untitled scenario"}</strong>
					</span>
				</summary>
				<div className="scenario-expanded-content">
					<p>{scenario.objective || "No additional objective provided."}</p>
					<span>
						{formatWorkspaceLabel(scenario.scenario_type, "Scenario")} · {formatWorkspaceLabel(scenario.priority, "Unprioritized")} ·{" "}
						{scenario.must_have ? "Must have" : "Recommended"}
					</span>
				</div>
			</Disclosure>
		</ListItem>
	);
}

function CoverageContext({ analysis }) {
	if (!analysis) {
		return (
			<CollectionState as="p" kind="empty" className="use-case-context-empty">
				No additional constraint or risk analysis is attached to this requirement.
			</CollectionState>
		);
	}
	const constraints = normalizeList(analysis.field_constraints);
	const risks = normalizeList(analysis.risk_signals);
	const dependencies = normalizeList(analysis.dependencies);
	const permissions = normalizeList(analysis.role_permissions);
	const transitions = normalizeList(analysis.state_transitions);
	return (
		<div className="use-case-context-grid">
			<div>
				<h4>Constraints</h4>
				{constraints.length ? (
					<List>
						{constraints.map((constraint, index) => (
							<ListItem key={constraint.id || `${constraint.field_name}-${index}`}>
								<strong>{constraint.field_name || "Constraint"}</strong>: {constraint.description}
							</ListItem>
						))}
					</List>
				) : (
					<p>No field constraints identified.</p>
				)}
			</div>
			<div>
				<h4>Risks and gaps</h4>
				{risks.length || dependencies.length || permissions.length || transitions.length ? (
					<List>
						{risks.map((risk, index) => (
							<ListItem key={risk.id || `${risk.title}-${index}`}>
								<strong>{formatWorkspaceLabel(risk.severity, "Risk")}</strong>: {risk.title || risk.rationale}
							</ListItem>
						))}
						{dependencies.map((dependency) => (
							<ListItem key={dependency}>Dependency: {dependency}</ListItem>
						))}
						{permissions.map((permission, index) => (
							<ListItem key={permission.id || `${permission.role}-${permission.action}-${index}`}>
								Permission: {permission.role} can {`${permission.action || "act"}`.toLowerCase()}
							</ListItem>
						))}
						{transitions.map((transition, index) => (
							<ListItem key={transition.id || `${transition.from_state}-${transition.to_state}-${index}`}>
								Transition: {transition.from_state} → {transition.to_state}
							</ListItem>
						))}
					</List>
				) : (
					<p>No risks or dependencies identified.</p>
				)}
			</div>
		</div>
	);
}

function ReviewDecisionPanel({ stageState, snapshot, review, scenarioTotal, groupTotal }) {
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
	const formDisabled = isBusy || !snapshotMatches || refreshRequired;
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
					<Button variant="secondary" onClick={() => openDecision("request_changes")} disabled={isBusy}>
						Request changes
					</Button>
					<Button onClick={() => openDecision("approve")} disabled={isBusy || approvalBlocked}>
						Approve all
					</Button>
				</div>
				{approvalBlocked ? <p role="note">Stale version — regenerate before approval.</p> : null}
			</section>
			{dialogOpen ? (
				<div className="auth-dialog-overlay">
					<Dialog
						manageFocus
						initialFocusRef={commentRef}
						onClose={() => {
							if (!isBusy) setDialogOpen(false);
						}}
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
								if (commentRequired && !review.comment.trim()) {
									void review.submit();
									commentRef.current?.focus();
									return;
								}
								void review.submit();
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
									<strong>{commentInvalid ? "Comment required" : "Decision not saved"}</strong>
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
										: "Approval records this version as ready for downstream work."}
								</p>
								<Button type="button" variant="secondary" disabled={isBusy} onClick={() => setDialogOpen(false)}>
									Cancel
								</Button>
								<Button type="submit" disabled={!review.decision || formDisabled || (review.decision === "approve" && approvalBlocked)}>
									{review.isSubmitting
										? "Saving decision…"
										: !review.decision
											? "Choose a decision"
											: review.decision === "request_changes"
												? "Request changes"
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

export default function UseCaseReviewWorkbench({ project, snapshot, stageState, review }) {
	const searchId = useId();
	const searchRef = useRef(null);
	const [query, setQuery] = useState("");
	const [groupsExpandedOverride, setGroupsExpandedOverride] = useState(null);
	const [groupToggles, setGroupToggles] = useState({});
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
	// Small plans and search results are shown expanded; larger plans start
	// collapsed so reviewers can scan requirement headings without scrolling
	// through every scenario card.
	const groupsExpandedByDefault =
		groupsExpandedOverride ?? (query.trim().length > 0 || filteredGroups.length <= USE_CASE_GROUP_AUTO_EXPAND_LIMIT);
	const setAllGroupsExpanded = (expanded) => {
		setGroupsExpandedOverride(expanded);
		setGroupToggles({});
	};
	const updateQuery = (nextQuery) => {
		setQuery(nextQuery);
		setGroupsExpandedOverride(null);
		setGroupToggles({});
	};
	const scenarioTotal = coveragePlan.reduce((total, group) => total + normalizeList(group.scenarios).length, 0);
	const visibleScenarioTotal = filteredGroups.reduce((total, group) => total + group.scenarios.length, 0);
	const mustHaveTotal = coveragePlan.reduce(
		(total, group) => total + normalizeList(group.scenarios).filter((scenario) => scenario.must_have).length,
		0
	);
	const machineReview = payload.review || null;
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
			<section className="use-case-collection" aria-labelledby="use-case-collection-title">
				<CollectionToolbar as="div" className="use-case-collection-heading">
					<div>
						<span className="use-case-section-kicker">Review artifact</span>
						<h2 id="use-case-collection-title">Use case scenarios</h2>
						<p role="status" aria-live="polite">
							Showing {visibleScenarioTotal} of {scenarioTotal} scenarios across {filteredGroups.length} of {coveragePlan.length}{" "}
							requirement groups.
						</p>
					</div>
					<div className="use-case-search-field">
						<label htmlFor={searchId}>Search use cases</label>
						<Input
							id={searchId}
							type="search"
							value={query}
							ref={searchRef}
							onChange={(event) => updateQuery(event.target.value)}
							placeholder="Requirement, scenario, risk, or constraint"
						/>
					</div>
				</CollectionToolbar>

				{filteredGroups.length > 1 ? (
					<div className="use-case-group-toolbar">
						<Button
							variant="plain"
							type="button"
							className="use-case-group-toggle-all"
							onClick={() => setAllGroupsExpanded(!groupsExpandedByDefault)}
						>
							{groupsExpandedByDefault ? "Collapse all groups" : "Expand all groups"}
						</Button>
					</div>
				) : null}

				{filteredGroups.length ? (
					<List as="div" variant="grouped" className="use-case-group-list" aria-label="Use Case groups">
						{filteredGroups.map((group, groupIndex) => {
							const groupKey = group.requirement_id || normalizeText(group.requirement_text) || `group-${groupIndex}`;
							const titleId = `use-case-group-${groupIndex}-${`${group.requirement_id || "requirement"}`.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
							const isOpen = groupToggles[groupKey] ?? groupsExpandedByDefault;
							return (
								<section
									className="use-case-group"
									aria-label={`${group.requirement_id || "Unidentified"} · ${group.requirement_text || "Requirement coverage"}`}
									key={groupKey}
								>
									<Disclosure
										className="use-case-group-details"
										open={isOpen}
										onToggle={(event) => {
											const nextOpen = event.currentTarget.open;
											if (nextOpen !== isOpen) {
												setGroupToggles((previous) => ({ ...previous, [groupKey]: nextOpen }));
											}
										}}
									>
										<summary className="use-case-group-heading">
											<div>
												<span>Source requirement {group.requirement_id || "Unidentified"}</span>
												<h3 id={titleId}>{group.requirement_text || "Requirement coverage"}</h3>
											</div>
											<strong>
												{group.scenarios.length} scenario{group.scenarios.length === 1 ? "" : "s"}
											</strong>
										</summary>
										{group.scenarios.length ? (
											<List
												className="use-case-scenario-list"
												aria-label={`Scenarios for ${group.requirement_id || "unidentified requirement"}`}
											>
												{group.scenarios.map((scenario, scenarioIndex) => (
													<ScenarioCard key={scenario.id || `${group.requirement_id}-${scenarioIndex}`} scenario={scenario} />
												))}
											</List>
										) : (
											<CollectionState as="p" kind="empty" className="use-case-context-empty">
												No scenarios were generated for this requirement.
											</CollectionState>
										)}
										<Disclosure className="use-case-coverage-context">
											<summary aria-label={`Coverage context for ${group.requirement_id || "unidentified requirement"}`}>
												Coverage context
											</summary>
											<CoverageContext analysis={group.analysis} />
										</Disclosure>
									</Disclosure>
								</section>
							);
						})}
					</List>
				) : (
					<CollectionState as="div" kind="filtered" className="use-case-search-empty" role="status">
						<strong>No use cases match “{query}”.</strong>
						<Button
							type="button"
							className="secondary"
							onClick={() => {
								updateQuery("");
								window.requestAnimationFrame(() => searchRef.current?.focus());
							}}
						>
							Clear search
						</Button>
					</CollectionState>
				)}
			</section>

			<Disclosure className="use-case-provenance">
				<summary>Details</summary>
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
			/>
		</div>
	);
}
