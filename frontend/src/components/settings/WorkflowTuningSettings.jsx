import WorkflowSettingsPanel from "../workflow/WorkflowSettingsPanel";

export default function WorkflowTuningSettings({
	requirementWorkflowSettings,
	setRequirementWorkflowSettings,
	testCaseWorkflowSettings,
	setTestCaseWorkflowSettings,
}) {
	return (
		<>
			<div className="settings-section-intro">
				<h3>Advanced workflow tuning</h3>
				<p>Leave fields blank for backend defaults. Adjust these only when you need stricter review gates or shorter AI loops.</p>
			</div>
			<WorkflowSettingsPanel
				title="Requirements workflow settings"
				description="Tune the requirement review loop when you want stricter gates or shorter runs."
				settings={requirementWorkflowSettings}
				setSettings={setRequirementWorkflowSettings}
			/>
			<WorkflowSettingsPanel
				title="Test-case workflow settings"
				description="Control validation strictness, loop length, and timeout behavior for generation and refinement."
				settings={testCaseWorkflowSettings}
				setSettings={setTestCaseWorkflowSettings}
			/>
		</>
	);
}
