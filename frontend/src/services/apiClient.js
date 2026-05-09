export const API_BASE = (() => {
	const configuredApiBase = (import.meta.env.VITE_API_BASE || "").trim();
	if (!configuredApiBase) {
		return "http://127.0.0.1:8000";
	}
	return configuredApiBase === "http://localhost:8000" ? "http://127.0.0.1:8000" : configuredApiBase;
})();

export const createRequestId = () => {
	if (globalThis.crypto?.randomUUID) {
		return globalThis.crypto.randomUUID();
	}
	return `tcg-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

export const parseApiError = async (res, fallbackMessage) => {
	const text = await res.text();
	if (!text) return fallbackMessage;
	try {
		const parsed = JSON.parse(text);
		if (typeof parsed?.detail === "string") {
			return parsed.detail;
		}
		if (parsed?.detail?.message) {
			const contactEmail = parsed?.detail?.contact_email;
			return contactEmail ? `${parsed.detail.message} Contact ${contactEmail}.` : parsed.detail.message;
		}
		return parsed?.message || fallbackMessage;
	} catch {
		return text;
	}
};

export const downloadResponseBlob = async (res, filename) => {
	const blob = await res.blob();
	const url = window.URL.createObjectURL(blob);
	const anchor = document.createElement("a");
	anchor.href = url;
	anchor.download = filename;
	document.body.appendChild(anchor);
	anchor.click();
	anchor.remove();
	window.URL.revokeObjectURL(url);
};
