import { PILOT_WARNING_THRESHOLD, USAGE_STATUS_ITEMS } from "../constants/workflow";
import { formatPlanLabel, normalizeUsageMetric } from "../utils/usage";

export default function useBillingStatus(billingEntitlements, usageSummary) {
	const billingContactEmail = billingEntitlements?.account?.support_contact_email || "hello@spica-digital.eu";
	const billingStatusItems = billingEntitlements
		? [
			{ key: "plan", label: "Plan", value: formatPlanLabel(billingEntitlements.account?.plan_tier), variant: "neutral" },
			...(billingEntitlements.account?.plan_tier === "pilot"
				? [
					{ key: "requirementsRemaining", label: "Req left", value: normalizeUsageMetric(billingEntitlements.requirements?.remaining), variant: billingEntitlements.requirements?.exhausted ? "alert" : "default" },
					{ key: "testCasesRemaining", label: "TC left", value: normalizeUsageMetric(billingEntitlements.test_cases?.remaining), variant: billingEntitlements.test_cases?.exhausted ? "alert" : "default" },
				]
				: []),
			...(billingEntitlements.account?.plan_tier !== "pilot"
				? [{ key: "walletBalance", label: billingEntitlements.account?.plan_tier === "enterprise" ? "Allocation" : "Credits", value: billingEntitlements.wallet?.balance_token_display || "0", variant: normalizeUsageMetric(billingEntitlements.wallet?.balance_units) > 0 ? "default" : "alert" }]
				: []),
		]
		: [];
	const statusUsageItems = usageSummary
		? USAGE_STATUS_ITEMS.map((item) => ({
			...item,
			value: normalizeUsageMetric(usageSummary[item.key]),
			variant: "default",
		}))
		: [];
	const pilotAlert = (() => {
		if (!billingEntitlements || billingEntitlements.account?.plan_tier !== "pilot") {
			return null;
		}

		const requirementsRemaining = normalizeUsageMetric(billingEntitlements.requirements?.remaining);
		const testCasesRemaining = normalizeUsageMetric(billingEntitlements.test_cases?.remaining);
		const exhaustedFamilies = [];
		const lowFamilies = [];

		if (billingEntitlements.requirements?.exhausted) {
			exhaustedFamilies.push("requirements");
		} else if (requirementsRemaining <= PILOT_WARNING_THRESHOLD) {
			lowFamilies.push(`${requirementsRemaining} requirement actions left`);
		}

		if (billingEntitlements.test_cases?.exhausted) {
			exhaustedFamilies.push("test cases");
		} else if (testCasesRemaining <= PILOT_WARNING_THRESHOLD) {
			lowFamilies.push(`${testCasesRemaining} test-case actions left`);
		}

		if (!exhaustedFamilies.length && !lowFamilies.length && !billingEntitlements.shadow_mode) {
			return null;
		}

		if (billingEntitlements.shadow_mode) {
			return {
				variant: "preview",
				title: "Billing preview is active",
				message: exhaustedFamilies.length
					? `Pilot limits would block ${exhaustedFamilies.join(" and ")} once enforcement is enabled.`
					: lowFamilies.length
						? `Pilot balances are informational for now: ${lowFamilies.join(" • ")}.`
						: "Pilot balances are being calculated in shadow mode before hard enforcement is switched on.",
			};
		}

		if (exhaustedFamilies.length) {
			return {
				variant: "locked",
				title: `Pilot limit reached for ${exhaustedFamilies.join(" and ")}`,
				message: "Upgrade to premium or contact support to keep processing those workflows.",
			};
		}

		if (lowFamilies.length) {
			return {
				variant: "warning",
				title: "Pilot quota running low",
				message: lowFamilies.join(" • "),
			};
		}

		return null;
	})();

	return {
		billingContactEmail,
		billingStatusItems,
		statusUsageItems,
		pilotAlert,
	};
}
