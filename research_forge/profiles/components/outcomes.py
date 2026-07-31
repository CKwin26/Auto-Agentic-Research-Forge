from ..base import ComponentKind, ProfileComponent

OUTCOME_COMPONENTS = {
    item.component_id: item
    for item in (
        ProfileComponent(
            component_id="continuous_outcome_v1",
            kind=ComponentKind.OUTCOME,
            version="1",
            description="Finite numeric outcome with a frozen direction.",
            implementation_id="continuous_analysis_table_v1",
        ),
        ProfileComponent(
            component_id="binary_outcome_v1",
            kind=ComponentKind.OUTCOME,
            version="1",
            description="Sample-level success/failure outcome encoded as 0/1.",
            implementation_id="binary_analysis_table_v1",
        ),
    )
}
