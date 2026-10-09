"""Job history, step trace, manual guide, and the parity features (gateways, SLA timers, BPMN 2.0 export)."""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from production_trace import read_history
from production_trace.cli import main as trace_main
from workflow_ir import compile_workflow, route_workflow, validate_request

from workflow_production import jobs as jobs_module
from workflow_production.bpmn import build_bpmn_model, render_bpmn
from workflow_production.cli import main as cli_main
from workflow_production.provider import STEP_KINDS
from workflow_production.qa import run_bpmn_qa
from workflow_production.render import build_execution_plan, render_execution_plan

CANARY = "CNRY-workflow-trace-91c2"
BPMN = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
BPMNDI = "{http://www.omg.org/spec/BPMN/20100524/DI}"


def _workflow(**overrides: Any) -> dict[str, Any]:
    workflow: dict[str, Any] = {
        "workflow_id": "expense-01",
        "name": "Expense approval",
        "fields": [
            {"field_id": "employee", "label": "Employee name", "field_type": "text", "required": True},
            {"field_id": "amount", "label": "Amount (THB)", "field_type": "decimal", "required": True},
            {"field_id": "urgent", "label": "Urgent?", "field_type": "boolean", "required": False},
            {"field_id": "spent_on", "label": "Date spent", "field_type": "date", "required": True},
        ],
        "form_values": {"employee": f"Ada {CANARY}", "amount": 2500.5, "urgent": True, "spent_on": "2026-09-30"},
        "steps": [
            {"step_id": "collect", "kind": "collect_form", "role": "requester"},
            {"step_id": "check", "kind": "validate", "depends_on": ["collect"], "retry": {"max_attempts": 3}},
            {
                "step_id": "route", "kind": "decision", "depends_on": ["check"],
                "branches": [{"target": "finance_review", "field_id": "amount", "operator": "gt", "value": 1000}],
                "default_target": "manager_review",
            },
            {"step_id": "finance_review", "kind": "human_review", "depends_on": ["route"], "role": "finance", "timeout_minutes": 60, "escalate_to": "manager"},
            {"step_id": "manager_review", "kind": "human_review", "depends_on": ["route"], "role": "manager", "timeout_minutes": 1440},
            {"step_id": "render", "kind": "render_document", "depends_on": ["finance_review", "manager_review"], "retry": {"max_attempts": 2}},
        ],
        "document": {
            "title": "Expense <summary> & approval",
            "sections": [
                {"kind": "heading", "text": "Claim"},
                {"kind": "field", "field_id": "employee"},
                {"kind": "field", "field_id": "amount"},
                {"kind": "paragraph", "text": "Approved per policy."},
            ],
        },
        "permissions": {"allowed_roles": ["requester", "finance", "manager"], "approver_role": "manager"},
    }
    workflow.update(overrides)
    return workflow


def _steps(**changes: dict[str, Any]) -> list[dict[str, Any]]:
    steps = copy.deepcopy(_workflow()["steps"])
    for step in steps:
        step.update(changes.get(step["step_id"], {}))
    return steps


def _trace(root: Path, job_id: str) -> dict[str, Any]:
    return json.loads((root / "jobs" / job_id / "trace.json").read_text(encoding="utf-8"))


# --- trace + history -------------------------------------------------------------------------


def test_accepted_job_writes_trace_ir_manual_history_and_bpmn(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = jobs_module.run_workflow_job(_workflow(), out, "key-1")

    assert result.status == "accepted", result.error_code
    assert {item.media_type for item in result.artifacts} == {"application/json", "text/markdown", "application/xml"}
    job_id = result.metadata["trace_job_id"]
    job_dir = out / "jobs" / job_id
    assert {"trace.json", "ir.json", "MANUAL-STEPS.md"} <= {path.name for path in job_dir.iterdir()}
    trace = _trace(out, job_id)
    assert trace["tool_id"] == "workflow-v1a" and trace["status"] == "accepted"
    assert trace["ir"]["schema"] == "workflow-ir.v1"
    assert [step["operation"] for step in trace["steps"]] == [
        "validate_request", "compile_workflow", "route_decisions", "render_execution_plan", "render_document",
        "render_bpmn", "write_package", "run_workflow_qa", "write_manifest",
    ]
    assert trace["steps"][2]["outputs"] == ["route->finance_review"]
    assert {item["sha256"] for item in trace["artifacts"]} == {item.content_hash for item in result.artifacts}
    assert trace["manual_guide"]["program"] == "Camunda Modeler"
    assert [(entry["job_id"], entry["status"]) for entry in read_history(out)] == [(job_id, "accepted")]


def test_replay_verifies_three_artifacts_and_adds_no_history(tmp_path: Path) -> None:
    out = tmp_path / "out"
    first = jobs_module.run_workflow_job(_workflow(), out, "key-1")
    replay = jobs_module.run_workflow_job(_workflow(), out, "key-1")

    assert replay.replayed is True and replay.artifacts == first.artifacts
    assert replay.metadata["trace_job_id"] == first.metadata["trace_job_id"]
    assert len(read_history(out)) == 1


def test_tampered_bpmn_is_rejected_on_replay_and_by_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"
    result = jobs_module.run_workflow_job(_workflow(), out, "key-1")
    job_id = result.metadata["trace_job_id"]
    assert trace_main(["verify", "--root", str(out), job_id]) == 0

    bpmn = out / next(item.filename for item in result.artifacts if item.media_type == "application/xml")
    bpmn.write_text(bpmn.read_text(encoding="utf-8").replace("PT60M", "PT6M"), encoding="utf-8")
    capsys.readouterr()
    assert trace_main(["verify", "--root", str(out), job_id]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    with pytest.raises(jobs_module.ReplayIntegrityError):
        jobs_module.run_workflow_job(_workflow(), out, "key-1")


def test_trace_and_history_hold_no_private_values(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = jobs_module.run_workflow_job(_workflow(), out, "key-1")
    job_dir = out / "jobs" / result.metadata["trace_job_id"]

    assert CANARY not in (job_dir / "trace.json").read_text(encoding="utf-8")
    assert CANARY not in (out / "history.jsonl").read_text(encoding="utf-8")
    assert CANARY in (job_dir / "MANUAL-STEPS.md").read_text(encoding="utf-8")


def test_rejected_and_schema_errors_are_traced_without_input(tmp_path: Path) -> None:
    out = tmp_path / "out"
    rejected = jobs_module.run_workflow_job(_workflow(steps=_steps(collect={"depends_on": ["render"]})), out, "key-cycle")
    schema = jobs_module.run_workflow_job(_workflow(workflow_id=None, name=CANARY), out, "key-schema")

    assert rejected.status == "rejected" and rejected.error_code == "workflow_cycle"
    assert schema.status == "rejected"
    history = read_history(out)
    assert [entry["status"] for entry in history] == ["rejected", "rejected"]
    assert _trace(out, history[0]["job_id"])["steps"][-1]["note"] == "workflow_cycle"
    assert CANARY not in (out / history[1]["trace"]).read_text(encoding="utf-8")


def test_engine_crash_is_traced_and_still_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out"

    def boom(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("bpmn exploded")

    monkeypatch.setattr(jobs_module, "render_bpmn", boom)
    with pytest.raises(RuntimeError, match="bpmn exploded"):
        jobs_module.run_workflow_job(_workflow(), out, "key-crash")
    [entry] = read_history(out)
    trace = _trace(out, entry["job_id"])
    assert entry["status"] == "failed"
    assert trace["steps"][-2]["operation"] == "render_bpmn" and trace["steps"][-2]["status"] == "failed"
    assert not list(out.glob("*.bpmn"))


def test_qa_failure_is_traced_and_files_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out"
    monkeypatch.setattr(jobs_module, "run_bpmn_qa", lambda *args: ["bpmn_shape_missing"])

    result = jobs_module.run_workflow_job(_workflow(), out, "key-qa")

    assert result.status == "rejected" and result.error_code == "bpmn_shape_missing"
    assert not list(out.glob("*.bpmn")) and not list(out.glob("*.execution.json"))
    assert read_history(out)[0]["status"] == "rejected"


def test_cli_build_records_ai_proposal_metadata(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"
    proposal = {"adapter": "workflow-provider-gateway", "mode": "live", "model_calls": 1, "provided": ["fields", "steps"], "inferred": ["name", CANARY]}
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps({"workflow": _workflow(), "proposal": proposal}), encoding="utf-8")

    assert cli_main(["build", "--input", str(path), "--output", str(out), "--idempotency-key", "cli-ai"]) == 0
    job_id = json.loads(capsys.readouterr().out)["metadata"]["trace_job_id"]
    trace = _trace(out, job_id)
    assert trace["ai"] == {"used": True, "model_calls": 1, "adapter": "workflow-provider-gateway", "mode": "live", "provided": ["fields", "steps"], "inferred": ["name"]}
    assert read_history(out)[0]["ai_used"] is True


# --- manual guide ----------------------------------------------------------------------------


def test_manual_guide_covers_every_field_step_role_edge_and_document_block(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = jobs_module.run_workflow_job(_workflow(), out, "key-1")
    manual = (out / "jobs" / result.metadata["trace_job_id"] / "MANUAL-STEPS.md").read_text(encoding="utf-8")
    ir = compile_workflow(_workflow())
    model = build_bpmn_model(ir)

    expected = [ir.name, ir.document.title, model.process_id, "Executable", "Problems"]
    for form_field in ir.fields:
        expected.append(form_field.label)
    expected += ["Ada " + CANARY, "2500.5", "2026-09-30", "Yes", "Is number", "Date"]
    for step in ir.steps:
        expected += [f"ID {step.step_id}", step.kind]
    for lane in model.lanes:
        expected.append(lane.name)
    for flow in model.flows:
        expected.append(f"ID {flow.id}")
        if flow.condition:
            expected.append(flow.condition)
    expected += ["Default flow", "PT60M", "PT1440M", "non-interrupting", "Retries 3", "Retries 2", "Candidate groups"]
    expected += ["route` ไปทาง `finance_review", "manager_review", "Join_render", "Inclusive gateway"]
    expected += [section.text for section in ir.document.sections if section.text]
    expected += ["Employee name: Ada", "Amount (THB): 2500.5", "Start and wait for an approval", "Count 2"]
    missing = [value for value in expected if value not in manual]
    assert missing == []
    assert "## 2. โครงสร้าง" in manual and "## 4. ตรวจผล" in manual and "เวลาโดยประมาณ" in manual


# --- gateways and SLA validation ------------------------------------------------------------


def test_route_selects_first_matching_branch_and_skips_the_rest() -> None:
    ir = compile_workflow(_workflow())
    route = route_workflow(ir)
    assert route.selected == {"route": "finance_review"}
    assert route.skipped == frozenset({"manager_review"})

    low = compile_workflow(_workflow(form_values={**_workflow()["form_values"], "amount": 120}))
    assert route_workflow(low).selected == {"route": "manager_review"}
    plan = build_execution_plan(low)
    statuses = {step["step_id"]: step["status"] for step in plan["steps"]}
    assert statuses == {"collect": "planned", "check": "planned", "route": "planned", "finance_review": "skipped", "manager_review": "planned", "render": "planned"}
    decision = next(step for step in plan["steps"] if step["kind"] == "decision")
    assert decision["selected_target"] == "manager_review" and decision["default_target"] == "manager_review"


@pytest.mark.parametrize(
    ("field_id", "operator", "value", "expected"),
    [
        ("spent_on", "lt", "2026-10-01", "finance_review"),
        ("spent_on", "gte", "2026-10-01", "manager_review"),
        ("urgent", "eq", True, "finance_review"),
        ("urgent", "ne", True, "manager_review"),
        ("employee", "eq", "nobody", "manager_review"),
    ],
)
def test_conditions_compare_by_field_type(field_id: str, operator: str, value: Any, expected: str) -> None:
    branch = {"target": "finance_review", "field_id": field_id, "operator": operator, "value": value}
    ir = compile_workflow(_workflow(steps=_steps(route={"branches": [branch]})))
    assert route_workflow(ir).selected["route"] == expected


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"route": {"default_target": None}}, "decision_default_required"),
        ({"route": {"branches": []}}, "decision_branches_required"),
        ({"route": {"default_target": "finance_review"}}, "decision_target_duplicate"),
        ({"route": {"branches": [{"target": "ghost", "field_id": "amount", "operator": "gt", "value": 1}]}}, "decision_target_missing"),
        ({"manager_review": {"depends_on": ["route", "check"]}}, "branch_target_dependencies_invalid"),
        ({"route": {"branches": [{"target": "finance_review", "field_id": "employee", "operator": "gt", "value": "a"}]}}, "decision_operator_invalid"),
        ({"route": {"branches": [{"target": "finance_review", "field_id": "amount", "operator": "gt", "value": "big"}]}}, "decision_value_type_invalid"),
        ({"route": {"branches": [{"target": "finance_review", "field_id": "ghost", "operator": "eq", "value": 1}]}}, "decision_field_missing"),
        ({"route": {"timeout_minutes": 5}}, "timeout_not_allowed"),
        ({"check": {"branches": [{"target": "route", "field_id": "amount", "operator": "gt", "value": 1}]}}, "branches_not_allowed"),
        ({"finance_review": {"timeout_minutes": None}}, "escalation_timeout_required"),
        ({"finance_review": {"escalate_to": "ceo"}}, "escalation_role_not_allowed"),
        ({"check": {"timeout_minutes": 5, "escalate_to": "manager"}}, "escalation_requires_review"),
    ],
)
def test_invalid_gateways_and_sla_are_rejected(changes: dict[str, dict[str, Any]], code: str) -> None:
    issues = validate_request(_workflow(steps=_steps(**changes)))
    assert code in {issue.code for issue in issues}


def test_decision_must_follow_collect_and_render_must_stay_reachable() -> None:
    steps = _steps()
    steps.insert(0, {"step_id": "early", "kind": "decision", "branches": [{"target": "early_a", "field_id": "amount", "operator": "gt", "value": 1}], "default_target": "early_b"})
    steps += [{"step_id": "early_a", "kind": "validate", "depends_on": ["early"]}, {"step_id": "early_b", "kind": "validate", "depends_on": ["early"]}]
    assert "decision_before_collect" in {issue.code for issue in validate_request(_workflow(steps=steps))}

    unreachable = _steps(render={"depends_on": ["manager_review"]})
    unreachable[3]["timeout_minutes"] = None
    unreachable[3]["escalate_to"] = None
    issues = {issue.code for issue in validate_request(_workflow(steps=unreachable))}
    assert "render_step_unreachable" in issues


def test_decision_child_must_be_a_branch() -> None:
    steps = _steps()
    steps.insert(3, {"step_id": "stray", "kind": "validate", "depends_on": ["route"]})
    assert "decision_child_not_branch" in {issue.code for issue in validate_request(_workflow(steps=steps))}


# --- BPMN export -----------------------------------------------------------------------------


def test_bpmn_export_is_valid_deterministic_and_matches_the_plan() -> None:
    ir = compile_workflow(_workflow())
    text = render_bpmn(ir)
    assert text == render_bpmn(ir)
    root = ET.fromstring(text)
    process = root.find(f"{BPMN}process")
    assert process is not None and process.get("isExecutable") == "false"
    types = {element.get("id"): element.tag.removeprefix(BPMN) for element in process}
    assert types["collect"] == "userTask" and types["check"] == "serviceTask" and types["route"] == "exclusiveGateway"
    assert types["Join_render"] == "inclusiveGateway" and types["Timeout_finance_review"] == "boundaryEvent"
    gateway = process.find(f"{BPMN}exclusiveGateway")
    assert gateway is not None and gateway.get("default") == "Flow_route__manager_review"
    conditions = [item.text for item in process.iter(f"{BPMN}conditionExpression")]
    assert conditions == ["=amount > 1000"]
    finance_timer = process.find(f"{BPMN}boundaryEvent[@attachedToRef='finance_review']")
    assert finance_timer is not None and finance_timer.get("cancelActivity") == "false"
    lanes = [lane.get("name") for lane in process.iter(f"{BPMN}lane")]
    assert lanes == ["requester", "system", "finance", "manager"]
    retries = {item.get("type"): item.get("retries") for item in process.iter("{http://camunda.org/schema/zeebe/1.0}taskDefinition")}
    assert retries == {"validate": "3", "render_document": "2"}
    node_ids = {element.get("id") for element in process if not element.tag.endswith(("sequenceFlow", "laneSet"))}
    shapes = {shape.get("bpmnElement") for shape in root.iter(f"{BPMNDI}BPMNShape")}
    assert node_ids <= shapes
    assert "Expense approval" in text and run_bpmn_qa(text, render_execution_plan(ir)) == []


def test_bpmn_escapes_names_and_qa_catches_tampering() -> None:
    ir = compile_workflow(_workflow(name='A & B <"x">'))
    text = render_bpmn(ir)
    assert 'name="A &amp; B &lt;&quot;x&quot;&gt;"' in text
    plan = render_execution_plan(ir)
    broken = text.replace('sourceRef="check"', 'sourceRef="ghost"')
    assert "bpmn_dependency_flow_missing" in run_bpmn_qa(broken, plan)
    assert run_bpmn_qa('<!DOCTYPE x [<!ENTITY a "b">]><x/>', plan) == ["bpmn_dtd_forbidden"]
    assert "bpmn_timer_mismatch" in run_bpmn_qa(text.replace("PT1440M", "PT1M"), plan)


def test_linear_workflow_still_exports_simple_bpmn() -> None:
    linear = _workflow(
        fields=[{"field_id": "customer", "label": "Customer", "field_type": "text"}],
        form_values={"customer": "Ada"},
        steps=[{"step_id": "collect", "kind": "collect_form"}, {"step_id": "render", "kind": "render_document", "depends_on": ["collect"]}],
        document={"title": "Doc", "sections": [{"kind": "field", "field_id": "customer"}]},
        permissions={"allowed_roles": ["ops"]},
    )
    ir = compile_workflow(linear)
    model = build_bpmn_model(ir)
    assert [node.id for node in model.nodes] == ["StartEvent_1", "collect", "render", "EndEvent_1"]
    assert [flow.id for flow in model.flows] == ["Flow_StartEvent_1__collect", "Flow_collect__render", "Flow_render__EndEvent_1"]


# --- V1B adapter stays fail-closed for the new step kind -------------------------------------


def test_adapter_does_not_propose_decisions_or_timers() -> None:
    assert "decision" not in STEP_KINDS
