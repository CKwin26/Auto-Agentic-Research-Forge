from ..base import ComponentKind, ProfileComponent

VERDICT_COMPONENTS = {
    item.component_id: item
    for item in (
        ProfileComponent(
            component_id="superiority_threshold_v1",
            kind=ComponentKind.VERDICT,
            version="1",
            description="Support/refute/inconclusive against a frozen directional threshold.",
            implementation_id="superiority_threshold_v1",
        ),
        ProfileComponent(
            component_id="superiority_with_safety_gate_v1",
            kind=ComponentKind.VERDICT,
            version="1",
            description="Primary superiority conditional on a safety/non-inferiority gate.",
            implementation_id="superiority_with_safety_gate_v1",
        ),
    )
}
