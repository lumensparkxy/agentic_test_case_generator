import { useId, useState } from "react";
import { ChevronRight } from "lucide-react";
import { getTestCaseLinkedRequirementIds } from "../../utils/requirements";

export default function GeneratedTestCasesView({
	testCases,
	expandedRows,
	onToggleRowExpansion,
	feedback,
	onFeedbackChange,
	onRefineTestCases,
	isGenerating,
	testCaseActionDisabled,
	allowRefinement = true,
	qualityIssues = [],
}) {
	const [selectedId, setSelectedId] = useState(null);
	const [query, setQuery] = useState("");
	const searchId = useId();
	const feedbackId = useId();
	const cases = testCases.filter((tc) =>
		[tc.id, tc.title, ...getTestCaseLinkedRequirementIds(tc)].join(" ").toLowerCase().includes(query.toLowerCase().trim())
	);
	const selected = cases.find((tc) => tc.id === selectedId) || cases[0];
	const steps = selected?.steps || [];
	const expanded = Boolean(expandedRows[selected?.id]);
	const findings = selected
		? qualityIssues.filter((issue) => new RegExp(`\\b${selected.id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(issue))
		: [];
	return (
		<>
			<section className="test-review-workspace" aria-label="Generated test cases">
				<div className="test-review-list">
					<h2>Generated Test Cases</h2>
					<label htmlFor={searchId} className="sr-only">
						Search test cases
					</label>
					<input
						id={searchId}
						type="search"
						placeholder="Search test cases"
						value={query}
						onChange={(event) => setQuery(event.target.value)}
					/>
					<p className="test-list-count" role="status">
						{cases.length} of {testCases.length} test cases
					</p>
					<div className="test-review-items">
						{cases.map((tc) => (
							<button
								key={tc.id}
								type="button"
								className={`test-review-item ${selected?.id === tc.id ? "selected" : ""}`}
								aria-pressed={selected?.id === tc.id}
								aria-controls="selected-test-case"
								onClick={() => setSelectedId(tc.id)}
							>
								<span>
									<small>{tc.id}</small>
									<strong>{tc.title}</strong>
									<span>
										{tc.priority || "Medium"} priority · {getTestCaseLinkedRequirementIds(tc).join(", ") || "No linked requirements"}
									</span>
								</span>
								<ChevronRight size={18} aria-hidden="true" />
							</button>
						))}
					</div>
					{!cases.length && <p>{testCases.length ? "No test cases match your search." : "No test cases generated yet."}</p>}
				</div>
				<section className="test-review-detail" id="selected-test-case" aria-label="Selected test case" aria-live="polite">
					{selected ? (
						<>
							<span className="case-id">{selected.id}</span>
							<p className="test-detail-meta">
								{selected.type || "Functional"} · {selected.priority || "Medium"} priority ·{" "}
								{getTestCaseLinkedRequirementIds(selected).join(", ")}
							</p>
							<h2>{selected.title}</h2>
							{selected.description && <p>{selected.description}</p>}
							{findings.length > 0 && (
								<details className="test-inline-findings">
									<summary>Quality findings for {selected.id}</summary>
									<ul>
										{findings.map((issue) => (
											<li key={issue}>{issue}</li>
										))}
									</ul>
								</details>
							)}
							<h3>Preconditions</h3>
							<p>{selected.preconditions || "None specified."}</p>
							<div className="test-steps-heading">
								<h3>Steps and expected results</h3>
								<span>{steps.length} steps</span>
							</div>
							<ol className="test-detail-steps">
								{steps.slice(0, expanded ? undefined : 2).map((step, index) => (
									<li key={`${selected.id}-${index}`}>
										<p>{step.action}</p>
										<p>
											<strong>Expected</strong> {step.expected || "Not specified"}
										</p>
										{step.test_data && (
											<p>
												<strong>Test data</strong> {step.test_data}
											</p>
										)}
									</li>
								))}
							</ol>
							{steps.length > 2 && (
								<button
									className="secondary small"
									type="button"
									aria-expanded={expanded}
									onClick={() => onToggleRowExpansion(selected.id)}
								>
									{expanded ? "Show fewer steps" : `Show all ${steps.length} steps`}
								</button>
							)}
							<h3>Expected result</h3>
							<p>{selected.expected_result || "Not specified."}</p>
							<details className="test-case-metadata">
								<summary>Test data and metadata</summary>
								<dl>
									{Object.entries({
										"Test data": selected.test_data,
										"Estimated time": selected.estimated_time,
										"Case status": selected.status || "Draft",
										Automation: selected.automation_status || "Manual",
										Component: selected.component,
										"Linked requirements": getTestCaseLinkedRequirementIds(selected).join(", "),
										Tags: selected.tags?.join(", "),
									}).map(([label, value]) => (
										<div key={label}>
											<dt>{label}</dt>
											<dd>{value || "Not specified"}</dd>
										</div>
									))}
								</dl>
								<p>Case status and automation intent do not indicate quality approval or execution readiness.</p>
							</details>
						</>
					) : (
						<p>Select a test case to review its details.</p>
					)}
				</section>
			</section>
			{testCases.length > 0 && allowRefinement && (
				<section className="feedback-section">
					<h3>Human Feedback</h3>
					<label htmlFor={feedbackId}>Changes to the test suite</label>
					<textarea
						id={feedbackId}
						className="feedback-textarea"
						placeholder="Describe the changes needed…"
						value={feedback}
						onChange={(event) => onFeedbackChange(event.target.value)}
						rows={4}
					/>
					<button onClick={onRefineTestCases} disabled={!feedback.trim() || isGenerating || testCaseActionDisabled}>
						{isGenerating ? "Updating test cases…" : "Implement Changes"}
					</button>
				</section>
			)}
		</>
	);
}
