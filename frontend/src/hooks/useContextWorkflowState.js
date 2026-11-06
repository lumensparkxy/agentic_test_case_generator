import { useState } from "react";

export default function useContextWorkflowState() {
	const [appLink, setAppLink] = useState("");
	const [prototypeLink, setPrototypeLink] = useState("");
	const [diagramLinks, setDiagramLinks] = useState("");
	const [imageLinks, setImageLinks] = useState("");
	const [contextNotes, setContextNotes] = useState("");
	const [enrichedContext, setEnrichedContext] = useState(null);
	const [selectedArtifactSourceIds, setSelectedArtifactSourceIds] = useState([]);
	const [isAnalyzingContext, setIsAnalyzingContext] = useState(false);
	const [contextAnalysisError, setContextAnalysisError] = useState("");

	const resetContextAnalysis = () => {
		setEnrichedContext(null);
		setSelectedArtifactSourceIds([]);
		setContextAnalysisError("");
	};

	return {
		appLink,
		setAppLink,
		prototypeLink,
		setPrototypeLink,
		diagramLinks,
		setDiagramLinks,
		imageLinks,
		setImageLinks,
		contextNotes,
		setContextNotes,
		enrichedContext,
		setEnrichedContext,
		selectedArtifactSourceIds,
		setSelectedArtifactSourceIds,
		isAnalyzingContext,
		setIsAnalyzingContext,
		contextAnalysisError,
		setContextAnalysisError,
		resetContextAnalysis,
	};
}
