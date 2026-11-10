import { Button } from "../components/ui/controls";
import { SlidersHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import GlobalNavigation from "./GlobalNavigation";

export default function GlobalAppShell({
	route,
	navigate,
	controls = null,
	compactControls = false,
	headerCollapsed = false,
	headerRef,
	onDismissHeader,
	children,
}) {
	const [areControlsOpen, setAreControlsOpen] = useState(false);
	const controlsToggleRef = useRef(null);
	const controlsId = "workspace-controls-panel";

	useEffect(() => {
		if (compactControls) setAreControlsOpen(false);
	}, [compactControls, route?.destination, route?.kind]);

	const closeControls = ({ restoreFocus = false } = {}) => {
		if (!compactControls || !areControlsOpen) return;
		setAreControlsOpen(false);
		if (restoreFocus) window.requestAnimationFrame(() => controlsToggleRef.current?.focus());
	};

	return (
		<div className="global-app-shell">
			<header
				id="application-menu"
				ref={headerRef}
				hidden={headerCollapsed}
				className={`global-app-shell-header ${compactControls ? "compact" : ""}`.trim()}
				onKeyDown={(event) => {
					if (event.key !== "Escape" || event.defaultPrevented) return;
					if (compactControls && areControlsOpen) {
						event.preventDefault();
						closeControls({ restoreFocus: true });
					} else if (onDismissHeader) {
						event.preventDefault();
						onDismissHeader();
					}
				}}
			>
				<GlobalNavigation route={route} navigate={navigate} />
				{controls && compactControls ? (
					<Button
						ref={controlsToggleRef}
						type="button"
						className="global-app-shell-controls-toggle secondary"
						onClick={() => setAreControlsOpen((value) => !value)}
						aria-expanded={areControlsOpen}
						aria-controls={controlsId}
					>
						<SlidersHorizontal aria-hidden="true" size={16} strokeWidth={2.2} />
						{areControlsOpen ? "Close workspace controls" : "Open workspace controls"}
					</Button>
				) : null}
				{controls ? (
					<div id={controlsId} className="global-app-shell-controls" hidden={compactControls && !areControlsOpen}>
						{controls}
					</div>
				) : null}
			</header>
			{children}
		</div>
	);
}
