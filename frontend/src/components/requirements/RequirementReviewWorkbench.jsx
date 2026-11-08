import { Fragment, useId, useRef, useState } from "react";
import { Badge, Disclosure } from "../ui/surfaces";
import { Button, Input, Select, Checkbox } from "../ui/controls";
import { CollectionState, TableScroll, Table } from "../ui/collections";
import { REQUIREMENT_QUALITY_FLAG_OPTIONS, REQUIREMENT_REVIEW_STATUSES } from "../../constants/workflow";
import { getRequirementReviewStatus, normalizeStringArray } from "../../utils/requirements";
import { requirementSources, downloadSourceMapping } from "../../utils/requirementSources";
import ReviewRowToggle from "../reviews/ReviewRowToggle";
import RequirementSourceEvidence from "./RequirementSourceEvidence";

function RequirementHistory({ requirement, loadHistory }) {
	const [history, setHistory] = useState(null);
	const [error, setError] = useState("");
	const [loading, setLoading] = useState(false);
	if (!requirement.requirement_uid || !loadHistory) return null;
	return (
		<Disclosure>
			<summary>Version history · v{requirement.content_version || 1}</summary>
			<Button
				className="secondary small"
				disabled={loading}
				onClick={async () => {
					setLoading(true);
					setError("");
					try {
						setHistory(await loadHistory(requirement.requirement_uid));
					} catch (e) {
						setError(e.message);
					} finally {
						setLoading(false);
					}
				}}
			>
				{loading ? "Loading history…" : "Load version history"}
			</Button>
			{error && <p role="alert">{error}</p>}
			{history?.map((item) => (
				<div key={item.snapshot_id} className="requirement-history-entry">
					<strong>
						v{item.requirement.content_version} · Revision {item.project_revision} · {item.requirement.lifecycle_status}
					</strong>
					<p>{item.requirement.text}</p>
					<span>
						{item.requirement.review_status} · {new Date(item.created_at).toLocaleString()}
					</span>
				</div>
			))}
		</Disclosure>
	);
}

export default function RequirementReviewWorkbench({
	requirements,
	approvedRequirementCount,
	reviewPendingRequirementCount,
	rejectedRequirementCount,
	onApproveNonRejected,
	onMarkAllNeedsReview,
	onReviewStatusChange,
	onQualityFlagToggle,
	loadHistory,
	retiredRequirements = [],
	busy = false,
	headingRef,
	reviewRequest,
}) {
	const [query, setQuery] = useState("");
	const [filterChoice, setFilterChoice] = useState({ request: null, value: "" });
	const [openRow, setOpenRow] = useState(null);
	const detailId = useId();
	const statusFilter = filterChoice.request === reviewRequest ? filterChoice.value : "pending";
	const filterRef = useRef(null);
	const search = query.trim().toLowerCase();
	const rows = requirements.filter(
		(req) =>
			(!statusFilter ||
				(statusFilter === "pending"
					? getRequirementReviewStatus(req) !== "Rejected" &&
						(getRequirementReviewStatus(req) !== "Approved" || ["open", "deferred", "excluded"].includes(req.clarification_status))
					: getRequirementReviewStatus(req) === statusFilter)) &&
			(!search || `${req.id} ${req.text}`.toLowerCase().includes(search))
	);
	return (
		<section className="compact-requirements" aria-labelledby="requirement-review-heading">
			{requirements.length === 0 ? (
				<h2 id="requirement-review-heading" ref={headingRef} tabIndex={-1}>
					Requirement Review Workbench
				</h2>
			) : (
				<div className="requirement-review-workbench">
					<div className="compact-collection-toolbar">
						<div className="compact-collection-label">
							<h2 id="requirement-review-heading" ref={headingRef} tabIndex={-1}>
								Requirement Review Workbench
							</h2>
							<p role="status">
								{approvedRequirementCount}/{requirements.length} approved · {reviewPendingRequirementCount} pending ·{" "}
								{rejectedRequirementCount} rejected
							</p>
						</div>
						<Input
							type="search"
							aria-label="Search requirements"
							placeholder="Search requirements"
							value={query}
							onChange={(event) => setQuery(event.target.value)}
						/>
						<Select
							ref={filterRef}
							aria-label="Filter requirements by review status"
							value={statusFilter}
							onChange={(event) => setFilterChoice({ request: reviewRequest, value: event.target.value })}
						>
							<option value="">All statuses</option>
							<option value="pending">Pending review</option>
							{REQUIREMENT_REVIEW_STATUSES.map((status) => (
								<option key={status}>{status}</option>
							))}
						</Select>
						<Disclosure className="requirement-review-bulk-actions">
							<summary>Review actions</summary>
							<div>
								<Button variant="secondary" size="compact" onClick={() => downloadSourceMapping(requirements)} disabled={busy}>
									Download source mapping (JSON)
								</Button>
								<Button variant="secondary" size="compact" onClick={onApproveNonRejected} disabled={busy}>
									Approve non-rejected
								</Button>
								<Button variant="secondary" size="compact" onClick={onMarkAllNeedsReview} disabled={busy}>
									Mark all needs review
								</Button>
							</div>
						</Disclosure>
					</div>
					{rows.length > 0 ? (
						<TableScroll className="requirement-table-wrapper" aria-label="Project requirements table">
							<Table density="compact" className="requirement-review-table canonical-requirements review-rows">
								<thead>
									<tr>
										<th scope="col">ID</th>
										<th scope="col">Requirement</th>
										<th scope="col">Review status</th>
										<th scope="col">Flags</th>
									</tr>
								</thead>
								<tbody>
									{rows.map((req) => {
										const key = req.requirement_uid || req.id;
										const status = getRequirementReviewStatus(req);
										const flags = normalizeStringArray(req.quality_flags);
										const expanded = openRow === key;
										return (
											<Fragment key={key}>
												<tr className="requirement-row" data-expanded={expanded}>
													<th scope="row">{req.id}</th>
													<td>
														<ReviewRowToggle
															expanded={expanded}
															controls={expanded ? detailId : undefined}
															label={`Review ${req.id}: ${req.text}`}
															onClick={() => setOpenRow(expanded ? null : key)}
														>
															{req.text}
														</ReviewRowToggle>
													</td>
													<td>
														<Badge
															className="workbench-status"
															icon={null}
															tone={status === "Approved" ? "success" : status === "Rejected" ? "neutral" : "warning"}
														>
															{status}
														</Badge>
													</td>
													<td>{flags.length}</td>
												</tr>
												{expanded && (
													<tr className="review-detail-row">
														<td id={detailId} colSpan={4}>
															<p className="requirement-item-copy">{req.text}</p>
															<div className="requirement-row-controls">
																<Select
																	value={status}
																	disabled={busy}
																	aria-label={`Review status for ${req.id}`}
																	onChange={(event) => {
																		onReviewStatusChange(req.id, event.target.value);
																		if (statusFilter && event.target.value !== statusFilter) filterRef.current?.focus();
																	}}
																>
																	{REQUIREMENT_REVIEW_STATUSES.map((option) => (
																		<option key={option}>{option}</option>
																	))}
																</Select>
																<Disclosure className="requirement-quality-details">
																	<summary>{flags.length ? `${flags.length} flag${flags.length === 1 ? "" : "s"}` : "Add flags"}</summary>
																	<div className="quality-flag-checklist">
																		{REQUIREMENT_QUALITY_FLAG_OPTIONS.map((flag) => (
																			<label key={flag}>
																				<Checkbox
																					type="checkbox"
																					checked={flags.includes(flag)}
																					disabled={busy}
																					onChange={() => onQualityFlagToggle(req.id, flag)}
																				/>
																				<span>{flag}</span>
																			</label>
																		))}
																	</div>
																</Disclosure>
															</div>
															<Disclosure>
																<summary>
																	Source evidence ({new Set(requirementSources(req).map((source) => source.source_id)).size})
																</summary>
																<RequirementSourceEvidence requirement={req} />
															</Disclosure>
															<RequirementHistory requirement={req} loadHistory={loadHistory} />
														</td>
													</tr>
												)}
											</Fragment>
										);
									})}
								</tbody>
							</Table>
						</TableScroll>
					) : (
						<CollectionState
							kind="filtered"
							title="No matching requirements"
							action={
								<Button
									variant="secondary"
									size="compact"
									onClick={() => {
										setQuery("");
										setFilterChoice({ request: reviewRequest, value: "" });
										filterRef.current?.focus();
									}}
								>
									Clear filters
								</Button>
							}
						/>
					)}
				</div>
			)}
			{requirements.length === 0 && <p className="helper-text">Import and apply reviewed changes to begin.</p>}
			{retiredRequirements.length > 0 && (
				<Disclosure>
					<summary>Retired requirements ({retiredRequirements.length})</summary>
					<p>Retained for traceability; excluded from new generation.</p>
					{retiredRequirements.map((req) => (
						<article key={req.requirement_uid}>
							<h4>{req.id}</h4>
							<p>{req.text}</p>
							<RequirementHistory requirement={req} loadHistory={loadHistory} />
						</article>
					))}
				</Disclosure>
			)}
		</section>
	);
}
