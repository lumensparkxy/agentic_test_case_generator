import { useState } from "react";

import { EMPTY_WORKFLOW_SETTINGS } from "../constants/workflow";

export default function useTestCaseWorkflowState() {
	const [templateName, setTemplateName] = useState("default");
	const [templateFormat, setTemplateFormat] = useState("table");
	const [testCases, setTestCases] = useState([]);
	const [requirementAnalysis, setRequirementAnalysis] = useState([]);
	const [coveragePlan, setCoveragePlan] = useState([]);
	const [coverageMetrics, setCoverageMetrics] = useState(null);
	const [testCaseReview, setTestCaseReview] = useState(null);
	const [testCaseWorkflowDiagnostics, setTestCaseWorkflowDiagnostics] = useState(null);
	const [appliedTestCaseWorkflowSettings, setAppliedTestCaseWorkflowSettings] = useState(null);
	const [testCaseIterationHistory, setTestCaseIterationHistory] = useState([]);
	const [feedback, setFeedback] = useState("");
	const [testCaseWorkflowSettings, setTestCaseWorkflowSettings] = useState(EMPTY_WORKFLOW_SETTINGS);
	const [expandedRows, setExpandedRows] = useState({});
	const [activeGenerateResultTab, setActiveGenerateResultTab] = useState("test-cases");
	const [isGenerating, setIsGenerating] = useState(false);

	const resetTestCaseWorkflowState = () => {
		setTestCases([]);
		setRequirementAnalysis([]);
		setCoveragePlan([]);
		setCoverageMetrics(null);
		setTestCaseReview(null);
		setTestCaseWorkflowDiagnostics(null);
		setAppliedTestCaseWorkflowSettings(null);
		setTestCaseIterationHistory([]);
		setExpandedRows({});
		setActiveGenerateResultTab("test-cases");
		setFeedback("");
	};

	return {
		templateName,
		setTemplateName,
		templateFormat,
		setTemplateFormat,
		testCases,
		setTestCases,
		requirementAnalysis,
		setRequirementAnalysis,
		coveragePlan,
		setCoveragePlan,
		coverageMetrics,
		setCoverageMetrics,
		testCaseReview,
		setTestCaseReview,
		testCaseWorkflowDiagnostics,
		setTestCaseWorkflowDiagnostics,
		appliedTestCaseWorkflowSettings,
		setAppliedTestCaseWorkflowSettings,
		testCaseIterationHistory,
		setTestCaseIterationHistory,
		feedback,
		setFeedback,
		testCaseWorkflowSettings,
		setTestCaseWorkflowSettings,
		expandedRows,
		setExpandedRows,
		activeGenerateResultTab,
		setActiveGenerateResultTab,
		isGenerating,
		setIsGenerating,
		resetTestCaseWorkflowState,
	};
}
