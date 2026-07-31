from ..base import ComponentKind, ProfileComponent

MULTIPLICITY_COMPONENTS = {
    item.component_id: item
    for item in (
        ProfileComponent(
            component_id="single_primary_hypothesis_v1",
            kind=ComponentKind.MULTIPLICITY,
            version="1",
            description="Exactly one preregistered primary hypothesis.",
            implementation_id="single_primary_hypothesis_v1",
        ),
        ProfileComponent(
            component_id="holm_family_v1",
            kind=ComponentKind.MULTIPLICITY,
            version="1",
            description="Frozen Holm correction over a named family.",
            implementation_id="holm_family_v1",
        ),
        ProfileComponent(
            component_id="hierarchical_gatekeeping_v1",
            kind=ComponentKind.MULTIPLICITY,
            version="1",
            description="Preregistered ordered hypothesis-family gates.",
            implementation_id="hierarchical_gatekeeping_v1",
        ),
    )
}
