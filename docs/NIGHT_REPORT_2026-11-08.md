# Night report, 2026-11-08 (00:30–03:00 +05, architecture side)

For the team's morning sync. Everything below is on `main` and deployed (hosted build **876c1de**).

## What changed overnight

**1. The organizers' answer on calls (contact true/false only).**
- C1 on a call whose opening the recording missed is decided by a code rule: «Да», which means "no penalty applied", not "greeting heard". The call is listed as a recording gap (`GET /api/analytics/recording-gaps`).
- Two independent reviews found problems, and all of them were fixed.
- Arguments for the pitch: `docs/PITCH.md` §06a. ChatGPT Pro's opinion: `docs/eval/SECOND_OPINION_CHATGPT.md`.

**2. Judge accuracy: +10 points against an independent gold set** (`docs/eval/ACCURACY.md`).
- Three AI labelers marked all 60 DEV chats, seeing only the checklist and the organizers' answers.
- The hosted judge went from **84,6% → 94,6%** per-criterion agreement.

| Criterion | Change | Effect |
|---|---|---|
| C7 | Polite goodbye with no summary and no offer of help → N/A. This is the organizers' own «N/a» answer; we had it as «Да». | 40% → 95% |
| C2 | An answer that merely uses known details → N/A, as the checklist says | 83% → 98% |
| C4 | Removed our own "must name a deadline" rule for escalations | 78% → 82% |
| C5 | Unanswered questions are C4; «я уже писал выше» needs an acknowledgement | 93% → 95% (within noise) |
| C6 | Memo rule: an unanswered question is C4, not C6 | 85% → 90% |

**3. Robustness.**
- **Control run:** it always exports. A timed-out or refused job leaves valid status=error records, and `--only-incomplete` finishes them later.
- **Rehearsed with the real judge:** 10 chats in 48 s, $2,23, valid, 95% agreement.
- **Backend fixes, reviewed independently:**
  - violation types ignore negated lists (RU/EN/KK);
  - partial re-runs carry decisions and move open appeals;
  - CSV formula-injection guard on every CSV export;
  - PDF turn numbers;
  - the disk warning judged by how many snapshots still fit;
  - answer matching accepts «15.0» and «true».
- **CI on `main`** had been red since d6c55d5 (a timing test); it has been green since 607c004.

## Hosted state (03:00)

- 876c1de: smoke 18/18, live asks 7/7, readiness «Готово к контрольному часу и показу».
- **The four demo dialogs keep their numbers:**
  - p1-ru-029 is 51,25 with the insult at turn 10;
  - p1-ru-005's appeal preview is 78,46 → 85,38;
  - p1-kz-005 has the open appeal and p1-en-007 the decided one.
- **The C7 weight what-if** now changes 20 dialogs (mean 83,10 → 82,72), because C7 is N/A on most polite goodbyes.
- **Snapshots:** `baseline` (retaken), `pre-specs-4ab648a`, `pre-deploy-round4` and `post-deploy-d6c55d5`. All are copied to `api/data/offsite/` on the team lead's Mac; the folder is git-ignored.
- **Railway:** Trial, $4,96 of credit left, volume 500 MB with about 250 MB free. Nothing needs buying.
- **Claude budget:** about $30 spent tonight on experiments and re-judging. The hosted daily guard has about $34 left in the window.
- **The organizers' API URL** answers: DEV returned 65 records in 2,3 s.

## Decisions you may want to revisit (each is one commit to revert)

- C7 = N/A for polite goodbyes (`146d9c6`). The evidence is strong: the organizers' answer, 2 of 3 labelers and ChatGPT agree.
- C2 = N/A when the data sufficed (`146d9c6`).
- C4 and C5 refinements (`876c1de`).
- The C1 call rule (`89a1b34` and later). The only alternative the format allows is `false`, which would give p4-ru-002 80,50.

## Morning checklist (before the 12:00 freeze)

1. ML teammate: read `docs/HANDOFF_ML.md` «Judge spec changes made at night». Accept, or revert specific criteria.
2. Frontend: the call banner text and the «Засчитано правилом» badge (`docs/FRONTEND_INTEGRATION.md`, «Calls»); the appeal dialog offers only Да/Нет for C1.
3. `GET /api/system/readiness` is green, and `scripts/smoke_asks.py` gives 7/7.
4. The Git link has been sent to the organizers.
5. 13:00: `api/.venv/bin/python scripts/control_run.py --group <CONTROL_GROUP_ID> --provider ensemble`. Use `--source-url` or `--source-file` if they hand over a link or a file.
   - The ensemble measured +1,5 points over single Opus on the noisy criteria (27 chats, 95,6% vs 94,1%) and lowers variance.
   - It costs about $6 for the 10 chats.
