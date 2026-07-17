import { useState } from "react";

export default function useExecutionWorkflowState() {
	const [executionTargetBaseUrl, setExecutionTargetBaseUrl] = useState("");
	const [executionTargetEnvironment, setExecutionTargetEnvironment] = useState("");
	const [executionPreview, setExecutionPreview] = useState(null);
	const [selectedExecutionCandidateIds, setSelectedExecutionCandidateIds] = useState([]);
	const [executionRunResult, setExecutionRunResult] = useState(null);
	const [isPreviewingExecution, setIsPreviewingExecution] = useState(false);
	const [isRunningExecution, setIsRunningExecution] = useState(false);

	const resetExecutionWorkflowState = () => {
		setExecutionPreview(null);
		setSelectedExecutionCandidateIds([]);
		setExecutionRunResult(null);
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
		isPreviewingExecution,
		setIsPreviewingExecution,
		isRunningExecution,
		setIsRunningExecution,
		resetExecutionWorkflowState,
	};
}
