import { PlayCircle, RefreshCw } from "lucide-react";
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
import { formatWorkspaceDate, matchesWorkspaceQuery, normalizeWorkspaceList } from "../components/workspace/workspacePresentation";

const optionFromValue = (value, formatter = (item) => item) => ({ value, label: formatter(value) });
const exactCount = (value) => (Number.isInteger(value) && value >= 0 ? value : "—");

function RunRow({ run, onOpenProject }) {
	const runIdentity = run.run_id || run.run_record_id || "Unavailable";
	return (
		<li className="activity-index-row activity-index-run-row">
			<span className="activity-index-kind-icon">
				<PlayCircle aria-hidden="true" size={20} />
			</span>
			<div className="activity-index-row-copy">
				<div className="activity-index-row-heading">
					<div>
						<span className="activity-index-project">{run.project_name || "Project"}</span>
						<h3>{runIdentity}</h3>
					</div>
					<ActivityStatus kind="run" status={run.status} />
				</div>
				<div className="activity-index-row-meta">
					<span>
						Environment <strong>{run.target_environment || "Not specified"}</strong>
					</span>
					{run.updated_at ? <time dateTime={run.updated_at}>Updated {formatWorkspaceDate(run.updated_at)}</time> : null}
				</div>
				<dl className="activity-index-metrics" aria-label={`Run totals for ${runIdentity}`}>
					<div>
						<dt>Selected</dt>
						<dd>{exactCount(run.selected_count)}</dd>
					</div>
					<div>
						<dt>Executed</dt>
						<dd>{exactCount(run.executed_count)}</dd>
					</div>
					<div>
						<dt>Passed</dt>
						<dd>{exactCount(run.passed_count)}</dd>
					</div>
					<div>
						<dt>Failed</dt>
						<dd>{exactCount(run.failed_count)}</dd>
					</div>
					<div>
						<dt>Invalid</dt>
						<dd>{exactCount(run.invalid_count)}</dd>
					</div>
				</dl>
			</div>
			<ProjectOpenLink
				projectId={run.project_id}
				destination={PROJECT_DESTINATIONS.AUTOMATION}
				onOpenProject={onOpenProject}
				className="workspace-open-link activity-index-open-link"
				ariaLabel={`Open automation evidence for ${run.project_name || "project"}`}
			>
				Open evidence
			</ProjectOpenLink>
		</li>
	);
}

export default function RunsPage({ summary, isLoading = false, isRefreshing = false, error = "", onRetry, onRefresh, onOpenProject }) {
	const [query, setQuery] = useState("");
	const [status, setStatus] = useState("all");
	const [environment, setEnvironment] = useState("all");
	const headingRef = useRef(null);
	const refreshButtonRef = useRef(null);
	const searchInputRef = useRef(null);
	const runs = normalizeWorkspaceList(summary?.recent_runs);
	const statusOptions = useMemo(
		() =>
			[...new Set(runs.map((run) => run.status).filter(Boolean))]
				.sort()
				.map((value) => optionFromValue(value, (item) => formatActivityStatus(item, "run"))),
		[runs]
	);
	const environmentOptions = useMemo(
		() => [...new Set(runs.map((run) => run.target_environment).filter(Boolean))].sort().map((value) => optionFromValue(value)),
		[runs]
	);
	const effectiveStatus = status === "all" || statusOptions.some((option) => option.value === status) ? status : "all";
	const effectiveEnvironment =
		environment === "all" || environmentOptions.some((option) => option.value === environment) ? environment : "all";

	useEffect(() => {
		headingRef.current?.focus();
	}, []);

	useEffect(() => {
		if (effectiveStatus !== status) setStatus(effectiveStatus);
		if (effectiveEnvironment !== environment) setEnvironment(effectiveEnvironment);
	}, [effectiveEnvironment, effectiveStatus, environment, status]);

	const filteredRuns = runs.filter(
		(run) =>
			(effectiveStatus === "all" || run.status === effectiveStatus) &&
			(effectiveEnvironment === "all" || run.target_environment === effectiveEnvironment) &&
			matchesWorkspaceQuery(query, run.project_name, run.status, run.target_environment, run.run_id, run.run_record_id)
	);
	const hasActiveFilters = Boolean(query.trim()) || effectiveStatus !== "all" || effectiveEnvironment !== "all";
	const clearFilters = () => {
		setQuery("");
		setStatus("all");
		setEnvironment("all");
	};
	const retryAndRestoreFocus = async () => {
		await onRetry?.();
		window.requestAnimationFrame(() => headingRef.current?.focus());
	};
	const refreshAndRestoreFocus = async () => {
		await onRefresh?.();
		window.requestAnimationFrame(() => refreshButtonRef.current?.focus());
	};
	const errorMessage = error && summary ? `${error} Refresh failed; showing the last available runs.` : error;

	return (
		<main
			className="workspace-page activity-index-page"
			aria-labelledby="runs-page-title"
			aria-busy={isLoading || isRefreshing || undefined}
		>
			<header className="workspace-page-header activity-index-page-header">
				<div>
					<span className="workspace-eyebrow">Execution evidence</span>
					<h1 id="runs-page-title" ref={headingRef} tabIndex={-1}>
						Runs
					</h1>
					<p>Find recent automation outcomes across projects, then open the project evidence that owns each run.</p>
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
							{isRefreshing ? "Refreshing…" : "Refresh runs"}
						</button>
					) : null}
				</div>
			</header>

			{error ? <WorkspaceErrorState message={errorMessage} onRetry={retryAndRestoreFocus} /> : null}
			{isLoading && !summary ? (
				<WorkspaceLoadingState />
			) : summary ? (
				<section className="activity-index" aria-labelledby="runs-results-title">
					<ActivityIndexFilters
						name="Runs"
						query={query}
						onQueryChange={setQuery}
						searchLabel="Search runs"
						searchPlaceholder="Search project, run, status, or environment"
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
								id: "environment",
								label: "Environment",
								value: effectiveEnvironment,
								onChange: setEnvironment,
								allLabel: "All environments",
								options: environmentOptions,
							},
						]}
						hasActiveFilters={hasActiveFilters}
						onClearFilters={clearFilters}
					/>
					<ActivityIndexResultsHeading
						id="runs-results-title"
						eyebrow="Newest first"
						title="Recent runs"
						countLabel={`${filteredRuns.length} run${filteredRuns.length === 1 ? "" : "s"}`}
					/>
					{filteredRuns.length ? (
						<ul className="activity-index-list" aria-label="Recent runs">
							{filteredRuns.map((run) => (
								<RunRow key={run.run_record_id || run.run_id} run={run} onOpenProject={onOpenProject} />
							))}
						</ul>
					) : runs.length === 0 ? (
						<ActivityIndexEmpty
							title="No recent runs"
							message="Automation outcomes will appear here after a project records execution evidence."
						/>
					) : (
						<ActivityIndexEmpty
							title="No runs match these filters"
							message="Try another project, status, or environment, or clear the filters."
						/>
					)}
				</section>
			) : null}
		</main>
	);
}
