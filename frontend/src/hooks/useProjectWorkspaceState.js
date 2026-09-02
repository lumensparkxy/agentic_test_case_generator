import { useState } from "react";

export default function useProjectWorkspaceState() {
	const [projects, setProjects] = useState([]);
	const [currentProject, setCurrentProject] = useState(null);
	const [newProjectName, setNewProjectName] = useState("");
	const [isLoadingProjects, setIsLoadingProjects] = useState(false);
	const [isCreatingProject, setIsCreatingProject] = useState(false);
	const [isOpeningProject, setIsOpeningProject] = useState(false);
	const [orchestratorStatus, setOrchestratorStatus] = useState(null);
	const [isLoadingOrchestrator, setIsLoadingOrchestrator] = useState(false);
	const [orchestratorError, setOrchestratorError] = useState("");

	return {
		projects,
		setProjects,
		currentProject,
		setCurrentProject,
		newProjectName,
		setNewProjectName,
		isLoadingProjects,
		setIsLoadingProjects,
		isCreatingProject,
		setIsCreatingProject,
		isOpeningProject,
		setIsOpeningProject,
		orchestratorStatus,
		setOrchestratorStatus,
		isLoadingOrchestrator,
		setIsLoadingOrchestrator,
		orchestratorError,
		setOrchestratorError,
	};
}
