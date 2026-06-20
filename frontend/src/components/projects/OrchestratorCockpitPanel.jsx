import NextActionPanel from "./NextActionPanel";

const normalizeList = (value) => (Array.isArray(value) ? value : []);

export default function OrchestratorCockpitPanel({ currentProject, status, isLoading, error, authActionDisabled, actionBusy, onAction }) {
	if (!currentProject) {
		return null;
	}

	const nextActions = normalizeList(status?.next_actions);
	const primaryActions = nextActions.filter((action) => action.primary);
	const secondaryActions = nextActions.filter((action) => action.secondary || !action.primary);
	const busyMap = actionBusy || {};

	return (
		<section className="orchestrator-cockpit" aria-label="Orchestrator Cockpit">
			{error && <div className="orchestrator-error">{error}</div>}
			<NextActionPanel
				primaryActions={primaryActions}
				secondaryActions={secondaryActions}
				busyMap={busyMap}
				disabled={authActionDisabled || isLoading}
				onAction={onAction}
			/>
		</section>
	);
}
