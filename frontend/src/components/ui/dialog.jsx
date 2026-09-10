import { useEffect, useRef } from "react";

export function Dialog({ as: Element = "div", className = "", onClose, manageFocus = false, ref: externalRef, ...props }) {
	const internalRef = useRef(null);
	const closeRef = useRef(onClose);
	useEffect(() => {
		closeRef.current = onClose;
	});
	useEffect(() => {
		if (!manageFocus) return;
		const previous = document.activeElement;
		const node = internalRef.current;
		const targets = () =>
			[...node.querySelectorAll("button, a[href], input, select, textarea, [tabindex]")].filter(
				(element) => !element.disabled && element.tabIndex >= 0 && element.getClientRects().length
			);
		(targets()[0] || node).focus();
		const keydown = (event) => {
			if (event.key === "Escape" && closeRef.current) {
				event.preventDefault();
				event.stopPropagation();
				closeRef.current();
			}
			if (event.key !== "Tab") return;
			const items = targets();
			const first = items[0];
			const last = items.at(-1);
			if (!items.length) {
				event.preventDefault();
				node.focus();
			} else if (event.shiftKey && (document.activeElement === first || document.activeElement === node)) {
				event.preventDefault();
				last.focus();
			} else if (!event.shiftKey && document.activeElement === last) {
				event.preventDefault();
				first.focus();
			}
		};
		node.addEventListener("keydown", keydown);
		return () => {
			node.removeEventListener("keydown", keydown);
			if (previous?.isConnected) previous.focus();
		};
	}, [manageFocus]);
	return (
		<Element
			role="dialog"
			aria-modal="true"
			tabIndex={-1}
			{...props}
			ref={(node) => {
				internalRef.current = node;
				if (typeof externalRef === "function") externalRef(node);
				else if (externalRef) externalRef.current = node;
			}}
			data-ui="dialog"
			className={`ui-dialog ${className}`}
		/>
	);
}
