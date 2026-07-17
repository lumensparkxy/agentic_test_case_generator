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
		<span
			className={`activity-index-status activity-index-status-${getWorkspaceStatusTone(status)}`}
			data-status-kind={kind}
			data-status-value={status || "unknown"}
		>
			<Icon aria-hidden="true" size={15} />
			<span className="sr-only">Status: </span>
			{label}
		</span>
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
		<fieldset className="activity-index-controls" aria-label={`Filter ${name.toLocaleLowerCase()}`}>
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
						<select ref={filter.selectRef} value={filter.value} onChange={(event) => filter.onChange(event.target.value)}>
							<option value="all">{filter.allLabel}</option>
							{filter.options.map((option) => (
								<option value={option.value} key={option.value}>
									{option.label}
								</option>
							))}
						</select>
					</label>
				))}
				{hasActiveFilters ? (
					<button type="button" className="activity-index-clear-filters" onClick={clearFilters}>
						Clear filters
					</button>
				) : null}
			</div>
		</fieldset>
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
		<section className="activity-index-empty" aria-labelledby="activity-index-empty-title">
			<span className="activity-index-empty-icon">
				<SearchX aria-hidden="true" size={22} />
			</span>
			<div>
				<h2 id="activity-index-empty-title">{title}</h2>
				<p>{message}</p>
			</div>
		</section>
	);
}
