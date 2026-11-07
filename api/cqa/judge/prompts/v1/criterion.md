# Criterion {code} · {title}

Checklist instruction: {instruction}
{channel_instruction}
Allowed answers: {allowed_answers}
N/A: {na_rule}
{anchors_block}
## Decision procedure
Definition: {definition}
Steps:
{steps_block}
{na_block}
Evidence requirement: {evidence_requirement}
{pitfalls_block}
{channel_notes_block}
{reference_block}
{fewshot_block}
Explanation language: {explanation_language}.

Walk the steps in order, record one short line per step in `step_results`, then decide `applicable`, `answer`, `confidence`, `evidence`, `checked_span` (if a required action is missing), `explanation`, `flags`, `claims`. Return the verdict JSON for criterion {code}.
