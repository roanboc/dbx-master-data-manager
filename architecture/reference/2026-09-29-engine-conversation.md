# Where the hub writes and where the lakehouse reads, 29 September 2026

_[← Reference documents](./README.md)_

A facts-only summary of one conversation with the product owner, on 29 September 2026. It records
what was asked, learnt and decided, in neutral words.

## What was asked

- Whether a packaged master data management product built natively on the platform should replace
  building the hub. The product keeps the lakehouse's Delta tables as the master and serves a copy
  of golden records from Lakebase for fast reads. It also offers code lists as a reference entity.
- Whether reconciliation belongs in the lakehouse rather than in Lakebase.
- Whether the hub can replace the incumbent hub, which masters Person only, from a few sources, and
  is little used.
- Which store should lead, given the wish for one platform, managed by Databricks, that is fast for
  both writes and reads.

## What was learnt

- The data governance team reported, in an earlier validation, that the incumbent hub delivers
  updates to listening systems every five minutes.
- The product owner wants a platform that stays fit as needs grow.
- The platform announced Lake Transactional/Analytical Processing (LTAP) on 16 June 2026:
  transactions through Lakebase Postgres and analytics through Delta or Iceberg readers, over one
  copy of the data in the lake. The single copy is announced as coming soon. Available today are
  Lakehouse Sync, which copies Lakebase tables into Delta by change data capture, and synced
  tables, which copy Delta into Lakebase. LTAP Direct Writes, in preview, needs Postgres 17.
- The Reference Data Manager edits business-owned lists; the packaged product's reference entity
  reconciles code lists found in sources. The two do different jobs.

## What was decided

- Keep building the hub, with everything written through Lakebase as answer 5 planned.
- The lakehouse reads what the hub commits: through Lakehouse Sync today, and through LTAP's single
  copy once it is generally available.
- Bulk matching moves to a lakehouse job only if the platform throughput test shows the commits
  cannot keep up.
- Recorded as [decision 25](../decisions/25_lakebase-writes-the-lakehouse-reads.md).
