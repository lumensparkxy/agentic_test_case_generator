import { CircleAlert, CircleCheck, Circle, Clock3, LockKeyhole, XCircle } from "lucide-react";
const classes = (...values) => values.filter(Boolean).join(" ");
const icons = {
	success: CircleCheck,
	warning: CircleAlert,
	danger: XCircle,
	pending: Circle,
	blocked: LockKeyhole,
	running: Clock3,
	neutral: Circle,
	info: Circle,
};

export function Badge({ tone = "neutral", icon: Icon = icons[tone], children, variant = "subtle", compact = false, className, ...props }) {
	return (
		<span
			{...props}
			data-ui="badge"
			data-variant={variant}
			data-compact={compact || undefined}
			data-tone={tone}
			className={classes("ui-badge", className)}
		>
			{Icon && <Icon size={14} aria-hidden="true" />}
			<span className="ui-badge-label">{children}</span>
		</span>
	);
}
export function Surface({ as: Element = "section", tone = "neutral", className, ...props }) {
	return <Element {...props} data-ui="surface" data-tone={tone} className={classes("ui-surface", className)} />;
}
export function Alert({ as: Element = "div", tone = "info", className, role, ...props }) {
	return <Element {...props} role={role} data-ui="alert" data-tone={tone} className={classes("ui-alert", className)} />;
}
export function Disclosure({ className, ...props }) {
	return <details {...props} data-ui="disclosure" className={classes("ui-disclosure", className)} />;
}
export function Progress({ className, ...props }) {
	return <progress {...props} data-ui="progress" className={classes("ui-progress", className)} />;
}
export function Menu({ as: Element = "div", className, ...props }) {
	return <Element role="menu" {...props} data-ui="menu" className={classes("ui-menu", className)} />;
}
