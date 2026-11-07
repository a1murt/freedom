You compile a customer-support quality criterion into a precise decision procedure for an LLM judge.

Company: Freedom Broker (Kazakhstan brokerage). Dialogs are chats (RU/KZ/EN, operator joins after a bot and an escalation event) or voice-call transcripts (RU, automatic speech recognition, IVR prefix, PII masks).

Given the criterion below, write a JudgeSpec:
- definition: one paragraph of what the criterion means in this company's context, incorporating every detail in the instruction and the N/A condition.
- decision_steps: 3–7 ordered, checkable steps that start from locating the relevant turns (e.g. "Find the first operator turn") and end with the mapping to the allowed answers. For scale criteria, include a step that maps findings to each listed step of the scale.
- na_conditions: explicit, observable conditions (empty if N/A is not allowed).
- evidence_requirement: what must be quoted for yes/no/each scale step and for N/A.
- pass_examples / fail_examples: 2–3 short illustrative operator behaviours each (not full dialogs).
- channel_notes: adaptations for "agent chat" and "voice call".
- pitfalls: mistakes a judge could make (leaking other criteria, penalising Russian UI labels in KZ/EN dialogs, treating ASR noise as literacy errors, confusing "no" with N/A).

Write everything in Russian except field names. Be concrete; quote the checklist wording where it matters.

Criterion:
{criterion_json}
