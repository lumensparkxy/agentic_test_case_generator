import { X } from "lucide-react";
import { Dialog } from "../ui/dialog";
import { IconButton } from "../ui/controls";
import { TabList, Tab, TabPanel } from "../ui/tabs";
import AzureDevOpsConnectionSettings from "../integrations/AzureDevOpsConnectionSettings";
import JiraConnectionSettings from "../integrations/JiraConnectionSettings";
import WorkflowSettingsPanel from "../workflow/WorkflowSettingsPanel";

export default function SettingsDialog({
	isOpen,
	onOverlayClick,
	onClose,
	settingsSection,
	setSettingsSection,
	requirementWorkflowSettings,
	setRequirementWorkflowSettings,
	testCaseWorkflowSettings,
	setTestCaseWorkflowSettings,
	isAuthenticated,
	jiraSettings,
	azureDevOpsSettings,
}) {
	if (!isOpen) {
		return null;
	}

	return (
		<div className="auth-dialog-overlay settings-dialog-overlay" onClick={onOverlayClick}>
			<Dialog
				manageFocus
				onClose={onClose}
				className="settings-dialog"
				role="dialog"
				aria-modal="true"
				aria-labelledby="settings-dialog-title"
				onClick={(event) => event.stopPropagation()}
			>
				<div className="settings-dialog-header">
					<div>
						<h2 id="settings-dialog-title">Settings</h2>
						<p>Manage one-time connections and advanced workflow tuning without crowding the main pipeline.</p>
					</div>
					<IconButton type="button" className="auth-dialog-close" onClick={onClose} aria-label="Close settings dialog">
						<X size={18} aria-hidden="true" />
					</IconButton>
				</div>
				<TabList className="settings-dialog-nav" role="tablist" aria-label="Settings sections">
					<Tab
						type="button"
						id="settings-workflow-tab"
						selected={settingsSection === "workflow"}
						aria-controls="settings-section-panel"
						className={`settings-nav-btn ${settingsSection === "workflow" ? "active" : ""}`}
						onClick={() => setSettingsSection("workflow")}
					>
						Workflow tuning
					</Tab>
					<Tab
						type="button"
						id="settings-integrations-tab"
						selected={settingsSection === "integrations"}
						aria-controls="settings-section-panel"
						className={`settings-nav-btn ${settingsSection === "integrations" ? "active" : ""}`}
						onClick={() => setSettingsSection("integrations")}
					>
						Integrations
					</Tab>
				</TabList>
				<TabPanel id="settings-section-panel" aria-labelledby={`settings-${settingsSection}-tab`} className="settings-dialog-body">
					{settingsSection === "workflow" ? (
						<>
							<div className="settings-section-intro">
								<h3>Advanced workflow tuning</h3>
								<p>Leave fields blank for backend defaults. Adjust these only when you need stricter review gates or shorter AI loops.</p>
							</div>
							<WorkflowSettingsPanel
								title="Requirements workflow settings"
								description="Tune the requirement review loop when you want stricter gates or shorter runs."
								settings={requirementWorkflowSettings}
								setSettings={setRequirementWorkflowSettings}
							/>
							<WorkflowSettingsPanel
								title="Test-case workflow settings"
								description="Control validation strictness, loop length, and timeout behavior for generation and refinement."
								settings={testCaseWorkflowSettings}
								setSettings={setTestCaseWorkflowSettings}
							/>
						</>
					) : (
						<>
							<div className="settings-section-intro">
								<h3>Integration connections</h3>
								<p>Set these up once per user. Import/search/sync actions stay in the Upload workflow where they are used.</p>
							</div>
							{!isAuthenticated && (
								<div className="settings-auth-note">🔐 Sign in to create or manage JIRA and Azure DevOps connections.</div>
							)}
							<div className="settings-integration-grid">
								<JiraConnectionSettings {...jiraSettings} />
								<AzureDevOpsConnectionSettings {...azureDevOpsSettings} />
							</div>
						</>
					)}
				</TabPanel>
			</Dialog>
		</div>
	);
}
