import { Badge } from "../ui/surfaces";
import { CollectionToolbar, CollectionState } from "../ui/collections";
import { Select, Button } from "../ui/controls";
import { CheckCircle2, Circle, CircleAlert, Clock3, FileText, SearchX, XCircle } from "lucide-react";

import { formatWorkspaceStatus, getWorkspaceStatusTone } from "./workspacePresentation";
import { WorkspaceSearch } from "./WorkspacePrimitives";

const normalizeStatus = (status) => `${status ?? ""}`.trim().toLocaleLowerCase().replaceAll("-", "_").replaceAll(" ", "_");

const getRunStatusIcon = (status) => {
	const normalized = normalizeStatus(status);
	if (["completed", "passed", "success", "succeeded"].includes(normalized)) return CheckCircle2;
	if (["failed", "error", "invalid"].includes(normalized)) return XCircle;
	if (["queued", "pending", "running", "in_progress"].includes(normalized)) return Clock3;
	if (["cancelled", "canceled", "disabled", "blocked"].includes(normalized)) return CircleAlert;
	return Circle;
};

const getReportStatusIcon = (status) => {
	if (status === "approved") return CheckCircle2;
	if (status === "stale") return CircleAlert;
	return FileText;
};

export const formatActivityStatus = (status, kind) => {
	const normalized = normalizeStatus(status);
	if (kind === "report" && normalized === "stale") return "Stale";
	if (kind === "run" && normalized === "completed") return "Completed";
	return formatWorkspaceStatus(status);
};

export function ActivityStatus({ status, kind }) {
	const Icon = kind === "report" ? getReportStatusIcon(status) : getRunStatusIcon(status);
	const label = formatActivityStatus(status, kind);
	return (
		<Badge
			tone={getWorkspaceStatusTone(status)}
			icon={Icon}
			className={`activity-index-status activity-index-status-${getWorkspaceStatusTone(status)}`}
			data-status-kind={kind}
			data-status-value={status || "unknown"}
		>
			<span className="sr-only">Status: </span>
			{label}
		</Badge>
	);
}

export function ActivityIndexFilters({
	name,
	query,
	onQueryChange,
	searchLabel,
	searchPlaceholder,
	searchInputRef,
	filters,
	hasActiveFilters,
	onClearFilters,
}) {
	const clearFilters = () => {
		onClearFilters();
		window.requestAnimationFrame(() => searchInputRef.current?.focus());
	};

	return (
		<CollectionToolbar as="fieldset" className="activity-index-controls" aria-label={`Filter ${name.toLocaleLowerCase()}`}>
			<legend className="sr-only">Filter {name.toLocaleLowerCase()}</legend>
			<WorkspaceSearch
				value={query}
				onChange={onQueryChange}
				label={searchLabel}
				placeholder={searchPlaceholder}
				inputRef={searchInputRef}
			/>
			<div className="activity-index-selects">
				{filters.map((filter) => (
					<label key={filter.id}>
						<span>{filter.label}</span>
						<Select ref={filter.selectRef} value={filter.value} onChange={(event) => filter.onChange(event.target.value)}>
							<option value="all">{filter.allLabel}</option>
							{filter.options.map((option) => (
								<option value={option.value} key={option.value}>
									{option.label}
								</option>
							))}
						</Select>
					</label>
				))}
				{hasActiveFilters ? (
					<Button type="button" className="activity-index-clear-filters" onClick={clearFilters}>
						Clear filters
					</Button>
				) : null}
			</div>
		</CollectionToolbar>
	);
}

export function ActivityIndexResultsHeading({ id, eyebrow, title, countLabel, headingRef }) {
	return (
		<div className="activity-index-results-heading">
			<div>
				<span className="workspace-eyebrow">{eyebrow}</span>
				<h2 id={id} ref={headingRef} tabIndex={-1}>
					{title}
				</h2>
			</div>
			<span role="status" aria-live="polite" aria-atomic="true">
				{countLabel}
			</span>
		</div>
	);
}

export function ActivityIndexEmpty({ title, message }) {
	return (
		<CollectionState as="section" kind="empty" className="activity-index-empty" aria-labelledby="activity-index-empty-title">
			<CollectionState as="span" kind="empty" className="activity-index-empty-icon">
				<SearchX aria-hidden="true" size={22} />
			</CollectionState>
			<div>
				<h2 id="activity-index-empty-title">{title}</h2>
				<p>{message}</p>
			</div>
		</CollectionState>
	);
}
