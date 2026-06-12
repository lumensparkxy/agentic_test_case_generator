import { useState } from "react";

export default function useContextWorkflowState() {
	const [appLink, setAppLink] = useState("");
	const [prototypeLink, setPrototypeLink] = useState("");
	const [diagramLinks, setDiagramLinks] = useState("");
	const [imageLinks, setImageLinks] = useState("");
	const [enrichedContext, setEnrichedContext] = useState(null);
	const [selectedArtifactSourceIds, setSelectedArtifactSourceIds] = useState([]);
	const [isAnalyzingContext, setIsAnalyzingContext] = useState(false);

	const resetContextAnalysis = () => {
		setEnrichedContext(null);
		setSelectedArtifactSourceIds([]);
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
		enrichedContext,
		setEnrichedContext,
		selectedArtifactSourceIds,
		setSelectedArtifactSourceIds,
		isAnalyzingContext,
		setIsAnalyzingContext,
		resetContextAnalysis,
	};
}
