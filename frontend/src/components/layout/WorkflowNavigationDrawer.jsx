const STATE_LABELS = {
	active: "Active",
	complete: "Complete",
	blocked: "Blocked",
	pending: "Pending",
};

export default function WorkflowNavigationDrawer({
	tabs,
	activeTab,
	onTabChange,
	statusByTabId = {},
	isCollapsed = false,
	onToggleCollapsed,
}) {
	const toggleLabel = isCollapsed ? "Expand workflow navigation" : "Collapse workflow navigation";

	return (
		<nav className={`workflow-navigation-drawer ${isCollapsed ? "collapsed" : ""}`} aria-label="Workflow navigation">
			<div className="workflow-navigation-header">
				<div>
					<span>Workflow</span>
					<strong>{tabs.find((tab) => tab.id === activeTab)?.label || "Workspace"}</strong>
				</div>
				<button type="button" className="workflow-navigation-toggle" onClick={onToggleCollapsed} aria-label={toggleLabel}>
					{isCollapsed ? "Expand" : "Collapse"}
				</button>
			</div>
			<div className="workflow-navigation-list">
				{tabs.map((tab) => {
					const isActive = activeTab === tab.id;
					const state = statusByTabId[tab.id] || "pending";
					const stateLabel = isActive ? STATE_LABELS.active : STATE_LABELS[state] || STATE_LABELS.pending;
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
								{tab.id + 1}
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
		</nav>
	);
}
