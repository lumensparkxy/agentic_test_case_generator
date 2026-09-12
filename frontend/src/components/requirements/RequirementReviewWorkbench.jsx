import { useState } from "react";
import { Surface, Disclosure } from "../ui/surfaces";
import { Button, Link, Select, Checkbox } from "../ui/controls";
import { TableScroll, Table } from "../ui/collections";
import { REQUIREMENT_QUALITY_FLAG_OPTIONS, REQUIREMENT_REVIEW_STATUSES } from "../../constants/workflow";
import { getRequirementReviewStatus, normalizeStringArray } from "../../utils/requirements";

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
}) {
	return (
		<Surface as="div" className="result-section">
			<h3>Requirement Review Workbench</h3>
			{requirements.length === 0 ? (
				<span className="helper-text">No active requirements yet. Import and apply reviewed changes to begin.</span>
			) : (
				<div className="requirement-review-workbench">
					<div className="requirement-review-summary">
						<div>
							<strong>
								{approvedRequirementCount}/{requirements.length} approved for test generation
							</strong>
							<p>
								{reviewPendingRequirementCount} pending review • {rejectedRequirementCount} rejected/out of scope
							</p>
						</div>
						<div className="requirement-review-bulk-actions">
							<Button className="secondary small" onClick={onApproveNonRejected} disabled={busy}>
								Approve non-rejected
							</Button>
							<Button className="secondary small" onClick={onMarkAllNeedsReview} disabled={busy}>
								Mark all needs review
							</Button>
						</div>
					</div>
					<TableScroll className="requirement-table-wrapper" role="region" aria-label="Project requirements table" tabIndex={0}>
						<Table className="requirement-review-table canonical-requirements">
							<thead>
								<tr>
									<th scope="col">ID</th>
									<th scope="col">Requirement</th>
									<th scope="col">Sources</th>
									<th scope="col">Review status</th>
									<th scope="col">Quality flags</th>
								</tr>
							</thead>
							<tbody>
								{requirements.map((req) => {
									const status = getRequirementReviewStatus(req);
									const flags = normalizeStringArray(req.quality_flags);
									const sources = req.sources?.length
										? req.sources
										: [
												{
													source_id: "legacy",
													source_version: "legacy",
													label: req.source_path || req.source_section || req.source_issue_key || "Imported requirements",
													excerpt: req.source_excerpt,
													source_issue_url: req.source_issue_url,
												},
											];
									return (
										<tr
											key={req.requirement_uid || req.id}
											className={`requirement-row status-${status.toLowerCase().replace(/\s/g, "-")}`}
										>
											<td>
												<strong>{req.id}</strong>
											</td>
											<td className="requirement-text-cell">
												<div className="requirement-item-copy">{req.text}</div>
												<RequirementHistory requirement={req} loadHistory={loadHistory} />
											</td>
											<td>
												<Disclosure>
													<summary>Source evidence ({new Set(sources.map((source) => source.source_id)).size})</summary>
													{sources.map((source) => (
														<div key={`${source.source_id}-${source.source_version}`}>
															<strong>{source.label}</strong>
															{source.source_issue_url && (
																<Link href={source.source_issue_url} target="_blank" rel="noreferrer">
																	Open source ↗
																</Link>
															)}
															<p>{source.excerpt || req.text}</p>
														</div>
													))}
												</Disclosure>
											</td>
											<td>
												<Select
													value={status}
													disabled={busy}
													onChange={(event) => onReviewStatusChange(req.id, event.target.value)}
													aria-label={`Review status for ${req.id}`}
												>
													{REQUIREMENT_REVIEW_STATUSES.map((option) => (
														<option key={option} value={option}>
															{option}
														</option>
													))}
												</Select>
											</td>
											<td>
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
											</td>
										</tr>
									);
								})}
							</tbody>
						</Table>
					</TableScroll>
				</div>
			)}
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
		</Surface>
	);
}
