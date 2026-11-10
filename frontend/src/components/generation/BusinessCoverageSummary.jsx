import { useEffectEvent, useLayoutEffect, useRef } from "react";
import { List, ListItem } from "../ui/collections";
import { Badge, Disclosure } from "../ui/surfaces";
import { Button } from "../ui/controls";
import RouteLink from "../../app/RouteLink";
import { buildSavedCasePath, createSavedCasePathResolver } from "../../app/savedCaseLinks";
import { buildProjectWorkspacePath } from "../../app/workflowRoutes";
import { requiresIndependentReview } from "./improvementFindings";
import "./BusinessCoverageSummary.css";

const RECORDED_STATUS = {
	covered: { label: "Covered", tone: "success" },
	unmet: { label: "Unmet", tone: "warning" },
	unknown: { label: "Unverified", tone: "neutral" },
};

function groupBehaviors(obligations) {
	const requirements = new Map();
	for (const [index, row] of obligations.entries()) {
		const id = row.requirement_id || "";
		if (!requirements.has(id)) requirements.set(id, new Map());
		const behaviors = requirements.get(id);
		const behavior = row.obligation || "";
		if (!behaviors.has(behavior)) behaviors.set(behavior, []);
		behaviors.get(behavior).push({ row, index });
	}
	return requirements;
}

function GroupedAssessment({ assessment, projectId, savedSuite, onOpenCitation, onViewCase }) {
	const requirements = groupBehaviors(assessment.obligations || []);
	const casePath = createSavedCasePathResolver(projectId, savedSuite);
	const savedCases = new Map((savedSuite?.payload?.test_cases || []).map((item) => [item.id, item]));
	const cite = (evidence) => {
		const testCase = savedCases.get(evidence.test_case_id);
		const matchingSteps = (testCase?.steps || []).filter((step) => step.step === evidence.step);
		return casePath(testCase) &&
			Number.isSafeInteger(evidence.step) &&
			evidence.step > 0 &&
			matchingSteps.length === 1 &&
			matchingSteps[0].action === evidence.action &&
			matchingSteps[0].expected === evidence.expected
			? buildSavedCasePath(projectId, savedSuite.snapshot_id, testCase.id, evidence.step)
			: "";
	};
	const citationLink = (path, key, reference) => (
		<RouteLink to={path} navigate={() => onOpenCitation(path, key)} data-finding-citation={key} aria-label={`Open saved case ${reference}`}>
			{reference}
		</RouteLink>
	);
	const grounding = assessment.case_grounding || [];
	return (
		<>
			{requirements.size > 0 && (
				<section aria-label="Saved business behaviors" className="business-behaviors">
					{[...requirements].map(([requirementId, behaviors]) => (
						<section key={requirementId} className="business-behavior-requirement" aria-label={requirementId || "Requirement unavailable"}>
							<h3>{requirementId || "Requirement unavailable"}</h3>
							{[...behaviors].map(([behavior, rows]) => (
								<section key={behavior} className="business-behavior" aria-label={behavior || "Behavior name unavailable"}>
									<h4>{behavior || "Behavior name unavailable"}</h4>
									<List variant="grouped">
										{rows.map(({ row, index }) => {
											const status = RECORDED_STATUS[row.status] || RECORDED_STATUS.unknown;
											const reviewCaseIds =
												row.status === "unmet" && onViewCase
													? [...new Set((row.evidence || []).filter(cite).map((step) => step.test_case_id))]
													: [];
											return (
												<ListItem key={index} className="business-behavior-record">
													<Badge tone={status.tone}>{status.label}</Badge>
													{row.source_quote && <blockquote>{row.source_quote}</blockquote>}
													{row.reason && <p>{row.reason}</p>}
													{(row.evidence || []).length > 0 && (
														<List variant="grouped" aria-label="Saved step citations">
															{row.evidence.map((step, stepIndex) => {
																const path = cite(step);
																const reference = `${step.test_case_id}, step ${step.step}`;
																return (
																	<ListItem key={stepIndex}>
																		{path ? (
																			citationLink(path, `obligation-${index}-${stepIndex}`, reference)
																		) : (
																			<strong>{reference}</strong>
																		)}
																		<span>
																			· {step.action}
																			{step.expected ? ` → ${step.expected}` : ""}
																		</span>
																		{!path && <p className="helper-text">Exact saved step unavailable.</p>}
																	</ListItem>
																);
															})}
														</List>
													)}
													{reviewCaseIds.map((id) => (
														<Button key={id} variant="secondary" size="compact" onClick={() => onViewCase(id)}>
															Open {id} in Review
														</Button>
													))}
												</ListItem>
											);
										})}
									</List>
								</section>
							))}
						</section>
					))}
				</section>
			)}
			{grounding.length > 0 && (
				<section aria-label="Case grounding and source gaps" className="business-behaviors">
					<h3>Case grounding and source gaps</h3>
					<List variant="grouped">
						{grounding.map((item, index) => {
							const path = casePath(savedCases.get(item.test_case_id));
							const gaps = (item.prerequisites || []).filter((gap) => !gap.execution_prerequisite_name);
							return (
								<ListItem key={index} className="business-behavior-record">
									{path ? citationLink(path, `grounding-${index}`, item.test_case_id) : <strong>{item.test_case_id}</strong>}
									<p>{item.reason}</p>
									{gaps.length > 0 && (
										<List>
											{gaps.map((gap, gapIndex) => (
												<ListItem key={gapIndex}>
													<strong>{gap.description}</strong>
													<p>
														{gap.status?.replaceAll("_", " ")} · {gap.required ? "Required" : "Optional exploration"}. {gap.reason}
													</p>
												</ListItem>
											))}
										</List>
									)}
								</ListItem>
							);
						})}
					</List>
				</section>
			)}
		</>
	);
}

export default function BusinessCoverageSummary({
	assessment,
	grouped = false,
	projectId,
	savedSuite,
	savedSuiteStale,
	open,
	onOpen,
	navigate,
	onOpenCitation,
	returnCitation,
	onCitationReturnHandled,
	onViewCase,
}) {
	const coverageRef = useRef(null);
	const evidenceOpen = open || Boolean(returnCitation);
	const finishCitationReturn = useEffectEvent((found) => {
		if (found && !open) onOpen(true);
		onCitationReturnHandled();
	});
	useLayoutEffect(() => {
		if (!returnCitation) return;
		const link = Array.from(coverageRef.current.querySelectorAll("a[data-finding-citation]")).find(
			(item) => item.dataset.findingCitation === returnCitation.key && item.getAttribute("href") === returnCitation.path
		);
		if (!link) {
			finishCitationReturn(false);
			return;
		}
		link.focus({ preventScroll: true });
		// Let native history scroll restoration finish before revealing the focused citation.
		const frame = window.requestAnimationFrame(() => {
			if (document.activeElement === link) link.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
			finishCitationReturn(true);
		});
		return () => window.cancelAnimationFrame(frame);
	}, [returnCitation]);
	const designStatus = assessment?.design_status ?? assessment?.status;
	const scope =
		assessment?.scope === "whole_requirements"
			? "Whole requirements"
			: assessment?.scope === "selected_scenarios"
				? "Selected scenarios"
				: null;
	return (
		<section aria-label="Business coverage assessment" ref={coverageRef}>
			<p>
				Business coverage:{" "}
				{designStatus === "assessed_complete" ? "assessed complete" : designStatus === "incomplete" ? "incomplete" : "unverified"}. Human
				acceptance and execution have their own checks.
			</p>
			{grouped && savedSuiteStale && savedSuite && (
				<p>
					Earlier saved assessment · Suite <code>{savedSuite.snapshot_id}</code>
				</p>
			)}
			{grouped && scope && <p>Assessment scope · {scope}</p>}
			{grouped && requiresIndependentReview(assessment) && <p>Some assessment evidence remains unverified.</p>}
			{grouped && assessment && (
				<Disclosure
					open={evidenceOpen}
					onToggle={(event) => {
						if (event.currentTarget.open !== evidenceOpen) onOpen(event.currentTarget.open);
					}}
				>
					<summary>Source obligations and prerequisites</summary>
					{evidenceOpen && (
						<>
							{assessment.reason && <p>{assessment.reason}</p>}
							<GroupedAssessment
								assessment={assessment}
								projectId={projectId}
								savedSuite={savedSuite}
								onOpenCitation={onOpenCitation}
								onViewCase={onViewCase}
							/>
						</>
					)}
				</Disclosure>
			)}
			{grouped && projectId && (
				<p>
					<RouteLink to={`${buildProjectWorkspacePath(projectId, "inputs", "context")}#quality-feedback-heading`} navigate={navigate}>
						Open project quality findings
					</RouteLink>{" "}
					for application defects and other classified findings.
				</p>
			)}
			{!grouped && assessment && (
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
										{step.test_case_id}, step {step.step}: {step.action}
										{step.expected ? ` → ${step.expected}` : ""}
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
							(item.prerequisites || [])
								.filter((prerequisite) => !prerequisite.execution_prerequisite_name)
								.map((prerequisite, index) => (
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
