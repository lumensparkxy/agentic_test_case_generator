import { Link } from "../ui/controls";
import { requirementSources } from "../../utils/requirementSources";

export default function RequirementSourceEvidence({ requirement }) {
	return requirementSources(requirement).map((source, index) => (
		<div className="requirement-source-evidence" key={`${source.source_id || "legacy"}-${source.source_version || "unknown"}-${index}`}>
			<strong>{source.label}</strong>
			{source.source_issue_key && <p>External issue: {source.source_issue_key}</p>}
			<p>
				Original IDs:{" "}
				{source.excerpt_verified === true && source.original_requirement_ids?.length
					? source.original_requirement_ids.join(", ")
					: "Unavailable"}
			</p>
			{source.source_section && <p>{source.source_section}</p>}
			{source.source_version && (
				<p title={source.source_version}>
					Source version: <code>{source.source_version.slice(0, 12)}</code>
				</p>
			)}
			{source.source_issue_url && (
				<Link href={source.source_issue_url} target="_blank" rel="noreferrer">
					Open source ↗
				</Link>
			)}
			{source.excerpt_verified === true && source.excerpt ? (
				<blockquote>{source.excerpt}</blockquote>
			) : (
				<p>
					Source excerpt unavailable.{" "}
					{source.source_system === "file"
						? "Reimport the document with a supporting quotation."
						: "A verified source quotation has not been recorded."}
				</p>
			)}
		</div>
	));
}
