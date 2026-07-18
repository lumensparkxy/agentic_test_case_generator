import { useState } from "react";

export default function useExecutionWorkflowState() {
	const [executionTargetBaseUrl, setExecutionTargetBaseUrl] = useState("");
	const [executionTargetEnvironment, setExecutionTargetEnvironment] = useState("");
	const [executionPreview, setExecutionPreview] = useState(null);
	const [selectedExecutionCandidateIds, setSelectedExecutionCandidateIds] = useState([]);
	const [executionRunResult, setExecutionRunResult] = useState(null);
	const [executionError, setExecutionError] = useState("");
	const [isPreviewingExecution, setIsPreviewingExecution] = useState(false);
	const [isRunningExecution, setIsRunningExecution] = useState(false);

	const resetExecutionWorkflowState = () => {
		setExecutionPreview(null);
		setSelectedExecutionCandidateIds([]);
		setExecutionRunResult(null);
		setExecutionError("");
	};

	return {
		executionTargetBaseUrl,
		setExecutionTargetBaseUrl,
		executionTargetEnvironment,
		setExecutionTargetEnvironment,
		executionPreview,
		setExecutionPreview,
		selectedExecutionCandidateIds,
		setSelectedExecutionCandidateIds,
		executionRunResult,
		setExecutionRunResult,
		executionError,
		setExecutionError,
		isPreviewingExecution,
		setIsPreviewingExecution,
		isRunningExecution,
		setIsRunningExecution,
		resetExecutionWorkflowState,
	};
}
