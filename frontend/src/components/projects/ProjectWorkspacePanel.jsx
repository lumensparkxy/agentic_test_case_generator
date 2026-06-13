import OrchestratorCockpitPanel from "./OrchestratorCockpitPanel";

const STAGES = [
	{ key: "requirements", label: "Requirements" },
	{ key: "context", label: "Context" },
	{ key: "use_cases", label: "Use Cases" },
	{ key: "impact_analysis", label: "Impact Analysis" },
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

function ReportEvidence({ reportState, reportSnapshot }) {
	if (!reportSnapshot) {
		return null;
	}
	const payload = reportSnapshot.payload || {};
	const evidence = payload.evidence || {};
	const sourceSnapshotIds = evidence.source_snapshot_ids || {};
	const executionRunIds = evidence.execution_run_ids || [];
	const sourceEntries = Object.entries(sourceSnapshotIds).filter(([, value]) => value);
	const status = reportState?.stale ? "Stale" : reportState?.approved ? "Approved" : "Draft";

	return (
		<div className="project-history-block">
			<h3>Latest Report</h3>
			<div className="project-run-list">
				<div className="project-run-row">
					<span>{payload.format || reportState?.operation || "report"}</span>
					<strong>{status}</strong>
					<span>{reportSnapshot.snapshot_id}</span>
				</div>
				{sourceEntries.slice(0, 3).map(([stage, snapshotId]) => (
					<div className="project-run-row" key={`${stage}-${snapshotId}`}>
						<span>{stage.replaceAll("_", " ")}</span>
						<strong>Evidence</strong>
						<span>{snapshotId}</span>
					</div>
				))}
				{executionRunIds.slice(0, 2).map((runId) => (
					<div className="project-run-row" key={runId}>
						<span>execution run</span>
						<strong>Evidence</strong>
						<span>{runId}</span>
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
	orchestratorStatus,
	orchestratorRuns,
	isLoadingOrchestrator,
	orchestratorError,
	authActionDisabled,
	onCreateProject,
	onOpenProject,
	onRefreshProjects,
	onRefreshOrchestrator,
	onOrchestratorAction,
	orchestratorActionBusy,
}) {
	const selectedProjectId = currentProject?.project_id || "";
	const stageState = currentProject?.stage_state || {};
	const reportSnapshot = currentProject?.current_snapshots?.reports || null;
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
					<OrchestratorCockpitPanel
						currentProject={currentProject}
						status={orchestratorStatus}
						runsPayload={orchestratorRuns}
						isLoading={isLoadingOrchestrator}
						error={orchestratorError}
						authActionDisabled={authActionDisabled}
						actionBusy={orchestratorActionBusy}
						onRefresh={onRefreshOrchestrator}
						onAction={onOrchestratorAction}
					/>
					<div className="project-history-grid">
						<ReportEvidence reportState={stageState.reports} reportSnapshot={reportSnapshot} />
						<ExecutionHistory runs={currentProject.execution_runs || []} />
						<TimelinePreview events={currentProject.timeline || []} />
					</div>
				</>
			)}
		</section>
	);
}
