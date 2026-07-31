# Workflow Run RO-Crate external validation (2026-08-01)

## Outcome

Research Forge now exports a Stage 3 Workflow Run RO-Crate rather than only a
base RO-Crate description. The reusable fixture at
`examples/rocrate-validation/stage3-minimal` passed the maintained CRS4
`rocrate-validator` in offline mode under profile
`workflow-run-crate-0.5`.

The selected profile inherits Process Run Crate 0.5, Workflow RO-Crate 1.0,
and RO-Crate 1.1. The final validation passed all 24 required requirements and
all 55 required checks, with zero required issues.

## Exported structure

The Stage 3 exporter now includes:

- a root Dataset that names the Workflow Run, Process Run, and Workflow
  RO-Crate profiles;
- a `mainEntity` computational workflow descriptor;
- a machine-readable `workflow.json` frozen execution descriptor;
- flattened input and output FormalParameter entities;
- a CreateAction linking the workflow, input collection, output collection,
  and Research Forge agent;
- content-addressed artifact identifiers represented as flattened
  PropertyValue entities;
- the existing bounded PROV export and human-readable audit report.

The workflow descriptor records a frozen Stage 3 execution object. It cannot
create, change, or upgrade a scientific verdict.

## Defects found by the external checker

The first Workflow Run validation did not pass. It exposed two genuine
metadata defects that the earlier base-profile test did not detect:

1. the local `sha256` property was not declared by the compacted JSON-LD
   context;
2. inline PropertyValue objects violated the RO-Crate flattened-form
   requirement.

The exporter now uses Schema.org `identifier` references to separate,
flattened PropertyValue entities. The second validation passed.

## Frozen acceptance facts

- Checker: `crs4/rocrate-validator`
- Distribution: `roc-validator`
- Version output: `rocrate-validator 0.11.3_b01f630+0-dirty`
- Profile: `workflow-run-crate-0.5`
- Mode: offline, prewarmed context cache
- Exit code: 0
- Required requirements: 24/24 passed
- Required checks: 55/55 passed
- Required issues: 0
- Metadata SHA-256:
  `6ed7efb60c1da2852d63a589b713d58aa97916c43eaadfb299edbb691f529c32`
- Raw validator report SHA-256:
  `61635102abcd75fed04a619f8b6468d05d938bb57e38e474ffb5eadc2825cf5a`

The machine report is frozen outside the crate by
`validate_rocrate_external`, so validation evidence cannot modify the object
being checked.

## Claim boundary

Permitted claim: the reusable Research Forge Stage 3 fixture passes every
required check of Workflow Run Crate 0.5 and its inherited required profiles
under the maintained external validator.

Not claimed: Provenance Run Crate conformance, third-party certification,
scientific validity of a contained experiment, external reproduction, or
validation of every historical Research Forge package.
