import useReviewDraft from "../../hooks/useReviewDraft";
import DraftRecoveryNotice from "../reviews/DraftRecoveryNotice";
import { Table, TableScroll, ListDetail, ResultCount, List, SelectableItem, ListItem, CollectionState } from "../ui/collections";
import { Input, Button } from "../ui/controls";
import { Disclosure, Badge } from "../ui/surfaces";
import { useId, useEffect, useRef } from "react";
import { ChevronRight } from "lucide-react";
import { getTestCaseLinkedRequirementIds } from "../../utils/requirements";

export default function GeneratedTestCasesView({
	testCases,
	identity,
	projectId,
	snapshotId,
	onReport,
	selectedId,
	setSelectedId,
	qualityIssues = [],
	requirements = [],
	substantiveAssessment = null,
	changedIds = null,
	onClearChanged,
}) {
	const recovery = useReviewDraft({
		identity,
		projectId,
		snapshotId,
		name: "test-review",
		initialValue: { query: "", selectedId: "", scrollTop: 0, changedIds: null },
	});
	const query = recovery.value.query;
	const setQuery = (query) => recovery.setValue((old) => ({ ...old, query }));
	const listRef = useRef(null);
	useEffect(() => {
		if (listRef.current) listRef.current.scrollTop = recovery.value.scrollTop || 0;
	}, [snapshotId]);
	const searchId = useId();
	const visibleChanges = changedIds ?? recovery.value.changedIds ?? null;
	useEffect(() => {
		if (changedIds !== null) recovery.setValue((old) => ({ ...old, changedIds }));
	}, [changedIds]);
	const cases = testCases.filter(
		(tc) =>
			(visibleChanges === null || visibleChanges.includes(tc.id)) &&
			[
				tc.id,
				tc.title,
				tc.description,
				tc.expected_result,
				...getTestCaseLinkedRequirementIds(tc),
				...(tc.steps || []).flatMap((step) => [step.action, step.expected, step.test_data]),
			]
				.join(" ")
				.toLowerCase()
				.includes(query.toLowerCase().trim())
	);
	const selected = cases.find((tc) => tc.id === (selectedId || recovery.value.selectedId)) || cases[0];
	const steps = selected?.steps || [];
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
					<DraftRecoveryNotice draft={recovery} />
					<h2 className="sr-only">Generated Test Cases</h2>
					{visibleChanges !== null && (
						<div role="status">
							<p>Showing changed tests</p>
							<Button
								variant="secondary"
								onClick={() => {
									recovery.setValue((old) => ({ ...old, changedIds: null }));
									onClearChanged?.();
								}}
							>
								Show all tests
							</Button>
						</div>
					)}
					<div className="compact-test-list-tools">
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
					</div>
					<List
						as="div"
						variant="grouped"
						className="test-review-items"
						ref={listRef}
						onScroll={(event) => recovery.setValue((old) => ({ ...old, scrollTop: event.currentTarget.scrollTop }))}
					>
						{cases.map((tc) => (
							<SelectableItem
								key={tc.id}
								type="button"
								className={`test-review-item ${selected?.id === tc.id ? "selected" : ""}`}
								aria-pressed={selected?.id === tc.id}
								aria-controls="selected-test-case"
								onClick={() => {
									setSelectedId(tc.id);
									recovery.setValue((old) => ({ ...old, selectedId: tc.id }));
								}}
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
							<p>
								{visibleChanges?.length === 0
									? "This application preserved all tests. There are no changed tests to review."
									: testCases.length
										? "No test cases match your search."
										: "No test cases generated yet."}
							</p>
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
							<div className="test-detail-title">
								<h2>{selected.title}</h2>
								{onReport && (
									<Button variant="secondary" size="compact" onClick={() => onReport(selected)}>
										Report a problem
									</Button>
								)}
							</div>
							<div className="test-steps-heading">
								<h3>Steps and expected results</h3>
								<span>
									{steps.length} {steps.length === 1 ? "step" : "steps"}
								</span>
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
										{steps.map((step, index) => (
											<tr key={`${selected.id}-${index}`}>
												<th scope="row">{index + 1}</th>
												<td>
													{step.action}
													{step.test_data && (
														<Disclosure className="test-step-data">
															<summary>Test data</summary>
															<p>{step.test_data}</p>
														</Disclosure>
													)}
												</td>
												<td>{step.expected || "Not specified"}</td>
											</tr>
										))}
									</tbody>
								</Table>
							</TableScroll>
							<h3>Expected result</h3>
							<p>{selected.expected_result || "Not specified."}</p>
							<div className="test-case-support">
								<Disclosure>
									<summary>Preconditions</summary>
									<p>{selected.preconditions || "None specified."}</p>
									{selected.description && <p>{selected.description}</p>}
								</Disclosure>
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
								<Disclosure className="test-source-evidence">
									<summary>Why this test? Source and assessment</summary>
									{getTestCaseLinkedRequirementIds(selected).map((id) => {
										const requirement = requirements.find((item) => item.id === id);
										return (
											<div key={id}>
												<strong>{id}</strong>
												<p>{requirement?.text || "Requirement text unavailable in the current project."}</p>
											</div>
										);
									})}
									<p>Current requirement text is shown for comparison. It may have changed since this test was generated.</p>
									{(substantiveAssessment?.obligations || [])
										.filter((item) => (item.evidence || []).some((evidence) => evidence.test_case_id === selected.id))
										.map((item, index) => (
											<div key={index}>
												<strong>
													{item.obligation} — {item.status}
												</strong>
												<blockquote>{item.source_quote}</blockquote>
												<p>{item.reason}</p>
												{(item.evidence || [])
													.filter((evidence) => evidence.test_case_id === selected.id)
													.map((evidence, i) => (
														<p key={i}>
															Step {evidence.step}: {evidence.action}
															{evidence.expected ? ` → ${evidence.expected}` : ""}
														</p>
													))}
											</div>
										))}
									{!(substantiveAssessment?.obligations || []).some((item) =>
										(item.evidence || []).some((evidence) => evidence.test_case_id === selected.id)
									) && (
										<p>No source-to-step assessment is available for this test. A requirement link alone does not establish coverage.</p>
									)}
								</Disclosure>
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
							</div>
						</>
					) : (
						<p>Select a test case to review its details.</p>
					)}
				</section>
			</ListDetail>
		</>
	);
}
