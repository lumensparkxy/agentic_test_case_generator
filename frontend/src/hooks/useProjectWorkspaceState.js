import { useState } from "react";

export default function useProjectWorkspaceState() {
	const [projects, setProjects] = useState([]);
	const [currentProject, setCurrentProject] = useState(null);
	const [newProjectName, setNewProjectName] = useState("");
	const [isLoadingProjects, setIsLoadingProjects] = useState(false);
	const [isCreatingProject, setIsCreatingProject] = useState(false);
	const [isOpeningProject, setIsOpeningProject] = useState(false);

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
	};
}
