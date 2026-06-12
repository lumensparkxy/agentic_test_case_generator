import { useState } from "react";

export default function useWorkflowNavigationState() {
	const [activeTab, setActiveTab] = useState(0);

	return {
		activeTab,
		setActiveTab,
	};
}
