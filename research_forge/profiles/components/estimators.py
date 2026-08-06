from ..base import ComponentKind, ProfileComponent

ESTIMATOR_COMPONENTS = {
    item.component_id: item
    for item in (
        ProfileComponent(
            component_id="paired_mean_estimator_v1",
            kind=ComponentKind.ESTIMATOR,
            version="1",
            description="Arithmetic mean over frozen independent units.",
            implementation_id="paired_mean_estimator_v1",
        ),
        ProfileComponent(
            component_id="paired_proportion_difference_v1",
            kind=ComponentKind.ESTIMATOR,
            version="1",
            description="(n01 - n10) / N over registered paired units.",
            implementation_id="paired_binary_estimator_v1",
        ),
        ProfileComponent(
            component_id="welch_mean_difference_v1",
            kind=ComponentKind.ESTIMATOR,
            version="1",
            description="Unpaired difference in group means.",
            implementation_id="welch_mean_estimator_v1",
        ),
        ProfileComponent(
            component_id="equal_weight_top_k_v1",
            kind=ComponentKind.ESTIMATOR,
            version="0.1",
            description=(
                "Deterministic score ranking with asset-id tie breaking and "
                "equal weight over the frozen top-k set."
            ),
            implementation_id="equal_weight_top_k_v1",
        ),
        ProfileComponent(
            component_id="normalized_exact_match_v1",
            kind=ComponentKind.ESTIMATOR,
            version="0.1",
            description="NFKC, case-folded and whitespace-normalized exact match.",
            implementation_id="normalized_exact_match_v1",
        ),
    )
}
