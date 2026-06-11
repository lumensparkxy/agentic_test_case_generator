export default function ScenarioCoveragePanel({
	coveragePlan,
	coveredScenarioTotal,
	plannedScenarioTotal,
	mustHaveCoveredScenarioTotal,
	mustHaveScenarioTotal,
	missingScenarioCount,
	getRequirementScenarioSummary,
}) {
	if (coveragePlan.length === 0) {
		return (
			<div className="generate-result-empty">
				<h3>Scenario Coverage Plan</h3>
				<p>No scenario coverage plan is available for this run.</p>
			</div>
		);
	}

	return (
		<div className="result-section">
			<details className="collapsible-panel" open>
				<summary className="collapsible-panel-summary">
					<span className="collapsible-panel-copy">
						<span className="collapsible-panel-title">Scenario Coverage Plan</span>
						<span className="collapsible-panel-description">
							Planned scenario intent per requirement, available on demand instead of taking over the page.
						</span>
					</span>
					<span className="collapsible-panel-meta">
						<span className="analysis-summary-pill">{coveragePlan.length} requirements</span>
						<span className="analysis-summary-pill">Scenarios {coveredScenarioTotal}/{plannedScenarioTotal}</span>
						<span className="analysis-summary-pill">Must-have {mustHaveCoveredScenarioTotal}/{mustHaveScenarioTotal}</span>
						{missingScenarioCount > 0 && (
							<span className="analysis-summary-pill collapsible-pill-alert">Missing {missingScenarioCount}</span>
						)}
						<span className="collapsible-panel-icon" aria-hidden="true">⏄</span>
					</span>
				</summary>
				<div className="collapsible-panel-body">
					<div className="coverage-plan-list">
						{coveragePlan.map((plan) => {
							const summary = getRequirementScenarioSummary(plan.requirement_id);
							const missingScenarioTypes = new Set(summary?.missing_scenario_types || []);
							return (
								<div key={plan.requirement_id} className="coverage-plan-card">
									<div className="coverage-plan-header">
										<div>
											<div className="coverage-plan-id">{plan.requirement_id}</div>
											<div className="coverage-plan-text">{plan.requirement_text}</div>
										</div>
										{summary && (
											<span className="coverage-plan-summary">
												{summary.covered_scenarios}/{summary.planned_scenarios} planned scenarios covered
											</span>
										)}
									</div>
									<div className="coverage-chip-row">
										{plan.scenarios?.map((scenario) => {
											const isMissing = missingScenarioTypes.has(scenario.scenario_type);
											return (
												<span
													key={scenario.id}
													className={`coverage-chip ${scenario.must_have ? "required" : "recommended"} ${isMissing ? "missing" : "covered"}`}
													title={scenario.objective}
												>
													{scenario.scenario_type}
												</span>
											);
										})}
									</div>
								</div>
							);
						})}
					</div>
				</div>
			</details>
		</div>
	);
}
