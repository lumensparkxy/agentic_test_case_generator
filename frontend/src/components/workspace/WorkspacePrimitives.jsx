import { SearchField, Link, Button } from "../ui/controls";
import { Progress, Badge } from "../ui/surfaces";
import { CollectionState } from "../ui/collections";
import { AlertCircle, ArrowRight, RotateCcw, Search } from "lucide-react";
import { useEffect, useRef } from "react";

import { formatWorkspaceStatus, getProjectPath, getWorkspaceStatusTone } from "./workspacePresentation";

const isPlainPrimaryClick = (event) =>
	!event.defaultPrevented && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;

export function WorkspaceSearch({
	value,
	onChange,
	label = "Search workspace",
	placeholder = "Search projects and work",
	inputRef: providedInputRef,
}) {
	const internalInputRef = useRef(null);
	const inputRef = providedInputRef || internalInputRef;

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
		<SearchField
			className="workspace-search"
			label={label}
			ref={inputRef}
			value={value}
			onChange={(event) => onChange(event.target.value)}
			placeholder={placeholder}
			autoComplete="off"
			shortcut="Ctrl/⌘ K"
		/>
	);
}

export function WorkspaceStatus({ status }) {
	return (
		<Badge tone={getWorkspaceStatusTone(status)} className={`workspace-status workspace-status-${getWorkspaceStatusTone(status)}`}>
			{formatWorkspaceStatus(status)}
		</Badge>
	);
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
			<Progress
				value={Math.min(safeCompleted, safeTotal)}
				max={safeTotal}
				aria-label={`Workflow progress: ${Math.min(safeCompleted, safeTotal)} of ${safeTotal} stages complete`}
			>
				{Math.round((Math.min(safeCompleted, safeTotal) / safeTotal) * 100)}%
			</Progress>
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
		<Link
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
		</Link>
	);
}

export function WorkspaceErrorState({ message, onRetry }) {
	return (
		<CollectionState
			as="section"
			kind="error"
			className="workspace-state workspace-error-state"
			role="alert"
			aria-labelledby="workspace-error-title"
		>
			<AlertCircle aria-hidden="true" size={24} />
			<div>
				<h2 id="workspace-error-title">We couldn’t load your workspace</h2>
				<p>{message || "Workspace summary is unavailable. Please try again."}</p>
			</div>
			{onRetry ? (
				<Button type="button" className="secondary workspace-retry-button" onClick={onRetry}>
					<RotateCcw aria-hidden="true" size={16} />
					Retry
				</Button>
			) : null}
		</CollectionState>
	);
}

export function WorkspaceLoadingState({ projectsOnly = false }) {
	return (
		<CollectionState
			as="div"
			kind="loading"
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
		</CollectionState>
	);
}
