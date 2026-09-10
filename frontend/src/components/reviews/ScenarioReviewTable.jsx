import { useState } from "react";
import { Button, Checkbox, Select } from "../ui/controls";
import { Alert, Disclosure } from "../ui/surfaces";
import { Table, TableScroll, CollectionState } from "../ui/collections";

export const SCENARIO_FLAGS = ["Ambiguous", "Duplicate", "Missing coverage", "Not testable", "Incorrect requirement mapping"];
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

export default function ScenarioReviewTable({ groups, allGroups, state, snapshot, revision, review, drafts, setDrafts, renderDetails }) {
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
				<Select aria-label="Filter by review status" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
					<option value="">All review statuses</option>
					{Object.entries(STATUSES).map(([value, label]) => (
						<option key={value} value={value}>
							{label}
						</option>
					))}
				</Select>
				<Disclosure className="scenario-flags">
					<summary>Quality flag filters{flagFilters.length ? ` (${flagFilters.length})` : ""}</summary>
					<div>
						{SCENARIO_FLAGS.map((flag) => (
							<label key={flag}>
								<Checkbox
									checked={flagFilters.includes(flag)}
									onChange={() =>
										setFlagFilters((previous) => (previous.includes(flag) ? previous.filter((item) => item !== flag) : [...previous, flag]))
									}
								/>
								{flag}
							</label>
						))}
					</div>
				</Disclosure>
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
				<Table className="scenario-review-table">
					<thead>
						<tr>
							<th scope="col">Source requirement</th>
							<th scope="col">Use Case ID</th>
							<th scope="col">Title / objective</th>
							<th scope="col">Review status</th>
							<th scope="col">Quality flags</th>
						</tr>
					</thead>
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
									<Disclosure>
										<summary aria-label={`Details for ${row.scenario.id}`}>Details</summary>
										{renderDetails(row.scenario, row.group)}
									</Disclosure>
								</td>
								<td>
									<div className="scenario-status-filter">
										{" "}
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
									<Disclosure className="scenario-flags">
										<summary aria-label={`Quality flags for ${row.scenario.id}: ${row.value.quality_flags.length || "None"}`}>
											{row.value.quality_flags.length ? `${row.value.quality_flags.length} flags` : "Add flags"}
										</summary>
										<div>
											{SCENARIO_FLAGS.map((flag) => (
												<label key={flag}>
													<Checkbox
														disabled={disabled || !row.scenario.id || !row.group.requirement_id}
														checked={row.value.quality_flags.includes(flag)}
														onChange={() =>
															change(row, {
																quality_flags: row.value.quality_flags.includes(flag)
																	? row.value.quality_flags.filter((item) => item !== flag)
																	: [...row.value.quality_flags, flag].sort(),
															})
														}
													/>
													{flag}
												</label>
											))}
										</div>
									</Disclosure>
								</td>
							</tr>
						))}
					</tbody>
				</Table>
			</TableScroll>
			{!rows.length ? (
				<CollectionState kind={total ? "filtered" : "empty"}>
					{total ? "No scenarios match these filters." : "No scenarios have been generated."}
				</CollectionState>
			) : null}
		</>
	);
}
