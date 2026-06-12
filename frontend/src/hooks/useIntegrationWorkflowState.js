import { useState } from "react";

import {
	DEFAULT_AZURE_DEVOPS_SYNC_SECTION_TITLE,
	DEFAULT_JIRA_SYNC_SECTION_TITLE,
	EMPTY_AZURE_DEVOPS_CONNECTION_FORM,
	EMPTY_AZURE_DEVOPS_CONNECTION_STATUS,
	EMPTY_JIRA_CONNECTION_FORM,
	EMPTY_JIRA_CONNECTION_STATUS,
} from "../constants/workflow";

export default function useIntegrationWorkflowState() {
	const [jiraConnectionStatus, setJiraConnectionStatus] = useState(EMPTY_JIRA_CONNECTION_STATUS);
	const [jiraConnectionForm, setJiraConnectionForm] = useState(EMPTY_JIRA_CONNECTION_FORM);
	const [isJiraConnectionLoading, setIsJiraConnectionLoading] = useState(false);
	const [isSavingJiraConnection, setIsSavingJiraConnection] = useState(false);
	const [isDeletingJiraConnection, setIsDeletingJiraConnection] = useState(false);
	const [jiraProjectQuery, setJiraProjectQuery] = useState("");
	const [jiraProjects, setJiraProjects] = useState([]);
	const [selectedJiraProjectKey, setSelectedJiraProjectKey] = useState("");
	const [isLoadingJiraProjects, setIsLoadingJiraProjects] = useState(false);
	const [jiraProjectIssueTypes, setJiraProjectIssueTypes] = useState([]);
	const [isLoadingJiraIssueTypes, setIsLoadingJiraIssueTypes] = useState(false);
	const [jiraIssueType, setJiraIssueType] = useState("");
	const [jiraIssueQuery, setJiraIssueQuery] = useState("");
	const [jiraIssueResults, setJiraIssueResults] = useState([]);
	const [selectedJiraIssueKey, setSelectedJiraIssueKey] = useState("");
	const [isSearchingJiraIssues, setIsSearchingJiraIssues] = useState(false);
	const [isImportingFromJira, setIsImportingFromJira] = useState(false);
	const [jiraSyncPreview, setJiraSyncPreview] = useState(null);
	const [jiraSyncResults, setJiraSyncResults] = useState(null);
	const [jiraManagedSectionTitle, setJiraManagedSectionTitle] = useState(DEFAULT_JIRA_SYNC_SECTION_TITLE);
	const [isPreviewingJiraSync, setIsPreviewingJiraSync] = useState(false);
	const [isApplyingJiraSync, setIsApplyingJiraSync] = useState(false);
	const [azureDevOpsConnectionStatus, setAzureDevOpsConnectionStatus] = useState(EMPTY_AZURE_DEVOPS_CONNECTION_STATUS);
	const [azureDevOpsConnectionForm, setAzureDevOpsConnectionForm] = useState(EMPTY_AZURE_DEVOPS_CONNECTION_FORM);
	const [isAzureDevOpsConnectionLoading, setIsAzureDevOpsConnectionLoading] = useState(false);
	const [isSavingAzureDevOpsConnection, setIsSavingAzureDevOpsConnection] = useState(false);
	const [isDeletingAzureDevOpsConnection, setIsDeletingAzureDevOpsConnection] = useState(false);
	const [azureDevOpsProjectQuery, setAzureDevOpsProjectQuery] = useState("");
	const [azureDevOpsProjects, setAzureDevOpsProjects] = useState([]);
	const [selectedAzureDevOpsProject, setSelectedAzureDevOpsProject] = useState("");
	const [isLoadingAzureDevOpsProjects, setIsLoadingAzureDevOpsProjects] = useState(false);
	const [azureDevOpsWorkItemTypes, setAzureDevOpsWorkItemTypes] = useState([]);
	const [isLoadingAzureDevOpsWorkItemTypes, setIsLoadingAzureDevOpsWorkItemTypes] = useState(false);
	const [azureDevOpsWorkItemType, setAzureDevOpsWorkItemType] = useState("");
	const [azureDevOpsWorkItemQuery, setAzureDevOpsWorkItemQuery] = useState("");
	const [azureDevOpsWorkItemResults, setAzureDevOpsWorkItemResults] = useState([]);
	const [selectedAzureDevOpsWorkItemId, setSelectedAzureDevOpsWorkItemId] = useState("");
	const [isSearchingAzureDevOpsWorkItems, setIsSearchingAzureDevOpsWorkItems] = useState(false);
	const [isImportingFromAzureDevOps, setIsImportingFromAzureDevOps] = useState(false);
	const [azureDevOpsSyncPreview, setAzureDevOpsSyncPreview] = useState(null);
	const [azureDevOpsSyncResults, setAzureDevOpsSyncResults] = useState(null);
	const [azureDevOpsManagedSectionTitle, setAzureDevOpsManagedSectionTitle] = useState(DEFAULT_AZURE_DEVOPS_SYNC_SECTION_TITLE);
	const [isPreviewingAzureDevOpsSync, setIsPreviewingAzureDevOpsSync] = useState(false);
	const [isApplyingAzureDevOpsSync, setIsApplyingAzureDevOpsSync] = useState(false);

	const resetJiraSyncState = () => {
		setJiraSyncPreview(null);
		setJiraSyncResults(null);
	};

	const resetAzureDevOpsSyncState = () => {
		setAzureDevOpsSyncPreview(null);
		setAzureDevOpsSyncResults(null);
	};

	const resetIntegrationSyncState = () => {
		resetJiraSyncState();
		resetAzureDevOpsSyncState();
	};

	return {
		jiraConnectionStatus,
		setJiraConnectionStatus,
		jiraConnectionForm,
		setJiraConnectionForm,
		isJiraConnectionLoading,
		setIsJiraConnectionLoading,
		isSavingJiraConnection,
		setIsSavingJiraConnection,
		isDeletingJiraConnection,
		setIsDeletingJiraConnection,
		jiraProjectQuery,
		setJiraProjectQuery,
		jiraProjects,
		setJiraProjects,
		selectedJiraProjectKey,
		setSelectedJiraProjectKey,
		isLoadingJiraProjects,
		setIsLoadingJiraProjects,
		jiraProjectIssueTypes,
		setJiraProjectIssueTypes,
		isLoadingJiraIssueTypes,
		setIsLoadingJiraIssueTypes,
		jiraIssueType,
		setJiraIssueType,
		jiraIssueQuery,
		setJiraIssueQuery,
		jiraIssueResults,
		setJiraIssueResults,
		selectedJiraIssueKey,
		setSelectedJiraIssueKey,
		isSearchingJiraIssues,
		setIsSearchingJiraIssues,
		isImportingFromJira,
		setIsImportingFromJira,
		jiraSyncPreview,
		setJiraSyncPreview,
		jiraSyncResults,
		setJiraSyncResults,
		jiraManagedSectionTitle,
		setJiraManagedSectionTitle,
		isPreviewingJiraSync,
		setIsPreviewingJiraSync,
		isApplyingJiraSync,
		setIsApplyingJiraSync,
		azureDevOpsConnectionStatus,
		setAzureDevOpsConnectionStatus,
		azureDevOpsConnectionForm,
		setAzureDevOpsConnectionForm,
		isAzureDevOpsConnectionLoading,
		setIsAzureDevOpsConnectionLoading,
		isSavingAzureDevOpsConnection,
		setIsSavingAzureDevOpsConnection,
		isDeletingAzureDevOpsConnection,
		setIsDeletingAzureDevOpsConnection,
		azureDevOpsProjectQuery,
		setAzureDevOpsProjectQuery,
		azureDevOpsProjects,
		setAzureDevOpsProjects,
		selectedAzureDevOpsProject,
		setSelectedAzureDevOpsProject,
		isLoadingAzureDevOpsProjects,
		setIsLoadingAzureDevOpsProjects,
		azureDevOpsWorkItemTypes,
		setAzureDevOpsWorkItemTypes,
		isLoadingAzureDevOpsWorkItemTypes,
		setIsLoadingAzureDevOpsWorkItemTypes,
		azureDevOpsWorkItemType,
		setAzureDevOpsWorkItemType,
		azureDevOpsWorkItemQuery,
		setAzureDevOpsWorkItemQuery,
		azureDevOpsWorkItemResults,
		setAzureDevOpsWorkItemResults,
		selectedAzureDevOpsWorkItemId,
		setSelectedAzureDevOpsWorkItemId,
		isSearchingAzureDevOpsWorkItems,
		setIsSearchingAzureDevOpsWorkItems,
		isImportingFromAzureDevOps,
		setIsImportingFromAzureDevOps,
		azureDevOpsSyncPreview,
		setAzureDevOpsSyncPreview,
		azureDevOpsSyncResults,
		setAzureDevOpsSyncResults,
		azureDevOpsManagedSectionTitle,
		setAzureDevOpsManagedSectionTitle,
		isPreviewingAzureDevOpsSync,
		setIsPreviewingAzureDevOpsSync,
		isApplyingAzureDevOpsSync,
		setIsApplyingAzureDevOpsSync,
		resetJiraSyncState,
		resetAzureDevOpsSyncState,
		resetIntegrationSyncState,
	};
}
