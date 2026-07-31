from ..base import ComponentKind, ProfileComponent

DESIGN_COMPONENTS = {
    item.component_id: item
    for item in (
        ProfileComponent(
            component_id="paired_two_arm_v1",
            kind=ComponentKind.DESIGN,
            version="1",
            description="Two arms evaluated on the same registered units.",
            implementation_id="paired_matrix_compiler_v1",
        ),
        ProfileComponent(
            component_id="unpaired_two_group_v1",
            kind=ComponentKind.DESIGN,
            version="1",
            description="Two non-overlapping groups without pair identity.",
            implementation_id="unpaired_matrix_compiler_v1",
        ),
        ProfileComponent(
            component_id="paired_multi_arm_v1",
            kind=ComponentKind.DESIGN,
            version="1",
            description="Registered contrasts over paired multi-arm cells.",
            implementation_id="paired_multi_arm_compiler_v1",
        ),
    )
}
