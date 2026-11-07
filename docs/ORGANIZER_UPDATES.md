# Organizer updates and our decisions (2026-11-07, ~17:30)

Sources: the results appendix PDF (`CQA_Student_Guide_Appendix_Results.pdf`, git-ignored in the repo root), organizer answers in the Telegram chat, and the organizer's example mockup (`docs/design/organizer-mockup.png`). This file is the single place every agent and teammate reads for these rules.

## 1. Result files are a hard deliverable

Two files in the repository root, committed and **pushed** before the stop-code:

```
results/dev/results.json    DEV: 60 chats + 5 calls (65 records)
results/prod/results.json   control: exactly 10 chats, including errors
```

Format "JSON 1.0" (field names, criterion codes and value types must not change):

```json
{
  "schema_version": "1.0",
  "team_name": "Я Тебя Могну",
  "model": {"mode": "external_api", "name": "claude-opus-5-5"},
  "results": [
    {
      "conversation_id": "<exact id from the API>",
      "status": "ok",
      "criteria": [
        {"criterion_id": "contact", "value": true, "rationale": "...", "evidence_turns": [10]},
        {"criterion_id": "discovery", "value": null, "rationale": "...", "evidence_turns": [1]},
        {"criterion_id": "identification", "value": null, "rationale": "...", "evidence_turns": [1]},
        {"criterion_id": "consultation", "value": 15, "rationale": "...", "evidence_turns": [11, 12]},
        {"criterion_id": "empathy", "value": true, "rationale": "...", "evidence_turns": [10]},
        {"criterion_id": "dialogue", "value": 7, "rationale": "...", "evidence_turns": [11]},
        {"criterion_id": "closure", "value": true, "rationale": "...", "evidence_turns": [12]},
        {"criterion_id": "critical", "value": false, "rationale": "...", "evidence_turns": [10, 11, 12]}
      ],
      "score": {"final_score": 85.38, "critical_flag": false}
    }
  ]
}
```

| criterion_id | Our code | value | Weight |
|---|---|---|---|
| contact | C1 | true / false | 10 |
| discovery | C2 | true / false / null | 15 |
| identification | C3 | true / false / null | 20 |
| consultation | C4 | 0, 5, 10, 15, 20 / null | 20 |
| empathy | C5 | true / false | 15 |
| dialogue | C6 | 0, 4, 7, 10 | 15 |
| closure | C7 | true / false / null | 5 |
| critical | C8 | true / false | — |

Rules:
- `true` = Да, `false` = Нет, `null` = N/A. Scale values are the raw step before weighting (dialogue 7 means 7 of 10).
- `status: "ok"` needs all 8 criteria, each exactly once.
- `evidence_turns` are **1-based indices of the original transcript array**, counting client, bot and system events (our `Turn.idx + 1`). Never renumber operator turns only.
- `final_score`: 0–100, two decimals, N/A weight redistributed proportionally (our scoring engine already does exactly this; half-up rounding confirmed by the organizers: 50.625 → 50.63).
- `critical_flag` equals the value of `critical`; it does not zero the total.
- `model.mode`: `local`, `external_api` or `hybrid`; `model.name` lists every model actually used.
- A dialog that could not be evaluated is kept: `status: "error"`, `criteria: []`, `score: null`, `error: "<reason>"`. Never hide it, never replace it with zero.
- UTF-8, no comments, no trailing commas; IDs exactly as in the API; prod has exactly 10 records; no duplicates.
- The control set (10 chats) arrives one hour before the stop-code; the hour covers fetching, evaluation, validation, commit and push.

Implementation: `api/cqa/export/results_json.py` (builder + validator), CLI `python -m cqa.export.results_json`, endpoint `GET /api/export/results.json`, a control-run script that fetches → evaluates → exports → validates → commits → pushes, and an "Export results.json" button in the UI.

## 2. Organizer answers about the checklist

| Question | Organizer answer | Our decision |
|---|---|---|
| Comparison with ground truth | Per criterion (each answer), not the total | Optimise per-criterion agreement; totals follow from the engine |
| Control set content | Only chats | Control-run script assumes chats; calls stay supported in the product |
| Replacing a criterion at the control check | "How would you do it?"; already processed control dialogs must be re-evaluated | Add or replace a criterion in Checklist Studio, set its weight (weights are relative; the engine normalises to 100 automatically and the UI shows the sum and offers "normalise to 100"), save a new version, apply with partial re-evaluation (only the new or changed criterion is re-judged, totals recomputed) |
| Calls, C1: greeting not in the recording | First N/A; on 2026-11-08 (JSON 1.0 allows only true/false for contact): "DEV calls are for the UI, the control set has no calls, your choice; justify it for the business" | C1 has no N/A on any channel. If the operator's first turn was said before «Запись и транскрибирование запущены», a code rule (not the model) answers Да, quotes the text the transcript kept and flags the call `rule_contact_outside_recording` (§5). A masked name `[Имя скрыто]` counts as introduced |
| Calls, C3: is asking ФИО and address/phone enough? | "How would you implement it?" | Identification = the operator requests and receives at least two identifiers (ФИО + ИИН/address/phone) or an SMS code before disclosing account data; masked identifiers count as provided |
| C4: is there a knowledge base? | Yes, the company has one; the system should act as an indicator when something went off-script, for appeals and for reworking the answer script | Claims ledger + public knowledge base; the card shows "deviations from the knowledge base" as an indicator; unverified claims are marked, never invented |
| C7: answered and said goodbye ("Пожалуйста. Хорошего вечера!") without summary or offer of help | "N/a" | **Ambiguous.** It reads either as "N/A" or as "no answer". Current judge spec scores a farewell with no open question as Да. The ML owner decides after the gold-set comparison; see `docs/HANDOFF_ML.md` |
| C2: client gave all details up front (partly to the bot), operator answered without clarifying | At our discretion | Keep N/A ("data sufficed from the start") |
| C3/C8: account data disclosed before identification | No answer | Keep both: C3 = Нет and C8 = Да (the checklist lists disclosure of confidential information as a gross error) |
| Rounding | Half up, two decimals | Implemented |
| How the project is run at the review | Local or hosted, as long as the UI can be clicked in real time | Hosted (Vercel + Railway) is primary, local Docker as fallback |

General guidance from the organizers: they do not expect a perfect match with the experts; they look at closeness and adequacy. N/A, gross errors, incomplete data and disputed statements must be handled sensibly; when information is insufficient the system must not invent facts. For calls, account for spoken-language specifics; perfect recognition is not expected. Do not spend time guessing their internal method.

## 3. What the jury will ask live (the presentation is mostly a live product demo)

1. "Show all dialogs where the system found gross errors and explain why they were classified so." → a dedicated gross-violations view listing each dialog with the violation type (insult, paid service bypassing the company, disclosure of confidential information), the explanation and the quoted turn, linked to the card.
2. "Show how an operator can send an evaluation result to appeal." → an **Operator** role that sees its own dialogs and files an appeal on a criterion; the reviewer resolves it.
3. "Show how to change the checklist: add a new criterion, change an existing one, or change its weight." → Checklist Studio must make add / edit / reweight obvious, with immediate re-apply.
4. "Show the results for a specific dialog, including the justification of the scores." → the conversation card.
5. They may ask for hands-on access for a few minutes to click through scenarios themselves: no dead ends, clear labels, safe defaults, demo data present, nothing that can break the shared instance irreversibly.

They value: a working end-to-end process, analysis of new dialogs, reasoned and reproducible scores, usability, completeness of the process, interesting extras, technical approach, and the local/cloud balance. A couple of slides are fine; the main part is the live product.

## 4. Visual style guidance (applies to the whole product)

The audience is a large company: directors, managers, QA specialists and regular employees. The interface must look like a serious corporate system. Cartoon characters, superheroes or game-like styling would likely be rejected by the customer. Creativity is welcome, but think as a product maker for a corporate client.

Decision:
- The product switches to a **corporate light theme by default**, following the organizer's example mockup (`docs/design/organizer-mockup.png`): a deep-green sidebar (Freedom green #0F5431 family) with white nav text and a highlighted active item, a light neutral background, white cards with soft borders and 12–16 px radius, bold dark headings, green accents (#2F8F2A / #51AF3D), a peach highlight for the evidence fragment, a score ring "85,5 / 100", criteria rows with green check / amber cross icons and "15 / 20" points, a "Почему снижена оценка" summary, and primary/secondary buttons "Перейти к фрагменту" / "Оспорить оценку". For calls: a two-track waveform (Клиент / Оператор) with the evidence fragment highlighted and a player bar.
- Dark theme stays available as an option, not the default.
- **The mascot is removed from the product UI and the landing page by default.** The recolored mascot stays in Figma and in `web/public/mascot/` and can be turned on in Settings → Appearance ("Brand mascot", default off) for informal presentations.
- The landing page follows the corporate tone: product screenshots and real numbers instead of characters; the Figma Landing structure (hero, problem, how it works, features, languages, security, comparison, FAQ, call to action, footer) is reused with corporate visuals.

## 5. Organizer answer 2026-11-08: contact is true/false, calls are ours to decide

The question was: JSON 1.0 allows only true/false for `contact`, while N/A was allowed earlier when the greeting is not in a call's recording. What goes into results/dev/results.json for such DEV calls?

The answer:
- DEV calls are there mainly for the UI, not for comparing results.
- The control set has no calls.
- Use the transcript, the recording or our own approach.
- At the presentation, show why the decision matters for the business. A reasoned approach can earn a small plus; an irrational one, a small minus.

Our decision. The principle: a deduction needs a fragment one can listen to and contest.
- `cqa/judge/validate.py` (`opening_outside_recording`) decides C1 on calls by rule when the operator's first turn has no audio offset, i.e. it was said before the recording-start event.
- The answer is Да, confidence 1.0, with the turn quoted as context evidence and the flag `rule_contact_outside_recording`.
- The explanation names the recording-start turn and the seconds lost.
- The model's own answer for C1 is ignored for such calls, so there is no retry and the result is identical on every run.
- The checklist seed's C1 call override lost `na_allowed`, and version 1 re-syncs on start-up.
- The results validator now treats a null contact as an error on every channel.
- In DEV this concerns only p4-ru-002: the recording starts 30.2 s in, and the operator's opening survives only in the text. Its C1 becomes Да; the total is 90,50, against 89,44 with N/A and 80,50 with Нет.
- Pitch arguments: `docs/PITCH.md` §06a.

