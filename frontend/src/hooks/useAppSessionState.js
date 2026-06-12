import { useState } from "react";

export default function useAppSessionState() {
	const [status, setStatus] = useState("");
	const [authToken, setAuthToken] = useState("");
	const [currentUser, setCurrentUser] = useState(null);
	const [isAuthenticating, setIsAuthenticating] = useState(false);
	const [activeAuthProvider, setActiveAuthProvider] = useState("");
	const [isSignInDialogOpen, setIsSignInDialogOpen] = useState(false);
	const [isSettingsDialogOpen, setIsSettingsDialogOpen] = useState(false);
	const [settingsSection, setSettingsSection] = useState("workflow");
	const [isVerifyingSession, setIsVerifyingSession] = useState(true);
	const [usageSummary, setUsageSummary] = useState(null);
	const [isUsageLoading, setIsUsageLoading] = useState(false);
	const [billingEntitlements, setBillingEntitlements] = useState(null);
	const [isBillingLoading, setIsBillingLoading] = useState(false);

	return {
		status,
		setStatus,
		authToken,
		setAuthToken,
		currentUser,
		setCurrentUser,
		isAuthenticating,
		setIsAuthenticating,
		activeAuthProvider,
		setActiveAuthProvider,
		isSignInDialogOpen,
		setIsSignInDialogOpen,
		isSettingsDialogOpen,
		setIsSettingsDialogOpen,
		settingsSection,
		setSettingsSection,
		isVerifyingSession,
		setIsVerifyingSession,
		usageSummary,
		setUsageSummary,
		isUsageLoading,
		setIsUsageLoading,
		billingEntitlements,
		setBillingEntitlements,
		isBillingLoading,
		setIsBillingLoading,
	};
}
