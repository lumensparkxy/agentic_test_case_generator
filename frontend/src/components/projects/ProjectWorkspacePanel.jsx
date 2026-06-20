import OrchestratorCockpitPanel from "./OrchestratorCockpitPanel";

export default function ProjectWorkspacePanel({
	currentProject,
	newProjectName,
	setNewProjectName,
	isLoadingProjects,
	isCreatingProject,
	orchestratorStatus,
	isLoadingOrchestrator,
	orchestratorError,
	authActionDisabled,
	onCreateProject,
	onRefreshProjects,
	onOrchestratorAction,
	orchestratorActionBusy,
}) {
	return (
		<section className="project-workspace">
			<div className="project-workspace-header">
				<div>
					<h2>QA Project</h2>
					<p>{currentProject ? `${currentProject.name} · revision ${currentProject.current_revision}` : "No project selected"}</p>
				</div>
				<div className="project-workspace-actions">
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

			<OrchestratorCockpitPanel
				currentProject={currentProject}
				status={orchestratorStatus}
				isLoading={isLoadingOrchestrator}
				error={orchestratorError}
				authActionDisabled={authActionDisabled}
				actionBusy={orchestratorActionBusy}
				onAction={onOrchestratorAction}
			/>
		</section>
	);
}
