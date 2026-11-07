# ML brief: the judge that wins the accuracy criterion

For the ML teammate. You own everything under `api/cqa/judge/` and `api/eval/`. The rest of the system (ingest, checklist versions, scoring engine, UI, appeals) is built around the contract below, so you can start now without waiting for the app. How you get there is yours; this brief states what must be ready, the data facts that matter, and ideas that are likely to pay off.

## What must be ready

| When | Deliverable | Definition of done |
|---|---|---|
| H+2 | Contract agreed | You accept `Verdict` and `JudgeRequest` below or propose changes before the API freezes |
| H+4 | `judge/providers/anthropic.py` v1 | One structured call per criterion returns a valid `Verdict` for any DEV chat; prompt version `v1` in `judge/prompts/` |
| H+6 | Gold set started | Labeling protocol agreed, 20 dialogs assigned (10 RU incl. 2 calls, 5 KZ, 5 EN), both of us label independently |
| H+10 | `eval/harness.py` | `python -m cqa.eval --gold eval/gold.json --provider anthropic --prompt v1` prints per-criterion exact match, MAE, quadratic-weighted kappa, total MAE, cost; appends to `eval/runs.csv` |
| H+12 | Gold set reconciled | Disagreements resolved together; human-human agreement recorded as the ceiling |
| H+14 | Local provider | vLLM on the university cluster serving an open model through an OpenAI-compatible endpoint, reachable from the API via a tunnel; `judge/providers/openai_compatible.py` passes the same harness |
| H+18 | Ensemble + arbiter | Majority/median merge, arbiter on disagreement, votes stored; harness shows whether it beats single Opus on validation |
| H+20 | Frozen control config | Provider mode, prompt version, effort, few-shot bank written to `judge/config/control.json` |
| H+23 | Control run executed | You run it with me; verify 10 cards; export CSV |

## The contract

```python
class Evidence(BaseModel):
    turn_idx: int                 # index into transcript as packed
    speaker: Literal["client", "operator", "bot", "system"]
    quote: str                    # verbatim, original language, ≤ 300 chars
    role: Literal["supports", "contradicts", "context"]

class Verdict(BaseModel):
    criterion_code: str           # "C1".."C8"
    applicable: bool              # False only if na_allowed and the N/A condition holds
    na_reason: str | None
    answer: str | int | None      # "yes"/"no" for binary, one of the listed steps for scale
    confidence: float             # 0..1
    evidence: list[Evidence]      # ≥1 for "no"/low scores; may be empty only with checked_span
    checked_span: tuple[int, int] | None   # where a missed action was expected
    explanation: str              # 1–3 sentences, in settings.explanation_language (default RU)
    flags: list[str] = []         # e.g. "possible_factual_claim", "late_turn", "asr_noise"
    claims: list[Claim] = []      # operator factual claims with status consistent|contradicted|unverified
```

`JudgeRequest` gives you: `conversation` (id, channel, language, turns with idx/speaker/text/timestamp/offset/segment), `criterion` (the organizer criterion with instruction, options, anchors, na_condition, channel override, compiled `judge_spec`), `fewshots` (list matched by language and channel), `acoustic` (for calls: pace, pauses, talk ratio) and `settings` (explanation language, PII masking on/off). You return `Verdict` plus `usage` (input, output, cache-read tokens) and the raw response for the trace. Validation (evidence indices exist, quotes fuzzy-match, answers in range, N/A has a reason, one re-ask) is in `judge/validate.py`; the scoring engine is not yours and never calls a model.

## The checklist you are judging against

Eight criteria from the organizer PDF (`api/seeds/checklist_freedom_v1.json`): C1 contact (Да/Нет, 10), C2 need discovery (Да/Нет/N/A, 15), C3 identification (Да/Нет/N/A, 20), C4 correct and complete consultation (20/15/10/5/0 or N/A, 20), C5 empathy (Да/Нет, 15), C6 dialog management, speed and literacy (10/7/4/0, 15), C7 closing (Да/Нет/N/A, 5), C8 gross errors (Да/Нет, no points, flagged). Ground truth will almost certainly be the organizers' own labels on these exact scales, so the target is agreement with a human reading the PDF literally, including its N/A conditions and its "Нет vs N/A" guidance on page 3. The PDF's own justification example is the style to imitate: «Завершение — Нет. После ответа на вопрос оператор закрыл чат без прощания. Основание — последняя реплика оператора и сообщение о закрытии чата».

## Data facts that matter (full profile in DATASET_NOTES.md)

- Chats follow one skeleton: client question, bot greeting, bot consent, one bot answer, client asks for an operator, escalation system event at index 8, operator from index 9 (or 10 if the client typed while waiting). Only the operator segment is scored; everything before is context the operator should have read (C2 depends on this).
- "Оператор закрыл чат" occurs five times, always after a bad exchange: strong signal for C7 = Нет and often C5 = Нет. "Соединение прервано" is neutral and makes C7 = N/A.
- "Личность клиента подтверждена" marks the four correctly identified cases; three dialogs disclose an account restriction without ID (C3 = Нет and C8 = Да).
- KZ and some EN dialogs cite Russian UI labels in guillemets. This is correct, not a literacy or language fault.
- Calls: IVR in garbled Kazakh, DTMF digits as client lines, PII masks, duplicates, hold blocks where two staff are both labelled operator, lines after "Клиент завершил разговор". Operator segment starts after "Агент присоединился к разговору с клиентом". Treat ASR garble as noise, not as C6 faults.
- Compliance violations (paid private advice, funds to a personal card) are paired with correct answers; C8 must be judged independently of C4.

## Ideas worth trying (yours to accept or reject)

1. **Transcript first, criterion second.** Pack the transcript as `[idx] speaker (ts): text` in the first user block and the criterion spec after it. On long dialogs the eight calls share a cached prefix and cost drops; on short ones nothing is lost.
2. **Decision steps instead of adjectives.** Compile each criterion into an ordered checklist the model walks ("1. Find the first operator turn. 2. Does it contain a greeting in the client's language? 3. Does it contain a name?") and make it output the step results before the answer. This is where most of the agreement gain usually comes from.
3. **Two-pass for C2/C3/C4.** First extract facts (list of client questions, which were answered where, what was disclosed, whether ID was requested), then score from the facts. Compare against single-pass on the harness; keep the winner per criterion.
4. **Hard N/A rules in code.** C7 = N/A when the terminal system event is a connection loss or client leaving; C4 = N/A when there is no client request before the end; C3 = N/A unless the dialog touches account data or protected actions. Let the model decide only inside the allowed region. Rule hits are recorded in `flags`.
5. **Cross-model voting, not resampling.** Claude 5.5 models have no temperature; consistency comes from Opus + Sonnet + the local model voting, with an arbiter call that sees the disagreeing rationales. Measure whether it beats single Opus before paying for it.
6. **Few-shots from the gold set and from reviewer decisions**, matched by language and channel, three per call at most. Watch for leakage: never put a validation dialog in the few-shots.
7. **Claims ledger for C4.** The ledger seed in DATASET_NOTES §4 lists consistent menu paths and policies; a knowledge base from public sources is being compiled into `docs/knowledge/`. Give the model the top-k relevant facts and ask it to mark each operator claim consistent/contradicted/unverified. Only "contradicted" should pull C4 down.
8. **Calls.** Add the acoustic summary (pace, long pauses, hold length, talk ratio) to C6, and the "hold etiquette" pattern (asked permission, thanked for waiting) to the decision steps. Decide whether to run `faster-whisper` for word confidence as a diction proxy; it is optional.
9. **Local model choice.** Qwen3-32B (dense) or Qwen3-30B-A3B (MoE, faster) via vLLM with `--guided-decoding` for JSON; Sherkala-8B-Chat is Kazakh-specialised but Llama-3.1-based and smaller. KazMMLU shows every open model is weaker in Kazakh, so measure KZ separately on the harness. Expose the endpoint through an SSH tunnel or cloudflared and give me the URL.
10. **Explanations.** The jury reads them; they must quote the PDF wording ("Нет — оператор …") and cite the turn. Ask the model to write the justification in the PDF's own format.

## Gold-set labeling protocol

1. Each of us labels the same 20 dialogs alone in `eval/gold_<name>.json` using the PDF literally: answer, N/A with condition, evidence turn index, one-line justification.
2. Compare, discuss every disagreement, write the resolved label and the reason into `eval/gold.json` and the reason into `eval/decisions.md` (these become decision steps in the prompts).
3. Record human-human agreement per criterion; that is the ceiling we report on the Analytics screen and in the deck.
4. Hold out 8 of the 20 (2 KZ, 2 EN, 1 call, 3 RU) for validation; never tune on them.

## Harness output format

```
criterion  n   exact  mae   qwk    na_agree  cost/dialog
C1         20  0.95   -     -      -         
C4         19  0.63   3.2   0.71   0.95
...
total MAE 4.8   cost $0.18/dialog   provider anthropic/claude-opus-5-5   prompt v3
```

## Observed on the first DEV run (Opus 5.5 single, v1 prompts, 2026-11-07)

- Run time about 20 s and about $0.20 per chat; 8 traces per dialog; all expected gross errors (ru-009, ru-022, ru-023, ru-029, kz-013, en-013, kz-015, en-014) were flagged with no false positives in the first 41 dialogs.
- Run-to-run variance exists on borderline criteria: `p1-ru-029` scored C2 = N/A, C7 = Да in one run and C2 = Нет, C7 = Нет in the next (70.00 vs 50.63). Claude 5.5 has no temperature, so the fix is decision steps that leave no room (C2 and C7 specs were tightened in `seeds/judge_specs_v1.json` and synced into version 1), plus voting. The ensemble supports repeating the same model: set `ensemble_members` to Opus, Opus, Sonnet in Settings to get three votes with the arbiter on disagreement.
- Start the harness from `eval/gold.template.json`: `uv run python -m cqa.eval.harness --gold eval/gold.json --split validation` scores the stored verdicts; add `--run --provider ensemble` to re-judge first.
- Criterion C7 is the most label-sensitive: the organizer PDF says "подвёл итог или предложил дальнейшую помощь и попрощался", yet most synthetic "good" chats end with a bare farewell after the client confirms. The current spec treats a farewell with no open question as Да. If the gold set shows the organizers read it literally, flip step 6 of the C7 spec and re-run.

### Run-to-run consistency (12 dialogs, Opus 5.5 single, v1 → v1 re-run)

Per-criterion agreement between two independent runs: C1 12/12, C2 10/12, C3 12/12, C4 8/12, C5 11/12, C6 11/12, C7 12/12, C8 12/12. Mean |Δtotal| 4.55, max 16.88 (en-007: C2 N/A→Нет, C5 Нет→Да, C6 4→7). C4 flips are always 15↔20, so the "второстепенная деталь" boundary is the biggest lever: the C4 spec now requires naming the missed detail for 15, otherwise 20. C2 flips are N/A↔Нет when the first answer is wrong on a clear request; the spec now sends that case to C4 and keeps C2 = N/A. Re-measure after your gold set; the 3-vote ensemble is expected to remove most of the remaining flips.
