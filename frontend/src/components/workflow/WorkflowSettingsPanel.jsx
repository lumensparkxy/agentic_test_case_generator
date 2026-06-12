import { WORKFLOW_SETTING_FIELDS } from "../../constants/workflow";

export default function WorkflowSettingsPanel({ title, description, settings, setSettings }) {
	const updateWorkflowSetting = (key) => (event) => {
		setSettings((prev) => ({ ...prev, [key]: event.target.value }));
	};

	return (
		<div className="workflow-settings-panel">
			<div className="workflow-settings-header">
				<div>
					<h3>{title}</h3>
					<p>{description}</p>
				</div>
				<span className="workflow-settings-badge">Optional</span>
			</div>
			<div className="workflow-settings-grid">
				{WORKFLOW_SETTING_FIELDS.map((field) => (
					<div className="form-group" key={field.key}>
						<label>{field.label}</label>
						<input
							type="number"
							min={field.min}
							max={field.max}
							placeholder="Use backend default"
							value={settings[field.key]}
							onChange={updateWorkflowSetting(field.key)}
						/>
					</div>
				))}
			</div>
			<p className="workflow-settings-help">Leave any field blank to use the backend default for that workflow.</p>
		</div>
	);
}
