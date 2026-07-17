import AxeBuilder from "@axe-core/playwright";
import { expect } from "@playwright/test";

const WCAG_AA_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

export async function settleAccessibilityLayout(page) {
	await page.addStyleTag({
		content: `
			*, *::before, *::after {
				animation-delay: 0s !important;
				animation-duration: 0.001ms !important;
				scroll-behavior: auto !important;
				transition-delay: 0s !important;
				transition-duration: 0.001ms !important;
			}
		`,
	});
	await page.evaluate(
		() =>
			new Promise((resolve) => {
				window.requestAnimationFrame(() => window.requestAnimationFrame(resolve));
			})
	);
}

export async function expectNoSeriousOrCriticalViolations(page, surface) {
	await settleAccessibilityLayout(page);
	const results = await new AxeBuilder({ page }).withTags(WCAG_AA_TAGS).analyze();
	const violations = results.violations
		.filter((violation) => ["serious", "critical"].includes(violation.impact))
		.map((violation) => ({
			id: violation.id,
			impact: violation.impact,
			help: violation.help,
			nodes: violation.nodes.map((node) => ({ target: node.target, summary: node.failureSummary })),
		}));

	expect(violations, `${surface} has serious or critical WCAG A/AA violations`).toEqual([]);
}
