import { useEffect, useState } from "react";

import { STORAGE_PROJECT_RAIL_COLLAPSED, STORAGE_WORKFLOW_NAV_COLLAPSED } from "../constants/workflow";

const readStoredBoolean = (key) => {
	try {
		return window.localStorage.getItem(key) === "true";
	} catch {
		return false;
	}
};

const writeStoredBoolean = (key, value) => {
	try {
		window.localStorage.setItem(key, value ? "true" : "false");
	} catch {
		// Layout preferences are optional; ignore storage failures.
	}
};

export default function useWorkflowShellLayoutState() {
	const [isWorkflowNavCollapsed, setIsWorkflowNavCollapsed] = useState(() => readStoredBoolean(STORAGE_WORKFLOW_NAV_COLLAPSED));
	const [isProjectRailCollapsed, setIsProjectRailCollapsed] = useState(() => readStoredBoolean(STORAGE_PROJECT_RAIL_COLLAPSED));

	useEffect(() => {
		writeStoredBoolean(STORAGE_WORKFLOW_NAV_COLLAPSED, isWorkflowNavCollapsed);
	}, [isWorkflowNavCollapsed]);

	useEffect(() => {
		writeStoredBoolean(STORAGE_PROJECT_RAIL_COLLAPSED, isProjectRailCollapsed);
	}, [isProjectRailCollapsed]);

	return {
		isWorkflowNavCollapsed,
		isProjectRailCollapsed,
		toggleWorkflowNavCollapsed: () => setIsWorkflowNavCollapsed((value) => !value),
		toggleProjectRailCollapsed: () => setIsProjectRailCollapsed((value) => !value),
	};
}
