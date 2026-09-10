import { MultiSelect } from "../ui/multi-select";
import { ResizableTable } from "../ui/resizable-table";
import { useState } from "react";
import { Button, Select } from "../ui/controls";
import { Alert } from "../ui/surfaces";
import { TableScroll, CollectionState } from "../ui/collections";

export const SCENARIO_FLAGS = ["Ambiguous", "Duplicate", "Missing coverage", "Not testable", "Incorrect requirement mapping"];
const COLUMNS = [
	{ label: "Source requirement", percent: 20, min: 140, initial: 220 },
	{ label: "Use Case ID", percent: 13, min: 100, initial: 143 },
	{ label: "Title / objective", percent: 32, min: 200, initial: 352 },
	{ label: "Review status", percent: 17, min: 150, initial: 187 },
	{ label: "Quality flags", percent: 18, min: 150, initial: 198 },
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

export default function ScenarioReviewTable({ groups, allGroups, state, snapshot, revision, review, drafts, setDrafts }) {
	const [statusFilter, setStatusFilter] = useState("");
	const [flagFilters, setFlagFilters] = useState([]);
	const [draftRevision, setDraftRevision] = useState(revision);
	const dirty = Object.keys(drafts).length > 0;
	const conflict = dirty && draftRevision !== revision;
	const disabled =
		review.isSubmitting || review.isReloading || review.status === "refresh_error" || review.status === "conflict" || conflict;
	const rows = groups
		.flatMap((group) =>
			group.scenarios.map((scenario) => {
				const key = keyFor(group, scenario);
				return { group, scenario, key, value: drafts[key] || scenarioReview(group, scenario, state, snapshot.snapshot_id) };
			})
		)
		.filter(
			(row) =>
				(!statusFilter || row.value.status === statusFilter) &&
				(!flagFilters.length || flagFilters.some((flag) => row.value.quality_flags.includes(flag)))
		);
	const approved = allGroups.reduce(
		(total, group) =>
			total +
			(group.scenarios || []).filter((scenario) => scenarioReview(group, scenario, state, snapshot.snapshot_id).status === "approved")
				.length,
		0
	);
	const total = allGroups.reduce((count, group) => count + (group.scenarios || []).length, 0);
	const change = (row, values) => {
		if (!dirty) setDraftRevision(revision);
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
			<div className="scenario-table-toolbar">
				<p role="status">
					{approved} of {total} approved · Showing {rows.length} scenarios{dirty ? ` · ${Object.keys(drafts).length} unsaved reviews` : ""}
				</p>
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
				{dirty ? (
					<>
						<Button variant="secondary" disabled={review.isSubmitting || review.isReloading} onClick={() => setDrafts({})}>
							Discard edits
						</Button>
						<Button disabled={disabled} onClick={() => void save()}>
							{review.isSubmitting ? "Saving reviews…" : "Save reviews"}
						</Button>
					</>
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
			<TableScroll label="Use case scenarios table" aria-label="Use case scenarios table" className="scenario-table-scroll">
				<ResizableTable columns={COLUMNS} storageKey="use-cases" className="scenario-review-table">
					<tbody>
						{rows.map((row) => (
							<tr key={row.key}>
								<td>
									<strong>{row.group.requirement_id}</strong>
									<p>{row.group.requirement_text}</p>
								</td>
								<th scope="row">{row.scenario.id}</th>
								<td>
									<strong>{row.scenario.title || row.scenario.objective}</strong>
									<p>{row.scenario.objective}</p>
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
						))}
					</tbody>
				</ResizableTable>
			</TableScroll>
			{!rows.length ? (
				<CollectionState kind={total ? "filtered" : "empty"}>
					{total ? "No scenarios match these filters." : "No scenarios have been generated."}
				</CollectionState>
			) : null}
		</>
	);
}
