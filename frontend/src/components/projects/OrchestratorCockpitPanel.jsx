import { Alert, Disclosure } from "../ui/surfaces";
import ContextualTaskCard from "./ContextualTaskCard";
import { selectContextualTask } from "./contextualTask";

export default function OrchestratorCockpitPanel({
	currentProject,
	status,
	currentDestination,
	isOverview = false,
	compact = false,
	hidden = false,
	isLoading,
	error,
	authActionDisabled,
	actionBusy,
	actionDisabled,
	focusFallbackRef,
	onAction,
}) {
	if (!currentProject || hidden) {
		return null;
	}

	const { primaryAction, secondaryActions } = selectContextualTask(status, {
		destination: currentDestination,
		overview: isOverview,
	});

	if (!primaryAction && !secondaryActions.length && !error) {
		return null;
	}

	const content = (
		<section className="contextual-task-region" aria-label={compact ? "Test suite actions" : "Contextual task"}>
			{error ? (
				<Alert as="div" tone="danger" className="orchestrator-error" role="alert">
					{error}
				</Alert>
			) : null}
			{primaryAction || secondaryActions.length ? (
				<ContextualTaskCard
					action={primaryAction}
					secondaryActions={secondaryActions}
					status={status}
					busyMap={actionBusy || {}}
					disabled={authActionDisabled || isLoading}
					disabledMap={actionDisabled || {}}
					navigationOnly={isOverview}
					compact={compact}
					focusFallbackRef={focusFallbackRef}
					onAction={onAction}
				/>
			) : null}
		</section>
	);
	return !primaryAction && !error ? (
		<Disclosure className="optional-workflow-actions">
			<summary>More actions</summary>
			{content}
		</Disclosure>
	) : (
		content
	);
}
