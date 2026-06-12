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
					{currentUser?.picture && <img src={currentUser.picture} alt={currentUser.name} className="auth-avatar" />}
					<div className="auth-user-meta">
						<strong>{currentUser?.name}</strong>
						<span>{currentUser?.email || getAuthProviderLabel(currentUser?.provider) || currentUser?.sub}</span>
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

export default function AppHeader({
	status,
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
}) {
	return (
		<header className="header">
			<div>
				<h1 className="title">Agentic Test Case Generator</h1>
				<p className="subtitle">
					A guided pipeline to parse requirements, enrich context, generate test cases, and export polished artifacts.
				</p>
			</div>
			<div className="header-right">
				<div className={`status ${isAuthenticated ? "status-authenticated" : ""}`}>
					<strong>Status:</strong>
					<span className="status-message">{status || "Idle"}</span>
					{isAuthenticated && (
						<StatusUsagePills
							billingStatusItems={billingStatusItems}
							statusUsageItems={statusUsageItems}
							isUsageLoading={isUsageLoading}
							isBillingLoading={isBillingLoading}
						/>
					)}
				</div>
				<button
					type="button"
					className="settings-open-btn"
					data-testid="settings-open-button"
					onClick={onOpenSettings}
					aria-label="Open settings"
				>
					<span aria-hidden="true">⚙</span>
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
		</header>
	);
}
