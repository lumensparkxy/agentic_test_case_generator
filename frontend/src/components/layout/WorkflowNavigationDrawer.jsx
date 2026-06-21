import { BookOpen, Bot, CloudUpload, Download, LayoutGrid, PanelLeftClose, PanelLeftOpen, WandSparkles } from "lucide-react";

const STATE_LABELS = {
	active: "Active",
	complete: "Complete",
	blocked: "Blocked",
	pending: "Pending",
};

const WORKFLOW_ICONS = {
	0: CloudUpload,
	1: BookOpen,
	2: LayoutGrid,
	3: WandSparkles,
	4: Bot,
	5: Download,
};

export default function WorkflowNavigationDrawer({
	tabs,
	activeTab,
	onTabChange,
	statusByTabId = {},
	isCollapsed = false,
	onToggleCollapsed,
	controls = null,
}) {
	const toggleLabel = isCollapsed ? "Expand workflow navigation" : "Collapse workflow navigation";
	const ToggleIcon = isCollapsed ? PanelLeftOpen : PanelLeftClose;

	return (
		<nav className={`workflow-navigation-drawer ${isCollapsed ? "collapsed" : ""}`} aria-label="Workflow navigation">
			<div className="workflow-navigation-header">
				<div>
					<span>Workflow</span>
					<strong>{tabs.find((tab) => tab.id === activeTab)?.label || "Workspace"}</strong>
				</div>
				<button
					type="button"
					className="workflow-navigation-toggle"
					onClick={onToggleCollapsed}
					aria-label={toggleLabel}
					title={toggleLabel}
				>
					<ToggleIcon aria-hidden="true" size={18} strokeWidth={2.1} />
				</button>
			</div>
			<div className="workflow-navigation-list">
				{tabs.map((tab) => {
					const isActive = activeTab === tab.id;
					const state = statusByTabId[tab.id] || "pending";
					const stateLabel = isActive ? STATE_LABELS.active : STATE_LABELS[state] || STATE_LABELS.pending;
					const WorkflowIcon = WORKFLOW_ICONS[tab.id] || LayoutGrid;
					return (
						<button
							type="button"
							key={tab.id}
							className={`workflow-navigation-item ${state} ${isActive ? "active" : ""}`}
							onClick={() => onTabChange(tab.id)}
							aria-current={isActive ? "page" : undefined}
							aria-label={`${tab.label}, ${stateLabel}`}
						>
							<span className="workflow-navigation-marker" aria-hidden="true">
								<WorkflowIcon size={18} strokeWidth={2.15} />
							</span>
							<span className="workflow-navigation-copy">
								<strong>{tab.label}</strong>
								<span>{tab.title}</span>
							</span>
							<span className="workflow-navigation-state">{stateLabel}</span>
						</button>
					);
				})}
			</div>
			{controls && <div className="workflow-navigation-controls">{controls}</div>}
		</nav>
	);
}
