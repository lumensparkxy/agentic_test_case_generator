import { SlidersHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import GlobalNavigation from "./GlobalNavigation";

export default function GlobalAppShell({ route, navigate, controls = null, compactControls = false, children }) {
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
				className={`global-app-shell-header ${compactControls ? "compact" : ""}`.trim()}
				onKeyDown={(event) => {
					if (event.key !== "Escape" || !compactControls || !areControlsOpen) return;
					event.preventDefault();
					closeControls({ restoreFocus: true });
				}}
			>
				<GlobalNavigation route={route} navigate={navigate} />
				{controls && compactControls ? (
					<button
						ref={controlsToggleRef}
						type="button"
						className="global-app-shell-controls-toggle secondary"
						onClick={() => setAreControlsOpen((value) => !value)}
						aria-expanded={areControlsOpen}
						aria-controls={controlsId}
					>
						<SlidersHorizontal aria-hidden="true" size={16} strokeWidth={2.2} />
						{areControlsOpen ? "Close workspace controls" : "Open workspace controls"}
					</button>
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
