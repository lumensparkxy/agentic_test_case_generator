import useReviewDraft from "../../hooks/useReviewDraft";
import DraftRecoveryNotice from "../reviews/DraftRecoveryNotice";
import { Table, TableScroll, ListDetail, ResultCount, List, SelectableItem, ListItem, CollectionState } from "../ui/collections";
import { Input, Button, Select } from "../ui/controls";
import { Disclosure, Badge } from "../ui/surfaces";
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";
import { createSavedCasePathResolver } from "../../app/savedCaseLinks";
import { getTestCaseLinkedRequirementIds } from "../../utils/requirements";

const CASE_PRIORITIES = ["Critical", "High", "Medium", "Low"];
const AUTOMATION_INTENTS = ["Manual", "To Be Automated", "Automated"];
const casePriority = (testCase) => testCase.priority || "Medium";
const caseAutomationIntent = (testCase) => testCase.automation_status || "Manual";

function revealCaseRow(list, index) {
	const row = list?.children[index];
	if (!row) return;
	const top = list.getBoundingClientRect().top + list.clientTop;
	const bottom = top + list.clientHeight;
	const bounds = row.getBoundingClientRect();
	if (bounds.top < top) list.scrollTop += bounds.top - top;
	else if (bounds.bottom > bottom) list.scrollTop += bounds.bottom - bottom;
}

function TestStepData({ value }) {
	const [escapedOpen, setEscapedOpen] = useState(false);
	if (value === "") return <p className="test-step-data">Test data is an empty string.</p>;

	return (
		<Disclosure className="test-step-data">
			<summary>Test data</summary>
			<p className="test-step-data-value" tabIndex={0}>
				{value}
			</p>
			<Disclosure onToggle={(event) => setEscapedOpen(event.currentTarget.open)}>
				<summary>Escaped JSON</summary>
				{escapedOpen && (
					<pre className="test-step-data-value" tabIndex={0}>
						{JSON.stringify(value).replace(/[^\u0020-\u007e]/g, (unit) => `\\u${unit.charCodeAt(0).toString(16).padStart(4, "0")}`)}
					</pre>
				)}
			</Disclosure>
		</Disclosure>
	);
}

function CaseReaderLayout({ singleCase, children, ...props }) {
	return singleCase ? children[1] : <ListDetail {...props}>{children}</ListDetail>;
}

function CaseLinkControl({ path }) {
	const [copyStatus, setCopyStatus] = useState("");
	const inputRef = useRef(null);
	const url = new URL(path, window.location.href).href;
	useEffect(() => {
		if (copyStatus === "manual") inputRef.current?.focus();
	}, [copyStatus]);
	const copyLink = async () => {
		try {
			await navigator.clipboard.writeText(url);
			setCopyStatus("copied");
		} catch {
			setCopyStatus("manual");
		}
	};
	return (
		<div className="test-case-link-control">
			<Button variant="secondary" size="compact" onClick={copyLink}>
				Copy case link
			</Button>
			{copyStatus === "copied" && <span role="status">Case link copied.</span>}
			{copyStatus === "manual" && (
				<label>
					Select and copy the case link
					<Input ref={inputRef} value={url} readOnly onFocus={(event) => event.currentTarget.select()} />
				</label>
			)}
		</div>
	);
}

export default function GeneratedTestCasesView({
	testCases,
	identity,
	projectId,
	snapshotId,
	savedSuite,
	exactCasePath = "",
	singleCase = false,
	savedStep = null,
	onReport,
	onReadCase,
	renderSelectedCaseRepair,
	selectedId,
	setSelectedId,
	findingCaseId,
	onDismissFindingCase,
	priorityCaseId,
	focusPriorityCase = false,
	onPriorityCaseOpened,
	onDismissPriorityCase,
	qualityIssues = [],
	requirements = [],
	substantiveAssessment = null,
	changedIds = null,
	onClearChanged,
	reviewGroup = null,
	onClearReviewGroup,
}) {
	const recovery = useReviewDraft({
		identity,
		projectId,
		snapshotId,
		name: reviewGroup ? `test-review:${reviewGroup.key}` : "test-review",
		initialValue: { query: "", selectedId: "", scrollTop: 0, changedIds: null },
	});
	const query = recovery.value.query ?? "";
	const dismissOpenedCase = () => {
		if (priorityCaseId) onDismissPriorityCase();
		if (findingCaseId) onDismissFindingCase();
	};
	const setQuery = (query) => {
		dismissOpenedCase();
		recovery.setValue((old) => ({ ...old, query }));
	};
	const listRef = useRef(null);
	const searchRef = useRef(null);
	const detailRef = useRef(null);
	const titleRef = useRef(null);
	const focusAfterMove = useRef(false);
	useEffect(() => {
		if (listRef.current) listRef.current.scrollTop = recovery.value.scrollTop || 0;
	}, [snapshotId]);
	const searchId = useId();
	const positionId = `${searchId}-position`;
	const [filtersOpen, setFiltersOpen] = useState(false);
	const visibleChanges = reviewGroup ? null : (changedIds ?? recovery.value.changedIds ?? null);
	useEffect(() => {
		if (changedIds !== null) recovery.setValue((old) => ({ ...old, changedIds }));
	}, [changedIds]);
	const filterScope = visibleChanges === null ? "suite" : "changed";
	const savedFilters = recovery.value.caseFilters?.[filterScope];
	const priority = CASE_PRIORITIES.includes(savedFilters?.priority) ? savedFilters.priority : "";
	const automationIntent = AUTOMATION_INTENTS.includes(savedFilters?.automationIntent) ? savedFilters.automationIntent : "";
	const activeFilterCount = Number(Boolean(priority)) + Number(Boolean(automationIntent));
	const hasCaseFilters = activeFilterCount > 0;
	const saveFilters = (filters, clearSearch = false) => {
		dismissOpenedCase();
		recovery.setValue((old) => ({
			...old,
			query: clearSearch ? "" : old.query,
			caseFilters: { ...old.caseFilters, [filterScope]: filters },
		}));
	};
	const clearFilters = () => {
		saveFilters({}, true);
		searchRef.current?.focus();
	};
	const groupIds = reviewGroup?.caseIds;
	const normalizedQuery = query.trim().toLowerCase();
	const cases = useMemo(() => {
		const scopedIds = groupIds ?? visibleChanges;
		const scope = scopedIds ? new Set(scopedIds) : null;
		return testCases.filter((tc) => {
			if (scope && !scope.has(tc.id)) return false;
			if (priority && casePriority(tc) !== priority) return false;
			if (automationIntent && caseAutomationIntent(tc) !== automationIntent) return false;
			if (!normalizedQuery) return true;
			return [
				tc.id,
				tc.title,
				tc.description,
				tc.expected_result,
				casePriority(tc),
				caseAutomationIntent(tc),
				...getTestCaseLinkedRequirementIds(tc),
				...(tc.steps || []).flatMap((step) => [step.action, step.expected, step.test_data]),
			]
				.join(" ")
				.toLowerCase()
				.includes(normalizedQuery);
		});
	}, [testCases, groupIds, visibleChanges, priority, automationIntent, normalizedQuery]);
	const findingCase = findingCaseId ? testCases.find((tc) => tc.id === findingCaseId) : null;
	const priorityCase = priorityCaseId ? testCases.find((tc) => tc.id === priorityCaseId) : null;
	const selected = singleCase
		? testCases[0]
		: priorityCase || findingCase || cases.find((tc) => tc.id === (selectedId || recovery.value.selectedId)) || cases[0];
	const selectedIndex = singleCase ? -1 : cases.indexOf(selected);
	const visibleCaseId = selected?.id;
	useEffect(() => onReadCase?.(visibleCaseId), [onReadCase, visibleCaseId]);
	const resolveCasePath = useMemo(() => createSavedCasePathResolver(projectId, savedSuite), [projectId, savedSuite]);
	const sharePath = useMemo(() => exactCasePath || resolveCasePath(selected), [exactCasePath, resolveCasePath, selected]);
	const selectCase = (id) => {
		dismissOpenedCase();
		setSelectedId(id);
		recovery.setValue((old) => ({ ...old, selectedId: id }));
	};
	const focusSelectedCase = useCallback(() => {
		titleRef.current?.focus({ preventScroll: true });
		detailRef.current?.scrollIntoView({ block: "start", behavior: "instant" });
	}, []);
	useLayoutEffect(() => {
		if (!focusPriorityCase || !priorityCase || !titleRef.current) return;
		recovery.setValue((old) => ({ ...old, selectedId: priorityCase.id }));
		focusSelectedCase();
		onPriorityCaseOpened();
	}, [focusPriorityCase, priorityCase?.id, focusSelectedCase]);
	const moveToCase = (index) => {
		const next = cases[index];
		if (!next) return;
		revealCaseRow(listRef.current, index);
		focusAfterMove.current = true;
		selectCase(next.id);
	};
	useLayoutEffect(() => {
		if (!focusAfterMove.current) return;
		focusAfterMove.current = false;
		focusSelectedCase();
	}, [selected?.id, focusSelectedCase]);
	const steps = selected?.steps || [];
	const citedSteps = singleCase && savedStep ? steps.filter((step) => step.step === savedStep) : [];
	const citedStep = citedSteps.length === 1 ? citedSteps[0] : null;
	const citedStepRef = useRef(null);
	useLayoutEffect(() => {
		const row = citedStepRef.current;
		if (!row) return;
		row.focus({ preventScroll: true });
		row.scrollIntoView({ block: "center" });
	}, [citedStep]);
	const findings = selected
		? qualityIssues.filter((issue) => new RegExp(`\\b${selected.id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(issue))
		: [];
	let emptyMessage = "No test cases generated yet.";
	if (visibleChanges?.length === 0) emptyMessage = "This application preserved all tests. There are no changed tests to review.";
	else if (hasCaseFilters) emptyMessage = "No test cases match your filters.";
	else if (normalizedQuery) emptyMessage = "No test cases match your search.";
	else if (reviewGroup) emptyMessage = "No test cases are available in this review group.";
	else if (visibleChanges) emptyMessage = "No changed test cases are available in this suite.";
	return (
		<>
			<CaseReaderLayout
				singleCase={singleCase}
				storageKey="test-cases"
				label="Resize test case list and details"
				className="test-review-workspace"
				aria-label="Generated test cases"
			>
				<div className="test-review-list">
					<DraftRecoveryNotice draft={recovery} />
					<h2 className="sr-only">Generated Test Cases</h2>
					{(reviewGroup || visibleChanges !== null) && (
						<div role="status">
							<p>{reviewGroup ? `Showing cases in ${reviewGroup.label}` : "Showing changed tests"}</p>
							<Button
								variant="secondary"
								onClick={() => {
									recovery.setValue((old) => ({ ...old, changedIds: null }));
									if (reviewGroup) onClearReviewGroup();
									else onClearChanged?.();
								}}
							>
								{reviewGroup ? "Clear group filter" : "Show all tests"}
							</Button>
						</div>
					)}
					<div className="compact-test-list-tools">
						<label htmlFor={searchId} className="sr-only">
							Search test cases
						</label>
						<Input
							id={searchId}
							ref={searchRef}
							type="search"
							placeholder="Search test cases"
							value={query}
							onChange={(event) => setQuery(event.target.value)}
						/>
						<Button
							variant="secondary"
							size="compact"
							aria-expanded={filtersOpen}
							aria-controls={`${searchId}-filters`}
							onClick={() => setFiltersOpen((open) => !open)}
						>
							Filters{hasCaseFilters ? ` (${activeFilterCount})` : ""}
						</Button>
					</div>
					<div id={`${searchId}-filters`} className="test-review-filters" role="group" aria-label="Test case filters" hidden={!filtersOpen}>
						<label>
							Priority
							<Select value={priority} onChange={(event) => saveFilters({ priority: event.target.value, automationIntent })}>
								<option value="">All priorities</option>
								{CASE_PRIORITIES.map((value) => (
									<option key={value}>{value}</option>
								))}
							</Select>
						</label>
						<label>
							Automation intent
							<Select value={automationIntent} onChange={(event) => saveFilters({ priority, automationIntent: event.target.value })}>
								<option value="">All intents</option>
								{AUTOMATION_INTENTS.map((value) => (
									<option key={value}>{value}</option>
								))}
							</Select>
						</label>
					</div>
					<div className="test-review-filter-results">
						<ResultCount as="p" className="test-list-count">
							{cases.length} of {testCases.length} test cases
						</ResultCount>
						{hasCaseFilters && (
							<Button variant="secondary" size="compact" onClick={clearFilters}>
								Clear filters
							</Button>
						)}
						{selected && (
							<Button
								variant="secondary"
								size="compact"
								className="test-case-jump"
								aria-controls="selected-test-case"
								onClick={focusSelectedCase}
							>
								Go to selected case
							</Button>
						)}
					</div>
					<List
						as="div"
						variant="grouped"
						className="test-review-items"
						ref={listRef}
						onScroll={(event) => recovery.setValue((old) => ({ ...old, scrollTop: event.currentTarget.scrollTop }))}
					>
						{cases.map((tc, index) => {
							const rowId = `${searchId}-case-${index}`;
							return (
								<SelectableItem
									key={tc.id}
									type="button"
									className={`test-review-item ${selected?.id === tc.id ? "selected" : ""}`}
									aria-pressed={selected?.id === tc.id}
									aria-controls="selected-test-case"
									aria-labelledby={`${rowId}-id ${rowId}-title`}
									aria-describedby={`${rowId}-meta`}
									onClick={() => selectCase(tc.id)}
								>
									<span>
										<small id={`${rowId}-id`}>{tc.id}</small> <strong id={`${rowId}-title`}>{tc.title}</strong>
										<span id={`${rowId}-meta`} className="test-review-item-meta">
											{casePriority(tc)} priority · {caseAutomationIntent(tc)}
										</span>
									</span>
									<ChevronRight size={18} aria-hidden="true" />
								</SelectableItem>
							);
						})}
					</List>
					{!cases.length && (
						<CollectionState kind={testCases.length ? "filtered" : "empty"}>
							<p>{emptyMessage}</p>
							{normalizedQuery && !hasCaseFilters && (
								<Button variant="secondary" size="compact" onClick={clearFilters}>
									Clear search
								</Button>
							)}
						</CollectionState>
					)}
				</div>
				<section ref={detailRef} className="test-review-detail" id="selected-test-case" aria-label="Selected test case" aria-live="polite">
					{selected ? (
						<>
							{(priorityCase || findingCase) && selectedIndex < 0 && (
								<p className="test-detail-meta">
									{priorityCase
										? "Opened from the generation result. This case is outside the current filters."
										: "Opened from Improve tests. This case is outside the current filters."}
								</p>
							)}
							<div className="test-case-identity">
								<Badge icon={null} tone="info" className="case-id">
									{selected.id}
								</Badge>
								{selectedIndex >= 0 && (
									<span id={positionId} className="test-case-position">
										Case {selectedIndex + 1} of {cases.length}
									</span>
								)}
							</div>
							<p className="test-detail-meta">
								{selected.type || "Functional"} · {casePriority(selected)} priority · {getTestCaseLinkedRequirementIds(selected).join(", ")}
							</p>
							<div className="test-detail-title">
								<h2 ref={titleRef} tabIndex={-1} aria-describedby={selectedIndex >= 0 ? positionId : undefined}>
									{selected.title}
								</h2>
								{(sharePath || onReport) && (
									<div className="test-detail-actions">
										{sharePath && <CaseLinkControl key={sharePath} path={sharePath} />}
										{onReport && (
											<Button variant="secondary" size="compact" onClick={() => onReport(selected)}>
												Report a problem
											</Button>
										)}
									</div>
								)}
							</div>
							{renderSelectedCaseRepair?.(selected, sharePath)}
							{singleCase && savedStep && !citedStep && <p role="status">The cited step is unavailable in this saved case.</p>}
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
											<tr
												key={`${selected.id}-${index}`}
												id={step === citedStep ? `saved-case-step-${step.step}` : undefined}
												ref={step === citedStep ? citedStepRef : undefined}
												tabIndex={step === citedStep ? -1 : undefined}
											>
												<th scope="row">{step.step ?? index + 1}</th>
												<td>
													{step.action}
													{typeof step.test_data === "string" && <TestStepData value={step.test_data} />}
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
												<p>
													{requirement?.text ||
														(singleCase
															? "Requirement text unavailable in the saved source."
															: "Requirement text unavailable in the current project.")}
												</p>
											</div>
										);
									})}
									{!singleCase && (
										<p>Current requirement text is shown for comparison. It may have changed since this test was generated.</p>
									)}
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
											"Automation intent": caseAutomationIntent(selected),
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
							{selectedIndex >= 0 && cases.length > 1 && (
								<nav className="test-case-navigation" aria-label="Case navigation">
									<Button variant="secondary" size="compact" disabled={selectedIndex === 0} onClick={() => moveToCase(selectedIndex - 1)}>
										Previous case
									</Button>
									<Button
										variant="secondary"
										size="compact"
										disabled={selectedIndex === cases.length - 1}
										onClick={() => moveToCase(selectedIndex + 1)}
									>
										Next case
									</Button>
								</nav>
							)}
						</>
					) : (
						<p>Select a test case to review its details.</p>
					)}
				</section>
			</CaseReaderLayout>
		</>
	);
}
