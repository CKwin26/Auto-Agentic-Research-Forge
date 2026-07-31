from ..base import ComponentKind, ProfileComponent

INFERENCE_COMPONENTS = {
    item.component_id: item
    for item in (
        ProfileComponent(
            component_id="legacy_paired_interval_v1",
            kind=ComponentKind.INFERENCE,
            version="1",
            description="Frozen historical Profile v1 interval semantics.",
            implementation_id="legacy_paired_interval_v1",
        ),
        ProfileComponent(
            component_id="cluster_bootstrap_continuous_v1",
            kind=ComponentKind.INFERENCE,
            version="1",
            description="Bootstrap over preregistered variance clusters.",
            implementation_id="cluster_bootstrap_continuous_v1",
            configuration_schema={
                "resamples": "integer>=1000",
                "seed": "integer",
                "resample_unit": "registered cluster field",
            },
        ),
        ProfileComponent(
            component_id="exact_mcnemar_v1",
            kind=ComponentKind.INFERENCE,
            version="1",
            description="Exact binomial inference over independent discordant pairs.",
            implementation_id="exact_mcnemar_v1",
        ),
        ProfileComponent(
            component_id="cluster_bootstrap_paired_binary_v1",
            kind=ComponentKind.INFERENCE,
            version="1",
            description="Cluster bootstrap for correlated paired binary units.",
            implementation_id="cluster_bootstrap_paired_binary_v1",
            configuration_schema={
                "resamples": "integer>=1000",
                "seed": "integer",
                "resample_unit": "cluster_id",
            },
        ),
        ProfileComponent(
            component_id="welch_interval_v1",
            kind=ComponentKind.INFERENCE,
            version="1",
            description="Frozen Welch two-group interval.",
            implementation_id="welch_interval_v1",
        ),
    )
}
