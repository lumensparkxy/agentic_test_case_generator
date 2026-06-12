export default function BillingBanner({ isAuthenticated, pilotAlert, billingContactEmail }) {
	if (!isAuthenticated || !pilotAlert) {
		return null;
	}

	return (
		<div className={`billing-banner billing-banner-${pilotAlert.variant}`}>
			<div>
				<strong>{pilotAlert.title}</strong>
				<span>{pilotAlert.message}</span>
			</div>
			<a href={`mailto:${billingContactEmail}`} className="billing-banner-link">
				Contact {billingContactEmail}
			</a>
		</div>
	);
}
