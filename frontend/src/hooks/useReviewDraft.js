import { useMemo, useState, useRef } from "react";

// Recovery belongs to one person, project and immutable source. A recovered
// value is an unsaved draft, never evidence that a server operation succeeded.
export default function useReviewDraft({ identity, projectId, snapshotId, name, initialValue }) {
	const key = identity && projectId ? `tcg.draft.v1:${JSON.stringify([identity, projectId, name])}` : "";
	const scope = `${key}:${snapshotId || "empty"}`;
	const loaded = useMemo(() => {
		try {
			const record = key ? JSON.parse(localStorage.getItem(key) || "null") : null;
			const previous = key ? JSON.parse(localStorage.getItem(`${key}:previous`) || "null") : null;
			return { record, previous, error: "" };
		} catch {
			return { record: null, previous: null, error: "Local recovery is unavailable. Keep this tab open until you save your work." };
		}
	}, [key, snapshotId]);
	const [edited, setEdited] = useState(null);
	const current = edited?.scope === scope ? edited : null;
	const stale = current ? current.stale : loaded.record && loaded.record.snapshotId !== snapshotId ? loaded.record : loaded.previous;
	const value = current ? current.value : loaded.record?.snapshotId === snapshotId ? loaded.record.value : initialValue;
	const latest = useRef({ scope, value });
	latest.current = { scope, value };
	const write = (next, discard = false) => {
		const prior = latest.current.scope === scope ? latest.current.value : value;
		const resolved = typeof next === "function" ? next(prior) : next;
		latest.current = { scope, value: resolved };
		let error = "";
		try {
			if (key) {
				if (discard) localStorage.removeItem(`${key}:previous`);
				else if (stale) localStorage.setItem(`${key}:previous`, JSON.stringify(stale));
				localStorage.setItem(key, JSON.stringify({ snapshotId, value: resolved }));
			}
		} catch {
			error = "Local recovery is unavailable. Keep this tab open until you save your work.";
		}
		setEdited({ scope, value: resolved, stale: discard ? null : stale, error });
	};
	return {
		value,
		setValue: write,
		stale,
		error: current?.error || loaded.error,
		clear: ({ keepVisible = false } = {}) => {
			let error = "";
			try {
				if (key && JSON.parse(localStorage.getItem(key) || "null")?.snapshotId === snapshotId) localStorage.removeItem(key);
				if (key && JSON.parse(localStorage.getItem(`${key}:previous`) || "null")?.snapshotId === snapshotId)
					localStorage.removeItem(`${key}:previous`);
			} catch {
				error = "Local recovery is unavailable. Keep this tab open until you save your work.";
			}
			const next = keepVisible ? latest.current.value : initialValue;
			latest.current = { scope, value: next };
			setEdited({ scope, value: next, stale: null, error });
		},
		discardStale: () => write(value, true),
	};
}
