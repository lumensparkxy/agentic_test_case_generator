# ADK skills and approved knowledge

Epic #269; stories #270–#274. Curated skills teach methods; approved knowledge stores product guidance. Current sources and explicit review decisions remain authoritative.

## Rollout
Both `ADK_SKILLS_ENABLED` and `ADK_MEMORY_ENABLED` default to false. `ADK_GUIDANCE_STAGES` independently limits rollout stages. No historic feedback is imported. Four-arm evaluation is required before enabling a stage in production.

## Run contract
One immutable manifest records model, skill versions/hashes, selected memory revisions, omissions and explicit bypass. Skill packages are loaded through ADK's loader before generation, without extra model tool calls. Parallel workers inherit the same manifest; JSON schemas and base guardrails are unchanged. Knowledge context is capped at 12 entries / 6000 characters in deterministic order.

## Delivery checklist
- #270: foundations and skill packages
- #271: durable repository, approvals and personal selections
- #272: shared management and provenance UI
- #273: generation and successful-feedback integration
- #274: validation, evaluation and rollout evidence
