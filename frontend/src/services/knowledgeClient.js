export async function knowledgeRequest(request, path, options = {}) {
	const response = await request(path, options);
	const data = await response.json();
	if (!response.ok) {
		const detail = data?.detail;
		const error = new Error(typeof detail === "string" ? detail : detail?.message || detail?.[0]?.msg || "Knowledge is unavailable.");
		error.status = response.status;
		throw error;
	}
	return data;
}
