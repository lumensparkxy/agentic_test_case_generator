import { REQUIREMENT_QUALITY_FLAG_OPTIONS, REQUIREMENT_REVIEW_STATUSES } from "../../constants/workflow";
import {
	formatSourceIssueKey,
	getRequirementEpicCell,
	getRequirementIssueCell,
	getRequirementReviewStatus,
	getRequirementSourceLabel,
	groupRequirementsByContext,
	normalizeStringArray,
} from "../../utils/requirements";

export default function RequirementReviewWorkbench({
	requirements,
	approvedRequirementCount,
	reviewPendingRequirementCount,
	rejectedRequirementCount,
	onApproveNonRejected,
	onMarkAllNeedsReview,
	onReviewStatusChange,
	onQualityFlagToggle,
}) {
	return (
		<div className="result-section">
			<h3>Requirement Review Workbench</h3>
			{requirements.length === 0 ? (
				<span className="helper-text">No requirements extracted yet.</span>
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
							<button type="button" className="secondary small" onClick={onApproveNonRejected}>
								Approve non-rejected
							</button>
							<button type="button" className="secondary small" onClick={onMarkAllNeedsReview}>
								Mark all needs review
							</button>
						</div>
					</div>
					{groupRequirementsByContext(requirements).map((group) => (
						<div key={group.id} className="requirement-context-group">
							<div className="requirement-context-header">
								<div>
									<span className="requirement-source-badge subtle">{group.sourceLabel}</span>
									<h4>{group.label}</h4>
								</div>
								<span className="analysis-summary-pill">
									{group.requirements.length} requirement{group.requirements.length === 1 ? "" : "s"}
								</span>
							</div>
							<div className="requirement-table-wrapper">
								<table className="requirement-review-table">
									<thead>
										<tr>
											<th>Epic</th>
											<th>Issue</th>
											<th>ID / Source</th>
											<th>Requirement</th>
											<th>Review source</th>
											<th>Review status</th>
											<th>Quality flags</th>
										</tr>
									</thead>
									<tbody>
										{group.requirements.map((req) => {
											const reviewStatus = getRequirementReviewStatus(req);
											const qualityFlags = normalizeStringArray(req.quality_flags);
											const requirementId = req.id || `REQ-${req.__index + 1}`;
											const epicCell = getRequirementEpicCell(req, group.label);
											const issueCell = getRequirementIssueCell(req);
											const sourceLabel = getRequirementSourceLabel(req);
											const hasSyncTarget = req.sync_target_issue_key && req.sync_target_issue_key !== req.source_issue_key;
											return (
												<tr
													key={req.id || req.text || req.__index}
													className={`requirement-row status-${reviewStatus.toLowerCase().replace(/\s/g, "-")}`}
												>
													<td className="requirement-epic-cell">
														<span className="cell-primary">{epicCell.primary}</span>
														{epicCell.secondary ? <span className="cell-secondary">{epicCell.secondary}</span> : null}
													</td>
													<td className="requirement-issue-cell">
														<span className="cell-primary">{issueCell.primary}</span>
														{issueCell.secondary ? <span className="cell-secondary">{issueCell.secondary}</span> : null}
													</td>
													<td className="requirement-id-cell">
														<strong>{requirementId}</strong>
														<span className="requirement-source-system">{sourceLabel}</span>
													</td>
													<td className="requirement-text-cell">
														<div className="requirement-item-copy">{req.text || req.title || ""}</div>
													</td>
													<td className="requirement-review-source-cell">
														{req.source_excerpt ? (
															<details className="requirement-evidence compact">
																<summary>Source evidence</summary>
																<p>{req.source_excerpt}</p>
																{req.source_issue_url ? (
																	<a href={req.source_issue_url} target="_blank" rel="noreferrer">
																		Open source ↗
																	</a>
																) : null}
															</details>
														) : req.source_issue_url ? (
															<a className="requirement-source-link" href={req.source_issue_url} target="_blank" rel="noreferrer">
																Open source ↗
															</a>
														) : req.source_path || req.source_section ? (
															<span className="cell-secondary">{req.source_path || req.source_section}</span>
														) : (
															<span className="cell-muted">—</span>
														)}
														{hasSyncTarget ? (
															<div className="requirement-source-meta compact">
																<span className="requirement-source-badge warning">
																	Sync target {formatSourceIssueKey(req, req.sync_target_issue_key)}
																</span>
															</div>
														) : null}
													</td>
													<td className="requirement-status-cell">
														<select
															value={reviewStatus}
															onChange={(event) => onReviewStatusChange(req.id, event.target.value)}
															aria-label={`Review status for ${requirementId}`}
														>
															{REQUIREMENT_REVIEW_STATUSES.map((statusOption) => (
																<option key={`${requirementId}-${statusOption}`} value={statusOption}>
																	{statusOption}
																</option>
															))}
														</select>
													</td>
													<td className="requirement-flags-cell">
														<details className="requirement-quality-details">
															<summary>
																{qualityFlags.length ? `${qualityFlags.length} flag${qualityFlags.length === 1 ? "" : "s"}` : "Add flags"}
															</summary>
															<div className="quality-flag-checklist">
																{REQUIREMENT_QUALITY_FLAG_OPTIONS.map((flag) => (
																	<label key={`${requirementId}-${flag}`}>
																		<input
																			type="checkbox"
																			checked={qualityFlags.includes(flag)}
																			onChange={() => onQualityFlagToggle(req.id, flag)}
																		/>
																		<span>{flag}</span>
																	</label>
																))}
															</div>
														</details>
													</td>
												</tr>
											);
										})}
									</tbody>
								</table>
							</div>
						</div>
					))}
				</div>
			)}
		</div>
	);
}
