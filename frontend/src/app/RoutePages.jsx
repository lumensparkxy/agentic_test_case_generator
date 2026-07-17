import RouteLink from "./RouteLink";
import {
	GLOBAL_DESTINATIONS,
	PROJECT_DESTINATIONS,
	PROJECT_NAV_ITEMS,
	buildGlobalPath,
	buildProjectPath,
	getDestinationForStage,
} from "./workflowRoutes";

const GLOBAL_PAGE_LABELS = Object.freeze({
	[GLOBAL_DESTINATIONS.REVIEWS]: "Review Inbox",
	[GLOBAL_DESTINATIONS.RUNS]: "Runs",
	[GLOBAL_DESTINATIONS.REPORTS]: "Reports",
});

function RecoveryLinks({ navigate, projectId = null, includeProjectOverview = false }) {
	return (
		<div className="route-recovery-actions">
			<RouteLink className="route-primary-link" to={buildGlobalPath(GLOBAL_DESTINATIONS.HOME)} navigate={navigate}>
				Go to Home
			</RouteLink>
			<RouteLink className="route-secondary-link" to={buildGlobalPath(GLOBAL_DESTINATIONS.PROJECTS)} navigate={navigate}>
				View Projects
			</RouteLink>
			{includeProjectOverview && projectId ? (
				<RouteLink className="route-secondary-link" to={buildProjectPath(projectId)} navigate={navigate}>
					Open project overview
				</RouteLink>
			) : null}
		</div>
	);
}

export function HomeRoutePage({ navigate, continueProject = null }) {
	const continueProjectId = continueProject?.project_id || continueProject?.projectId || "";
	return (
		<main className="route-page" aria-labelledby="home-route-title">
			<header className="route-page-header">
				<span className="route-page-kicker">Workspace</span>
				<h1 id="home-route-title">Home</h1>
				<p>Choose what to work on without reopening stale project work.</p>
			</header>
			<section className="route-page-card" aria-labelledby="home-start-title">
				<h2 id="home-start-title">Start working</h2>
				{continueProjectId ? (
					<>
						<p>Continue with {continueProject.name || "your most recent project"}, or choose another project.</p>
						<div className="route-recovery-actions">
							<RouteLink className="route-primary-link" to={buildProjectPath(continueProjectId)} navigate={navigate}>
								Continue working
							</RouteLink>
							<RouteLink className="route-secondary-link" to={buildGlobalPath(GLOBAL_DESTINATIONS.PROJECTS)} navigate={navigate}>
								View Projects
							</RouteLink>
						</div>
					</>
				) : (
					<>
						<p>Select an existing project or create one to begin a project workflow.</p>
						<RouteLink className="route-primary-link" to={buildGlobalPath(GLOBAL_DESTINATIONS.PROJECTS)} navigate={navigate}>
							View Projects
						</RouteLink>
					</>
				)}
			</section>
		</main>
	);
}

export function ProjectsRoutePage({ navigate, projects = [], isLoading = false, error = "", onRetry = null }) {
	return (
		<main className="route-page" aria-labelledby="projects-route-title" aria-busy={isLoading || undefined}>
			<header className="route-page-header">
				<span className="route-page-kicker">Workspace</span>
				<h1 id="projects-route-title">Projects</h1>
				<p>Open a QA project at its stable overview and workflow destinations.</p>
			</header>
			{error ? (
				<div className="route-inline-error" role="alert">
					<p>{error}</p>
					{onRetry ? (
						<button type="button" className="secondary" onClick={onRetry}>
							Retry
						</button>
					) : null}
				</div>
			) : null}
			{isLoading ? (
				<div className="route-project-list" aria-label="Loading projects">
					<div className="route-project-card route-skeleton" />
					<div className="route-project-card route-skeleton" />
				</div>
			) : projects.length ? (
				<ul className="route-project-list" aria-label="QA projects">
					{projects.map((project) => (
						<li className="route-project-card" key={project.project_id}>
							<div>
								<strong>{project.name}</strong>
								<span>Revision {project.current_revision ?? 0}</span>
							</div>
							<RouteLink className="route-secondary-link" to={buildProjectPath(project.project_id)} navigate={navigate}>
								Open project
							</RouteLink>
						</li>
					))}
				</ul>
			) : (
				<section className="route-page-card route-empty-state" aria-labelledby="projects-empty-title">
					<h2 id="projects-empty-title">No projects yet</h2>
					<p>Use the project control to create your first QA project.</p>
				</section>
			)}
		</main>
	);
}

export function GlobalDestinationPlaceholderPage({ destination, navigate }) {
	const label = GLOBAL_PAGE_LABELS[destination] || "Workspace destination";
	return (
		<main className="route-page" aria-labelledby="global-placeholder-title">
			<header className="route-page-header">
				<span className="route-page-kicker">Workspace</span>
				<h1 id="global-placeholder-title">{label}</h1>
				<p>This destination has a stable URL. Its full workspace view is not available yet.</p>
			</header>
			<RecoveryLinks navigate={navigate} />
		</main>
	);
}

export function RouteRecoveryPage({ kind = "not-found", title = "", message = "", projectId = null, navigate }) {
	const invalidProjectDestination = kind === "invalid-stage";
	const resolvedTitle =
		title ||
		(kind === "missing" || kind === "forbidden" || kind === "inaccessible"
			? "Project unavailable"
			: invalidProjectDestination
				? "Project destination unavailable"
				: kind === "error"
					? "We couldn’t open this project"
					: "Page not found");
	const resolvedMessage =
		message ||
		(kind === "forbidden"
			? "You do not have access to this project. Choose another project or return Home."
			: kind === "missing"
				? "This project could not be found. It may have been removed or the link may be out of date."
				: invalidProjectDestination
					? "This project link does not match a supported workflow destination."
					: "The requested address does not match an available workspace page.");

	return (
		<main className="route-page route-recovery-page" aria-labelledby="route-recovery-title">
			<header className="route-page-header">
				<span className="route-page-kicker">Recovery</span>
				<h1 id="route-recovery-title">{resolvedTitle}</h1>
				<p>{resolvedMessage}</p>
			</header>
			<RecoveryLinks navigate={navigate} projectId={projectId} includeProjectOverview={invalidProjectDestination} />
		</main>
	);
}

export function ProjectLoadingPage({ projectId = "" }) {
	return (
		<main className="route-page route-loading-page" aria-labelledby="project-loading-title" aria-live="polite" aria-busy="true">
			<header className="route-page-header">
				<span className="route-page-kicker">Project</span>
				<h1 id="project-loading-title">Opening project…</h1>
				<p>Loading the current workflow, status, and project evidence.</p>
			</header>
			{projectId ? <span className="route-loading-detail">Project {projectId}</span> : null}
		</main>
	);
}

export function ProjectOverviewPage({ project, status = null, navigate }) {
	const projectId = project?.project_id || "";
	const currentStage = status?.current_stage || project?.latest_stage || "requirements";
	const recommendedDestination = getDestinationForStage(currentStage) || PROJECT_DESTINATIONS.REQUIREMENTS;

	return (
		<main className="route-page" aria-labelledby="project-overview-title">
			<header className="route-page-header">
				<span className="route-page-kicker">Project overview</span>
				<h1 id="project-overview-title">{project?.name || "Project"}</h1>
				<p>
					Revision {status?.project_revision ?? project?.current_revision ?? 0} · Current stage {`${currentStage}`.replaceAll("_", " ")}
				</p>
			</header>
			{projectId ? (
				<>
					<section className="route-page-card" aria-labelledby="project-continue-title">
						<h2 id="project-continue-title">Continue this project</h2>
						<p>Open the workbench that matches the project’s current orchestrator stage.</p>
						<RouteLink className="route-primary-link" to={buildProjectPath(projectId, recommendedDestination)} navigate={navigate}>
							Continue working
						</RouteLink>
					</section>
					<ul className="route-workbench-list" aria-label="Project workbenches">
						{PROJECT_NAV_ITEMS.filter((item) => item.id !== PROJECT_DESTINATIONS.OVERVIEW).map((item) => (
							<li key={item.id}>
								<RouteLink to={buildProjectPath(projectId, item.id)} navigate={navigate}>
									<strong>{item.label}</strong>
									<span>{item.title}</span>
								</RouteLink>
							</li>
						))}
					</ul>
				</>
			) : (
				<RecoveryLinks navigate={navigate} />
			)}
		</main>
	);
}

export function UseCasesPlaceholderPage({ project, navigate }) {
	const projectId = project?.project_id || "";
	const snapshot = project?.current_snapshots?.use_cases || null;
	return (
		<main className="route-page" aria-labelledby="use-cases-placeholder-title">
			<header className="route-page-header">
				<span className="route-page-kicker">{project?.name || "Project"}</span>
				<h1 id="use-cases-placeholder-title">Use Cases</h1>
				<p>
					{snapshot
						? "The current use-case artifact is preserved. The dedicated review workbench is not available yet."
						: "No current use-case artifact is available. Continue through Test Cases to generate project coverage."}
				</p>
			</header>
			{projectId ? (
				<div className="route-recovery-actions">
					<RouteLink className="route-primary-link" to={buildProjectPath(projectId, PROJECT_DESTINATIONS.TEST_CASES)} navigate={navigate}>
						Open Test Cases
					</RouteLink>
					<RouteLink className="route-secondary-link" to={buildProjectPath(projectId)} navigate={navigate}>
						Back to project overview
					</RouteLink>
				</div>
			) : (
				<RecoveryLinks navigate={navigate} />
			)}
		</main>
	);
}
