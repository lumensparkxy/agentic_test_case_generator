import { useEffect, useRef, useState } from "react";
import { Button, Checkbox, Select } from "../ui/controls";
import { Disclosure, Surface } from "../ui/surfaces";

const labels = {
	new: "New",
	updated: "Updated",
	unchanged: "Unchanged",
	needs_decision: "Needs decision",
	skipped: "Skipped",
	retiring: "Retiring",
};
const normalize = (value) => value.trim().replace(/\s+/g, " ");

export default function RequirementImportReview({ preview, revision, busy, error, onAction }) {
	const heading = useRef(null);
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
		heading.current?.focus();
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
	const stale = revision !== preview.base_project_revision;
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
	return (
		<Surface as="section" className="requirement-import-review" aria-label="Compare incoming requirements">
			<div className="import-review-heading">
				<div>
					<p className="helper-text">
						Import → <strong>Compare changes</strong> → Apply
					</p>
					<h3 ref={heading} tabIndex={-1}>
						Compare incoming requirements
					</h3>
					<p>
						{preview.source_name} · Comparing with project revision {preview.base_project_revision}
					</p>
				</div>
				<Button className="secondary" onClick={() => onAction("cancel")} disabled={busy}>
					Cancel import
				</Button>
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
			{stale && <p role="alert">Project changed after this comparison. Compare again before applying.</p>}
			{error && <p role="alert">{error}</p>}
			<div className="import-review-toolbar">
				<label>
					Show changes{" "}
					<Select aria-label="Filter import changes" value={filter} onChange={(e) => setFilter(e.target.value)}>
						<option value="all">All changes</option>
						{Object.entries(labels).map(([key, label]) => (
							<option key={key} value={key}>
								{label} ({key === "retiring" ? retiring.length : counts[key] || 0})
							</option>
						))}
					</Select>
				</label>
				<p role="status">
					{counts.new || 0} added · {counts.updated || 0} updated · {retiring.length} retired · {activeCount} active requirements
				</p>
			</div>
			<div className="import-comparison-list">
				{preview.candidates
					.filter((c) => filter === "all" || kindOf(c) === filter)
					.map((candidate) => {
						const decision = decisions[candidate.candidate_id];
						const previous = current.get(decision.target_requirement_uid);
						const kind = kindOf(candidate);
						return (
							<article className="import-comparison-card" key={candidate.candidate_id}>
								<div className="import-candidate-heading">
									<strong>{labels[kind]}</strong>
									<span>{previous?.id || "New requirement"}</span>
								</div>
								<div className="import-text-comparison">
									<div>
										<h4>Current requirement</h4>
										<p>{previous?.text || "Select an existing requirement to compare, or add independently."}</p>
									</div>
									<div>
										<h4>Incoming requirement</h4>
										<p>{candidate.requirement.text}</p>
									</div>
								</div>
								<p className="helper-text">{candidate.reason}</p>
								<label>
									How should this requirement be handled?
									<Select
										aria-label={`Decision for ${candidate.candidate_id}`}
										value={decision.target_requirement_uid || decision.action}
										disabled={busy}
										onChange={(e) => update(candidate, e.target.value)}
									>
										<option value="">Choose a decision</option>
										<option value="add">Add independently</option>
										<option value="skip">Skip this incoming requirement</option>
										{preview.current_requirements.map((r) => (
											<option key={r.requirement_uid} value={r.requirement_uid}>
												{r.id} — {r.text}
											</option>
										))}
									</Select>
								</label>
								{duplicateTargets.has(decision.target_requirement_uid) && (
									<p role="alert">Another incoming requirement targets this same requirement. Resolve the conflict.</p>
								)}
								<Disclosure>
									<summary>Source evidence and suggested matches</summary>
									<p>{candidate.requirement.source_excerpt || candidate.requirement.text}</p>
									<p>{candidate.requirement.source_path || preview.source_name}</p>
									{candidate.suggestions.map((s) => (
										<p key={s.requirement_uid}>
											<strong>{current.get(s.requirement_uid)?.id}</strong> — {s.reason}
											<br />
											{current.get(s.requirement_uid)?.text}
										</p>
									))}
								</Disclosure>
							</article>
						);
					})}
			</div>
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
			<div className="import-apply-bar">
				<span>
					{counts.needs_decision ? `${counts.needs_decision} requirement(s) need a decision.` : "Ready to apply the reviewed changes."}
				</span>
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
		</Surface>
	);
}
