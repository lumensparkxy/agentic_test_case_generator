import { CheckCircle2, Circle, CircleAlert, Clock3, LockKeyhole, MapPin, XCircle } from "lucide-react";

const STATUS_ICONS = Object.freeze({
	active: MapPin,
	complete: CheckCircle2,
	pending: Circle,
	blocked: LockKeyhole,
	attention: CircleAlert,
	running: Clock3,
	failed: XCircle,
});

const normalizeStatus = (value) => `${value || "pending"}`.trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");

export function getStatusTone(status) {
	const normalized = normalizeStatus(status);
	if (["active", "current"].includes(normalized)) return "active";
	if (["complete", "completed", "approved", "passed", "success", "ready_for_export"].includes(normalized)) return "complete";
	if (["blocked", "locked"].includes(normalized)) return "blocked";
	if (normalized.includes("attention") || normalized.includes("review") || normalized === "stale") return "attention";
	if (["failed", "failure", "error", "invalid"].includes(normalized)) return "failed";
	if (["ready", "running", "queued", "in_progress"].includes(normalized)) return "running";
	return "pending";
}

export default function StatusBadge({ status, label, compact = false, accessibleLabel = "", className = "" }) {
	const tone = getStatusTone(status);
	const Icon = STATUS_ICONS[tone] || Circle;
	const classes = ["status-badge-token", `status-badge-token--${tone}`, compact ? "compact" : "", className].filter(Boolean).join(" ");

	return (
		<span className={classes} data-status-tone={tone} aria-label={accessibleLabel || undefined} title={compact ? label : undefined}>
			<Icon aria-hidden="true" size={14} strokeWidth={2.25} />
			<span className="status-badge-token-label">{label}</span>
		</span>
	);
}
