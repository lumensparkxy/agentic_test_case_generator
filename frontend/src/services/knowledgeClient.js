export async function knowledgeRequest(request, path, options = {}) {
	const response = await request(path, options);
	const data = await response.json();
	if (!response.ok) {
		const detail = data?.detail;
		const error = new Error(typeof detail === "string" ? detail : detail?.message || detail?.[0]?.msg || "Knowledge is unavailable.");
		error.status = response.status;
		error.fieldErrors = {};
		if (response.status === 422 && Array.isArray(detail)) {
			for (const issue of detail) {
				if (Array.isArray(issue?.loc) && issue.loc.slice(-2).join(".") === "draft.text") {
					error.fieldErrors.text =
						issue.type === "string_too_long"
							? "Guidance must contain no more than 1000 characters."
							: typeof issue.msg === "string"
								? issue.msg
								: "Guidance was rejected by the server.";
				}
			}
		}
		throw error;
	}
	return data;
}
