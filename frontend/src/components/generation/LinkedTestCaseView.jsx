import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState } from "react";

import { buildSavedCasePath, readExactSavedCaseEvidence } from "../../app/savedCaseLinks";
import { buildProjectPath, PROJECT_DESTINATIONS } from "../../app/workflowRoutes";
import { Button, Link } from "../ui/controls";
import GeneratedTestCasesView from "./GeneratedTestCasesView";

export default function LinkedTestCaseView({ projectId, snapshotId, caseId, request, navigate }) {
	const [result, setResult] = useState(null);
	const [attempt, setAttempt] = useState(0);
	const headingRef = useRef(null);
	useLayoutEffect(() => {
		headingRef.current?.focus();
	}, []);
	const fetchCase = useEffectEvent((signal) => readExactSavedCaseEvidence({ projectId, snapshotId, caseId, request, signal }));

	useEffect(() => {
		if (!snapshotId || !caseId) return;
		const controller = new AbortController();
		async function load() {
			try {
				const evidence = await fetchCase(controller.signal);
				if (!controller.signal.aborted) setResult(evidence ? { evidence } : { unavailable: true });
			} catch {
				if (!controller.signal.aborted) setResult({ retryable: true });
			}
		}
		load();
		return () => controller.abort();
	}, [projectId, snapshotId, caseId, attempt]);

	const suitePath = buildProjectPath(projectId, PROJECT_DESTINATIONS.TEST_CASES);
	const evidence = result?.evidence;
	const unavailable = !snapshotId || !caseId || result?.unavailable;
	return (
		<section className="panel linked-test-case" aria-label="Saved test case">
			<header className="linked-test-case-header">
				<h2 className="panel-title" ref={headingRef} tabIndex={-1}>
					Saved test case
				</h2>
				<Link
					href={suitePath}
					onClick={(event) => {
						if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
						event.preventDefault();
						navigate(suitePath);
					}}
				>
					Open current suite
				</Link>
			</header>
			{unavailable ? (
				<p role="status">This saved test case is unavailable.</p>
			) : result?.retryable ? (
				<div role="alert">
					<p>Couldn’t load this saved test case.</p>
					<Button
						variant="secondary"
						onClick={() => {
							setResult(null);
							setAttempt((value) => value + 1);
						}}
					>
						Retry saved case
					</Button>
				</div>
			) : !evidence ? (
				<p role="status">Loading saved test case…</p>
			) : (
				<>
					<p className="linked-test-case-meta">
						{evidence.historical ? "Historical saved suite" : evidence.stale ? "Current saved suite needs review" : "Current saved suite"} ·
						Version {evidence.suite_version} · Project revision {evidence.project_revision}
					</p>
					<GeneratedTestCasesView
						singleCase
						projectId={projectId}
						snapshotId={snapshotId}
						testCases={[evidence.test_case]}
						requirements={evidence.requirements}
						substantiveAssessment={{ obligations: evidence.obligations }}
						exactCasePath={buildSavedCasePath(projectId, snapshotId, caseId)}
					/>
				</>
			)}
		</section>
	);
}
