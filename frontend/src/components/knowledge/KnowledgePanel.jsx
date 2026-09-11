import { useId, useState } from "react";
import { createPortal } from "react-dom";
import useKnowledge from "../../hooks/useKnowledge";
import { Button, Checkbox, Field, Select, Textarea, Input, Link } from "../ui/controls";
import { Table, TableScroll, CollectionState, CollectionToolbar } from "../ui/collections";
import { Alert, Badge, Disclosure, Surface } from "../ui/surfaces";
import { Dialog } from "../ui/dialog";

const STAGES = { requirements: "Requirements", use_cases: "Use Cases", test_cases: "Test Cases", automation: "Automation" };
const LABELS = {
	active: "Active",
	suggested: "Suggested",
	needs_confirmation: "Needs confirmation",
	retired: "Retired",
	dismissed: "Dismissed",
};

function Modal({ title, onClose, children }) {
	const id = useId();
	return createPortal(
		<div className="ui-dialog-backdrop">
			<Dialog manageFocus onClose={onClose} aria-labelledby={id} className="knowledge-dialog">
				<h3 id={id}>{title}</h3>
				{children}
			</Dialog>
		</div>,
		document.body
	);
}

function EntryDialog({ entry, mode, personal, busy, error, onSubmit, onClose }) {
	const value = entry?.pending || entry?.active;
	const [text, setText] = useState(value?.text || "");
	const [kind, setKind] = useState(value?.kind || "convention");
	const [stages, setStages] = useState(value?.stages || Object.keys(STAGES));
	const [links, setLinks] = useState(mode === "promote" ? "" : (value?.requirement_ids || []).join(", "));
	const readOnly = ["approve", "delete", "retire", "view"].includes(mode);
	const title = {
		propose: "Add guidance",
		revise: "Edit proposed guidance",
		approve: "Approve knowledge",
		promote: "Promote to personal library",
		delete: "Delete guidance",
		retire: "Retire guidance",
		view: "Guidance details",
	}[mode];
	const submit = (event) => {
		event.preventDefault();
		void onSubmit({
			action: mode,
			entry_id: entry?.id,
			base_revision: entry?.revision || 0,
			...(!readOnly
				? {
						draft: {
							text,
							stages,
							kind,
							requirement_ids: links
								.split(",")
								.map((s) => s.trim())
								.filter(Boolean),
						},
					}
				: {}),
		});
	};
	return (
		<Modal title={title} onClose={busy ? undefined : onClose}>
			<form onSubmit={submit} className="knowledge-form">
				{mode === "promote" && <p>Generalize this lesson. It will need separate approval in your personal library.</p>}
				{mode === "delete" && (
					<Alert tone="warning">
						Delete the wording and its saved revisions. Future runs cannot use it; historical artifacts keep only its reference.
					</Alert>
				)}
				<Field>
					<label htmlFor="knowledge-text">Guidance</label>
					<Textarea
						id="knowledge-text"
						required
						maxLength={1000}
						value={text}
						readOnly={readOnly}
						onChange={(e) => setText(e.target.value)}
					/>
				</Field>
				<Field>
					<label htmlFor="knowledge-kind">Kind</label>
					<Select id="knowledge-kind" value={kind} disabled={readOnly} onChange={(e) => setKind(e.target.value)}>
						<option value="convention">Testing convention</option>
						<option value="terminology">Terminology</option>
						{!personal && mode !== "promote" && <option value="business_fact">Business fact</option>}
					</Select>
				</Field>
				<fieldset>
					<legend>Applies to</legend>
					{Object.entries(STAGES).map(([id, label]) => (
						<label key={id} className="knowledge-check">
							<Checkbox
								checked={stages.includes(id)}
								disabled={readOnly}
								onChange={() => setStages(stages.includes(id) ? stages.filter((s) => s !== id) : [...stages, id])}
							/>
							{label}
						</label>
					))}
				</fieldset>
				{!personal && mode !== "promote" && (
					<Field>
						<label htmlFor="knowledge-links">Source requirement IDs</label>
						<Input id="knowledge-links" value={links} readOnly={readOnly} onChange={(e) => setLinks(e.target.value)} />
						<p>Separate IDs with commas. Required for business facts.</p>
					</Field>
				)}
				<p>Source: {value?.source || "Manual guidance"}</p>
				{mode === "approve" && <p>This approves knowledge for future runs. It does not approve generated artifacts.</p>}
				{error && (
					<Alert tone="danger" role="alert">
						{error}
					</Alert>
				)}
				<div className="button-row">
					<Button type="button" variant="secondary" disabled={busy} onClick={onClose}>
						{mode === "view" ? "Close" : "Cancel"}
					</Button>
					{mode !== "view" && (
						<Button type="submit" disabled={busy || !text.trim() || !stages.length}>
							{busy
								? "Saving…"
								: mode === "approve"
									? "Approve knowledge"
									: mode === "delete"
										? "Delete guidance"
										: mode === "retire"
											? "Retire guidance"
											: "Save proposal"}
						</Button>
					)}
				</div>
			</form>
		</Modal>
	);
}

function PersonalChooser({ request, knowledge, onClose }) {
	const personal = useKnowledge(request, null);
	const entries = personal.data?.entries.filter((e) => e.status === "active") || [];
	return (
		<Modal title="Choose from personal library" onClose={onClose}>
			<p>Only selected entries will be available to this project.</p>
			{personal.error && (
				<Alert role="alert" tone="danger">
					{personal.error}
					<Button onClick={personal.load}>Retry</Button>
				</Alert>
			)}
			{knowledge.error && (
				<Alert role="alert" tone="danger">
					{knowledge.error}
				</Alert>
			)}
			{entries.map((entry) => (
				<label className="knowledge-check" key={entry.id}>
					<Checkbox
						aria-disabled={knowledge.busy}
						checked={knowledge.data?.entries.some((e) => e.id === entry.id && e.selected) || false}
						onChange={() =>
							void knowledge.mutate({
								action: knowledge.data?.entries.some((e) => e.id === entry.id && e.selected) ? "unselect" : "select",
								entry_id: entry.id,
								base_revision: entry.revision,
							})
						}
					/>
					{entry.active.text}
				</label>
			))}
			{!personal.loading && !entries.length && (
				<CollectionState>No approved personal guidance yet. Add and approve it in Settings → Personal knowledge.</CollectionState>
			)}
			<Button variant="secondary" onClick={onClose}>
				Done
			</Button>
		</Modal>
	);
}

export default function KnowledgePanel({ request, projectId, personal = false }) {
	const knowledge = useKnowledge(request, personal ? null : projectId);
	const [filter, setFilter] = useState("active");
	const [dialog, setDialog] = useState(null);
	const [chooser, setChooser] = useState(false);
	const entries = knowledge.data?.entries || [];
	const filtered = entries.filter((e) => filter === "all" || (filter === "suggested" ? !!e.pending : e.status === filter));
	const change = async (payload) => {
		if (await knowledge.mutate(payload)) {
			setDialog(null);
			if (payload.action === "propose") setFilter("suggested");
		}
	};
	return (
		<Surface className="knowledge-panel" aria-label={personal ? "Personal knowledge" : "Project knowledge"}>
			<h2>{personal ? "Personal knowledge" : "Project knowledge"}</h2>
			<p>
				{personal
					? "Reusable guidance you explicitly select for each project."
					: "Approved guidance for future generation. Suggestions stay inactive until approved."}
			</p>
			{!personal && knowledge.data && !Object.values(knowledge.data.features || {}).some((f) => f.memory) && (
				<p>Memory use is currently disabled. You can prepare guidance before rollout.</p>
			)}
			<CollectionToolbar className="knowledge-toolbar">
				<Select aria-label="Filter knowledge" value={filter} onChange={(e) => setFilter(e.target.value)}>
					{["active", "suggested", "needs_confirmation", "all"].map((s) => (
						<option key={s} value={s}>
							{s === "all" ? "All guidance" : LABELS[s]}
						</option>
					))}
				</Select>
				<Button disabled={knowledge.busy} onClick={() => setDialog({ mode: "propose" })}>
					Add guidance
				</Button>
				{!personal && (
					<Button variant="secondary" onClick={() => setChooser(true)}>
						Choose from personal library
					</Button>
				)}
			</CollectionToolbar>
			{knowledge.error && !dialog && (
				<Alert tone="danger" role="alert">
					{knowledge.error}
					<Button variant="secondary" onClick={knowledge.load}>
						Reload knowledge
					</Button>
				</Alert>
			)}
			{knowledge.loading && !knowledge.data ? (
				<CollectionState kind="loading">Loading knowledge…</CollectionState>
			) : filtered.length ? (
				<TableScroll aria-label="Knowledge entries">
					<Table>
						<thead>
							<tr>
								<th scope="col">Guidance</th>
								<th scope="col">Applies to</th>
								<th scope="col">Source</th>
								<th scope="col">Status</th>
								<th scope="col">Actions</th>
							</tr>
						</thead>
						<tbody>
							{filtered.map((entry) => {
								const value = entry.pending || entry.active;
								const shared = !personal && entry.scope === "personal";
								return (
									<tr key={entry.id}>
										<td>
											{value?.text || "Retired guidance"}
											{entry.pending && entry.active && <p>Active version: {entry.active.text}</p>}
										</td>
										<td>{value?.stages.map((s) => STAGES[s]).join(", ")}</td>
										<td>
											{shared ? "Personal library" : value?.source}
											{personal && entry.selected_by_projects.length > 0 && (
												<Disclosure>
													<summary>Selected by {entry.selected_by_projects.length} projects</summary>
													{entry.selected_by_projects.map((id) => (
														<p key={id}>
															<Link href={`/projects/${encodeURIComponent(id)}`}>Open project {id}</Link>
														</p>
													))}
												</Disclosure>
											)}
										</td>
										<td>
											<Badge tone={entry.status === "active" ? "success" : "warning"}>{LABELS[entry.status] || entry.status}</Badge>
											{entry.pending && entry.active && <Badge>Proposed edit</Badge>}
										</td>
										<td>
											<div className="knowledge-actions">
												{shared ? (
													<>
														<Button variant="plain" onClick={() => setDialog({ entry, mode: "view" })}>
															View
														</Button>
														<Button
															variant="plain"
															disabled={knowledge.busy}
															onClick={() => void change({ action: "unselect", entry_id: entry.id, base_revision: entry.revision })}
														>
															Remove from project
														</Button>
													</>
												) : (
													<>
														<Button variant="plain" disabled={knowledge.busy} onClick={() => setDialog({ entry, mode: "revise" })}>
															Edit
														</Button>
														{(entry.pending || entry.status === "needs_confirmation") && (
															<Button disabled={knowledge.busy} onClick={() => setDialog({ entry, mode: "approve" })}>
																Review
															</Button>
														)}
														{entry.pending && (
															<Button
																variant="plain"
																disabled={knowledge.busy}
																onClick={() => void change({ action: "dismiss", entry_id: entry.id, base_revision: entry.revision })}
															>
																Dismiss
															</Button>
														)}
														{entry.status === "active" && (
															<>
																<Button variant="plain" disabled={knowledge.busy} onClick={() => setDialog({ entry, mode: "retire" })}>
																	Retire
																</Button>
																{!personal && entry.active.kind !== "business_fact" && (
																	<Button variant="plain" onClick={() => setDialog({ entry, mode: "promote" })}>
																		Promote
																	</Button>
																)}
															</>
														)}
														<Button variant="plain" disabled={knowledge.busy} onClick={() => setDialog({ entry, mode: "delete" })}>
															Delete
														</Button>
													</>
												)}
											</div>
										</td>
									</tr>
								);
							})}
						</tbody>
					</Table>
				</TableScroll>
			) : (
				!knowledge.error && (
					<CollectionState kind={entries.length ? "filtered" : "empty"}>
						{entries.length
							? "No guidance matches this filter."
							: "No knowledge recorded yet. Add guidance or review suggestions after giving feedback."}
					</CollectionState>
				)
			)}
			{!personal && (
				<Disclosure>
					<summary>Skills used by this project</summary>
					<p>Curated methods are selected automatically by stage when skills are enabled.</p>
					{knowledge.data?.skills.map((s) => (
						<p key={s.id}>
							<strong>{STAGES[s.stage]}</strong> · {s.id} v{s.version} — {s.description}
						</p>
					))}
				</Disclosure>
			)}
			{dialog && (
				<EntryDialog
					{...dialog}
					personal={personal}
					busy={knowledge.busy}
					error={knowledge.error}
					onClose={() => setDialog(null)}
					onSubmit={change}
				/>
			)}
			{chooser && <PersonalChooser request={request} knowledge={knowledge} onClose={() => setChooser(false)} />}
		</Surface>
	);
}
