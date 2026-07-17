import GlobalNavigation from "./GlobalNavigation";

export default function GlobalAppShell({ route, navigate, controls = null, children }) {
	return (
		<div className="global-app-shell">
			<header className="global-app-shell-header">
				<GlobalNavigation route={route} navigate={navigate} />
				{controls ? <div className="global-app-shell-controls">{controls}</div> : null}
			</header>
			{children}
		</div>
	);
}
