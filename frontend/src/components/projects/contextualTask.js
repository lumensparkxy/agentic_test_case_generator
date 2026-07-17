import { resolveOrchestratorDestination } from "../../app/workflowRoutes";

const SOURCE_STAGE_LABELS = Object.freeze({
	requirements: "Requirements",
	use_cases: "Use Cases",
});

const normalizeList = (value) => (Array.isArray(value) ? value : []);

export const formatTaskLabel = (value) =>
	`${value || ""}`
		.split(/[_\s-]+/)
		.filter(Boolean)
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(" ");

export function selectContextualTask(status, { destination = null, overview = false } = {}) {
	const actions = normalizeList(status?.next_actions);
	const scopedActions = overview
		? actions
		: actions.filter((action) => destination && resolveOrchestratorDestination(action) === destination);
	const primaryAction =
		scopedActions.find((action) => action?.primary && action.action !== "full_regenerate") ||
		scopedActions.find((action) => action?.action !== "full_regenerate") ||
		null;

	if (!primaryAction) {
		return { primaryAction: null, secondaryActions: scopedActions };
	}

	return {
		primaryAction,
		secondaryActions: scopedActions.filter((action) => action !== primaryAction),
	};
}

export function getTaskProvenance(status) {
	return Object.entries(SOURCE_STAGE_LABELS).flatMap(([stageName, label]) => {
		const stage = status?.stages?.[stageName];
		if (!stage || (!stage.version && !stage.current_snapshot_id)) {
			return [];
		}
		return [
			{
				stage: stageName,
				label,
				version: Number(stage.version) > 0 ? Number(stage.version) : null,
				snapshotId: stage.current_snapshot_id || "",
			},
		];
	});
}

export function formatTaskProvenance(provenance) {
	const sources = normalizeList(provenance).map((source) => `${source.label}${source.version ? ` v${source.version}` : ""}`);
	if (!sources.length) {
		return "";
	}
	if (sources.length === 1) {
		return `Based on ${sources[0]}`;
	}
	return `Based on ${sources.slice(0, -1).join(", ")} and ${sources.at(-1)}`;
}
