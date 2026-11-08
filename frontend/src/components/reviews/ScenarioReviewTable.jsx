import { MultiSelect } from "../ui/multi-select";
import { ResizableTable, useTableColumnWidths } from "../ui/resizable-table";
import { Fragment, useId, useState } from "react";
import { Button, Select } from "../ui/controls";
import { Alert, Badge } from "../ui/surfaces";
import { TableScroll, CollectionState } from "../ui/collections";

import ReviewRowToggle from "./ReviewRowToggle";

export const SCENARIO_FLAGS = ["Ambiguous", "Duplicate", "Missing coverage", "Not testable", "Incorrect requirement mapping"];
const COLUMNS = [
	{ label: "Use Case ID", percent: 18, min: 140, initial: 144 },
	{ label: "Title / objective", percent: 42, min: 200, initial: 336 },
	{ label: "Review status", percent: 20, min: 150, initial: 160 },
	{ label: "Quality flags", percent: 20, min: 150, initial: 160 },
];
const STATUSES = { needs_review: "Needs review", approved: "Approved", request_changes: "Changes requested" };
const keyFor = (group, scenario) => JSON.stringify([group.requirement_id, scenario.id]);
export function scenarioReview(group, scenario, state, snapshotId) {
	const stored = state?.metadata?.scenario_reviews;
	const item = stored?.snapshot_id === snapshotId ? stored.items?.[keyFor(group, scenario)] : null;
	const legacy = state?.metadata?.latest_human_review;
	return (
		item || {
			status: !stored && legacy?.snapshot_id === snapshotId && legacy.decision === "approve" ? "approved" : "needs_review",
			quality_flags: [],
		}
	);
}

export default function ScenarioReviewTable({
	groups,
	allGroups,
	state,
	snapshot,
	revision,
	review,
	drafts,
	setDrafts,
	assessment,
	recovery,
	onContinue,
	search,
}) {
	const detailId = useId();
	const [openRow, setOpenRow] = useState(null);
	const [widths, setWidths] = useTableColumnWidths(COLUMNS, "use-cases-grouped-v1");
	const { statusFilter, flagFilters, revision: draftRevision } = recovery.value;
	const setStatusFilter = (statusFilter) => recovery.setValue((old) => ({ ...old, statusFilter }));
	const setFlagFilters = (flagFilters) => recovery.setValue((old) => ({ ...old, flagFilters }));
	const dirty = Object.keys(drafts).length > 0;
	const conflict = dirty && draftRevision !== revision;
	const disabled =
		review.isSubmitting || review.isReloading || review.status === "refresh_error" || review.status === "conflict" || conflict;
	const rows = groups
		.flatMap((group) =>
			group.scenarios.map((scenario) => {
				const key = keyFor(group, scenario);
				const findings = assessment?.items?.find(
					(item) => item.requirement_id === group.requirement_id && item.scenario_id === scenario.id
				);
				return { group, scenario, key, findings, value: drafts[key] || scenarioReview(group, scenario, state, snapshot.snapshot_id) };
			})
		)
		.filter(
			(row) =>
				(!statusFilter || row.value.status === statusFilter) &&
				(!flagFilters.length || flagFilters.some((flag) => row.value.quality_flags.includes(flag)))
		);
	const visibleGroups = groups
		.map((group) => ({ group, rows: rows.filter((row) => row.group === group) }))
		.filter(({ rows }) => rows.length);
	const approved = allGroups.reduce(
		(total, group) =>
			total +
			(group.scenarios || []).filter((scenario) => scenarioReview(group, scenario, state, snapshot.snapshot_id).status === "approved")
				.length,
		0
	);
	const total = allGroups.reduce((count, group) => count + (group.scenarios || []).length, 0);
	const change = (row, values) => {
		setDrafts((previous) => ({
			...previous,
			[row.key]: {
				requirement_id: row.group.requirement_id,
				scenario_id: row.scenario.id,
				status: row.value.status,
				quality_flags: row.value.quality_flags,
				...values,
			},
		}));
	};
	const save = async () => {
		const response = await review.submit({ scenarioReviews: Object.values(drafts) });
		if (response) setDrafts({});
	};
	return (
		<>
			<div className="scenario-table-toolbar compact-collection-toolbar">
				{search}
				<div className="scenario-status-filter">
					<Select aria-label="Filter by review status" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
						<option value="">All review statuses</option>
						{Object.entries(STATUSES).map(([value, label]) => (
							<option key={value} value={value}>
								{label}
							</option>
						))}
					</Select>
				</div>
				<div className="scenario-status-filter">
					<MultiSelect label="Quality flag filters" options={SCENARIO_FLAGS} value={flagFilters} onChange={setFlagFilters} />
				</div>
				{statusFilter || flagFilters.length ? (
					<Button
						variant="plain"
						onClick={() => {
							setStatusFilter("");
							setFlagFilters([]);
						}}
					>
						Clear filters
					</Button>
				) : null}
				<p role="status" className="scenario-review-count">
					{approved} of {total} approved · Showing {rows.length} scenarios{dirty ? ` · ${Object.keys(drafts).length} unsaved reviews` : ""}
				</p>
				{dirty ? (
					<>
						<Button variant="secondary" size="compact" disabled={review.isSubmitting || review.isReloading} onClick={() => setDrafts({})}>
							Discard edits
						</Button>
						<Button size="compact" disabled={disabled} onClick={() => void save()}>
							{review.isSubmitting ? "Saving reviews…" : "Save reviews"}
						</Button>
					</>
				) : onContinue ? (
					<Button size="compact" onClick={onContinue}>
						Open Test Cases
					</Button>
				) : null}
			</div>
			{conflict || ["error", "conflict", "refresh_error"].includes(review.status) ? (
				<Alert tone="warning" role="alert">
					<p>{conflict ? "Project changed while you were editing. Reload and review the latest version before saving." : review.error}</p>
					{review.status === "error" && !conflict ? (
						<Button variant="secondary" onClick={() => void save()}>
							Retry saving reviews
						</Button>
					) : (
						<Button
							variant="secondary"
							disabled={review.isReloading}
							onClick={async () => {
								const project = await review.reloadLatest();
								if (project) setDrafts({});
							}}
						>
							Reload latest and discard edits
						</Button>
					)}
				</Alert>
			) : null}
			{visibleGroups.length > 0 && (
				<TableScroll aria-label="Use case scenarios table" className="scenario-table-scroll">
					<ResizableTable
						columns={COLUMNS}
						responsiveMinWidth={800}
						widths={widths}
						onWidthsChange={setWidths}
						aria-label="Use case scenarios by requirement"
						className="scenario-review-table review-rows"
						density="compact"
					>
						{visibleGroups.map(({ group, rows: groupRows }) => (
							<tbody key={group.requirement_id}>
								<tr className="scenario-group-heading">
									<th scope="rowgroup" colSpan={4}>
										<strong>{group.requirement_id}</strong> <span>{group.requirement_text}</span>
									</th>
								</tr>
								{groupRows.map((row) => {
									const expanded = openRow === row.key;
									const warningCount = row.findings?.warnings?.length || 0;
									return (
										<Fragment key={row.key}>
											<tr className="scenario-review-row" data-expanded={expanded}>
												<th scope="row">{row.scenario.id}</th>
												<td>
													<ReviewRowToggle
														expanded={expanded}
														controls={expanded ? detailId : undefined}
														label={`Review ${row.scenario.id}: ${row.scenario.title || row.scenario.objective}`}
														onClick={() => setOpenRow(expanded ? null : row.key)}
													>
														{row.scenario.title || row.scenario.objective}
													</ReviewRowToggle>
													{warningCount > 0 && (
														<Badge compact icon={null} tone="warning" className="workbench-status scenario-finding-count">
															{warningCount} {warningCount === 1 ? "finding" : "findings"}
														</Badge>
													)}
													{!warningCount && row.findings?.status === "needs_review" && (
														<Badge className="workbench-status" icon={null} tone="warning">
															Needs assessment
														</Badge>
													)}
												</td>
												<td>
													<div className="scenario-status-filter">
														<Select
															aria-label={`Review status for ${row.scenario.id}`}
															value={row.value.status}
															disabled={disabled || !row.scenario.id || !row.group.requirement_id}
															onChange={(event) => change(row, { status: event.target.value })}
														>
															{Object.entries(STATUSES).map(([value, label]) => (
																<option key={value} value={value} disabled={value === "approved" && state?.stale}>
																	{label}
																</option>
															))}
														</Select>
													</div>
												</td>
												<td>
													<MultiSelect
														label="Quality flags"
														accessibleLabel={`Quality flags for ${row.scenario.id}`}
														options={SCENARIO_FLAGS}
														value={row.value.quality_flags}
														disabled={disabled || !row.scenario.id || !row.group.requirement_id}
														onChange={(quality_flags) => change(row, { quality_flags })}
													/>
												</td>
											</tr>
											{expanded && (
												<tr className="review-detail-row">
													<td id={detailId} colSpan={4}>
														<p>{row.scenario.objective}</p>
														<div aria-label={`Semantic assessment for ${row.scenario.id}`}>
															<strong>
																{row.findings?.status === "passed"
																	? "Semantic checks passed"
																	: row.findings?.status === "needs_review"
																		? "Semantic findings"
																		: "Semantic assessment unavailable"}
															</strong>
															{row.findings?.warnings?.length ? (
																<ul>
																	{row.findings.warnings.map((warning, index) => (
																		<li key={index}>{warning}</li>
																	))}
																</ul>
															) : null}
															{row.findings && !row.findings.review_available ? (
																<p>Semantic reviewer unavailable; any listed warnings are local checks.</p>
															) : null}
														</div>
													</td>
												</tr>
											)}
										</Fragment>
									);
								})}
							</tbody>
						))}
					</ResizableTable>
				</TableScroll>
			)}

			{!rows.length ? (
				<CollectionState kind={total ? "filtered" : "empty"}>
					{total ? "No scenarios match these filters." : "No scenarios have been generated."}
				</CollectionState>
			) : null}
		</>
	);
}
