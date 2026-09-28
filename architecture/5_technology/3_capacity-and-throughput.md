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
| Inbox page | 50 tasks, keyed by due time and task ID | One read of the inbox; it never skips with an offset |
| Counts shown | Exact to 999, then "999+"; each count reads at most 1,000 rows | Every count on screen: views, task kinds, breaches and the tray (adopted — capped counts) |
| Refresh | Counts every 30 seconds; the tray every 2 seconds while a decision is staged, and not at all otherwise | How often a browser tab asks the hub |
| Candidates shown | 3 | The candidate columns of the decide pane |
| Cases kept | 64 per process, masked, one per task version and role | The decide pane's prepared cases |
| Members shown | 200 per record | The members listed on a record; the rest are counted |
| Relationships shown | 200 per record | The relationships listed on a record; the rest are counted |
| Timeline page | 50 events, newest first, keyed | One read of a record's history |
| Tray shown | 50 of a steward's own decisions: those still staged, and those settled in the last 10 minutes | The tray's list in the header |
| Tray flush | 100 decisions a pass, each in its own transaction; an unexpected failure is tried in 3 passes | One pass of the undo tray's flush |
| Undo window | 60 seconds, set by `MDM_UNDO_SECONDS` | How long a staged decision can be undone (adopted — the Blueprint's window) |
| Claim | 10 minutes, set by `MDM_CLAIM_MINUTES` | How long a task stays claimed without an action (adopted — the Blueprint's soft lock) |
| Close call | 10 score points, set by `MDM_CLOSE_CALL_POINTS` | How close the top two candidates are before a link needs an explicit choice (adopted — ten points) |
| Service levels | Review 8 hours, held 8, possible duplicate 24, exception 24, orphan 72, unresolved reference 72, quality sample 72, set by `MDM_SLA_HOURS` | When a task starts breaching, per kind (adopted — until the governance policy holds them) |
| Quality samples | 2% of the sampled decisions (the solution design's [What is sampled](../4_application/4_solution-design.md#what-is-sampled)), set by `MDM_SAMPLE_SHARE` (adopted — Blueprint §3); at most 200 automated samples open per entity, set by `MDM_SAMPLE_OPEN_CAP`, which accepts at most 10,000 (proposed). On a shared store the share cannot be 0; until initiative 4, the values are deployment settings, not approved policy | How many blind reviews the matcher and the stewards' decisions open. A 1,000,000-record initial load would draw about 20,000; 200 per entity are open at once, and the rest are skipped and counted |
| Breaker, agreement | The latest 100 reviewed automatic links per entity, set by `MDM_BREAKER_WINDOW` and at most 1,000; at least 20 reviewed, set by `MDM_BREAKER_MIN_SAMPLES`; a trip when the one-sided 95% upper bound on agreement falls below 95%, set by `MDM_BREAKER_AGREEMENT`, at least 50% on a shared store (proposed) | When low agreement demotes the automatic band. It trips on 3 disagreements of 20, 6 of 50 or 9 of 100. At a true agreement of 97% that happens 2.1% of the time on 20 reviews and 0.3% on 100; at 90%, 32% to 68% of the time |
| Breaker, arrivals | More than 5 times the mean of the same clock hour over the previous 7 days, and at least 1,000 in the hour, set by `MDM_BREAKER_SPIKE_MULTIPLE`, `MDM_BREAKER_SPIKE_DAYS` and `MDM_BREAKER_SPIKE_MIN`; counts kept 8 days (proposed) | When an arrival spike demotes the automatic band. It needs arrivals counted 7 days back, leaves initial loads aside, starts again after a volume trip restored for an expected load, and rests only for its hour after any other volume restore |

## Measured throughput

The spike lands an invented initial load, arrives it in bulk, lands about 1,000 updates and arrives them incrementally, then scores the result against the invented truth. It runs with the default settings, the matcher's checkpoint included, so a share of the automated decisions is drawn for blind review as it arrives. It ran again on 2026-09-27, once the checkpoint was built, in a container with 4 virtual processors and 15 GB of memory, on Python 3.11.15, DuckDB 1.5.5 on a file, and Postgres 16.13 on a throwaway local server, with:

```bash
uv run python tools/spike_throughput.py --engine duckdb   --records 100000
uv run python tools/spike_throughput.py --engine postgres --records 100000
```

| Engine | Records landed | Landing rows/s | Arrival records/s | Incremental records/s | Commits | Golden records | Tasks | Quality samples | Largest block per pass | Seconds | Extrapolated to 1,000,000 |
| ------ | -------------- | -------------- | ----------------- | --------------------- | ------- | -------------- | ----- | --------------- | ---------------------- | ------- | ------------------------- |
| DuckDB | 100,709 | 25,014 | 158 | 48 | 28 | 43,469 persons, 11,090 organisations | 9,136 reviews, 847 possible duplicates, 57 held | 400 drawn, 1,384 skipped at the cap | person `family_birth_year` 237, organisation `name_tokens` 218; every other pass 5 or fewer | 677 | 6,346 s (1.8 h) linear; 25,093 s (7.0 h) with the block growth |
| Postgres | 100,709 | 27,768 | 160 | 79 | 28 | 43,469 persons, 11,090 organisations | 9,136 reviews, 847 possible duplicates, 57 held | 400 drawn, 1,384 skipped at the cap | person `family_birth_year` 237, organisation `name_tokens` 218; every other pass 5 or fewer | 657 | 6,250 s (1.7 h) linear; 24,714 s (6.9 h) with the block growth |

Both engines gave the same golden records, tasks, commits, quality samples and scores. Against the invented truth, person records were linked with precision 0.978 and recall 0.879, and organisations with 0.976 and 0.928. The records whose candidates hit the cap of 200 were 1,063, about 1%. The mean candidates per record grew from 10.24 at a quarter of the load to 50.58 at the end. Peak memory was 1,039 MB on Postgres and 1,717 MB on DuckDB, which runs in the same process. At the 2% share the load drew 1,784 samples; the cap of 200 open automated samples per entity opened 400 of them and skipped the rest, so the initial load leaves the stewards 400 blind reviews, not 1,784. Against the run before the checkpoint, the golden records, tasks and scores are the same; bulk arrival is 4% slower on DuckDB and 7% slower on Postgres, and incremental arrival and landing are faster on both.

The 1,000,000-record run on DuckDB did not happen. It was to run only if the 100,000-record run finished in under 3 minutes, so that ten times the load fitted in 30 minutes; that run took 11 minutes.

Against the declared capacity, the daily figure holds on this machine. At 79 records a second on Postgres and 48 on DuckDB, 200,000 source changes take 0.7 to 1.2 hours of processing a day. The volume figure is not proven yet. Loading 1,000,000 source records in bulk projects to 1.7 to 1.8 hours if the rate held, and to 6.9 to 7.0 hours if blocks keep growing as they did. That is acceptable for a one-off initial load, but it is a projection, not a measurement. Closing it takes the run at the declared volume on the operational database, and the platform run. It may also take three changes, if that run confirms the growth: a finer key for the family name and birth year pass, term-frequency adjustment for common names, and bulk arrival in parallel by entity.

The linear extrapolation has two limits. Scoring costs Python time per record, and blocks grow as the store grows, because common names form large blocks. Two runs are spend, and wait: one on the operational database at the declared volume, and one in the platform's development environment. Both are **Pending — future initiative** ([initiative 4](../6_transition/2_sequence.md#sequence)).
