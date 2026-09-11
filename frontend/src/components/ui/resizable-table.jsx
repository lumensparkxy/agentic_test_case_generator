import { useEffect, useRef, useState } from "react";
import { Table } from "./collections";

export function ResizableTable({ columns, storageKey, children, className }) {
	const table = useRef(null);
	const drag = useRef(null);
	const [measured, setMeasured] = useState(null);
	useEffect(() => {
		const observer = new ResizeObserver(() =>
			setMeasured([...table.current.querySelectorAll("thead th")].map((cell) => cell.getBoundingClientRect().width))
		);
		observer.observe(table.current);
		return () => observer.disconnect();
	}, []);
	const [widths, setWidths] = useState(() => {
		try {
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
		if (widths) {
			try {
				localStorage.setItem(`tcg.columns.${storageKey}`, JSON.stringify(widths));
			} catch {
				/* Resizing works without storage. */
			}
		}
	}, [widths, storageKey]);
	const actualWidths = () => [...table.current.querySelectorAll("thead th")].map((cell) => cell.getBoundingClientRect().width);
	const resize = (index, width, baseline = widths || actualWidths()) =>
		setWidths(baseline.map((value, i) => (i === index ? Math.round(Math.max(columns[i].min, Math.min(1200, width))) : value)));
	return (
		<Table
			ref={table}
			className={className}
			style={{
				tableLayout: "fixed",
				minWidth: widths ? widths.reduce((sum, value) => sum + value, 0) : undefined,
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
					{columns.map((column, i) => (
						<th scope="col" key={column.label} className="ui-resizable-column">
							{column.label}
							<span
								role="separator"
								tabIndex={0}
								aria-orientation="vertical"
								aria-label={`Resize ${column.label} column`}
								aria-valuemin={column.min}
								aria-valuemax={1200}
								aria-valuenow={Math.round(widths?.[i] || measured?.[i] || column.initial)}
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
									if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
								}}
								onPointerCancel={() => {
									drag.current = null;
								}}
								onLostPointerCapture={() => {
									drag.current = null;
								}}
								onDoubleClick={() => resize(i, column.initial)}
								onKeyDown={(event) => {
									const current = widths || actualWidths();
									const step = event.shiftKey ? 40 : 10;
									const next = {
										ArrowLeft: current[i] - step,
										ArrowRight: current[i] + step,
										Home: column.min,
										End: 1200,
										Enter: column.initial,
									}[event.key];
									if (next !== undefined) {
										event.preventDefault();
										resize(i, next, current);
									}
								}}
							/>
						</th>
					))}
				</tr>
			</thead>
			{children}
		</Table>
	);
}
