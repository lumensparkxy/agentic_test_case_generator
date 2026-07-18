import { RefreshCw } from "lucide-react";
import { useRef } from "react";

import ReviewInbox from "../components/reviews/ReviewInbox";
import { formatWorkspaceDate } from "../components/workspace/workspacePresentation";
import { WorkspaceErrorState, WorkspaceLoadingState } from "../components/workspace/WorkspacePrimitives";

export default function ReviewsPage({ summary, isLoading = false, isRefreshing = false, error = "", onRetry, onRefresh, onOpenProject }) {
	const headingRef = useRef(null);
	const refreshButtonRef = useRef(null);

	const retryAndRestoreFocus = async () => {
		await onRetry?.();
		window.requestAnimationFrame(() => headingRef.current?.focus());
	};
	const refreshAndRestoreFocus = async () => {
		await onRefresh?.();
		window.requestAnimationFrame(() => refreshButtonRef.current?.focus());
	};
	const errorMessage = error && summary ? `${error} Refresh failed; showing the last available review queue.` : error;

	return (
		<main
			id="main-content"
			className="workspace-page review-inbox-page"
			aria-labelledby="review-inbox-title"
			aria-busy={isLoading || isRefreshing || undefined}
			tabIndex={-1}
		>
			<header className="workspace-page-header review-inbox-page-header">
				<div>
					<span className="workspace-eyebrow">Your review queue</span>
					<h1 id="review-inbox-title" ref={headingRef} tabIndex={-1}>
						Review Inbox
					</h1>
					<p>Review actionable work across projects, then open the exact workbench that owns the decision.</p>
				</div>
				<div className="review-inbox-header-actions">
					{summary?.generated_at ? <time dateTime={summary.generated_at}>Updated {formatWorkspaceDate(summary.generated_at)}</time> : null}
					{onRefresh ? (
						<button
							ref={refreshButtonRef}
							type="button"
							className="secondary review-inbox-refresh-button"
							disabled={isLoading || isRefreshing}
							onClick={refreshAndRestoreFocus}
						>
							<RefreshCw aria-hidden="true" size={15} />
							{isRefreshing ? "Refreshing…" : "Refresh reviews"}
						</button>
					) : null}
				</div>
			</header>

			{error ? <WorkspaceErrorState message={errorMessage} onRetry={retryAndRestoreFocus} /> : null}
			{isLoading && !summary ? (
				<WorkspaceLoadingState />
			) : summary ? (
				<ReviewInbox items={summary.work_items} onOpenProject={onOpenProject} />
			) : null}
		</main>
	);
}
