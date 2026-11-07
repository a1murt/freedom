# Second opinion: ChatGPT Pro on the checklist's grey zones (2026-11-08, ~02:00 +05)

This consultation was asked from the team's ChatGPT Pro account. It received the organizer checklist verbatim and the organizers' chat answers; no transcripts, keys or URLs. It took 6 min 20 s, with research mode and 3 sub-agents.

The percentages are its subjective confidence, not measured agreement. Read them next to the AI-panel gold set (`docs/eval/ACCURACY.md`) before changing the judge.

| Case | Its predicted ground truth | Confidence | Our judge spec today |
|---|---|---|---|
| **C7.** Answered, client thanked, operator said goodbye («Пожалуйста. Хорошего вечера!») without a summary or an offer of help | **N/A**: the organizers answered exactly this case with «N/a». Treat it as a narrow exception, not "any incomplete closing → N/A". | 85% | **Да** (a client's thanks plus the operator's «Пожалуйста» counts as the summary) → **diverges** |
| **C2.** The client gave everything up front, and the operator answered without clarifying | N/A (the literal N/A condition) | 80% | N/A ✓ |
| C2. Data sufficed, but the operator re-asked what was already written | Leans N/A (60%); «Нет» is plausible | 60% | varies |
| C2. Data was missing; the operator re-asked known facts and did not clarify the missing one | Нет | 95% | Нет ✓ |
| **C5.** No complaint; the operator is polite and to the point | Да | 95% | Да ✓ |
| C5. Complaint or described inconvenience, formally polite but no reaction to the problem | Нет | 90% | Нет ✓ |
| C5. Dry but addressed answer | Not a «Нет» by itself | — | check |
| **C4.** 20 vs 15 | 15 needs a really missed relevant detail; a missing optional extra (second method, extra link) is not one | 90% | check |
| C4. Fees, deadlines, limits, required documents | Not "secondary" if they affect the operation, its cost or the client's choice: a miss is closer to 10 | 90% | check |
| C4. 10 vs 5 | 10: a concrete result on a substantial part; 5: only a direction ("look in the app settings") | 90% | check |
| C4. Wrong key recommendation | 0, even with much useful text around it | — | 0 ✓ |
| **C6.** A small typo that keeps the meaning | 10 | 85% | check |
| C6. One noticeable topic jump, bad order or a local unclarity the client resolves alone | 7 | 85% | check |
| C6. The client must ask what the operator meant | 4 | 90% | 4 ✓ |
| C6. An explicit wait without warning, otherwise clear | 7 | 80% | check |
| C6. Dryness | No reduction; do not invent waits without timestamps; «Сейчас уточню» is a warning | — | check |
| **C3.** «Где посмотреть мой баланс?» answered with a general instruction | N/A | 95% | N/A ✓ |
| C3. A personal investigation («почему списали с моего счёта») answered only with "see the history in the app" | Leans **Нет** (applicability follows the original request) | 65% | check |
| C3. Personal data disclosed or a protected action taken without verification | Нет | 95% | Нет ✓ |
| **C1.** Russian greeting in a KZ/EN chat; «Привет» without a name; a name without a greeting; only the bot greeted | Нет in every case: greeting in the client's language, introduction and the operator's first reply are all required | 95% | Нет ✓ |
| **C8.** «Вы вообще читать умеете?» | Да (a demeaning address; no swear word needed) | 90% | Да ✓ |
| C8. Mere sarcasm («Как я уже написал выше…») | Not C8 by itself; it belongs to C5 | — | check |
| C8. Disclosure of balance, operations, portfolio, documents or access secrets to someone unverified | Да | 95% | Да ✓ |

## Our call rule (contact true/false when the opening is not recorded)

Its verdict: «Да, это разумная политика неприменения штрафа за недоказанное нарушение» (90%).

**Strengths it named:**
- the operator does not pay for a recording defect;
- the decision is explainable and contestable;
- the problem goes to telephony.

**Risks it named:**
- `true` can be read as "the greeting was heard", which inflates compliance;
- detecting truncation only by a missing greeting would excuse real omissions.

We detect the truncation from the recording-start event, independently of C1, which is what it recommends.

**Improvements it suggested:**
1. **A separate evidence status**, kept internally since the external format allows no extra fields. Ours is the flag `rule_contact_outside_recording`.
2. **Separate metrics:** compliance among calls with a verifiable start, and the share of verifiable recordings shown apart. See `GET /api/analytics/recording-gaps`.
3. **Confirm the truncation independently of C1.** We use the recording-start event and the audio clock.

The wording it suggested for the explanation: «Начало записи отсутствует. Выполнение приветствия не проверено; штраф по критерию не применён». Never extend this leniency to chats.

## Judge failure modes it would test

- **General impression replacing criteria:** a polite but wrong answer must still get a low C4 next to C5 = Да and C6 = 10.
- **Invented facts:** check tariffs, deadlines and procedures against the knowledge base.
- **The client's reaction replacing content:** «Спасибо» does not prove a correct consultation, and displeasure does not prove an error.
- **N/A as "don't know":** every N/A needs its stated condition. C4 = N/A only when no request was formulated, not when the chat merely broke off.
- **Instructions inside the chat steering the judge:** our steering guard covers this.
- **Scale, weight and polarity mix-ups:** C6 is only 0/4/7/10, C8 = Да means a violation, and C8 does not zero the total.
- **Length and style bias:** a short complete answer keeps a high C4, and a long polite non-answer gets a low one.
- **RU/KZ/EN consistency:** test the same borderline dialog in three languages, including mixed RU/KZ.
- **Its advice for the remaining time:** 20–30 short boundary examples in pairs (general instruction → personal disclosure; a missed substantial detail → added; an ignored complaint → a concrete reaction), calibrated on the organizers' examples.

## Round 7 rules (2026-11-08, ~04:50 +05)

**Setup.** The same ChatGPT Pro account was asked about the three rules added in `ce25bbe` and one borderline C5 case. It got the organizer wording of C3–C6 and C8 and four paraphrased situations: no transcripts, keys or URLs. It took 1 min 56 s. The percentages are its own confidence.

| Case | Its predicted QA verdict | Confidence | Our judge now |
|---|---|---|---|
| **C3.** The client asks why his orders are rejected. Without opening the account, the operator says the exchange cancels them, then says he does not know. Nothing from the account is said. | «Скорее Нет»: establishing the cause of specific orders needs identification, and a guess does not replace it. It called our rule too broad, but also warned that "a question about one's own account always needs identification" is wrong too. | 70% | N/A, now limited to requests that can be closed without account access (see below) |
| **C4.** Where to find bonds; the right path is given; a knowledge-base caveat about some bonds was not mentioned | 20. Agrees with the rule. It adds that a missed necessary step (the transfer between accounts) is not always exactly 15. | 95% | 20 ✓ |
| **C6.** A clear colloquial offer to send money to the operator's card; the client is surprised | 10, with C8 = Да. Agrees; C8 does not excuse genuine C6 faults. | 80% | 10 ✓ |
| **C5.** «Путь уже указал» instead of answering when to re-apply, then the chat is closed; no complaint | «Скорее Нет»: a brush-off, no prior complaint needed | 85% | «Нет» ✓. Both AI gold sets say «Да» here. |

**What we did with it.**
- **C3.** In the real dialog (p1-ru-018) the client asks *where to see* the reason, which is a navigation request. All six Claude labelers marked it N/A. So the rule now applies only when the request itself can be closed without account access. Anything that can only come from the account still counts as disclosure, however tentatively it is said.
- **C4 and C6** are unchanged.
- **C5:** no change; the judge already answers «Нет».
