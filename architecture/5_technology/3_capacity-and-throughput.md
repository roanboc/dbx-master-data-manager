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
| Commit chunk | 500 published rows, and at most 500 decisions for a batch; 10,000 in bulk | One commit, and so how long the commit-order lock is held |
| Candidates per record | 200 | The candidates a record keeps, those sharing the most blocking passes |
| Stop key | 1,000 records | A blocking key shared by more records is dropped, since it separates nothing |
| Members checked | 500 per golden record | The members compared when a record meets a golden record |
| Gap probes | 100 ranges a run | The landing gaps probed again on each run |
| Landing retention | 14 days | How long a lost gap is probed again, daily |
| Estimation samples | 100,000 pairs for u, a comparison level's chance among non-matches; 20,000 records, at most 200,000 pairs a blocking pass, kept by a hash of the pair, and 50 iterations for expectation maximisation; at least 200 pairs sharing a registered ID for m, a level's chance among true matches | One weight estimation |
| Feed page | 5,000 change rows | One read of the change feed |
| Landing row size | 4,000 characters a text value, 100 entries a repeating group, 64 KB a payload | One landing row; a row over a limit is rejected `too_large`, so no single row slows arrival |
| Bulk throttle | 200,000 published rows an hour, proposed; off unless `MDM_THROTTLE_ROWS_PER_HOUR` sets it. It paces arrival's bulk mode and each chunk of a batch, which waits for its turn rather than holding the tray | How fast a later bulk change reaches listening systems |
| Inbox page | 50 tasks, keyed by due time and task ID | One read of the inbox; it never skips with an offset |
| Alike reviews | The 1,000 open reviews due soonest per entity are grouped, and the 25 largest groups of at least 2 are shown (proposed); each count exact to 999 | One read of the Alike reviews page; a group outside the window shows once its reviews come due sooner |
| Batch rows | 50 rows a page, keyed by position | One page of a batch's every-row change |
| Counts shown | Exact to 999, then "999+"; each count reads at most 1,000 rows | Every count on screen: views, task kinds, breaches and the tray (adopted — capped counts) |
| Refresh | Counts every 30 seconds; the tray every 2 seconds while a decision is staged or a batch commits, and not at all otherwise; a batch's page every 2 seconds while it waits in the tray or commits, and every 30 seconds while it is open otherwise | How often a browser tab asks the hub |
| Candidates shown | 3 | The candidate columns of the decide pane |
| Cases kept | 64 per process, masked, one per task version and role | The decide pane's prepared cases |
| Members shown | 200 per record | The members listed on a record; the rest are counted |
| Relationships shown | 200 per record | The relationships listed on a record; the rest are counted |
| Timeline page | 50 events, newest first, keyed | One read of a record's history |
| Tray shown | 50 of a steward's own decisions, and the batches they confirmed as second steward: those still staged, a batch while it commits, and those settled in the last 10 minutes | The tray's list in the header |
| Tray flush | 100 decisions a pass, each in its own transaction, and one chunk of each committing batch; an unexpected failure is tried in 3 passes in a row | One pass of the undo tray's flush |
| Undo window | 60 seconds, set by `MDM_UNDO_SECONDS` | How long a staged decision can be undone (adopted — the Blueprint's window) |
| Batch undo | 30 days after its last chunk, set by `MDM_BATCH_UNDO_DAYS` (adopted — Blueprint §2) | How long a committed batch can be compensated |
| Claim | 10 minutes, set by `MDM_CLAIM_MINUTES` | How long a task stays claimed without an action (adopted — the Blueprint's soft lock) |
| Close call | 10 score points, set by `MDM_CLOSE_CALL_POINTS` | How close the top two candidates are before a link needs an explicit choice (adopted — ten points) |
| Forced sample | 5 reviews plus 1 per 150 of the batch, rounded down, stratified by the source-system pair; set by `MDM_FORCED_SAMPLE_BASE` and `MDM_FORCED_SAMPLE_PER` (adopted — Blueprint §3). On a shared store it cannot shrink | How many alike reviews a steward decides one by one before the rest are linked together: 9 of 612 |
| Second steward | Above 250 decisions, set by `MDM_BATCH_CHECKER_ABOVE` (adopted — Blueprint §3, §5.5); it cannot rise on a shared store | When a batch waits for a second steward before it enters the tray |
| Largest batch | 1,000 reviews (proposed) | One batch's draw, preparation and rows; the rest of a larger group waits for a later batch |
| Service levels | Review 8 hours, held 8, possible duplicate 24, exception 24, orphan 72, unresolved reference 72, quality sample 72, set by `MDM_SLA_HOURS` | When a task starts breaching, per kind (adopted — until the governance policy holds them) |
| Quality samples | 2% of the sampled decisions (the solution design's [What is sampled](../4_application/4_solution-design.md#what-is-sampled)), set by `MDM_SAMPLE_SHARE` (adopted — Blueprint §3), and exactly 2% of each batch's links, at least one; at most 200 automated samples open per entity, set by `MDM_SAMPLE_OPEN_CAP`, which accepts at most 10,000 (proposed). On a shared store the share cannot be 0; until initiative 4, the values are deployment settings, not approved policy | How many blind reviews the matcher and the stewards' decisions open. A 1,000,000-record initial load would draw about 20,000; 200 per entity are open at once, and the rest are skipped and counted |
| Breaker, agreement | The latest 100 reviewed automatic links per entity, set by `MDM_BREAKER_WINDOW` and at most 1,000; at least 20 reviewed, set by `MDM_BREAKER_MIN_SAMPLES`; a trip when the one-sided 95% upper bound on agreement falls below 95%, set by `MDM_BREAKER_AGREEMENT`, at least 50% on a shared store (proposed) | When low agreement demotes the automatic band. It trips on 3 disagreements of 20, 6 of 50 or 9 of 100. At a true agreement of 97% that happens 2.1% of the time on 20 reviews and 0.3% on 100; at 90%, 32% to 68% of the time |
| Breaker, arrivals | More than 5 times the mean of the same clock hour over the previous 7 days, and at least 1,000 in the hour, set by `MDM_BREAKER_SPIKE_MULTIPLE`, `MDM_BREAKER_SPIKE_DAYS` and `MDM_BREAKER_SPIKE_MIN`; counts kept 8 days (proposed) | When an arrival spike demotes the automatic band. It needs arrivals counted 7 days back, leaves initial loads aside, starts again after a volume trip restored for an expected load, and rests only for its hour after any other volume restore. It never withdraws bulk rights ([decision 3](../decisions/3_quality-breaker-autonomy.md)) |
| Bulk rights | The latest 50 reviewed batch samples of a pattern, set by `MDM_BULK_WINDOW`; at least 5 reviewed, set by `MDM_BULK_MIN_SAMPLES`; a withdrawal when the one-sided 95% upper bound on agreement falls below 95%, set by `MDM_BULK_AGREEMENT`, at least 50% on a shared store (proposed) | When low agreement withdraws a pattern's bulk rights. It withdraws on 2 disagreements of 5, 3 of 20 or 6 of 50. At a true agreement of 97% that happens 0.9% of the time on 5 reviews; at 90%, 8% on 5 and 32% on 20. At 2%, 5 reviews come from about 250 batched links, or from five smaller batches, since each batch sends at least one |

## Measured throughput

The spike lands an invented initial load, arrives it in bulk, lands about 1,000 updates and arrives them incrementally, then scores the result against the invented truth. It runs with the default settings, the matcher's checkpoint included, so a share of the automated decisions is drawn for blind review as it arrives. With `--batch` it then links the largest Person group of alike reviews as a signature batch. It ran again on 2026-09-29, once signature batches were built, in a container with 4 virtual processors and 15 GB of memory, on Python 3.11.15, DuckDB 1.5.5 on a file, and Postgres 16.13 on a throwaway local server, with:

```bash
uv run python tools/spike_throughput.py --engine duckdb   --records 100000 --batch
uv run python tools/spike_throughput.py --engine postgres --records 100000 --batch
```

| Engine | Records landed | Landing rows/s | Arrival records/s | Incremental records/s | Commits | Golden records | Tasks | Quality samples | Largest block per pass | Seconds | Extrapolated to 1,000,000 |
| ------ | -------------- | -------------- | ----------------- | --------------------- | ------- | -------------- | ----- | --------------- | ---------------------- | ------- | ------------------------- |
| DuckDB | 100,709 | 25,889 | 163 | 49 | 28 | 43,469 persons, 11,090 organisations | 9,136 reviews, 847 possible duplicates, 57 held | 400 drawn, 1,344 skipped at the cap | person `family_birth_year` 237, organisation `name_tokens` 218; every other pass 5 or fewer | 756, the batch included | 6,153 s (1.7 h) linear; 24,328 s (6.8 h) with the block growth |
| Postgres | 100,709 | 25,105 | 168 | 82 | 28 | 43,469 persons, 11,090 organisations | 9,136 reviews, 847 possible duplicates, 57 held | 400 drawn, 1,344 skipped at the cap | person `family_birth_year` 237, organisation `name_tokens` 218; every other pass 5 or fewer | 664, the batch included | 5,965 s (1.7 h) linear; 23,587 s (6.6 h) with the block growth |

Both engines gave the same golden records, tasks, commits, quality samples and scores. Against the invented truth, person records were linked with precision 0.978 and recall 0.879, and organisations with 0.976 and 0.928. The records whose candidates hit the cap of 200 were 1,063, about 1%. The mean candidates per record grew from 10.24 at a quarter of the load to 50.58 at the end. Peak memory was 1,041 MB on Postgres and 1,757 MB on DuckDB, which runs in the same process. At the 2% share the load drew 1,744 samples; the cap of 200 open automated samples per entity opened 400 of them and skipped the rest, so the initial load leaves the stewards 400 blind reviews, not 1,744. Against the run of 2026-09-27, the golden records, tasks, commits and scores are the same, and arrival and landing ran at much the same rates. The draw of 2% picked 40 fewer records than on 2026-09-27, all of them beyond the cap.

The batch phase takes the largest Person group in the Alike reviews window, whose count reads "999+", and draws a batch from its 1,000 reviews due soonest. It links each forced-sample review to the golden record its case suggests, through the tray with an undo window of 1 second, then shows every change. It stages the batch, confirmed by the coordinating steward since it is above 250 decisions, and flushes until the batch commits:

| Engine | Eligible at the draw | Forced sample | Links | Draw | Deciding the sample | Every change shown | Chunks: published rows, and seconds each | Outcome |
| ------ | -------------------- | ------------- | ----- | ---- | ------------------- | ------------------ | ---------------------------------------- | ------- |
| DuckDB | 535 of 1,000 | 8, all agreed | 478 | 14.1 s | 14.7 s, 8.4 s of it the undo windows | 37.0 s | 377 rows in 12.0 s, 371 in 12.7 s, 235 in 9.4 s | Committed |
| Postgres | 535 of 1,000 | 8, all agreed | 478 | 14.3 s | 9.2 s, 8.4 s of it the undo windows | 9.5 s | 377 rows in 0.9 s, 371 in 0.8 s, 235 in 0.5 s | Committed |

Both engines drew, sampled, prepared and chunked the same batch. The draw left out 465 of the 1,000 reviews: 280 whose best candidate a cannot-link rule blocks, 171 close calls, 13 whose pattern had changed since arrival, and 1 whose record had moved. Preparation left out 49 more: 45 that a cannot-link rule keeps from their golden record or from another review joining it, and 4 whose pattern had changed. The 478 links joined 348 golden records and sent 10 to blind review. Each chunk stayed under the 500 published rows of a commit chunk, and every chunk committed in its own flush pass. On Postgres a chunk of 185 links commits in under a second. On DuckDB the flush pass that commits it takes about 12 seconds. That is the local mode only: on the platform the operational database commits the chunks.

The 1,000,000-record run on DuckDB did not happen. It was to run only if the 100,000-record run finished in under 3 minutes, so that ten times the load fitted in 30 minutes; that run took 11 minutes before its batch phase.

Against the declared capacity, the daily figure holds on this machine. At 82 records a second on Postgres and 49 on DuckDB, 200,000 source changes take 0.7 to 1.1 hours of processing a day. The volume figure is not proven yet. Loading 1,000,000 source records in bulk projects to 1.7 hours if the rate held, and to 6.6 to 6.8 hours if blocks keep growing as they did. That is acceptable for a one-off initial load, but it is a projection, not a measurement. Closing it takes the run at the declared volume on the operational database, and the platform run. It may also take three changes, if that run confirms the growth: a finer key for the family name and birth year pass, term-frequency adjustment for common names, and bulk arrival in parallel by entity.

The linear extrapolation has two limits. Scoring costs Python time per record, and blocks grow as the store grows, because common names form large blocks. Two runs are spend, and wait: one on the operational database at the declared volume, and one in the platform's development environment. Both are **Pending — future initiative** ([initiative 4](../6_transition/2_sequence.md#sequence)).
