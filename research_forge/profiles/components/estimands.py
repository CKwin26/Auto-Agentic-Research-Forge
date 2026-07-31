from ..base import ComponentKind, ProfileComponent

ESTIMAND_COMPONENTS = {
    item.component_id: item
    for item in (
        ProfileComponent(
            component_id="paired_mean_difference_v1",
            kind=ComponentKind.ESTIMAND,
            version="1",
            description="Mean treatment-minus-baseline difference.",
            implementation_id="paired_mean_estimand_v1",
        ),
        ProfileComponent(
            component_id="paired_risk_difference_v1",
            kind=ComponentKind.ESTIMAND,
            version="1",
            description="Treatment success probability minus baseline.",
            implementation_id="paired_risk_estimand_v1",
        ),
        ProfileComponent(
            component_id="unpaired_mean_difference_v1",
            kind=ComponentKind.ESTIMAND,
            version="1",
            description="Treatment group mean minus baseline group mean.",
            implementation_id="unpaired_mean_estimand_v1",
        ),
    )
}
