import { CircleAlert, CircleCheck, ClipboardCheck, Info, ListChecks, Sparkles, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
	formatWorkspaceDate,
	formatWorkspaceLabel,
	formatWorkspaceStatus,
	getWorkItemDestination,
	getWorkItemTitle,
	getWorkspaceStatusTone,
	normalizeWorkspaceList,
} from "../workspace/workspacePresentation";
import { ProjectOpenLink } from "../workspace/WorkspacePrimitives";

const COMPLETED_STATUSES = new Set(["approved", "completed", "passed"]);
const STAGE_ORDER = Object.freeze([
	"requirements",
	"context",
	"use_cases",
	"impact_analysis",
	"test_cases",
	"review",
	"automation",
	"execution",
	"reports",
]);

const COUNT_UNIT_BY_STAGE = Object.freeze({
	requirements: "requirement",
	use_cases: "scenario",
	impact_analysis: "change",
	test_cases: "test case",
	review: "test case",
	automation: "candidate",
	execution: "check",
	reports: "evidence item",
});

const itemIdentity = (item) => `${item?.project_id || ""}::${item?.stage || ""}::${item?.current_snapshot_id || ""}`;

export function deduplicateReviewItems(items) {
	const seen = new Set();
	return normalizeWorkspaceList(items).filter((item) => {
		const key = itemIdentity(item);
		if (seen.has(key)) return false;
		seen.add(key);
		return true;
	});
}

export const isActionableReviewItem = (item) =>
	item?.enabled === true && item?.kind !== "information" && !COMPLETED_STATUSES.has(item?.status);

const formatCount = (item) => {
	if (!Number.isInteger(item?.count)) return "";
	const unit = COUNT_UNIT_BY_STAGE[item.stage] || "item";
	return `${item.count} ${unit}${item.count === 1 ? "" : "s"}`;
};

const kindMeta = (item) => {
	if (item?.kind === "review") return { label: "Review", Icon: ClipboardCheck };
	if (item?.kind === "information") return { label: "Information", Icon: Info };
	return { label: "Action", Icon: Wrench };
};

const statusIcon = (status) => {
	if (COMPLETED_STATUSES.has(status)) return CircleCheck;
	if (["attention_required", "blocked", "failed", "stale"].includes(status)) return CircleAlert;
	return Sparkles;
};

function ReviewInboxStatus({ status }) {
	const Icon = statusIcon(status);
	return (
		<span className={`review-inbox-status review-inbox-status-${getWorkspaceStatusTone(status)}`}>
			<Icon aria-hidden="true" size={14} />
			<span className="sr-only">Status: </span>
			{formatWorkspaceStatus(status)}
		</span>
	);
}

function ReviewInboxRow({ item, onOpenProject }) {
	const { label: kindLabel, Icon: KindIcon } = kindMeta(item);
	const taskTitle = getWorkItemTitle(item);
	const destination = getWorkItemDestination(item);
	const count = formatCount(item);
	return (
		<li className="review-inbox-row">
			<div className="review-inbox-kind">
				<span className="review-inbox-kind-icon">
					<KindIcon aria-hidden="true" size={18} />
				</span>
				<span>
					<span className="sr-only">Task type: </span>
					{kindLabel}
				</span>
			</div>
			<div className="review-inbox-row-copy">
				<div className="review-inbox-row-heading">
					<div>
						<span className="review-inbox-project">{item.project_name || "Project"}</span>
						<h3>
							<span className="sr-only">
								{item.project_name || "Project"} · {formatWorkspaceLabel(item.stage, "Project")}:{" "}
							</span>
							{taskTitle}
						</h3>
					</div>
					<ReviewInboxStatus status={item.status} />
				</div>
				<p>{item.reason}</p>
				<div className="review-inbox-row-meta">
					<span className="review-inbox-stage">{formatWorkspaceLabel(item.stage, "Project")}</span>
					{count ? <span>{count}</span> : null}
					{item.updated_at ? <time dateTime={item.updated_at}>Updated {formatWorkspaceDate(item.updated_at)}</time> : null}
				</div>
			</div>
			<ProjectOpenLink
				projectId={item.project_id}
				destination={destination}
				onOpenProject={onOpenProject}
				className="workspace-open-link review-inbox-open-link"
				ariaLabel={`Open ${taskTitle} for ${item.project_name || "project"}`}
			>
				Open workbench
			</ProjectOpenLink>
		</li>
	);
}

function ReviewInboxEmpty({ title, message, action = null }) {
	return (
		<section className="review-inbox-empty" aria-labelledby="review-inbox-empty-title">
			<span>
				<ListChecks aria-hidden="true" size={22} />
			</span>
			<div>
				<h2 id="review-inbox-empty-title">{title}</h2>
				<p>{message}</p>
			</div>
			{action}
		</section>
	);
}

function ReviewInboxFilters({
	view,
	onViewChange,
	stage,
	onStageChange,
	status,
	onStatusChange,
	onClearFilters,
	stageOptions,
	statusOptions,
	stageSelectRef,
}) {
	const hasFilters = stage !== "all" || status !== "all";
	return (
		<fieldset className="review-inbox-controls">
			<legend className="sr-only">Review filters</legend>
			<div className="review-inbox-view-switch" role="group" aria-label="Inbox view">
				<button
					type="button"
					className={view === "actionable" ? "active" : ""}
					aria-pressed={view === "actionable"}
					onClick={() => onViewChange("actionable")}
				>
					Actionable
				</button>
				<button
					type="button"
					className={view === "informational" ? "active" : ""}
					aria-pressed={view === "informational"}
					onClick={() => onViewChange("informational")}
				>
					Informational &amp; completed
				</button>
			</div>
			<div className="review-inbox-selects">
				<label>
					<span>Stage</span>
					<select ref={stageSelectRef} value={stage} onChange={(event) => onStageChange(event.target.value)}>
						<option value="all">All stages</option>
						{stageOptions.map((option) => (
							<option value={option} key={option}>
								{formatWorkspaceLabel(option)}
							</option>
						))}
					</select>
				</label>
				<label>
					<span>Status</span>
					<select value={status} onChange={(event) => onStatusChange(event.target.value)}>
						<option value="all">All statuses</option>
						{statusOptions.map((option) => (
							<option value={option} key={option}>
								{formatWorkspaceStatus(option)}
							</option>
						))}
					</select>
				</label>
				{hasFilters ? (
					<button type="button" className="review-inbox-clear-filters" onClick={onClearFilters}>
						Clear filters
					</button>
				) : null}
			</div>
		</fieldset>
	);
}

export default function ReviewInbox({ items, onOpenProject }) {
	const [view, setView] = useState("actionable");
	const [stage, setStage] = useState("all");
	const [status, setStatus] = useState("all");
	const stageSelectRef = useRef(null);
	const resultsHeadingRef = useRef(null);
	const deduplicatedItems = useMemo(() => deduplicateReviewItems(items), [items]);
	const stageOptions = useMemo(
		() =>
			[...new Set(deduplicatedItems.map((item) => item.stage).filter(Boolean))].sort(
				(left, right) =>
					(STAGE_ORDER.indexOf(left) < 0 ? Number.MAX_SAFE_INTEGER : STAGE_ORDER.indexOf(left)) -
						(STAGE_ORDER.indexOf(right) < 0 ? Number.MAX_SAFE_INTEGER : STAGE_ORDER.indexOf(right)) || left.localeCompare(right)
			),
		[deduplicatedItems]
	);
	const statusOptions = useMemo(
		() => [...new Set(deduplicatedItems.map((item) => item.status).filter(Boolean))].sort((left, right) => left.localeCompare(right)),
		[deduplicatedItems]
	);
	const effectiveStage = stage === "all" || stageOptions.includes(stage) ? stage : "all";
	const effectiveStatus = status === "all" || statusOptions.includes(status) ? status : "all";

	useEffect(() => {
		if (effectiveStage !== stage) setStage(effectiveStage);
		if (effectiveStatus !== status) setStatus(effectiveStatus);
	}, [effectiveStage, effectiveStatus, stage, status]);

	const viewItems = deduplicatedItems.filter((item) =>
		view === "actionable" ? isActionableReviewItem(item) : !isActionableReviewItem(item)
	);
	const filteredItems = viewItems.filter(
		(item) => (effectiveStage === "all" || item.stage === effectiveStage) && (effectiveStatus === "all" || item.status === effectiveStatus)
	);
	const hasActiveFilters = effectiveStage !== "all" || effectiveStatus !== "all";
	const clearFilters = () => {
		setStage("all");
		setStatus("all");
		window.requestAnimationFrame(() => stageSelectRef.current?.focus());
	};
	const showInformationalItems = () => {
		setView("informational");
		window.requestAnimationFrame(() => resultsHeadingRef.current?.focus());
	};
	const resultsLabel =
		view === "actionable"
			? `${filteredItems.length} actionable review${filteredItems.length === 1 ? "" : "s"}`
			: `${filteredItems.length} informational or completed item${filteredItems.length === 1 ? "" : "s"}`;

	return (
		<section className="review-inbox" aria-labelledby="review-inbox-list-title">
			<ReviewInboxFilters
				view={view}
				onViewChange={setView}
				stage={effectiveStage}
				onStageChange={setStage}
				status={effectiveStatus}
				onStatusChange={setStatus}
				onClearFilters={clearFilters}
				stageOptions={stageOptions}
				statusOptions={statusOptions}
				stageSelectRef={stageSelectRef}
			/>

			<div className="review-inbox-results-heading">
				<div>
					<span className="workspace-eyebrow">Server-ranked queue</span>
					<h2 id="review-inbox-list-title" ref={resultsHeadingRef} tabIndex={-1}>
						{view === "actionable" ? "Actionable reviews" : "Informational & completed"}
					</h2>
				</div>
				<span role="status" aria-live="polite">
					{resultsLabel}
				</span>
			</div>

			{filteredItems.length ? (
				<ul className="review-inbox-list">
					{filteredItems.map((item) => (
						<ReviewInboxRow key={itemIdentity(item)} item={item} onOpenProject={onOpenProject} />
					))}
				</ul>
			) : deduplicatedItems.length === 0 ? (
				<ReviewInboxEmpty
					title="Review queue is clear"
					message="New review and action items will appear here when project work needs attention."
				/>
			) : hasActiveFilters ? (
				<ReviewInboxEmpty
					title="No items match these filters"
					message="Choose another stage or status, or clear the filters to restore this queue view."
					action={
						<button type="button" className="secondary" onClick={clearFilters}>
							Clear filters
						</button>
					}
				/>
			) : view === "actionable" ? (
				<ReviewInboxEmpty
					title="No actionable reviews"
					message="You are caught up. Informational and completed project states remain available in the secondary view."
					action={
						<button type="button" className="secondary" onClick={showInformationalItems}>
							View informational items
						</button>
					}
				/>
			) : (
				<ReviewInboxEmpty
					title="No informational items"
					message="Blocked, failed, completed, and other non-actionable states will appear here without mixing into the active queue."
				/>
			)}
		</section>
	);
}
