import { AlertCircle, ArrowRight, RotateCcw, Search } from "lucide-react";
import { useEffect, useRef } from "react";

import { formatWorkspaceStatus, getProjectPath, getWorkspaceStatusTone } from "./workspacePresentation";

const isPlainPrimaryClick = (event) =>
	!event.defaultPrevented && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;

export function WorkspaceSearch({ value, onChange, label = "Search workspace", placeholder = "Search projects and work" }) {
	const inputRef = useRef(null);

	useEffect(() => {
		const focusSearch = (event) => {
			if (event.key.toLocaleLowerCase() !== "k" || (!event.metaKey && !event.ctrlKey) || event.altKey || event.shiftKey) {
				return;
			}
			event.preventDefault();
			inputRef.current?.focus();
			inputRef.current?.select();
		};
		window.addEventListener("keydown", focusSearch);
		return () => window.removeEventListener("keydown", focusSearch);
	}, []);

	return (
		<label className="workspace-search">
			<span className="sr-only">{label}</span>
			<Search aria-hidden="true" size={18} />
			<input
				ref={inputRef}
				type="search"
				value={value}
				onChange={(event) => onChange(event.target.value)}
				placeholder={placeholder}
				autoComplete="off"
			/>
			<kbd aria-hidden="true">Ctrl/⌘ K</kbd>
		</label>
	);
}

export function WorkspaceStatus({ status }) {
	return <span className={`workspace-status workspace-status-${getWorkspaceStatusTone(status)}`}>{formatWorkspaceStatus(status)}</span>;
}

export function ProjectProgress({ completed = 0, total = 0 }) {
	const safeCompleted = Math.max(0, Number(completed) || 0);
	const safeTotal = Math.max(0, Number(total) || 0);
	if (!safeTotal) {
		return <span className="workspace-progress-label">Workflow starting</span>;
	}

	return (
		<div className="workspace-progress">
			<div className="workspace-progress-copy">
				<span>Progress</span>
				<strong>
					{Math.min(safeCompleted, safeTotal)} of {safeTotal} stages
				</strong>
			</div>
			<progress
				value={Math.min(safeCompleted, safeTotal)}
				max={safeTotal}
				aria-label={`Workflow progress: ${Math.min(safeCompleted, safeTotal)} of ${safeTotal} stages complete`}
			>
				{Math.round((Math.min(safeCompleted, safeTotal) / safeTotal) * 100)}%
			</progress>
		</div>
	);
}

export function ProjectOpenLink({ projectId, destination, onOpenProject, children, className = "workspace-open-link", ariaLabel }) {
	if (!projectId) {
		return (
			<span className={`${className} workspace-open-link-disabled`} aria-disabled="true">
				<span>{children}</span>
			</span>
		);
	}
	const path = getProjectPath(projectId, destination);
	return (
		<a
			href={path}
			className={className}
			aria-label={ariaLabel}
			onClick={(event) => {
				if (!onOpenProject || !isPlainPrimaryClick(event)) return;
				event.preventDefault();
				onOpenProject({ projectId, destination, path });
			}}
		>
			<span>{children}</span>
			<ArrowRight aria-hidden="true" size={17} />
		</a>
	);
}

export function WorkspaceErrorState({ message, onRetry }) {
	return (
		<section className="workspace-state workspace-error-state" role="alert" aria-labelledby="workspace-error-title">
			<AlertCircle aria-hidden="true" size={24} />
			<div>
				<h2 id="workspace-error-title">We couldn’t load your workspace</h2>
				<p>{message || "Workspace summary is unavailable. Please try again."}</p>
			</div>
			{onRetry ? (
				<button type="button" className="secondary workspace-retry-button" onClick={onRetry}>
					<RotateCcw aria-hidden="true" size={16} />
					Retry
				</button>
			) : null}
		</section>
	);
}

export function WorkspaceLoadingState({ projectsOnly = false }) {
	return (
		<div
			className={`workspace-loading-grid ${projectsOnly ? "workspace-loading-projects" : ""}`}
			role="status"
			aria-live="polite"
			aria-label="Loading workspace"
			aria-busy="true"
		>
			<span className="sr-only">Loading workspace</span>
			{Array.from({ length: projectsOnly ? 6 : 4 }, (_, index) => (
				<div className="workspace-skeleton-card" key={index}>
					<span className="workspace-skeleton-line workspace-skeleton-short" />
					<span className="workspace-skeleton-line" />
					<span className="workspace-skeleton-line workspace-skeleton-medium" />
				</div>
			))}
		</div>
	);
}
