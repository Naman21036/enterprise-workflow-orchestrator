import pytest
from backend.app.orchestration.router import WorkflowRouter
from backend.app.discovery.actions import AgentAction, DiscoveryStepResult

def test_extract_member_id_from_goal():
    router = WorkflowRouter(None)
    
    assert router.extract_member_id_from_goal("Find member 1002 and retrieve balance") == "1002"
    assert router.extract_member_id_from_goal("Lookup member 12345 profile") == "12345"
    assert router.extract_member_id_from_goal("Look up member 99999", {"member_id": "99999"}) == "99999"
    assert router.extract_member_id_from_goal("Find a member and retrieve the savings balance") is None
    assert router.extract_member_id_from_goal("Find member 1002", {"member_id": "abc"}) is None


@pytest.mark.asyncio
async def test_missing_member_id_is_not_silently_replaced_with_a_demo_member():
    result = await WorkflowRouter(None).execute_goal("Find a member and retrieve the savings balance")
    assert result["status"] == "FAILED"
    assert result["error_code"] == "MEMBER_ID_REQUIRED"
    assert result["llm_decision_calls"] == 0


def test_recording_action_count_excludes_observation_and_unexecuted_decisions():
    observation_only = DiscoveryStepResult(
        step_number=1,
        action=AgentAction(action_type="escalate", reason="Provider unavailable"),
        observation_summary="Observed the live search page",
        status="FAILED",
        observation_before={"url": "http://localhost:3001/", "interactive_elements": []},
        ui_action_executed=False,
    )
    executed = DiscoveryStepResult(
        step_number=2,
        action=AgentAction(action_type="click", selector="#search-btn"),
        observation_summary="Clicked the search button",
        status="SUCCESS",
        action_result={"status": "SUCCESS"},
        ui_action_executed=True,
    )
    actions = WorkflowRouter._recorded_actions([observation_only, executed])
    assert len(actions) == 1
    assert actions[0]["action_type"] == "click"
