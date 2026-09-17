from .contracts import FieldAction, Observation, TargetFieldSpec


def baseline_field_action(target: TargetFieldSpec, zone_ids: list[str], axis_ids: list[str]) -> FieldAction:
    """A transparent baseline; replace with MPC/BO/RL adapter after calibration."""
    command = min(1.0, target.mean_velocity / 20.0)
    return FieldAction(fan_commands={z: command for z in zone_ids},
                       flap_angles_deg={a: target.direction_deg for a in axis_ids},
                       hold_s=target.duration_s)


def reward(target: TargetFieldSpec, observation: Observation) -> dict[str, float]:
    velocity_error = abs(target.mean_velocity - observation.mean_velocity)
    turbulence_error = abs(target.turbulence_intensity - observation.turbulence_intensity)
    total = velocity_error + turbulence_error * target.mean_velocity
    return {"velocity_error": velocity_error, "turbulence_error": turbulence_error,
            "total_penalty": total}
