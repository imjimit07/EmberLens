# Evaluation evidence record

This file records evidence that EmberLens earned in a real evaluation. It is not a results claim. Do not replace bracketed fields with estimates.

## Case study record

Use one record for each region and question that you present.

| Field | Record |
| --- | --- |
| Case-study ID | `[for example, Punjab-2024-harvest-window]` |
| Analyst question | `[What changed, and why was it worth investigating?]` |
| Region | `[EmberLens region]` |
| Earlier period | `[YYYY-MM-DD to YYYY-MM-DD]` |
| Later period | `[YYYY-MM-DD to YYYY-MM-DD]` |
| Sensor filter | `[ALL, MODIS, or VIIRS]` |
| Data mode | `[real or simulated]` |
| Data import / retrieval record | `[archive manifest names or query-window dates]` |
| Exported evidence report | `[repository path or attachment name]` |
| Raw loaded-record result | `[copy from the report]` |
| Matched-source result | `[copy from the report, or state unavailable]` |
| What the analyst did next | `[independent source, field record, or no action]` |
| Limitation that remains | `[specific limitation]` |

A case study is ready for a presentation only when every row is complete and the exported report opens to the same query and result. Keep an inconclusive result. It demonstrates that the workflow did not force a story from incomplete records.

## Intended-user task study

Run this with people who resemble the stated audience, such as environmental-analysis students, fire researchers, or GIS analysts. Ask everyone to use the same imported dataset and browser/device class if possible.

Task: “Two periods have different loaded hotspot totals. Decide whether the comparison is suitable for follow-up, name one limitation, and state which evidence supports your decision.”

For each participant, record:

| Participant ID | Relevant experience | Time to answer | Correctly identifies source-mix issue | States a valid limitation | Notes |
| --- | --- | ---: | --- | --- | --- |
| `[P01]` | `[role / experience]` | `[mm:ss]` | `[yes/no]` | `[yes/no]` | `[observed behavior]` |

Use a pre-defined answer key based on the evidence report. If you compare EmberLens with another workflow, randomize the order where practical and disclose the comparison method. Report the number of participants, unfinished tasks, and any ambiguous scoring. Do not turn informal feedback into an accuracy claim.

## Deployment check

Record this on the deployed URL used in the presentation.

| Check | Date and result |
| --- | --- |
| Health route returns ready | `[timestamp and status]` |
| A real-data filter loads | `[query and observed result]` |
| Change explanation runs | `[query and observed result]` |
| Evidence report downloads | `[filename]` |
| Empty-data state is understandable | `[route/filter and observation]` |
| Mobile viewport checked | `[device or width and observation]` |

## Scientific review notes

List independent reviewers, feedback, and changes made. Keep unresolved concerns visible. The source-aware comparison makes a loaded-data claim; it does not validate burned area, fire perimeters, fire severity, or operational fire risk.
