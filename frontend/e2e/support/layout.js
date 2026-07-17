import { expect } from "@playwright/test";

export async function settleLayout(page) {
	await page.evaluate(
		() =>
			new Promise((resolve) => {
				window.requestAnimationFrame(() => window.requestAnimationFrame(resolve));
			})
	);
}

export async function expectVisuallyContained(target, container, tolerance = 2) {
	const [targetBox, containerBox] = await Promise.all([target.boundingBox(), container.boundingBox()]);
	expect(targetBox, "Expected the target to have a visible bounding box").not.toBeNull();
	expect(containerBox, "Expected the container to have a visible bounding box").not.toBeNull();
	expect(targetBox.x).toBeGreaterThanOrEqual(containerBox.x - tolerance);
	expect(targetBox.y).toBeGreaterThanOrEqual(containerBox.y - tolerance);
	expect(targetBox.x + targetBox.width).toBeLessThanOrEqual(containerBox.x + containerBox.width + tolerance);
	expect(targetBox.y + targetBox.height).toBeLessThanOrEqual(containerBox.y + containerBox.height + tolerance);
}

export async function expectWithinInitialViewport(locator, page, tolerance = 2) {
	const [box, viewport] = await Promise.all([locator.boundingBox(), page.evaluate(() => ({ width: innerWidth, height: innerHeight }))]);
	expect(box, "Expected the element to have a visible bounding box").not.toBeNull();
	expect(box.x).toBeGreaterThanOrEqual(-tolerance);
	expect(box.y).toBeGreaterThanOrEqual(-tolerance);
	expect(box.x + box.width).toBeLessThanOrEqual(viewport.width + tolerance);
	expect(box.y + box.height).toBeLessThanOrEqual(viewport.height + tolerance);
}

export async function expectExactlyOneCurrent(navigation) {
	const current = navigation.locator('[aria-current="page"]');
	const active = navigation.locator(".active");
	await expect(current).toHaveCount(1);
	await expect(active).toHaveCount(1);
	expect(await current.evaluate((element) => element.classList.contains("active"))).toBe(true);
	expect(await active.evaluate((element) => element.getAttribute("aria-current"))).toBe("page");
}

export async function readHorizontalScrollRegions(page) {
	return page.evaluate(() =>
		Array.from(document.querySelectorAll("body *"))
			.filter((element) => {
				const style = getComputedStyle(element);
				return ["auto", "scroll"].includes(style.overflowX) && element.scrollWidth > element.clientWidth + 2;
			})
			.map((element) => ({
				left: Math.round(element.getBoundingClientRect().left),
				right: Math.round(element.getBoundingClientRect().right),
				width: Math.round(element.getBoundingClientRect().width),
				parentClassName: `${element.parentElement?.className || ""}`,
				parentDisplay: element.parentElement ? getComputedStyle(element.parentElement).display : "",
				parentFlexDirection: element.parentElement ? getComputedStyle(element.parentElement).flexDirection : "",
				tag: element.tagName.toLowerCase(),
				className: `${element.className || ""}`,
				role: element.getAttribute("role") || "",
				label: element.getAttribute("aria-label") || "",
				labelledBy: element.getAttribute("aria-labelledby") || "",
				tabIndex: element.tabIndex,
				hasIntrinsicContent:
					element.matches("table, pre, code") || Boolean(element.querySelector(":scope > table, :scope > pre, :scope > code")),
			}))
	);
}

export async function expectNoDocumentOverflow(page, label, tolerance = 2) {
	await settleLayout(page);
	const metrics = await page.evaluate((allowedTolerance) => {
		const isAllowedScrollRegion = (element) => {
			const style = getComputedStyle(element);
			const isScrollable = ["auto", "scroll"].includes(style.overflowX) && element.scrollWidth > element.clientWidth + allowedTolerance;
			const isNamed = Boolean(element.getAttribute("aria-label") || element.getAttribute("aria-labelledby"));
			const isRegion = element.getAttribute("role") === "region";
			const hasIntrinsicContent =
				element.matches("table, pre, code") || Boolean(element.querySelector(":scope > table, :scope > pre, :scope > code"));
			return isScrollable && isNamed && isRegion && hasIntrinsicContent;
		};

		const belongsToAllowedScrollRegion = (element) => {
			let current = element.parentElement;
			while (current && current !== document.body) {
				if (isAllowedScrollRegion(current)) return true;
				current = current.parentElement;
			}
			return false;
		};

		const viewportWidth = window.innerWidth;
		const overflowing = Array.from(document.querySelectorAll("body *"))
			.filter((element) => {
				const rect = element.getBoundingClientRect();
				if (!rect.width || !rect.height || belongsToAllowedScrollRegion(element)) return false;
				return rect.left < -allowedTolerance || rect.right > viewportWidth + allowedTolerance;
			})
			.slice(0, 8)
			.map((element) => ({
				left: Math.round(element.getBoundingClientRect().left),
				right: Math.round(element.getBoundingClientRect().right),
				width: Math.round(element.getBoundingClientRect().width),
				parentClassName: `${element.parentElement?.className || ""}`,
				parentDisplay: element.parentElement ? getComputedStyle(element.parentElement).display : "",
				parentFlexDirection: element.parentElement ? getComputedStyle(element.parentElement).flexDirection : "",
				tag: element.tagName.toLowerCase(),
				className: `${element.className || ""}`,
				text: element.textContent?.trim().replace(/\s+/g, " ").slice(0, 90) || "",
			}));

		return {
			viewportWidth,
			scrollWidth: document.documentElement.scrollWidth,
			overflowing,
		};
	}, tolerance);

	const message = `${label}: ${JSON.stringify(metrics)}`;
	expect(metrics.scrollWidth, message).toBeLessThanOrEqual(metrics.viewportWidth + tolerance);
	expect(metrics.overflowing, message).toEqual([]);
}

export async function readCenterWidth(page) {
	await settleLayout(page);
	return page.evaluate(() => {
		const center = document.querySelector(".workflow-shell > .workflow-main, .workflow-shell > .route-page");
		return center ? Math.round(center.getBoundingClientRect().width) : 0;
	});
}
