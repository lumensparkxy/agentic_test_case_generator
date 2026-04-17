import { getApp, getApps, initializeApp } from "firebase/app";
import { getAuth, GoogleAuthProvider, OAuthProvider } from "firebase/auth";

const firebaseConfig = {
	apiKey: import.meta.env.VITE_FIREBASE_API_KEY || "",
	authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN || "",
	projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID || "",
	storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET || "",
	messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID || "",
	appId: import.meta.env.VITE_FIREBASE_APP_ID || "",
	measurementId: import.meta.env.VITE_FIREBASE_MEASUREMENT_ID || "",
};

const parseBooleanEnvFlag = (value, defaultValue = true) => {
	const normalized = `${value ?? ""}`.trim().toLowerCase();
	if (!normalized) {
		return defaultValue;
	}

	return !["0", "false", "no", "off"].includes(normalized);
};

export const hasFirebaseAuthConfig = Boolean(
	firebaseConfig.apiKey && firebaseConfig.authDomain && firebaseConfig.projectId && firebaseConfig.appId,
);

export const firebaseAuthDomain = firebaseConfig.authDomain || "";
export const firebaseAuthHandlerUrl = firebaseAuthDomain ? `https://${firebaseAuthDomain}/__/auth/handler` : "";

export const firebaseApp = hasFirebaseAuthConfig
	? (getApps().length ? getApp() : initializeApp(firebaseConfig))
	: null;

export const firebaseAuth = firebaseApp ? getAuth(firebaseApp) : null;

const createGoogleProvider = () => {
	const provider = new GoogleAuthProvider();
	provider.setCustomParameters({ prompt: "select_account" });
	return provider;
};

const createMicrosoftProvider = () => {
	const provider = new OAuthProvider("microsoft.com");
	provider.setCustomParameters({ prompt: "select_account" });
	return provider;
};

const createAppleProvider = () => {
	const provider = new OAuthProvider("apple.com");
	provider.addScope("email");
	provider.addScope("name");
	return provider;
};

export const firebaseAuthProviderConfig = [
	{
		id: "google",
		label: "Google",
		buttonText: "Sign in with Google",
		description: "Continue with your Google account",
		providerId: "google.com",
		buttonVariant: "google",
		enabled: parseBooleanEnvFlag(import.meta.env.VITE_FIREBASE_ENABLE_GOOGLE_AUTH, true),
		createProvider: createGoogleProvider,
	},
	{
		id: "microsoft",
		label: "Microsoft",
		buttonText: "Sign in with Microsoft",
		description: "Use your Microsoft work or personal account",
		providerId: "microsoft.com",
		buttonVariant: "microsoft",
		enabled: parseBooleanEnvFlag(import.meta.env.VITE_FIREBASE_ENABLE_MICROSOFT_AUTH, true),
		createProvider: createMicrosoftProvider,
	},
	{
		id: "apple",
		label: "Apple",
		buttonText: "Sign in with Apple",
		description: "Sign in with your Apple ID",
		providerId: "apple.com",
		buttonVariant: "apple",
		enabled: parseBooleanEnvFlag(import.meta.env.VITE_FIREBASE_ENABLE_APPLE_AUTH, true),
		createProvider: createAppleProvider,
	},
];

export const visibleFirebaseAuthProviders = firebaseAuthProviderConfig.filter((provider) => provider.enabled);

export const getFirebaseAuthProviderConfig = (providerKey) => (
	visibleFirebaseAuthProviders.find((provider) => provider.id === providerKey) || null
);

export const createFirebaseAuthProvider = (providerKey) => {
	const providerConfig = getFirebaseAuthProviderConfig(providerKey);
	return providerConfig ? providerConfig.createProvider() : null;
};