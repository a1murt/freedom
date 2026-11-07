# Judge accuracy against an AI-panel gold set (2026-11-08 night)

**Why.** The top-50 cut is decided mainly by per-criterion agreement with the organizers' hidden labels on 10 control chats. No human gold set had landed on main by 02:00, so the architecture side built an independent AI-panel gold set and measured the judge against it.

## Method

- **Data:** the 60 DEV chats (30 RU / 15 KZ / 15 EN), exported from the hosted database with 1-based turn numbers.
- **Labelers:** three, each with a different lens:
  - `literal`, a pedantic auditor who applies the text word for word;
  - `practitioner`, a contact-centre QA expert;
  - `organizer`, "the answer most likely in the organizers' gold".
- **Labeler input:** the checklist verbatim (all 3 pages of the PDF) and the organizers' raw chat answers. They never saw our judge's answers or specs.
- **Gold:** the majority answer per criterion; a reconciler decides the 3-way splits.
- **Second opinion:** ChatGPT Pro on the grey zones (`SECOND_OPINION_CHATGPT.md`).
- **Caveat:** this is not the organizers' gold. It is the best independent reading available at night, so a disagreement is a lead to inspect, not proof.

## Results

The final gold set: 9 of 9 labeler batches, 423 of 480 cells unanimous across the three lenses, and 1 three-way split decided by the reconciler. The hosted judge was measured before and after the spec deploy (build 4ab648a). In the "after" column, 56 chats were re-judged on C2/C6/C7 with the real judge for ,61. The four demo dialogs (p1-ru-029, p1-ru-005, p1-kz-005, p1-en-007) were left untouched for the jury script, so they keep their earlier C2/C6/C7.

| Criterion | Hosted judge, old specs | Hosted judge, new specs | Remaining differences |
|---|---|---|---|
| C1 Установление контакта | 100% | 100% | |
| C2 Выявление потребности | 83% | **98%** | p1-ru-029 (demo, old spec) |
| C3 Идентификация | 97% | 97% | 2 |
| C4 Консультация | 78% | **82%** | our own "name the deadline" requirement removed (876c1de); the knowledge-base 15/20 calls stay |
| C5 Эмпатия | 93% | 95% | tone and stated complaints only (876c1de); within run-to-run noise |
| C6 Ведение диалога | 85% | **90%** | 6 |
| C7 Завершение | **40%** | **95%** | 3, one of them the demo dialog p1-ru-029 |
| C8 Грубые ошибки | 100% | 100% | |
| **All 480 cells** | **84,6%** | **94,6%** | **+10,0 points** (specs 146d9c6 + 876c1de) |

The lab runs on a scratch copy, measured against the provisional gold, showed the same picture: C7 53% → 98% and C2 83% → 95% over two runs.

**C7 is the big one.**
- The organizers were asked about «Пожалуйста. Хорошего вечера!» with no summary and no offer of help, and they answered «N/a». Our spec listed exactly that as a «Да» example.
- 42 of the 43 DEV chats that the judge scored «Да» close this way: «You're welcome. Take care!», «Оқасы жоқ. Сау болыңыз!», «Хорошего дня!».
- The control chats come from the same generator, so this decides C7 on most of them.
- Two of three lenses chose N/A, and the literal lens chose «Нет» while citing the organizers' «N/a». ChatGPT Pro predicts N/A with 85% confidence.
- The new spec:
  - **Да:** goodbye + (summary or offer of help);
  - **N/A:** a polite goodbye with neither, or a broken-off chat;
  - **Нет:** no goodbye, or a goodbye with an open question.

**C2:** two runs of the new spec gave 95% both times. The remaining 2–3 misses move between chats from run to run, which is normal judge variance.

**C4: only our own extra rule was removed.**
- The judge gives 15 where the panel gives 20 in 9 chats. It cites concrete misses: no deadline for an escalated question, a bond name asked for but not obtained, an implicit answer to a yes/no question.
- The synthetic DEV set may well contain deliberate "minor missed detail" cases.
- Without the organizers' labels, moving to the more lenient reading is a coin flip, so the judge stays as it is.

**Cost of the experiment:** $2.04 (C7) + $3.57 (C2 and C6) + $1.80 (C2 refined), on a scratch copy; the hosted instance was not touched.

## Single judge vs ensemble (for the control run)

The same final specs, 27 chats (a balanced half without the demo dialogs) and the five criteria where the single judge varies between runs (C2/C4/C5/C6/C7):

| | Agreement with gold | Cost per chat (these 5 criteria) |
|---|---|---|
| Single Opus (`--provider anthropic`) | 127/135 = 94,1% | ≈ $0,13 |
| Ensemble Opus ×2 + Sonnet + arbiter (`--provider ensemble`) | **129/135 = 95,6%** | ≈ $0,38 |

The ensemble wins one cell on C4 and one on C6, and ties on the rest. The gain is small but goes the right way, and majority voting lowers run-to-run variance, which matters on 10 chats. Recommendation: `control_run.py --group <id> --provider ensemble`. That costs about $6 for 10 chats.

## How to reproduce

```bash
scripts/rehearse_local.sh                     # or start a scratch API on a copy of the data, see the script
api/.venv/bin/python <scratchpad>/acc/run_criterion.py http://127.0.0.1:8020 out.json C7
api/.venv/bin/python <scratchpad>/gold/compare.py gold.json out.json --detail C7
```

The gold set and per-lens labels are in `api/eval/gold_ai_panel.json` (`labels` per lens, `gold` by majority).

## Rolling it back

The spec change is a single commit, «Judge specs: C7 polite goodbye…». `git revert` it, then redeploy. Version 1 re-syncs the specs from the repo on start-up, so new evaluations follow at once. Stored evaluations change only when re-judged.

## Independent confirmation (ML teammate, PR #1, merged 03:50)

The ML teammate built a second gold set: 65 DEV dialogs including calls, three blind labelers plus arbitration (`api/eval/gold.json`, `api/eval/PROTOCOL.md`). Against it:

- The two gold sets agree on **95,6%** of the shared chat labels. Most disagreements are in C4 and C6.
- The deployed specs (876c1de), single Opus, score **0,936** on the ML gold, 0,938 on the panel gold and **0,984** on the ML holdout. Before the night's spec work: 0,854 / 0,844 / 0,859.
- The C7 bare-farewell policy switch (default `na`) with the merged code scores **97%** on C7 in the lab and 95% on hosted, which is run-to-run variance.
- The harness reads both gold formats: `uv run python -m cqa.eval.harness --gold eval/gold_ai_panel.json --api <hosted>`.

## Round 7: three narrow fixes where both gold sets agree (2026-11-08, 04:00–04:35)

**Method.** We listed every chat cell where the two gold sets agree with each other and the deployed judge does not: 14 of 480. We set aside:
- p1-ru-029, a live-demo dialog (C2, C7);
- three cells where the judge follows a rule the team lead set on purpose (p1-ru-002 C3, p1-ru-014 C5, the «Ну, тогда сайт» = 7 example).

The rest fall into three patterns. Each fix was measured in the lab on all 60 chats against both gold sets before it shipped (`ce25bbe`):

| Criterion | Fix | ML gold (lab) | Panel gold (lab) | Fixed / broken |
|---|---|---|---|---|
| C3 | A guess or general rule without opening the account is not disclosure → N/A | 58 → 59 | 58 → 59 | 1 / 0 |
| C4 | A reference-fact caveat the request did not need is not a missed detail; a step needed for the client's goal still is | 54 → 56 | 49 → 51 | 2 / 0 |
| C6 | The improper offer itself (card transfer, paid advice, personal number) is C8 and does not lower C6 when it is clear | 50 → 53 | 54 → 56 | 2 / 0 |

- **C6 took three attempts.** Wider wordings broke contradictory replies (should be 0) and forced re-asks (should be 4).
- **The C4 D-account case.** The first C4 wording forgave a missing "transfer between accounts first", which the ML labelers marked 15 unanimously. The final wording names that case.

**Hosted, after re-judging C3/C4/C6 on 56 chats (1141be2, $5,61):**
- Panel gold: **94,6% → 95,0%**. ML gold: **94,8% → 95,2%**.
- 5 cells fixed (p1-ru-018 C3, p1-kz-012 C4, p1-ru-015 C4, p1-ru-022 C6, p1-en-013 C6).
- 3 cells moved the other way. p1-en-008 C4 and p1-kz-015 C4 sit at the 15/20 and 5/10 borders, and their new rationales do not use the new rule. p1-ru-018 C6 sits at the 0/4 border (the ML arbiter itself moved it from 4 to 0).

**Variance.** Two single-Opus runs with the same specs differ on about 2 of 60 C4 or C6 cells. That variance is the reason the control run uses the ensemble.

**Totals.** The MAE of the final score against the gold totals was computed with the export's own scoring (`recompute_final_score`):
- panel gold: 3,20 → **2,89** (exact totals 40 → 43 of 60);
- ML gold: 3,19 → **2,87** (43 → 45).

**Final configuration, rehearsed** (ensemble, 10 DEV chats, 04:33): 98,8% panel / 95,0% ML gold, against 97,5% / 93,8% for hosted single Opus on the same chats.
