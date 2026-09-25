import { useEffect } from "react";
import useKnowledge from "../../hooks/useKnowledge";
import { Button } from "../ui/controls";
import { Badge } from "../ui/surfaces";
import { GUIDANCE_STAGES, guidanceAvailability } from "./guidanceAvailability";

export function GuidanceAvailability({ data, stage }) {
	const memory = guidanceAvailability(data, stage, "memory");
	return (
		<div className="guidance-availability" role="group" aria-label={`${GUIDANCE_STAGES[stage]} guidance availability`}>
			<strong>{GUIDANCE_STAGES[stage]}</strong>
			<div className="guidance-availability-values">
				{["skills", "memory"].map((kind) => {
					const state = guidanceAvailability(data, stage, kind);
					return (
						<span key={kind}>
							{kind === "skills" ? "Skills" : "Approved context"}: <Badge>{state.label}</Badge>
							{state.count !== undefined && ` · ${state.count} eligible`}
						</span>
					);
				})}
			</div>
			{memory.label === "Disabled by configuration" && (
				<p>
					Approved context is not injected directly into {GUIDANCE_STAGES[stage]} in this configuration. Review generated assumptions before
					approval. Facts may still be carried through upstream artifacts.
				</p>
			)}
			{stage === "test_cases" && (data?.features?.test_cases?.skills === true || data?.features?.test_cases?.memory === true) && (
				<p>Test Cases generation may also use enabled Use Cases guidance when planning scenarios.</p>
			)}
		</div>
	);
}

export default function GenerationGuidance({ request, projectId, stage }) {
	const knowledge = useKnowledge(request, projectId);
	const { load } = knowledge;
	useEffect(() => {
		window.addEventListener("focus", load);
		return () => window.removeEventListener("focus", load);
	}, [load]);
	// A failed or pending refresh must not present a cached flag as current.
	const data = knowledge.error || knowledge.loading ? null : knowledge.data;
	return (
		<section className="generation-guidance" aria-label="Guidance for next generation" aria-busy={knowledge.loading || undefined}>
			<h2>Guidance for next generation</h2>
			<GuidanceAvailability data={data} stage={stage} />
			<p>
				Availability is checked before matching run inputs and context limits. The saved run records what was actually used; these settings
				do not change earlier artifacts or their approval.
			</p>
			{stage === "automation" && <p>These settings apply to AI code generation. Execution preview is a deterministic check.</p>}
			{knowledge.loading && <p role="status">Checking guidance availability…</p>}
			{knowledge.error && <p aria-live="polite">Guidance availability could not be checked. Retry to see current settings.</p>}
			<Button variant="secondary" disabled={knowledge.loading} onClick={load}>
				Refresh guidance availability
			</Button>
		</section>
	);
}
