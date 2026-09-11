import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Dialog } from "../components/ui/dialog";
import { Button } from "../components/ui/controls";

// A failed preflight never silently changes the generation inputs. Keep the same
// request ID on retry; reject pending requests when their project/user is gone.
export default function useGuidanceRecovery(scope) {
	const queue = useRef([]);
	const scopeRef = useRef(scope);
	useEffect(() => {
		scopeRef.current = scope;
	}, [scope]);
	const [pending, setPending] = useState(null);
	const advance = useCallback((choice) => {
		const current = queue.current.shift();
		current?.resolve(choice);
		setPending(queue.current[0] || null);
	}, []);
	useEffect(
		() => () => {
			for (const item of queue.current) item.resolve("cancel");
			queue.current = [];
		},
		[scope]
	);
	const decide = useCallback(
		(origin) =>
			new Promise((resolve) => {
				if (origin !== scopeRef.current) {
					resolve("cancel");
					return;
				}
				const item = { resolve, origin };
				queue.current.push(item);
				if (queue.current.length === 1) setPending(item);
			}),
		[]
	);
	const dialog =
		pending && pending.origin === scope
			? createPortal(
					<div className="ui-dialog-backdrop">
						<Dialog manageFocus aria-labelledby="guidance-failed-title" className="knowledge-dialog" onClose={() => advance("cancel")}>
							<h2 id="guidance-failed-title">Remembered guidance could not be loaded</h2>
							<p>
								Generation has not started. Retry loading, or explicitly run using current inputs and enabled skills without remembered
								guidance.
							</p>
							<div className="knowledge-actions">
								<Button onClick={() => advance("retry")}>Retry loading guidance</Button>
								<Button variant="secondary" onClick={() => advance("bypass")}>
									Run without remembered guidance
								</Button>
								<Button variant="secondary" onClick={() => advance("cancel")}>
									Cancel generation
								</Button>
							</div>
						</Dialog>
					</div>,
					document.body
				)
			: null;
	return { decide, dialog };
}
