import { X } from "lucide-react";
import { deferWorkspace } from "../../app/deferWorkspace";
import { Dialog } from "../ui/dialog";
import { IconButton } from "../ui/controls";
import { TabList, Tab, TabPanel } from "../ui/tabs";

const WorkflowTuningSettings = deferWorkspace(() => import("./WorkflowTuningSettings"), "workflow tuning");
const IntegrationSettings = deferWorkspace(() => import("./IntegrationSettings"), "integration settings");
const KnowledgePanel = deferWorkspace(() => import("../knowledge/KnowledgePanel"), "personal knowledge");

export default function SettingsDialog({
	request,
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
					<Tab
						id="settings-knowledge-tab"
						selected={settingsSection === "knowledge"}
						aria-controls="settings-section-panel"
						onClick={() => setSettingsSection("knowledge")}
					>
						Personal knowledge
					</Tab>
				</TabList>
				<TabPanel id="settings-section-panel" aria-labelledby={`settings-${settingsSection}-tab`} className="settings-dialog-body">
					{settingsSection === "workflow" ? (
						<WorkflowTuningSettings
							requirementWorkflowSettings={requirementWorkflowSettings}
							setRequirementWorkflowSettings={setRequirementWorkflowSettings}
							testCaseWorkflowSettings={testCaseWorkflowSettings}
							setTestCaseWorkflowSettings={setTestCaseWorkflowSettings}
						/>
					) : settingsSection === "knowledge" ? (
						isAuthenticated ? (
							<KnowledgePanel request={request} personal />
						) : (
							<p>Sign in to manage personal knowledge.</p>
						)
					) : (
						<IntegrationSettings isAuthenticated={isAuthenticated} jiraSettings={jiraSettings} azureDevOpsSettings={azureDevOpsSettings} />
					)}
				</TabPanel>
			</Dialog>
		</div>
	);
}
