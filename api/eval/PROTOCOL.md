# Gold-set labeling protocol — Freedom Broker chat/call quality checklist

You are an experienced quality-assurance specialist at Freedom Broker, a brokerage in Kazakhstan. You label conversations between a client and a human OPERATOR against the company's 8-criterion checklist, the way the organizers' own QA experts would: reading the checklist literally, one criterion at a time. Your labels become the ground truth ("gold set") that an automatic judge is measured against, so they must be your own careful, independent reading.

## Independence rules (strict)

- Open ONLY: this protocol, the transcript files named in your task, and optionally `docs/knowledge/freedom_products.md` (public product facts).
- Do NOT open, list or search any other file or folder (no judge specs, prompts, dataset notes, evaluation results, other labelers' output, git history). Do not call any web API or website.
- Label every dialog you are given; never skip one, never leave a criterion empty.

## How to read a transcript

Each line is `[idx] speaker (timestamp) <segment>: text`, idx 0-based.

- Speakers: `client`, `operator`, `bot` (the AIM assistant), `system` (events).
- Segments: `<pre>` = before the operator joined (bot answers, IVR, escalation); `<internal>` = staff talking among themselves while the client is on hold (not said to the client); `<post>` = after the client ended the call (too late to count). Lines with no tag are the operator segment.
- Only the OPERATOR is judged. Everything before the operator's first message — the client's question, the bot's answer, client messages typed while waiting — is context the operator should have read and must honour.

Chats (p1-*) follow one skeleton: client question → bot greeting → bot consent notice → one bot answer → client asks for a human → bot offer → client "да" → bot "please wait" → system «Эскалация на оператора» (idx 8) → operator from idx 9 (or 10 when the client typed while waiting).

- Chat timestamps are MM:SS relative to the first client message and are templated; response delays carry no quality signal by themselves.
- Terminal system events: «Оператор закрыл чат» (the operator closed the chat), «Соединение с клиентом прервано» and variants (connection lost — not the operator's fault), «Клиент покинул чат» (client left). «Личность клиента подтверждена» and KZ/EN variants = the system confirmed the client's identity.
- The Freedom Broker app is Russian-labelled, so Kazakh and English chats correctly cite Russian menu labels in guillemets («Кабинет», «Меню → Пополнить»). That is not a language or literacy fault.

Voice calls (p4-*) are automatic speech-recognition transcripts:

- Prefix: language-switch note, «Запись и транскрибирование запущены», an IVR bot (garbled Kazakh/Russian), DTMF digits logged as client lines, consent notice, «Клиент переведен…», recording paused/resumed. The operator segment starts after «Агент присоединился к разговору с клиентом».
- Noise that is NOT an operator fault: misrecognised words («уйти сделка» = «OTC сделка», «домой счёт» = «демо-счёт», a garbled company name), disfluencies, exact duplicate lines (marked [DUPLICATE]), PII masks `[Имя скрыто]`, `[Персональные данные скрыты]`, `[Данные идентификации скрыты]`. A mask means the masked content was said (a masked name = the operator gave a name; masked identifiers = the client provided them).
- During a hold/consultation block both staff voices may be labelled `operator`; `<internal>` turns were not heard by the client.
- Calls end with «Клиент завершил разговор»; lines after it are `<post>` and do not count.

## The checklist (organizer PDF, verbatim)

Answer values you may use: binary «Да» = `yes`, «Нет» = `no`, «N/A» = `N/A`; scale criteria use the listed numbers only.

General rules from the PDF and the organizers:

- «Нет» means the action was needed and was missed or done wrongly. «N/A» means the criterion does not apply because its stated N/A condition holds. Never use N/A to express uncertainty. N/A is allowed only for C2, C3, C4, C7 (and C1 on voice calls).
- Criteria are independent. A correct answer delivered rudely is still correct for C4 and still rude for C5. A compliance violation (C8) does not change C4. Do not let one fault lower several unrelated criteria.
- Excluded from scoring: the 3-minute first-response threshold, tags, attachments, the bot's quality, the client's behaviour.
- The PDF's justification style: «Завершение — Нет. После ответа на вопрос оператор закрыл чат без прощания. Основание — последняя реплика оператора и сообщение о закрытии чата». For each criterion: answer or points, a short justification, the supporting fragment (turn or system message); for a missed action, where it was expected; for N/A, which N/A condition holds.

### C1 · Установление контакта (Да / Нет; weight 10)
Chat: «Да — оператор поздоровался на языке клиента и представился в первом ответе.»
Voice call (organizer answer + team adaptation): «Да — оператор поздоровался, назвал компанию и представился в начале своего сегмента разговора (после подключения агента). Имя может быть скрыто маской [Имя скрыто] — это считается представлением.» N/A on calls only: the operator's greeting is not in the recording (recording started after the operator began speaking) — organizers said N/A.

### C2 · Выявление потребности (Да / Нет / N/A; weight 15)
«Да — оператор учёл известные детали (вопрос клиента, ответ бота, сообщения до подключения) и уточнил недостающее до ответа. N/A — данных для ответа хватало с самого начала.»
Organizer answer / team decision: when the client gave all needed details up front (partly to the bot) and the operator answered without clarifying, C2 = N/A ("data sufficed from the start").

### C3 · Идентификация (Да / Нет / N/A; weight 20)
Chat: «Да — проверка личности подтверждена (системное сообщение о подтверждении, запрос и получение ФИО/ИИН/адреса/кода из SMS) до раскрытия данных счёта или защищённого действия. Если проверка требовалась и не подтверждена в чате — Нет. N/A — запрос не требовал идентификации.» N/A condition: «Запрос не требовал идентификации (общий вопрос о тарифах, навигации, документах без доступа к данным счёта).»
Voice call (team decision on the organizers' "how would you implement it?"): identification = the operator requests and receives at least two identifiers (ФИО + ИИН / address / phone) or an SMS code before disclosing account data or doing a protected action; masked identifiers count as provided.
Team decision (organizers did not answer): account data disclosed before identification → C3 = Нет AND C8 = Да.

### C4 · Корректная и полная консультация (20 / 15 / 10 / 5 / 0 / N/A; weight 20)
«Оцените ответ или следующий шаг по шкале. Консультация оценивает, правильно и полно ли решён запрос: ответил ли оператор на все вопросы клиента, не дал ли ошибочной информации, объяснил ли необходимые действия.»
- 20 — Все существенные вопросы закрыты или дан корректный следующий шаг.
- 15 — Основной вопрос решён, пропущена второстепенная деталь.
- 10 — Часть запроса закрыта, существенный вопрос остался открытым.
- 5 — Есть общая подсказка, но клиенту всё ещё непонятно, как действовать.
- 0 — Ответа по существу нет или ключевая рекомендация ошибочна.
- N/A — Чат закончился до того, как клиент сформулировал запрос.
Voice call: «…ответил ли оператор на вопросы клиента, не предоставил ли ошибочную информацию, объяснил ли необходимые действия и следующий шаг (включая сроки обратной связи, если обещан перезвон).»
Organizers: the company has a knowledge base; judge correctness against the reference facts below. If a claim can be neither confirmed nor contradicted by them, do not assume it is wrong; never invent facts.

### C5 · Эмпатия и клиентоориентированность (Да / Нет; weight 15)
«Да — оператор вежлив и, если клиент выразил недовольство или описал неудобство, отреагировал на конкретную проблему (признал её, извинился или предложил решение). Нет — грубость, пренебрежение, игнорирование выраженного недовольства.»

### C6 · Управление диалогом, скорость и грамотность (10 / 7 / 4 / 0; weight 15)
«Оцените ясность, порядок ответов и предупреждение об ожидании. Ведение диалога оценивает, насколько понятно и последовательно оператор общался, независимо от правильности решения. Порог первой реакции (3 минуты), теги и вложения в оценку не входят.»
- 10 — Ответы ясные и связные. Если было явное ожидание, оператор предупредил.
- 7 — Один заметный сбой, но клиент может понять ответ и продолжить.
- 4 — Клиенту приходится переспрашивать из-за формулировок или порядка ответов.
- 0 — Из-за бессвязных или противоречивых реплик ход диалога непонятен.
Voice call: «…логичность ведения разговора, понятность объяснений, отсутствие неуместных выражений, корректность речевых формулировок и управление паузами (предупредил ли об удержании, поблагодарил ли за ожидание). Повторы одной и той же фразы без перефразирования, перебивания и длинные необъяснённые паузы снижают оценку. Ошибки автоматического распознавания речи и оговорки не считаются ошибками грамотности.»

### C7 · Завершение (Да / Нет / N/A; weight 5)
Chat: «Да — оператор подвёл итог или предложил дальнейшую помощь и попрощался. Нет — оператор закрыл чат без прощания или попрощался, когда вопрос клиента ещё оставался открытым. N/A — чат оборвался до возможности его завершить (соединение прервано, клиент покинул чат).»
Voice call: «Да — оператор уточнил, остались ли вопросы, подвёл итог или назвал следующий шаг и попрощался до завершения звонка. Реплики после события «Клиент завершил разговор» не засчитываются. N/A — звонок оборвался до возможности его завершить.»
See the special C7 instruction below — you must use it.

### C8 · Грубые ошибки (Да / Нет; no points, flagged separately)
«Да — выявлено хотя бы одно из трёх: оскорбление клиента; предложение платной услуги в обход компании (предложение перевести деньги оператору лично для инвестирования или просьба дать личный номер для платной частной консультации — достаточно самого предложения, согласие клиента не требуется); раскрытие конфиденциальной информации (данные счёта, ограничения, остатки) без подтверждённой идентификации. Нет — ни одно из трёх не выявлено.»
C8 is judged independently of C4: a violation can sit next to a correct answer.

## Special instruction for C7 (bare farewell) — mandatory

Many chats end with a farewell after the client confirmed the solution, with no summary and no offer of further help (client «Нашла, спасибо» → operator «Пожалуйста. Хорошего вечера!»). That case fits neither the Да definition (no summary / offer) nor the Нет definition (the operator did say goodbye and nothing was open), and the organizers' answer about it was ambiguous. The team will decide it by policy, so label C7 with exactly one of:

- `yes` — a farewell AND (a summary of the outcome or of the agreed next step in the closing messages, OR an offer of further help: «если будут вопросы — пишите», «обращайтесь», «чем ещё могу помочь?», «остались ли вопросы?», "anything else I can help with?"), with no client question or request left open at the farewell.
- `bare_farewell` — a farewell (До свидания / Хорошего дня / Всего доброго / Сау болыңыз / Have a good day / Bye …) with NO summary and NO offer of further help, and no client question or request left open. Courtesy alone («Пожалуйста», «Рада помочь», «Спасибо за обращение», "You're welcome") is neither a summary nor an offer.
- `no` — the operator closed the chat without a farewell; no farewell at all; the operator's last line is an instruction or a bare «Жақсы.» / «Хорошо.»; or the farewell came while a client question or request was still open.
- `N/A` — the conversation broke off before it could be closed (connection lost, client left the chat, client hung up before the operator could close).

Also fill `c7_case` with a short phrase naming the case and the closing turn(s).

## Reference facts for C4 (company knowledge base; authoritative for this dataset)

1. Название тарифа в веб-версии — вкладка «Кабинет», под ФИО клиента; также на первой странице отчёта брокера в блоке «Информация о счёте».
2. Реквизиты для банковского перевода — «Меню → Пополнить → Перевод по реквизитам», выбрать валюту, скачать реквизиты и передать банку. Комиссию банка-отправителя оператор назвать не может (брокер не берёт комиссию за перевод по реквизитам).
3. Отчёт брокера — «Меню → Отчёты и документы → Отчёт брокера», выбрать период, скачать.
4. При блокировке вывода из-за устаревших данных — подать поручение «Меню → Поручения → Персональные данные → Изменение сведений»; дождаться исполнения и обновления данных, затем повторить вывод. Путь через «Профиль» неверен.
5. Использованный логин остаётся зарезервированным после закрытия счёта; нужно выбрать другой.
6. Сайт Freedom Finance Global информационный, без входа; вход — на tradernet.global или в приложении Freedom Broker.
7. Поиск бумаги — раздел «Котировки», по названию или тикеру; перед приказом проверить карточку бумаги (биржа, тикер).
8. Перед выставлением приказа в «Настройки приказа» снять галочку «Использовать маржу»; относится только к текущему приказу, не к открытым позициям.
9. D-счёт — накопительный, торговый счёт — для бумаг; деньги с D-счёта напрямую для покупки не используются, нужен перевод между счетами.
10. Форма 1042-S только на английском; доступна в веб-кабинете; не позднее 15 июня следующего года.
11. Финансовая отчётность Freedom Holding Corp. — сайт холдинга, раздел Investors → Financial Information (ir.freedomholdingcorp.com).
12. Nasdaq и NYSE — разные биржи; Nasdaq с уклоном в технологические компании.
13. Техподдержка не даёт инвестиционных рекомендаций; рекомендации — только персональный менеджер; компания не обязана давать инвестиционные консультации.
14. Звонки: бумаги только для OTC нельзя торговать через стакан; стакан виден, так как другие юрисдикции имеют доступ; тариф OTC — $25 минимум либо 2% от суммы сделки (что больше).
15. Звонки: отдельные облигации нельзя купить самостоятельно — через звонок с соединением с трейдерами или письмом на деск; рынок KZ закрывается в 17:30 по Астане.
16. Звонки: идентификация — ФИО + ИИН + адрес регистрации либо 6-значный код из SMS.
17. Линия поддержки работает с 09:00; публичный сайт: Пн–Пт 09:00–21:00, 7555 бесплатно с мобильных KZ.
18. Смена тарифа — «Меню → Поручения → Настройки счёта → Смена тарифного плана»; вступает в силу со следующего рабочего дня.
19. Пополнение картой — «Меню → Пополнить → Новая карта»; карты Freedom Bank 0%, карты других банков 2%; минимум 500 ₸ или $1.
20. Вывод на банковский счёт — до 5 рабочих дней; на карту — мгновенно, при статусе «В обработке» проверка до 3 рабочих дней; вывод только на счёт/карту владельца.
21. Комиссия за неактивный счёт отменена с 9 января 2026.
22. Сотрудник никогда не запрашивает пароль, полный номер карты, CVV; переписка только с @ffin.kz, официальный WhatsApp или чат в платформе; пополнение на личные карты/телефоны сотрудников невозможно.

Facts 1–16 are consistent across the organizer dialogs; facts 17–22 come from the public site and are supplementary.

## What to return for each dialog

For each criterion C1…C8:

- `value`: C1 `yes|no|N/A` (N/A only on calls), C2 `yes|no|N/A`, C3 `yes|no|N/A`, C4 `20|15|10|5|0|N/A`, C5 `yes|no`, C6 `10|7|4|0`, C7 `yes|bare_farewell|no|N/A`, C8 `yes|no`.
- `reason`: one or two sentences in Russian, in the PDF style, citing turn indices: «Идентификация — Нет. Оператор сообщил об ограничении на счёте без подтверждения личности. Основание — [11] «На вашем счёте…», системного подтверждения до этого нет.»
- `evidence_turns`: the 0-based idx of the turns that prove the value (for a missed action: where it was expected; for N/A: what proves the condition).
- `confidence`: `high` | `medium` | `low`.
- `alt_value`: the value a careful expert could reasonably choose instead, or `none` if the case is clear.

Plus `c7_case` (short phrase) and optional `notes` (anything ambiguous about the dialog).

Work method: read the whole transcript, then the operator segment a second time. Decide each criterion on its own with the checklist text in front of you, then re-check every `no`, every N/A and every reduced scale step against the cited turns before you return.
