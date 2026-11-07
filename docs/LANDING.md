# Landing page (final)

The corporate landing at `#/` (bare layout, no app shell). Final synthesis of the demo proposal's landing (structure, live product card, copy deck) with the grafts: «Сценарии для жюри» as the second section (corporate), a Dynamics 365 block with a framed live embed (enterprise), honest cost copy (corporate). Built by WP-9 (core sections) and WP-10 (proof sections) on top of WP-0; see `docs/BUILD_BACKLOG.md`. Visual tokens: `docs/DESIGN_SYSTEM.md`.

## 1. Goals and rules

- **Ten-second read**: what (AI evaluation of every operator conversation against the company checklist), for whom (QA, leads, directors, operators), why trust it (every score has an explanation and the exact message; disputes go through appeal and review), where to click («Открыть демо», «Сценарии для жюри»).
- **Real product, not pictures**: the hero card, the jury cards, the counters and the embedded card are live React components fed by the API. Each has a bundled snapshot (`web/src/features/landing/snapshot.json`, synthetic chats and numbers only, never a call transcript) used when the API is slow (> 3 s) or down, stamped «по состоянию на …».
- **Truthful claims only**: numbers come from the API with an as-of stamp. Cost is «≈ $0,22 за оценку · среднее по 75 оценкам», never «все вызовы учтены». The local model is «поддерживается, на стенде не подключена» unless `/api/health` reports it. No accuracy percentage until the ML owner publishes a gold-set comparison.
- **Corporate, no characters**: no mascot whatever the setting, no glow, no decorative numerals larger than the section title. Two deep-green bands (hero, final CTA panel) on an otherwise light page; restrained gradients only inside those bands.
- **The four live asks are one click away**: the jury section is the second thing on the page.

## 2. Structure

Sections are ordered by an `order` number. Core sections are in WP-9; proof sections are separate files in `web/src/features/landing/proof/` (WP-10), discovered with `import.meta.glob` and inserted by `order`, so the page works with or without them. Backgrounds alternate white and `--bg` (`#F5F7F5`) by rendered position (hero, CTA panel and footer excluded), so a missing proof section never leaves two equal bands next to each other. Header anchors are rendered only for sections that exist.

| Order | Section (anchor id) | Package | Priority | Visual | Data | Calls to action |
|---|---|---|---|---|---|---|
| 0 | Header | WP-9 | P0 | white, sticky, 64 px, bottom border `--line` after scroll | `cqa.role` (returning visitor) | «Открыть демо» (primary) |
| 10 | Hero (`#top`) | WP-9 | P0 | deep-green band `linear-gradient(180deg, #0F5431, #0B4127)` + one radial lift `rgba(81,175,61,.22)` top right | `GET /api/conversations/p1-ru-029/evaluation`, `GET /api/audio/p4-ru-002/peaks?buckets=120` | «Открыть демо» (inverse), «Сценарии для жюри» (outline white, scrolls to `#scenarios`), the card itself → `#/conversations/p1-ru-029` |
| 15 | Live counters | WP-9 | P0 | white strip under the hero, six figures in `kpi` 32/36 | `GET /api/analytics/summary`, `GET /api/violations` | each figure links: dialogs → `#/conversations`, violations → `#/violations`, cost → `#/settings?section=model` |
| 20 | Сценарии для жюри (`#scenarios`) | WP-9 (cards from WP-8) | P0 | 7 cards, grid 4 + 3 (2 per row ≤ 1024 px, 1 per row ≤ 640 px) | `scenarios.ts` (WP-8); live hints | each card switches the role and opens its target |
| 30 | Проблема (`#problem`) | WP-10 | P1 | three numbered cards 01 / 02 / 03 with line icons | — | — |
| 40 | Как это работает (`#how`) | WP-9 | P0 | five steps in a row joined by a thin line; each step an icon tile (P1: a real UI crop) | — | «Посмотреть на примере» → `#/conversations/p1-ru-029` |
| 50 | Возможности (`#features`) | WP-10 | P1 | three chapters alternating text / live crop | `GET /api/violations` (top 3 rows), `GET /api/checklists` | «Открыть пример →» per chapter |
| 55 | Для кого (`#audience`) | WP-9 | P0 | four role cards with an icon tile | — | «Открыть как …» role entries |
| 60 | Языки и каналы (`#languages`) | WP-10 | P1 | three evidence chips (RU / KZ / EN quotes from synthetic chats) + marquee «Оценено · Бағаланды · Evaluated» | snapshot quotes | — |
| 70 | Данные под контролем · Модели и стоимость (`#security`) | WP-10 | P0 within WP-10 | bullets + white «Модели и стоимость» card with status dots | `GET /api/settings`, `GET /api/health`, summary providers | «Как устроена оценка» → `#/conversations/p1-ru-029?tab=trace` |
| 75 | Встраивается в Dynamics 365 (`#d365`) | WP-9 | P0 | bullets + a neutral frame with a lazy iframe of the embed | `index.html?embed=1&conversation=p1-ru-029&host=d365&userlcid=1049` | «Руководство по установке» → `docs/INTEGRATION_D365.md` on GitHub |
| 80 | Сравнение (`#compare`) | WP-10 | P1 | white card radius 24, table «Вручную / CQA», CQA header as a deep-green pill | cost row live | — |
| 90 | Вопросы (`#faq`) | WP-10 | P1 | accordion rows radius 14 | cost answer live | — |
| 95 | Final CTA | WP-9 | P0 | deep-green panel radius 24 on a white page | `summary.count` | «Открыть демо», «Оценить свой диалог» |
| 100 | Footer | WP-9 | P0 | `#092818`, white text | `GET /api/version`, web build commit | links (§3.14) |

Sacrifice order if WP-10 runs late: FAQ → Сравнение → Языки и каналы → Возможности → Проблема; «Данные под контролем» goes last.

### 2.1 Layout

- Content width 1200 px; section padding 96 px top and bottom (64 px ≤ 1024 px, 48 px at 375 px).
- Hero: two columns 6 / 6 from 1024 px (text left, product shot right), stacked below with the text first. The product shot is a live compact `QualityScoreCard` of p1-ru-029 (ring 51,25, three criteria rows with the largest losses, «Почему снижена оценка», the two buttons rendered as links) overlapping a two-lane waveform strip drawn from the p4-ru-002 peaks (numbers only, no transcript text). White cards, radius 16, soft shadow `0 24px 48px -24px rgba(9,40,24,.45)`.
- At 1280×720 without scrolling: header, eyebrow, H1, subline, both buttons, the reassurance line, the product card and the top of the counters strip.
- 375 px: no horizontal page scroll; counters 2 per row; the comparison table scrolls inside its card.

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ CQA Freedom Broker   Сценарии  Как это работает  Возможности  Безопасность  D365  Вопросы   RU KK EN [Открыть демо] │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│▓ Freedom Broker · контроль качества обслуживания                                        ▓│
│▓ Оценка каждого диалога по чек-листу —           ┌ Клиент   ▌▌▍▌▌░░▌▌▌▍▌  (p4-ru-002) ┐  ▓│
│▓ с обоснованием, цитатой и апелляцией            │ Оператор ▍▌▌▍░░▍▌▌▌▍▌              │  ▓│
│▓ CQA проверяет чаты и звонки на русском,         ┌──────────────────────────────────┐ ▓│
│▓ казахском и английском. По каждому критерию …   │ Оценка качества                   │ ▓│
│▓ [Открыть демо]  [Сценарии для жюри]             │ (51,25)  ✕ Выявление потр. 0/18,75│ ▓│
│▓ Без входа в систему · данные DEV уже оценены …  │  / 100   ✕ Эмпатия         0/18,75│ ▓│
│▓ Работает с: Русский · Қазақша · English · Чаты · Звонки с записью               ▓│
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ 65 диалогов оценено │ RU 35 · KZ 15 · EN 15 │ 60 чатов и 5 звонков │ 8 диалогов с грубыми нарушениями │ ≈ $0,22 … │ ≈ 15 с │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

## 3. Final copy

RU is the source; EN ships in the same commit. KK: translated in the `landing` and `landingProof` namespaces where available and checked by a Kazakh speaker; any missing KK key falls back to RU. Values in `{braces}` are live. Decimal comma in RU and KK, dot in EN.

### 3.1 Header

| Element | RU | EN |
|---|---|---|
| Wordmark | CQA · Freedom Broker | CQA · Freedom Broker |
| Anchors | Сценарии для жюри · Как это работает · Возможности · Безопасность · Dynamics 365 · Вопросы | Jury scenarios · How it works · Features · Security · Dynamics 365 · FAQ |
| Primary | Открыть демо | Open the demo |
| Returning visitor | Продолжить как {роль} | Continue as {role} |

### 3.2 Hero

| Element | RU | EN |
|---|---|---|
| Eyebrow | Freedom Broker · контроль качества обслуживания | Freedom Broker · service quality control |
| H1 | Оценка каждого диалога по чек-листу — с обоснованием, цитатой и апелляцией | Every conversation scored against the checklist — with reasons, quotes and appeals |
| Subline | CQA проверяет чаты и звонки на русском, казахском и английском. По каждому критерию — ответ, баллы, объяснение и реплика, на которой основан вывод. Оператор оспаривает оценку, ревьюер принимает решение, итог пересчитывается автоматически. | CQA reviews chats and calls in Russian, Kazakh and English. Every criterion gets an answer, points, an explanation and the exact message it rests on. Operators appeal, reviewers decide, and the total is recalculated automatically. |
| Buttons | Открыть демо · Сценарии для жюри | Open the demo · Jury scenarios |
| Reassurance | Без входа в систему · данные DEV уже оценены · модель и стоимость каждой оценки видны в карточке | No sign-in · DEV data already evaluated · model and cost of every evaluation on the card |
| Chips | Работает с: Русский · Қазақша · English · Чаты · Звонки с записью | Works with: Russian · Kazakh · English · Chats · Recorded calls |
| Card caption | Живая карточка p1-ru-029 из набора DEV · открыть | Live card p1-ru-029 from the DEV set · open |

KK draft of the H1 for the native check: «Әр диалог чек-лист бойынша бағаланады — негіздемесімен, дәйексөзімен және апелляциясымен».

### 3.3 Live counters

| Figure | RU | EN | Source |
|---|---|---|---|
| Dialogs | {65} диалогов оценено | {65} conversations evaluated | `summary.count` |
| Languages | RU {35} · KZ {15} · EN {15} | RU {35} · KZ {15} · EN {15} | `summary.by_language` |
| Channels | {60} чатов и {5} звонков | {60} chats and {5} calls | `summary.by_channel` |
| Violations | {8} диалогов с грубыми нарушениями | {8} conversations with gross violations | `GET /api/violations` → `total` |
| Cost | ≈ ${0,22} за оценку · среднее по {75} оценкам | ≈ ${0.22} per evaluation · mean of {75} evaluations | Σ `summary.providers[].total_cost_usd` ÷ Σ `providers[].n` |
| Speed | ≈ {15} с на оценку | ≈ {15} s per evaluation | `providers[].mean_latency_ms`, weighted by `n` |
| Stamp | по данным API на {20:31} | live from the API at {20:31} | fetch time |
| Fallback stamp | по состоянию на {07.11.2026, 20:30} | as of {07.11.2026, 20:30} | `snapshot.json` `as_of` |

### 3.4 Сценарии для жюри

Title «Сценарии для жюри» / «Jury scenarios». Subtitle «Каждый вопрос жюри — в один клик. Роль переключится автоматически.» / «Each jury question in one click. The role switches automatically.» Card button «Открыть →» / «Open →»; role chip «Роль: {роль}» / «Role: {role}».

| # | Title RU / EN | Sentence RU | Sentence EN | Role and target |
|---|---|---|---|---|
| 1 | Грубые нарушения и почему / Gross violations and why | Все диалоги с грубыми ошибками: тип, цитата, правило и обоснование. | Every conversation with a gross error: type, quote, rule and reasoning. | QA-аналитик → `#/violations` |
| 2 | Оператор подаёт апелляцию / An operator files an appeal | Оператор Нина оспаривает оценку по критерию в своём диалоге. | Operator Nina disputes a criterion score in her own conversation. | Оператор · Нина → `#/my` |
| 3 | Ревьюер принимает решение / A reviewer decides | Ревьюер подтверждает или меняет балл с причиной; итог пересчитывается. | A reviewer confirms or changes the score with a reason; the total is recalculated. | Ревьюер → `#/review` |
| 4 | Изменить чек-лист / Change the checklist | Добавьте критерий, измените формулировку или вес и примените к диалогам. | Add a criterion, change its wording or weight, and apply it to conversations. | Методолог → the active version |
| 5 | Результат диалога с обоснованием / A conversation's result with reasons | Баллы по критериям, «Почему снижена оценка» и переход к реплике. | Points per criterion, «Why the score was lowered» and a jump to the message. | QA-аналитик → `#/conversations/p1-ru-029?criterion=C8&turn=10` |
| 6 | Звонок: переход к моменту записи / A call: jump to the moment | Дорожки «Клиент» и «Оператор»; обоснование ведёт к нужной секунде записи. | Client and operator tracks; the justification leads to the right second of the recording. | QA-аналитик → `#/conversations/p4-ru-002?criterion=C6` |
| 7 | Оценить новый диалог / Evaluate a new conversation | Вставьте текст — оценка появится примерно через 20 секунд. | Paste the text — the score appears in about 20 seconds. | QA-аналитик → `#/conversations?paste=1` |

Live hints (WP-8): card 1 shows «{8} диалогов: оскорбление {1} · платная услуга {4} · раскрытие {3}»; card 3 shows «Открытых апелляций: {N}» or «Открытых апелляций нет — начните со сценария 2».

### 3.5 Проблема (WP-10, P1)

Title «Почему контроль качества буксует» / «Why quality control stalls».

| # | RU | EN |
|---|---|---|
| 01 | **Выборка вместо охвата.** Вручную специалист успевает прочитать и прослушать лишь часть обращений; остальные остаются без оценки и без обратной связи. | **A sample, not coverage.** By hand, a QA specialist reads and listens to only part of the contacts; the rest get no score and no feedback. |
| 02 | **Оценка без доказательств.** Оператор не видит, за какую фразу снижен балл, и спорит с оценкой, а не с фактами. | **Scores without evidence.** Operators cannot see which message cost them points, so they argue with the score instead of the facts. |
| 03 | **Риски видны поздно.** Грубость, платные услуги в обход компании и раскрытие данных без идентификации всплывают из жалоб, а не в день разговора. | **Risks surface late.** Rudeness, paid side services and disclosure without identification come up through complaints, not on the day of the conversation. |

### 3.6 Как это работает (WP-9)

Title «Как это работает · пять шагов от диалога до окончательной оценки» / «How it works · five steps from a conversation to a final score». Link «Посмотреть на примере» / «See an example».

| Step | RU | EN |
|---|---|---|
| 1 Импорт / Import | Диалоги приходят из API по group_id, из файла или вставкой текста. Бот и системные события сохраняются как контекст. | Conversations come from the API by group_id, from a file or pasted text. Bot turns and system events stay as context. |
| 2 Оценка по чек-листу / Checklist evaluation | Каждый критерий оценивается отдельным вызовом модели; баллы и итог считает движок по правилам чек-листа, а не модель. | Each criterion is judged by its own model call; points and the total are computed by the engine from the checklist rules, not by the model. |
| 3 Обоснование и реплика / Reason and message | У каждого критерия — ответ, баллы, объяснение и номер реплики; клик ведёт к сообщению или к моменту записи. | Every criterion has an answer, points, an explanation and a message number; a click opens the message or the moment in the recording. |
| 4 Апелляция / Appeal | Оператор или QA-специалист оспаривает критерий с комментарием. | An operator or a QA specialist disputes a criterion with a comment. |
| 5 Решение / Decision | Ревьюер подтверждает или меняет оценку с причиной; итог пересчитывается, исходная оценка остаётся в истории. | A reviewer confirms or changes the score with a reason; the total is recalculated and the original stays in the history. |

### 3.7 Возможности (WP-10, P1)

Title «Возможности» / «What it does».

| Chapter | RU title · bullets | EN title · bullets | Link |
|---|---|---|---|
| A | **Карточка разговора: всё обоснование на одном экране.** Ответ, баллы и вес по каждому критерию · «Почему снижена оценка» с переходом к реплике · N/A с причиной и перераспределением веса · звонки: дорожки «Клиент» и «Оператор», переход к моменту записи | **The conversation card: the whole justification on one screen.** Answer, points and weight per criterion · «Why the score was lowered» with a jump to the message · N/A with its reason and weight redistribution · calls: client and operator tracks, jump to the moment | `#/conversations/p1-ru-029` |
| B | **Чек-листы без программирования.** Добавить, изменить или заменить критерий и его вес — итог всегда нормируется к 100 · каждое сохранение — новая версия, версии можно сравнить · переоцениваются только изменённые критерии; новые веса пересчитываются без вызова модели | **Checklists without code.** Add, edit or replace a criterion and its weight — the total is always normalised to 100 · every save is a new version you can compare · only changed criteria are re-judged; new weights are recalculated without a model call | `#/checklists?role=methodologist` |
| C | **Грубые нарушения и апелляции.** Реестр по типам: оскорбление клиента, платная услуга в обход компании, раскрытие конфиденциальной информации · оператор оспаривает оценку своего диалога · ревьюер подтверждает или меняет её с причиной, исходная и итоговая оценки видны | **Gross violations and appeals.** A register by type: insulting the client, paid service bypassing the company, disclosure of confidential information · operators appeal scores of their own conversations · reviewers confirm or change them with a reason; original and final scores stay visible | `#/violations` |

### 3.8 Для кого (WP-9)

Title «Для кого» / «Who it is for». Button «Открыть как {роль}» / «Open as {role}».

| Card | RU | EN | Entry |
|---|---|---|---|
| Директор / Director | Индекс качества, риски и стоимость оценки — только главное | Quality index, risks and evaluation cost — only what matters | `#/overview?role=director` |
| Руководитель группы / Team lead | Обзор команды, грубые нарушения и апелляции | Team overview, gross violations and appeals | `#/overview?role=lead` |
| QA-специалист / QA specialist | Импорт, оценка, чек-листы и выгрузка результатов | Import, evaluation, checklists and results export | `#/conversations?role=analyst` |
| Оператор / Operator | Мои оценки и апелляции по своим диалогам | My scores and appeals on my own conversations | `#/my?role=operator` («Кто вы?» opens) |

### 3.9 Языки и каналы (WP-10, P1)

Title «Три языка, чаты и звонки» / «Three languages, chats and calls». Body RU: «Модель оценивает диалог на его языке и цитирует реплики дословно, а объяснения пишет на языке интерфейса. Русские названия разделов приложения в казахских и английских чатах ошибкой не считаются. Для звонков критерии адаптированы к устной речи.» EN: «The model judges each conversation in its own language and quotes messages verbatim, while explanations follow the interface language. Russian app labels inside Kazakh and English chats are not treated as errors. For calls, the criteria are adapted to spoken language.» Three evidence chips: one neutral quote each from a synthetic RU, KZ and EN DEV chat (never a gross-violation quote, never a call), stored in the snapshot. Marquee «Оценено · Бағаланды · Evaluated».

### 3.10 Данные под контролем · Модели и стоимость (WP-10, P0 within the package)

Title «Данные под контролем» / «Data under control».

| Bullet RU | Bullet EN |
|---|---|
| Перед облачным вызовом телефоны, ИИН, номера карт, e-mail и IBAN заменяются метками. | Phone numbers, IINs, card numbers, e-mails and IBANs are replaced with placeholders before any cloud call. |
| Локальная модель через OpenAI-совместимый API (vLLM, Ollama): данные не покидают контур. | A local model through an OpenAI-compatible API (vLLM, Ollama): data stays inside the perimeter. |
| Каждый вызов модели записан: модель, версия промпта, токены, стоимость и время. | Every model call is recorded: model, prompt version, tokens, cost and time. |
| Роли и журнал действий разделяют подачу апелляции и решение по ней. | Roles and an audit trail keep filing an appeal and deciding it apart. |

Card «Модели и стоимость» / «Models and cost», every row live:

| Row RU | Row EN | Source |
|---|---|---|
| Судья: Claude Opus 5.5 · облако | Judge: Claude Opus 5.5 · cloud | `settings.default_model`, `default_provider` |
| Ансамбль по выбору: + Claude Sonnet 5.5 | Optional ensemble: + Claude Sonnet 5.5 | `settings.secondary_model` |
| Локальная модель: {подключена · <model> \| поддерживается, на стенде не подключена} | Local model: {connected · <model> \| supported, not connected on this stand} | `GET /api/health` providers |
| ≈ ${0,22} за оценку · среднее по {75} оценкам | ≈ ${0.22} per evaluation · mean of {75} evaluations | summary providers |
| Маскирование ПДн: {включено \| выключено} | PII masking: {on \| off} | `settings` |
| Режим в results.json: {external_api} | results.json mode: {external_api} | derived from the provider (`local` · `external_api` · `hybrid`) |

Status dots: green «подключено», grey «не подключено», amber «нет данных»; never red for «not connected». Link «Как устроена оценка» / «How an evaluation works» → `#/conversations/p1-ru-029?tab=trace`.

### 3.11 Встраивается в Dynamics 365 (WP-9)

Title «Встраивается в Dynamics 365» / «Built to run inside Dynamics 365». Lead RU: «Карточка оценки открывается во вкладке формы разговора — без отдельного входа и без копирования данных.» EN: «The evaluation card opens in a tab of the conversation form — no separate sign-in and no copying.»

| Bullet RU | Bullet EN |
|---|---|
| Один HTML-файл как веб-ресурс формы (≈ 1,7 МБ). | One HTML file as a form web resource (≈ 1.7 MB). |
| Язык карточки следует профилю пользователя Dynamics 365: RU, KK или EN. | The card follows the Dynamics 365 user's language: RU, KK or EN. |
| Power Automate может передавать расшифровку после закрытия разговора. | Power Automate can send the transcript when a conversation closes. |

Frame: a neutral white card with a 1 px border and the label «Пример: вкладка «Оценка качества» на форме разговора» / «Example: the «Quality evaluation» tab on the conversation form»; inside, a lazy iframe (rendered when scrolled into view) of `index.html?embed=1&conversation=p1-ru-029&host=d365&userlcid=1049` (EN page: `userlcid=1033`), 900 × 560, scaled to fit. No Microsoft logos and no imitation of the Dynamics 365 chrome. Link «Руководство по установке» / «Installation guide» → `https://github.com/k4ssymzhomart/freedom/blob/main/docs/INTEGRATION_D365.md`.

### 3.12 Сравнение (WP-10, P1)

Title «Ручная проверка и CQA» / «Manual QA and CQA».

| RU row | Вручную | CQA | EN row | By hand | CQA |
|---|---|---|---|---|---|
| Сколько диалогов оценено | выборка | каждый загруженный диалог | Conversations scored | a sample | every imported conversation |
| Обоснование оценки | заметка проверяющего | объяснение и номер реплики по каждому критерию | Justification | the reviewer's note | explanation and message number per criterion |
| Звонки | прослушать запись целиком | переход к моменту записи | Calls | listen to the whole recording | jump to the moment |
| Спорная оценка | переписка и служебные записки | апелляция по критерию, решение с причиной, пересчёт итога | Disputed score | e-mails and memos | appeal per criterion, decision with a reason, recalculated total |
| Изменение чек-листа | новая методичка и перепроверка | новая версия и переоценка только изменённого критерия | Checklist change | a new manual and re-checking | a new version, only the changed criterion re-judged |
| Грубые нарушения | по жалобам | реестр по типам в день оценки | Gross violations | through complaints | a register by type on the day of evaluation |
| Стоимость одной оценки | время специалиста | ≈ ${0,22} в облаке (среднее по {75} оценкам) · $0 на локальной модели без учёта оборудования | Cost per evaluation | specialist time | ≈ ${0.22} in the cloud (mean of {75} evaluations) · $0 on a local model, hardware excluded |

### 3.13 Вопросы (WP-10, P1)

| Question RU / EN | Answer RU (EN translates one to one in `landingProof/en.ts`) |
|---|---|
| Откуда берутся баллы? / Where do the points come from? | Модель отвечает на каждый критерий отдельно: ответ, объяснение и реплики-основания. Баллы и итог считает движок по правилам чек-листа: «Да» — полный вес, «Нет» — ноль, шкала — пропорционально весу; итог округляется до двух знаков, половина вверх. |
| Что значит N/A? / What does N/A mean? | Критерий неприменим к этому диалогу — например, идентификация не нужна для общего вопроса о тарифе. N/A разрешён только там, где его допускает чек-лист; в карточке видна причина, а вес перераспределяется между применимыми критериями, максимум остаётся 100. |
| Почему грубая ошибка не обнуляет итог? / Why doesn't a gross error zero the total? | Так требует чек-лист: оскорбление, платная услуга в обход компании и раскрытие конфиденциальной информации отмечаются отдельно и попадают в реестр «Грубые нарушения»; итог считается по остальным критериям. |
| Как оспорить оценку? / How do I dispute a score? | В карточке разговора нажмите «Оспорить оценку», выберите критерий и напишите комментарий. Ревьюер видит спорный балл, комментарий и фрагмент диалога, подтверждает или меняет оценку с причиной; итог пересчитывается, исходная оценка остаётся в истории. |
| Как изменить чек-лист? / How do I change the checklist? | В разделе «Чек-листы» создайте новую версию, добавьте или измените критерий, вес, тип ответа и правило N/A, сохраните её и примените к выбранным диалогам. Переоцениваются только изменённые критерии; новые веса пересчитываются без вызова модели. |
| Это заранее посчитанные результаты? / Are these results precomputed? | Нет. Вставьте любой диалог — оценка появится примерно через 20 секунд; во вкладке «Трассировка» видны время, модель, токены и стоимость каждого вызова. |
| Уходят ли данные в облако? / Does data go to the cloud? | С облачной моделью — да, но телефоны, ИИН, номера карт, e-mail и IBAN заменяются метками до отправки. Для данных, которые не должны покидать контур, есть локальная модель через OpenAI-совместимый API. |
| Работает ли со звонками? / Does it work with calls? | Да: расшифровка с ролями, запись с дорожками «Клиент» и «Оператор», переход от обоснования к моменту записи. Критерии адаптированы к устной речи: например, приветствие, не попавшее в запись, — N/A. |
| Сколько стоит оценка? / What does an evaluation cost? | Около ${0,22} за оценку — среднее по {75} оценкам на стенде с Claude Opus 5.5. Стоимость каждой оценки и каждого вызова видна в карточке и во вкладке «Трассировка». На локальной модели — $0 без учёта оборудования. |
| Можно ли встроить в Dynamics 365? / Can it run inside Dynamics 365? | Да: интерфейс собирается в один HTML-файл, карточка открывается во вкладке формы разговора на языке пользователя, а Power Automate может передавать расшифровки после закрытия разговора. |

### 3.14 Final CTA and footer (WP-9)

| Element | RU | EN |
|---|---|---|
| CTA title | Проверьте любой диалог сами | Check any conversation yourself |
| CTA text | Без входа в систему. {65} диалогов DEV уже оценены — откройте карточку или вставьте свой диалог: оценка появится примерно через 20 секунд. | No sign-in. {65} DEV conversations are already evaluated — open a card or paste your own: the score appears in about 20 seconds. |
| CTA buttons | Открыть демо · Оценить свой диалог (→ `#/conversations?paste=1&role=analyst`) | Open the demo · Evaluate your own conversation |
| Footer tagline | CQA · Conversation Quality Analytics для Freedom Broker | CQA · Conversation Quality Analytics for Freedom Broker |
| Column «Продукт» / «Product» | Как это работает · Возможности · Вопросы | How it works · Features · FAQ |
| Column «Для жюри» / «For the jury» | Сценарии для жюри · `results/dev/results.json` · `results/prod/results.json` («появится после контрольного этапа» until the file exists) · API Swagger `/docs` | Jury scenarios · `results/dev/results.json` · `results/prod/results.json` («available after the control stage») · API Swagger `/docs` |
| Column «Проект» / «Project» | GitHub (`github.com/k4ssymzhomart/freedom`) · README · Архитектура · Интеграция с Dynamics 365 | GitHub · README · Architecture · Dynamics 365 integration |
| Bottom line | Прототип команды «Я Тебя Могну» · хакатон Freedom Broker, 2026 · web {commit} · API {version} | Prototype by team «Я Тебя Могну» · Freedom Broker hackathon, 2026 · web {commit} · API {version} |

`results/prod/results.json` existence is checked with a HEAD request to the raw GitHub URL; on any failure the link shows the «появится после контрольного этапа» note.

## 4. Live data contract

| Block | Endpoint | Budget | Fallback |
|---|---|---|---|
| Hero card | `GET /api/conversations/p1-ru-029/evaluation` | 3 s | `snapshot.hero` (synthetic chat p1-ru-029: total, rows, reasons) |
| Hero waveform strip | `GET /api/audio/p4-ru-002/peaks?buckets=120` | 3 s | `snapshot.peaks` (numbers only) or no strip |
| Counters, cost | `GET /api/analytics/summary` | 3 s | `snapshot.summary` with its stamp |
| Violations count, jury hint 1 | `GET /api/violations` | 3 s | `snapshot.violations` |
| Jury hint 3 | `GET /api/appeals?status=open` | 3 s | hint hidden |
| Models card | `GET /api/settings`, `GET /api/health` | 3 s | static rows with «нет данных» |
| Footer | `GET /api/version`, `import.meta.env` build commit | 3 s | «API —» |

All requests are GETs without custom headers, fired in parallel on mount. A block never shows 0, an empty chart or a spinner longer than 3 s; it switches to its snapshot with the stamp. The text of the page never waits for the API. The snapshot holds only synthetic chats, aggregate numbers and peaks; no transcript text from the five real calls.

## 5. Behaviour

- **CTAs**: «Открыть демо» opens the stored role's home, or `#/conversations` as QA-аналитик; it never opens a dialog first. Role-entry links (`?role=…&as=…`) are handled by WP-0's link parameters.
- **Language**: the landing uses the same RU · KK · EN switch and `cqa.lang`, so the choice carries into the app.
- **Performance**: app routes are lazy-loaded, so the landing does not ship recharts or the editor; live cards render after first paint with skeletons of the final size (no layout shift); the iframe loads only when its section enters the viewport.
- **Accessibility**: one H1, section H2s, anchors with visible focus, white on the deep-green band 9,0:1 and secondary white at 78 % 6,2:1; the FAQ accordion is keyboard operable; marquee motion stops with `prefers-reduced-motion`.
- **No mascot**: the landing never renders `BrandMascot`, whatever the setting.

## 6. Acceptance

1. `https://cqa-freedom-pi.vercel.app/#/` at 1280×720 shows, without scrolling: the header, eyebrow, H1, subline, both buttons, the reassurance line, the live hero card (ring 51,25, three rows, «Почему снижена оценка») and the top of the counters strip.
2. «Открыть демо» opens `#/conversations` as QA-аналитик in one click; a returning reviewer sees «Продолжить как Ревьюер» and lands on `#/review`.
3. «Сценарии для жюри» is the second section; each of the 7 cards opens its target with the role switched (scenario 2 lands on `#/my` as «Оператор · Нина»).
4. Counters are live with a stamp; with the API blocked, every block shows snapshot values stamped «по состоянию на …» and the page still renders.
5. The Dynamics 365 block shows a working embedded card of p1-ru-029 in the light theme, read-only, without the sidebar.
6. The RU · KK · EN switch changes every landing text (KK may fall back to RU) and carries into the app; at 375 px there is no horizontal scroll.
7. No mascot image, no «все вызовы учтены», no accuracy figure anywhere on the page.
