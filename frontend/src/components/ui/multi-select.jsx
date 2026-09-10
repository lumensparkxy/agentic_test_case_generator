import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Button, Checkbox } from "./controls";

export function MultiSelect({ label, accessibleLabel = label, options, value, onChange, disabled = false }) {
	const id = useId();
	const trigger = useRef(null);
	const popup = useRef(null);
	const [open, setOpen] = useState(false);
	const [position, setPosition] = useState({});
	useEffect(() => {
		const dismiss = () => {
			if (popup.current?.matches(":popover-open")) popup.current.hidePopover();
		};
		const reposition = (event) => {
			if (!popup.current?.matches(":popover-open") || popup.current.contains(event.target)) return;
			const box = trigger.current.getBoundingClientRect();
			setPosition({
				left: Math.max(8, Math.min(box.left, window.innerWidth - 288)),
				top: Math.max(8, Math.min(box.bottom + 4, window.innerHeight - 320)),
			});
		};
		window.addEventListener("resize", dismiss);
		document.addEventListener("scroll", reposition, true);
		return () => {
			window.removeEventListener("resize", dismiss);
			document.removeEventListener("scroll", reposition, true);
		};
	}, []);
	return (
		<>
			<Button
				ref={trigger}
				variant="secondary"
				className="ui-multi-select-trigger"
				disabled={disabled}
				popoverTarget={id}
				aria-label={accessibleLabel}
				aria-expanded={open}
				aria-controls={id}
				onClick={() => {
					const box = trigger.current.getBoundingClientRect();
					setPosition({
						left: Math.max(8, Math.min(box.left, window.innerWidth - 288)),
						top: Math.max(8, Math.min(box.bottom + 4, window.innerHeight - 320)),
					});
				}}
			>
				{label}
				{value.length ? <span> ({value.length})</span> : null}
				<ChevronDown size={16} aria-hidden="true" />
			</Button>
			<div
				ref={popup}
				id={id}
				popover="auto"
				role="group"
				aria-label={accessibleLabel}
				className="ui-multi-select-popup"
				style={position}
				onToggle={(event) => setOpen(event.newState === "open")}
			>
				{options.map((option) => (
					<label key={option}>
						<Checkbox
							disabled={disabled}
							checked={value.includes(option)}
							onChange={() => onChange(value.includes(option) ? value.filter((item) => item !== option) : [...value, option].sort())}
						/>
						{option}
					</label>
				))}
				<Button
					variant="secondary"
					onClick={() => {
						popup.current.hidePopover();
						trigger.current.focus();
					}}
				>
					Done
				</Button>
			</div>
		</>
	);
}
