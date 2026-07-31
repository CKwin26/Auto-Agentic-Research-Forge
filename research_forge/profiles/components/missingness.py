from ..base import ComponentKind, ProfileComponent

MISSINGNESS_COMPONENTS = {
    item.component_id: item
    for item in (
        ProfileComponent(
            component_id="legacy_inconclusive_or_disqualify_v1",
            kind=ComponentKind.MISSINGNESS,
            version="1",
            description="Historical Profile v1 missing-cell semantics.",
            implementation_id="legacy_missing_cell_v1",
        ),
        ProfileComponent(
            component_id="block_on_missing_pair_v1",
            kind=ComponentKind.MISSINGNESS,
            version="1",
            description="Any missing member of a registered pair blocks qualification.",
            implementation_id="block_on_missing_pair_v1",
        ),
        ProfileComponent(
            component_id="block_on_group_specific_missingness_v1",
            kind=ComponentKind.MISSINGNESS,
            version="1",
            description="Block asymmetric or unregistered group missingness.",
            implementation_id="block_group_missingness_v1",
        ),
    )
}
