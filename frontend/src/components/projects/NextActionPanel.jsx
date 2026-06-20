const ACTION_KIND_LABELS = {
	refine: "Refine",
	approve: "Review approvals",
	generate: "Generate",
	analyze_impact: "Analyze Impact",
	apply_update: "Apply Accepted Updates",
	full_regenerate: "Full Regenerate",
	automate: "Preview Automation",
	execute: "Run Execution",
	review: "Review",
	report: "Open Reports",
};

const formatLabel = (value) =>
	`${value || ""}`
		.split(/[_\s-]+/)
		.filter(Boolean)
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(" ");

const normalizeList = (value) => (Array.isArray(value) ? value : []);

function ActionButton({ action, disabled, busy, onAction }) {
	const label = action.label || ACTION_KIND_LABELS[action.action] || formatLabel(action.action);
	const blockedMessage = normalizeList(action.blockers)[0]?.message || "";
	return (
		<div className={`orchestrator-action ${action.primary ? "primary" : action.secondary ? "secondary-action" : ""}`}>
			<button
				type="button"
				className={action.primary ? "" : "secondary"}
				onClick={() => onAction(action)}
				disabled={disabled || busy || !action.enabled}
			>
				{busy ? "Working..." : label}
			</button>
			<p>{action.reason || blockedMessage || (action.enabled ? "Ready to run." : "Blocked.")}</p>
			{blockedMessage && <span className="orchestrator-action-blocker">{blockedMessage}</span>}
			{action.agent_kind && (
				<span className="orchestrator-agent-contract">
					{formatLabel(action.agent_kind)} contract {action.agent_contract_version || "v1"}
				</span>
			)}
		</div>
	);
}

export default function NextActionPanel({ primaryActions, secondaryActions, busyMap, disabled, onAction }) {
	const primaryAction = primaryActions[0] || null;
	const remainingPrimaryActions = primaryActions.slice(1);
	const supportingActions = [...remainingPrimaryActions, ...secondaryActions].slice(0, 3);

	return (
		<section className="next-action-panel" aria-label="Recommended next action">
			<div className="next-action-copy">
				<span className="next-action-kicker">Next action</span>
				<h3>{primaryAction?.label || ACTION_KIND_LABELS[primaryAction?.action] || "No primary action available"}</h3>
				<p>
					{primaryAction?.reason ||
						normalizeList(primaryAction?.blockers)[0]?.message ||
						(primaryAction ? "Ready to continue this workflow." : "Open a project or complete the current stage to see the next action.")}
				</p>
			</div>
			<div className="next-action-buttons">
				{primaryAction ? (
					<ActionButton action={primaryAction} busy={Boolean(busyMap[primaryAction.action])} disabled={disabled} onAction={onAction} />
				) : (
					<p className="orchestrator-empty-text">No primary action is currently available.</p>
				)}
				{supportingActions.length ? (
					<div className="next-action-secondary-list" aria-label="Secondary actions">
						{supportingActions.map((action) => (
							<ActionButton
								action={action}
								busy={Boolean(busyMap[action.action])}
								disabled={disabled}
								key={`${action.action}-${action.stage}`}
								onAction={onAction}
							/>
						))}
					</div>
				) : null}
			</div>
		</section>
	);
}
