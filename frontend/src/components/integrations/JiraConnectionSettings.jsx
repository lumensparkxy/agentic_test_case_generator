export default function JiraConnectionSettings({
	jiraConnected,
	jiraConnection,
	jiraConnectionForm,
	setJiraConnectionForm,
	saveJiraConnection,
	authActionDisabled,
	isSavingJiraConnection,
	isJiraConnectionLoading,
	refreshJiraConnectionStatus,
	currentUser,
	deleteStoredJiraConnection,
	isDeletingJiraConnection,
}) {
	return (
		<div className="jira-card settings-integration-card">
			<div className="jira-card-header">
				<div>
					<h3>JIRA Cloud</h3>
					<p>Store a per-user JIRA Cloud connection so imports and managed requirement sync can use it later.</p>
				</div>
				{jiraConnected ? <span className="jira-status-badge connected">Connected</span> : <span className="jira-status-badge">Not connected</span>}
			</div>
			{jiraConnected && jiraConnection ? (
				<div className="jira-connection-summary">
					<span className="jira-summary-pill">{jiraConnection.display_name || jiraConnection.email}</span>
					<span className="jira-summary-pill">{jiraConnection.base_url}</span>
					{jiraConnection.api_token_hint && <span className="jira-summary-pill">Token {jiraConnection.api_token_hint}</span>}
				</div>
			) : null}
			{!jiraConnected ? (
				<div className="panel-form two-cols jira-connection-form">
					<div className="form-group">
						<label>JIRA base URL</label>
						<input
							placeholder="https://your-team.atlassian.net"
							value={jiraConnectionForm.baseUrl}
							onChange={(event) => setJiraConnectionForm((prev) => ({ ...prev, baseUrl: event.target.value }))}
						/>
					</div>
					<div className="form-group">
						<label>JIRA email</label>
						<input
							type="email"
							placeholder="qa@company.com"
							value={jiraConnectionForm.email}
							onChange={(event) => setJiraConnectionForm((prev) => ({ ...prev, email: event.target.value }))}
						/>
					</div>
					<div className="form-group jira-connection-token-group">
						<label>JIRA API token</label>
						<input
							type="password"
							placeholder="Paste your Atlassian API token"
							value={jiraConnectionForm.apiToken}
							onChange={(event) => setJiraConnectionForm((prev) => ({ ...prev, apiToken: event.target.value }))}
						/>
					</div>
					<div className="panel-form button-row jira-connection-actions">
						<button onClick={saveJiraConnection} disabled={authActionDisabled || isSavingJiraConnection || isJiraConnectionLoading}>
							{isSavingJiraConnection ? "⏳ Connecting..." : "Connect JIRA"}
						</button>
						{isJiraConnectionLoading && <span className="helper-text">Refreshing JIRA connection…</span>}
					</div>
				</div>
			) : (
				<div className="jira-connected-actions">
					<button className="secondary" onClick={() => refreshJiraConnectionStatus(currentUser)} disabled={authActionDisabled || isJiraConnectionLoading}>
						{isJiraConnectionLoading ? "⏳ Refreshing status..." : "Refresh Status"}
					</button>
					<button className="secondary" onClick={deleteStoredJiraConnection} disabled={authActionDisabled || isDeletingJiraConnection}>
						{isDeletingJiraConnection ? "⏳ Disconnecting..." : "Disconnect"}
					</button>
				</div>
			)}
		</div>
	);
}
