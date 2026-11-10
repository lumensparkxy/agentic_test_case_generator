import { Button } from "../ui/controls";
import { Disclosure } from "../ui/surfaces";

function PreviousDraft({ value }) {
	if (typeof value === "string") return <p>{value}</p>;
	const labels = {
		query: "Search",
		selectedId: "Selected test",
		caseId: "Reported test",
		instructions: "Instructions",
		comment: "Review comment",
		decision: "Unsaved decision",
		statusFilter: "Review filter",
	};
	return (
		<>
			{Object.entries(labels)
				.filter(([key]) => value?.[key])
				.map(([key, label]) => (
					<p key={key}>
						<strong>{label}:</strong> {String(value[key]).replaceAll("_", " ")}
					</p>
				))}
			{value?.selected?.length > 0 && (
				<ul>
					{value.selected.map((item) => (
						<li key={item}>{item}</li>
					))}
				</ul>
			)}
			{Object.entries(value?.caseInstructions || {})
				.filter(([, instructions]) => instructions)
				.map(([caseId, instructions]) => (
					<p key={caseId}>
						<strong>Report for {caseId}</strong> · {instructions}
					</p>
				))}
			{Object.values(value?.items || {}).map((item) => (
				<p key={item.scenario_id}>
					<strong>{item.scenario_id}:</strong> {item.status?.replaceAll("_", " ")}
					{item.quality_flags?.length ? ` · ${item.quality_flags.join(", ")}` : ""}
				</p>
			))}
		</>
	);
}

export default function DraftRecoveryNotice({ draft }) {
	return (
		<>
			{draft.error && <p role="alert">{draft.error}</p>}
			{draft.stale && (
				<Disclosure className="draft-recovery-notice">
					<summary>Previous source draft kept for reference</summary>
					<p>The source changed. This draft has not been applied to the current review. Compare it before making a new decision.</p>
					<PreviousDraft value={draft.stale.value} />
					<Button variant="secondary" onClick={draft.discardStale}>
						Discard previous draft
					</Button>
				</Disclosure>
			)}
		</>
	);
}
