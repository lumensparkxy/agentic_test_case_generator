import { Disclosure } from "../ui/surfaces";
export default function RunDetailsDrawer({ title = "Run details", summary, children, defaultOpen = true }) {
	return (
		<Disclosure className="run-details-drawer" open={defaultOpen}>
			<summary>
				<span className="run-details-summary-copy">
					<strong>{title}</strong>
					{summary ? <span>{summary}</span> : null}
				</span>
				<span className="run-details-toggle">Show details</span>
			</summary>
			<div className="run-details-body">{children}</div>
		</Disclosure>
	);
}
