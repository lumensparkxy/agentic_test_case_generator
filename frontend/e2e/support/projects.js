function escapeRegExp(value) {
	return `${value}`.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export async function openQaProjectMenu(page) {
	await page.getByRole("button", { name: /^Open QA project menu$/i }).click();
	return page.getByRole("dialog", { name: /^Projects$/i });
}

export async function openQaProjectByName(page, projectName) {
	const menu = await openQaProjectMenu(page);
	await menu.getByRole("button", { name: new RegExp(`^Open QA project ${escapeRegExp(projectName)}$`, "i") }).click();
}

export async function createQaProjectFromMenu(page, projectName) {
	const menu = await openQaProjectMenu(page);
	await menu.getByPlaceholder("New QA project name").fill(projectName);
	await menu.getByRole("button", { name: /^New Project$/i }).click();
}
