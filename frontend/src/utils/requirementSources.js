export function requirementSources(requirement) {
	return requirement.sources?.length
		? requirement.sources
		: [
				{
					label: requirement.source_path || requirement.source_issue_key || "Imported requirements",
					source_system: requirement.source_system,
					source_issue_url: requirement.source_issue_url,
					source_issue_key: requirement.source_issue_key,
					excerpt_verified: false,
					original_requirement_ids: [],
				},
			];
}

export function sourceMapping(requirements) {
	return {
		format: "requirement_source_mapping_v1",
		requirements: requirements.map((requirement) => ({
			normalized_id: requirement.id,
			requirement_uid: requirement.requirement_uid || null,
			content_version: requirement.content_version || 1,
			text: requirement.text,
			sources: requirementSources(requirement).map((source) => ({
				source_id: source.source_id || null,
				source_system: source.source_system || requirement.source_system || null,
				import_id: source.import_id || null,
				source_version: source.source_version || null,
				label: source.label,
				source_section: source.source_section || null,
				source_issue_key: source.source_issue_key || null,
				source_issue_url: source.source_issue_url || null,
				original_requirement_ids: source.excerpt_verified === true ? source.original_requirement_ids || [] : [],
				excerpt_verified: source.excerpt_verified === true,
				excerpt: source.excerpt_verified === true ? source.excerpt : null,
			})),
		})),
	};
}

export function downloadSourceMapping(requirements) {
	const url = URL.createObjectURL(new Blob([JSON.stringify(sourceMapping(requirements), null, 2)], { type: "application/json" }));
	const anchor = document.createElement("a");
	anchor.href = url;
	anchor.download = "requirement-source-mapping.json";
	anchor.click();
	setTimeout(() => URL.revokeObjectURL(url), 0);
}
