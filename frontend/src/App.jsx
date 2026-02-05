import React, { useState } from "react";
import "./App.css";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export default function App() {
	const [file, setFile] = useState(null);
	const [rawText, setRawText] = useState("");
	const [requirements, setRequirements] = useState([]);
	const [activeTab, setActiveTab] = useState(0);
	const [appLink, setAppLink] = useState("");
	const [prototypeLink, setPrototypeLink] = useState("");
	const [diagramLinks, setDiagramLinks] = useState("");
	const [imageLinks, setImageLinks] = useState("");
	const [templateName, setTemplateName] = useState("default");
	const [templateFormat, setTemplateFormat] = useState("table");
	const [testCases, setTestCases] = useState([]);
	const [jiraProject, setJiraProject] = useState("");
	const [jiraIssueType, setJiraIssueType] = useState("Test");
	const [status, setStatus] = useState("");
	const [feedback, setFeedback] = useState("");
	const [reqFeedback, setReqFeedback] = useState("");
	const [expandedRows, setExpandedRows] = useState({});
	const [isGenerating, setIsGenerating] = useState(false);
	const [isParsing, setIsParsing] = useState(false);
	const [isExporting, setIsExporting] = useState(false);
	const [exportFormat, setExportFormat] = useState("csv");

	const toggleRowExpansion = (id) => {
		setExpandedRows(prev => ({ ...prev, [id]: !prev[id] }));
	};

	const parseRequirements = async (withFeedback = false) => {
		if (!file && !withFeedback) return;
		setIsParsing(true);
		setStatus(withFeedback ? "Refining requirements with feedback..." : "Parsing requirements...");
		try {
			const formData = new FormData();
			if (file) formData.append("file", file);
			if (withFeedback && reqFeedback) {
				formData.append("feedback", reqFeedback);
				formData.append("existing_requirements", JSON.stringify(requirements));
			}
			const res = await fetch(`${API_BASE}/requirements/parse`, {
				method: "POST",
				body: formData
			});
			if (!res.ok) {
				const errorText = await res.text();
				throw new Error(errorText || "Failed to parse requirements");
			}
			const data = await res.json();
			setRawText(data.raw_text || rawText);
			setRequirements(data.requirements || []);
			setStatus(withFeedback ? "Requirements refined." : "Parsed.");
			if (withFeedback) setReqFeedback("");
		} catch (error) {
			setStatus(`Parse failed: ${error.message}`);
		} finally {
			setIsParsing(false);
		}
	};

	const generateTestCases = async (withFeedback = false) => {
		setIsGenerating(true);
		setStatus(withFeedback ? "Refining test cases with feedback..." : "Generating test cases...");
		try {
			const payload = {
				requirements,
				template: {
					name: templateName,
					format: templateFormat,
					fields: ["id", "title", "description", "priority", "type", "status", "preconditions", "steps", "expected_result", "test_data", "estimated_time", "automation_status", "component", "tags"]
				},
				context: {
					requirements,
					app_link: appLink || null,
					prototype_link: prototypeLink || null,
					diagram_links: diagramLinks
						? diagramLinks.split(";").map((x) => x.trim())
						: null,
					image_links: imageLinks
						? imageLinks.split(";").map((x) => x.trim())
						: null,
					notes: "Generated via UI"
				},
				feedback: withFeedback && feedback ? feedback : null
			};
			const res = await fetch(`${API_BASE}/testcases/generate`, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(payload)
			});
			const data = await res.json();
			setTestCases(data.test_cases || []);
			setStatus(withFeedback ? "Test cases refined." : "Generated.");
			if (withFeedback) setFeedback("");
		} finally {
			setIsGenerating(false);
		}
	};

	const exportToJira = async () => {
		setIsExporting(true);
		setStatus("Exporting to JIRA...");
		try {
			const payload = {
				project_key: jiraProject,
				issue_type: jiraIssueType,
				test_cases: testCases
			};
			const res = await fetch(`${API_BASE}/export/jira`, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(payload)
			});
			const data = await res.json();
			setStatus(`${data.status}: ${data.message}`);
		} finally {
			setIsExporting(false);
		}
	};

	const exportToFormat = async (format) => {
		setIsExporting(true);
		setStatus(`Exporting to ${format.toUpperCase()}...`);
		try {
			const payload = { test_cases: testCases };
			const res = await fetch(`${API_BASE}/export/${format}`, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(payload)
			});
			
			if (!res.ok) throw new Error("Export failed");
			
			// Download the file
			const blob = await res.blob();
			const url = window.URL.createObjectURL(blob);
			const a = document.createElement("a");
			a.href = url;
			const extensions = { csv: "csv", excel: "xlsx", json: "json" };
			a.download = `test_cases.${extensions[format] || format}`;
			document.body.appendChild(a);
			a.click();
			a.remove();
			window.URL.revokeObjectURL(url);
			setStatus(`✓ Exported to ${format.toUpperCase()} successfully`);
		} catch (error) {
			setStatus(`Export failed: ${error.message}`);
		} finally {
			setIsExporting(false);
		}
	};

	const generateAutomation = async () => {
		setStatus("Generating Playwright POM...");
		const payload = {
			test_cases: testCases,
			target_base_url: appLink || null
		};
		const res = await fetch(`${API_BASE}/automation/playwright`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(payload)
		});
		const data = await res.json();
		setStatus(`${data.status}: ${data.notes}`);
	};

	const getPriorityClass = (priority) => {
		const map = { Critical: "priority-critical", High: "priority-high", Medium: "priority-medium", Low: "priority-low" };
		return map[priority] || "";
	};

	const getStatusClass = (status) => {
		const map = { Draft: "status-draft", Ready: "status-ready", "In Review": "status-review", Approved: "status-approved" };
		return map[status] || "";
	};

	const tabs = [
		{ id: 0, label: "Upload", title: "Upload Requirements" },
		{ id: 1, label: "Context", title: "Context Inputs" },
		{ id: 2, label: "Template", title: "Template Setup" },
		{ id: 3, label: "Generate", title: "Generate Test Cases" },
		{ id: 4, label: "Export", title: "Export Test Cases" },
		{ id: 5, label: "Automation", title: "Playwright POM" }
	];

	const goNext = () => setActiveTab((prev) => Math.min(prev + 1, tabs.length - 1));
	const goPrev = () => setActiveTab((prev) => Math.max(prev - 1, 0));

	return (
		<div className="page">
			<header className="header">
				<div>
					<h1 className="title">Agentic Test Case Generator</h1>
					<p className="subtitle">
						A guided pipeline to parse requirements, enrich context, generate test cases,
						export to JIRA, and create Playwright (Python) POM stubs.
					</p>
				</div>
				<div className="status">
					<strong>Status:</strong> {status || "Idle"}
				</div>
			</header>

			<div className="tabs">
				{tabs.map((tab) => (
					<button
						key={tab.id}
						className={`tab ${activeTab === tab.id ? "active" : ""}`}
						onClick={() => setActiveTab(tab.id)}
					>
						<span className="tab-number">{tab.id + 1}</span>
						<span className="tab-label">{tab.label}</span>
					</button>
				))}
			</div>

			<div className="tab-content">
				{activeTab === 0 && (
					<section className="panel">
						<h2 className="panel-title">Upload Requirements</h2>
						<p className="panel-description">
							Add your requirements file (.md, .docx, or .xlsx) and parse it to extract requirement items.
						</p>
						<div className="panel-form">
							<div className="form-group">
								<label>Requirements file</label>
								<input
									type="file"
									accept=".md,.docx,.xlsx"
									onChange={(e) => setFile(e.target.files?.[0] || null)}
								/>
							</div>
							<button onClick={() => parseRequirements(false)} disabled={!file || isParsing}>
								{isParsing ? "⏳ Parsing..." : "Parse Requirements"}
							</button>
						</div>

						<div className="result-section">
							<h3>Raw Text</h3>
							<pre>{rawText || "No content yet"}</pre>
						</div>

						<div className="result-section">
							<h3>Extracted Requirements</h3>
							{requirements.length === 0 ? (
								<span className="helper-text">No requirements extracted yet.</span>
							) : (
								<ul className="requirements-list">
									{requirements.map((req, index) => (
										<li key={req.id || req.text || index}>
											<strong>{req.id || `REQ-${index + 1}`}:</strong> {req.text || req.title || ""}
										</li>
									))}
								</ul>
							)}
						</div>

						{requirements.length > 0 && (
							<div className="feedback-section">
								<h3>Human Feedback</h3>
								<p className="feedback-description">
									Provide feedback on the extracted requirements. The AI will refine them based on your input.
								</p>
								<textarea
									className="feedback-textarea"
									placeholder="Enter your feedback here... e.g., 'Merge REQ-003 and REQ-004 into one', 'Split REQ-001 into multiple requirements', 'REQ-005 is too vague, make it more specific', 'Add a requirement for error handling', etc."
									value={reqFeedback}
									onChange={(e) => setReqFeedback(e.target.value)}
									rows={4}
								/>
								<div className="feedback-actions">
									<button 
										onClick={() => parseRequirements(true)} 
										disabled={!reqFeedback.trim() || isParsing}
										className="feedback-button"
									>
										{isParsing ? "⏳ Refining Requirements..." : "🔄 Implement Changes"}
									</button>
								</div>
							</div>
						)}

						<div className="panel-nav">
							<button onClick={goNext} className="secondary">
								Next
							</button>
						</div>
					</section>
				)}

				{activeTab === 1 && (
					<section className="panel">
						<h2 className="panel-title">Context Inputs</h2>
						<p className="panel-description">
							Add links and references to enrich the test case generation context.
						</p>
						<div className="panel-form two-cols">
							<div className="form-group">
								<label>Application link</label>
								<input
									placeholder="https://your-app"
									value={appLink}
									onChange={(e) => setAppLink(e.target.value)}
								/>
							</div>
							<div className="form-group">
								<label>Prototype link</label>
								<input
									placeholder="https://prototype"
									value={prototypeLink}
									onChange={(e) => setPrototypeLink(e.target.value)}
								/>
							</div>
							<div className="form-group">
								<label>Diagram links</label>
								<input
									placeholder="Link1; Link2"
									value={diagramLinks}
									onChange={(e) => setDiagramLinks(e.target.value)}
								/>
							</div>
							<div className="form-group">
								<label>Image links</label>
								<input
									placeholder="Link1; Link2"
									value={imageLinks}
									onChange={(e) => setImageLinks(e.target.value)}
								/>
							</div>
						</div>
						<div className="panel-nav">
							<button onClick={goPrev} className="secondary">Back</button>
							<button onClick={goNext}>Next</button>
						</div>
					</section>
				)}

				{activeTab === 2 && (
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
								<input
									placeholder="table"
									value={templateFormat}
									onChange={(e) => setTemplateFormat(e.target.value)}
								/>
							</div>
						</div>
						<span className="helper-text">
							Fields used: id, title, preconditions, steps, tags.
						</span>
						<div className="panel-nav">
							<button onClick={goPrev} className="secondary">Back</button>
							<button onClick={goNext}>Next</button>
						</div>
					</section>
				)}

				{activeTab === 3 && (
					<section className="panel">
						<h2 className="panel-title">Generate Test Cases</h2>
						<p className="panel-description">
							Generate structured test cases from your parsed requirements and context.
						</p>
						<div className="panel-form button-row">
							<button onClick={() => generateTestCases(false)} disabled={requirements.length === 0 || isGenerating}>
								{isGenerating ? "⏳ Generating..." : "Generate Test Cases"}
							</button>
						</div>

						<div className="result-section">
							<h3>Generated Test Cases</h3>
							{testCases.length === 0 ? (
								<span className="helper-text">No test cases generated yet.</span>
							) : templateFormat === "table" ? (
								<div className="test-cases-table-wrapper">
									<table className="test-cases-table">
										<thead>
											<tr>
												<th className="col-id">ID</th>
												<th className="col-title">Title</th>
												<th className="col-priority">Priority</th>
												<th className="col-type">Type</th>
												<th className="col-status">Status</th>
												<th className="col-preconditions">Preconditions</th>
												<th className="col-steps">Steps</th>
												<th className="col-expected">Expected Result</th>
												<th className="col-testdata">Test Data</th>
												<th className="col-time">Est. Time</th>
												<th className="col-automation">Automation</th>
												<th className="col-component">Component</th>
												<th className="col-tags">Tags</th>
											</tr>
										</thead>
										<tbody>
											{testCases.map((tc) => (
												<React.Fragment key={tc.id}>
													<tr className={expandedRows[tc.id] ? "expanded" : ""} onClick={() => toggleRowExpansion(tc.id)}>
														<td className="tc-id">{tc.id}</td>
														<td className="tc-title">
															<div className="title-cell">
																<span className="expand-icon">{expandedRows[tc.id] ? "▼" : "▶"}</span>
																{tc.title}
															</div>
															{tc.description && <div className="tc-description">{tc.description}</div>}
														</td>
														<td className="tc-priority">
															<span className={`priority-badge ${getPriorityClass(tc.priority)}`}>{tc.priority || "Medium"}</span>
														</td>
														<td className="tc-type">{tc.type || "Functional"}</td>
														<td className="tc-status">
															<span className={`status-badge ${getStatusClass(tc.status)}`}>{tc.status || "Draft"}</span>
														</td>
														<td className="tc-preconditions">{tc.preconditions || "-"}</td>
														<td className="tc-steps">
															<ol>
																{tc.steps?.slice(0, expandedRows[tc.id] ? undefined : 2).map((step) => (
																	<li key={step.step || step.action}>
																		<strong>{step.action}</strong>
																		<span className="step-expected">→ {step.expected}</span>
																		{step.test_data && <span className="step-data">📋 {step.test_data}</span>}
																	</li>
																))}
																{!expandedRows[tc.id] && tc.steps?.length > 2 && (
																	<li className="more-steps">+{tc.steps.length - 2} more steps...</li>
																)}
															</ol>
														</td>
														<td className="tc-expected-result">{tc.expected_result || "-"}</td>
														<td className="tc-testdata">{tc.test_data || "-"}</td>
														<td className="tc-time">{tc.estimated_time || "-"}</td>
														<td className="tc-automation">
															<span className={`automation-badge ${tc.automation_status?.replace(/\s/g, "-").toLowerCase() || "manual"}`}>
																{tc.automation_status || "Manual"}
															</span>
														</td>
														<td className="tc-component">{tc.component || "-"}</td>
														<td className="tc-tags">
															{tc.tags?.map((tag) => (
																<span key={tag} className="tag">{tag}</span>
															))}
														</td>
													</tr>
												</React.Fragment>
											))}
										</tbody>
									</table>
								</div>
							) : (
								<div className="test-cases-grid">
									{testCases.map((tc) => (
										<div key={tc.id} className="case-card">
											<div className="case-header">
												<span className="case-id">{tc.id}</span>
												<span className="case-title">{tc.title}</span>
												<span className={`priority-badge ${getPriorityClass(tc.priority)}`}>{tc.priority}</span>
											</div>
											{tc.description && <div className="case-description">{tc.description}</div>}
											<div className="case-meta">
												<span className="meta-item"><strong>Type:</strong> {tc.type}</span>
												<span className={`status-badge ${getStatusClass(tc.status)}`}>{tc.status}</span>
												<span className="meta-item"><strong>Est:</strong> {tc.estimated_time}</span>
											</div>
											{tc.preconditions && (
												<div className="case-preconditions">{tc.preconditions}</div>
											)}
											<div className="case-steps">
												<strong>Steps</strong>
												<ol>
													{tc.steps?.map((step) => (
														<li key={step.step || step.action}>
															<span className="step-action">{step.step}. {step.action}</span>
															<span className="step-expected">→ {step.expected}</span>
															{step.test_data && <span className="step-data">📋 {step.test_data}</span>}
														</li>
													))}
												</ol>
											</div>
											{tc.expected_result && (
												<div className="case-expected"><strong>Expected Result:</strong> {tc.expected_result}</div>
											)}
											{tc.tags && tc.tags.length > 0 && (
												<div className="case-tags">
													{tc.tags.map((tag) => (
														<span key={tag} className="tag">{tag}</span>
													))}
												</div>
											)}
										</div>
									))}
								</div>
							)}
						</div>

						{testCases.length > 0 && (
							<div className="feedback-section">
								<h3>Human Feedback</h3>
								<p className="feedback-description">
									Provide feedback on the generated test cases. The AI will refine them based on your input.
								</p>
								<textarea
									className="feedback-textarea"
									placeholder="Enter your feedback here... e.g., 'Add more negative test cases for upload feature', 'TC-003 needs more detailed steps', 'Include security test cases', etc."
									value={feedback}
									onChange={(e) => setFeedback(e.target.value)}
									rows={4}
								/>
								<div className="feedback-actions">
									<button 
										onClick={() => generateTestCases(true)} 
										disabled={!feedback.trim() || isGenerating}
										className="feedback-button"
									>
										{isGenerating ? "⏳ Updating Test Cases..." : "🔄 Implement Changes"}
									</button>
								</div>
							</div>
						)}

						<div className="panel-nav">
							<button onClick={goPrev} className="secondary">Back</button>
							<button onClick={goNext} disabled={testCases.length === 0}>Next</button>
						</div>
					</section>
				)}

				{activeTab === 4 && (
					<section className="panel">
						<h2 className="panel-title">Export Test Cases</h2>
						<p className="panel-description">
							Export your generated test cases in various formats.
						</p>
						
						{/* Quick Export Options */}
						<div className="export-section">
							<h3 className="section-subtitle">📥 Quick Export</h3>
							<p className="helper-text">Download test cases directly to your computer.</p>
							<div className="export-buttons">
								<button 
									className="export-btn csv" 
									onClick={() => exportToFormat("csv")} 
									disabled={testCases.length === 0 || isExporting}
								>
									<span className="export-icon">📄</span>
									<span className="export-label">CSV</span>
									<span className="export-desc">Excel compatible</span>
								</button>
								<button 
									className="export-btn excel" 
									onClick={() => exportToFormat("excel")} 
									disabled={testCases.length === 0 || isExporting}
								>
									<span className="export-icon">📊</span>
									<span className="export-label">Excel</span>
									<span className="export-desc">Formatted .xlsx</span>
								</button>
								<button 
									className="export-btn json" 
									onClick={() => exportToFormat("json")} 
									disabled={testCases.length === 0 || isExporting}
								>
									<span className="export-icon">{ }</span>
									<span className="export-label">JSON</span>
									<span className="export-desc">API/Import ready</span>
								</button>
							</div>
						</div>
						
						<hr className="section-divider" />
						
						{/* JIRA Integration */}
						<div className="export-section">
							<h3 className="section-subtitle">🔗 JIRA Integration</h3>
							<p className="helper-text">Push test cases directly to your JIRA project.</p>
							<div className="panel-form two-cols">
								<div className="form-group">
									<label>JIRA Project Key</label>
									<input
										placeholder="e.g., QA, TEST, PROJ"
										value={jiraProject}
										onChange={(e) => setJiraProject(e.target.value)}
									/>
								</div>
								<div className="form-group">
									<label>Issue Type</label>
									<select value={jiraIssueType} onChange={(e) => setJiraIssueType(e.target.value)}>
										<option value="Test">Test</option>
										<option value="Test Case">Test Case</option>
										<option value="Test Execution">Test Execution</option>
										<option value="Story">Story</option>
										<option value="Task">Task</option>
									</select>
								</div>
							</div>
							<div className="panel-form button-row">
								<button 
									className="export-btn jira" 
									onClick={exportToJira} 
									disabled={testCases.length === 0 || !jiraProject || isExporting}
								>
									{isExporting ? "⏳ Exporting..." : "🚀 Export to JIRA"}
								</button>
							</div>
							<span className="helper-text warning">
								⚠️ JIRA integration requires API credentials to be configured in the backend.
							</span>
						</div>
						
						<hr className="section-divider" />
						
						{/* Other Integrations */}
						<div className="export-section">
							<h3 className="section-subtitle">📦 Other Integrations</h3>
							<div className="integration-grid">
								<div className="integration-card disabled">
									<span className="integration-icon">🧪</span>
									<span className="integration-name">Xray</span>
									<span className="integration-status">Coming Soon</span>
								</div>
								<div className="integration-card disabled">
									<span className="integration-icon">🧫</span>
									<span className="integration-name">TestRail</span>
									<span className="integration-status">Coming Soon</span>
								</div>
								<div className="integration-card disabled">
									<span className="integration-icon">🔬</span>
									<span className="integration-name">qTest</span>
									<span className="integration-status">Coming Soon</span>
								</div>
								<div className="integration-card disabled">
									<span className="integration-icon">📋</span>
									<span className="integration-name">Azure DevOps</span>
									<span className="integration-status">Coming Soon</span>
								</div>
							</div>
						</div>
						
						<div className="panel-nav">
							<button onClick={goPrev} className="secondary">Back</button>
							<button onClick={goNext} disabled={testCases.length === 0}>Next</button>
						</div>
					</section>
				)}

				{activeTab === 5 && (
					<section className="panel">
						<h2 className="panel-title">Playwright POM</h2>
						<p className="panel-description">
							Generate Playwright (Python) Page Object Model stubs from test cases.
						</p>
						<div className="panel-form button-row">
							<button onClick={generateAutomation} disabled={testCases.length === 0}>
								Generate Automation Stubs
							</button>
						</div>
						<span className="helper-text">
							Generates POM and test file stubs based on the generated test cases.
						</span>
						<div className="panel-nav">
							<button onClick={goPrev} className="secondary">Back</button>
						</div>
					</section>
				)}
			</div>
		</div>
	);
}
