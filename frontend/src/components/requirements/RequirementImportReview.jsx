import { Fragment, useEffect, useId, useRef, useState } from "react";
import { Button, Checkbox, Select } from "../ui/controls";
import { Badge, Disclosure } from "../ui/surfaces";
import { CollectionState, Table, TableScroll } from "../ui/collections";
import ReviewRowToggle from "../reviews/ReviewRowToggle";
import RequirementSourceEvidence from "./RequirementSourceEvidence";

const labels = {
	new: "New",
	updated: "Updated",
	unchanged: "Unchanged",
	needs_decision: "Needs decision",
	skipped: "Skipped",
	retiring: "Retiring",
};
const normalize = (value) => value.trim().replace(/\s+/g, " ");
const requirementOption = (requirement) => (
	<option key={requirement.requirement_uid} value={requirement.requirement_uid}>
		{requirement.id} — {requirement.text}
	</option>
);

export default function RequirementImportReview({ preview, revision, busy, error, stalePreview, onAction }) {
	const heading = useRef(null);
	const actionBar = useRef(null);
	const filterControl = useRef(null);
	const detailId = useId();
	const [openCandidate, setOpenCandidate] = useState(null);
	const [filter, setFilter] = useState("all");
	const [scope, setScope] = useState([]);
	const [confirm, setConfirm] = useState(false);
	const [decisions, setDecisions] = useState(() =>
		Object.fromEntries(
			preview.candidates.map((c) => [
				c.candidate_id,
				{
					candidate_id: c.candidate_id,
					action:
						c.classification === "unchanged" ? "keep" : c.classification === "updated" ? "update" : c.classification === "new" ? "add" : "",
					target_requirement_uid: c.target_requirement_uid || null,
				},
			])
		)
	);
	useEffect(() => {
		heading.current?.focus({ preventScroll: true });
		heading.current?.scrollIntoView({ block: "nearest" });
	}, []);
	const current = new Map(preview.current_requirements.map((r) => [r.requirement_uid, r]));
	const targets = Object.values(decisions)
		.map((d) => d.target_requirement_uid)
		.filter(Boolean);
	const duplicateTargets = new Set(targets.filter((uid, index) => targets.indexOf(uid) !== index));
	const retiring = preview.current_requirements.filter((r) => scope.includes(r.requirement_uid) && !targets.includes(r.requirement_uid));
	const kindOf = (candidate) => {
		const d = decisions[candidate.candidate_id];
		if (!d.action || duplicateTargets.has(d.target_requirement_uid)) return "needs_decision";
		if (d.action === "skip") return "skipped";
		if (d.action === "add") return "new";
		const old = current.get(d.target_requirement_uid);
		if (!old) return "needs_decision";
		return normalize(old.text) === normalize(candidate.requirement.text) ? "unchanged" : "updated";
	};
	const counts = preview.candidates.reduce((result, c) => {
		const kind = kindOf(c);
		result[kind] = (result[kind] || 0) + 1;
		return result;
	}, {});
	const activeCount = current.size + (counts.new || 0) - retiring.length;
	const stale = stalePreview || revision !== preview.base_project_revision;
	const update = (candidate, value) => {
		const choice =
			value === "add" || value === "skip" || !value
				? { action: value, target_requirement_uid: null }
				: {
						action: normalize(current.get(value)?.text || "") === normalize(candidate.requirement.text) ? "keep" : "update",
						target_requirement_uid: value,
					};
		setDecisions((rows) => ({ ...rows, [candidate.candidate_id]: { candidate_id: candidate.candidate_id, ...choice } }));
		setConfirm(false);
	};
	const rows = preview.candidates.filter((candidate) => filter === "all" || kindOf(candidate) === filter);
	const opened = rows.find((candidate) => candidate.candidate_id === openCandidate);
	const openCandidateVisible = Boolean(opened);
	const sourceMatches = new Map(
		(opened?.suggestions || [])
			.filter((match) => match.verified_original_ids?.length && current.has(match.requirement_uid))
			.map((match) => [match.requirement_uid, match.verified_original_ids])
	);
	const [firstSourceMatch] = sourceMatches.keys();
	useEffect(() => {
		if (openCandidate && !openCandidateVisible) {
			filterControl.current?.focus();
			setOpenCandidate(null);
		}
	}, [openCandidate, openCandidateVisible]);
	const keepFocusClear = ({ target }) => {
		const bar = actionBar.current;
		if (!bar || bar.contains(target)) return;
		const focused = target.getBoundingClientRect();
		const actions = bar.getBoundingClientRect();
		if (focused.bottom > actions.top && focused.top < actions.bottom) {
			target.scrollIntoView({ block: "center", inline: "nearest" });
		}
	};
	return (
		<section className="requirement-import-review" aria-label="Compare incoming requirements" onFocusCapture={keepFocusClear}>
			<div className="import-review-heading">
				<h2 ref={heading} tabIndex={-1}>
					Compare incoming requirements
				</h2>
				<p>
					{preview.source_name} · Revision {preview.base_project_revision}
				</p>
			</div>
			{preview.warnings.map((warning) => (
				<p key={warning} className="import-notice">
					{warning}
				</p>
			))}
			{preview.recovery_snapshot_ids.length > 0 && (
				<Disclosure>
					<summary>Restore saved baseline ({preview.current_requirements.length} requirements)</summary>
					{preview.current_requirements.map((r) => (
						<article key={r.requirement_uid}>
							<h4>
								{r.id} · {r.review_status}
							</h4>
							<p>{r.text}</p>
							<p className="helper-text">{r.sources?.map((source) => source.label).join(", ")}</p>
						</article>
					))}
				</Disclosure>
			)}
			{(stale || error) && <p role="alert">{error || "Project changed after this comparison. Compare again before applying."}</p>}
			<div className="import-review-toolbar">
				<Select ref={filterControl} aria-label="Filter import changes" value={filter} onChange={(e) => setFilter(e.target.value)}>
					<option value="all">All changes</option>
					{Object.entries(labels).map(([key, label]) => (
						<option key={key} value={key}>
							{label} ({key === "retiring" ? retiring.length : counts[key] || 0})
						</option>
					))}
				</Select>
				<p role="status">
					{counts.new || 0} added · {counts.updated || 0} updated · {retiring.length} retired · {activeCount} active requirements
				</p>
			</div>
			{rows.length > 0 ? (
				<TableScroll aria-label="Incoming requirement changes">
					<Table density="compact" className="review-rows import-candidate-table">
						<thead>
							<tr>
								<th scope="col">Incoming requirement</th>
								<th scope="col">Change</th>
								<th scope="col">Decision</th>
							</tr>
						</thead>
						<tbody>
							{rows.map((candidate) => {
								const { candidate_id: id } = candidate;
								const decision = decisions[id];
								const previous = current.get(decision.target_requirement_uid);
								const kind = kindOf(candidate);
								const expanded = openCandidate === id;
								const suggested = expanded && !decision.action && sourceMatches.size === 1 ? current.get(firstSourceMatch) : null;
								const comparison = previous || suggested;
								const duplicate = duplicateTargets.has(decision.target_requirement_uid);
								const decisionLabel = previous
									? `${decision.action === "keep" ? "Keep" : "Update"} ${previous.id}`
									: decision.action === "add"
										? "Add independently"
										: decision.action === "skip"
											? "Skip"
											: "Choose a decision";
								return (
									<Fragment key={id}>
										<tr className="import-candidate-row" data-expanded={expanded}>
											<th scope="row">
												<ReviewRowToggle
													expanded={expanded}
													controls={expanded ? detailId : undefined}
													onClick={() => setOpenCandidate(expanded ? null : id)}
													label={`Review incoming requirement: ${candidate.requirement.text}`}
												>
													{candidate.requirement.text}
												</ReviewRowToggle>
											</th>
											<td>
												<Badge
													className="workbench-status"
													icon={null}
													tone={kind === "needs_decision" ? "warning" : kind === "new" ? "success" : "neutral"}
												>
													{labels[kind]}
												</Badge>
											</td>
											<td>{duplicate ? "Resolve duplicate match" : decisionLabel}</td>
										</tr>
										{expanded && (
											<tr className="review-detail-row">
												<td colSpan={3} id={detailId}>
													<div className={`import-text-comparison${comparison ? "" : " single"}`}>
														{comparison && (
															<div>
																<h3>
																	{suggested ? "Suggested current requirement" : "Current requirement"} · {comparison.id}
																</h3>
																<p>{comparison.text}</p>
																{sourceMatches.has(comparison.requirement_uid) && (
																	<p className="helper-text">
																		Verified original IDs: {sourceMatches.get(comparison.requirement_uid).join(", ")}
																	</p>
																)}
															</div>
														)}
														<div>
															<h3>Incoming requirement</h3>
															<p>{candidate.requirement.text}</p>
														</div>
													</div>
													<p className="helper-text">{candidate.reason}</p>
													<label className="import-decision-field">
														Decision
														<Select
															aria-label={`Decision for ${id}`}
															value={decision.target_requirement_uid || decision.action}
															disabled={busy}
															onChange={(event) => update(candidate, event.target.value)}
														>
															<option value="">Choose a decision</option>
															<option value="add">Add independently</option>
															<option value="skip">Skip this incoming requirement</option>
															{sourceMatches.size > 0 && (
																<optgroup label="Verified source matches">
																	{Array.from(sourceMatches.keys(), (uid) => requirementOption(current.get(uid)))}
																</optgroup>
															)}
															{preview.current_requirements
																.filter((requirement) => !sourceMatches.has(requirement.requirement_uid))
																.map(requirementOption)}
														</Select>
													</label>
													{duplicate && (
														<p role="alert">Another incoming requirement targets this same requirement. Resolve the conflict.</p>
													)}
													<Disclosure>
														<summary>Source evidence and suggested matches</summary>
														<RequirementSourceEvidence requirement={candidate.requirement} />
														{candidate.suggestions.map((suggestion) => (
															<p key={suggestion.requirement_uid}>
																<strong>{current.get(suggestion.requirement_uid)?.id}</strong> — {suggestion.reason}
																<br />
																{current.get(suggestion.requirement_uid)?.text}
																{suggestion.verified_original_ids?.length > 0 && (
																	<>
																		<br />
																		Verified original IDs: {suggestion.verified_original_ids.join(", ")}
																	</>
																)}
															</p>
														))}
													</Disclosure>
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
				filter !== "retiring" && <CollectionState kind="filtered">No incoming requirements match this filter.</CollectionState>
			)}
			<Disclosure className="import-scope">
				<summary>Replacement scope ({scope.length} existing requirements)</summary>
				<p>
					Select only the existing requirements covered by this incoming version. Selected requirements without an incoming match will
					retire. Leave empty when adding a new source.
				</p>
				{preview.suggested_update_scope.length > 0 && (
					<Button
						className="secondary"
						disabled={busy}
						onClick={() => {
							setScope(preview.suggested_update_scope);
							setConfirm(false);
						}}
					>
						Use suggested refinement scope
					</Button>
				)}
				{preview.current_requirements.map((r) => (
					<label key={r.requirement_uid}>
						<Checkbox
							type="checkbox"
							checked={scope.includes(r.requirement_uid)}
							disabled={busy}
							onChange={(e) => {
								setScope((old) => (e.target.checked ? [...old, r.requirement_uid] : old.filter((uid) => uid !== r.requirement_uid)));
								setConfirm(false);
							}}
						/>{" "}
						<span>
							{r.id} — {r.text}
						</span>
					</label>
				))}
			</Disclosure>
			{retiring.length > 0 && (
				<div className="import-retirements">
					<h4>Retiring ({retiring.length})</h4>
					{retiring.map((r) => (
						<p key={r.requirement_uid}>
							<strong>{r.id}</strong> — {r.text}
						</p>
					))}
					<label>
						<Checkbox type="checkbox" checked={confirm} disabled={busy} onChange={(e) => setConfirm(e.target.checked)} /> Retire these{" "}
						{retiring.length} requirements when applying this update; retain their history.
					</label>
				</div>
			)}
			<div className="import-apply-bar" ref={actionBar}>
				<span>
					{stale
						? "Compare again before applying."
						: counts.needs_decision
							? `${counts.needs_decision} requirement(s) need a decision.`
							: "Ready to apply the reviewed changes."}
				</span>
				<Button variant="secondary" onClick={() => onAction("cancel")} disabled={busy}>
					Cancel import
				</Button>
				<Button className="secondary" disabled={busy || Boolean(preview.recovery_snapshot_ids.length)} onClick={() => onAction("compare")}>
					Compare again
				</Button>
				<Button
					disabled={busy || stale || Boolean(counts.needs_decision) || (retiring.length > 0 && !confirm)}
					onClick={() =>
						onAction("apply", {
							base_project_revision: preview.base_project_revision,
							decisions: Object.values(decisions),
							update_scope: scope,
							confirm_retirements: confirm,
						})
					}
				>
					{busy ? "Saving…" : "Apply reviewed changes"}
				</Button>
			</div>
		</section>
	);
}
