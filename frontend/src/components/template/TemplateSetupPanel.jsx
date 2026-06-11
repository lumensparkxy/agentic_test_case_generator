import { TEMPLATE_FORMAT_OPTIONS } from "../../constants/workflow";

export default function TemplateSetupPanel({
	templateName,
	setTemplateName,
	templateFormat,
	setTemplateFormat,
	goPrev,
	goNext,
}) {
	return (
		<section className="panel">
			<h2 className="panel-title">Template Setup</h2>
			<p className="panel-description">
				Configure the template name and output format for generated test cases.
			</p>
			<div className="panel-form">
				<div className="form-group">
					<label>Template name</label>
					<input
						placeholder="default"
						value={templateName}
						onChange={(e) => setTemplateName(e.target.value)}
					/>
				</div>
				<div className="form-group">
					<label>Template format</label>
					<select
						value={templateFormat}
						onChange={(e) => setTemplateFormat(e.target.value)}
					>
						{TEMPLATE_FORMAT_OPTIONS.map((option) => (
							<option key={option.value} value={option.value}>{option.label}</option>
						))}
					</select>
					<span className="field-hint">
						{TEMPLATE_FORMAT_OPTIONS.find((option) => option.value === templateFormat)?.description || "Choose how generated cases should be displayed."}
					</span>
				</div>
			</div>
			<span className="helper-text">
				Fields used: id, title, description, priority, type, status, preconditions, steps, expected result, test data, estimated time, automation status, component, linked requirement IDs, scenario refs, source refs, and tags.
			</span>
			<div className="panel-nav">
				<button onClick={goPrev} className="secondary">Back</button>
				<button onClick={goNext}>Next</button>
			</div>
		</section>
	);
}
