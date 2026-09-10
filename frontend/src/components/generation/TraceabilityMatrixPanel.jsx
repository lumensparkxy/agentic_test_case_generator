import { CollectionState, TableScroll, Table } from "../ui/collections";
import { Surface } from "../ui/surfaces";
import { getRequirementContextPath } from "../../utils/requirements";

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
			<div className="workflow-diagnostics-pills">
				<span className="workflow-diagnostics-pill">
					Approved requirements covered {tracedRequirementCount}/{approvedRequirements.length}
				</span>
				<span className="workflow-diagnostics-pill">
					Cases with traceability {coverageMetrics?.cases_with_traceability ?? 0}/{testCases.length}
				</span>
				<span className="workflow-diagnostics-pill">
					Scenario coverage {coverageMetrics?.covered_planned_scenarios ?? 0}/{coverageMetrics?.planned_scenarios_total ?? 0}
				</span>
			</div>
			<TableScroll className="traceability-table-wrapper" role="region" aria-label="Requirement traceability table" tabIndex={0}>
				<Table className="traceability-table">
					<thead>
						<tr>
							<th scope="col">Requirement</th>
							<th scope="col">Story / source path</th>
							<th scope="col">Linked test cases</th>
							<th scope="col">Scenario coverage</th>
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
										<span>{requirement.text}</span>
									</td>
									<td>{getRequirementContextPath(requirement)}</td>
									<td>
										{linkedTestCases.length ? (
											linkedTestCases.map((testCase) => (
												<span key={testCase.id} className="tag traceability-case-tag">
													{testCase.id}
												</span>
											))
										) : (
											<span className="traceability-missing-text">No linked tests</span>
										)}
									</td>
									<td>
										{scenarioSummary ? `${scenarioSummary.covered_scenarios}/${scenarioSummary.planned_scenarios}` : "—"}
										{linkedScenarioTypes.length > 0 && (
											<div className="traceability-scenario-tags">
												{linkedScenarioTypes.slice(0, 4).map((scenario) => (
													<span key={`${requirement.id}-${scenario}`}>{scenario}</span>
												))}
											</div>
										)}
									</td>
									<td>
										<span className={`traceability-status ${isCovered ? "covered" : "missing"}`}>{isCovered ? "Covered" : "Gap"}</span>
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
