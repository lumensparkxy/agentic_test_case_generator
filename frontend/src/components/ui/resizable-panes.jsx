import { Children, useEffect, useId, useRef, useState } from "react";

const DIVIDER_WIDTH = 20;
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

// Compose these splits to support workspaces with more than two panes.
export function ResizablePanes({
	as: Element = "section",
	children,
	label = "Resize panes",
	storageKey,
	defaultSize = 38,
	minFirst = 260,
	minSecond = 360,
	className = "",
	...props
}) {
	const root = useRef(null);
	const dragging = useRef(false);
	const id = useId();
	const [width, setWidth] = useState(0);
	const [size, setSize] = useState(() => {
		try {
			const saved = storageKey ? localStorage.getItem(`tcg.panes.${storageKey}`) : null;
			const value = saved === null ? defaultSize : Number(saved);
			return Number.isFinite(value) && value > 0 && value < 100 ? value : defaultSize;
		} catch {
			return defaultSize;
		}
	});
	const latestSize = useRef(size);
	useEffect(() => {
		const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
		observer.observe(root.current);
		return () => observer.disconnect();
	}, []);
	const available = Math.max(1, width - DIVIDER_WIDTH);
	const expanded = available >= minFirst + minSecond;
	const min = expanded ? (minFirst / available) * 100 : 0;
	const max = expanded ? 100 - (minSecond / available) * 100 : 100;
	const effectiveSize = clamp(size, min, max);
	const persist = (value) => {
		try {
			if (storageKey) localStorage.setItem(`tcg.panes.${storageKey}`, String(value));
		} catch {
			// Resizing remains usable when browser storage is unavailable.
		}
	};
	const changeSize = (value, save = true) => {
		const next = clamp(value, min, max);
		latestSize.current = next;
		setSize(next);
		if (save) persist(next);
	};
	const panes = Children.toArray(children);
	if (panes.length !== 2) throw new Error("ResizablePanes requires two panes; nest splits for additional panes.");
	return (
		<Element
			{...props}
			ref={root}
			className={`ui-resizable-panes ${className}`}
			data-expanded={expanded}
			style={
				expanded
					? { gridTemplateColumns: `minmax(0, ${effectiveSize}fr) ${DIVIDER_WIDTH}px minmax(0, ${100 - effectiveSize}fr)` }
					: undefined
			}
		>
			<div className="ui-resizable-pane" id={`${id}-first`}>
				{panes[0]}
			</div>
			{expanded && (
				<div
					className="ui-pane-divider"
					role="separator"
					tabIndex={0}
					aria-label={label}
					aria-orientation="vertical"
					aria-controls={`${id}-first`}
					aria-valuemin={Math.round(min)}
					aria-valuemax={Math.round(max)}
					aria-valuenow={Math.round(effectiveSize)}
					aria-valuetext={`${Math.round(effectiveSize)} percent left pane`}
					title="Drag to resize. Arrow keys adjust; Home/End set limits; Enter or double-click resets."
					onPointerDown={(event) => {
						if (event.button !== 0) return;
						event.preventDefault();
						event.currentTarget.focus();
						event.currentTarget.setPointerCapture(event.pointerId);
						dragging.current = true;
					}}
					onPointerMove={(event) => {
						if (!dragging.current) return;
						const bounds = root.current.getBoundingClientRect();
						changeSize(((event.clientX - bounds.left - DIVIDER_WIDTH / 2) / available) * 100, false);
					}}
					onPointerUp={(event) => {
						if (!dragging.current) return;
						dragging.current = false;
						persist(latestSize.current);
						event.currentTarget.releasePointerCapture(event.pointerId);
					}}
					onLostPointerCapture={() => {
						dragging.current = false;
					}}
					onDoubleClick={() => changeSize(defaultSize)}
					onKeyDown={(event) => {
						const delta = event.shiftKey ? 10 : 2;
						const next = { ArrowLeft: effectiveSize - delta, ArrowRight: effectiveSize + delta, Home: min, End: max, Enter: defaultSize }[
							event.key
						];
						if (next === undefined) return;
						event.preventDefault();
						changeSize(next);
					}}
				/>
			)}
			<div className="ui-resizable-pane">{panes[1]}</div>
		</Element>
	);
}
