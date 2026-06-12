import { useState } from "react";

import { EMPTY_WORKFLOW_SETTINGS } from "../constants/workflow";

export default function useRequirementWorkflowState() {
	const [file, setFile] = useState(null);
	const [rawText, setRawText] = useState("");
	const [requirements, setRequirements] = useState([]);
	const [requirementReview, setRequirementReview] = useState(null);
	const [requirementCoverageMetrics, setRequirementCoverageMetrics] = useState(null);
	const [requirementWorkflowDiagnostics, setRequirementWorkflowDiagnostics] = useState(null);
	const [appliedRequirementWorkflowSettings, setAppliedRequirementWorkflowSettings] = useState(null);
	const [requirementIterationHistory, setRequirementIterationHistory] = useState([]);
	const [reqFeedback, setReqFeedback] = useState("");
	const [requirementWorkflowSettings, setRequirementWorkflowSettings] = useState(EMPTY_WORKFLOW_SETTINGS);
	const [requirementSourceMode, setRequirementSourceMode] = useState("file");
	const [isParsing, setIsParsing] = useState(false);

	return {
		file,
		setFile,
		rawText,
		setRawText,
		requirements,
		setRequirements,
		requirementReview,
		setRequirementReview,
		requirementCoverageMetrics,
		setRequirementCoverageMetrics,
		requirementWorkflowDiagnostics,
		setRequirementWorkflowDiagnostics,
		appliedRequirementWorkflowSettings,
		setAppliedRequirementWorkflowSettings,
		requirementIterationHistory,
		setRequirementIterationHistory,
		reqFeedback,
		setReqFeedback,
		requirementWorkflowSettings,
		setRequirementWorkflowSettings,
		requirementSourceMode,
		setRequirementSourceMode,
		isParsing,
		setIsParsing,
	};
}
