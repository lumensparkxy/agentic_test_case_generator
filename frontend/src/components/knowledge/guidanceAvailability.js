export const GUIDANCE_STAGES = {
	requirements: "Requirements",
	use_cases: "Use Cases",
	test_cases: "Test Cases",
	automation: "Automation",
};

export function entryEligibleForStage(entry, stage) {
	return (
		entry.status === "active" && Boolean(entry.active?.stages?.includes(stage)) && (entry.scope !== "personal" || entry.selected === true)
	);
}

// This is availability before input matching and context limits, not a predicted
// manifest. Mirror stages_for_run: an enabled Test Cases entry point can also
// use enabled Use Cases guidance for its internal planner. A held entry point
// never inherits an enabled upstream stage.
export function guidanceAvailability(data, stage, kind) {
	const flag = data?.features?.[stage]?.[kind];
	if (flag === false) return { label: "Disabled by configuration" };
	if (flag !== true) return { label: "Unavailable" };
	const candidates = kind === "skills" ? data?.skills : data?.entries;
	if (!Array.isArray(candidates)) return { label: "Unavailable" };
	const stages = [stage];
	if (stage === "test_cases") {
		const plannerFlag = data?.features?.use_cases?.[kind];
		if (typeof plannerFlag !== "boolean") return { label: "Unavailable" };
		if (plannerFlag) stages.push("use_cases");
	}
	const count = candidates.filter((item) =>
		stages.some((s) => (kind === "skills" ? item.stage === s : entryEligibleForStage(item, s)))
	).length;
	return { label: count ? "Enabled" : "No eligible entries", count };
}

export function entryStageAvailability(entry, stage, data) {
	if (!entryEligibleForStage(entry, stage)) {
		if (entry.status === "needs_confirmation") return "Needs confirmation";
		if (entry.status !== "active" || !entry.active) return "Not approved and active";
		if (entry.scope === "personal" && !entry.selected) return "Not selected for this project";
		if (stage === "test_cases" && entryEligibleForStage(entry, "use_cases")) {
			const runFlag = data?.features?.test_cases?.memory;
			const plannerFlag = data?.features?.use_cases?.memory;
			if (runFlag === false) return "Disabled by configuration";
			if (typeof runFlag !== "boolean" || typeof plannerFlag !== "boolean") return "Unavailable";
			if (plannerFlag) return "Eligible for Use Cases planning within Test Cases";
		}
		return "Not configured in the approved version";
	}
	const flag = data?.features?.[stage]?.memory;
	return flag === true ? "Eligible before run selection" : flag === false ? "Disabled by configuration" : "Unavailable";
}
