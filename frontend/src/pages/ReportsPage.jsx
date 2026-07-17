import { FileText, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { PROJECT_DESTINATIONS } from "../app/workflowRoutes";
import {
	ActivityIndexEmpty,
	ActivityIndexFilters,
	ActivityIndexResultsHeading,
	ActivityStatus,
	formatActivityStatus,
} from "../components/workspace/ActivityIndex";
import { ProjectOpenLink, WorkspaceErrorState, WorkspaceLoadingState } from "../components/workspace/WorkspacePrimitives";
import {
	formatWorkspaceDate,
	formatWorkspaceLabel,
	matchesWorkspaceQuery,
	normalizeWorkspaceList,
} from "../components/workspace/workspacePresentation";

const optionFromValue = (value, formatter = (item) => item) => ({ value, label: formatter(value) });
const formatReportFormat = (value) => (/^[a-z0-9]{1,8}$/i.test(`${value || ""}`) ? `${value}`.toUpperCase() : value);

function ReportRow({ report, onOpenProject }) {
	const evidenceIdentity = report.report_id || "Unavailable";
	return (
		<li className="activity-index-row activity-index-report-row">
			<span className="activity-index-kind-icon">
				<FileText aria-hidden="true" size={20} />
			</span>
			<div className="activity-index-row-copy">
				<div className="activity-index-row-heading">
					<div>
						<span className="activity-index-project">{report.project_name || "Project"}</span>
						<h3>{formatWorkspaceLabel(report.report_type, "Report")}</h3>
					</div>
					<ActivityStatus kind="report" status={report.status} />
				</div>
				<div className="activity-index-evidence">
					<span>Evidence ID</span>
					<code>{evidenceIdentity}</code>
				</div>
				<div className="activity-index-row-meta">
					<span>
						Format <strong>{report.format ? formatReportFormat(report.format) : "Not specified"}</strong>
					</span>
					<span>
						Operation <strong>{report.operation || "Not specified"}</strong>
					</span>
					{Number.isInteger(report.count) ? (
						<span>
							Evidence items <strong>{report.count}</strong>
						</span>
					) : null}
					{report.updated_at ? <time dateTime={report.updated_at}>Updated {formatWorkspaceDate(report.updated_at)}</time> : null}
				</div>
			</div>
			<ProjectOpenLink
				projectId={report.project_id}
				destination={PROJECT_DESTINATIONS.REPORTS}
				onOpenProject={onOpenProject}
				className="workspace-open-link activity-index-open-link"
				ariaLabel={`Open report evidence for ${report.project_name || "project"}`}
			>
				Open evidence
			</ProjectOpenLink>
		</li>
	);
}

export default function ReportsPage({ summary, isLoading = false, isRefreshing = false, error = "", onRetry, onRefresh, onOpenProject }) {
	const [query, setQuery] = useState("");
	const [status, setStatus] = useState("all");
	const [type, setType] = useState("all");
	const [format, setFormat] = useState("all");
	const headingRef = useRef(null);
	const refreshButtonRef = useRef(null);
	const searchInputRef = useRef(null);
	const reports = normalizeWorkspaceList(summary?.recent_reports);
	const statusOptions = useMemo(
		() =>
			[...new Set(reports.map((report) => report.status).filter(Boolean))]
				.sort()
				.map((value) => optionFromValue(value, (item) => formatActivityStatus(item, "report"))),
		[reports]
	);
	const typeOptions = useMemo(
		() =>
			[...new Set(reports.map((report) => report.report_type).filter(Boolean))]
				.sort()
				.map((value) => optionFromValue(value, formatWorkspaceLabel)),
		[reports]
	);
	const formatOptions = useMemo(
		() =>
			[...new Set(reports.map((report) => report.format).filter(Boolean))]
				.sort()
				.map((value) => optionFromValue(value, formatReportFormat)),
		[reports]
	);
	const effectiveStatus = status === "all" || statusOptions.some((option) => option.value === status) ? status : "all";
	const effectiveType = type === "all" || typeOptions.some((option) => option.value === type) ? type : "all";
	const effectiveFormat = format === "all" || formatOptions.some((option) => option.value === format) ? format : "all";

	useEffect(() => {
		if (effectiveStatus !== status) setStatus(effectiveStatus);
		if (effectiveType !== type) setType(effectiveType);
		if (effectiveFormat !== format) setFormat(effectiveFormat);
	}, [effectiveFormat, effectiveStatus, effectiveType, format, status, type]);

	const filteredReports = reports.filter(
		(report) =>
			(effectiveStatus === "all" || report.status === effectiveStatus) &&
			(effectiveType === "all" || report.report_type === effectiveType) &&
			(effectiveFormat === "all" || report.format === effectiveFormat) &&
			matchesWorkspaceQuery(
				query,
				report.project_name,
				report.status,
				report.report_type,
				report.format,
				report.operation,
				report.report_id
			)
	);
	const hasActiveFilters = Boolean(query.trim()) || effectiveStatus !== "all" || effectiveType !== "all" || effectiveFormat !== "all";
	const clearFilters = () => {
		setQuery("");
		setStatus("all");
		setType("all");
		setFormat("all");
	};
	const retryAndRestoreFocus = async () => {
		await onRetry?.();
		window.requestAnimationFrame(() => headingRef.current?.focus());
	};
	const refreshAndRestoreFocus = async () => {
		await onRefresh?.();
		window.requestAnimationFrame(() => refreshButtonRef.current?.focus());
	};
	const errorMessage = error && summary ? `${error} Refresh failed; showing the last available reports.` : error;

	return (
		<main
			id="main-content"
			className="workspace-page activity-index-page"
			aria-labelledby="reports-page-title"
			aria-busy={isLoading || isRefreshing || undefined}
			tabIndex={-1}
		>
			<header className="workspace-page-header activity-index-page-header">
				<div>
					<span className="workspace-eyebrow">Report evidence</span>
					<h1 id="reports-page-title" ref={headingRef} tabIndex={-1}>
						Reports
					</h1>
					<p>Find current report evidence across projects, with approval state and the identity needed to verify it.</p>
				</div>
				<div className="activity-index-header-actions">
					{summary?.generated_at ? <time dateTime={summary.generated_at}>Updated {formatWorkspaceDate(summary.generated_at)}</time> : null}
					{onRefresh ? (
						<button
							ref={refreshButtonRef}
							type="button"
							className="secondary activity-index-refresh-button"
							disabled={isLoading || isRefreshing}
							onClick={refreshAndRestoreFocus}
						>
							<RefreshCw aria-hidden="true" size={15} />
							{isRefreshing ? "Refreshing…" : "Refresh reports"}
						</button>
					) : null}
				</div>
			</header>

			{error ? <WorkspaceErrorState message={errorMessage} onRetry={retryAndRestoreFocus} /> : null}
			{isLoading && !summary ? (
				<WorkspaceLoadingState />
			) : summary ? (
				<section className="activity-index" aria-labelledby="reports-results-title">
					<ActivityIndexFilters
						name="Reports"
						query={query}
						onQueryChange={setQuery}
						searchLabel="Search reports"
						searchPlaceholder="Search project, report, format, or evidence ID"
						searchInputRef={searchInputRef}
						filters={[
							{
								id: "status",
								label: "Status",
								value: effectiveStatus,
								onChange: setStatus,
								allLabel: "All statuses",
								options: statusOptions,
							},
							{
								id: "type",
								label: "Type",
								value: effectiveType,
								onChange: setType,
								allLabel: "All types",
								options: typeOptions,
							},
							{
								id: "format",
								label: "Format",
								value: effectiveFormat,
								onChange: setFormat,
								allLabel: "All formats",
								options: formatOptions,
							},
						]}
						hasActiveFilters={hasActiveFilters}
						onClearFilters={clearFilters}
					/>
					<ActivityIndexResultsHeading
						id="reports-results-title"
						eyebrow="Current evidence"
						title="Recent reports"
						countLabel={`${filteredReports.length} report${filteredReports.length === 1 ? "" : "s"}`}
					/>
					{filteredReports.length ? (
						<ul className="activity-index-list" aria-label="Recent reports">
							{filteredReports.map((report) => (
								<ReportRow key={report.report_id} report={report} onOpenProject={onOpenProject} />
							))}
						</ul>
					) : reports.length === 0 ? (
						<ActivityIndexEmpty
							title="No recent reports"
							message="Current report evidence will appear here after a project generates its first report."
						/>
					) : (
						<ActivityIndexEmpty
							title="No reports match these filters"
							message="Try another project, status, format, or evidence ID, or clear the filters."
						/>
					)}
				</section>
			) : null}
		</main>
	);
}
