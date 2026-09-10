import { Search } from "lucide-react";
import { Children, cloneElement, isValidElement, useId } from "react";

const classes = (...values) => values.filter(Boolean).join(" ");

export function Button({ variant, size = "normal", busy = false, disabled, type = "button", className, children, ...props }) {
	const appearance = variant || (className?.split(" ").includes("secondary") ? "secondary" : "primary");
	return (
		<button
			{...props}
			type={type}
			disabled={disabled || busy}
			aria-busy={busy || props["aria-busy"] || undefined}
			data-ui={props["data-ui"] || "button"}
			data-variant={appearance}
			data-size={size}
			className={classes("ui-button", className)}
		>
			{children}
		</button>
	);
}

export function Link({ className, ...props }) {
	return <a {...props} data-ui="link" className={classes("ui-link", className)} />;
}

export function Input({ type = "text", className, ...props }) {
	return <input {...props} type={type} data-ui="input" className={classes("ui-input", className)} />;
}
export function Checkbox(props) {
	return <Input {...props} type="checkbox" />;
}
export function Radio(props) {
	return <Input {...props} type="radio" />;
}
export function Select({ className, ...props }) {
	return <select {...props} data-ui="select" className={classes("ui-input", className)} />;
}
export function Textarea({ className, ...props }) {
	return <textarea {...props} data-ui="textarea" className={classes("ui-input", className)} />;
}

// Existing composed fields can pass children only; labeled fields associate their
// control, help and error text without owning its value or validation policy.
export function Field({ id, label, hint, error, children, className, ...props }) {
	const generatedId = useId();
	const parts = Children.toArray(children);
	const controls = parts.filter((child) => isValidElement(child) && [Input, Select, Textarea, Checkbox, Radio].includes(child.type));
	const singleControl = controls.length === 1 ? controls[0] : null;
	const controlId = id || singleControl?.props.id || generatedId;
	const hints = parts.filter((child) => isValidElement(child) && /(?:hint|helper-text)/.test(child.props.className || ""));
	const describedBy = [
		hint && `${controlId}-hint`,
		error && `${controlId}-error`,
		...hints.map((child, index) => child.props.id || `${controlId}-hint-${index}`),
	]
		.filter(Boolean)
		.join(" ");
	const associate = (child) =>
		cloneElement(child, {
			id: controlId,
			"aria-describedby": [child.props["aria-describedby"], describedBy].filter(Boolean).join(" ") || undefined,
			"aria-invalid": error ? true : child.props["aria-invalid"],
		});
	const control =
		label && isValidElement(children)
			? associate(children)
			: parts.map((child) => {
					if (!isValidElement(child) || !singleControl) return child;
					if (child === singleControl) return associate(child);
					if (child.type === "label") return cloneElement(child, { htmlFor: child.props.htmlFor || controlId });
					const hintIndex = hints.indexOf(child);
					return hintIndex >= 0 ? cloneElement(child, { id: child.props.id || `${controlId}-hint-${hintIndex}` }) : child;
				});
	return (
		<div {...props} data-ui="field" className={classes("ui-field", className)}>
			{label && <label htmlFor={controlId}>{label}</label>}
			{control}
			{hint && (
				<p id={`${controlId}-hint`} className="ui-field-hint">
					{hint}
				</p>
			)}
			{error && (
				<p id={`${controlId}-error`} className="ui-field-error" role="alert">
					{error}
				</p>
			)}
		</div>
	);
}

export function SearchField({ label, className, shortcut, ...props }) {
	return (
		<label className={classes("ui-search", className)}>
			<span className="sr-only">{label}</span>
			<Search size={18} aria-hidden="true" />
			<Input {...props} type="search" />
			{shortcut && <kbd aria-hidden="true">{shortcut}</kbd>}
		</label>
	);
}
