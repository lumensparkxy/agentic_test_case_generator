import React, { useEffect, useState } from "react";
import { GoogleLogin } from "@react-oauth/google";
import "./App.css";

const API_BASE = (() => {
	const configuredApiBase = (import.meta.env.VITE_API_BASE || "").trim();
	if (!configuredApiBase) {
		return "http://127.0.0.1:8000";
	}
	return configuredApiBase === "http://localhost:8000" ? "http://127.0.0.1:8000" : configuredApiBase;
})();
const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";
const STORAGE_AUTH_TOKEN = "tcg.auth.token";
const STORAGE_AUTH_USER = "tcg.auth.user";
const AUTH_REQUIRED_MESSAGE = "Sign in with Google to continue.";

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
	const [requirementAnalysis, setRequirementAnalysis] = useState([]);
	const [coveragePlan, setCoveragePlan] = useState([]);
	const [coverageMetrics, setCoverageMetrics] = useState(null);
	const [testCaseReview, setTestCaseReview] = useState(null);
	const [enrichedContext, setEnrichedContext] = useState(null);
	const [selectedArtifactSourceIds, setSelectedArtifactSourceIds] = useState([]);
	const [status, setStatus] = useState("");
	const [feedback, setFeedback] = useState("");
	const [reqFeedback, setReqFeedback] = useState("");
	const [expandedRows, setExpandedRows] = useState({});
	const [isGenerating, setIsGenerating] = useState(false);
	const [isParsing, setIsParsing] = useState(false);
	const [isAnalyzingContext, setIsAnalyzingContext] = useState(false);
	const [isExporting, setIsExporting] = useState(false);
	const [authToken, setAuthToken] = useState("");
	const [currentUser, setCurrentUser] = useState(null);
	const [isAuthenticating, setIsAuthenticating] = useState(false);
	const [isVerifyingSession, setIsVerifyingSession] = useState(true);

	const isAuthenticated = Boolean(authToken && currentUser);
	const authActionDisabled = !isAuthenticated || isAuthenticating || isVerifyingSession;
	const hasContextInputs = Boolean(appLink || prototypeLink || diagramLinks.trim() || imageLinks.trim());

	const toggleRowExpansion = (id) => {
		setExpandedRows(prev => ({ ...prev, [id]: !prev[id] }));
	};

	const resetContextAnalysis = () => {
		setEnrichedContext(null);
		setSelectedArtifactSourceIds([]);
	};

	const buildContextPayload = () => {
		const baseContext = {
			requirements,
			app_link: appLink || null,
			prototype_link: prototypeLink || null,
			diagram_links: diagramLinks
				? diagramLinks.split(";").map((x) => x.trim()).filter(Boolean)
				: null,
			image_links: imageLinks
				? imageLinks.split(";").map((x) => x.trim()).filter(Boolean)
				: null,
			notes: "Generated via UI",
		};

		if (!enrichedContext?.grounded_context) {
			return { ...baseContext, grounded_context: null };
		}

		const selectedIds = new Set(selectedArtifactSourceIds);
		const groundedContext = enrichedContext.grounded_context;
		return {
			...baseContext,
			grounded_context: {
				...groundedContext,
				artifact_sources: (groundedContext.artifact_sources || []).filter((source) => selectedIds.has(source.id)),
				ui_elements: (groundedContext.ui_elements || []).filter((element) => !element.source_id || selectedIds.has(element.source_id)),
			},
		};
	};

	const parseApiError = async (res, fallbackMessage) => {
		const text = await res.text();
		if (!text) return fallbackMessage;
		try {
			const parsed = JSON.parse(text);
			return parsed?.detail || parsed?.message || fallbackMessage;
		} catch {
			return text;
		}
	};

	const clearAuthState = (nextStatus = null) => {
		setAuthToken("");
		setCurrentUser(null);
		localStorage.removeItem(STORAGE_AUTH_TOKEN);
		localStorage.removeItem(STORAGE_AUTH_USER);
		if (nextStatus) {
			setStatus(nextStatus);
		}
	};

	const persistAuthState = (token, user) => {
		setAuthToken(token);
		setCurrentUser(user);
		localStorage.setItem(STORAGE_AUTH_TOKEN, token);
		localStorage.setItem(STORAGE_AUTH_USER, JSON.stringify(user));
	};

	useEffect(() => {
		const storedToken = localStorage.getItem(STORAGE_AUTH_TOKEN);
		const storedUserRaw = localStorage.getItem(STORAGE_AUTH_USER);

		if (!storedToken || !storedUserRaw) {
			setIsVerifyingSession(false);
			return;
		}

		let storedUser;
		try {
			storedUser = JSON.parse(storedUserRaw);
		} catch {
			clearAuthState();
			setIsVerifyingSession(false);
			return;
		}

		const restoreSession = async () => {
			try {
				const res = await fetch(`${API_BASE}/auth/me`, {
					method: "GET",
					headers: {
						Authorization: `Bearer ${storedToken}`
					}
				});
				if (!res.ok) {
					throw new Error("Stored session is no longer valid");
				}
				const data = await res.json();
				persistAuthState(storedToken, data || storedUser);
				setStatus(`Welcome back, ${(data || storedUser).name}.`);
			} catch {
				clearAuthState("Session expired. Please sign in again.");
			} finally {
				setIsVerifyingSession(false);
			}
		};

		restoreSession();
	}, []);

	const apiRequest = async (path, options = {}, authRequired = true) => {
		const headers = { ...(options.headers || {}) };

		if (authRequired) {
			if (!authToken) {
				setStatus(AUTH_REQUIRED_MESSAGE);
				throw new Error(AUTH_REQUIRED_MESSAGE);
			}
			headers.Authorization = `Bearer ${authToken}`;
		}

		const res = await fetch(`${API_BASE}${path}`, {
			...options,
			headers
		});

		if (authRequired && res.status === 401) {
			clearAuthState("Session expired or unauthorized. Please sign in again.");
			throw new Error("Session expired or unauthorized. Please sign in again.");
		}

		return res;
	};

	const handleGoogleLoginSuccess = async (credentialResponse) => {
		if (!credentialResponse?.credential) {
			setStatus("Google login did not return a credential token.");
			return;
		}

		setIsAuthenticating(true);
		setStatus("Signing in with Google...");
		try {
			const res = await apiRequest(
				"/auth/google/login",
				{
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({
						credential: credentialResponse.credential,
						client_id: credentialResponse.clientId || GOOGLE_CLIENT_ID || null
					})
				},
				false
			);
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to sign in with Google");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			persistAuthState(data.access_token, data.user);
			setStatus(`Signed in as ${data.user.name}.`);
		} catch (error) {
			clearAuthState(`Google sign-in failed: ${error.message}`);
		} finally {
			setIsAuthenticating(false);
		}
	};

	const handleGoogleLoginError = () => {
		setStatus("Google sign-in failed. Please try again.");
	};

	const handleLogout = async () => {
		setIsAuthenticating(true);
		try {
			await apiRequest("/auth/logout", { method: "POST" }, false);
		} catch {
			// Ignore logout network errors because session is local-storage backed.
		} finally {
			clearAuthState("Signed out.");
			setIsAuthenticating(false);
		}
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
			const res = await apiRequest("/requirements/parse", {
				method: "POST",
				body: formData
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to parse requirements");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			setRawText(data.raw_text || rawText);
			setRequirements(data.requirements || []);
			setTestCases([]);
			setRequirementAnalysis([]);
			setCoveragePlan([]);
			setCoverageMetrics(null);
			setTestCaseReview(null);
			resetContextAnalysis();
			setExpandedRows({});
			setFeedback("");
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
			const sharedPayload = {
				requirements,
				template: {
					name: templateName,
					format: templateFormat,
					fields: ["id", "title", "description", "priority", "type", "status", "preconditions", "steps", "expected_result", "test_data", "estimated_time", "automation_status", "component", "tags"]
				},
				context: buildContextPayload(),
			};

			const useRefineEndpoint = withFeedback && testCases.length > 0;
			const payload = useRefineEndpoint
				? {
					...sharedPayload,
					test_cases: testCases,
					feedback: feedback.trim()
				}
				: {
					...sharedPayload,
					feedback: withFeedback && feedback ? feedback.trim() : null
				};

			const res = await apiRequest(useRefineEndpoint ? "/testcases/refine" : "/testcases/generate", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(payload)
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to generate test cases");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			setTestCases(data.test_cases || []);
			setRequirementAnalysis(data.requirement_analysis || []);
			setCoveragePlan(data.coverage_plan || []);
			setCoverageMetrics(data.coverage_metrics || null);
			setTestCaseReview(data.review || null);
			setExpandedRows({});
			const reviewScore = typeof data.review?.score === "number" ? ` Score ${data.review.score}/${data.review.threshold}.` : "";
			const reviewSummary = data.review?.summary ? ` ${data.review.summary}` : "";
			setStatus(`${withFeedback ? "Test cases refined." : "Generated."}${reviewScore}${reviewSummary}`.trim());
			if (withFeedback) setFeedback("");
		} catch (error) {
			setStatus(`Generation failed: ${error.message}`);
		} finally {
			setIsGenerating(false);
		}
	};

	const exportToFormat = async (format) => {
		setIsExporting(true);
		setStatus(`Exporting to ${format.toUpperCase()}...`);
		try {
			const payload = { test_cases: testCases };
			const res = await apiRequest(`/export/${format}`, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(payload)
			});
			
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Export failed");
				throw new Error(errorMessage);
			}
			
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

	const getPriorityClass = (priority) => {
		const map = { Critical: "priority-critical", High: "priority-high", Medium: "priority-medium", Low: "priority-low" };
		return map[priority] || "";
	};

	const getStatusClass = (status) => {
		const map = { Draft: "status-draft", Ready: "status-ready", "In Review": "status-review", Approved: "status-approved" };
		return map[status] || "";
	};

	const getRequirementScenarioSummary = (requirementId) => {
		return coverageMetrics?.requirement_scenario_summary?.[requirementId] || null;
	};

	const getRequirementAnalysisSummary = (requirementId) => {
		return coverageMetrics?.requirement_analysis_summary?.[requirementId] || null;
	};

	const getRequirementAnalysisGaps = (requirementId) => {
		const analysis = requirementAnalysis.find((a) => a.requirement_id === requirementId);
		if (!analysis) {
			return { highRisks: [], rules: [], constraints: [], permissions: [], transitions: [] };
		}
		const summary = coverageMetrics?.requirement_analysis_summary?.[requirementId] || {};
		const coveredRules = new Set(summary.rules_covered || []);
		const coveredConstraints = new Set(summary.constraints_covered || []);
		const coveredPermissions = new Set(summary.permissions_covered || []);
		const coveredTransitions = new Set(summary.transitions_covered || []);
		const coveredRisks = new Set(summary.risks_covered || []);
		return {
			highRisks: (analysis.risk_signals || []).filter((r) => r.severity === "High" && !coveredRisks.has(r.id)).map((r) => r.title),
			rules: (analysis.business_rules || []).filter((r) => !coveredRules.has(r.id)).map((r) => r.title),
			constraints: (analysis.field_constraints || []).filter((c) => !coveredConstraints.has(c.id)).map((c) => c.field_name),
			permissions: (analysis.role_permissions || []).filter((p) => !coveredPermissions.has(p.id)).map((p) => `${p.role}: ${p.action}`),
			transitions: (analysis.state_transitions || []).filter((t) => !coveredTransitions.has(t.id)).map((t) => `${t.from_state} → ${t.to_state}`),
		};
	};

	const coveredScenarioTotal = coveragePlan.reduce((sum, plan) => sum + (getRequirementScenarioSummary(plan.requirement_id)?.covered_scenarios || 0), 0);
	const plannedScenarioTotal = coveragePlan.reduce((sum, plan) => sum + (plan.scenarios?.length || 0), 0);
	const mustHaveScenarioTotal = coveragePlan.reduce((sum, plan) => sum + (plan.scenarios?.filter((s) => s.must_have).length || 0), 0);
	const mustHaveCoveredScenarioTotal = coveragePlan.reduce((sum, plan) => {
		const missing = new Set(getRequirementScenarioSummary(plan.requirement_id)?.missing_scenario_types || []);
		return sum + (plan.scenarios?.filter((s) => s.must_have && !missing.has(s.scenario_type)).length || 0);
	}, 0);
	const missingScenarioCount = coveragePlan.reduce((sum, plan) => sum + (getRequirementScenarioSummary(plan.requirement_id)?.missing_scenario_types?.length || 0), 0);
	const requirementAnalysisGapCount = requirementAnalysis.reduce((sum, analysis) => {
		const gaps = getRequirementAnalysisGaps(analysis.requirement_id);
		return sum + Object.values(gaps).reduce((s, arr) => s + arr.length, 0);
	}, 0);

	const analyzeContext = async () => {
		setIsAnalyzingContext(true);
		setStatus("Analyzing context artifacts...");
		try {
			const res = await apiRequest("/requirements/enrich", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(buildContextPayload())
			});
			if (!res.ok) {
				const errorMessage = await parseApiError(res, "Failed to analyze context");
				throw new Error(errorMessage);
			}
			const data = await res.json();
			setEnrichedContext(data);
			setSelectedArtifactSourceIds((data.grounded_context?.artifact_sources || []).map((source) => source.id));
			setStatus("Context analyzed.");
		} catch (error) {
			setStatus(`Context analysis failed: ${error.message}`);
			resetContextAnalysis();
		} finally {
			setIsAnalyzingContext(false);
		}
	};

	const tabs = [
		{ id: 0, label: "Upload", title: "Upload Requirements" },
		{ id: 1, label: "Context", title: "Context Inputs" },
		{ id: 2, label: "Template", title: "Template Setup" },
		{ id: 3, label: "Generate", title: "Generate Test Cases" },
		{ id: 4, label: "Export", title: "Export Test Cases" }
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
						and export polished artifacts.
					</p>
				</div>
				<div className="header-right">
					<div className={`status ${isAuthenticated ? "status-authenticated" : ""}`}>
						<strong>Status:</strong> {status || "Idle"}
					</div>
					<div className="auth-panel">
						{isVerifyingSession ? (
							<span className="auth-message">Checking session...</span>
						) : isAuthenticated ? (
							<div className="auth-user">
								{currentUser?.picture && (
									<img src={currentUser.picture} alt={currentUser.name} className="auth-avatar" />
								)}
								<div className="auth-user-meta">
									<strong>{currentUser?.name}</strong>
									<span>{currentUser?.email}</span>
								</div>
								<button
									type="button"
									onClick={handleLogout}
									className="secondary auth-logout-btn"
									disabled={isAuthenticating}
								>
									{isAuthenticating ? "Signing out..." : "Sign Out"}
								</button>
							</div>
						) : GOOGLE_CLIENT_ID ? (
							<div className="auth-login">
								<GoogleLogin
									onSuccess={handleGoogleLoginSuccess}
									onError={handleGoogleLoginError}
									text="signin_with"
									shape="pill"
								/>
							</div>
						) : (
							<span className="auth-message auth-config-missing">
								Set VITE_GOOGLE_CLIENT_ID to enable Google sign-in.
							</span>
						)}
					</div>
				</div>
			</header>

			{!isAuthenticated && !isVerifyingSession && (
				<div className="auth-warning-banner">
					🔐 Sign in with Google to parse requirements, generate test cases, and export artifacts.
				</div>
			)}

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
							<button onClick={() => parseRequirements(false)} disabled={!file || isParsing || authActionDisabled}>
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
										disabled={!reqFeedback.trim() || isParsing || authActionDisabled}
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
						{hasContextInputs && (
							<div className="panel-form button-row">
								<button
									onClick={analyzeContext}
									disabled={isAnalyzingContext || authActionDisabled}
								>
									{isAnalyzingContext ? "⏳ Analyzing..." : "Analyze Context"}
								</button>
								{enrichedContext && (
									<button
										className="secondary"
										onClick={resetContextAnalysis}
									>
										Clear Analysis
									</button>
								)}
							</div>
						)}
						{enrichedContext?.grounded_context && (
							<div className="result-section">
								<h3>Grounded Context</h3>
								{(enrichedContext.grounded_context.artifact_sources || []).length > 0 && (
									<div className="artifact-sources">
										<h4>Artifact Sources</h4>
										<ul className="artifact-source-list">
											{enrichedContext.grounded_context.artifact_sources.map((source) => (
												<li key={source.id} className="artifact-source-item">
													<label>
														<input
															type="checkbox"
															checked={selectedArtifactSourceIds.includes(source.id)}
															onChange={(e) => {
																setSelectedArtifactSourceIds((prev) =>
																	e.target.checked
																		? [...prev, source.id]
																		: prev.filter((id) => id !== source.id)
																);
															}}
														/>
														<span>{source.url || source.id}</span>
														{source.type && <span className="artifact-type">{source.type}</span>}
													</label>
												</li>
											))}
										</ul>
									</div>
								)}
								<div className="analysis-detail-grid">
									{(enrichedContext.grounded_context.ui_elements || []).length > 0 && (
										<div className="analysis-detail-block">
											<h4>UI Elements</h4>
											<ul className="analysis-detail-list">
												{enrichedContext.grounded_context.ui_elements.slice(0, 6).map((el) => (
													<li key={el.id}>{el.element_type}: {el.label || el.id}</li>
												))}
											</ul>
										</div>
									)}
									{(enrichedContext.grounded_context.workflows || []).length > 0 && (
										<div className="analysis-detail-block">
											<h4>Workflows</h4>
											<ul className="analysis-detail-list">
												{enrichedContext.grounded_context.workflows.slice(0, 4).map((workflow) => (
													<li key={workflow.id}>{workflow.name}: {(workflow.transitions || []).join(", ") || workflow.description}</li>
												))}
											</ul>
										</div>
									)}
								</div>
							</div>
						)}
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
							Fields used: id, title, description, priority, type, status, preconditions, steps, expected result, test data, estimated time, automation status, component, tags.
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
							<button onClick={() => generateTestCases(false)} disabled={requirements.length === 0 || isGenerating || authActionDisabled}>
								{isGenerating ? "⏳ Generating..." : "Generate Test Cases"}
							</button>
						</div>

						{testCaseReview && (
							<div className={`review-banner ${testCaseReview.approved ? "review-approved" : "review-needs-work"}`}>
								<div className="review-banner-header">
									<strong>{testCaseReview.approved ? "Approved for export" : "Needs refinement"}</strong>
									<span>Score {testCaseReview.score}/{testCaseReview.threshold}</span>
								</div>
								<p>{testCaseReview.summary || "The review loop completed without a summary."}</p>
								{!testCaseReview.approved && testCaseReview.blocking_issues?.length > 0 && (
									<ul className="review-issues">
										{testCaseReview.blocking_issues.slice(0, 3).map((issue) => (
											<li key={issue}>{issue}</li>
										))}
									</ul>
								)}
							</div>
						)}

						{coveragePlan.length > 0 && (
							<div className="result-section">
								<details className="collapsible-panel">
									<summary className="collapsible-panel-summary">
										<span className="collapsible-panel-copy">
											<span className="collapsible-panel-title">Scenario Coverage Plan</span>
											<span className="collapsible-panel-description">
												Planned scenario intent per requirement, available on demand instead of taking over the page.
											</span>
										</span>
										<span className="collapsible-panel-meta">
											<span className="analysis-summary-pill">{coveragePlan.length} requirements</span>
											<span className="analysis-summary-pill">Scenarios {coveredScenarioTotal}/{plannedScenarioTotal}</span>
											<span className="analysis-summary-pill">Must-have {mustHaveCoveredScenarioTotal}/{mustHaveScenarioTotal}</span>
											{missingScenarioCount > 0 && (
												<span className="analysis-summary-pill collapsible-pill-alert">Missing {missingScenarioCount}</span>
											)}
											<span className="collapsible-panel-icon" aria-hidden="true">⏄</span>
										</span>
									</summary>
									<div className="collapsible-panel-body">
										<div className="coverage-plan-list">
											{coveragePlan.map((plan) => {
												const summary = getRequirementScenarioSummary(plan.requirement_id);
												const missingScenarioTypes = new Set(summary?.missing_scenario_types || []);
												return (
													<div key={plan.requirement_id} className="coverage-plan-card">
														<div className="coverage-plan-header">
															<div>
																<div className="coverage-plan-id">{plan.requirement_id}</div>
																<div className="coverage-plan-text">{plan.requirement_text}</div>
															</div>
															{summary && (
																<span className="coverage-plan-summary">
																	{summary.covered_scenarios}/{summary.planned_scenarios} planned scenarios covered
																</span>
															)}
														</div>
														<div className="coverage-chip-row">
															{plan.scenarios?.map((scenario) => {
																const isMissing = missingScenarioTypes.has(scenario.scenario_type);
																return (
																	<span
																		key={scenario.id}
																		className={`coverage-chip ${scenario.must_have ? "required" : "recommended"} ${isMissing ? "missing" : "covered"}`}
																		title={scenario.objective}
																	>
																		{scenario.scenario_type}
																	</span>
																);
															})}
														</div>
													</div>
												);
											})}
										</div>
									</div>
								</details>
							</div>
						)}

						{requirementAnalysis.length > 0 && (
							<div className="result-section">
								<details className="collapsible-panel">
									<summary className="collapsible-panel-summary">
										<span className="collapsible-panel-copy">
											<span className="collapsible-panel-title">Requirement Analysis</span>
											<span className="collapsible-panel-description">
												Rules, constraints, permissions, transitions, and risks extracted before scenario planning.
											</span>
										</span>
										<span className="collapsible-panel-meta">
											<span className="analysis-summary-pill">{requirementAnalysis.length} requirements</span>
											{coverageMetrics && (
												<>
													<span className="analysis-summary-pill">Rules {coverageMetrics.business_rules_covered || 0}/{coverageMetrics.business_rules_total || 0}</span>
													<span className="analysis-summary-pill">Constraints {coverageMetrics.field_constraints_covered || 0}/{coverageMetrics.field_constraints_total || 0}</span>
												</>
											)}
											{requirementAnalysisGapCount > 0 && (
												<span className="analysis-summary-pill collapsible-pill-alert">Gaps {requirementAnalysisGapCount}</span>
											)}
											<span className="collapsible-panel-icon" aria-hidden="true">⏄</span>
										</span>
									</summary>
									<div className="collapsible-panel-body">
										{coverageMetrics && (
											<div className="analysis-overview-row">
												<span className="analysis-summary-pill">Rules {coverageMetrics.business_rules_covered || 0}/{coverageMetrics.business_rules_total || 0}</span>
												<span className="analysis-summary-pill">Constraints {coverageMetrics.field_constraints_covered || 0}/{coverageMetrics.field_constraints_total || 0}</span>
												<span className="analysis-summary-pill">Permissions {coverageMetrics.role_permissions_covered || 0}/{coverageMetrics.role_permissions_total || 0}</span>
												<span className="analysis-summary-pill">Transitions {coverageMetrics.state_transitions_covered || 0}/{coverageMetrics.state_transitions_total || 0}</span>
												<span className="analysis-summary-pill">Risks {coverageMetrics.risk_signals_covered || 0}/{coverageMetrics.risk_signals_total || 0}</span>
											</div>
										)}
										<div className="analysis-card-list">
											{requirementAnalysis.map((analysis) => {
												const summary = getRequirementAnalysisSummary(analysis.requirement_id);
												const gaps = getRequirementAnalysisGaps(analysis.requirement_id);
												const hasGaps = Object.values(gaps).some((items) => items.length > 0);
												return (
													<div key={analysis.requirement_id} className="analysis-card">
														<div className="analysis-card-header">
															<div>
																<div className="coverage-plan-id">{analysis.requirement_id}</div>
																<div className="coverage-plan-text">{analysis.requirement_text}</div>
															</div>
															{summary && (
																<span className="coverage-plan-summary">
																	{summary.business_rules_covered}/{summary.business_rules_total} rules • {summary.field_constraints_covered}/{summary.field_constraints_total} constraints
																</span>
															)}
														</div>
														<div className="analysis-summary-row">
															<span className="analysis-summary-pill">Rules {analysis.business_rules?.length || 0}</span>
															<span className="analysis-summary-pill">Constraints {analysis.field_constraints?.length || 0}</span>
															<span className="analysis-summary-pill">Permissions {analysis.role_permissions?.length || 0}</span>
															<span className="analysis-summary-pill">Transitions {analysis.state_transitions?.length || 0}</span>
															<span className="analysis-summary-pill">Risks {analysis.risk_signals?.length || 0}</span>
														</div>
														{analysis.suggested_scenarios?.length > 0 && (
															<div className="analysis-chip-row">
																{analysis.suggested_scenarios.map((scenario) => (
																	<span key={`${analysis.requirement_id}-${scenario}`} className="analysis-chip">
																		{scenario}
																	</span>
																))}
															</div>
														)}
														<div className="analysis-detail-grid">
															<div className="analysis-detail-block">
																<h4>Business rules</h4>
																<ul className="analysis-detail-list">
																	{(analysis.business_rules || []).slice(0, 2).map((rule) => (
																		<li key={rule.id}>{rule.title}</li>
																	))}
																</ul>
															</div>
															<div className="analysis-detail-block">
																<h4>Constraints</h4>
																<ul className="analysis-detail-list">
																	{(analysis.field_constraints || []).slice(0, 2).map((constraint) => (
																		<li key={constraint.id}>{constraint.field_name}: {constraint.description}</li>
																	))}
																</ul>
															</div>
															<div className="analysis-detail-block">
																<h4>Permissions</h4>
																<ul className="analysis-detail-list">
																	{(analysis.role_permissions || []).slice(0, 2).map((permission) => (
																		<li key={permission.id}>{permission.role}: {permission.action}</li>
																	))}
																</ul>
															</div>
															<div className="analysis-detail-block">
																<h4>Transitions</h4>
																<ul className="analysis-detail-list">
																	{(analysis.state_transitions || []).slice(0, 2).map((transition) => (
																		<li key={transition.id}>{transition.from_state} → {transition.to_state}</li>
																	))}
																</ul>
															</div>
															<div className="analysis-detail-block">
																<h4>Risks</h4>
																<ul className="analysis-detail-list">
																	{(analysis.risk_signals || []).slice(0, 2).map((risk) => (
																		<li key={risk.id}>{risk.severity}: {risk.title}</li>
																	))}
																</ul>
															</div>
														</div>
														{hasGaps && (
															<div className="analysis-gap-block">
																<strong>Coverage gaps</strong>
																<ul className="analysis-gap-list">
																	{gaps.highRisks.slice(0, 2).map((item) => <li key={item}>{item}</li>)}
																	{gaps.rules.slice(0, 2).map((item) => <li key={item}>{item}</li>)}
																	{gaps.constraints.slice(0, 2).map((item) => <li key={item}>{item}</li>)}
																	{gaps.permissions.slice(0, 2).map((item) => <li key={item}>{item}</li>)}
																	{gaps.transitions.slice(0, 2).map((item) => <li key={item}>{item}</li>)}
																</ul>
															</div>
														)}
													</div>
												);
											})}
										</div>
									</div>
								</details>
							</div>
						)}

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
																{tc.steps?.slice(0, expandedRows[tc.id] ? undefined : 2).map((step, index) => (
																	<li key={`${tc.id}-step-${step.step || index + 1}`}>
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
													{tc.steps?.map((step, index) => (
														<li key={`${tc.id}-card-step-${step.step || index + 1}`}>
															<span className="step-action">{step.step || index + 1}. {step.action}</span>
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
										disabled={!feedback.trim() || isGenerating || authActionDisabled}
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
							Download your generated test cases as CSV, Excel, or JSON.
						</p>
						<div className="export-section">
							<h3 className="section-subtitle">📥 Quick Export</h3>
							<p className="helper-text">Download test cases directly to your computer.</p>
							<div className="export-buttons">
								<button 
									className="export-btn csv" 
									onClick={() => exportToFormat("csv")} 
									disabled={testCases.length === 0 || isExporting || authActionDisabled}
								>
									<span className="export-icon">📄</span>
									<span className="export-label">CSV</span>
									<span className="export-desc">Excel compatible</span>
								</button>
								<button 
									className="export-btn excel" 
									onClick={() => exportToFormat("excel")} 
									disabled={testCases.length === 0 || isExporting || authActionDisabled}
								>
									<span className="export-icon">📊</span>
									<span className="export-label">Excel</span>
									<span className="export-desc">Formatted .xlsx</span>
								</button>
								<button 
									className="export-btn json" 
									onClick={() => exportToFormat("json")} 
									disabled={testCases.length === 0 || isExporting || authActionDisabled}
								>
									<span className="export-icon">🧾</span>
									<span className="export-label">JSON</span>
									<span className="export-desc">API/Import ready</span>
								</button>
							</div>
						</div>
						<div className="panel-nav">
							<button onClick={goPrev} className="secondary">Back</button>
						</div>
					</section>
				)}
			</div>
		</div>
	);
}
