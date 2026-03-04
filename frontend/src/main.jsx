import React from "react";
import { createRoot } from "react-dom/client";
import { GoogleOAuthProvider } from "@react-oauth/google";
import App from "./App.jsx";

const googleClientId = import.meta.env.VITE_GOOGLE_CLIENT_ID;
const root = createRoot(document.getElementById("root"));

if (!googleClientId) {
	console.warn("VITE_GOOGLE_CLIENT_ID is not set. Google login will be unavailable.");
}

root.render(
	googleClientId ? (
		<GoogleOAuthProvider clientId={googleClientId}>
			<App />
		</GoogleOAuthProvider>
	) : (
		<App />
	)
);
