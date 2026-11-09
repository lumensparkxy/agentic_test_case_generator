import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState } from "react";
import { API_CONTRACT_ENDPOINTS } from "../../api/generated/api-contracts";
import RouteLink from "../../app/RouteLink";
import { buildSavedCasePath } from "../../app/savedCaseLinks";
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

function savedRecordIdForSnapshot(report, project) {
	const snapshot = project?.current_snapshots?.execution;
	if (!report?.run_id || !snapshot?.snapshot_id || snapshot.payload?.run_id !== report.run_id) return null;
	const records = (project.execution_runs || []).filter(
		(run) =>
			run.project_id === project.project_id &&
			run.run_id === report.run_id &&
			run.snapshot_id === snapshot.snapshot_id &&
			run.source_snapshot_id === snapshot.source_snapshot_id
	);
	return records.length === 1 ? records[0].run_record_id : null;
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

function CaseOutcomes({ results, caseNavigation }) {
	const disclosureRef = useRef(null);
	const returnResultId = caseNavigation?.returnResultId;
	useLayoutEffect(() => {
		const disclosure = disclosureRef.current;
		if (!disclosure || !returnResultId) return;
		const link = Array.from(disclosure.querySelectorAll("a[data-run-case-result]")).find(
			(item) => item.dataset.runCaseResult === returnResultId
		);
		if (!link) return;
		disclosure.open = true;
		link.focus({ preventScroll: true });
		link.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
	}, [returnResultId]);

	if (!results?.length) {
		return <p className="helper-text">Saved case outcomes are unavailable for this run.</p>;
	}

	return (
		<Disclosure ref={disclosureRef} className="execution-case-outcomes">
			<summary>Case outcomes ({results.length})</summary>
			<List className="execution-case-outcome-list" role="list" aria-label="Recorded case outcomes">
				{results.map((result) => {
					const path =
						caseNavigation && result.source_test_case_id
							? buildSavedCasePath(caseNavigation.projectId, caseNavigation.snapshotId, result.source_test_case_id)
							: "";
					return (
						<ListItem key={result.id}>
							<div className="execution-case-heading">
								<div>
									<strong>
										{path ? (
											<RouteLink
												to={path}
												navigate={(to) => caseNavigation.openCase(to, result.id)}
												aria-label={`Open saved case ${result.source_test_case_id} ${result.title}`}
												data-run-case-result={result.id}
											>
												{result.source_test_case_id}
											</RouteLink>
										) : (
											result.source_test_case_id
										)}
									</strong>
									<p>{result.title}</p>
								</div>
								<span className="execution-case-status">{formatExecutionCaseStatus(result.status)}</span>
							</div>
							<ExecutionIssues issues={result.issues} />
						</ListItem>
					);
				})}
			</List>
		</Disclosure>
	);
}

function PlaywrightExecutionReport({
	report,
	notice,
	onRetry,
	caseNavigation,
	emptyMessage = "No Playwright execution report has been recorded yet.",
}) {
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
					{!notice && <CaseOutcomes results={report.results} caseNavigation={caseNavigation} />}
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
					<p>{emptyMessage}</p>
				</CollectionState>
			) : null}
		</div>
	);
}

function SavedExecutionReport({ projectId, runRecordId, fallbackReport, expectedSuiteSnapshotId, request, location, navigate }) {
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
				if (
					run?.project_id !== projectId ||
					run?.run_record_id !== runRecordId ||
					!run?.run_id ||
					(expectedSuiteSnapshotId !== undefined && run.source_snapshot_id !== expectedSuiteSnapshotId)
				) {
					throw new Error("The saved evidence did not match the selected run.");
				}
				if (!controller.signal.aborted) setResult({ run });
			} catch {
				if (!controller.signal.aborted) setResult({ notice: "Couldn’t load saved evidence for this run.", retryable: true });
			}
		}
		load();
		return () => controller.abort();
	}, [projectId, runRecordId, expectedSuiteSnapshotId, attempt]);

	const retry = () => {
		setResult(null);
		setAttempt((count) => count + 1);
	};
	const returnCase = location.state?.runCase;
	const caseNavigation = result?.run?.source_snapshot_id
		? {
				projectId,
				snapshotId: result.run.source_snapshot_id,
				returnResultId: returnCase?.projectId === projectId && returnCase.runRecordId === runRecordId ? returnCase.resultId : null,
				openCase: (path, resultId) => {
					const query = new URLSearchParams(location.search);
					query.set("run", runRecordId);
					navigate(`${location.pathname}?${query}${location.hash}`, {
						replace: true,
						state: { ...location.state, runCase: { projectId, runRecordId, resultId } },
					});
					navigate(path);
				},
			}
		: null;
	return (
		<PlaywrightExecutionReport
			report={result?.run || fallbackReport}
			notice={result ? result.notice : "Loading saved run evidence…"}
			onRetry={result?.retryable ? retry : undefined}
			caseNavigation={caseNavigation}
		/>
	);
}

export default function ExecutionReport({
	currentProject,
	executionRunResult,
	currentSuiteSnapshotId,
	runRecordId,
	request,
	location,
	navigate,
}) {
	const projectId = currentProject?.project_id;
	const expectedSuiteSnapshotId = runRecordId ? undefined : currentSuiteSnapshotId;
	const latest =
		expectedSuiteSnapshotId === undefined
			? latestExecutionReport(executionRunResult, currentProject)
			: expectedSuiteSnapshotId && executionRunResult?.source_snapshot_id === expectedSuiteSnapshotId
				? executionRunResult
				: null;
	const savedRunId = runRecordId || latest?.run_record_id || savedRecordIdForSnapshot(latest, currentProject);
	return savedRunId && projectId ? (
		<SavedExecutionReport
			key={JSON.stringify([projectId, savedRunId, Boolean(runRecordId), expectedSuiteSnapshotId])}
			projectId={projectId}
			runRecordId={savedRunId}
			fallbackReport={runRecordId ? null : latest}
			expectedSuiteSnapshotId={expectedSuiteSnapshotId}
			request={request}
			location={location}
			navigate={navigate}
		/>
	) : (
		<PlaywrightExecutionReport
			report={runRecordId ? null : latest}
			emptyMessage={expectedSuiteSnapshotId === undefined ? undefined : "No Playwright execution report matches this saved suite."}
		/>
	);
}
