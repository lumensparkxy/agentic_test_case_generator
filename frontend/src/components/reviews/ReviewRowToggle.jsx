import { ChevronDown, ChevronRight } from "lucide-react";
import { Button } from "../ui/controls";

export default function ReviewRowToggle({ expanded, controls, onClick, children, label }) {
	const Icon = expanded ? ChevronDown : ChevronRight;
	return (
		<Button
			variant="plain"
			className="review-row-toggle"
			aria-expanded={expanded}
			aria-controls={controls}
			aria-label={label}
			onClick={onClick}
		>
			<Icon size={15} aria-hidden="true" />
			<span className="review-row-preview">{children}</span>
		</Button>
	);
}
