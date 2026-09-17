from typing import Any

from .contracts import FanZone, FlapAxis, Scenario, TargetFieldSpec
from .controllers import baseline_field_action, reward
from .device_gateway import DeviceGateway, SimulatedDeviceGateway
from .safety import SafetyPolicy


def field_generation_run(parameters: dict[str, Any], gateway: DeviceGateway | None = None) -> dict[str, Any]:
    target = TargetFieldSpec.model_validate(parameters.get("target", parameters))
    zones = [FanZone(zone_id=z, fan_ids=[i]) for i, z in enumerate(parameters.get("zone_ids", ["zone-0"]))]
    axes = [FlapAxis(axis_id=a, min_angle_deg=-45, max_angle_deg=45, max_rate_deg_s=10)
            for a in parameters.get("axis_ids", [])]
    action = baseline_field_action(target, [z.zone_id for z in zones], [a.axis_id for a in axes])
    decision = SafetyPolicy().validate(action, zones, axes)
    if not decision.allowed:
        return {"status": "rejected", "reasons": decision.reasons}
    gateway = gateway or SimulatedDeviceGateway()
    operation = gateway.apply_field_action(action)
    observation = gateway.observe()
    return {"status": operation.status, "action": action.model_dump(),
            "observation": observation.model_dump(), "reward": reward(target, observation)}


def piv_run(parameters: dict[str, Any]) -> dict[str, Any]:
    return {"status": "planned", "adapter": "DaVisAdapter", "input": parameters,
            "next_tools": ["validate_images", "run_davis_or_rpa", "export_vectors", "quality_report"]}


def flow_control_run(parameters: dict[str, Any]) -> dict[str, Any]:
    return {"status": "planned", "controller": parameters.get("controller", "baseline_pid"),
            "input": parameters, "next_tools": ["measure_state", "compute_control", "apply_safe_action"]}


def run_scenario(scenario: Scenario, parameters: dict[str, Any]) -> dict[str, Any]:
    if scenario == Scenario.FIELD_GENERATION:
        return field_generation_run(parameters)
    if scenario == Scenario.PIV:
        return piv_run(parameters)
    return flow_control_run(parameters)
