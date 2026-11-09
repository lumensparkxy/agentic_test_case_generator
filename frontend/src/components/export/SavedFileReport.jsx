import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState } from "react";

import RouteLink from "../../app/RouteLink";
import { buildSavedCasePath } from "../../app/savedCaseLinks";
import { buildSavedFileReportPath, readExactSavedFileReport } from "../../app/savedFileReportLinks";
import { Button } from "../ui/controls";
import { Badge, Disclosure, Surface } from "../ui/surfaces";
import { formatWorkspaceLabel, formatWorkspaceStatus } from "../workspace/workspacePresentation";
import "./SavedFileReport.css";

const FILE_FORMAT_LABELS = { csv: "CSV", excel: "Excel", json: "JSON" };
const SOURCE_LABELS = { context: "Context", requirements: "Requirements", use_cases: "Scenarios", test_cases: "Test suite" };
const reportDateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" });

function RecordedTime({ value }) {
	const date = new Date(value || "");
	return Number.isNaN(date.getTime()) ? "Unavailable" : <time dateTime={value}>{reportDateFormat.format(date)} UTC</time>;
}

function ReportFacts({ rows }) {
	return (
		<dl className="saved-file-report-facts">
			{rows.map(([label, value]) => (
				<div key={label}>
					<dt>{label}</dt>
					<dd>{value}</dd>
				</div>
			))}
		</dl>
	);
}

function SavedExportEvidence({ evidence, projectId, navigate }) {
	const design = evidence.design_delivery;
	const rows = [
		["Exported", <RecordedTime value={evidence.exported_at} />],
		["Machine review", formatWorkspaceStatus(evidence.machine_review_status)],
		["Execution status", formatWorkspaceStatus(evidence.execution_status)],
	];
	if (design) {
		rows.push(
			["Independent design", formatWorkspaceStatus(design.design_status)],
			["Source at export", formatWorkspaceStatus(design.source_freshness)],
			["Scenario approval", formatWorkspaceStatus(design.scenario_decision)],
			["Human suite acceptance", formatWorkspaceStatus(design.human_acceptance)],
			["Declared setup", formatWorkspaceStatus(design.declared_setup)],
			["Target readiness", formatWorkspaceStatus(design.target_readiness)]
		);
	} else {
		rows.push(["Human review", formatWorkspaceStatus(evidence.human_review_status)]);
	}
	const references = Object.entries(evidence.source_snapshot_ids || {}).map(([source, id]) => [
		SOURCE_LABELS[source] || formatWorkspaceLabel(source),
		<code>{id}</code>,
	]);
	if (design?.scenario_review_id) references.push(["Scenario decision", <code>{design.scenario_review_id}</code>]);
	if (evidence.requirement_ids?.length) references.push(["Exported requirements", evidence.requirement_ids.join(", ")]);
	if (design?.required_requirement_ids?.length) references.push(["Required scope", design.required_requirement_ids.join(", ")]);
	if (design?.deferred_requirement_ids?.length) references.push(["Deferred scope", design.deferred_requirement_ids.join(", ")]);
	if (design?.excluded_requirement_ids?.length) references.push(["Excluded scope", design.excluded_requirement_ids.join(", ")]);
	if (evidence.execution_run_ids?.length) references.push(["Recorded execution run IDs", evidence.execution_run_ids.join(", ")]);

	const suiteId = evidence.suite_snapshot_id;
	const cases = suiteId ? (evidence.case_versions || []).filter((row) => row.case_id && row.case_version_id) : [];
	return (
		<section className="saved-file-report-evidence" aria-label="Recorded export evidence">
			<h3 className="section-subtitle">Recorded export evidence</h3>
			<ReportFacts rows={rows} />
			{evidence.draft_override_reason && (
				<p className="saved-file-report-reason">
					<strong>Draft export reason</strong>
					<span>{evidence.draft_override_reason}</span>
				</p>
			)}
			{references.length > 0 && (
				<Disclosure>
					<summary>Saved source references</summary>
					<ReportFacts rows={references} />
				</Disclosure>
			)}
			{cases.length > 0 && (
				<Disclosure>
					<summary>Original saved cases</summary>
					<ul className="saved-file-report-cases">
						{cases.map(({ case_id: caseId, case_version_id: versionId, case_version_number: version }) => (
							<li key={caseId}>
								<RouteLink to={buildSavedCasePath(projectId, suiteId, caseId)} navigate={navigate}>
									Open saved case {caseId}
								</RouteLink>
								<span>
									{Number.isInteger(version) && <>Version {version} · </>}
									<code>{versionId}</code>
								</span>
							</li>
						))}
					</ul>
				</Disclosure>
			)}
		</section>
	);
}

export default function SavedFileReport({ projectId, reportId, request, navigate }) {
	const headingRef = useRef(null);
	const [result, setResult] = useState(null);
	const [attempt, setAttempt] = useState(0);
	useLayoutEffect(() => {
		headingRef.current?.focus();
	}, []);
	const fetchReport = useEffectEvent((signal) => readExactSavedFileReport({ projectId, reportId, request, signal }));
	useEffect(() => {
		if (!reportId) return;
		const controller = new AbortController();
		async function load() {
			try {
				const report = await fetchReport(controller.signal);
				if (!controller.signal.aborted) setResult(report ? { report } : { unavailable: true });
			} catch {
				if (!controller.signal.aborted) setResult({ retryable: true });
			}
		}
		load();
		return () => controller.abort();
	}, [projectId, reportId, attempt]);

	const report = result?.report;
	return (
		<Surface className="panel saved-file-report" aria-labelledby="saved-file-report-heading">
			<header className="saved-file-report-header">
				<h2 className="panel-title" id="saved-file-report-heading" ref={headingRef} tabIndex={-1}>
					Saved file report
				</h2>
				<RouteLink to={buildSavedFileReportPath(projectId)} navigate={navigate}>
					Open current export
				</RouteLink>
			</header>
			{!reportId || result?.unavailable ? (
				<p role="status">This saved file report is unavailable.</p>
			) : result?.retryable ? (
				<div role="alert">
					<p>Couldn’t load this saved file report.</p>
					<Button
						variant="secondary"
						onClick={() => {
							setResult(null);
							setAttempt((value) => value + 1);
						}}
					>
						Retry saved report
					</Button>
				</div>
			) : !report ? (
				<p role="status">Loading saved file report…</p>
			) : (
				<>
					<p className="panel-description">Evidence recorded when this file was exported.</p>
					<div className="saved-file-report-status">
						<Badge tone={report.delivery_kind === "draft" ? "warning" : "neutral"}>{formatWorkspaceStatus(report.delivery_kind)}</Badge>
						<Badge tone="neutral">{report.historical ? "Historical" : "Current"}</Badge>
						{report.stale && <Badge tone="warning">Stale</Badge>}
					</div>
					<ReportFacts
						rows={[
							["Report ID", <code>{report.report_id}</code>],
							["Format", FILE_FORMAT_LABELS[report.format]],
							["Created", <RecordedTime value={report.created_at} />],
							["Project revision", report.project_revision],
							[
								"Legacy approval",
								report.legacy_approved === true ? "Approved" : report.legacy_approved === false ? "Unapproved" : "Unavailable",
							],
						]}
					/>
					{report.evidence ? (
						<SavedExportEvidence evidence={report.evidence} projectId={projectId} navigate={navigate} />
					) : (
						<p className="helper-text">Detailed saved evidence is unavailable for this file.</p>
					)}
				</>
			)}
		</Surface>
	);
}
