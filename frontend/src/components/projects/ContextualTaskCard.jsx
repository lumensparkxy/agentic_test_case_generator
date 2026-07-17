import { useEffect, useRef, useState } from "react";

import { formatTaskLabel, formatTaskProvenance, getTaskProvenance } from "./contextualTask";

const ACTION_CTA_LABELS = Object.freeze({
	refine: "Open workbench",
	approve: "Open workbench",
	generate: "Start generation",
	analyze_impact: "Start analysis",
	apply_update: "Apply accepted changes",
	automate: "Create preview",
	execute: "Run approved cases",
	review: "Open review",
	report: "Open reports",
});

const normalizeList = (value) => (Array.isArray(value) ? value : []);

function actionLabel(action) {
	return action?.label || formatTaskLabel(action?.action) || "Continue workflow";
}

function firstBlocker(action) {
	return normalizeList(action?.blockers)[0]?.message || "";
}

function actionCtaLabel(action, navigationOnly = false) {
	if (navigationOnly) {
		return "Open workbench";
	}
	return ACTION_CTA_LABELS[action?.action] || "Continue";
}

function RegenerationConfirmation({ isSubmitting, error, onCancel, onConfirm }) {
	const confirmButtonRef = useRef(null);
	const dialogRef = useRef(null);

	useEffect(() => {
		if (isSubmitting) {
			dialogRef.current?.focus();
			return;
		}
		confirmButtonRef.current?.focus();
	}, [isSubmitting]);

	return (
		<div
			className="contextual-task-dialog-overlay"
			onClick={() => {
				if (!isSubmitting) onCancel();
			}}
			onKeyDown={(event) => {
				if (event.key === "Escape" && !isSubmitting) {
					event.preventDefault();
					onCancel();
					return;
				}
				if (event.key !== "Tab") return;
				const focusable = Array.from(
					dialogRef.current?.querySelectorAll(
						"button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled)"
					) || []
				);
				if (!focusable.length) {
					event.preventDefault();
					dialogRef.current?.focus();
					return;
				}
				const first = focusable[0];
				const last = focusable.at(-1);
				if (event.shiftKey && document.activeElement === first) {
					event.preventDefault();
					last.focus();
				} else if (!event.shiftKey && document.activeElement === last) {
					event.preventDefault();
					first.focus();
				}
			}}
		>
			<div
				ref={dialogRef}
				className="contextual-task-dialog"
				role="dialog"
				aria-modal="true"
				aria-busy={isSubmitting}
				aria-labelledby="full-regenerate-dialog-title"
				aria-describedby="full-regenerate-dialog-description"
				tabIndex={-1}
				onClick={(event) => event.stopPropagation()}
			>
				<span className="contextual-task-kicker">Confirm replacement</span>
				<h2 id="full-regenerate-dialog-title">Regenerate the entire test suite?</h2>
				<p id="full-regenerate-dialog-description">
					Your current suite stays visible and unchanged while regeneration runs. If regeneration succeeds, the new suite becomes current
					and existing cases or coverage may be replaced. If it fails, the current suite remains available.
				</p>
				{error ? (
					<div className="contextual-task-dialog-error" role="alert">
						{error}
					</div>
				) : null}
				<div className="contextual-task-dialog-actions">
					<button type="button" className="secondary" onClick={onCancel} disabled={isSubmitting}>
						Cancel
					</button>
					<button ref={confirmButtonRef} type="button" onClick={onConfirm} disabled={isSubmitting}>
						{isSubmitting ? "Regenerating…" : "Confirm regeneration"}
					</button>
				</div>
			</div>
		</div>
	);
}

export default function ContextualTaskCard({
	action,
	secondaryActions = [],
	status,
	busyMap = {},
	disabled = false,
	disabledMap = {},
	navigationOnly = false,
	focusFallbackRef,
	onAction,
}) {
	const [locallyBusyAction, setLocallyBusyAction] = useState("");
	const [confirmationAction, setConfirmationAction] = useState(null);
	const [confirmationError, setConfirmationError] = useState("");
	const invocationLockRef = useRef(false);
	const regenerationTriggerRef = useRef(null);
	const hasPrimaryAction = Boolean(action);
	const provenance = getTaskProvenance(status);
	const provenanceLabel = formatTaskProvenance(provenance);
	const blocker = firstBlocker(action);
	const reason = hasPrimaryAction
		? action?.reason || blocker || "Continue the current workflow."
		: "Optional actions for this workbench remain available without replacing the recommended next task.";
	const showSeparateBlocker = Boolean(blocker && blocker !== reason);
	const isPrimaryBusy = Boolean(busyMap[action?.action] || locallyBusyAction === action?.action);
	const primaryDisabled = Boolean(disabled || disabledMap[action?.action] || !action?.enabled || isPrimaryBusy);
	const stage = status?.stages?.[action?.stage] || null;
	const hasDiagnostics = Boolean(action?.agent_kind || action?.agent_contract_version || action?.agent_implementation);
	const hasDetails = Boolean(provenanceLabel || stage || hasDiagnostics || secondaryActions.length);

	const invokeAction = async (nextAction) => {
		if (invocationLockRef.current || disabled || disabledMap[nextAction?.action] || busyMap[nextAction?.action] || !nextAction?.enabled) {
			return false;
		}

		invocationLockRef.current = true;
		setLocallyBusyAction(nextAction.action || "pending");
		try {
			return await onAction(nextAction);
		} finally {
			invocationLockRef.current = false;
			setLocallyBusyAction("");
		}
	};

	const closeConfirmation = () => {
		if (locallyBusyAction) return;
		setConfirmationAction(null);
		setConfirmationError("");
		requestAnimationFrame(() => regenerationTriggerRef.current?.focus());
	};

	const confirmRegeneration = async () => {
		if (!confirmationAction || invocationLockRef.current) return;
		setConfirmationError("");
		const result = await invokeAction(confirmationAction);
		if (result === false) {
			setConfirmationError("Regeneration did not complete. The current suite was preserved; you can try again.");
			return;
		}
		setConfirmationAction(null);
		requestAnimationFrame(() => {
			const focusTarget = regenerationTriggerRef.current?.isConnected ? regenerationTriggerRef.current : focusFallbackRef?.current;
			focusTarget?.focus();
		});
	};

	const handleSecondaryAction = (nextAction, event) => {
		if (nextAction.action === "full_regenerate" && !navigationOnly) {
			if (disabled || disabledMap[nextAction.action] || busyMap[nextAction.action] || !nextAction.enabled) return;
			regenerationTriggerRef.current = event.currentTarget;
			setConfirmationError("");
			setConfirmationAction(nextAction);
			return;
		}
		void invokeAction(nextAction);
	};

	const handlePrimaryAction = (event) => {
		if (!action) return;
		if (action.action === "full_regenerate" && !navigationOnly) {
			if (primaryDisabled) return;
			regenerationTriggerRef.current = event.currentTarget;
			setConfirmationError("");
			setConfirmationAction(action);
			return;
		}
		void invokeAction(action);
	};

	return (
		<article className="contextual-task-card" aria-labelledby="contextual-task-title" aria-busy={hasPrimaryAction && isPrimaryBusy}>
			<div className="contextual-task-copy">
				<span className="contextual-task-kicker">{hasPrimaryAction ? "Next task" : "More actions"}</span>
				<h2 id="contextual-task-title">{hasPrimaryAction ? actionLabel(action) : "Optional test suite actions"}</h2>
				<p>{reason}</p>
				{showSeparateBlocker ? (
					<div className="contextual-task-blocker" role="note">
						{blocker}
					</div>
				) : null}
			</div>
			<div className="contextual-task-controls">
				{hasPrimaryAction ? (
					<button type="button" onClick={handlePrimaryAction} disabled={primaryDisabled}>
						{isPrimaryBusy ? "Working…" : actionCtaLabel(action, navigationOnly)}
					</button>
				) : null}
				{hasDetails ? (
					<details className="contextual-task-details">
						<summary>Details</summary>
						<div className="contextual-task-details-body">
							{stage ? (
								<p>
									<strong>Status</strong> {formatTaskLabel(action.stage)} · {formatTaskLabel(stage.status)}
								</p>
							) : null}
							{provenanceLabel ? (
								<div>
									<p>
										<strong>Sources</strong> {provenanceLabel}
									</p>
									{provenance.some((source) => source.snapshotId) ? (
										<ul className="contextual-task-snapshot-list" aria-label="Source snapshots">
											{provenance
												.filter((source) => source.snapshotId)
												.map((source) => (
													<li key={source.stage}>
														{source.label}: {source.snapshotId}
													</li>
												))}
										</ul>
									) : null}
								</div>
							) : null}
							{hasDiagnostics ? (
								<p className="contextual-task-diagnostics">
									<strong>Diagnostics</strong> {formatTaskLabel(action.agent_kind)}
									{action.agent_contract_version ? ` · contract ${action.agent_contract_version}` : ""}
									{action.agent_implementation ? ` · ${action.agent_implementation}` : ""}
								</p>
							) : null}
							{secondaryActions.length ? (
								<div className="contextual-task-secondary-actions" aria-label="More actions">
									<strong>More actions</strong>
									{secondaryActions.map((secondaryAction) => {
										const secondaryBusy = Boolean(busyMap[secondaryAction.action] || locallyBusyAction === secondaryAction.action);
										return (
											<div className="contextual-task-secondary-action" key={`${secondaryAction.action}-${secondaryAction.stage}`}>
												<button
													ref={secondaryAction.action === "full_regenerate" ? regenerationTriggerRef : null}
													type="button"
													className="secondary small"
													onClick={(event) => handleSecondaryAction(secondaryAction, event)}
													disabled={disabled || disabledMap[secondaryAction.action] || secondaryBusy || !secondaryAction.enabled}
												>
													{secondaryBusy ? "Working…" : actionLabel(secondaryAction)}
												</button>
												<p>{secondaryAction.reason || firstBlocker(secondaryAction) || "Optional workflow action."}</p>
												{firstBlocker(secondaryAction) && firstBlocker(secondaryAction) !== secondaryAction.reason ? (
													<span className="contextual-task-blocker">{firstBlocker(secondaryAction)}</span>
												) : null}
											</div>
										);
									})}
								</div>
							) : null}
						</div>
					</details>
				) : null}
			</div>
			{confirmationAction ? (
				<RegenerationConfirmation
					isSubmitting={locallyBusyAction === confirmationAction.action || Boolean(busyMap[confirmationAction.action])}
					error={confirmationError}
					onCancel={closeConfirmation}
					onConfirm={() => void confirmRegeneration()}
				/>
			) : null}
		</article>
	);
}
