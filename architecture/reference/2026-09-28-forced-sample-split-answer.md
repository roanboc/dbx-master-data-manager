# The product owner's answer on a forced sample's split, 28 September 2026

_[← Reference documents](./README.md)_

A facts-only summary of one conversation with the product owner, on 28 September 2026, while story
3.3 of initiative 3 was planned. It records what was asked and answered, in neutral words.

## The question

When a review of a forced sample disagrees, which alike reviews leave the batch, so that they are
decided one by one? Three options were put:

- the reviews that share the disagreeing record's value on the comparison the steward names;
- the reviews of the disagreeing review's source-system pair;
- no partial split, so that any disagreement ends the batch.

## The answer

- The product owner chose the first option: the reviews that share the disagreeing record's value
  on the comparison the steward names leave the batch.
- The value is compared inside the hub only.
- The hub stores task IDs and the comparison's name, and shows a count.
- When the comparison's attributes are personal, the hub writes one access-log row per split
  record, with the reason `batch_split`.
