import AzureDevOpsConnectionSettings from "../integrations/AzureDevOpsConnectionSettings";
import JiraConnectionSettings from "../integrations/JiraConnectionSettings";

export default function IntegrationSettings({ isAuthenticated, jiraSettings, azureDevOpsSettings }) {
	return (
		<>
			<div className="settings-section-intro">
				<h3>Integration connections</h3>
				<p>Set these up once per user. Import/search/sync actions stay in the Upload workflow where they are used.</p>
			</div>
			{!isAuthenticated && <div className="settings-auth-note">🔐 Sign in to create or manage JIRA and Azure DevOps connections.</div>}
			<div className="settings-integration-grid">
				<JiraConnectionSettings {...jiraSettings} />
				<AzureDevOpsConnectionSettings {...azureDevOpsSettings} />
			</div>
		</>
	);
}
