# Demo script (5 minutes) and control-stage runbook

## Before the demo

- Hosted app open in one tab, local Docker app in a second tab as fallback, the Trace tab of `p1-ru-029` pre-opened in a third tab.
- DEV fully evaluated with the organizer checklist v1; the control group imported and evaluated at H+23.
- One appeal already decided on another dialog (shows the History timeline) and one fresh dialog pasted during the demo.
- Role toggle set to QA analyst; language RU; dark theme.

## Minute 0:00 – 1:00 · Interface and a dialog

1. Conversations list: point at the columns (language, channel, total with two decimals, gross-error badge, provider badge "Claude · Opus 5.5", cost per dialog).
2. Filter KZ chats, then open `p1-ru-029` (rude operator). Say: "every number here was produced by a live call, nothing is precomputed; the trace proves it".

## 1:00 – 3:00 · Evaluation, justification, evidence, N/A, total

3. Total ring 70.00, gross-error banner (criterion 8 = Да, no points, flagged separately as the PDF requires).
4. Criterion 5 (Эмпатия) = Нет: read the explanation in the PDF's own style, click the evidence chip `[9]` → transcript scrolls and highlights «Вы вообще читать умеете?».
5. Criterion 3 (Идентификация) = N/A: show the N/A reason (general navigation question, no account data disclosed) and the cited turns.
6. Criterion 1 = Да with the first operator turn cited; mention weight redistribution: C2 and C3 are N/A, so the remaining 65 points are rescaled to 100 (weight_applied column).
7. Open the Trace tab: model, prompt version hash, tokens, cost $0.20, 19 s, raw request and response. Close it.
8. Switch to the call `p4-ru-002`: press play from an evidence timestamp (hold etiquette), show acoustic chips (pace, long pauses, hold segments) feeding criterion 6.

## 3:00 – 4:00 · Appeal and reviewer decision

9. On `p1-ru-029` criterion 7 (Завершение = Да): press Appeal, comment "прощание сразу после жалобы клиента, без итога", save → row shows "На проверке".
10. Toggle role to Reviewer → Review Queue: the disputed verdict, comment, evidence excerpt; choose "Изменить" → Нет, reason, see the preview total drop from 70.00 to 62.31, save.
11. Back on the card: original 70.00 struck through, final 62.31, History shows AI verdict → appeal → decision. Mention: the accepted decision became a few-shot example for criterion 7.

## 4:00 – 5:00 · Change a criterion and re-apply

12. Checklist Studio: open v1, edit criterion 7 wording (or replace it with the organizer's new criterion), set its weight, save as v2 with a note.
13. Diff view: one criterion changed, requires re-judging; weight-only changes need no model calls.
14. Apply to dataset → Partial → dry run shows the cost estimate (cents) → Run → progress bar completes in under two minutes for the control set → list shows new totals.
15. Paste a dialog the jury hands over → Evaluate → card in about 20 seconds. End on the Analytics screen: criterion heatmap by language, AI-vs-reviewer agreement, cost per provider, "local model" option in Settings.

Fallbacks: if the network fails, the local Docker app has the same data; if the API key is rate-limited, switch Settings → provider to Sonnet; a recorded run of the demo is in `deck/`.

## Control-stage runbook (H+22 → H+24)

1. H+22:00 Freeze: Settings → default provider `ensemble` (Opus + Sonnet, local if the cluster endpoint is up), prompt version v1 or the best harness version, effort high, few-shot bank on. Tag commit `freeze`.
2. H+23:00 Conversations → Import → control `group_id` → confirm the record count, languages and channels.
3. H+23:02 Select all new conversations → Evaluate → checklist v1 (organizer) → ensemble → Run. Watch progress; failed items are retried by re-running Evaluate on the failed subset. If the API rate-limits, re-run with provider `anthropic` model `claude-sonnet-5-5`, then local.
4. H+23:20 Spot-check three cards; export `evaluations.csv` and keep a copy outside the repo.
5. H+23:30 Organizer criterion replacement: Checklist Studio → v1 → replace the criterion → save v2 → Diff → Apply partial to the control conversations → new totals; export CSV again.
6. H+23:50 Final commit and push; verify the public repo opens and the README has the hosted URL; stop coding.

Costs: control set of 10 in ensemble mode ≈ $4; partial re-judge of one criterion ≈ $0.30.
