import { PanelRightClose, PanelRightOpen } from "lucide-react";

import ProjectSummaryPanel from "./ProjectSummaryPanel";

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
	return (
		<section className="project-rail-section orchestrator-blockers" aria-label="Blockers">
			<div className="project-rail-section-header">
				<h3>Blockers</h3>
				<span>{items.length}</span>
			</div>
			{items.length ? (
				<ul>
					{items.map((blocker, index) => (
						<li key={`${blocker.code || "blocker"}-${blocker.stage || "stage"}-${index}`}>
							<strong>{formatLabel(blocker.code)}</strong>
							<span>{blocker.message}</span>
						</li>
					))}
				</ul>
			) : (
				<p className="project-rail-empty">No blockers.</p>
			)}
		</section>
	);
}

function RunTimeline({ runsPayload }) {
	const runs = normalizeList(runsPayload?.runs);
	const events = normalizeList(runsPayload?.events);
	const checkpoints = normalizeList(runsPayload?.checkpoints);
	const latestRun = runs[0] || null;
	const checkpointById = new Map(checkpoints.map((checkpoint) => [checkpoint.checkpoint_id, checkpoint]));

	return (
		<section className="project-rail-section orchestrator-timeline" aria-label="Agent Timeline">
			<div className="orchestrator-timeline-header">
				<div>
					<h3>Agent Timeline</h3>
					<p>
						{latestRun
							? `${formatLabel(latestRun.current_action)} ${latestRun.status || "running"} · ${formatDateTime(latestRun.updated_at)}`
							: events.length
								? `${events.length} event${events.length === 1 ? "" : "s"}`
								: "No orchestrated runs yet."}
					</p>
				</div>
				{latestRun && <StatusPill status={latestRun.status} />}
			</div>
			{events.length ? (
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
			) : (
				<p className="project-rail-empty">No durable agent events recorded.</p>
			)}
		</section>
	);
}

function ExecutionHistory({ runs }) {
	const items = normalizeList(runs);
	if (!items.length) {
		return null;
	}
	return (
		<div className="project-history-block">
			<h3>Execution Runs</h3>
			<div className="project-run-list">
				{items.slice(0, 4).map((run) => (
					<div className="project-run-row" key={run.run_record_id}>
						<span>{run.target_environment}</span>
						<strong>{run.status}</strong>
						<span>
							{run.summary?.passed || 0} passed / {run.summary?.failed || 0} failed
						</span>
					</div>
				))}
			</div>
		</div>
	);
}

function ReportEvidence({ reportState, reportSnapshot }) {
	if (!reportSnapshot) {
		return null;
	}
	const payload = reportSnapshot.payload || {};
	const evidence = payload.evidence || {};
	const sourceSnapshotIds = evidence.source_snapshot_ids || {};
	const executionRunIds = evidence.execution_run_ids || [];
	const sourceEntries = Object.entries(sourceSnapshotIds).filter(([, value]) => value);
	const status = reportState?.stale ? "Stale" : reportState?.approved ? "Approved" : "Draft";

	return (
		<div className="project-history-block">
			<h3>Latest Report</h3>
			<div className="project-run-list">
				<div className="project-run-row">
					<span>{payload.format || reportState?.operation || "report"}</span>
					<strong>{status}</strong>
					<span>{reportSnapshot.snapshot_id}</span>
				</div>
				{sourceEntries.slice(0, 3).map(([stage, snapshotId]) => (
					<div className="project-run-row" key={`${stage}-${snapshotId}`}>
						<span>{stage.replaceAll("_", " ")}</span>
						<strong>Evidence</strong>
						<span>{snapshotId}</span>
					</div>
				))}
				{executionRunIds.slice(0, 2).map((runId) => (
					<div className="project-run-row" key={runId}>
						<span>execution run</span>
						<strong>Evidence</strong>
						<span>{runId}</span>
					</div>
				))}
			</div>
		</div>
	);
}

function TimelinePreview({ events }) {
	const items = normalizeList(events);
	if (!items.length) {
		return null;
	}
	return (
		<div className="project-history-block">
			<h3>Project Timeline</h3>
			<div className="project-timeline-list">
				{items.slice(0, 4).map((event) => (
					<div className="project-timeline-row" key={event.event_id}>
						<span>{formatDateTime(event.occurred_at)}</span>
						<strong>{event.summary}</strong>
					</div>
				))}
			</div>
		</div>
	);
}

function LastRunSummary({ currentProject, runsPayload }) {
	const latestRun = normalizeList(runsPayload?.runs)[0] || currentProject?.execution_runs?.[0] || null;
	if (!latestRun) {
		return (
			<section className="project-rail-section" aria-label="Last run">
				<div className="project-rail-section-header">
					<h3>Last run</h3>
					<span>None</span>
				</div>
				<p className="project-rail-empty">No execution run has been recorded.</p>
			</section>
		);
	}
	return (
		<section className="project-rail-section" aria-label="Last run">
			<div className="project-rail-section-header">
				<h3>Last run</h3>
				<StatusPill status={latestRun.status} />
			</div>
			<div className="project-last-run">
				<strong>{latestRun.target_environment || formatLabel(latestRun.current_action) || "Execution"}</strong>
				<span>{formatDateTime(latestRun.updated_at || latestRun.completed_at || latestRun.started_at) || latestRun.run_id}</span>
			</div>
		</section>
	);
}

export default function ProjectInformationRail({
	currentProject,
	status,
	runsPayload,
	isLoading,
	error,
	authActionDisabled,
	onRefresh,
	isCollapsed = false,
	onToggleCollapsed,
}) {
	const toggleLabel = isCollapsed ? "Expand project information" : "Collapse project information";
	const ToggleIcon = isCollapsed ? PanelRightOpen : PanelRightClose;

	if (!currentProject) {
		return (
			<aside className={`project-information-rail empty ${isCollapsed ? "collapsed" : ""}`} aria-label="Project information rail">
				<section className="project-rail-header" aria-label="Status overview">
					<div>
						<span className="project-rail-kicker">Operational status</span>
						<strong>Idle</strong>
					</div>
					<button type="button" className="project-rail-toggle" onClick={onToggleCollapsed} aria-label={toggleLabel} title={toggleLabel}>
						<ToggleIcon aria-hidden="true" size={18} strokeWidth={2.1} />
					</button>
				</section>
				{!isCollapsed && (
					<p className="project-rail-empty">Select or create a QA project to see status, blockers, timeline, and evidence.</p>
				)}
			</aside>
		);
	}

	const stageState = currentProject.stage_state || {};
	const reportSnapshot = currentProject.current_snapshots?.reports || null;
	const currentStage = status?.current_stage || currentProject.latest_stage || "requirements";
	const statusValue = status?.upstream_changed ? "stale" : status?.stages?.[currentStage]?.status || "ready";

	return (
		<aside className={`project-information-rail ${isCollapsed ? "collapsed" : ""}`} aria-label="Project information rail">
			<section className="project-rail-header" aria-label="Status overview">
				<div>
					<span className="project-rail-kicker">Operational status</span>
					<strong>{formatLabel(currentStage)}</strong>
					<p>
						{currentProject.name} · revision {status?.project_revision ?? currentProject.current_revision}
					</p>
				</div>
				<div className="project-rail-header-actions">
					<StatusPill status={statusValue} />
					<button type="button" className="project-rail-toggle" onClick={onToggleCollapsed} aria-label={toggleLabel} title={toggleLabel}>
						<ToggleIcon aria-hidden="true" size={18} strokeWidth={2.1} />
					</button>
				</div>
			</section>

			{!isCollapsed && (
				<>
					<button type="button" className="secondary project-rail-refresh" onClick={onRefresh} disabled={authActionDisabled || isLoading}>
						{isLoading ? "Refreshing" : "Refresh status"}
					</button>

					{error && <div className="orchestrator-error">{error}</div>}

					<ProjectSummaryPanel currentProject={currentProject} status={status} runsPayload={runsPayload} />

					<section className="project-rail-section" aria-label="Stage progress">
						<div className="project-rail-section-header">
							<h3>Stage progress</h3>
							<span>{STAGE_ORDER.filter((stage) => status?.stages?.[stage]?.version || stageState[stage]?.version).length} started</span>
						</div>
						<StageRail stages={status?.stages || stageState} />
					</section>

					<BlockerList blockers={status?.blockers} />
					<RunTimeline runsPayload={runsPayload} />
					<LastRunSummary currentProject={currentProject} runsPayload={runsPayload} />

					<section className="project-rail-section" aria-label="Project evidence">
						<div className="project-rail-section-header">
							<h3>Project evidence</h3>
							<span>{reportSnapshot ? "Ready" : "Pending"}</span>
						</div>
						<div className="project-history-grid rail">
							<ReportEvidence reportState={stageState.reports} reportSnapshot={reportSnapshot} />
							<ExecutionHistory runs={currentProject.execution_runs || []} />
							<TimelinePreview events={currentProject.timeline || []} />
						</div>
					</section>
				</>
			)}
		</aside>
	);
}
