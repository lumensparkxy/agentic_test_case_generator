export default function RequirementAnalysisPanel({
	requirementAnalysis,
	coverageMetrics,
	requirementAnalysisGapCount,
	getRequirementAnalysisSummary,
	getRequirementAnalysisGaps,
}) {
	if (requirementAnalysis.length === 0) {
		return (
			<div className="generate-result-empty">
				<h3>Requirement Analysis</h3>
				<p>No requirement analysis is available for this run.</p>
			</div>
		);
	}

	return (
		<div className="result-section">
			<details className="collapsible-panel" open>
				<summary className="collapsible-panel-summary">
					<span className="collapsible-panel-copy">
						<span className="collapsible-panel-title">Requirement Analysis</span>
						<span className="collapsible-panel-description">
							Rules, constraints, permissions, transitions, and risks extracted before scenario planning.
						</span>
					</span>
					<span className="collapsible-panel-meta">
						<span className="analysis-summary-pill">{requirementAnalysis.length} requirements</span>
						{coverageMetrics && (
							<>
								<span className="analysis-summary-pill">
									Rules {coverageMetrics.business_rules_covered || 0}/{coverageMetrics.business_rules_total || 0}
								</span>
								<span className="analysis-summary-pill">
									Constraints {coverageMetrics.field_constraints_covered || 0}/{coverageMetrics.field_constraints_total || 0}
								</span>
							</>
						)}
						{requirementAnalysisGapCount > 0 && (
							<span className="analysis-summary-pill collapsible-pill-alert">Gaps {requirementAnalysisGapCount}</span>
						)}
						<span className="collapsible-panel-icon" aria-hidden="true">
							⏄
						</span>
					</span>
				</summary>
				<div className="collapsible-panel-body">
					{coverageMetrics && (
						<div className="analysis-overview-row">
							<span className="analysis-summary-pill">
								Rules {coverageMetrics.business_rules_covered || 0}/{coverageMetrics.business_rules_total || 0}
							</span>
							<span className="analysis-summary-pill">
								Constraints {coverageMetrics.field_constraints_covered || 0}/{coverageMetrics.field_constraints_total || 0}
							</span>
							<span className="analysis-summary-pill">
								Permissions {coverageMetrics.role_permissions_covered || 0}/{coverageMetrics.role_permissions_total || 0}
							</span>
							<span className="analysis-summary-pill">
								Transitions {coverageMetrics.state_transitions_covered || 0}/{coverageMetrics.state_transitions_total || 0}
							</span>
							<span className="analysis-summary-pill">
								Risks {coverageMetrics.risk_signals_covered || 0}/{coverageMetrics.risk_signals_total || 0}
							</span>
						</div>
					)}
					<div className="analysis-card-list">
						{requirementAnalysis.map((analysis) => {
							const summary = getRequirementAnalysisSummary(analysis.requirement_id);
							const gaps = getRequirementAnalysisGaps(analysis.requirement_id);
							const hasGaps = Object.values(gaps).some((items) => items.length > 0);
							return (
								<div key={analysis.requirement_id} className="analysis-card">
									<div className="analysis-card-header">
										<div>
											<div className="coverage-plan-id">{analysis.requirement_id}</div>
											<div className="coverage-plan-text">{analysis.requirement_text}</div>
										</div>
										{summary && (
											<span className="coverage-plan-summary">
												{summary.business_rules_covered}/{summary.business_rules_total} rules • {summary.field_constraints_covered}/
												{summary.field_constraints_total} constraints
											</span>
										)}
									</div>
									<div className="analysis-summary-row">
										<span className="analysis-summary-pill">Rules {analysis.business_rules?.length || 0}</span>
										<span className="analysis-summary-pill">Constraints {analysis.field_constraints?.length || 0}</span>
										<span className="analysis-summary-pill">Permissions {analysis.role_permissions?.length || 0}</span>
										<span className="analysis-summary-pill">Transitions {analysis.state_transitions?.length || 0}</span>
										<span className="analysis-summary-pill">Risks {analysis.risk_signals?.length || 0}</span>
									</div>
									{analysis.suggested_scenarios?.length > 0 && (
										<div className="analysis-chip-row">
											{analysis.suggested_scenarios.map((scenario) => (
												<span key={`${analysis.requirement_id}-${scenario}`} className="analysis-chip">
													{scenario}
												</span>
											))}
										</div>
									)}
									<div className="analysis-detail-grid">
										<div className="analysis-detail-block">
											<h4>Business rules</h4>
											<ul className="analysis-detail-list">
												{(analysis.business_rules || []).slice(0, 2).map((rule) => (
													<li key={rule.id}>{rule.title}</li>
												))}
											</ul>
										</div>
										<div className="analysis-detail-block">
											<h4>Constraints</h4>
											<ul className="analysis-detail-list">
												{(analysis.field_constraints || []).slice(0, 2).map((constraint) => (
													<li key={constraint.id}>
														{constraint.field_name}: {constraint.description}
													</li>
												))}
											</ul>
										</div>
										<div className="analysis-detail-block">
											<h4>Permissions</h4>
											<ul className="analysis-detail-list">
												{(analysis.role_permissions || []).slice(0, 2).map((permission) => (
													<li key={permission.id}>
														{permission.role}: {permission.action}
													</li>
												))}
											</ul>
										</div>
										<div className="analysis-detail-block">
											<h4>Transitions</h4>
											<ul className="analysis-detail-list">
												{(analysis.state_transitions || []).slice(0, 2).map((transition) => (
													<li key={transition.id}>
														{transition.from_state} → {transition.to_state}
													</li>
												))}
											</ul>
										</div>
										<div className="analysis-detail-block">
											<h4>Risks</h4>
											<ul className="analysis-detail-list">
												{(analysis.risk_signals || []).slice(0, 2).map((risk) => (
													<li key={risk.id}>
														{risk.severity}: {risk.title}
													</li>
												))}
											</ul>
										</div>
									</div>
									{hasGaps && (
										<div className="analysis-gap-block">
											<strong>Coverage gaps</strong>
											<ul className="analysis-gap-list">
												{gaps.highRisks.slice(0, 2).map((item) => (
													<li key={item}>{item}</li>
												))}
												{gaps.rules.slice(0, 2).map((item) => (
													<li key={item}>{item}</li>
												))}
												{gaps.constraints.slice(0, 2).map((item) => (
													<li key={item}>{item}</li>
												))}
												{gaps.permissions.slice(0, 2).map((item) => (
													<li key={item}>{item}</li>
												))}
												{gaps.transitions.slice(0, 2).map((item) => (
													<li key={item}>{item}</li>
												))}
											</ul>
										</div>
									)}
								</div>
							);
						})}
					</div>
				</div>
			</details>
		</div>
	);
}
