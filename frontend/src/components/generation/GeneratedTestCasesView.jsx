import { Table, TableScroll, ListDetail, ResultCount, List, SelectableItem, ListItem, CollectionState } from "../ui/collections";
import { Input, Button, Textarea } from "../ui/controls";
import { Disclosure, Badge } from "../ui/surfaces";
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
			<ListDetail
				storageKey="test-cases"
				label="Resize test case list and details"
				className="test-review-workspace"
				aria-label="Generated test cases"
			>
				<div className="test-review-list">
					<h2>Generated Test Cases</h2>
					<label htmlFor={searchId} className="sr-only">
						Search test cases
					</label>
					<Input
						id={searchId}
						type="search"
						placeholder="Search test cases"
						value={query}
						onChange={(event) => setQuery(event.target.value)}
					/>
					<ResultCount as="p" className="test-list-count" role="status">
						{cases.length} of {testCases.length} test cases
					</ResultCount>
					<List as="div" variant="grouped" className="test-review-items">
						{cases.map((tc) => (
							<SelectableItem
								key={tc.id}
								type="button"
								className={`test-review-item ${selected?.id === tc.id ? "selected" : ""}`}
								aria-pressed={selected?.id === tc.id}
								aria-controls="selected-test-case"
								onClick={() => setSelectedId(tc.id)}
							>
								<span>
									<small>{tc.id}</small> <strong>{tc.title}</strong>
								</span>
								<ChevronRight size={18} aria-hidden="true" />
							</SelectableItem>
						))}
					</List>
					{!cases.length && (
						<CollectionState kind={testCases.length ? "filtered" : "empty"}>
							<p>{testCases.length ? "No test cases match your search." : "No test cases generated yet."}</p>
							{testCases.length > 0 && (
								<Button variant="secondary" size="compact" onClick={() => setQuery("")}>
									Clear search
								</Button>
							)}
						</CollectionState>
					)}
				</div>
				<section className="test-review-detail" id="selected-test-case" aria-label="Selected test case" aria-live="polite">
					{selected ? (
						<>
							<Badge icon={null} tone="info" className="case-id">
								{selected.id}
							</Badge>
							<p className="test-detail-meta">
								{selected.type || "Functional"} · {selected.priority || "Medium"} priority ·{" "}
								{getTestCaseLinkedRequirementIds(selected).join(", ")}
							</p>
							<h2>{selected.title}</h2>
							{selected.description && <p>{selected.description}</p>}
							{findings.length > 0 && (
								<Disclosure className="test-inline-findings">
									<summary>Quality findings for {selected.id}</summary>
									<List>
										{findings.map((issue) => (
											<ListItem key={issue}>{issue}</ListItem>
										))}
									</List>
								</Disclosure>
							)}
							<h3>Preconditions</h3>
							<p>{selected.preconditions || "None specified."}</p>
							<div className="test-steps-heading">
								<h3>Steps and expected results</h3>
								<span>{steps.length} steps</span>
							</div>
							<TableScroll aria-label="Test steps and expected results">
								<Table density="compact" className="test-detail-steps">
									<colgroup>
										<col className="test-step-number-column" />
										<col />
										<col />
									</colgroup>
									<thead>
										<tr>
											<th scope="col">Step</th>
											<th scope="col">Action</th>
											<th scope="col">Expected result</th>
										</tr>
									</thead>
									<tbody>
										{steps.slice(0, expanded ? undefined : 2).map((step, index) => (
											<tr key={`${selected.id}-${index}`}>
												<th scope="row">{index + 1}</th>
												<td>
													{step.action}
													{step.test_data && (
														<p className="test-step-data">
															<strong>Test data:</strong> {step.test_data}
														</p>
													)}
												</td>
												<td>{step.expected || "Not specified"}</td>
											</tr>
										))}
									</tbody>
								</Table>
							</TableScroll>
							{steps.length > 2 && (
								<Button
									className="secondary small"
									type="button"
									aria-expanded={expanded}
									onClick={() => onToggleRowExpansion(selected.id)}
								>
									{expanded ? "Show fewer steps" : `Show all ${steps.length} steps`}
								</Button>
							)}
							<h3>Expected result</h3>
							<p>{selected.expected_result || "Not specified."}</p>
							<Disclosure className="test-case-metadata">
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
							</Disclosure>
						</>
					) : (
						<p>Select a test case to review its details.</p>
					)}
				</section>
			</ListDetail>
			{testCases.length > 0 && allowRefinement && (
				<section className="feedback-section">
					<h3>Human Feedback</h3>
					<label htmlFor={feedbackId}>Changes to the test suite</label>
					<Textarea
						id={feedbackId}
						className="feedback-textarea"
						placeholder="Describe the changes needed…"
						value={feedback}
						onChange={(event) => onFeedbackChange(event.target.value)}
						rows={4}
					/>
					<Button onClick={onRefineTestCases} disabled={!feedback.trim() || isGenerating || testCaseActionDisabled}>
						{isGenerating ? "Updating test cases…" : "Implement Changes"}
					</Button>
				</section>
			)}
		</>
	);
}
