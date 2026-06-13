from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Type
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from ..models import (
    AutomationInput,
    AutomationResponse,
    ExecutionPreviewInput,
    ExecutionRunInput,
    GenerateTestCasesInput,
    GenerateTestCasesResponse,
    JiraExportInput,
    RefineTestCasesInput,
)
from .specialist_contracts import (
    SPECIALIST_AGENT_CONTRACT_VERSION,
    AutomationTaskInput,
    AutomationTaskOutput,
    ExecutionTaskInput,
    ExecutionTaskOutput,
    ImpactTaskInput,
    ImpactTaskOutput,
    ReportTaskInput,
    ReportTaskOutput,
    RequirementTaskInput,
    RequirementTaskOutput,
    ReviewTaskInput,
    ReviewTaskOutput,
    SpecialistAgentImplementation,
    SpecialistAgentKind,
    SpecialistDiagnostic,
    SpecialistTaskResult,
    SpecialistTaskTrace,
    TestCaseTaskInput,
    TestCaseTaskOutput,
    UseCaseTaskInput,
    UseCaseTaskOutput,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SpecialistAgentAdapter:
    agent_kind: SpecialistAgentKind
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]
    handler: Callable[[BaseModel, SpecialistTaskTrace], BaseModel | dict[str, Any]]
    implementation: SpecialistAgentImplementation = "local"
    contract_version: str = SPECIALIST_AGENT_CONTRACT_VERSION


class SpecialistAgentRegistry:
    def __init__(self) -> None:
        self._adapters: Dict[SpecialistAgentKind, SpecialistAgentAdapter] = {}

    def register(self, adapter: SpecialistAgentAdapter) -> None:
        self._adapters[adapter.agent_kind] = adapter

    def get(self, agent_kind: SpecialistAgentKind) -> SpecialistAgentAdapter:
        return self._adapters[agent_kind]

    def manifest(self) -> list[dict[str, str]]:
        return [
            {
                "agent_kind": adapter.agent_kind,
                "implementation": adapter.implementation,
                "contract_version": adapter.contract_version,
                "input_model": adapter.input_model.__name__,
                "output_model": adapter.output_model.__name__,
            }
            for adapter in self._adapters.values()
        ]

    def dispatch(
        self,
        agent_kind: SpecialistAgentKind,
        payload: BaseModel | dict[str, Any],
        trace: SpecialistTaskTrace,
        *,
        task_id: str | None = None,
    ) -> SpecialistTaskResult:
        started_at = _utcnow()
        task_id = task_id or f"{agent_kind}-{uuid4().hex[:12]}"
        try:
            adapter = self.get(agent_kind)
        except KeyError:
            return self._failed_result(
                agent_kind=agent_kind,
                implementation="local",
                contract_version=SPECIALIST_AGENT_CONTRACT_VERSION,
                trace=trace,
                task_id=task_id,
                started_at=started_at,
                diagnostic=SpecialistDiagnostic(
                    code="agent_not_registered",
                    message=f"No specialist agent adapter is registered for {agent_kind}.",
                    retryable=False,
                ),
            )

        try:
            task_input = payload if isinstance(payload, adapter.input_model) else adapter.input_model.model_validate(payload)
        except ValidationError as exc:
            return self._failed_result(
                agent_kind=agent_kind,
                implementation=adapter.implementation,
                contract_version=adapter.contract_version,
                trace=trace,
                task_id=task_id,
                started_at=started_at,
                diagnostic=SpecialistDiagnostic(
                    code="agent_input_validation_failed",
                    message=f"{adapter.input_model.__name__} validation failed.",
                    retryable=False,
                    details={"errors": exc.errors(include_url=False, include_input=False)},
                ),
            )

        try:
            raw_output = adapter.handler(task_input, trace)
            output = raw_output if isinstance(raw_output, adapter.output_model) else adapter.output_model.model_validate(raw_output)
        except TimeoutError as exc:
            return self._failed_result(
                agent_kind=agent_kind,
                implementation=adapter.implementation,
                contract_version=adapter.contract_version,
                trace=trace,
                task_id=task_id,
                started_at=started_at,
                diagnostic=SpecialistDiagnostic(
                    code="agent_timeout",
                    message=str(exc) or f"{agent_kind} task timed out.",
                    retryable=True,
                ),
            )
        except ValidationError as exc:
            return self._failed_result(
                agent_kind=agent_kind,
                implementation=adapter.implementation,
                contract_version=adapter.contract_version,
                trace=trace,
                task_id=task_id,
                started_at=started_at,
                diagnostic=SpecialistDiagnostic(
                    code="agent_output_validation_failed",
                    message=f"{adapter.output_model.__name__} validation failed.",
                    retryable=False,
                    details={"errors": exc.errors(include_url=False, include_input=False)},
                ),
            )
        except Exception as exc:
            return self._failed_result(
                agent_kind=agent_kind,
                implementation=adapter.implementation,
                contract_version=adapter.contract_version,
                trace=trace,
                task_id=task_id,
                started_at=started_at,
                diagnostic=SpecialistDiagnostic(
                    code="agent_execution_failed",
                    message=str(exc) or f"{agent_kind} task failed.",
                    retryable=False,
                ),
            )

        output_payload = output.model_dump(mode="json")
        output_artifact_refs = list(getattr(output, "output_artifact_refs", []) or [])
        return SpecialistTaskResult(
            task_id=task_id,
            agent_kind=agent_kind,
            implementation=adapter.implementation,
            contract_version=adapter.contract_version,
            status="completed",
            trace=trace,
            output_type=adapter.output_model.__name__,
            output_payload=output_payload,
            output_artifact_refs=output_artifact_refs,
            diagnostics=[],
            started_at=started_at,
            completed_at=_utcnow(),
        )

    def _failed_result(
        self,
        *,
        agent_kind: SpecialistAgentKind,
        implementation: SpecialistAgentImplementation,
        contract_version: str,
        trace: SpecialistTaskTrace,
        task_id: str,
        started_at: datetime,
        diagnostic: SpecialistDiagnostic,
    ) -> SpecialistTaskResult:
        return SpecialistTaskResult(
            task_id=task_id,
            agent_kind=agent_kind,
            implementation=implementation,
            contract_version=contract_version,
            status="failed",
            trace=trace,
            diagnostics=[diagnostic],
            started_at=started_at,
            completed_at=_utcnow(),
        )


def _run_requirement_task(task_input: BaseModel, trace: SpecialistTaskTrace) -> RequirementTaskOutput:
    from . import requirements_agent

    payload = RequirementTaskInput.model_validate(task_input)
    operation = "requirements.refine" if payload.feedback and payload.existing_requirements else "requirements.parse"
    if operation == "requirements.refine":
        workflow = requirements_agent.refine_requirements(
            [item.model_dump(mode="json") for item in payload.existing_requirements],
            payload.feedback or "",
            workflow_settings=payload.workflow_settings,
            actor_user_id=trace.actor_user_id,
            request_id=trace.request_id,
            workflow_run_id=trace.workflow_run_id,
            operation=operation,
        )
    else:
        workflow = requirements_agent.extract_requirements(
            payload.text or "",
            document_count=payload.document_count,
            workflow_settings=payload.workflow_settings,
            actor_user_id=trace.actor_user_id,
            request_id=trace.request_id,
            workflow_run_id=trace.workflow_run_id,
            operation=operation,
        )
    return RequirementTaskOutput.model_validate(workflow)


def _run_use_case_task(task_input: BaseModel, trace: SpecialistTaskTrace) -> UseCaseTaskOutput:
    from . import test_case_agent

    payload = UseCaseTaskInput.model_validate(task_input)
    workflow = test_case_agent.generate_test_cases(
        GenerateTestCasesInput(
            requirements=payload.requirements,
            template=payload.template,
            context=payload.context,
            feedback=payload.feedback,
            workflow_settings=payload.workflow_settings,
        ),
        actor_user_id=trace.actor_user_id,
        request_id=trace.request_id,
        workflow_run_id=trace.workflow_run_id,
        operation="orchestrator.use_cases.generate",
    )
    response = GenerateTestCasesResponse.model_validate(workflow)
    return UseCaseTaskOutput(
        requirement_analysis=response.requirement_analysis,
        coverage_plan=response.coverage_plan,
        approved=response.approved,
        review=response.review,
        coverage_metrics=response.coverage_metrics,
        workflow_settings=response.workflow_settings,
        workflow_diagnostics=response.workflow_diagnostics,
    )


def _run_impact_task(task_input: BaseModel, _trace: SpecialistTaskTrace) -> ImpactTaskOutput:
    from . import impact_update_agent

    payload = ImpactTaskInput.model_validate(task_input)
    return ImpactTaskOutput(
        analysis=impact_update_agent.analyze_impact(
            current_requirements_snapshot=payload.current_requirements_snapshot,
            current_use_cases_snapshot=payload.current_use_cases_snapshot,
            current_context_snapshot=payload.current_context_snapshot,
            baseline_requirements_snapshot=payload.baseline_requirements_snapshot,
            baseline_use_cases_snapshot=payload.baseline_use_cases_snapshot,
            baseline_context_snapshot=payload.baseline_context_snapshot,
            test_cases_snapshot=payload.test_cases_snapshot,
        )
    )


def _run_test_case_task(task_input: BaseModel, trace: SpecialistTaskTrace) -> TestCaseTaskOutput:
    from . import test_case_agent

    payload = TestCaseTaskInput.model_validate(task_input)
    if payload.feedback and payload.existing_test_cases:
        workflow = test_case_agent.refine_test_cases(
            RefineTestCasesInput(
                requirements=payload.requirements,
                test_cases=payload.existing_test_cases,
                template=payload.template,
                context=payload.context,
                feedback=payload.feedback,
                workflow_settings=payload.workflow_settings,
            ),
            actor_user_id=trace.actor_user_id,
            request_id=trace.request_id,
            workflow_run_id=trace.workflow_run_id,
            operation="orchestrator.test_cases.refine",
        )
    else:
        workflow = test_case_agent.generate_test_cases(
            GenerateTestCasesInput(
                requirements=payload.requirements,
                template=payload.template,
                context=payload.context,
                feedback=payload.feedback,
                workflow_settings=payload.workflow_settings,
            ),
            actor_user_id=trace.actor_user_id,
            request_id=trace.request_id,
            workflow_run_id=trace.workflow_run_id,
            operation="orchestrator.test_cases.generate",
        )
    response = GenerateTestCasesResponse.model_validate(workflow)
    return TestCaseTaskOutput(
        test_cases=response.test_cases,
        approved=response.approved,
        review=response.review,
        iteration_history=response.iteration_history,
        coverage_plan=response.coverage_plan,
        requirement_analysis=response.requirement_analysis,
        coverage_metrics=response.coverage_metrics,
        workflow_settings=response.workflow_settings,
        workflow_diagnostics=response.workflow_diagnostics,
    )


def _run_automation_task(task_input: BaseModel, _trace: SpecialistTaskTrace) -> AutomationTaskOutput:
    from . import automation_agent

    payload = AutomationTaskInput.model_validate(task_input)
    return AutomationTaskOutput(
        automation=automation_agent.generate_playwright_pom(AutomationInput(test_cases=payload.test_cases, target_base_url=payload.target_base_url))
    )


def _run_execution_task(task_input: BaseModel, _trace: SpecialistTaskTrace) -> ExecutionTaskOutput:
    from ..services.execution_service import preview_execution, run_execution

    payload = ExecutionTaskInput.model_validate(task_input)
    if payload.mode == "run" and payload.run is not None:
        run_input = ExecutionRunInput.model_validate(payload.run)
        return ExecutionTaskOutput(
            mode="run",
            run=run_execution(
                run_input.test_cases,
                selected_test_case_ids=run_input.selected_test_case_ids,
                target_base_url=str(run_input.target_base_url) if run_input.target_base_url else None,
            ),
        )
    preview_input = ExecutionPreviewInput.model_validate(payload.preview)
    return ExecutionTaskOutput(
        mode="preview",
        preview=preview_execution(
            preview_input.test_cases,
            target_base_url=str(preview_input.target_base_url) if preview_input.target_base_url else None,
        ),
    )


def _run_review_task(task_input: BaseModel, _trace: SpecialistTaskTrace) -> ReviewTaskOutput:
    payload = ReviewTaskInput.model_validate(task_input)
    blockers = [*payload.review.blocking_issues, *payload.review.unmet_criteria]
    return ReviewTaskOutput(
        approved=payload.review.approved and not blockers,
        review=payload.review,
        blockers=blockers,
        traceability_ids=payload.traceability_ids,
    )


def _run_report_task(task_input: BaseModel, _trace: SpecialistTaskTrace) -> ReportTaskOutput:
    from . import export_agent

    payload = ReportTaskInput.model_validate(task_input)
    traceability_ids = sorted({trace_id for test_case in payload.test_cases for trace_id in [*test_case.linked_requirement_ids, *test_case.scenario_refs]})
    if payload.format == "json":
        content = export_agent.export_to_json(payload.test_cases)
        return ReportTaskOutput(format="json", content_length=len(content), evidence_refs=payload.evidence_refs, traceability_ids=traceability_ids)
    if payload.format == "csv":
        content = export_agent.export_to_csv(payload.test_cases)
        return ReportTaskOutput(format="csv", content_length=len(content), evidence_refs=payload.evidence_refs, traceability_ids=traceability_ids)
    if payload.format == "excel":
        content = export_agent.export_to_excel(payload.test_cases)
        return ReportTaskOutput(format="excel", content_length=len(content), evidence_refs=payload.evidence_refs, traceability_ids=traceability_ids)
    if payload.format == "jira":
        response = export_agent.export_to_jira(JiraExportInput(project_key="ORCH", issue_type="Test", test_cases=payload.test_cases))
        return ReportTaskOutput(
            format="jira",
            status="stubbed",
            message=response.message,
            evidence_refs=payload.evidence_refs,
            traceability_ids=traceability_ids,
        )
    return ReportTaskOutput(
        format="execution_summary",
        content_length=len(str(payload.execution_run)),
        message="Execution evidence summary generated.",
        evidence_refs=payload.evidence_refs,
        traceability_ids=traceability_ids,
    )


def build_default_agent_registry() -> SpecialistAgentRegistry:
    registry = SpecialistAgentRegistry()
    registry.register(SpecialistAgentAdapter("requirements", RequirementTaskInput, RequirementTaskOutput, _run_requirement_task))
    registry.register(SpecialistAgentAdapter("use_cases", UseCaseTaskInput, UseCaseTaskOutput, _run_use_case_task))
    registry.register(SpecialistAgentAdapter("impact", ImpactTaskInput, ImpactTaskOutput, _run_impact_task))
    registry.register(SpecialistAgentAdapter("test_cases", TestCaseTaskInput, TestCaseTaskOutput, _run_test_case_task))
    registry.register(SpecialistAgentAdapter("automation", AutomationTaskInput, AutomationTaskOutput, _run_automation_task))
    registry.register(SpecialistAgentAdapter("execution", ExecutionTaskInput, ExecutionTaskOutput, _run_execution_task))
    registry.register(SpecialistAgentAdapter("review", ReviewTaskInput, ReviewTaskOutput, _run_review_task))
    registry.register(SpecialistAgentAdapter("report", ReportTaskInput, ReportTaskOutput, _run_report_task))
    return registry


_DEFAULT_REGISTRY: SpecialistAgentRegistry | None = None


def get_default_agent_registry() -> SpecialistAgentRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = build_default_agent_registry()
    return _DEFAULT_REGISTRY


ACTION_AGENT_KIND_MAP: dict[str, SpecialistAgentKind] = {
    "refine": "requirements",
    "approve": "review",
    "generate": "test_cases",
    "analyze_impact": "impact",
    "apply_update": "impact",
    "full_regenerate": "test_cases",
    "automate": "automation",
    "execute": "execution",
    "review": "review",
    "report": "report",
}


def agent_contract_metadata_for_action(action: str) -> dict[str, str]:
    agent_kind = ACTION_AGENT_KIND_MAP.get(action)
    if not agent_kind:
        return {}
    adapter = get_default_agent_registry().get(agent_kind)
    return {
        "agent_kind": adapter.agent_kind,
        "agent_contract_version": adapter.contract_version,
        "agent_implementation": adapter.implementation,
    }
