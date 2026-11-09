import { Component, lazy, Suspense, useCallback, useEffect, useRef } from "react";
import { Button } from "../components/ui/controls";
import { Alert } from "../components/ui/surfaces";

class WorkspaceModuleError extends Error {}

function WorkspaceCommitted({ onModuleReady }) {
	useEffect(() => {
		onModuleReady();
	}, [onModuleReady]);
	return null;
}

function WorkspaceLoading({ label, page, loadingFocused }) {
	const message = `Loading ${label}…`;
	const status = <p role="status">{message}</p>;
	return page ? (
		<main
			id="main-content"
			className="route-page"
			tabIndex={-1}
			aria-label={message}
			data-workspace-module-pending="true"
			onFocus={() => (loadingFocused.current = true)}
			onBlur={() => (loadingFocused.current = false)}
		>
			{status}
		</main>
	) : (
		status
	);
}

function WorkspaceLoadFailure({ label, page, onModuleReady }) {
	const recovery = (
		<Alert as="div" tone="danger" className="route-inline-error" role="alert">
			<p>Couldn’t load {label}.</p>
			<Button variant="secondary" onClick={() => window.location.reload()}>
				Reload page
			</Button>
		</Alert>
	);
	return page ? (
		<main id="main-content" className="route-page" tabIndex={-1}>
			{recovery}
			{onModuleReady && <WorkspaceCommitted onModuleReady={onModuleReady} />}
		</main>
	) : (
		recovery
	);
}

class WorkspaceModuleBoundary extends Component {
	state = { failed: false };

	static getDerivedStateFromError(error) {
		if (!(error instanceof WorkspaceModuleError)) throw error;
		return { failed: true };
	}

	render() {
		return this.state.failed ? this.props.recovery : this.props.children;
	}
}

export function deferWorkspace(load, label, { page = false } = {}) {
	let moduleRequest;
	const requestModule = () => {
		moduleRequest ??= load().catch((cause) => {
			throw new WorkspaceModuleError(`Failed to load ${label}`, { cause });
		});
		return moduleRequest;
	};
	const View = lazy(requestModule);

	function DeferredWorkspace({ onModuleReady, ...props }) {
		const loadingFocused = useRef(false);
		const onCommitted = useCallback(() => {
			onModuleReady?.(loadingFocused.current);
			loadingFocused.current = false;
		}, [onModuleReady]);
		return (
			<WorkspaceModuleBoundary recovery={<WorkspaceLoadFailure label={label} page={page} onModuleReady={onCommitted} />}>
				<Suspense fallback={<WorkspaceLoading label={label} page={page} loadingFocused={loadingFocused} />}>
					<View {...props} />
					{onModuleReady && <WorkspaceCommitted onModuleReady={onCommitted} />}
				</Suspense>
			</WorkspaceModuleBoundary>
		);
	}

	DeferredWorkspace.preload = () => {
		// The same promise is used on render, where a failed preload gets local recovery.
		void requestModule().catch(() => {});
	};
	return DeferredWorkspace;
}
