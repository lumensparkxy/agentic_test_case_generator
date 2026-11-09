import { useEffectEvent, useLayoutEffect, useMemo, useRef } from "react";
import { createSavedCasePathResolver } from "../../app/savedCaseLinks";
import { CollectionState, TableScroll, Table } from "../ui/collections";
import { Link } from "../ui/controls";
import { Surface, Badge, Disclosure } from "../ui/surfaces";

const VISIBLE_CASE_COUNT = 3;

function LinkedCaseTags({ requirementId, cases, paths, onOpenCase }) {
	const renderCases = (items) =>
		items.map((testCase, index) => {
			const key = `${testCase.id}-${index}`;
			const path = paths.get(testCase);
			const badge = (
				<Badge key={key} icon={null} tone="neutral" className="tag traceability-case-tag">
					{testCase.id}
				</Badge>
			);
			return path ? (
				<Link
					key={key}
					href={path}
					className="traceability-case-link"
					aria-label={`Open saved case ${testCase.id} ${testCase.title}`}
					data-traceability-case={testCase.id}
					data-requirement-id={requirementId}
					onClick={(event) => {
						if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
						event.preventDefault();
						onOpenCase(path, { requirementId, caseId: testCase.id });
					}}
				>
					{badge}
				</Link>
			) : (
				badge
			);
		});
	const remaining = cases.length - VISIBLE_CASE_COUNT;
	return (
		<>
			<div className="traceability-case-items">{renderCases(cases.slice(0, VISIBLE_CASE_COUNT))}</div>
			{remaining > 0 && (
				<Disclosure className="traceability-case-overflow">
					<summary>
						{remaining} more linked {remaining === 1 ? "test" : "tests"}
					</summary>
					<div
						className="traceability-case-items traceability-more-cases"
						role="region"
						aria-label={`More linked tests for ${requirementId}`}
						tabIndex={0}
					>
						{renderCases(cases.slice(VISIBLE_CASE_COUNT))}
					</div>
				</Disclosure>
			)}
		</>
	);
}

export default function TraceabilityMatrixPanel({
	approvedRequirements,
	requirementTraceabilityRows,
	tracedRequirementCount,
	coverageMetrics,
	testCases,
	projectId,
	savedSuite,
	returnCase,
	onOpenCase,
	onReturnHandled,
}) {
	const tableRef = useRef(null);
	const paths = useMemo(() => {
		const resolve = createSavedCasePathResolver(projectId, savedSuite);
		return new Map(testCases.map((testCase) => [testCase, resolve(testCase)]));
	}, [projectId, savedSuite, testCases]);
	const linkedCases = new Set(requirementTraceabilityRows.flatMap((row) => row.linkedTestCases));
	const linkedCount = testCases.filter((testCase) => linkedCases.has(testCase)).length;
	const handleReturn = useEffectEvent(onReturnHandled);
	useLayoutEffect(() => {
		if (!returnCase || !tableRef.current) return;
		const link = Array.from(tableRef.current.querySelectorAll("a[data-traceability-case]")).find(
			(item) => item.dataset.traceabilityCase === returnCase.caseId && item.dataset.requirementId === returnCase.requirementId
		);
		if (link) {
			const overflow = link.closest("details");
			if (overflow) overflow.open = true;
			link.focus({ preventScroll: true });
			link.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
		}
		handleReturn();
	}, [returnCase]);

	if (approvedRequirements.length === 0) {
		return (
			<CollectionState as="div" kind="empty" className="generate-result-empty">
				<h3>Traceability Matrix</h3>
				<p>No approved requirements are available to trace for this run.</p>
			</CollectionState>
		);
	}

	return (
		<Surface as="div" className="result-section">
			<h3>Traceability Matrix</h3>
			<p>Links identify related tests. Business coverage assessment, human approval and execution results are separate evidence.</p>
			<div className="workflow-diagnostics-pills">
				<span className="workflow-diagnostics-pill">
					Approved requirements with linked tests {tracedRequirementCount}/{approvedRequirements.length}
				</span>
				<span className="workflow-diagnostics-pill">
					Cases with traceability {linkedCount}/{testCases.length}
				</span>
				<span className="workflow-diagnostics-pill">
					Linked scenarios{" "}
					{coverageMetrics?.planned_scenarios_total > 0 &&
					Number.isInteger(coverageMetrics?.covered_planned_scenarios) &&
					coverageMetrics.covered_planned_scenarios >= 0 &&
					coverageMetrics.covered_planned_scenarios <= coverageMetrics.planned_scenarios_total
						? `${coverageMetrics.covered_planned_scenarios}/${coverageMetrics.planned_scenarios_total}`
						: "Unavailable"}
				</span>
			</div>
			<TableScroll
				ref={tableRef}
				className="traceability-table-wrapper"
				role="region"
				aria-label="Requirement traceability table"
				tabIndex={0}
			>
				<Table className="traceability-table">
					<thead>
						<tr>
							<th scope="col">Requirement</th>
							<th scope="col">Linked test cases</th>
							<th scope="col">Linked scenarios</th>
							<th scope="col">Status</th>
						</tr>
					</thead>
					<tbody>
						{requirementTraceabilityRows.map(({ requirement, linkedTestCases, scenarioSummary, linkedScenarioTypes }) => {
							const isCovered = linkedTestCases.length > 0;
							return (
								<tr key={requirement.id} className={isCovered ? "covered" : "missing"}>
									<td>
										<strong>{requirement.id}</strong>
									</td>
									<td>
										{linkedTestCases.length ? (
											<LinkedCaseTags requirementId={requirement.id} cases={linkedTestCases} paths={paths} onOpenCase={onOpenCase} />
										) : (
											<span className="traceability-missing-text">No linked tests</span>
										)}
									</td>
									<td>
										{scenarioSummary &&
										scenarioSummary.planned_scenarios > 0 &&
										Number.isInteger(scenarioSummary.covered_scenarios) &&
										scenarioSummary.covered_scenarios >= 0 &&
										scenarioSummary.covered_scenarios <= scenarioSummary.planned_scenarios &&
										(linkedTestCases.length > 0 || scenarioSummary.covered_scenarios === 0)
											? `${scenarioSummary.covered_scenarios}/${scenarioSummary.planned_scenarios}`
											: "Unavailable"}
										{linkedScenarioTypes.length > 0 && (
											<div className="traceability-scenario-tags">
												{linkedScenarioTypes.slice(0, 4).map((scenario) => (
													<span key={`${requirement.id}-${scenario}`}>{scenario}</span>
												))}
											</div>
										)}
									</td>
									<td>
										<span className={`traceability-status ${isCovered ? "covered" : "missing"}`}>
											{isCovered ? "Tests linked" : "No linked tests"}
										</span>
									</td>
								</tr>
							);
						})}
					</tbody>
				</Table>
			</TableScroll>
		</Surface>
	);
}
