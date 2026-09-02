import { useEffect, useState } from "react";

import { STORAGE_WORKFLOW_NAV_COLLAPSED } from "../constants/workflow";

const COMPACT_WORKFLOW_NAV_QUERY = "(max-width: 900px)";

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

const readCompactWorkflowNavigation = () => {
	try {
		return window.matchMedia(COMPACT_WORKFLOW_NAV_QUERY).matches;
	} catch {
		return false;
	}
};

export default function useWorkflowShellLayoutState() {
	const [isDesktopWorkflowNavCollapsed, setIsDesktopWorkflowNavCollapsed] = useState(() =>
		readStoredBoolean(STORAGE_WORKFLOW_NAV_COLLAPSED)
	);
	const [isCompactWorkflowNavigation, setIsCompactWorkflowNavigation] = useState(readCompactWorkflowNavigation);
	const [isCompactWorkflowNavigationOpen, setIsCompactWorkflowNavigationOpen] = useState(false);

	useEffect(() => {
		writeStoredBoolean(STORAGE_WORKFLOW_NAV_COLLAPSED, isDesktopWorkflowNavCollapsed);
	}, [isDesktopWorkflowNavCollapsed]);

	useEffect(() => {
		const mediaQuery = window.matchMedia(COMPACT_WORKFLOW_NAV_QUERY);
		const handleChange = (event) => {
			setIsCompactWorkflowNavigation(event.matches);
			if (event.matches) setIsCompactWorkflowNavigationOpen(false);
		};
		mediaQuery.addEventListener("change", handleChange);
		return () => mediaQuery.removeEventListener("change", handleChange);
	}, []);

	const isWorkflowNavCollapsed = isCompactWorkflowNavigation ? !isCompactWorkflowNavigationOpen : isDesktopWorkflowNavCollapsed;

	return {
		isWorkflowNavCollapsed,
		isCompactWorkflowNavigation,
		toggleWorkflowNavCollapsed: () => {
			if (isCompactWorkflowNavigation) {
				setIsCompactWorkflowNavigationOpen((value) => !value);
				return;
			}
			setIsDesktopWorkflowNavCollapsed((value) => !value);
		},
		closeCompactWorkflowNavigation: () => setIsCompactWorkflowNavigationOpen(false),
	};
}
