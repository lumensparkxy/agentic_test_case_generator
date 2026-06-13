const STAGE_ORDER = [
	"requirements",
	"context",
	"use_cases",
	"impact_analysis",
	"test_cases",
	"automation",
	"execution",
	"review",
	"reports",
];

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

const STATUS_LABELS = {
	not_started: "Not started",
	ready: "Ready",
	blocked: "Blocked",
	completed: "Complete",
	stale: "Stale",
	failed: "Failed",
	attention_required: "Needs review",
};

const formatLabel = (value) =>
	`${value || ""}`
		.split(/[_\s-]+/)
		.filter(Boolean)
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(" ");

const formatDateTime = (value) => {
	if (!value) return "";
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return "";
	return date.toLocaleString(undefined, {
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	});
};

const normalizeList = (value) => (Array.isArray(value) ? value : []);

function StatusPill({ status }) {
	const normalized = `${status || "not_started"}`.replaceAll("_", "-");
	return <span className={`orchestrator-status-pill ${normalized}`}>{STATUS_LABELS[status] || formatLabel(status)}</span>;
}

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

function StageRail({ stages }) {
	return (
		<div className="orchestrator-stage-rail" aria-label="Orchestrator stages">
			{STAGE_ORDER.map((stageKey) => {
				const stage = stages?.[stageKey] || { stage: stageKey, status: "not_started", version: 0 };
				return (
					<div className={`orchestrator-stage-step ${stage.status || "not_started"}`} key={stageKey}>
						<span>{formatLabel(stageKey)}</span>
						<strong>{stage.version ? `v${stage.version}` : STATUS_LABELS[stage.status] || formatLabel(stage.status)}</strong>
					</div>
				);
			})}
		</div>
	);
}

function BlockerList({ blockers }) {
	const items = normalizeList(blockers);
	if (!items.length) return null;
	return (
		<div className="orchestrator-blockers">
			<h3>Blockers</h3>
			<ul>
				{items.map((blocker, index) => (
					<li key={`${blocker.code || "blocker"}-${blocker.stage || "stage"}-${index}`}>
						<strong>{formatLabel(blocker.code)}</strong>
						<span>{blocker.message}</span>
					</li>
				))}
			</ul>
		</div>
	);
}

function RunTimeline({ runsPayload }) {
	const runs = normalizeList(runsPayload?.runs);
	const events = normalizeList(runsPayload?.events);
	const checkpoints = normalizeList(runsPayload?.checkpoints);
	const latestRun = runs[0] || null;
	const checkpointById = new Map(checkpoints.map((checkpoint) => [checkpoint.checkpoint_id, checkpoint]));

	if (!runs.length && !events.length) {
		return (
			<div className="orchestrator-timeline empty">
				<h3>Agent Timeline</h3>
				<p>No orchestrated runs have been recorded for this project yet.</p>
			</div>
		);
	}

	return (
		<div className="orchestrator-timeline">
			<div className="orchestrator-timeline-header">
				<div>
					<h3>Agent Timeline</h3>
					<p>
						{latestRun
							? `${formatLabel(latestRun.current_action)} ${latestRun.status || "running"} · ${formatDateTime(latestRun.updated_at)}`
							: `${events.length} event${events.length === 1 ? "" : "s"}`}
					</p>
				</div>
				{latestRun && <StatusPill status={latestRun.status} />}
			</div>
			<div className="orchestrator-event-list">
				{events.slice(0, 5).map((event) => {
					const checkpoint = event.checkpoint_id ? checkpointById.get(event.checkpoint_id) : null;
					const outputSnapshots = checkpoint?.output_snapshot_ids || {};
					const outputSnapshotText = Object.entries(outputSnapshots)
						.filter(([, value]) => value)
						.map(([stage, value]) => `${formatLabel(stage)} ${value}`)
						.join(", ");
					return (
						<div className="orchestrator-event-row" key={event.event_id}>
							<span>{formatDateTime(event.occurred_at)}</span>
							<div>
								<strong>{event.summary}</strong>
								<p>
									{formatLabel(event.event_type)}
									{event.run_id ? ` · ${event.run_id}` : ""}
									{outputSnapshotText ? ` · ${outputSnapshotText}` : ""}
								</p>
							</div>
						</div>
					);
				})}
			</div>
		</div>
	);
}

export default function OrchestratorCockpitPanel({
	currentProject,
	status,
	runsPayload,
	isLoading,
	error,
	authActionDisabled,
	actionBusy,
	onRefresh,
	onAction,
}) {
	if (!currentProject) {
		return null;
	}

	const nextActions = normalizeList(status?.next_actions);
	const primaryActions = nextActions.filter((action) => action.primary);
	const secondaryActions = nextActions.filter((action) => action.secondary || !action.primary);
	const currentStage = status?.current_stage || currentProject.latest_stage || "requirements";
	const stageSummary = status?.stages?.[currentStage]?.summary || {};
	const busyMap = actionBusy || {};

	return (
		<section className="orchestrator-cockpit" aria-label="Orchestrator Cockpit">
			<div className="orchestrator-cockpit-header">
				<div>
					<h2>Orchestrator Cockpit</h2>
					<p>
						{formatLabel(currentStage)} · revision {status?.project_revision ?? currentProject.current_revision}
					</p>
				</div>
				<div className="orchestrator-cockpit-actions">
					<StatusPill status={status?.upstream_changed ? "stale" : status?.stages?.[currentStage]?.status || "ready"} />
					<button type="button" className="secondary" onClick={onRefresh} disabled={authActionDisabled || isLoading}>
						{isLoading ? "Refreshing" : "Refresh"}
					</button>
				</div>
			</div>

			{error && <div className="orchestrator-error">{error}</div>}

			<div className="orchestrator-summary-grid">
				<div>
					<span>Baseline suite</span>
					<strong>{status?.has_baseline_test_suite ? "Present" : "Not generated"}</strong>
				</div>
				<div>
					<span>Upstream change</span>
					<strong>
						{status?.upstream_changed ? normalizeList(status.changed_upstream_stages).map(formatLabel).join(", ") || "Detected" : "None"}
					</strong>
				</div>
				<div>
					<span>Changed items</span>
					<strong>
						{stageSummary.changed_item_count ?? currentProject.stage_state?.impact_analysis?.metadata?.changed_item_count ?? 0}
					</strong>
				</div>
				<div>
					<span>Runs</span>
					<strong>{normalizeList(runsPayload?.runs).length}</strong>
				</div>
			</div>

			<StageRail stages={status?.stages} />

			<div className="orchestrator-action-grid">
				<div className="orchestrator-action-column">
					<h3>Primary Action</h3>
					{primaryActions.length ? (
						primaryActions.map((action) => (
							<ActionButton
								action={action}
								busy={Boolean(busyMap[action.action])}
								disabled={authActionDisabled}
								key={`${action.action}-${action.stage}`}
								onAction={onAction}
							/>
						))
					) : (
						<p className="orchestrator-empty-text">No primary action is currently available.</p>
					)}
				</div>
				<div className="orchestrator-action-column">
					<h3>Secondary Actions</h3>
					{secondaryActions.length ? (
						secondaryActions
							.slice(0, 3)
							.map((action) => (
								<ActionButton
									action={action}
									busy={Boolean(busyMap[action.action])}
									disabled={authActionDisabled}
									key={`${action.action}-${action.stage}`}
									onAction={onAction}
								/>
							))
					) : (
						<p className="orchestrator-empty-text">No secondary actions are currently available.</p>
					)}
				</div>
			</div>

			<BlockerList blockers={status?.blockers} />
			<RunTimeline runsPayload={runsPayload} />
		</section>
	);
}
