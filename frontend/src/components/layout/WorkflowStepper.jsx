export default function WorkflowStepper({ tabs, activeTab, onTabChange }) {
	return (
		<nav className="tabs workflow-stepper" aria-label="Workflow steps">
			{tabs.map((tab) => {
				const isActive = activeTab === tab.id;
				const isComplete = activeTab > tab.id;
				return (
					<button
						key={tab.id}
						className={`tab workflow-step ${isActive ? "active" : ""} ${isComplete ? "complete" : ""}`}
						onClick={() => onTabChange(tab.id)}
						aria-current={isActive ? "step" : undefined}
					>
						<span className="tab-number">{isComplete ? "✓" : tab.id + 1}</span>
						<span className="tab-label">{tab.label}</span>
					</button>
				);
			})}
		</nav>
	);
}
