import { FolderPlus } from "lucide-react";
import { useMemo, useState } from "react";

import { PROJECT_DESTINATIONS, buildProjectPath } from "../app/workflowRoutes";
import { WorkspaceProjectsSection } from "../components/workspace/HomeWorkspaceSections";
import { WorkspaceErrorState, WorkspaceLoadingState, WorkspaceSearch } from "../components/workspace/WorkspacePrimitives";
import { matchesWorkspaceQuery, normalizeWorkspaceList } from "../components/workspace/workspacePresentation";

const MAX_PROJECT_NAME_LENGTH = 160;

export default function ProjectsPage({
	projects = [],
	isLoading = false,
	error = "",
	onRetry,
	onCreateProject,
	onOpenProject,
	isCreatingProject = false,
}) {
	const [query, setQuery] = useState("");
	const [projectName, setProjectName] = useState("");
	const [createError, setCreateError] = useState("");
	const [createNotice, setCreateNotice] = useState("");
	const [isSubmitting, setIsSubmitting] = useState(false);
	const normalizedProjects = normalizeWorkspaceList(projects);
	const filteredProjects = useMemo(
		() =>
			normalizedProjects.filter((project) =>
				matchesWorkspaceQuery(query, project?.name, project?.current_stage, project?.current_status, project?.reason)
			),
		[normalizedProjects, query]
	);
	const createBusy = isCreatingProject || isSubmitting;

	const handleCreateProject = async (event) => {
		event.preventDefault();
		const name = projectName.trim();
		setCreateError("");
		setCreateNotice("");
		if (!name) {
			setCreateError("Enter a project name.");
			return;
		}
		if (name.length > MAX_PROJECT_NAME_LENGTH) {
			setCreateError(`Use ${MAX_PROJECT_NAME_LENGTH} characters or fewer.`);
			return;
		}
		if (typeof onCreateProject !== "function") {
			setCreateError("Project creation is unavailable right now.");
			return;
		}

		setIsSubmitting(true);
		try {
			const result = await onCreateProject(name);
			if (result?.error) {
				throw new Error(result.error);
			}
			const createdProject = result?.project || result;
			if (!createdProject?.project_id) {
				throw new Error("Project could not be created. Check the name and try again.");
			}
			setProjectName("");
			setCreateNotice(`Created ${createdProject.name || name}.`);
			await onOpenProject?.({
				projectId: createdProject.project_id,
				destination: PROJECT_DESTINATIONS.OVERVIEW,
				path: buildProjectPath(createdProject.project_id),
			});
		} catch (createProjectError) {
			if (createProjectError?.name === "AbortError") {
				return;
			}
			setCreateError(createProjectError?.message || "Project could not be created. Try again.");
		} finally {
			setIsSubmitting(false);
		}
	};

	return (
		<main
			id="main-content"
			className="workspace-page workspace-projects-page"
			aria-labelledby="workspace-projects-page-title"
			aria-busy={isLoading || createBusy || undefined}
			tabIndex={-1}
		>
			<header className="workspace-page-header">
				<div>
					<span className="workspace-eyebrow">Workspace</span>
					<h1 id="workspace-projects-page-title">Projects</h1>
					<p>Find active work or create a focused QA workspace.</p>
				</div>
				<WorkspaceSearch value={query} onChange={setQuery} label="Search projects" placeholder="Search active projects" />
			</header>

			<section className="workspace-create-panel" aria-labelledby="create-project-title">
				<div>
					<span className="workspace-create-icon">
						<FolderPlus aria-hidden="true" size={20} />
					</span>
					<div>
						<h2 id="create-project-title">Create a project</h2>
						<p>Give the workspace a clear product or initiative name.</p>
					</div>
				</div>
				<form onSubmit={handleCreateProject} aria-label="Create project" noValidate>
					<label htmlFor="new-workspace-project-name">Project name</label>
					<div className="workspace-create-controls">
						<input
							id="new-workspace-project-name"
							type="text"
							value={projectName}
							onChange={(event) => {
								setProjectName(event.target.value);
								setCreateError("");
								setCreateNotice("");
							}}
							maxLength={MAX_PROJECT_NAME_LENGTH}
							disabled={createBusy}
							autoComplete="off"
							placeholder="e.g. Customer onboarding"
							aria-invalid={Boolean(createError)}
							aria-describedby={createError ? "create-project-error" : createNotice ? "create-project-notice" : undefined}
						/>
						<button type="submit" disabled={createBusy}>
							<FolderPlus aria-hidden="true" size={17} />
							{createBusy ? "Creating…" : "Create project"}
						</button>
					</div>
					{createError ? (
						<p className="workspace-form-message workspace-form-error" id="create-project-error" role="alert">
							{createError}
						</p>
					) : null}
					{createNotice ? (
						<p className="workspace-form-message workspace-form-success" id="create-project-notice" role="status">
							{createNotice}
						</p>
					) : null}
				</form>
			</section>

			{error ? (
				<WorkspaceErrorState message={error} onRetry={onRetry} />
			) : isLoading ? (
				<WorkspaceLoadingState projectsOnly />
			) : (
				<WorkspaceProjectsSection
					projects={filteredProjects}
					onOpenProject={onOpenProject}
					title="Projects"
					eyebrow={normalizedProjects.length ? "Workspace list" : "Start here"}
					emptyMessage={
						normalizedProjects.length
							? `No active projects match “${query.trim()}”.`
							: "Create your first project above to start building grounded test coverage."
					}
					labelledBy="active-projects-title"
				/>
			)}
		</main>
	);
}
