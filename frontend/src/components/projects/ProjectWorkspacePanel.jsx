const STAGES = [
	{ key: "requirements", label: "Requirements" },
	{ key: "context", label: "Context" },
	{ key: "use_cases", label: "Use Cases" },
	{ key: "test_cases", label: "Test Cases" },
	{ key: "execution", label: "Execution" },
	{ key: "reports", label: "Reports" },
];

const formatDateTime = (value) => {
	if (!value) return "";
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return "";
	return date.toLocaleString(undefined, {
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	});
};

function StagePill({ stage, state }) {
	const version = state?.version || 0;
	const className = state?.stale ? "project-stage-pill stale" : version ? "project-stage-pill ready" : "project-stage-pill";
	return (
		<div className={className}>
			<span className="project-stage-label">{stage.label}</span>
			<span className="project-stage-meta">
				{version ? `v${version}` : "Not started"}
				{state?.stale ? " · stale" : state?.approved ? " · approved" : ""}
			</span>
		</div>
	);
}

function ExecutionHistory({ runs }) {
	if (!runs?.length) {
		return null;
	}
	return (
		<div className="project-history-block">
			<h3>Execution Runs</h3>
			<div className="project-run-list">
				{runs.slice(0, 4).map((run) => (
					<div className="project-run-row" key={run.run_record_id}>
						<span>{run.target_environment}</span>
						<strong>{run.status}</strong>
						<span>
							{run.summary?.passed || 0} passed / {run.summary?.failed || 0} failed
						</span>
					</div>
				))}
			</div>
		</div>
	);
}

function TimelinePreview({ events }) {
	if (!events?.length) {
		return null;
	}
	return (
		<div className="project-history-block">
			<h3>Timeline</h3>
			<div className="project-timeline-list">
				{events.slice(0, 4).map((event) => (
					<div className="project-timeline-row" key={event.event_id}>
						<span>{formatDateTime(event.occurred_at)}</span>
						<strong>{event.summary}</strong>
					</div>
				))}
			</div>
		</div>
	);
}

export default function ProjectWorkspacePanel({
	projects,
	currentProject,
	newProjectName,
	setNewProjectName,
	isLoadingProjects,
	isCreatingProject,
	isOpeningProject,
	authActionDisabled,
	onCreateProject,
	onOpenProject,
	onRefreshProjects,
}) {
	const selectedProjectId = currentProject?.project_id || "";
	const stageState = currentProject?.stage_state || {};
	return (
		<section className="project-workspace">
			<div className="project-workspace-header">
				<div>
					<h2>QA Project</h2>
					<p>{currentProject ? `${currentProject.name} · revision ${currentProject.current_revision}` : "No project selected"}</p>
				</div>
				<div className="project-workspace-actions">
					<select
						value={selectedProjectId}
						onChange={(event) => onOpenProject(event.target.value)}
						disabled={authActionDisabled || isLoadingProjects || isOpeningProject}
						aria-label="Open QA project"
					>
						<option value="">Select project</option>
						{projects.map((project) => (
							<option key={project.project_id} value={project.project_id}>
								{project.name}
							</option>
						))}
					</select>
					<button type="button" className="secondary" onClick={onRefreshProjects} disabled={authActionDisabled || isLoadingProjects}>
						{isLoadingProjects ? "Loading" : "Refresh"}
					</button>
				</div>
			</div>

			<div className="project-create-row">
				<input
					type="text"
					value={newProjectName}
					onChange={(event) => setNewProjectName(event.target.value)}
					placeholder="New QA project name"
					disabled={authActionDisabled || isCreatingProject}
				/>
				<button type="button" onClick={onCreateProject} disabled={authActionDisabled || isCreatingProject || !newProjectName.trim()}>
					{isCreatingProject ? "Creating" : "New Project"}
				</button>
			</div>

			{currentProject && (
				<>
					<div className="project-stage-grid">
						{STAGES.map((stage) => (
							<StagePill key={stage.key} stage={stage} state={stageState[stage.key]} />
						))}
					</div>
					<div className="project-history-grid">
						<ExecutionHistory runs={currentProject.execution_runs || []} />
						<TimelinePreview events={currentProject.timeline || []} />
					</div>
				</>
			)}
		</section>
	);
}
