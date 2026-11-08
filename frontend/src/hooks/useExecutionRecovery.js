import { useState } from "react";

export default function useExecutionRecovery(userId, projectId) {
	const key = userId && projectId ? `tcg.execution.receipt:${JSON.stringify([userId, projectId])}` : null;
	const [entry, setEntry] = useState(null);
	let recovery = null;
	if (key) {
		try {
			recovery = entry?.key === key ? entry.value : JSON.parse(sessionStorage.getItem(key));
		} catch {
			// The in-memory entry still works if browser storage is unavailable.
		}
	}

	const save = (value) => {
		if (!key) return;
		setEntry({ key, value });
		try {
			if (value) sessionStorage.setItem(key, JSON.stringify(value));
			else sessionStorage.removeItem(key);
		} catch {
			// Browser storage can be disabled.
		}
	};
	return [recovery, save];
}
