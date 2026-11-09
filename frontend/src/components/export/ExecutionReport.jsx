import { useEffect, useEffectEvent, useState } from "react";
import { API_CONTRACT_ENDPOINTS } from "../../api/generated/api-contracts";
import { List, ListItem, CollectionState } from "../ui/collections";
import { Button } from "../ui/controls";
import { Disclosure } from "../ui/surfaces";
import { formatExecutionCaseStatus } from "../automation/automationEvidence";
import { ExecutionIssues, RunnerIssues } from "../automation/ExecutionIssues";

const REPORT_PATH_LIMIT = 8;

function latestExecutionReport(executionRunResult, currentProject) {
	if (executionRunResult?.run_id) {
		return executionRunResult;
	}
	const executionSnapshotPayload = currentProject?.current_snapshots?.execution?.payload;
	if (executionSnapshotPayload?.run_id) {
		return { ...executionSnapshotPayload, source_snapshot_id: currentProject.current_snapshots.execution.source_snapshot_id };
	}
	const projectRun = currentProject?.execution_runs?.find((run) => !run.historical);
	if (projectRun?.run_id) {
		return projectRun;
	}
	const reportSnapshotPayload = currentProject?.current_snapshots?.reports?.payload;
	return reportSnapshotPayload?.run_id ? reportSnapshotPayload : null;
}

function normalizeReportPaths(report) {
	const directPaths = Array.isArray(report?.playwright_report_paths) ? report.playwright_report_paths : [];
	const resultPaths = Array.isArray(report?.results) ? report.results.map((result) => result?.playwright_report_path).filter(Boolean) : [];
	return [...new Set([...directPaths, ...resultPaths])];
}

function renderSummaryPill(label, value, tone = "") {
	return (
		<span className={`workflow-diagnostics-pill ${tone}`.trim()}>
			{label} {value || 0}
		</span>
	);
}

function CaseOutcomes({ results }) {
	if (!results?.length) {
		return <p className="helper-text">Saved case outcomes are unavailable for this run.</p>;
	}

	return (
		<Disclosure className="execution-case-outcomes">
			<summary>Case outcomes ({results.length})</summary>
			<List className="execution-case-outcome-list" role="list" aria-label="Recorded case outcomes">
				{results.map((result) => (
					<ListItem key={result.id}>
						<div className="execution-case-heading">
							<div>
								<strong>{result.source_test_case_id}</strong>
								<p>{result.title}</p>
							</div>
							<span className="execution-case-status">{formatExecutionCaseStatus(result.status)}</span>
						</div>
						<ExecutionIssues issues={result.issues} />
					</ListItem>
				))}
			</List>
		</Disclosure>
	);
}

function PlaywrightExecutionReport({ report, notice, onRetry }) {
	const summary = report?.summary || {};
	const reportPaths = normalizeReportPaths(report);
	const visibleReportPaths = reportPaths.slice(0, REPORT_PATH_LIMIT);
	const hiddenReportPathCount = Math.max(0, reportPaths.length - visibleReportPaths.length);

	return (
		<div className="export-section playwright-report-section">
			<h3 className="section-subtitle">Playwright Execution Report</h3>
			{notice && (
				<div className="workflow-result-notice" role={onRetry ? "alert" : "status"}>
					<p>{notice}</p>
					{onRetry && (
						<Button type="button" className="secondary" onClick={onRetry}>
							Retry run evidence
						</Button>
					)}
				</div>
			)}
			{report ? (
				<div className="playwright-report-card">
					<div className="playwright-report-header">
						<div>
							<strong>Run {report.run_id}</strong>
							<p>
								{report.target_environment || "default"} {report.target_base_url ? `• ${report.target_base_url}` : ""}
							</p>
						</div>
						<span className={`review-banner ${report.status === "passed" ? "review-approved" : "review-needs-work"}`}>
							{report.status || "recorded"}
						</span>
					</div>
					<div className="workflow-diagnostics-pills">
						{renderSummaryPill("Passed", summary.passed, "success")}
						{renderSummaryPill("Failed", summary.failed, "warning")}
						{summary.flaky > 0 && renderSummaryPill("Flaky", summary.flaky, "warning")}
						{renderSummaryPill("Invalid", summary.invalid, "warning")}
						{renderSummaryPill("Skipped", summary.skipped)}
					</div>
					{report.source_snapshot_id && (
						<p className="helper-text">
							Source suite snapshot <code>{report.source_snapshot_id}</code>
						</p>
					)}
					{report.historical && <p className="helper-text">Historical run from project revision {report.project_revision}.</p>}
					<RunnerIssues issues={report.issues} />
					{!notice && <CaseOutcomes results={report.results} />}
					{report.artifacts_root && (
						<p className="helper-text">
							Artifacts root: <code>{report.artifacts_root}</code>
						</p>
					)}
					{visibleReportPaths.length ? (
						<>
							<p className="helper-text">Consolidated report path:</p>
							<List className="playwright-report-paths">
								{visibleReportPaths.map((path) => (
									<ListItem key={path}>
										<code>{path}</code>
									</ListItem>
								))}
							</List>
						</>
					) : (
						<p className="helper-text">No Playwright report path was returned for this execution run.</p>
					)}
					{hiddenReportPathCount > 0 && <p className="helper-text">+ {hiddenReportPathCount} additional report paths</p>}
				</div>
			) : !notice ? (
				<CollectionState as="div" kind="empty" className="playwright-report-card empty">
					<p>No Playwright execution report has been recorded yet.</p>
				</CollectionState>
			) : null}
		</div>
	);
}

function SavedExecutionReport({ projectId, runRecordId, fallbackReport, request }) {
	const [result, setResult] = useState(null);
	const [attempt, setAttempt] = useState(0);
	const fetchRun = useEffectEvent((signal) => {
		const path = API_CONTRACT_ENDPOINTS.projectExecutionRun.path
			.replace("{project_id}", encodeURIComponent(projectId))
			.replace("{run_record_id}", encodeURIComponent(runRecordId));
		return request(path, { signal });
	});

	useEffect(() => {
		const controller = new AbortController();
		async function load() {
			try {
				const response = await fetchRun(controller.signal);
				if (response.status === 404 || response.status === 403) {
					if (!controller.signal.aborted) setResult({ notice: "Saved evidence for this run is unavailable." });
					return;
				}
				if (!response.ok) throw new Error("Run lookup failed.");
				const run = await response.json();
				if (run?.project_id !== projectId || run?.run_record_id !== runRecordId || !run?.run_id) {
					throw new Error("The saved evidence did not match the selected run.");
				}
				if (!controller.signal.aborted) setResult({ run });
			} catch {
				if (!controller.signal.aborted) setResult({ notice: "Couldn’t load saved evidence for this run.", retryable: true });
			}
		}
		load();
		return () => controller.abort();
	}, [projectId, runRecordId, attempt]);

	const retry = () => {
		setResult(null);
		setAttempt((count) => count + 1);
	};
	return (
		<PlaywrightExecutionReport
			report={result?.run || fallbackReport}
			notice={result ? result.notice : "Loading saved run evidence…"}
			onRetry={result?.retryable ? retry : undefined}
		/>
	);
}

export default function ExecutionReport({ currentProject, executionRunResult, runRecordId, request }) {
	const projectId = currentProject?.project_id;
	const latest = latestExecutionReport(executionRunResult, currentProject);
	const savedRunId = runRecordId || latest?.run_record_id;
	return savedRunId && projectId ? (
		<SavedExecutionReport
			key={JSON.stringify([projectId, savedRunId, Boolean(runRecordId)])}
			projectId={projectId}
			runRecordId={savedRunId}
			fallbackReport={runRecordId ? null : latest}
			request={request}
		/>
	) : (
		<PlaywrightExecutionReport report={runRecordId ? null : latest} />
	);
}
