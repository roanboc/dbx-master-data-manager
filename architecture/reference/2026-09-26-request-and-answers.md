# The product owner's request and answers, 26 September 2026

_[← Reference documents](./README.md)_

A facts-only summary of a conversation with the product owner, written the same day. It records
what was asked and decided, in neutral words.

## The request

- Build a Master Data Manager, a sibling of the Reference Data Manager, as a Databricks App with a
  local mode on DuckDB, for managing master data entities and their records.
- Research the key functions of master data management solutions, and include those services.
- The master data lives in Lakebase. As first stated, source changes were to arrive there as Zerobus
  events, and changes to tables were to be notified to the integration platform. That wiring is
  built separately: the app works on the data in Lakebase, and changes to master entities reach
  listening systems without the app taking part.
- Refine the design with DAMA-DMBOK and the leading products assessed in the Gartner Magic Quadrant
  for Master Data Management Solutions, naming no vendor.
- Design the best user experience for an innovative master data management product, including
  services assisted by artificial intelligence (AI), as the Enterprise Architecture Repository does
  for enterprise architecture.
- The repository is public and names no organisation, vendor or person.

## Answers

Given after the product owner read the proposal *Master Data Manager Blueprint*.

1. Release 1 proves two entities end to end: Person and Organisation.
2. Language-model services arrive in Release 2. Release 1 ships the deterministic services, the AI
   plumbing and a complete stub.
3. Release 1 is built and tested for under one million golden records per entity. The architecture
   keeps the path to five million easy: paged reads, stored blocking keys, resumable jobs and
   incremental arrivals from a watermark. The product owner may raise the figure if Person nears
   one million.
4. Delivery follows the archreator method 0.6: an establishing commit and strategy discovery as a
   documents-only pull request, merged first; then Foundations, Steward workbench, and Tune, govern
   and deploy, each as its own pull request.
5. Operational integration uses Lakebase directly. This later decision of the same day replaces
   the proposal's landing and propagation route.
   - Inbound: the integration platform writes source changes straight into landing tables in
     Lakebase. The app never writes a landing table.
   - Outbound: changes to the published master tables appear in a change feed inside Lakebase, with
     commit versions. A change notifier the platform runs, not the app, tells the integration
     platform which versions changed. Consumers query the changes back from their own watermark,
     within seconds.
   - Lakehouse Sync, or any sync to Delta, serves analytics only. It is not the propagation route.
