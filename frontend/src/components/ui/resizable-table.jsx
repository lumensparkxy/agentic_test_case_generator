import { useEffect, useRef, useState } from "react";
import { Table } from "./collections";

export function useTableColumnWidths(columns, storageKey) {
	const [widths, setWidths] = useState(() => {
		try {
			if (!storageKey) return null;
			const saved = JSON.parse(localStorage.getItem(`tcg.columns.${storageKey}`));
			return Array.isArray(saved) &&
				saved.length === columns.length &&
				saved.every((width, i) => Number.isFinite(width) && width >= columns[i].min && width <= 1200)
				? saved
				: null;
		} catch {
			return null;
		}
	});
	useEffect(() => {
		if (widths && storageKey) {
			try {
				localStorage.setItem(`tcg.columns.${storageKey}`, JSON.stringify(widths));
			} catch {
				/* Resizing works without storage. */
			}
		}
	}, [widths, storageKey]);
	return [widths, setWidths];
}

export function ResizableTable({ columns, storageKey, widths: controlledWidths, onWidthsChange, children, className, ...props }) {
	const table = useRef(null);
	const drag = useRef(null);
	const [measured, setMeasured] = useState(null);
	useEffect(() => {
		const observer = new ResizeObserver(() =>
			setMeasured([...table.current.querySelectorAll("thead th")].map((cell) => cell.getBoundingClientRect().width))
		);
		table.current.querySelectorAll("thead th").forEach((cell) => observer.observe(cell));
		return () => observer.disconnect();
	}, []);
	const [localWidths, setLocalWidths] = useTableColumnWidths(columns, controlledWidths === undefined ? storageKey : undefined);
	const widths = controlledWidths === undefined ? localWidths : controlledWidths;
	const setWidths = onWidthsChange || setLocalWidths;
	const actualWidths = () => [...table.current.querySelectorAll("thead th")].map((cell) => cell.getBoundingClientRect().width);
	const limits = (index, baseline) => {
		const combined = baseline[index] + baseline[index + 1];
		return { min: Math.max(columns[index].min, combined - 1200), max: Math.min(1200, combined - columns[index + 1].min) };
	};
	const resize = (index, width, baseline = actualWidths()) => {
		const { min, max } = limits(index, baseline);
		const next = Math.max(min, Math.min(max, Math.round(width)));
		setWidths(baseline.map((value, i) => (i === index ? next : i === index + 1 ? value + baseline[index] - next : value)));
	};
	return (
		<Table
			{...props}
			ref={table}
			className={className}
			style={{
				tableLayout: "fixed",
				minWidth: widths ? "100%" : undefined,
				width: widths ? widths.reduce((sum, value) => sum + value, 0) : "100%",
			}}
		>
			<colgroup>
				{columns.map((column, i) => (
					<col key={column.label} style={{ width: widths ? widths[i] : column.percent + "%" }} />
				))}
			</colgroup>
			<thead>
				<tr>
					{columns.map((column, i) => {
						const resizable = i < columns.length - 1;
						const bounds = resizable ? limits(i, measured || widths || columns.map((item) => item.initial)) : null;
						return (
							<th scope="col" key={column.label} className={resizable ? "ui-resizable-column" : undefined}>
								{column.label}
								{resizable && (
									<span
										role="separator"
										tabIndex={0}
										aria-orientation="vertical"
										aria-label={`Resize ${column.label} column`}
										aria-valuemin={bounds.min}
										aria-valuemax={bounds.max}
										aria-valuenow={Math.round(measured?.[i] || widths?.[i] || column.initial)}
										className="ui-column-divider"
										onPointerDown={(event) => {
											if (event.button !== 0) return;
											event.preventDefault();
											event.currentTarget.focus();
											event.currentTarget.setPointerCapture(event.pointerId);
											drag.current = { start: event.clientX, widths: actualWidths() };
										}}
										onPointerMove={(event) => {
											if (drag.current && event.currentTarget.hasPointerCapture(event.pointerId))
												resize(i, drag.current.widths[i] + event.clientX - drag.current.start, drag.current.widths);
										}}
										onPointerUp={(event) => {
											drag.current = null;
											if (event.currentTarget.hasPointerCapture(event.pointerId))
												event.currentTarget.releasePointerCapture(event.pointerId);
										}}
										onPointerCancel={() => {
											drag.current = null;
										}}
										onLostPointerCapture={() => {
											drag.current = null;
										}}
										onDoubleClick={() => resize(i, column.initial)}
										onKeyDown={(event) => {
											const current = actualWidths();
											const step = event.shiftKey ? 40 : 10;
											const next = {
												ArrowLeft: current[i] - step,
												ArrowRight: current[i] + step,
												Home: limits(i, current).min,
												End: limits(i, current).max,
												Enter: column.initial,
											}[event.key];
											if (next !== undefined) {
												event.preventDefault();
												resize(i, next, current);
											}
										}}
									/>
								)}
							</th>
						);
					})}
				</tr>
			</thead>
			{children}
		</Table>
	);
}
