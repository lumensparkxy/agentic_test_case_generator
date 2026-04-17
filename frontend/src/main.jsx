import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { hasFirebaseAuthConfig } from "./firebase.js";

const root = createRoot(document.getElementById("root"));

if (!hasFirebaseAuthConfig) {
	console.warn("Firebase web config is incomplete. Firebase sign-in will be unavailable.");
}

root.render(<App />);
