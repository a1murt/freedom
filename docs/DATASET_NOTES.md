# DEV dataset profile (65 records, pulled 2026-11-07)

Source: organizer Power Automate GET, `dataset_group_id = "DEV"`. 30 RU / 15 KZ / 15 EN synthetic chats (`p1-*`) and 5 real RU voice calls (`p4-ru-001..005`, returned out of order). No empty texts.

## 1. Topics and cross-language correspondence

| Topic | n | ids |
|---|---|---|
| Trading / app navigation (ticker search in «Котировки», rejected order, «Использовать маржу», D-счёт vs торговый) | 15 | ru-017/018/019/020/023/024, kz-009/010/011/012/013, en-008/009/010/013 |
| Login / access (info site has no sign-in, login taken after closure) | 9 | ru-014/015/016/029, kz-007/008, en-001/002/015 |
| Reports and documents (broker report, 1042-S language, holding financials) | 9 | ru-011/012/013/027, kz-006/014, en-004/011/012 |
| Deposits (requisites, bank commission, deposit not credited) | 8 | ru-004/005/006/026, kz-002/003, en-005/006 |
| Tariff / pricing plan | 7 | ru-001/002/003/030, kz-001, en-003 (+ru-027) |
| Withdrawals (blocked → «Изменение сведений», restriction) | 7 | ru-007/008/009, kz-004/015, en-007/014 |
| Personal data update | 2 | ru-028, kz-005 |
| Missing position | 1 | ru-010 |
| Market education (Nasdaq vs NYSE) | 1 | ru-021 |
| Investment-advice request (fraud variant) | 1 | ru-022 |
| Client leaves after greeting | 1 | ru-025 |

The RU/KZ/EN sets are not translations. Timestamp skeletons are reused by index (ru-004, kz-004, en-004 share timestamps) but topics differ. Scenario templates recur with the good/bad variant usually preserved:

- Tariff → «Кабинет»: ru-001 ≈ kz-001 ≈ en-003
- Requisites: ru-004 ≈ kz-002 ≈ en-005
- Deposit not credited (empathy + ID + escalation + follow-up): ru-006 ≈ kz-003 ≈ en-006 (ru-010 same skeleton for a missing position)
- Withdrawal blocked: ru-007 ≈ en-007 (ignore follow-up) vs kz-004 (answers it)
- Login taken: ru-015 ≈ kz-007 ≈ en-001
- Info site login: ru-014 ≈ kz-008 (informal, no name) vs en-002 (good) vs ru-016 ≈ en-015 (guess without clarifying) vs ru-029 (rude)
- Similar securities: ru-019 ≈ kz-009 ≈ en-008
- Margin checkbox: ru-020 ≈ kz-010 ≈ en-009
- D-account: ru-024 ≈ kz-011 ≈ en-010
- 1042-S: ru-012 ≈ kz-014 ≈ en-011
- Paid private advice solicitation: ru-023 ≈ kz-013 ≈ en-013
- Account looked up without ID: ru-009 ≈ kz-015 ≈ en-014
- Order rejected: ru-018 (bad) vs kz-012 (good)

## 2. Operator behaviour patterns

- Greeting: 57/60 give a name; only 3 name the company or department (ru-003, ru-025, en-001). No name: ru-014, kz-008, en-015 (no greeting at all).
- Closings: "Хорошего дня", "До свидания", "Сау болыңыз", "Have a good day". Nobody in chats asks whether questions remain. Weak: kz-005 "Жақсы.", en-015 "Bye.", ru-029 after rudeness; kz-008 has no closing. Five chats end with "Оператор закрыл чат" and always after a bad exchange.
- Identification: a system event "Личность клиента подтверждена" (and KZ/EN variants) appears only in ru-006, ru-010, kz-003, en-006. ru-009, kz-015, en-014 look at the account without ID and brush off the client's objection.
- Clarifying questions present in ru-002/003/009/010/017/019/030, kz-012, en-008; absent where needed in ru-016, en-015, ru-018.
- Ten chats have a client message between escalation and the operator's first line (index 9); good operators fold it in, bad ones ignore it (ru-003).
- Unresolved questions: ru-003, ru-007, en-007, ru-009, kz-015, en-014, ru-026.
- Follow-up promises with channel: ru-006, ru-010, kz-003, en-006. Vague "передам на проверку": ru-009, kz-015, en-014.
- Empathy phrases only in deposit/position scenarios (ru-002, ru-006, kz-003, en-006).
- Hold handling model: ru-028 ("Сейчас проверю путь" → "Спасибо за ожидание").
- Tone: rudeness ru-029; colloquial "Привет", "Сәлем", "Ну, тогда сайт", "Скиньте". No caps, no emoji, almost no typos.
- Language mixing: every KZ chat and some EN chats cite Russian UI labels in guillemets. This is correct behaviour (the app is Russian-labelled) and must not be penalised.

## 3. Failure-mode catalogue (chats)

Compliance / security / ethics
- ru-022: solicits money to a personal card, closes chat.
- ru-023, kz-013, en-013: asks for a personal phone and offers paid private advice after hours; kz-013 and en-013 close the chat on the client.
- ru-009, kz-015, en-014: disclose account restriction without identification.

Rudeness / tone
- ru-029: "Вы вообще читать умеете?"; no apology.
- ru-014, kz-008: informal greeting, no name, re-asks what was written.
- ru-016: "Ну, тогда сайт."

Unfounded or wrong information
- ru-018: asserts the exchange cancels the order without asking anything; "Тогда не знаю."; closes chat.
- kz-005: first instruction wrong (Profile → orders), corrected after pushback.
- ru-005: incoherent first instruction, then corrects itself.
- ru-016, en-015: guessed fixes without asking the platform.

Question ignored / premature close
- ru-003: commission never answered.
- ru-007, en-007: ignore "can I retry right after".
- ru-009, kz-015, en-014: ignore "why" and "will I hear back".
- ru-026: "Всё есть в приложении." then closes chat.

Weak greeting or closing: no name ru-014, kz-008, en-015; no closing kz-008; minimal closing kz-005, en-015, en-014 (with a question pending).

Near-perfect anchors: ru-006, ru-010, kz-003, en-006; ru-002; ru-004, ru-011, ru-017, ru-019, ru-020, ru-021, ru-024, ru-027, ru-028, ru-030; kz-001/002/004/006/007/009/010/012; en-001/002/004/005/008/010/011/012. Neutral: ru-025 (client leaves), ru-012/kz-011/en-009 (connection lost after resolution; do not penalise missing closing).

## 4. Operator factual claims (claims ledger seed)

1. Tariff name: web → «Кабинет», under ФИО; also broker report page 1 «Информация о счёте». Consistent.
2. Bank requisites: «Меню → Пополнить → Перевод по реквизитам», choose currency, download. Bank commission unknown to the operator (correct hedging in ru-005).
3. Broker report: «Меню → Отчёты и документы → Отчёт брокера», pick period.
4. Change personal details: «Меню → Поручения → Персональные данные → Изменение сведений»; wait for execution, then re-submit withdrawal. Contradiction: kz-005 first answer.
5. A used login stays reserved after closure; pick another.
6. Freedom Finance Global site is informational; log in at tradernet.global or the app.
7. Security search in «Котировки» by name or ticker; verify the card before ordering.
8. Margin: «Настройки приказа» → untick «Использовать маржу»; applies to the current order only.
9. D-счёт = savings; trading account = securities; D-account funds not directly usable to buy.
10. 1042-S is English-only.
11. Holding financials: site → Investors → Financial Information.
12. Unfounded: ru-018 exchange cancellation; ru-009/kz-015/en-014 restriction asserted without ID.
13. Calls: OTC-only securities cannot be traded in the order book; OTC fee "$25 minimum or 2%, whichever is greater" (said by colleague, not relayed); bonds via traders only, desk email and phone; support line from 09:00; tech support gives no investment advice; identification = ФИО + ИИН + address or 6-digit SMS code.

## 5. Voice calls

`audio_url` returns an HTML player page whose `<source src>` is a Dynamics MP3 (16 kHz mono, 64 kbps) with a SAS token valid about an hour. Durations: p4-ru-001 5:20 (45 turns), p4-ru-003 6:40 (78), p4-ru-005 5:52 (75), p4-ru-004 7:16 (115), p4-ru-002 10:49 (73, about 7 min on hold).

Structure: English language-switch note, "Запись и транскрибирование запущены", IVR bot (Kazakh greeting garbled by RU ASR), DTMF digits logged as client lines, consent notice, "Клиент переведен…", recording paused about 10 s, "Агент присоединился к разговору с клиентом", recording resumed. p4-ru-002 is an outbound callback with a hold and consultation block where both staff voices are labelled `operator` plus stray desk chatter. Calls end with "Клиент завершил разговор"; in 4 of 5 one more line is logged after it (in p4-ru-004 the closing question arrived after hang-up).

Noise: `[Имя скрыто]`, `[Персональные данные скрыты]`, `[Данные идентификации скрыты]` (p4-ru-004 has 14, with 6 consecutive duplicates); exact duplicate lines with identical timestamps; ASR garble ("уйти сделка" = "OTC сделка", "домой счёт" = "демо-счёт"); 30–70 s silences on hold.

Per-call quality
- p4-ru-001: correct compliance (no advice from tech support) but flat, no alternative offered, defensive ending, no closing.
- p4-ru-002: full re-identification, asks permission to hold, thanks for waiting, correct OTC explanation, proper closing; 7-minute hold, fee not relayed.
- p4-ru-003: initial misdiagnosis, interrupts then apologises, full ID incl. SMS code, escalates, promises callback with a time; no farewell.
- p4-ru-004: ID friction, falls back to SMS; correct bonds explanation but repeats the same sentence four times; closing question after hang-up.
- p4-ru-005: best structure; looks up by phone, explains timing, asks whether questions remain, thanks; vague timing, heavy ASR.

Call adaptations for the checklist: greeting includes company + name (mask-aware); identification = ID-mask or SMS exchange, not a system event; hold etiquette; exclude IVR and consultation blocks from tone scoring; lines after "Клиент завершил разговор" are late; ignore ASR typos and disfluencies; closing = "остались вопросы?" + farewell; callback promises need a timeframe; repetition without rephrasing and interruptions are call-specific communication flaws.

## 6. Engineering notes

- Chat timestamps `MM:SS` relative to the first client message, templated by index (latency carries no quality signal). Call timestamps ISO 8601 with 7 fractional digits (truncate to 6 for `datetime.fromisoformat` on older Pythons).
- Chat skeleton: client → bot greeting → bot consent → bot answer → client asks operator → bot offer → "да" → bot wait → system escalation at index 8 → operator at index 9 (or 10 when the client typed during the wait).
- Terminal system events: "Оператор закрыл чат" and variants (5×, always negative), "Соединение с клиентом прервано" and variants (3×, neutral), "Клиент покинул чат" (1×), "Личность клиента подтверждена" and variants (4×, positive ID signal).
- Speakers per chat: bot 5, system 1–2, operator 1–10, client 3–15. Calls: bot 0–4, system 7–8, client 14–65, operator 13–51.
- Longest chat ru-010 (32 turns), shortest ru-025 (11 turns).
- Voice operator segment starts after "Агент присоединился к разговору с клиентом"; mark turns between "Началась консультация с агентом" and "Агент вышел из разговора" as internal.
- Operator name is extractable from the first operator line in chats; masked in calls.

## 7. Organizer checklist mapped to the data (expected labels for the gold set)

The real checklist (`CQA_Chat_Quality_Checklist`) has eight criteria. Expected failures in DEV, to be confirmed by hand labeling:

1. **C1 Установление контакта** (Да/Нет, 10): greeted in the client's language and introduced themselves in the first reply. Expected Нет: ru-014, kz-008, en-015 (no name or no greeting).
2. **C2 Выявление потребности** (Да/Нет/N/A, 15): used known details and clarified what was missing before answering; N/A when the data sufficed from the start. Expected Нет: ru-016, en-015, ru-018 (guessed without asking), ru-014, kz-008, ru-026 (re-asked what was already written). Expected N/A: most simple navigation questions (ru-001, kz-001, en-003, ru-004…).
3. **C3 Идентификация** (Да/Нет/N/A, 20): identity confirmed before disclosing data or a protected action. Expected Да: ru-006, ru-010, kz-003, en-006 (system confirmation event), ru-002 (states ID is needed before looking). Expected Нет: ru-009, kz-015, en-014. Expected N/A: all general tariff, navigation, document questions.
4. **C4 Корректная и полная консультация** (20/15/10/5/0 or N/A, 20): Expected 0: ru-018 (wrong key recommendation), ru-022. Expected 5–10: ru-003, ru-007, en-007, ru-026, kz-005. Expected 15: cases with a minor missed detail. Expected N/A: ru-025 (client left before stating a request).
5. **C5 Эмпатия** (Да/Нет, 15): polite, and reacts to a stated inconvenience. Expected Нет: ru-029, ru-014, ru-016, ru-026, ru-018, kz-008.
6. **C6 Управление диалогом** (10/7/4/0, 15): clarity, order, warning about waiting. Expected 4–7: ru-005 (incoherent first instruction), ru-016, kz-005; calls: p4-ru-004 repetition, p4-ru-002 long hold (but warned and thanked).
7. **C7 Завершение** (Да/Нет/N/A, 5): summary or offer of further help plus farewell. Expected Нет: all "Оператор закрыл чат" cases (ru-018, ru-022, ru-026, kz-013, en-013), kz-008 (no closing), en-014 (goodbye with a question pending). Expected N/A: ru-012, kz-011, en-009 (connection lost), ru-025 (client left).
8. **C8 Грубые ошибки** (Да/Нет, no points, flagged separately): insult, paid service bypassing the company, disclosure of confidential information. Expected Да: ru-022 (funds to a personal card), ru-023, kz-013, en-013 (personal number for paid advice), ru-009, kz-015, en-014 (account restriction disclosed without ID), ru-029 (insult).

Per the organizer PDF, C8 does not change the total; it is a separate mark. The 3-minute first-response threshold, tags and attachments are outside the score.
