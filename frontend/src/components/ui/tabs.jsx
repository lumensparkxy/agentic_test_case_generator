import { Button } from "./controls";

export function TabList({ as: Element = "div", className = "", onKeyDown, ...props }) {
	return (
		<Element
			{...props}
			role="tablist"
			data-ui="tablist"
			className={`ui-tablist ${className}`}
			onKeyDown={(event) => {
				onKeyDown?.(event);
				if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
				const vertical = props["aria-orientation"] === "vertical";
				const previous = vertical ? "ArrowUp" : "ArrowLeft";
				const next = vertical ? "ArrowDown" : "ArrowRight";
				if (![previous, next, "Home", "End"].includes(event.key)) return;
				const tabs = [...event.currentTarget.querySelectorAll('[role="tab"]')].filter(
					(tab) => !tab.disabled && tab.getAttribute("aria-disabled") !== "true"
				);
				const index = tabs.indexOf(event.target);
				if (index < 0 || !tabs.length) return;
				const target =
					event.key === "Home"
						? tabs[0]
						: event.key === "End"
							? tabs.at(-1)
							: tabs[(index + (event.key === next ? 1 : tabs.length - 1)) % tabs.length];
				event.preventDefault();
				target.focus();
				target.click();
			}}
		/>
	);
}
export function Tab({ selected, className = "", ...props }) {
	const active = selected ?? props["aria-selected"];
	return (
		<Button
			variant="tab"
			{...props}
			role="tab"
			aria-selected={active}
			tabIndex={active ? 0 : -1}
			data-ui="tab"
			className={`ui-tab ${className}`}
		/>
	);
}
export function TabPanel({ as: Element = "div", className = "", ...props }) {
	return <Element role="tabpanel" tabIndex={0} {...props} data-ui="tabpanel" className={`ui-tabpanel ${className}`} />;
}
