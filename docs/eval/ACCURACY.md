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

Per-criterion agreement with the provisional gold, 7 of 9 labeler batches. Every dialog has at least two lenses, and 24 cells were still missing at the time. The same scratch copy of the hosted data was used throughout, and only the changed criterion was re-judged with the real Claude judge (claude-opus-5-5).

| Criterion | Hosted judge (spec before) | New specs | Note |
|---|---|---|---|
| C1 Установление контакта | 100% | 100% | |
| C2 Выявление потребности | 83% | **95%** | the judge called «Да» whenever the answer used the client's details, where the checklist's own N/A condition («данных для ответа хватало с самого начала») applies |
| C3 Идентификация | 97% | 97% | |
| C4 Консультация | 79% | 79% | **unchanged on purpose**, see below |
| C5 Эмпатия | 95% | 95% | |
| C6 Ведение диалога | 88% | 88% | memo rule added; neutral on DEV |
| C7 Завершение | **53%** | **98%** | see below |
| C8 Грубые ошибки | 100% | 100% | |
| **All cells** | **87.9%** | **93.9%** | 456 compared cells |

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

**C4 was not touched.**
- The judge gives 15 where the panel gives 20 in 9 chats. It cites concrete misses: no deadline for an escalated question, a bond name asked for but not obtained, an implicit answer to a yes/no question.
- The synthetic DEV set may well contain deliberate "minor missed detail" cases.
- Without the organizers' labels, moving to the more lenient reading is a coin flip, so the judge stays as it is.

**Cost of the experiment:** $2.04 (C7) + $3.57 (C2 and C6) + $1.80 (C2 refined), on a scratch copy; the hosted instance was not touched.

## How to reproduce

```bash
scripts/rehearse_local.sh                     # or start a scratch API on a copy of the data, see the script
api/.venv/bin/python <scratchpad>/acc/run_criterion.py http://127.0.0.1:8020 out.json C7
api/.venv/bin/python <scratchpad>/gold/compare.py gold.json out.json --detail C7
```

The gold set and per-lens labels are in `api/eval/gold_ai_panel.json` (`labels` per lens, `gold` by majority).

## Rolling it back

The spec change is a single commit, «Judge specs: C7 polite goodbye…». `git revert` it, then redeploy. Version 1 re-syncs the specs from the repo on start-up, so new evaluations follow at once. Stored evaluations change only when re-judged.
