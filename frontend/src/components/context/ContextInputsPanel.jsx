import QualityFeedbackPanel from "./QualityFeedbackPanel";
import ProjectSyncPanel, { ProjectSyncStatus } from "./ProjectSyncPanel";
import KnowledgePanel from "../knowledge/KnowledgePanel";
import { Surface, Disclosure } from "../ui/surfaces";
import { Field, Input, Textarea, Button, Checkbox } from "../ui/controls";
import { List, ListItem } from "../ui/collections";
export default function ContextInputsPanel({
	request,
	projectId,
	identity,
	prerequisiteSearch,
	executionFindingsLink,
	navigate,
	focusQualityFeedback,
	project,
	onProjectUpdated,
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
	contextDraft,
	contextDraftChanged = false,
	contextStale = false,
	contextSaveBlocked = false,
	embedded = false,
	hasContextInputs,
	analyzeContext,
	isAnalyzingContext,
	contextAnalysisError,
	authActionDisabled,
	enrichedContext,
	resetContextAnalysis,
	selectedArtifactSourceIds,
	setSelectedArtifactSourceIds,
	goPrev,
	goNext,
}) {
	return (
		<Surface as="section" className={embedded ? "panel project-intake-context" : "panel"}>
			<h2 className="panel-title">Context Inputs</h2>
			<p className="panel-description">Add notes, links, and references to enrich the test case generation context.</p>
			{contextDraft && (
				<>
					{contextDraft.error && <p role="alert">{contextDraft.error}</p>}
					{contextDraftChanged && (
						<p role="status">
							{contextDraft.error ? "Unsaved context in this tab." : "Unsaved context on this device."} Analyze Context to save it to the
							project.
						</p>
					)}
					{contextStale && <p role="status">Saved context is stale. Analyze Context against the current requirements.</p>}
					{contextSaveBlocked && <p>Finish or cancel the source comparison before saving context.</p>}
					{contextDraft.stale && (
						<Disclosure className="draft-recovery-notice">
							<summary>Context draft from an earlier snapshot</summary>
							<p>Compare these earlier inputs with the current values below.</p>
							<dl>
								{[
									["appLink", "Application link"],
									["prototypeLink", "Prototype link"],
									["diagramLinks", "Diagram links"],
									["imageLinks", "Image links"],
									["contextNotes", "Context notes"],
								].map(([field, label]) => (
									<div key={field}>
										<dt>{label}</dt>
										<dd>{contextDraft.stale.value?.[field] || "Empty"}</dd>
									</div>
								))}
							</dl>
							<Button variant="secondary" disabled={isAnalyzingContext} onClick={contextDraft.discardStale}>
								Discard earlier context draft
							</Button>
						</Disclosure>
					)}
				</>
			)}
			<div className="panel-form two-cols">
				<Field className="form-group">
					<label>Application link</label>
					<Input
						placeholder="https://your-app"
						value={appLink}
						onChange={(e) => setAppLink(e.target.value)}
						disabled={isAnalyzingContext}
					/>
				</Field>
				<Field className="form-group">
					<label>Prototype link</label>
					<Input
						placeholder="https://prototype"
						value={prototypeLink}
						onChange={(e) => setPrototypeLink(e.target.value)}
						disabled={isAnalyzingContext}
					/>
				</Field>
				<Field className="form-group">
					<label>Diagram links</label>
					<Input
						placeholder="Link1; Link2"
						value={diagramLinks}
						onChange={(e) => setDiagramLinks(e.target.value)}
						disabled={isAnalyzingContext}
					/>
				</Field>
				<Field className="form-group">
					<label>Image links</label>
					<Input
						placeholder="Link1; Link2"
						value={imageLinks}
						onChange={(e) => setImageLinks(e.target.value)}
						disabled={isAnalyzingContext}
					/>
				</Field>
			</div>
			<div className="context-notes">
				<Field
					label="Context notes"
					hint="Describe business rules, roles, test data, and assumptions. Notes can be saved without links. Select Analyze Context to save."
				>
					<Textarea rows={8} value={contextNotes} onChange={(e) => setContextNotes(e.target.value)} disabled={isAnalyzingContext} />
				</Field>
			</div>
			{hasContextInputs && (
				<div className="panel-form button-row">
					<Button onClick={analyzeContext} disabled={isAnalyzingContext || authActionDisabled || contextSaveBlocked}>
						{isAnalyzingContext ? "⏳ Analyzing..." : "Analyze Context"}
					</Button>
					{enrichedContext && !contextDraft && (
						<Button className="secondary" onClick={resetContextAnalysis} disabled={isAnalyzingContext}>
							Clear Analysis
						</Button>
					)}
				</div>
			)}
			{contextAnalysisError && <p role="alert">Context analysis failed: {contextAnalysisError}</p>}
			{enrichedContext?.grounded_context && (
				<Surface as="div" className="result-section">
					<h3>{contextDraft ? "Saved context evidence" : "Grounded Context"}</h3>
					{contextDraft && contextDraftChanged && <p>These results describe the last saved inputs.</p>}
					{(enrichedContext.grounded_context.artifact_sources || []).length > 0 && (
						<div className="artifact-sources">
							<h4>Artifact Sources</h4>
							<List className="artifact-source-list">
								{enrichedContext.grounded_context.artifact_sources.map((source) => (
									<ListItem key={source.id} className="artifact-source-item">
										<label>
											<Checkbox
												type="checkbox"
												disabled={isAnalyzingContext}
												checked={selectedArtifactSourceIds.includes(source.id)}
												onChange={(e) => {
													setSelectedArtifactSourceIds((prev) =>
														e.target.checked ? [...prev, source.id] : prev.filter((id) => id !== source.id)
													);
												}}
											/>
											<span>{source.url || source.id}</span>
											{contextDraft ? (
												<span className="artifact-type">
													{" · "}
													{source.source_type} · {contextStale ? "Stale" : source.status || "Provided"}
												</span>
											) : (
												source.type && <span className="artifact-type">{source.type}</span>
											)}
										</label>
										{contextDraft && source.notes && <p>{source.notes}</p>}
									</ListItem>
								))}
							</List>
						</div>
					)}
					<div className="analysis-detail-grid">
						{(enrichedContext.grounded_context.ui_elements || []).length > 0 && (
							<div className="analysis-detail-block">
								<h4>UI Elements</h4>
								<List className="analysis-detail-list">
									{enrichedContext.grounded_context.ui_elements.slice(0, 6).map((el) => (
										<ListItem key={el.id}>
											{el.element_type}: {el.name || el.label || el.id}
										</ListItem>
									))}
								</List>
							</div>
						)}
						{(enrichedContext.grounded_context.workflows || []).length > 0 && (
							<div className="analysis-detail-block">
								<h4>Workflows</h4>
								<List className="analysis-detail-list">
									{enrichedContext.grounded_context.workflows.slice(0, 4).map((workflow) => (
										<ListItem key={workflow.id}>
											{workflow.name}: {(workflow.transitions || []).join(", ") || workflow.description}
										</ListItem>
									))}
								</List>
							</div>
						)}
					</div>
				</Surface>
			)}
			{!executionFindingsLink && projectId && request && (
				<ProjectSyncStatus key={`sync-status-${projectId}`} project={project} request={request} onUpdated={onProjectUpdated} />
			)}
			{!executionFindingsLink && !contextDraft && projectId && project && request && (
				<ProjectSyncPanel key={`sync-${projectId}`} project={project} request={request} onUpdated={onProjectUpdated} />
			)}
			{!embedded && projectId && request && (
				<QualityFeedbackPanel
					key={JSON.stringify([identity, projectId, prerequisiteSearch, executionFindingsLink])}
					request={request}
					projectId={projectId}
					projectStatus={project?.status}
					prerequisiteSearch={prerequisiteSearch}
					executionFindingsLink={executionFindingsLink}
					navigate={navigate}
					focusRequested={focusQualityFeedback}
					onUpdated={onProjectUpdated}
				/>
			)}
			{!embedded && projectId && request && (
				<Disclosure className="context-knowledge-details">
					<summary>Project knowledge and diagnostics</summary>
					<KnowledgePanel key={projectId} request={request} projectId={projectId} />
				</Disclosure>
			)}
			{!embedded && (
				<div className="panel-nav">
					<Button onClick={goPrev} className="secondary">
						Back
					</Button>
					<Button onClick={goNext}>{contextDraft ? "Review scope" : "Next"}</Button>
				</div>
			)}
		</Surface>
	);
}
