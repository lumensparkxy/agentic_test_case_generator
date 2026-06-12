import { getTestCaseLinkedRequirementIds } from "../../utils/requirements";

const getPriorityClass = (priority) => {
	const map = { Critical: "priority-critical", High: "priority-high", Medium: "priority-medium", Low: "priority-low" };
	return map[priority] || "";
};

const getStatusClass = (status) => {
	const map = { Draft: "status-draft", Ready: "status-ready", "In Review": "status-review", Approved: "status-approved" };
	return map[status] || "";
};

export default function GeneratedTestCasesView({
	testCases,
	templateFormat,
	expandedRows,
	onToggleRowExpansion,
	feedback,
	onFeedbackChange,
	onRefineTestCases,
	isGenerating,
	testCaseActionDisabled,
}) {
	return (
		<>
			<div className="result-section generate-result-section">
				<h3>Generated Test Cases</h3>
				{testCases.length === 0 ? (
					<span className="helper-text">No test cases generated yet.</span>
				) : templateFormat === "table" ? (
					<div className="test-cases-table-wrapper">
						<table className="test-cases-table">
							<thead>
								<tr>
									<th className="col-id">ID</th>
									<th className="col-title">Title</th>
									<th className="col-priority">Priority</th>
									<th className="col-type">Type</th>
									<th className="col-status">Status</th>
									<th className="col-preconditions">Preconditions</th>
									<th className="col-steps">Steps</th>
									<th className="col-expected">Expected Result</th>
									<th className="col-testdata">Test Data</th>
									<th className="col-time">Est. Time</th>
									<th className="col-automation">Automation</th>
									<th className="col-component">Component</th>
									<th className="col-tags">Linked Reqs</th>
									<th className="col-tags">Tags</th>
								</tr>
							</thead>
							<tbody>
								{testCases.map((tc) => (
									<tr key={tc.id} className={expandedRows[tc.id] ? "expanded" : ""} onClick={() => onToggleRowExpansion(tc.id)}>
										<td className="tc-id">{tc.id}</td>
										<td className="tc-title">
											<div className="title-cell">
												<span className="expand-icon">{expandedRows[tc.id] ? "▼" : "▶"}</span>
												{tc.title}
											</div>
											{tc.description && <div className="tc-description">{tc.description}</div>}
										</td>
										<td className="tc-priority">
											<span className={`priority-badge ${getPriorityClass(tc.priority)}`}>{tc.priority || "Medium"}</span>
										</td>
										<td className="tc-type">{tc.type || "Functional"}</td>
										<td className="tc-status">
											<span className={`status-badge ${getStatusClass(tc.status)}`}>{tc.status || "Draft"}</span>
										</td>
										<td className="tc-preconditions">{tc.preconditions || "-"}</td>
										<td className="tc-steps">
											<ol>
												{tc.steps?.slice(0, expandedRows[tc.id] ? undefined : 2).map((step, index) => (
													<li key={`${tc.id}-step-${step.step || index + 1}`}>
														<strong>{step.action}</strong>
														<span className="step-expected">→ {step.expected}</span>
														{step.test_data && <span className="step-data">📋 {step.test_data}</span>}
													</li>
												))}
												{!expandedRows[tc.id] && tc.steps?.length > 2 && (
													<li className="more-steps">+{tc.steps.length - 2} more steps...</li>
												)}
											</ol>
										</td>
										<td className="tc-expected-result">{tc.expected_result || "-"}</td>
										<td className="tc-testdata">{tc.test_data || "-"}</td>
										<td className="tc-time">{tc.estimated_time || "-"}</td>
										<td className="tc-automation">
											<span className={`automation-badge ${tc.automation_status?.replace(/\s/g, "-").toLowerCase() || "manual"}`}>
												{tc.automation_status || "Manual"}
											</span>
										</td>
										<td className="tc-component">{tc.component || "-"}</td>
										<td className="tc-tags">
											{getTestCaseLinkedRequirementIds(tc).map((requirementId) => (
												<span key={`${tc.id}-${requirementId}`} className="tag traceability-case-tag">
													{requirementId}
												</span>
											))}
										</td>
										<td className="tc-tags">
											{tc.tags?.map((tag) => (
												<span key={tag} className="tag">
													{tag}
												</span>
											))}
										</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				) : (
					<div className="test-cases-grid">
						{testCases.map((tc) => {
							const linkedRequirementIds = getTestCaseLinkedRequirementIds(tc);
							return (
								<div key={tc.id} className="case-card">
									<div className="case-header">
										<span className="case-id">{tc.id}</span>
										<span className="case-title">{tc.title}</span>
										<span className={`priority-badge ${getPriorityClass(tc.priority)}`}>{tc.priority}</span>
									</div>
									{tc.description && <div className="case-description">{tc.description}</div>}
									<div className="case-meta">
										<span className="meta-item">
											<strong>Type:</strong> {tc.type}
										</span>
										<span className={`status-badge ${getStatusClass(tc.status)}`}>{tc.status}</span>
										<span className="meta-item">
											<strong>Est:</strong> {tc.estimated_time}
										</span>
									</div>
									{linkedRequirementIds.length > 0 && (
										<div className="case-tags traceability-links">
											{linkedRequirementIds.map((requirementId) => (
												<span key={`${tc.id}-linked-${requirementId}`} className="tag traceability-case-tag">
													{requirementId}
												</span>
											))}
										</div>
									)}
									{tc.preconditions && <div className="case-preconditions">{tc.preconditions}</div>}
									<div className="case-steps">
										<strong>Steps</strong>
										<ol>
											{tc.steps?.map((step, index) => (
												<li key={`${tc.id}-card-step-${step.step || index + 1}`}>
													<span className="step-action">
														{step.step || index + 1}. {step.action}
													</span>
													<span className="step-expected">→ {step.expected}</span>
													{step.test_data && <span className="step-data">📋 {step.test_data}</span>}
												</li>
											))}
										</ol>
									</div>
									{tc.expected_result && (
										<div className="case-expected">
											<strong>Expected Result:</strong> {tc.expected_result}
										</div>
									)}
									{tc.tags && tc.tags.length > 0 && (
										<div className="case-tags">
											{tc.tags.map((tag) => (
												<span key={tag} className="tag">
													{tag}
												</span>
											))}
										</div>
									)}
								</div>
							);
						})}
					</div>
				)}
			</div>

			{testCases.length > 0 && (
				<div className="feedback-section">
					<h3>Human Feedback</h3>
					<p className="feedback-description">Provide feedback on the generated test cases. The AI will refine them based on your input.</p>
					<textarea
						className="feedback-textarea"
						placeholder="Enter your feedback here... e.g., 'Add more negative test cases for upload feature', 'TC-003 needs more detailed steps', 'Include security test cases', etc."
						value={feedback}
						onChange={(event) => onFeedbackChange(event.target.value)}
						rows={4}
					/>
					<div className="feedback-actions">
						<button
							onClick={onRefineTestCases}
							disabled={!feedback.trim() || isGenerating || testCaseActionDisabled}
							className="feedback-button"
						>
							{isGenerating ? "⏳ Updating Test Cases..." : "🔄 Implement Changes"}
						</button>
					</div>
				</div>
			)}
		</>
	);
}
