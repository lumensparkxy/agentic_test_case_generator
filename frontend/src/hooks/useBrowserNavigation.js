import { useCallback, useEffect, useMemo, useState } from "react";

import { parseWorkflowRoute } from "../app/workflowRoutes";

const NAVIGATION_EVENT = "tcg:browser-navigation";

const readBrowserLocation = () => {
	if (typeof window === "undefined") {
		return { pathname: "/", search: "", hash: "", state: null };
	}
	return {
		pathname: window.location.pathname || "/",
		search: window.location.search || "",
		hash: window.location.hash || "",
		state: window.history.state ?? null,
	};
};

export default function useBrowserNavigation() {
	const [location, setLocation] = useState(readBrowserLocation);

	useEffect(() => {
		const syncLocation = () => setLocation(readBrowserLocation());
		window.addEventListener("popstate", syncLocation);
		window.addEventListener(NAVIGATION_EVENT, syncLocation);
		return () => {
			window.removeEventListener("popstate", syncLocation);
			window.removeEventListener(NAVIGATION_EVENT, syncLocation);
		};
	}, []);

	const navigate = useCallback((to, { replace = false, state = null } = {}) => {
		const target = new URL(`${to || "/"}`, window.location.href);
		if (target.origin !== window.location.origin) {
			window.location.assign(target.href);
			return;
		}

		const nextUrl = `${target.pathname}${target.search}${target.hash}`;
		const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
		if (!replace && nextUrl === currentUrl) {
			return;
		}
		window.history[replace ? "replaceState" : "pushState"](state, "", nextUrl);
		window.dispatchEvent(new Event(NAVIGATION_EVENT));
	}, []);

	const replace = useCallback((to, state = null) => navigate(to, { replace: true, state }), [navigate]);
	const route = useMemo(() => parseWorkflowRoute(location.pathname), [location.pathname]);

	return {
		location,
		route,
		navigate,
		replace,
		back: () => window.history.back(),
		forward: () => window.history.forward(),
	};
}
