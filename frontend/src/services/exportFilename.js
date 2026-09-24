const EXPORT_EXTENSIONS = { csv: "csv", excel: "xlsx", json: "json" };

function filenamePart(value) {
	return Array.from(
		(value || "")
			.normalize("NFC")
			.toLowerCase()
			.replace(/[^\p{L}\p{N}\p{M}]+/gu, "-")
			.replace(/^-+|-+$/g, "")
	)
		.slice(0, 40)
		.join("")
		.replace(/-+$/g, "");
}

export function buildTestCaseExportFilename(project, format, exportedAt = new Date()) {
	const extension = EXPORT_EXTENSIONS[format];
	if (!extension) throw new Error(`Unsupported export format: ${format}`);
	const projectName = filenamePart(project?.name) || filenamePart(project?.project_id) || "project";
	const timestamp = exportedAt.toISOString().replace(/[:.]/g, "-");
	return `${projectName}_test-cases_${timestamp}.${extension}`;
}
