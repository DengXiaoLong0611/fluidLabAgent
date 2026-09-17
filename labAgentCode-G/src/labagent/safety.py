from dataclasses import dataclass

from .contracts import FanZone, FieldAction, FlapAxis


@dataclass
class SafetyDecision:
    allowed: bool
    reasons: list[str]


class SafetyPolicy:
    """Pure validation layer. It never sends hardware commands."""

    def validate(self, action: FieldAction, zones: list[FanZone], axes: list[FlapAxis],
                 previous: FieldAction | None = None) -> SafetyDecision:
        reasons: list[str] = []
        zone_map = {z.zone_id: z for z in zones}
        axis_map = {a.axis_id: a for a in axes}
        for zone_id, value in action.fan_commands.items():
            zone = zone_map.get(zone_id)
            if not zone:
                reasons.append(f"unknown fan zone: {zone_id}")
                continue
            if not zone.min_command <= value <= zone.max_command:
                reasons.append(f"fan zone {zone_id} outside command limits")
            if (
                previous
                and zone_id in previous.fan_commands
                and abs(value - previous.fan_commands[zone_id]) > zone.max_delta
            ):
                reasons.append(f"fan zone {zone_id} exceeds rate limit")
        for axis_id, angle in action.flap_angles_deg.items():
            axis = axis_map.get(axis_id)
            if not axis:
                reasons.append(f"unknown flap axis: {axis_id}")
            elif not axis.min_angle_deg <= angle <= axis.max_angle_deg:
                reasons.append(f"flap axis {axis_id} outside angle limits")
        return SafetyDecision(not reasons, reasons)
