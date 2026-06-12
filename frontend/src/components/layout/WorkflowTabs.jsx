export default function WorkflowTabs({ tabs, activeTab, onTabChange }) {
	return (
		<div className="tabs">
			{tabs.map((tab) => (
				<button key={tab.id} className={`tab ${activeTab === tab.id ? "active" : ""}`} onClick={() => onTabChange(tab.id)}>
					<span className="tab-number">{tab.id + 1}</span>
					<span className="tab-label">{tab.label}</span>
				</button>
			))}
		</div>
	);
}
