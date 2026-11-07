import { CollectionState, TableScroll, Table } from "../ui/collections";
import { getTestCaseLinkedRequirementIds } from "../../utils/requirements";
import { Surface, Badge } from "../ui/surfaces";

export default function TraceabilityMatrixPanel({
	approvedRequirements,
	requirementTraceabilityRows,
	tracedRequirementCount,
	coverageMetrics,
	testCases,
}) {
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
					Cases with traceability{" "}
					{
						testCases.filter((item) =>
							getTestCaseLinkedRequirementIds(item).some((id) => approvedRequirements.some((requirement) => requirement.id === id))
						).length
					}
					/{testCases.length}
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
			<TableScroll className="traceability-table-wrapper" role="region" aria-label="Requirement traceability table" tabIndex={0}>
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
											linkedTestCases.map((testCase) => (
												<Badge key={testCase.id} icon={null} tone="neutral" className="tag traceability-case-tag">
													{testCase.id}
												</Badge>
											))
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
