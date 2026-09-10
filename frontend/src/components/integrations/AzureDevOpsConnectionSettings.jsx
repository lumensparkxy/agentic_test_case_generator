import { Field, Input, Button } from "../ui/controls";
export default function AzureDevOpsConnectionSettings({
	azureDevOpsConnected,
	azureDevOpsConnection,
	azureDevOpsConnectionForm,
	setAzureDevOpsConnectionForm,
	saveAzureDevOpsConnection,
	authActionDisabled,
	isSavingAzureDevOpsConnection,
	isAzureDevOpsConnectionLoading,
	refreshAzureDevOpsConnectionStatus,
	currentUser,
	deleteStoredAzureDevOpsConnection,
	isDeletingAzureDevOpsConnection,
}) {
	return (
		<div className="jira-card settings-integration-card">
			<div className="jira-card-header">
				<div>
					<h3>Azure DevOps</h3>
					<p>Store a per-user Azure DevOps connection so imports and managed requirement sync can use it later.</p>
				</div>
				{azureDevOpsConnected ? (
					<span className="jira-status-badge connected">Connected</span>
				) : (
					<span className="jira-status-badge">Not connected</span>
				)}
			</div>
			{azureDevOpsConnected && azureDevOpsConnection ? (
				<div className="jira-connection-summary">
					<span className="jira-summary-pill">{azureDevOpsConnection.display_name || azureDevOpsConnection.organization}</span>
					<span className="jira-summary-pill">{azureDevOpsConnection.organization_url}</span>
					{azureDevOpsConnection.default_project && (
						<span className="jira-summary-pill">Default project {azureDevOpsConnection.default_project}</span>
					)}
					{azureDevOpsConnection.token_hint && <span className="jira-summary-pill">PAT {azureDevOpsConnection.token_hint}</span>}
				</div>
			) : null}
			{!azureDevOpsConnected ? (
				<div className="panel-form two-cols jira-connection-form">
					<Field className="form-group">
						<label>Azure DevOps organization or project URL</label>
						<Input
							placeholder="https://dev.azure.com/{organization}/{project}"
							value={azureDevOpsConnectionForm.organizationUrl}
							onChange={(event) => setAzureDevOpsConnectionForm((prev) => ({ ...prev, organizationUrl: event.target.value }))}
						/>
					</Field>
					<Field className="form-group">
						<label>Account email (optional)</label>
						<Input
							type="email"
							placeholder="you@company.com or personal@example.com"
							value={azureDevOpsConnectionForm.accountEmail}
							onChange={(event) => setAzureDevOpsConnectionForm((prev) => ({ ...prev, accountEmail: event.target.value }))}
						/>
					</Field>
					<div className="form-group jira-connection-token-group">
						<label>Azure DevOps PAT</label>
						<Input
							type="password"
							placeholder="Paste your Azure DevOps Personal Access Token"
							value={azureDevOpsConnectionForm.personalAccessToken}
							onChange={(event) => setAzureDevOpsConnectionForm((prev) => ({ ...prev, personalAccessToken: event.target.value }))}
						/>
						<span className="helper-text">
							Use a minimal PAT with Project/team read and Work Items read/write scopes. Microsoft app sign-in is separate from Azure DevOps
							API access.
						</span>
					</div>
					<div className="panel-form button-row jira-connection-actions">
						<Button
							onClick={saveAzureDevOpsConnection}
							disabled={authActionDisabled || isSavingAzureDevOpsConnection || isAzureDevOpsConnectionLoading}
						>
							{isSavingAzureDevOpsConnection ? "⏳ Connecting..." : "Connect Azure DevOps"}
						</Button>
						{isAzureDevOpsConnectionLoading && <span className="helper-text">Refreshing Azure DevOps connection…</span>}
					</div>
				</div>
			) : (
				<div className="jira-connected-actions">
					<Button
						className="secondary"
						onClick={() => refreshAzureDevOpsConnectionStatus(currentUser)}
						disabled={authActionDisabled || isAzureDevOpsConnectionLoading}
					>
						{isAzureDevOpsConnectionLoading ? "⏳ Refreshing status..." : "Refresh Status"}
					</Button>
					<Button
						className="secondary"
						onClick={deleteStoredAzureDevOpsConnection}
						disabled={authActionDisabled || isDeletingAzureDevOpsConnection}
					>
						{isDeletingAzureDevOpsConnection ? "⏳ Disconnecting..." : "Disconnect"}
					</Button>
				</div>
			)}
		</div>
	);
}
