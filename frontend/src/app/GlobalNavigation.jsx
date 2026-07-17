import RouteLink from "./RouteLink";
import { GLOBAL_DESTINATIONS, GLOBAL_NAV_ITEMS } from "./workflowRoutes";

export default function GlobalNavigation({ route, navigate }) {
	const activeDestination = route?.kind === "project" ? GLOBAL_DESTINATIONS.PROJECTS : route?.kind === "global" ? route.destination : null;

	return (
		<nav className="global-navigation" aria-label="Global navigation">
			<ul className="global-navigation-list">
				{GLOBAL_NAV_ITEMS.map((item) => {
					const isActive = activeDestination === item.id;
					return (
						<li key={item.id}>
							<RouteLink
								to={item.path}
								navigate={navigate}
								className={`global-navigation-link ${isActive ? "active" : ""}`}
								aria-current={isActive ? "page" : undefined}
							>
								{item.label}
							</RouteLink>
						</li>
					);
				})}
			</ul>
		</nav>
	);
}
