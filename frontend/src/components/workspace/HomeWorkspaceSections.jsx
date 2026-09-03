import { useState } from "react";
import { Activity, ClipboardCheck, FileText, FolderKanban, PlayCircle } from "lucide-react";

import { PROJECT_DESTINATIONS } from "../../app/workflowRoutes";
import {
	formatWorkspaceDate,
	formatWorkspaceLabel,
	getProjectDestination,
	getWorkItemDestination,
	getWorkItemTitle,
	groupWorkspaceItems,
	normalizeWorkspaceList,
} from "./workspacePresentation";
import { ProjectOpenLink, ProjectProgress, WorkspaceStatus } from "./WorkspacePrimitives";

export function ContinueWorkingSection({
	item,
	project,
	onOpenProject,
	emptyTitle = "You’re caught up",
	emptyMessage = "No project needs a next action right now. Open a project to review its latest status.",
}) {
	return (
		<section className="workspace-section workspace-continue-section" aria-labelledby="continue-working-title">
			<div className="workspace-section-heading">
				<div>
					<span className="workspace-eyebrow">Recommended</span>
					<h2 id="continue-working-title">Continue working</h2>
				</div>
			</div>
			{item ? (
				<div className="workspace-continue-card">
					<div className="workspace-continue-copy">
						<div className="workspace-card-meta">
							<span>{item.project_name || project?.name || "Project"}</span>
							<WorkspaceStatus status={item.status} />
						</div>
						<h3>{getWorkItemTitle(item)}</h3>
						<p>{item.reason}</p>
						{Number.isInteger(item.count) ? <span className="workspace-count">{item.count} items</span> : null}
						<ProjectProgress completed={project?.completed_stage_count} total={project?.total_stage_count} />
					</div>
					<ProjectOpenLink
						projectId={item.project_id}
						destination={getWorkItemDestination(item)}
						onOpenProject={onOpenProject}
						className="workspace-open-link workspace-primary-link"
					>
						Continue
					</ProjectOpenLink>
				</div>
			) : (
				<div className="workspace-quiet-state">
					<ClipboardCheck aria-hidden="true" size={22} />
					<div>
						<h3>{emptyTitle}</h3>
						<p>{emptyMessage}</p>
					</div>
				</div>
			)}
		</section>
	);
}

const MY_WORK_GROUP_PREVIEW_LIMIT = 3;

export function MyWorkSection({
	items,
	onOpenProject,
	emptyTitle = "No open work",
	emptyMessage = "Reviews, blockers, failed runs, and ready next steps will appear here.",
}) {
	const groups = groupWorkspaceItems(items);
	const [expandedGroupIds, setExpandedGroupIds] = useState(() => new Set());
	const toggleGroup = (groupId) =>
		setExpandedGroupIds((prev) => {
			const next = new Set(prev);
			if (next.has(groupId)) {
				next.delete(groupId);
			} else {
				next.add(groupId);
			}
			return next;
		});
	return (
		<section className="workspace-section workspace-my-work" aria-labelledby="my-work-title">
			<div className="workspace-section-heading">
				<div>
					<span className="workspace-eyebrow">Prioritized</span>
					<h2 id="my-work-title">My work</h2>
				</div>
			</div>
			{groups.length ? (
				<div className="workspace-work-groups">
					{groups.map((group) => {
						const isExpanded = expandedGroupIds.has(group.id);
						const hiddenCount = group.items.length - MY_WORK_GROUP_PREVIEW_LIMIT;
						const visibleItems = isExpanded || hiddenCount <= 0 ? group.items : group.items.slice(0, MY_WORK_GROUP_PREVIEW_LIMIT);
						return (
						<section className="workspace-work-group" key={group.id} aria-labelledby={`work-group-${group.id}`}>
							<h3 id={`work-group-${group.id}`}>
								{group.label}
								<span className="workspace-work-group-count" aria-hidden="true">
									{group.items.length}
								</span>
							</h3>
							<ul>
								{visibleItems.map((item) => (
									<li key={item.work_item_id}>
										<div className="workspace-work-item-copy">
											<div className="workspace-card-meta">
												<span>{item.project_name || "Project"}</span>
												<WorkspaceStatus status={item.status} />
											</div>
											<strong>{getWorkItemTitle(item)}</strong>
											<p>{item.reason}</p>
											{Number.isInteger(item.count) ? <span className="workspace-count">{item.count} items</span> : null}
										</div>
										<ProjectOpenLink projectId={item.project_id} destination={getWorkItemDestination(item)} onOpenProject={onOpenProject}>
											Open
										</ProjectOpenLink>
									</li>
								))}
							</ul>
							{hiddenCount > 0 ? (
								<button
									type="button"
									className="workspace-show-more"
									aria-expanded={isExpanded}
									onClick={() => toggleGroup(group.id)}
								>
									{isExpanded ? "Show fewer" : `Show ${hiddenCount} more`}
								</button>
							) : null}
						</section>
						);
					})}
				</div>
			) : (
				<div className="workspace-quiet-state">
					<ClipboardCheck aria-hidden="true" size={22} />
					<div>
						<h3>{emptyTitle}</h3>
						<p>{emptyMessage}</p>
					</div>
				</div>
			)}
		</section>
	);
}

export function WorkspaceProjectsSection({
	projects,
	onOpenProject,
	title = "Projects",
	eyebrow = "Recently active",
	emptyMessage = "No projects match this search.",
	labelledBy = "workspace-projects-title",
}) {
	return (
		<section className="workspace-section workspace-projects-section" aria-labelledby={labelledBy}>
			<div className="workspace-section-heading">
				<div>
					<span className="workspace-eyebrow">{eyebrow}</span>
					<h2 id={labelledBy}>{title}</h2>
				</div>
			</div>
			{projects.length ? (
				<ul className="workspace-project-grid">
					{projects.map((project) => (
						<li className="workspace-project-card" key={project.project_id}>
							<div className="workspace-project-card-heading">
								<FolderKanban aria-hidden="true" size={19} />
								<div>
									<h3>{project.name || "Untitled project"}</h3>
									<span>{formatWorkspaceLabel(project.current_stage, "Getting started")}</span>
								</div>
								<WorkspaceStatus status={project.current_status || project.project_status} />
							</div>
							{project.reason ? <p>{project.reason}</p> : null}
							<ProjectProgress completed={project.completed_stage_count} total={project.total_stage_count} />
							<div className="workspace-project-card-footer">
								<time dateTime={project.updated_at}>{formatWorkspaceDate(project.updated_at)}</time>
								<ProjectOpenLink projectId={project.project_id} destination={getProjectDestination(project)} onOpenProject={onOpenProject}>
									Open
								</ProjectOpenLink>
							</div>
						</li>
					))}
				</ul>
			) : (
				<p className="workspace-filter-empty" role="status">
					{emptyMessage}
				</p>
			)}
		</section>
	);
}

function RunActivity({ run, onOpenProject }) {
	return (
		<li>
			<PlayCircle aria-hidden="true" size={18} />
			<div>
				<div className="workspace-card-meta">
					<span>{run.project_name || "Project"}</span>
					<WorkspaceStatus status={run.status} />
				</div>
				<strong>Automation run</strong>
				<p>
					{run.executed_count} executed · {run.passed_count} passed
				</p>
				<time dateTime={run.updated_at}>{formatWorkspaceDate(run.updated_at)}</time>
			</div>
			<ProjectOpenLink projectId={run.project_id} destination={PROJECT_DESTINATIONS.AUTOMATION} onOpenProject={onOpenProject}>
				View
			</ProjectOpenLink>
		</li>
	);
}

function ReportActivity({ report, onOpenProject }) {
	return (
		<li>
			<FileText aria-hidden="true" size={18} />
			<div>
				<div className="workspace-card-meta">
					<span>{report.project_name || "Project"}</span>
					<WorkspaceStatus status={report.status} />
				</div>
				<strong>{formatWorkspaceLabel(report.report_type, "Test report")}</strong>
				<p>{report.count == null ? "Report activity" : `${report.count} items included`}</p>
				<time dateTime={report.updated_at}>{formatWorkspaceDate(report.updated_at)}</time>
			</div>
			<ProjectOpenLink projectId={report.project_id} destination={PROJECT_DESTINATIONS.REPORTS} onOpenProject={onOpenProject}>
				View
			</ProjectOpenLink>
		</li>
	);
}

export function RecentActivitySection({
	runs,
	reports,
	onOpenProject,
	limit = 4,
	emptyTitle = "No recent activity",
	emptyMessage = "Completed runs and generated reports will appear here with their project context.",
}) {
	const visibleRuns = normalizeWorkspaceList(runs).slice(0, limit);
	const visibleReports = normalizeWorkspaceList(reports).slice(0, limit);
	const hasActivity = visibleRuns.length > 0 || visibleReports.length > 0;

	return (
		<section className="workspace-section workspace-activity-section" aria-labelledby="recent-activity-title">
			<div className="workspace-section-heading">
				<div>
					<span className="workspace-eyebrow">Latest evidence</span>
					<h2 id="recent-activity-title">Recent activity</h2>
				</div>
			</div>
			{hasActivity ? (
				<div className="workspace-activity-columns">
					{visibleRuns.length ? (
						<section aria-labelledby="recent-runs-title">
							<h3 id="recent-runs-title">Runs</h3>
							<ul>
								{visibleRuns.map((run) => (
									<RunActivity key={run.run_record_id} run={run} onOpenProject={onOpenProject} />
								))}
							</ul>
						</section>
					) : null}
					{visibleReports.length ? (
						<section aria-labelledby="recent-reports-title">
							<h3 id="recent-reports-title">Reports</h3>
							<ul>
								{visibleReports.map((report) => (
									<ReportActivity key={report.report_id} report={report} onOpenProject={onOpenProject} />
								))}
							</ul>
						</section>
					) : null}
				</div>
			) : (
				<div className="workspace-quiet-state">
					<Activity aria-hidden="true" size={22} />
					<div>
						<h3>{emptyTitle}</h3>
						<p>{emptyMessage}</p>
					</div>
				</div>
			)}
		</section>
	);
}
