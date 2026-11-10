import RouteLink from "../../app/RouteLink";
import { PROJECT_WORKSPACES, buildProjectPath, buildProjectWorkspacePath } from "../../app/workflowRoutes";

export default function ProjectWorkspaceNavigation({ route, navigate, counts, reviewEntrySection, reviewFindings }) {
	const { projectId, workspace, section } = route;
	const activeWorkspace = PROJECT_WORKSPACES.find(({ id }) => id === workspace);

	return (
		<header className="project-workspace-navigation">
			<nav aria-label="Project workspaces">
				<ul className="global-navigation-list">
					{PROJECT_WORKSPACES.map(({ id, label }) => (
						<li key={id}>
							<RouteLink
								to={buildProjectWorkspacePath(projectId, id, id === "review" ? reviewEntrySection : undefined)}
								navigate={navigate}
								className={id === workspace ? "global-navigation-link active" : "global-navigation-link"}
								aria-current={id === workspace ? "location" : undefined}
							>
								{label}
							</RouteLink>
						</li>
					))}
				</ul>
			</nav>
			{activeWorkspace && (
				<nav className="project-workspace-sections" aria-label="Workspace sections">
					<ul className="global-navigation-list">
						{activeWorkspace.sections.map(({ id, label }) => (
							<li key={id}>
								<RouteLink
									to={buildProjectWorkspacePath(projectId, workspace, id)}
									navigate={navigate}
									className="project-workspace-section"
									aria-current={id === section && !reviewFindings?.active ? "page" : undefined}
								>
									{label}
								</RouteLink>
							</li>
						))}
						{reviewFindings && (
							<li>
								<RouteLink
									to={reviewFindings.path}
									navigate={navigate}
									className="project-workspace-section"
									aria-current={reviewFindings.active ? "page" : undefined}
								>
									{reviewFindings.label}
								</RouteLink>
							</li>
						)}
					</ul>
				</nav>
			)}
			<div className="project-workspace-meta">
				{activeWorkspace && (
					<p className="project-workspace-counts">
						{counts.requirements} {counts.requirements === 1 ? "requirement" : "requirements"} · {counts.scenarios}{" "}
						{counts.scenarios === 1 ? "scenario" : "scenarios"} · {counts.testCases} {counts.testCases === 1 ? "test" : "tests"}
					</p>
				)}
				<RouteLink to={buildProjectPath(projectId)} navigate={navigate} aria-current={!workspace ? "page" : undefined}>
					Project status
				</RouteLink>
			</div>
		</header>
	);
}
