import {
	BookOpen,
	Bot,
	ClipboardCheck,
	CloudUpload,
	Download,
	LayoutGrid,
	PanelLeftClose,
	PanelLeftOpen,
	WandSparkles,
} from "lucide-react";
import { useRef } from "react";

import StatusBadge from "../workflow/StatusBadge";

const STATE_LABELS = {
	active: "Current",
	complete: "Complete",
	blocked: "Blocked",
	pending: "Pending",
	attention: "Needs attention",
};

const WORKFLOW_ICONS = {
	0: CloudUpload,
	1: BookOpen,
	2: LayoutGrid,
	3: WandSparkles,
	4: Bot,
	5: Download,
	6: ClipboardCheck,
};

export default function WorkflowNavigationDrawer({
	tabs,
	activeTab,
	onTabChange,
	statusByTabId = {},
	isCollapsed = false,
	isCompact = false,
	onToggleCollapsed,
	onRequestClose,
	controls = null,
}) {
	const toggleLabel = isCompact
		? isCollapsed
			? "Open project navigation"
			: "Close project navigation"
		: isCollapsed
			? "Expand project navigation"
			: "Collapse project navigation";
	const ToggleIcon = isCollapsed ? PanelLeftOpen : PanelLeftClose;
	const toggleRef = useRef(null);
	const navigationItemsId = "project-workflow-navigation-items";
	const itemsHidden = isCompact && isCollapsed;

	const closeCompactNavigation = ({ restoreFocus = false } = {}) => {
		if (!isCompact || isCollapsed) return;
		onRequestClose?.();
		if (restoreFocus) window.requestAnimationFrame(() => toggleRef.current?.focus());
	};

	const handleSelection = (tabId) => {
		onTabChange(tabId);
		closeCompactNavigation();
	};

	return (
		<nav
			className={`workflow-navigation-drawer ${isCollapsed ? "collapsed" : ""} ${isCompact ? "compact" : ""}`.trim()}
			aria-label="Project navigation"
			onKeyDown={(event) => {
				if (event.key !== "Escape" || !isCompact || isCollapsed) return;
				event.preventDefault();
				closeCompactNavigation({ restoreFocus: true });
			}}
		>
			<div className="workflow-navigation-header">
				<div>
					<span>Workflow</span>
					<strong>{tabs.find((tab) => tab.id === activeTab)?.label || "Workspace"}</strong>
				</div>
				<button
					ref={toggleRef}
					type="button"
					className="workflow-navigation-toggle"
					onClick={onToggleCollapsed}
					aria-label={toggleLabel}
					title={toggleLabel}
					aria-expanded={isCompact ? !isCollapsed : undefined}
					aria-controls={isCompact ? navigationItemsId : undefined}
				>
					<ToggleIcon aria-hidden="true" size={18} strokeWidth={2.1} />
				</button>
			</div>
			<div id={navigationItemsId} className="workflow-navigation-list" hidden={itemsHidden}>
				{tabs.map((tab) => {
					const isActive = activeTab === tab.id;
					const state = statusByTabId[tab.id] || "pending";
					const stateLabel = isActive ? STATE_LABELS.active : STATE_LABELS[state] || STATE_LABELS.pending;
					const WorkflowIcon = WORKFLOW_ICONS[tab.id] || LayoutGrid;
					const itemContent = (
						<>
							<span className="workflow-navigation-marker" aria-hidden="true">
								<WorkflowIcon size={18} strokeWidth={2.15} />
							</span>
							<span className="workflow-navigation-copy">
								<strong>{tab.label}</strong>
								<span>{tab.title}</span>
							</span>
							{!isCollapsed && (
								<StatusBadge className="workflow-navigation-state" status={isActive ? "active" : state} label={stateLabel} />
							)}
						</>
					);
					const itemClassName = `workflow-navigation-item ${state} ${isActive ? "active" : ""}`;

					if (tab.href) {
						return (
							<a
								key={tab.id}
								href={tab.href}
								className={itemClassName}
								onClick={(event) => {
									if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
										return;
									}
									event.preventDefault();
									handleSelection(tab.id);
								}}
								aria-current={isActive ? "page" : undefined}
								aria-label={`${tab.label}, ${stateLabel}`}
								title={isCollapsed ? [tab.label, stateLabel].join(" — ") : undefined}
							>
								{itemContent}
							</a>
						);
					}

					return (
						<button
							type="button"
							key={tab.id}
							className={itemClassName}
							onClick={() => handleSelection(tab.id)}
							aria-current={isActive ? "page" : undefined}
							aria-label={`${tab.label}, ${stateLabel}`}
							title={isCollapsed ? [tab.label, stateLabel].join(" — ") : undefined}
						>
							{itemContent}
						</button>
					);
				})}
			</div>
			{controls && (
				<div className="workflow-navigation-controls" hidden={itemsHidden}>
					{controls}
				</div>
			)}
		</nav>
	);
}
