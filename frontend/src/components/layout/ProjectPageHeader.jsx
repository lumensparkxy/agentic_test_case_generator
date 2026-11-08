import RouteLink from "../../app/RouteLink";

export default function ProjectPageHeader({ title, project, navigate, titleId, actions, children, compact = false }) {
	return (
		<header className={`project-page-header${compact ? " compact-workbench-header" : ""}`}>
			<nav className="project-breadcrumb" aria-label="Breadcrumb">
				<RouteLink to="/projects" navigate={navigate}>
					Projects
				</RouteLink>
				<span aria-hidden="true">/</span>
				{compact ? (
					<span>
						{project?.name || "Project"} · Revision {project?.current_revision ?? 0}
					</span>
				) : (
					<span aria-current="page">{title}</span>
				)}
			</nav>
			<div className="project-page-title-row">
				<h1 id={titleId}>{title}</h1>
				{actions}
			</div>
			{!compact && (
				<p>
					{project?.name || "Project"} · Revision {project?.current_revision ?? 0}
				</p>
			)}
			{children}
		</header>
	);
}
