import { CheckCircle2, LoaderCircle, AlertCircle } from "lucide-react";
import { Button } from "../ui/controls";

export default function ImpactApplicationBar({
	state,
	operation,
	recommendations,
	error,
	disabled,
	analysisDisabled,
	approvalRequired,
	onApply,
	onCheck,
	onReview,
	onAnalyze,
}) {
	const selected = recommendations.filter((item) =>
		operation ? operation.accepted_recommendation_ids.includes(item.recommendation_id) : item.accepted
	);
	const summary = [
		["keep", "keep unchanged"],
		["update", "update"],
		["add", "add coverage"],
		["deprecate", "deprecate"],
	]
		.map(([action, label]) => [selected.filter((item) => item.action === action).length, label])
		.filter(([count]) => count)
		.map(([count, label]) => `${count} ${label}`)
		.join(" · ");
	const busy = ["applying", "submitting", "checking"].includes(state);
	const applied = state === "applied";
	const verify = state === "verification_required";
	return (
		<section className={`impact-application-bar ${applied ? "is-applied" : ""}`} aria-label="Recommendation application">
			<div role="status" aria-live="polite" aria-atomic="true">
				<strong>
					{busy ? (
						<LoaderCircle className="impact-progress-spinner" size={18} aria-hidden="true" />
					) : applied ? (
						<CheckCircle2 size={18} aria-hidden="true" />
					) : ["failed", "unreachable"].includes(state) ? (
						<AlertCircle size={18} aria-hidden="true" />
					) : null}
					{verify
						? "Application status needs verification"
						: applied
							? "Recommendations applied"
							: state === "applying" || state === "submitting"
								? "Applying recommendations…"
								: state === "checking"
									? "Checking application status…"
									: state === "unreachable"
										? "Application status unavailable"
										: state === "failed" || verify
											? "Recommendations were not applied"
											: approvalRequired
												? "Approval required"
												: "Ready to apply"}
				</strong>
				<p>
					{verify
						? operation.error
						: applied
							? `${operation.preserved_count} preserved · ${operation.updated_count} updated · ${operation.added_count} added · ${operation.deprecated_count} deprecated`
							: state === "applying"
								? "Your request was received."
								: state === "submitting"
									? "Sending your request…"
									: state === "failed" || verify
										? operation.error
										: ["checking", "unreachable"].includes(state)
											? "We’ll check the saved result before another attempt."
											: summary || "Select recommendations to apply."}
				</p>
				{applied && (
					<p>
						<time dateTime={operation.completed_at}>
							{operation.completed_at ? new Date(operation.completed_at).toLocaleString() : "Saved"}
						</time>{" "}
						· Review the changed tests before export.
					</p>
				)}
				{approvalRequired && !applied && <p>Approve changed requirements/use cases before applying impact updates.</p>}
				{error && <p>{error}</p>}
			</div>
			<div className="impact-application-buttons">
				<Button
					onClick={applied ? onReview : verify ? onAnalyze : state === "unreachable" ? onCheck : onApply}
					disabled={busy || (verify && analysisDisabled) || (!applied && !verify && state !== "unreachable" && disabled)}
				>
					{verify
						? "Analyze impact again"
						: applied
							? "Review changed tests"
							: state === "unreachable"
								? "Check status"
								: busy
									? "Applying…"
									: state === "failed" || verify
										? "Retry"
										: `Apply ${selected.length} recommendation${selected.length === 1 ? "" : "s"}`}
				</Button>
				{applied && operation.accepted_recommendation_ids.length < recommendations.length && (
					<Button variant="secondary" onClick={onAnalyze} disabled={analysisDisabled}>
						Analyze remaining work
					</Button>
				)}
			</div>
		</section>
	);
}
