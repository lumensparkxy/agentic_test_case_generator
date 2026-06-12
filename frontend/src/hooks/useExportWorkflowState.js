import { useState } from "react";

export default function useExportWorkflowState() {
	const [isExporting, setIsExporting] = useState(false);
	const [draftExportOverrideRequested, setDraftExportOverrideRequested] = useState(false);
	const [draftExportOverrideReason, setDraftExportOverrideReason] = useState("");

	const resetExportWorkflowState = () => {
		setDraftExportOverrideRequested(false);
		setDraftExportOverrideReason("");
	};

	return {
		isExporting,
		setIsExporting,
		draftExportOverrideRequested,
		setDraftExportOverrideRequested,
		draftExportOverrideReason,
		setDraftExportOverrideReason,
		resetExportWorkflowState,
	};
}
