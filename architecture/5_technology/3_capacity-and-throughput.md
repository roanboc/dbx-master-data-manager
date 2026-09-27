# Capacity and throughput

_[← Technology layer](./README.md) · [Model home](../README.md)_

**ArchiMate viewpoint:** Technology layer: the load the technology services are built for, and what they were measured to carry.

## Declared capacity

Release 1 is built and tested for under one million golden records per entity. [Assessment [`ASM3`] Release 1 volumes stay under a million golden records per entity](../1_strategy/1_motivation.md#assessments) expects that volume. The path to five million stays open, because every figure below bounds a read, a write or a job rather than the store. [Decision 14](../decisions/14_declared-capacity.md) records why. The figures live in `src/mdm/capacity.py`, and a test fails any read of a large table that is neither keyed nor paged.

| Figure | Value | What it bounds |
| ------ | ----- | -------------- |
| Golden records per entity | Under 1,000,000; 5,000,000 kept within reach | The volume every other figure is sized for |
| Source changes a day | 200,000, sustained | The arrival job's daily work |
| Read page | 5,000 rows | Every scan pages by key and never skips with an offset |
| Key chunk | 5,000 keys | Every keyed read, which joins a document of keys |
| Write chunk | 10,000 rows | Every insert and upsert, bound as one JavaScript Object Notation (JSON) row document |
| Arrival batch | 1,000 records; 10,000 in bulk | One pass of the arrival job, which resumes from its queue and position |
| Commit chunk | 500 published rows; 10,000 in bulk | One commit, and so how long the commit-order lock is held |
| Candidates per record | 200 | The candidates a record keeps, those sharing the most blocking passes |
| Stop key | 1,000 records | A blocking key shared by more records is dropped, since it separates nothing |
| Members checked | 500 per golden record | The members compared when a record meets a golden record |
| Gap probes | 100 ranges a run | The landing gaps probed again on each run |
| Landing retention | 14 days | How long a lost gap is probed again, daily |
| Estimation samples | 100,000 pairs for u, a comparison level's chance among non-matches; 20,000 records, at most 200,000 pairs a blocking pass, kept by a hash of the pair, and 50 iterations for expectation maximisation; at least 200 pairs sharing a registered ID for m, a level's chance among true matches | One weight estimation |
| Feed page | 5,000 change rows | One read of the change feed |
| Landing row size | 4,000 characters a text value, 100 entries a repeating group, 64 KB a payload | One landing row; a row over a limit is rejected `too_large`, so no single row slows arrival |
| Bulk throttle | 200,000 published rows an hour, proposed; off unless `MDM_THROTTLE_ROWS_PER_HOUR` sets it | How fast a later bulk change reaches listening systems |

## Measured throughput

The spike lands an invented initial load, arrives it in bulk, lands about 1,000 updates and arrives them incrementally, then scores the result against the invented truth. It ran on 2026-09-27, in a container with 4 virtual processors and 15 GB of memory, on Python 3.11.15, DuckDB 1.5.5 on a file, and Postgres 16.13 on a throwaway local server, with:

```bash
uv run python tools/spike_throughput.py --engine duckdb   --records 100000
uv run python tools/spike_throughput.py --engine postgres --records 100000
```

| Engine | Records landed | Landing rows/s | Arrival records/s | Incremental records/s | Commits | Golden records | Tasks | Largest block per pass | Seconds | Extrapolated to 1,000,000 |
| ------ | -------------- | -------------- | ----------------- | --------------------- | ------- | -------------- | ----- | ---------------------- | ------- | ------------------------- |
| DuckDB | 100,709 | 16,322 | 165 | 38 | 28 | 43,469 persons, 11,090 organisations | 9,136 reviews, 847 possible duplicates, 57 held | person `family_birth_year` 237, organisation `name_tokens` 218; every other pass 5 or fewer | 659 | 6,071 s (1.7 h) linear; 24,005 s (6.7 h) with the block growth |
| Postgres | 100,709 | 22,033 | 172 | 64 | 28 | 43,469 persons, 11,090 organisations | 9,136 reviews, 847 possible duplicates, 57 held | person `family_birth_year` 237, organisation `name_tokens` 218; every other pass 5 or fewer | 621 | 5,814 s (1.6 h) linear; 22,990 s (6.4 h) with the block growth |

Both engines gave the same golden records, tasks, commits and scores. Against the invented truth, person records were linked with precision 0.978 and recall 0.879, and organisations with 0.976 and 0.928. The records whose candidates hit the cap of 200 were 1,063, about 1%. The mean candidates per record grew from 10.24 at a quarter of the load to 50.58 at the end. Peak memory was 1,048 MB on Postgres and 1,700 MB on DuckDB, which runs in the same process.

The 1,000,000-record run on DuckDB did not happen. It was to run only if the 100,000-record run finished in under 3 minutes, so that ten times the load fitted in 30 minutes; that run took 11 minutes.

Against the declared capacity, the daily figure holds on this machine. At 64 records a second on Postgres and 38 on DuckDB, 200,000 source changes take 0.9 to 1.5 hours of processing a day. The volume figure is not proven yet. Loading 1,000,000 source records in bulk projects to 1.6 to 1.7 hours if the rate held, and to 6.4 to 6.7 hours if blocks keep growing as they did. That is acceptable for a one-off initial load, but it is a projection, not a measurement. Closing it takes the run at the declared volume on the operational database, and the platform run. It may also take three changes, if that run confirms the growth: a finer key for the family name and birth year pass, term-frequency adjustment for common names, and bulk arrival in parallel by entity.

The linear extrapolation has two limits. Scoring costs Python time per record, and blocks grow as the store grows, because common names form large blocks. Two runs are spend, and wait: one on the operational database at the declared volume, and one in the platform's development environment. Both are **Pending — future initiative** ([initiative 4](../6_transition/2_sequence.md#sequence)).
