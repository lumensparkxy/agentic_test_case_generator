import { FolderPlus, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";

import {
	ContinueWorkingSection,
	MyWorkSection,
	RecentActivitySection,
	WorkspaceProjectsSection,
} from "../components/workspace/HomeWorkspaceSections";
import { WorkspaceErrorState, WorkspaceLoadingState, WorkspaceSearch } from "../components/workspace/WorkspacePrimitives";
import { matchesWorkspaceQuery, normalizeWorkspaceList } from "../components/workspace/workspacePresentation";

const itemMatches = (query, item) =>
	matchesWorkspaceQuery(query, item?.project_name, item?.reason, item?.stage, item?.status, item?.action, item?.kind);

const projectMatches = (query, project) =>
	matchesWorkspaceQuery(query, project?.name, project?.current_stage, project?.current_status, project?.reason);

const runMatches = (query, run) => matchesWorkspaceQuery(query, run?.project_name, run?.status, run?.target_environment, "automation run");

const reportMatches = (query, report) =>
	matchesWorkspaceQuery(query, report?.project_name, report?.status, report?.report_type, report?.format, "report");

export default function HomePage({
	summary,
	isLoading = false,
	isRefreshing = false,
	error = "",
	onRetry,
	onOpenProject,
	onCreateProject,
}) {
	const [query, setQuery] = useState("");
	const projects = normalizeWorkspaceList(summary?.projects);
	const normalizedQuery = query.trim();
	const filtered = useMemo(
		() => ({
			continueItem: itemMatches(normalizedQuery, summary?.continue_working) ? summary?.continue_working || null : null,
			projects: projects.filter((project) => projectMatches(normalizedQuery, project)).slice(0, 6),
			workItems: normalizeWorkspaceList(summary?.work_items).filter((item) => itemMatches(normalizedQuery, item)),
			runs: normalizeWorkspaceList(summary?.recent_runs).filter((run) => runMatches(normalizedQuery, run)),
			reports: normalizeWorkspaceList(summary?.recent_reports).filter((report) => reportMatches(normalizedQuery, report)),
		}),
		[normalizedQuery, projects, summary]
	);
	const continueProject = projects.find((project) => project.project_id === filtered.continueItem?.project_id) || null;
	const searchEmptyTitle = normalizedQuery ? "No matching work" : undefined;
	const searchEmptyMessage = normalizedQuery ? `Nothing in this section matches “${normalizedQuery}”.` : undefined;

	return (
		<main
			id="main-content"
			className="workspace-page workspace-home-page"
			aria-labelledby="workspace-home-title"
			aria-busy={isLoading || isRefreshing || undefined}
			tabIndex={-1}
		>
			<header className="workspace-page-header">
				<div>
					<span className="workspace-eyebrow">Your QA workspace</span>
					<h1 id="workspace-home-title">Home</h1>
					<p>Pick up the right task, review active work, or start something new.</p>
				</div>
				<div className="workspace-header-actions">
					<WorkspaceSearch value={query} onChange={setQuery} />
					{onCreateProject ? (
						<button type="button" className="workspace-create-button" onClick={onCreateProject}>
							<FolderPlus aria-hidden="true" size={17} />
							Create project
						</button>
					) : null}
				</div>
			</header>

			{error ? <WorkspaceErrorState message={error} onRetry={onRetry} /> : null}
			{isLoading && !summary ? (
				<WorkspaceLoadingState />
			) : projects.length === 0 && summary ? (
				<section className="workspace-first-run" aria-labelledby="workspace-first-run-title">
					<span className="workspace-first-run-icon">
						<Sparkles aria-hidden="true" size={24} />
					</span>
					<div>
						<h2 id="workspace-first-run-title">Create your first QA project</h2>
						<p>Bring requirements into one place, ground them with product context, and build reviewable test coverage.</p>
					</div>
					{onCreateProject ? (
						<button type="button" onClick={onCreateProject}>
							<FolderPlus aria-hidden="true" size={17} />
							Create project
						</button>
					) : null}
				</section>
			) : summary ? (
				<div className="workspace-home-content">
					<ContinueWorkingSection
						item={filtered.continueItem}
						project={continueProject}
						onOpenProject={onOpenProject}
						emptyTitle={searchEmptyTitle}
						emptyMessage={searchEmptyMessage}
					/>
					<div className="workspace-home-columns">
						<MyWorkSection
							items={filtered.workItems}
							onOpenProject={onOpenProject}
							emptyTitle={searchEmptyTitle}
							emptyMessage={searchEmptyMessage}
						/>
						<WorkspaceProjectsSection
							projects={filtered.projects}
							onOpenProject={onOpenProject}
							emptyMessage={searchEmptyMessage || "No active projects are available."}
						/>
					</div>
					<RecentActivitySection
						runs={filtered.runs}
						reports={filtered.reports}
						onOpenProject={onOpenProject}
						emptyTitle={searchEmptyTitle}
						emptyMessage={searchEmptyMessage}
					/>
				</div>
			) : null}
		</main>
	);
}
