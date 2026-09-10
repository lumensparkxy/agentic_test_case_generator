import { ResizablePanes } from "./resizable-panes";

const classes = (...values) => values.filter(Boolean).join(" ");

export function Table({ density = "comfortable", className, ...props }) {
	return <table {...props} data-ui="table" data-density={density} className={classes("ui-table", className)} />;
}
export function TableScroll({ as: Element = "div", className, ...props }) {
	return <Element role="region" tabIndex={0} {...props} data-ui="table-scroll" className={classes("ui-table-scroll", className)} />;
}
export function List({ as: Element = "ul", variant = "plain", className, ...props }) {
	return <Element {...props} data-ui="list" data-variant={variant} className={classes("ui-list", className)} />;
}
export function ListItem({ as: Element = "li", className, ...props }) {
	return <Element {...props} data-ui="list-item" className={classes("ui-list-item", className)} />;
}
export function SelectableItem({ selected, className, ...props }) {
	return (
		<button
			type="button"
			{...props}
			aria-pressed={selected ?? props["aria-pressed"]}
			data-ui="selectable-item"
			className={classes("ui-selectable-item", className)}
		/>
	);
}
export function ListDetail({ as: Element = "section", className, ...props }) {
	return <ResizablePanes as={Element} {...props} data-ui="list-detail" className={classes("ui-list-detail", className)} />;
}
export function CollectionToolbar({ as: Element = "div", className, ...props }) {
	return <Element {...props} data-ui="collection-toolbar" className={classes("ui-collection-toolbar", className)} />;
}
export function ResultCount({ as: Element = "p", className, ...props }) {
	return (
		<Element
			role="status"
			aria-live="polite"
			aria-atomic="true"
			{...props}
			data-ui="result-count"
			className={classes("ui-result-count", className)}
		/>
	);
}
export function CollectionState({ as: Element = "div", kind = "empty", title, description, action, children, className, ...props }) {
	return (
		<Element
			role={kind === "error" ? "alert" : kind === "loading" ? "status" : undefined}
			aria-busy={kind === "loading" || undefined}
			{...props}
			data-ui="collection-state"
			data-kind={kind}
			className={classes("ui-collection-state", className)}
		>
			{title && <h3>{title}</h3>}
			{description && <p>{description}</p>}
			{children}
			{action}
		</Element>
	);
}
