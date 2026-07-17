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
	6: ClipboardCheck,
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
		<nav className={`workflow-navigation-drawer ${isCollapsed ? "collapsed" : ""}`} aria-label="Project navigation">
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
					const itemContent = (
						<>
							<span className="workflow-navigation-marker" aria-hidden="true">
								<WorkflowIcon size={18} strokeWidth={2.15} />
							</span>
							<span className="workflow-navigation-copy">
								<strong>{tab.label}</strong>
								<span>{tab.title}</span>
							</span>
							<span className="workflow-navigation-state">{stateLabel}</span>
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
									onTabChange(tab.id);
								}}
								aria-current={isActive ? "page" : undefined}
								aria-label={`${tab.label}, ${stateLabel}`}
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
							onClick={() => onTabChange(tab.id)}
							aria-current={isActive ? "page" : undefined}
							aria-label={`${tab.label}, ${stateLabel}`}
						>
							{itemContent}
						</button>
					);
				})}
			</div>
			{controls && <div className="workflow-navigation-controls">{controls}</div>}
		</nav>
	);
}
