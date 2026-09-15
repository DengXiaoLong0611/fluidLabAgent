import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from labagent.contracts import Scenario
from labagent.graph import invoke


def test_field_generation_is_safe_and_observable():
    result = invoke(Scenario.FIELD_GENERATION, {
        "mean_velocity": 5, "direction_deg": 10, "turbulence_intensity": .2,
        "duration_s": 1, "tolerance": .5, "zone_ids": ["z1", "z2"], "axis_ids": ["flap-1"]
    })
    assert result["status"] == "completed"
    assert "observation" in result and "reward" in result


def test_piv_is_planned_not_fake_executed():
    result = invoke(Scenario.PIV, {"project": "demo.davis"})
    assert result["status"] == "planned"
    assert result["adapter"] == "DaVisAdapter"


def test_illegal_flap_action_is_rejected():
    result = invoke(Scenario.FIELD_GENERATION, {
        "mean_velocity": 5, "direction_deg": 90, "turbulence_intensity": .2,
        "duration_s": 1, "tolerance": .5, "axis_ids": ["flap-1"]
    })
    assert result["status"] == "rejected"
