import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, FolderOpen, Plus, RefreshCw } from "lucide-react";

import AuthProviderIcon from "../auth/AuthProviderIcon";

function StatusUsagePills({ billingStatusItems, statusUsageItems, isUsageLoading, isBillingLoading }) {
	return (
		<div className="status-usage" aria-label="Current user usage summary">
			{billingStatusItems.length > 0
				? billingStatusItems.map((item) => (
						<span className={`status-usage-pill ${item.variant ? `status-usage-pill-${item.variant}` : ""}`} key={item.key}>
							<span className="status-usage-pill-label">{item.label}</span>
							<span className="status-usage-pill-value">{item.value}</span>
						</span>
					))
				: null}
			{statusUsageItems.length > 0
				? statusUsageItems.map((item) => (
						<span className={`status-usage-pill ${item.variant ? `status-usage-pill-${item.variant}` : ""}`} key={item.key}>
							<span className="status-usage-pill-label">{item.label}</span>
							<span className="status-usage-pill-value">{item.value}</span>
						</span>
					))
				: null}
			{isUsageLoading || isBillingLoading ? <span className="status-usage-loading">Loading usage…</span> : null}
		</div>
	);
}

function AuthPanel({
	isVerifyingSession,
	isAuthenticated,
	currentUser,
	getAuthProviderLabel,
	handleLogout,
	isAuthenticating,
	hasFirebaseAuthConfig,
	hasVisibleAuthProviders,
	openSignInDialog,
	currentAuthProviderLabel,
}) {
	return (
		<div className="auth-panel">
			{isVerifyingSession ? (
				<span className="auth-message">Checking session...</span>
			) : isAuthenticated ? (
				<div className="auth-user">
					<div className="auth-user-identity">
						{currentUser?.picture && <img src={currentUser.picture} alt={currentUser.name} className="auth-avatar" />}
						<div className="auth-user-meta">
							<strong>{currentUser?.name}</strong>
							<span>{currentUser?.email || getAuthProviderLabel(currentUser?.provider) || currentUser?.sub}</span>
						</div>
					</div>
					<button type="button" onClick={handleLogout} className="secondary auth-logout-btn" disabled={isAuthenticating}>
						{isAuthenticating ? "Signing out..." : "Sign Out"}
					</button>
				</div>
			) : hasFirebaseAuthConfig && hasVisibleAuthProviders ? (
				<div className="auth-login">
					<button type="button" onClick={openSignInDialog} disabled={isAuthenticating}>
						{isAuthenticating && currentAuthProviderLabel
							? `Signing in with ${currentAuthProviderLabel}...`
							: isAuthenticating
								? "Signing in..."
								: "Sign In"}
					</button>
				</div>
			) : hasFirebaseAuthConfig ? (
				<span className="auth-message auth-config-missing">No Firebase sign-in providers are currently available.</span>
			) : (
				<span className="auth-message auth-config-missing">Set the VITE_FIREBASE_* variables to enable Firebase sign-in.</span>
			)}
		</div>
	);
}

function ProjectMenu({
	projects,
	currentProject,
	isLoadingProjects,
	isOpeningProject,
	authActionDisabled,
	newProjectName,
	setNewProjectName,
	isCreatingProject,
	onOpenProject,
	onCreateProject,
	onRefreshProjects,
}) {
	const [isOpen, setIsOpen] = useState(false);
	const menuRef = useRef(null);
	const selectedProjectId = currentProject?.project_id || "";
	const isDisabled = authActionDisabled || isLoadingProjects || isOpeningProject;
	const triggerText = currentProject?.name || "Select project";
	const triggerMeta = currentProject ? `revision ${currentProject.current_revision}` : "Projects";

	useEffect(() => {
		if (!isOpen) {
			return undefined;
		}

		const handlePointerDown = (event) => {
			if (menuRef.current && !menuRef.current.contains(event.target)) {
				setIsOpen(false);
			}
		};
		const handleKeyDown = (event) => {
			if (event.key === "Escape") {
				setIsOpen(false);
			}
		};

		document.addEventListener("mousedown", handlePointerDown);
		document.addEventListener("keydown", handleKeyDown);
		return () => {
			document.removeEventListener("mousedown", handlePointerDown);
			document.removeEventListener("keydown", handleKeyDown);
		};
	}, [isOpen]);

	const handleOpenProject = async (projectId) => {
		await onOpenProject(projectId);
		setIsOpen(false);
	};

	const handleCreateProject = async (event) => {
		event.preventDefault();
		const createdProject = await onCreateProject();
		if (createdProject) {
			setIsOpen(false);
		}
	};

	return (
		<div className="command-project-control" ref={menuRef}>
			<span className="command-project-label">Projects</span>
			<button
				type="button"
				className="command-project-trigger"
				onClick={() => setIsOpen((current) => !current)}
				disabled={isDisabled}
				aria-label="Open QA project menu"
				aria-haspopup="dialog"
				aria-expanded={isOpen}
			>
				<span className="command-project-trigger-copy">
					<strong>{isOpeningProject ? "Opening project" : triggerText}</strong>
					<span>{isLoadingProjects ? "Loading projects" : triggerMeta}</span>
				</span>
				<ChevronDown aria-hidden="true" size={18} strokeWidth={2.1} />
			</button>

			{isOpen && (
				<div className="command-project-menu" role="dialog" aria-label="Projects">
					<div className="command-project-menu-header">
						<div>
							<strong>Projects</strong>
							<span>{projects.length ? `${projects.length} available` : "No projects yet"}</span>
						</div>
						<button
							type="button"
							className="command-project-menu-action"
							onClick={onRefreshProjects}
							disabled={authActionDisabled || isLoadingProjects}
						>
							<RefreshCw aria-hidden="true" size={16} strokeWidth={2.1} />
							{isLoadingProjects ? "Refreshing" : "Refresh projects"}
						</button>
					</div>

					<div className="command-project-list" aria-label="Available QA projects">
						{projects.length ? (
							projects.map((project) => {
								const isSelected = selectedProjectId === project.project_id;
								return (
									<button
										type="button"
										key={project.project_id}
										className={`command-project-option ${isSelected ? "selected" : ""}`}
										onClick={() => handleOpenProject(project.project_id)}
										disabled={authActionDisabled || isOpeningProject}
										aria-label={`Open QA project ${project.name}`}
										aria-current={isSelected ? "true" : undefined}
									>
										<FolderOpen aria-hidden="true" size={18} strokeWidth={2.1} />
										<span>
											<strong>{project.name}</strong>
											<span>revision {project.current_revision}</span>
										</span>
										{isSelected && <Check aria-hidden="true" size={17} strokeWidth={2.4} />}
									</button>
								);
							})
						) : (
							<p className="command-project-empty">Create a QA project to persist workflow progress.</p>
						)}
					</div>

					{currentProject && (
						<button
							type="button"
							className="command-project-clear"
							onClick={() => handleOpenProject("")}
							disabled={authActionDisabled || isOpeningProject}
						>
							Clear selection
						</button>
					)}

					<form className="command-project-create" onSubmit={handleCreateProject}>
						<label htmlFor="command-project-create-name">New project</label>
						<div className="command-project-create-row">
							<input
								id="command-project-create-name"
								type="text"
								value={newProjectName}
								onChange={(event) => setNewProjectName(event.target.value)}
								placeholder="New QA project name"
								disabled={authActionDisabled || isCreatingProject}
							/>
							<button type="submit" disabled={authActionDisabled || isCreatingProject || !newProjectName.trim()}>
								<Plus aria-hidden="true" size={16} strokeWidth={2.2} />
								{isCreatingProject ? "Creating" : "New Project"}
							</button>
						</div>
					</form>
				</div>
			)}
		</div>
	);
}

export function SignInDialog({ isOpen, onOverlayClick, onClose, isAuthenticating, providers, onProviderSignIn }) {
	if (!isOpen) {
		return null;
	}

	return (
		<div className="auth-dialog-overlay" onClick={onOverlayClick}>
			<div
				className="auth-dialog"
				role="dialog"
				aria-modal="true"
				aria-labelledby="auth-dialog-title"
				onClick={(event) => event.stopPropagation()}
			>
				<div className="auth-dialog-header">
					<div>
						<h2 id="auth-dialog-title">Choose a sign-in method</h2>
						<p>Select one provider to continue into the workspace.</p>
					</div>
					<button
						type="button"
						className="auth-dialog-close"
						onClick={onClose}
						disabled={isAuthenticating}
						aria-label="Close sign-in dialog"
					>
						×
					</button>
				</div>
				<div className="auth-provider-list">
					{providers.map((provider) => (
						<button
							key={provider.id}
							type="button"
							className={`auth-provider-option auth-provider-option--${provider.buttonVariant || provider.id}`}
							onClick={() => onProviderSignIn(provider.id)}
							disabled={isAuthenticating}
						>
							<span className="auth-provider-option-icon" aria-hidden="true">
								<AuthProviderIcon providerId={provider.id} />
							</span>
							<span className="auth-provider-option-label">{provider.buttonText || `Sign in with ${provider.label}`}</span>
						</button>
					))}
				</div>
			</div>
		</div>
	);
}

export default function AppNavigationControls({
	isAuthenticated,
	billingStatusItems,
	statusUsageItems,
	isUsageLoading,
	isBillingLoading,
	onOpenSettings,
	isVerifyingSession,
	currentUser,
	getAuthProviderLabel,
	handleLogout,
	isAuthenticating,
	hasFirebaseAuthConfig,
	hasVisibleAuthProviders,
	openSignInDialog,
	currentAuthProviderLabel,
	projects,
	currentProject,
	isLoadingProjects,
	isOpeningProject,
	authActionDisabled,
	newProjectName,
	setNewProjectName,
	isCreatingProject,
	onOpenProject,
	onCreateProject,
	onRefreshProjects,
}) {
	const healthLabel = isAuthenticated ? "System healthy" : "Sign in required";

	return (
		<div className="app-navigation-controls" aria-label="Workspace controls">
			<ProjectMenu
				projects={projects}
				currentProject={currentProject}
				isLoadingProjects={isLoadingProjects}
				isOpeningProject={isOpeningProject}
				authActionDisabled={authActionDisabled}
				newProjectName={newProjectName}
				setNewProjectName={setNewProjectName}
				isCreatingProject={isCreatingProject}
				onOpenProject={onOpenProject}
				onCreateProject={onCreateProject}
				onRefreshProjects={onRefreshProjects}
			/>

			<details className={`command-health ${isAuthenticated ? "status-authenticated" : ""}`}>
				<summary>
					<span className="command-health-dot" aria-hidden="true" />
					<span className="status-message">{healthLabel}</span>
				</summary>
				<div className="command-health-details">
					<p>
						{isAuthenticated ? "Session active. Workflow messages stay in the active workspace." : "Sign in to enable workflow actions."}
					</p>
					{isAuthenticated && (
						<StatusUsagePills
							billingStatusItems={billingStatusItems}
							statusUsageItems={statusUsageItems}
							isUsageLoading={isUsageLoading}
							isBillingLoading={isBillingLoading}
						/>
					)}
				</div>
			</details>
			<button
				type="button"
				className="settings-open-btn"
				data-testid="settings-open-button"
				onClick={onOpenSettings}
				aria-label="Open settings"
			>
				Settings
			</button>
			<AuthPanel
				isVerifyingSession={isVerifyingSession}
				isAuthenticated={isAuthenticated}
				currentUser={currentUser}
				getAuthProviderLabel={getAuthProviderLabel}
				handleLogout={handleLogout}
				isAuthenticating={isAuthenticating}
				hasFirebaseAuthConfig={hasFirebaseAuthConfig}
				hasVisibleAuthProviders={hasVisibleAuthProviders}
				openSignInDialog={openSignInDialog}
				currentAuthProviderLabel={currentAuthProviderLabel}
			/>
		</div>
	);
}
