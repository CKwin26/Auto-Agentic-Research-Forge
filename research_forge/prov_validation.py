"""External-engine semantic validation for Research Forge PROV exports.

PySHACL is independent from the Research Forge domain model.  The validator
checks a deliberately bounded Stage 3 PROV projection; it is not an official
W3C certification service and does not claim full PROV constraint coverage.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pyshacl
import rdflib
from pyshacl import validate as shacl_validate
from rdflib import Graph, Literal, Namespace, RDF, URIRef

from .reproduction_policy import canonical_sha256
from .storage import read_json, sha256_file, write_json_atomic


PROV = Namespace("http://www.w3.org/ns/prov#")
RF = Namespace("https://research-forge.local/ns#")
SH = Namespace("http://www.w3.org/ns/shacl#")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


_SHAPES = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix rf: <https://research-forge.local/ns#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

rf:FrozenArtifactShape a sh:NodeShape ;
  sh:targetClass prov:Entity ;
  sh:property [
    sh:path rf:sha256 ;
    sh:minCount 1 ;
    sh:maxCount 1 ;
    sh:datatype xsd:string ;
    sh:pattern "^[a-f0-9]{64}$"
  ] .

rf:Stage3ExecutionShape a sh:NodeShape ;
  sh:targetClass prov:Activity ;
  sh:property [
    sh:path prov:generated ;
    sh:minCount 1 ;
    sh:class prov:Entity
  ] .
"""


def _node(identifier: str) -> URIRef:
    if identifier.startswith("rf:"):
        return URIRef(str(RF) + identifier[3:])
    return URIRef(identifier)


def _project_to_rdf(payload: dict[str, Any]) -> tuple[Graph, list[str]]:
    graph = Graph()
    graph.bind("prov", PROV)
    graph.bind("rf", RF)
    violations: list[str] = []
    entities = dict(payload.get("entity") or {})
    activities = dict(payload.get("activity") or {})
    if payload.get("@context") != "https://www.w3.org/ns/prov.jsonld":
        violations.append("PROV_CONTEXT_MISSING_OR_CHANGED")
    if not entities:
        violations.append("PROV_ENTITY_SET_EMPTY")
    if not activities:
        violations.append("PROV_ACTIVITY_SET_EMPTY")

    for identifier, attributes in sorted(entities.items()):
        subject = _node(str(identifier))
        graph.add((subject, RDF.type, PROV.Entity))
        digest = str(dict(attributes).get("rf:sha256") or "")
        if not _SHA256.fullmatch(digest):
            violations.append(f"PROV_ENTITY_SHA256_INVALID:{identifier}")
        graph.add((subject, RF.sha256, Literal(digest)))
        if dict(attributes).get("prov:type") != "rf:FrozenArtifact":
            violations.append(f"PROV_ENTITY_TYPE_INVALID:{identifier}")

    for identifier, attributes in sorted(activities.items()):
        subject = _node(str(identifier))
        graph.add((subject, RDF.type, PROV.Activity))
        attrs = dict(attributes)
        if attrs.get("prov:type") != "rf:Stage3Execution":
            violations.append(f"PROV_ACTIVITY_TYPE_INVALID:{identifier}")
        generated = list(attrs.get("prov:generated") or [])
        if not generated:
            violations.append(f"PROV_ACTIVITY_GENERATED_EMPTY:{identifier}")
        for object_id in generated:
            object_text = str(object_id)
            if object_text not in entities:
                violations.append(
                    f"PROV_GENERATED_ENTITY_MISSING:{identifier}:{object_text}"
                )
            graph.add((subject, PROV.generated, _node(object_text)))
    return graph, sorted(set(violations))


def validate_prov_external(
    *,
    prov_path: str | Path,
    report_output: str | Path,
) -> dict[str, Any]:
    """Validate the bounded PROV projection with the external PySHACL engine."""

    source = Path(prov_path).resolve()
    output = Path(report_output).resolve()
    if output == source or source in output.parents:
        raise ValueError("validator evidence must be outside the PROV source")
    payload = read_json(source)
    data_graph, deterministic_violations = _project_to_rdf(payload)
    shapes = Graph().parse(data=_SHAPES, format="turtle")
    conforms, result_graph, _ = shacl_validate(
        data_graph,
        shacl_graph=shapes,
        inference="rdfs",
        abort_on_first=False,
        allow_infos=False,
        allow_warnings=False,
    )
    shacl_findings = []
    for result in result_graph.subjects(RDF.type, SH.ValidationResult):
        focus = next(result_graph.objects(result, SH.focusNode), None)
        path = next(result_graph.objects(result, SH.resultPath), None)
        message = next(result_graph.objects(result, SH.resultMessage), None)
        shacl_findings.append(
            {
                "focus_node": str(focus or ""),
                "result_path": str(path or ""),
                "message": str(message or ""),
            }
        )
    shacl_findings.sort(
        key=lambda item: (item["focus_node"], item["result_path"], item["message"])
    )
    report = {
        "schema_version": 1,
        "validator": "pyshacl",
        "validator_version": pyshacl.__version__,
        "rdf_engine": "rdflib",
        "rdf_engine_version": rdflib.__version__,
        "profile": "research-forge-stage3-prov-projection-v1",
        "source_sha256": sha256_file(source),
        "conforms": bool(conforms) and not deterministic_violations,
        "deterministic_violations": deterministic_violations,
        "shacl_findings": shacl_findings,
        "entity_count": len(dict(payload.get("entity") or {})),
        "activity_count": len(dict(payload.get("activity") or {})),
        "triple_count": len(data_graph),
        "claim_boundary": (
            "External SHACL-engine acceptance of the bounded Research Forge "
            "Stage 3 PROV projection; not official W3C certification or full "
            "PROV-CONSTRAINTS coverage."
        ),
    }
    report["report_subject_sha256"] = canonical_sha256(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output, report)
    return report


__all__ = ["validate_prov_external"]
