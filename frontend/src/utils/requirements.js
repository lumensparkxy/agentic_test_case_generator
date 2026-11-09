import { JIRA_SOURCE_FIELDS, REQUIREMENT_REVIEW_STATUSES } from "../constants/workflow";

export const buildJiraConnectionForm = (connection, user) => ({
	baseUrl: connection?.base_url || "",
	email: connection?.email || user?.email || "",
	apiToken: "",
});

export const buildAzureDevOpsConnectionForm = (connection, user) => ({
	organizationUrl: connection?.organization_url || "",
	accountEmail: connection?.account_email || user?.email || "",
	personalAccessToken: "",
});

export const isJiraLinkedRequirement = (requirement) =>
	Boolean(
		requirement?.source_system === "jira" ||
		(!requirement?.source_system && (requirement?.source_issue_key || requirement?.sync_target_issue_key || requirement?.artifact_item_id))
	);

export const isAzureDevOpsLinkedRequirement = (requirement) => Boolean(requirement?.source_system === "azure_devops");

export const getRequirementSourceLabel = (requirement) => {
	if (requirement?.source_system === "azure_devops") {
		return "Azure DevOps";
	}
	if (requirement?.source_system === "file") {
		return "File";
	}
	if (requirement?.source_system === "jira" || requirement?.source_issue_key || requirement?.sync_target_issue_key) {
		return "JIRA";
	}
	return "Source";
};

export const normalizeStringArray = (value) => {
	if (Array.isArray(value)) {
		return [...new Set(value.map((item) => `${item || ""}`.trim()).filter(Boolean))];
	}
	const normalized = `${value || ""}`.trim();
	return normalized ? [normalized] : [];
};

export const getRequirementReviewStatus = (requirement) => {
	const status = `${requirement?.review_status || "Draft"}`.trim();
	return REQUIREMENT_REVIEW_STATUSES.includes(status) ? status : "Draft";
};

export const getRequirementContextPath = (requirement) => {
	const hierarchy = normalizeStringArray(requirement?.source_hierarchy);
	if (hierarchy.length) {
		return hierarchy.join(" › ");
	}
	if (requirement?.source_path) {
		return requirement.source_path;
	}
	const sourceKey = requirement?.source_issue_key || requirement?.sync_target_issue_key;
	if (sourceKey) {
		return [requirement?.source_parent_key, sourceKey, requirement?.source_section].filter(Boolean).join(" › ");
	}
	return "Imported requirements";
};

export const formatSourceIssueKey = (requirement, key) => {
	const normalized = `${key || ""}`.trim();
	if (!normalized) {
		return "";
	}
	if (requirement?.source_system === "azure_devops" && !normalized.startsWith("#")) {
		return `#${normalized}`;
	}
	return normalized;
};

export const getContextTitle = (contextPath = "") => {
	const segments = `${contextPath || ""}`
		.split("›")
		.map((segment) => segment.trim())
		.filter(Boolean);
	return segments.length ? segments[segments.length - 1] : "";
};

export const getRequirementEpicCell = (requirement, contextPath = "") => {
	const issueType = `${requirement?.source_issue_type || ""}`.trim();
	const issueKey = formatSourceIssueKey(requirement, requirement?.source_issue_key || requirement?.sync_target_issue_key);
	const parentKey = formatSourceIssueKey(requirement, requirement?.source_parent_key);
	const parentTitle = `${requirement?.source_parent_title || ""}`.trim();
	const contextTitle = getContextTitle(contextPath);
	const isEpic = /epic/i.test(issueType);

	if (parentTitle || parentKey) {
		return {
			primary: parentTitle || parentKey,
			secondary: parentTitle && parentKey ? parentKey : "",
		};
	}

	if (isEpic) {
		return {
			primary: requirement?.source_section || contextTitle || issueKey || "—",
			secondary: issueKey || issueType,
		};
	}

	if (contextTitle && contextTitle !== "Imported requirements") {
		return { primary: contextTitle, secondary: "" };
	}

	return { primary: "—", secondary: "" };
};

export const getRequirementIssueCell = (requirement) => {
	const issueKey = formatSourceIssueKey(
		requirement,
		requirement?.source_issue_key || requirement?.sync_target_issue_key || requirement?.artifact_item_id
	);
	const issueType = `${requirement?.source_issue_type || ""}`.trim();
	return {
		primary: issueKey || "—",
		secondary: issueType || "",
	};
};

export const groupRequirementsByContext = (items = []) => {
	const groups = new Map();
	items.forEach((requirement, index) => {
		const contextPath = getRequirementContextPath(requirement);
		if (!groups.has(contextPath)) {
			groups.set(contextPath, {
				id: contextPath || `group-${groups.size + 1}`,
				label: contextPath || "Imported requirements",
				sourceLabel: getRequirementSourceLabel(requirement),
				requirements: [],
			});
		}
		groups.get(contextPath).requirements.push({ ...requirement, __index: index });
	});
	return [...groups.values()];
};

export const getTestCaseLinkedRequirementIds = (testCase) => {
	const explicit = normalizeStringArray(testCase?.linked_requirement_ids);
	const tagLinks = normalizeStringArray(testCase?.tags).filter((tag) => /^REQ-[A-Za-z0-9_-]+$/i.test(tag));
	return [...new Set([...explicit, ...tagLinks])];
};

export const buildRequirementTraceability = (approvedRequirements, testCases, scenarioSummaries) => {
	const byRequirement = new Map(approvedRequirements.map(({ id }) => [id, { cases: [], scenarios: new Set() }]));
	let linkedCaseCount = 0;
	for (const testCase of testCases) {
		const links = getTestCaseLinkedRequirementIds(testCase).filter((id) => byRequirement.has(id));
		if (!links.length) continue;
		linkedCaseCount += 1;
		const scenarios = normalizeStringArray(testCase.tags)
			.filter((tag) => tag.startsWith("scenario:"))
			.map((tag) => tag.replace("scenario:", "").replace(/-/g, " "));
		for (const id of links) {
			const row = byRequirement.get(id);
			row.cases.push(testCase);
			for (const scenario of scenarios) row.scenarios.add(scenario);
		}
	}
	const rows = approvedRequirements.map((requirement) => {
		const { cases, scenarios } = byRequirement.get(requirement.id);
		return {
			requirement,
			linkedTestCases: cases,
			linkedScenarioTypes: [...scenarios],
			scenarioSummary: scenarioSummaries?.[requirement.id] || null,
		};
	});
	return { rows, linkedCaseCount, tracedRequirementCount: rows.filter((row) => row.linkedTestCases.length > 0).length };
};

export const mergeRequirementMetadata = (nextRequirements = [], previousRequirements = []) => {
	const previousList = Array.isArray(previousRequirements) ? previousRequirements : [];
	const nextList = Array.isArray(nextRequirements) ? nextRequirements : [];
	if (!previousList.length || !nextList.length) {
		return nextList;
	}

	const previousByArtifactId = new Map();
	const previousById = new Map();
	const resolvedTargets = previousList
		.map((requirement) => requirement?.sync_target_issue_key || requirement?.source_issue_key || "")
		.filter(Boolean);
	const uniqueResolvedTargets = [...new Set(resolvedTargets)];
	const defaultSyncTargetKey = uniqueResolvedTargets.length === 1 ? uniqueResolvedTargets[0] : null;

	previousList.forEach((requirement) => {
		if (requirement?.artifact_item_id) {
			previousByArtifactId.set(requirement.artifact_item_id, requirement);
		}
		if (requirement?.id) {
			previousById.set(requirement.id, requirement);
		}
	});

	return nextList.map((requirement, index) => {
		const matchedRequirement =
			(requirement?.artifact_item_id && previousByArtifactId.get(requirement.artifact_item_id)) ||
			previousById.get(requirement?.id) ||
			(nextList.length === previousList.length ? previousList[index] : null);

		const metadata = JIRA_SOURCE_FIELDS.reduce((acc, field) => {
			if (
				(requirement?.[field] == null || requirement?.[field] === "") &&
				matchedRequirement?.[field] != null &&
				matchedRequirement?.[field] !== ""
			) {
				acc[field] = matchedRequirement[field];
			}
			return acc;
		}, {});

		if (!requirement?.sync_target_issue_key && !metadata.sync_target_issue_key && defaultSyncTargetKey && !matchedRequirement) {
			metadata.sync_target_issue_key = defaultSyncTargetKey;
		}

		return Object.keys(metadata).length ? { ...requirement, ...metadata } : requirement;
	});
};
