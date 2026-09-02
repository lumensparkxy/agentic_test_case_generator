import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown, FolderOpen, LogOut, Plus, RefreshCw, Settings, UserRound } from "lucide-react";

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

function HealthMenu({ isAuthenticated, billingStatusItems, statusUsageItems, isUsageLoading, isBillingLoading }) {
	const [isOpen, setIsOpen] = useState(false);
	const healthRef = useRef(null);
	const healthTriggerRef = useRef(null);
	const healthDetailsId = useId();
	const healthLabel = isAuthenticated ? "System healthy" : "Sign in required";
	const closeAndRestoreFocus = useCallback(() => {
		setIsOpen(false);
		window.requestAnimationFrame(() => healthTriggerRef.current?.focus());
	}, []);

	useEffect(() => {
		if (!isOpen) return undefined;
		const handlePointerDown = (event) => {
			if (healthRef.current && !healthRef.current.contains(event.target)) setIsOpen(false);
		};
		document.addEventListener("mousedown", handlePointerDown);
		return () => document.removeEventListener("mousedown", handlePointerDown);
	}, [isOpen]);

	return (
		<div
			ref={healthRef}
			className={`command-health ${isAuthenticated ? "status-authenticated" : ""}`}
			onKeyDown={(event) => {
				if (event.key !== "Escape" || !isOpen) return;
				event.preventDefault();
				event.stopPropagation();
				closeAndRestoreFocus();
			}}
		>
			<button
				ref={healthTriggerRef}
				type="button"
				className="command-health-trigger"
				onClick={() => setIsOpen((current) => !current)}
				aria-label={`${isOpen ? "Close" : "Open"} system health details`}
				aria-expanded={isOpen}
				aria-controls={healthDetailsId}
			>
				<span className="command-health-dot" aria-hidden="true" />
				<span className="status-message">{healthLabel}</span>
			</button>
			{isOpen ? (
				<div id={healthDetailsId} className="command-health-details">
					<p>
						{isAuthenticated ? "Session active. Workflow messages stay in the active workspace." : "Sign in to enable workflow actions."}
					</p>
					{isAuthenticated ? (
						<StatusUsagePills
							billingStatusItems={billingStatusItems}
							statusUsageItems={statusUsageItems}
							isUsageLoading={isUsageLoading}
							isBillingLoading={isBillingLoading}
						/>
					) : null}
				</div>
			) : null}
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
	onOpenSettings,
}) {
	const [isAccountMenuOpen, setIsAccountMenuOpen] = useState(false);
	const accountRef = useRef(null);
	const accountMenuRef = useRef(null);
	const accountTriggerRef = useRef(null);
	const accountMenuId = useId();
	const userName = currentUser?.name || "Signed in";
	const userIdentifier = currentUser?.email || getAuthProviderLabel(currentUser?.provider) || currentUser?.sub || "Active session";
	const closeAndRestoreFocus = useCallback(() => {
		setIsAccountMenuOpen(false);
		window.requestAnimationFrame(() => accountTriggerRef.current?.focus());
	}, []);

	useEffect(() => {
		if (!isAccountMenuOpen) return undefined;
		const focusFrame = window.requestAnimationFrame(() => {
			accountMenuRef.current?.querySelector('[role="menuitem"]:not(:disabled)')?.focus();
		});
		const handlePointerDown = (event) => {
			if (accountRef.current && !accountRef.current.contains(event.target)) setIsAccountMenuOpen(false);
		};
		document.addEventListener("mousedown", handlePointerDown);
		return () => {
			window.cancelAnimationFrame(focusFrame);
			document.removeEventListener("mousedown", handlePointerDown);
		};
	}, [isAccountMenuOpen]);

	const handleAccountMenuKeyDown = (event) => {
		if (event.key === "Escape") {
			event.preventDefault();
			event.stopPropagation();
			closeAndRestoreFocus();
			return;
		}
		if (event.key === "Tab") {
			setIsAccountMenuOpen(false);
			return;
		}
		if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
		const items = Array.from(accountMenuRef.current?.querySelectorAll('[role="menuitem"]:not(:disabled)') || []);
		if (!items.length) return;
		event.preventDefault();
		const currentIndex = items.indexOf(document.activeElement);
		const nextIndex =
			event.key === "Home"
				? 0
				: event.key === "End"
					? items.length - 1
					: event.key === "ArrowDown"
						? (currentIndex + 1) % items.length
						: (currentIndex - 1 + items.length) % items.length;
		items[nextIndex]?.focus();
	};

	return (
		<div className="auth-panel">
			{isVerifyingSession ? (
				<span className="auth-message">Checking session...</span>
			) : isAuthenticated ? (
				<div className="auth-account" ref={accountRef}>
					<button
						ref={accountTriggerRef}
						type="button"
						className="auth-account-trigger"
						onClick={() => setIsAccountMenuOpen((current) => !current)}
						aria-label={`Open account menu for ${userName}`}
						aria-haspopup="menu"
						aria-expanded={isAccountMenuOpen}
						aria-controls={accountMenuId}
					>
						{currentUser?.picture ? (
							<img src={currentUser.picture} alt="" className="auth-avatar" />
						) : (
							<span className="auth-avatar auth-avatar-fallback" aria-hidden="true">
								<UserRound size={18} strokeWidth={2.1} />
							</span>
						)}
						<span className="auth-account-trigger-name">{userName}</span>
						<ChevronDown className="auth-account-trigger-chevron" aria-hidden="true" size={15} strokeWidth={2.1} />
					</button>
					{isAccountMenuOpen ? (
						<div
							ref={accountMenuRef}
							id={accountMenuId}
							className="auth-account-menu"
							role="menu"
							aria-label="Account menu"
							onKeyDown={handleAccountMenuKeyDown}
						>
							<div className="auth-user-identity">
								{currentUser?.picture ? (
									<img src={currentUser.picture} alt="" className="auth-avatar" />
								) : (
									<span className="auth-avatar auth-avatar-fallback" aria-hidden="true">
										<UserRound size={18} strokeWidth={2.1} />
									</span>
								)}
								<div className="auth-user-meta">
									<strong>{userName}</strong>
									<span>{userIdentifier}</span>
								</div>
							</div>
							<div className="auth-account-actions">
								<button
									type="button"
									role="menuitem"
									onClick={() => {
										setIsAccountMenuOpen(false);
										onOpenSettings();
									}}
								>
									<Settings aria-hidden="true" size={17} strokeWidth={2.1} />
									Settings
								</button>
								<button
									type="button"
									role="menuitem"
									onClick={() => {
										setIsAccountMenuOpen(false);
										handleLogout();
									}}
									disabled={isAuthenticating}
								>
									<LogOut aria-hidden="true" size={17} strokeWidth={2.1} />
									{isAuthenticating ? "Signing out..." : "Sign Out"}
								</button>
							</div>
						</div>
					) : null}
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
	const dialogRef = useRef(null);
	const triggerRef = useRef(null);
	const selectedProjectId = currentProject?.project_id || "";
	const isDisabled = authActionDisabled || isLoadingProjects || isOpeningProject;
	const triggerText = currentProject?.name || "Select project";
	const triggerMeta = currentProject ? `revision ${currentProject.current_revision}` : "Projects";
	const closeAndRestoreFocus = useCallback(() => {
		setIsOpen(false);
		window.requestAnimationFrame(() => triggerRef.current?.focus());
	}, []);

	useEffect(() => {
		if (!isOpen) {
			return undefined;
		}
		const focusFrame = window.requestAnimationFrame(() => {
			const selectedProject = dialogRef.current?.querySelector('.command-project-option[aria-current="true"]');
			const firstControl = dialogRef.current?.querySelector("button:not(:disabled), input:not(:disabled)");
			(selectedProject || firstControl)?.focus();
		});

		const handlePointerDown = (event) => {
			if (menuRef.current && !menuRef.current.contains(event.target)) {
				setIsOpen(false);
			}
		};
		const handleKeyDown = (event) => {
			if (event.key === "Escape") {
				event.preventDefault();
				closeAndRestoreFocus();
			}
		};

		document.addEventListener("mousedown", handlePointerDown);
		document.addEventListener("keydown", handleKeyDown);
		return () => {
			window.cancelAnimationFrame(focusFrame);
			document.removeEventListener("mousedown", handlePointerDown);
			document.removeEventListener("keydown", handleKeyDown);
		};
	}, [closeAndRestoreFocus, isOpen]);

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
			<button
				ref={triggerRef}
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
				<div
					ref={dialogRef}
					className="command-project-menu"
					role="dialog"
					aria-label="Projects"
					onKeyDown={(event) => {
						if (event.key !== "Escape") return;
						event.preventDefault();
						event.stopPropagation();
						closeAndRestoreFocus();
					}}
				>
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

			<HealthMenu
				isAuthenticated={isAuthenticated}
				billingStatusItems={billingStatusItems}
				statusUsageItems={statusUsageItems}
				isUsageLoading={isUsageLoading}
				isBillingLoading={isBillingLoading}
			/>
			<button
				type="button"
				className="settings-open-btn"
				data-testid="settings-open-button"
				onClick={onOpenSettings}
				aria-label="Open settings"
			>
				<Settings aria-hidden="true" size={18} strokeWidth={2.1} />
				<span className="settings-open-label">Settings</span>
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
				onOpenSettings={onOpenSettings}
			/>
		</div>
	);
}
