import { useRef, useState } from "react";
import { Button, Checkbox } from "../ui/controls";
import { Dialog } from "../ui/dialog";

const identityDefaults = { requirement_uid: null, content_version: 1, clarification_status: null, parent_requirement_id: null };

export default function UseCaseRefreshDialog({ requirements, priorPlan, priorIdentities, contextChanged, onClose, onConfirm }) {
	const dialog = useRef(null);
	const [additional, setAdditional] = useState([]);
	const previous = new Map(priorPlan.map((group) => [group.requirement_id, group.requirement_text]));
	const currentIds = new Set(requirements.map((row) => row.id));
	const added = requirements.filter((row) => !previous.has(row.id));
	const changed = requirements.filter((row) => {
		if (!previous.has(row.id)) return false;
		const identity = priorIdentities?.[row.id];
		return (
			previous.get(row.id) !== row.text ||
			(identity && Object.entries(identityDefaults).some(([key, fallback]) => (row[key] ?? fallback) !== (identity[key] ?? fallback)))
		);
	});
	const changedIds = new Set(changed.map((row) => row.id));
	const unchanged = requirements.filter((row) => previous.has(row.id) && !changedIds.has(row.id));
	const removed = priorPlan.filter((group) => !currentIds.has(group.requirement_id));
	const refreshIds = [...changed.map((row) => row.id), ...added.map((row) => row.id), ...additional];
	return (
		<div className="auth-dialog-overlay">
			<Dialog
				ref={dialog}
				className="use-case-review-dialog"
				manageFocus
				initialFocusRef={dialog}
				onClose={onClose}
				aria-labelledby="use-case-refresh-title"
				aria-describedby="use-case-refresh-summary"
			>
				<form
					className="use-case-decision-panel"
					onSubmit={(event) => {
						event.preventDefault();
						onConfirm(refreshIds);
					}}
				>
					<h2 id="use-case-refresh-title">Review Use Cases refresh scope</h2>
					<p id="use-case-refresh-summary" role="status">
						{changed.length} changed · {added.length} added · {unchanged.length - additional.length} kept · {removed.length} removed
						{additional.length > 0 ? ` · ${additional.length} additionally selected` : ""}
					</p>
					<p>The combined plan will be reviewed against current Context and require a new human decision.</p>
					{contextChanged && <p>Context was saved again after this plan. Include any other requirements it affects.</p>}
					{[
						["Changed requirements", changed],
						["Added requirements", added],
					].map(([label, rows]) =>
						rows.length ? (
							<section key={label} aria-label={label}>
								<h3>{label}</h3>
								<ul>
									{rows.map((row) => (
										<li key={row.id}>
											<strong>{row.id}</strong> {row.text}
										</li>
									))}
								</ul>
							</section>
						) : null
					)}
					{removed.length > 0 && (
						<section aria-label="Removed from this plan">
							<h3>Removed from this plan</h3>
							<ul>
								{removed.map((group) => (
									<li key={group.requirement_id}>
										<strong>{group.requirement_id}</strong> {group.requirement_text}
									</li>
								))}
							</ul>
						</section>
					)}
					{unchanged.length > 0 && (
						<fieldset className="use-case-decision-options">
							<legend>Keep previous scenarios</legend>
							<p>Select any additional requirement whose scenarios need to reflect Context changes.</p>
							{unchanged.map((row) => (
								<label key={row.id}>
									<Checkbox
										aria-label={`Also refresh ${row.id}`}
										checked={additional.includes(row.id)}
										onChange={(event) => {
											const checked = event.target.checked;
											setAdditional((selected) => (checked ? [...selected, row.id] : selected.filter((id) => id !== row.id)));
										}}
									/>
									<span>
										<strong>{row.id}</strong> {row.text}
									</span>
								</label>
							))}
						</fieldset>
					)}
					<div className="use-case-decision-actions">
						<Button type="button" variant="secondary" onClick={onClose}>
							Cancel
						</Button>
						<Button type="submit" disabled={!refreshIds.length && !removed.length}>
							Confirm scoped refresh
						</Button>
					</div>
				</form>
			</Dialog>
		</div>
	);
}
