import { List, ListItem } from "../ui/collections";
import { Disclosure } from "../ui/surfaces";

export default function BusinessCoverageSummary({ assessment }) {
	return (
		<section aria-label="Business coverage assessment">
			<p>
				Business coverage:{" "}
				{assessment?.status === "assessed_complete"
					? "assessed complete"
					: assessment?.status === "incomplete"
						? "incomplete"
						: "unverified"}
				. This is test design review, not execution or human acceptance.
			</p>
			{assessment && (
				<Disclosure>
					<summary>Source obligations and prerequisites</summary>
					<p>{assessment.reason}</p>
					<List>
						{(assessment.obligations || []).map((item, index) => (
							<ListItem key={index}>
								<strong>
									{item.requirement_id}: {item.obligation} — {item.status}
								</strong>
								<p>Source: “{item.source_quote}”</p>
								<p>{item.reason}</p>
								{(item.evidence || []).map((step, i) => (
									<p key={i}>
										{step.test_case_id}, step {step.step}: {step.action} → {step.expected}
									</p>
								))}
							</ListItem>
						))}
						{(assessment.case_grounding || []).map((item) => (
							<ListItem key={`grounding-${item.test_case_id}`}>
								<strong>{item.test_case_id}: Source grounding</strong>
								<p>{item.reason}</p>
							</ListItem>
						))}
						{(assessment.case_grounding || []).flatMap((item) =>
							(item.prerequisites || []).map((prerequisite, index) => (
								<ListItem key={`${item.test_case_id}-${index}`}>
									<strong>
										{item.test_case_id}: {prerequisite.description}
									</strong>
									<p>
										{prerequisite.status.replaceAll("_", " ")} · {prerequisite.required ? "Required" : "Optional exploration"}.{" "}
										{prerequisite.reason}
									</p>
								</ListItem>
							))
						)}
					</List>
				</Disclosure>
			)}
		</section>
	);
}
