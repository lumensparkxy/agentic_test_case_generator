import { Button } from "../ui/controls";

export default function ClarificationScopeNotice({ project, onOpen }) {
	const rows = project?.current_snapshots?.requirements?.payload?.requirements || [];
	const pending = rows.filter((r) => ["open", "deferred", "excluded"].includes(r.clarification_status));
	if (!pending.length) return null;
	const ready = rows.filter((r) => r.review_status === "Approved" && r.lifecycle_status !== "retired" && !pending.includes(r));
	return (
		<section className="project-sync-summary" aria-label="Clarification scope">
			<strong>
				{pending.length} requirement{pending.length === 1 ? "" : "s"} awaiting a business decision
			</strong>
			<p>
				{pending.map((r) => `${r.id}: ${r.clarification_status}`).join(" · ")}. These requirements are outside new generation.{" "}
				{ready.length} other requirements retain approval.
			</p>
			<Button variant="secondary" onClick={onOpen}>
				Review business questions
			</Button>
		</section>
	);
}
