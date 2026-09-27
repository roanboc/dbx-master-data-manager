# Decisions

_[← Repository README](../../README.md) · [Model home](../README.md)_

One file per decision, numbered chronologically, each explaining a single call
smaller than an initiative (see [scope documents](../scope/README.md)) but
consequential enough that a future reader will ask "why this and not the
alternative?" — most often an AI actor's autonomy level or decision rights (see
`architecture-document-style`'s actor notation in
[architecture/2_business/](../2_business/README.md)).

## Index

| #   | Decision | Status | Touches |
| --- | -------- | ------ | ------- |
| 1 | [The automated matcher acts in the automatic band](./1_automated-matcher-autonomy.md) | Accepted | [Automated matcher](../2_business/1_business-actors-and-roles.md#automated-matcher) |
| 2 | [The work router ranks and escalates, and decides nothing](./2_work-router-autonomy.md) | Accepted | [Work router](../2_business/1_business-actors-and-roles.md#work-router) |
| 3 | [The quality breaker may only reduce automation](./3_quality-breaker-autonomy.md) | Accepted | [Quality breaker](../2_business/1_business-actors-and-roles.md#quality-breaker) |
| 4 | [The AI assistant is advisory](./4_ai-assistant-autonomy.md) | Accepted | [AI assistant](../2_business/1_business-actors-and-roles.md#ai-assistant) |
| 5 | [The hub is written in Python, with a Dash interface and a Typer command line](./5_python-dash-and-typer.md) | Accepted | [Technology services](../5_technology/1_technology-services.md#technology-services) |
| 6 | [One SQL store runs on DuckDB and on Postgres](./6_one-sql-store-two-engines.md) | Accepted | [One store, two engines](../4_application/4_solution-design.md#one-store-two-engines) |
| 7 | [The tables sit in schema groups, and one published schema only the commit path writes](./7_schema-groups-and-one-published-schema.md) | Accepted | [Schema groups](../3_information/4_data-architecture.md#schema-groups) |
| 8 | [Each commit takes the commit-order lock and writes the change feed itself](./8_commit-order-lock-and-change-feed.md) | Accepted | [Listener interface](../4_application/5_interface-contracts.md#listener-interface) |
| 9 | [Source changes land in one shared table, read from a high-water mark that probes every gap again](./9_landing-table-and-watermark.md) | Accepted | [Landing interface](../4_application/5_interface-contracts.md#landing-interface) |
| 10 | [Matching scores pairs by Fellegi–Sunter arithmetic in Python, with weights estimated from the data](./10_explainable-scorer-and-weight-estimation.md) | Accepted | [Matching](../4_application/4_solution-design.md#matching) |
| 11 | [Repeating groups are stored as JSON documents](./11_json-for-repeating-groups.md) | Accepted | [Portable types](../3_information/4_data-architecture.md#portable-types) |
| 12 | [History refers to personal values in a vault, so they can be redacted](./12_personal-value-vault.md) | Accepted | [Classification](../3_information/4_data-architecture.md#classification) |
| 13 | [Every commit names its authority, and local personas run only on a local store](./13_authority-and-personas.md) | Accepted | [Authority and personas](../4_application/4_solution-design.md#authority-and-personas) |
| 14 | [Release 1 is built for under a million golden records per entity](./14_declared-capacity.md) | Accepted | [Declared capacity](../5_technology/3_capacity-and-throughput.md#declared-capacity) |
| 15 | [The hub validates against its own versioned copy of the governed code lists](./15_code-list-snapshots.md) | Accepted | [Master data configuration](../3_information/2_data-objects.md#master-data-configuration) |
| 16 | [Each source's policy is part of its entity model, and every automated change names the clause that allowed it](./16_source-policies-and-clauses.md) | Accepted | [Authority and personas](../4_application/4_solution-design.md#authority-and-personas) |
| 17 | [Prompts carry masked values only, and the stub answers until an entity enables an endpoint](./17_masked-prompts-and-the-stub.md) | Accepted | [Assistance plumbing](../4_application/1_application-services.md#application-services) |
| 18 | [The workbench is one Dash application driven by keys the steward can turn off](./18_workbench-shell-and-keys.md) | Proposed | [Application components](../4_application/2_application-components.md#application-components) |
| 19 | [A steward's decision waits in a server-side undo tray, and commits through the commit path once its window passes](./19_undo-tray.md) | Proposed | [Resolution work](../3_information/2_data-objects.md#resolution-work) |
| 20 | [The workbench shows personal values masked by role, and a reveal asks a reason and is logged per attribute](./20_masking-and-reveal-on-screen.md) | Proposed | [Application services](../4_application/1_application-services.md#application-services) |
| 21 | [The workbench's actor is the user the platform forwards, and a persona only on a local store](./21_workbench-actor.md) | Proposed | [Authority and personas](../4_application/4_solution-design.md#authority-and-personas) |
| 22 | [A steward's label binds the automated matcher, and "Not a match" hands the record back to arrival](./22_labels-bind-the-matcher.md) | Proposed | [Automated matcher](../2_business/1_business-actors-and-roles.md#automated-matcher) |
