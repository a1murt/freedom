# Roles and permissions (final)

Final role model for the CQA prototype. Written 2026-11-07 ≈ H+7,5 (UTC+5) as the synthesis of `docs/design/proposal-demo.md` (winning proposal) with the judges' grafts from `proposal-corporate.md` and `proposal-enterprise.md` and their cuts applied. Binding source: `docs/ORGANIZER_UPDATES.md`. Source of truth for role codes, homes and permissions: `api/cqa/security/roles.py` (working tree, goes live with the B-0 deploy). The web app mirrors it in `web/src/lib/roles.ts` (WP-0). Companion docs: `docs/IA.md` (routes), `docs/DESIGN_SYSTEM.md`, `docs/LANDING.md`, `docs/BUILD_BACKLOG.md` (who builds what).

## 1. Principles

- **No login**, as the organizer FAQ allows. A person picks a role in the sidebar, or a link sets it. The role decides which actions the UI offers. The API enforces the same permission map on every write (403 `forbidden`) and signs every write with the role and the name (audit trail in `audit_events`).
- **Attribution and guard rails, not authentication.** In production the user and the role come from Entra ID / Dynamics 365 (D365-06, later). This sentence is part of the role-pill tooltip and of the presenter's answers (§9).
- **Filing an appeal and deciding it are separate actions in separate roles**, as the organizers ask: Оператор files, Ревьюер decides, and the card shows both signatures.
- **The UI follows `roles.py` exactly**: same codes, homes and permissions. Not changed tonight: the lead stays «Руководитель группы», the admin home stays `#/settings`, the visitor home stays `#/`.
- **`demo` is never offered.** A request without a role header is the backend's `demo` role (every permission) for old clients. The redesigned web sends a role on every write, so the server-side check is real from the first click.
- **One default, no blocking picker.** A fresh browser works as QA-аналитик. There is no top bar, no `#/start` page and no role dialog on first open (all cut).

## 2. Roles

| Code | Label RU / EN / KK | Represents | Home | Demo signature (default name) | Where in the role menu |
|---|---|---|---|---|---|
| `operator` | Оператор / Operator / Оператор | contact-centre operator, sees own work | `#/my` | the operator chosen in «Кто вы?» (Нина for the demo) | main list |
| `analyst` | QA-аналитик / QA analyst / QA-талдаушы | QA specialist: import, evaluate, read cards, file appeals, export | `#/conversations` | Жанар Б. | main list (default role) |
| `reviewer` | Ревьюер / Reviewer / Ревьюер | senior QA who decides appeals | `#/review` | Айгерим С. | main list |
| `methodologist` | Методолог / Methodologist / Әдіскер | owner of checklists and criteria | `#/checklists` | Бекзат Н. | main list |
| `lead` | Руководитель группы / Team lead / Топ жетекшісі | QA or team lead | `#/overview` | Ерлан К. | main list |
| `director` | Директор / Director / Директор | director of client service, read-only | `#/overview` | Гульнара А. | main list |
| `visitor` | Гость / Visitor / Қонақ | audience, read-only | `#/` (landing) | none | «Ещё роли» |
| `admin` | Администратор / Administrator / Әкімші | the team running the stand | `#/settings` | Администратор стенда | «Ещё роли», only after `?role=admin` was opened in this browser |

One-line descriptions (role menu, tooltips; KK in `web/src/i18n/shell/kk.ts`, checked by a Kazakh speaker):

| Role | RU | EN |
|---|---|---|
| Оператор | Мои оценки и апелляции по своим диалогам | My evaluations and appeals on my own conversations |
| QA-аналитик | Импорт и оценка диалогов, обоснования, выгрузка результатов | Import and evaluate conversations, justifications, results export |
| Ревьюер | Разбор апелляций: подтвердить или изменить оценку | Resolve appeals: confirm or change the score |
| Методолог | Чек-листы: критерии, веса, версии и применение | Checklists: criteria, weights, versions and apply |
| Руководитель группы | Качество команды, грубые нарушения, апелляции и чек-листы | Team quality, gross violations, appeals and checklists |
| Директор | Ключевые показатели и риски, только просмотр | Key indicators and risks, view only |
| Гость | Только просмотр | View only |
| Администратор | Настройки, модель, оформление и готовность стенда | Settings, model, appearance and stand readiness |

The staff signatures were checked against the 56 operator names in DEV (Нина, Алексей, Алина, Мұрат, Nina, Maya …): none collide, so an audit row or a History line never looks as if an operator acted. «Нина» (RU, p1-ru-005) and «Nina» (EN) are different operators and stay apart. `scripts/seed_demo.py` signs its seeded appeal and decision with the same names (WP-11 aligns its constants to «Жанар Б.» and «Айгерим С.»).

## 3. Choosing a role without login

### 3.1 The role pill (top of the sidebar)

- Sits right under the «CQA» wordmark, the first thing in the deep-green sidebar (graft from the corporate proposal; the operator → reviewer switch of live ask 2 must be visible on a projector).
- Two lines: caption «Роль» (12/16, white 64 %) and the value «QA-аналитик · Жанар Б.» or «Оператор · Нина» (15/20, 600, white), chevron on the right. 48 px tall, radius 10, background `rgba(255,255,255,.10)`, hover `.16`, white focus ring.
- Collapsed sidebar (72 px): a 40 px circle with the initials of the role («QA», «ОП», «РВ»), the full value in the tooltip.
- Tooltip (hover and focus), RU: «Демо без входа: роль определяет доступные действия и подписывает апелляции и решения. В Dynamics 365 пользователь и роль берутся из учётной записи.» EN: «Demo without sign-in: the role decides the available actions and signs appeals and decisions. In Dynamics 365 the user and the role come from the account.»

### 3.2 The role menu

- Opens under the pill (popover, 320 px). Six main roles in this order: Оператор, QA-аналитик, Ревьюер, Методолог, Руководитель группы, Директор. Each row: role label, the one-line description, the home in grey («→ Апелляции»), a check on the current role.
- «Ещё роли ▸» submenu: Гость (always) and Администратор (only when `cqa.adminUnlocked` is set, i.e. `?role=admin` was opened in this browser). On the jury instance a juror never sees Администратор unless the team gives them that link.
- Bottom actions: «Ваше имя для подписи…» for staff roles (inline input, ≤ 128 characters, stored per role), «Сменить оператора…» for Оператор (opens «Кто вы?»). P1: «Скопировать ссылку для этой роли» (adds `?role=&as=` on purpose).

### 3.3 «Кто вы?» (operator identity)

- Opens when Оператор is chosen and no operator is stored, or from «Сменить оператора…».
- List from `GET /api/operators`, the «—» group (unknown operator) excluded. Row: «Нина · RU · 1 диалог». Search is case-insensitive, treats ё as е and matches Kazakh letters («мұр» finds Мұрат).
- Pinned on top: «Рекомендуем для демонстрации: Нина · p1-ru-005».
- «Отмена» returns to the previous role. API offline: error with «Повторить»; no free-text names for operators (the API would refuse their appeals with 403 `not_your_dialog`).
- After the choice the pill reads «Оператор · Нина» and the app opens `#/my`.

### 3.4 Defaults and landing entries

- A fresh browser that opens any app route works as QA-аналитик signed «Жанар Б.». No dialog appears.
- The landing's «Открыть демо» opens the stored role's home, or QA-аналитик's home; a returning visitor sees «Продолжить как Ревьюер».
- The landing's «Для кого» cards and «Сценарии для жюри» cards are role entries: they set the role (and the operator) and open the target in one click.

### 3.5 Links

- `?role=<code>` sets the role; `?as=<operator name>` sets the operator and implies `role=operator`. Both work in the hash query (`#/my?role=operator&as=Нина`) and before the hash. They are applied on load, persisted, and then removed from the address bar with `history.replaceState`, so a copied URL never carries a role by accident.
- Unknown role: toast «Роль «xyz» не найдена», the current role stays. Unknown operator: «Кто вы?» opens with «Оператор «xyz» не найден».
- `?role=admin` sets Администратор and stores `cqa.adminUnlocked = 1` for this browser.

### 3.6 Persistence

`localStorage`, every read and write in try/catch with an in-memory fallback (blocked storage, Dynamics 365 iframe sandbox): `cqa.role`, `cqa.operator`, `cqa.signature.<role>`, `cqa.adminUnlocked`, `cqa.lang`, `cqa.theme`, `cqa.nav`, `cqa.jury.done`, `cqa.jury.autoOpened`, `cqa.my.seen.<operator>`.

### 3.7 Switching roles

- After a switch the app opens the new role's home, unless the current route is allowed for the new role; then the page stays and re-renders with the new permissions (example: Оператор → Ревьюер while on `#/conversations/p1-ru-005` stays on the card).
- Open dialogs close. Nothing typed is ever submitted under the new role. P1: a guard «Есть несохранённые изменения. Переключить роль?» when an appeal comment, a decision reason or a checklist draft is unsaved.

### 3.8 Embedded card (Dynamics 365)

No pill. The role and the user come only from `window.CQA_CONFIG.role` / `.user` set by the host page; without them the embed acts as Гость. The embed is read-only tonight (appeals inside Dynamics 365 are D365-03, cut). «Открыть в CQA» opens the full app in a new tab with `?role=&as=` taken from the host.

## 4. Home and navigation per role

Upper group in order; the lower group is the same for everyone: «Сценарии для жюри» · «Настройки», then the language switch RU · KK · EN and the API status dot. A nav item is shown only when its route is registered (a route whose package has not landed yet stays hidden). Badges: «Грубые нарушения» = register total, «Апелляции» = open appeals, «Мои апелляции» = decisions not yet opened by this operator.

| Role | Home | Upper group |
|---|---|---|
| Оператор | `#/my` | Мои оценки · Мои апелляции · Чек-лист |
| QA-аналитик | `#/conversations` | Диалоги · Грубые нарушения · Апелляции · Чек-листы · Аналитика |
| Ревьюер | `#/review` | Апелляции · Диалоги · Грубые нарушения · Чек-листы · Аналитика |
| Методолог | `#/checklists` | Чек-листы · Диалоги · Грубые нарушения · Аналитика |
| Руководитель группы | `#/overview` | Обзор · Диалоги · Грубые нарушения · Апелляции · Чек-листы · Аналитика |
| Директор | `#/overview` | Обзор · Грубые нарушения · Аналитика · Диалоги · Чек-листы |
| Гость | `#/` | Обзор · Диалоги · Грубые нарушения · Аналитика |
| Администратор | `#/settings` | Обзор · Диалоги · Грубые нарушения · Апелляции · Чек-листы · Аналитика |

Route policy (WP-0 shell):

- Оператор on `#/conversations` → `#/my`; on `#/review` → `#/my/appeals`; on `#/violations`, `#/overview`, `#/analytics` → read-only notice with «Вернуться к моим оценкам».
- `#/my` for any other role → notice «Раздел оператора» with «Войти как оператор» (opens «Кто вы?»).
- A route the role may read but not change opens read-only: write buttons are hidden or disabled with the reason, and screens that are mostly writes show «Раздел доступен роли «Методолог». Вы работаете как «Директор» — изменения недоступны.» with «Переключиться на роль Методолог».
- A role's home that is not registered yet (for example `#/overview` before WP-7 lands) falls back to `#/conversations`.

## 5. Permission matrix

Legend: **Y** can · **Own** own dialogs or own appeals only · **R** read-only · **—** hidden, or disabled with the reason. «Enforced by» names the API permission from `roles.py` (403 on writes once B-0 is deployed) or «UI» when only the interface applies the rule.

| Capability | Опер. | QA-анал. | Ревьюер | Методолог | Руковод. | Директор | Гость | Админ | Enforced by | Stories |
|---|---|---|---|---|---|---|---|---|---|---|
| Dialog list, card, call playback | Own¹ | Y | Y | Y | Y | R | R | Y | UI (API read is global) | CONV-10, CARD-01, OPR-02 |
| Gross-violations register `#/violations` | —² | Y | Y | Y | Y | R | R | Y | UI | GV-01..03 |
| Import by group_id, upload, paste | — | Y | — | Y | Y | — | — | Y | API `conversations.import` | CONV-01..06 |
| Evaluate, re-evaluate, apply a version to dialogs | — | Y | Y | Y | Y | — | — | Y | API `evaluations.run` + run guards | CONV-07, CHK-16 |
| File an appeal («Оспорить оценку») | Own | Y | Y³ | — | Y | — | — | Y | API `appeals.create` + own-dialog check | APL-01 |
| Withdraw an own open appeal (P1) | Own | Own | Own | — | Own | — | — | Y | API (author check) | APL-04 |
| Decide an appeal: confirm, change, N/A | — | — | Y | — | Y | — | — | Y | API `appeals.decide` | APL-05..08 |
| Read the active checklist | R | R | R | Y | Y | R | R | Y | — | CHK-01 |
| Add, change, replace, reweight; save a version (draft or active); activate | — | — | — | Y | Y | — | — | Y | API `checklists.write` | CHK-02..15 |
| What-if of a draft («изменятся N диалогов») | — | R | R | Y | Y | — | — | Y | API `read` (no writes, no model calls) | CHK-18 |
| Overview `#/overview` | —⁴ | Y | Y | Y | Y | Y | R | Y | UI | EXE-01, EXE-02 |
| Analytics `#/analytics` | — | Y | Y | Y | Y | R | R | Y | UI | ANL-02, ANL-03 |
| Operator scorecard `#/operators/:name` (P1) | own via `#/my` | Y | Y | Y | Y | R | — | Y | UI | ANL-06 |
| CSV exports and the PDF card | own PDF | Y | Y | Y | Y | Y | — | Y | UI (GET) | EXP-05, EXP-06 |
| Validate and download results.json | — | Y | — | — | Y | — | — | Y | UI (GET) | EXP-01 |
| Personal settings: theme, language, signature | Y | Y | Y | Y | Y | Y | Y | Y | — (browser only) | SHELL-01, SHELL-08 |
| System settings: judge, masking, few-shot, «Фирменный персонаж» | R | R | R | R | R | R | R | Y | API `settings.write` | ADM-01, SHELL-02 |
| Stand readiness panel (green/red checks) | — | — | — | — | R | — | — | Y | UI (GET `/api/system/readiness`) | DEMO-03, ADM-07 |
| Snapshots and restore | — | — | — | — | — | — | — | API only, with `CQA_ADMIN_TOKEN` | API `admin` | ADM-06 |
| «Сценарии для жюри» | Y | Y | Y | Y | Y | Y | Y | Y | — | DEMO-01 |

¹ The operator's lists show only own dialogs (`GET /api/conversations?operator=<name>`). Another operator's card opens read-only with «Диалог другого оператора» and no «Оспорить оценку».
² Not in the operator's navigation; the operator's own card still shows its gross-violation block.
³ A reviewer files only as a spot-check; deciding one's own appeal triggers the four-eyes warning (§7.3).
⁴ The operator's own summary lives at `#/my`.

## 6. «Own dialogs»

- A dialog is the operator's own when its `operator_name` or `agent_name` equals the chosen operator, case-insensitive, ё = е. This is the same rule the API applies (`is_own` in `api/cqa/routers/appeals.py`).
- 10 DEV dialogs have no detected operator (the 5 calls with masked names and 5 chats without an introduction). Nobody can file an operator appeal on them; a QA-аналитик can. Assigning operators (OPR-06) is cut.
- A refused appeal (403 `not_your_dialog`) reads «Оператор может оспаривать только оценки своих диалогов» and keeps the typed comment.

## 7. Attribution

### 7.1 What the client sends

- **Writes** (POST, PUT, PATCH, DELETE) carry `X-CQA-Role: <code>`, `X-CQA-User: encodeURIComponent(<signature>)` and `X-Request-ID: web-<12 hex>`. The signature is the chosen operator for Оператор and the display name for staff roles.
- **Reads** carry no custom headers, so the browser sends no CORS preflight to Railway. They run as `demo` with read permission. `GET /api/me` is not used.
- Every response carries `X-Request-ID` (exposed to cross-origin scripts by the API); the client keeps it on errors.
- Bodies: appeals add `author_role` and `author_name`; decisions add `reviewer_role` and always an explicit `add_to_fewshot: false` (the API default is `true`). `author_label` and `reviewer_label` are not sent, so the API composes «Айгерим С. (Ревьюер)» from the headers. Against an API build without `/api/version` (the old one) the client also sends both labels, built as «Имя (Роль)» (`actorFields()` in WP-0).

### 7.2 What is stored and shown

| Event | Stored (API) | Shown in the UI |
|---|---|---|
| Appeal filed | `author_role`, `author_name`, `author_label`, comment, `created_at`; audit row with the request id | «Апелляция · Оператор · Нина · 07.11, 21:40» on the criterion, in the review queue and in «Мои апелляции» |
| Proposed answer | first comment line «Предлагаемый ответ: 7 из 10» (P0); an additive `requested_answer` field only if the team lead adds it (P1) | «Оператор просит: 7 из 10» on the review card; the appeal dialog shows the preview «78,46 → 85,38» |
| Decision | `reviewer_role`, `reviewer_label`, action, new answer, reason; totals recomputed | on the criterion: «4 → 7 · Изменено ревьюером · Айгерим С. · 07.11, 21:44 · <причина>»; on the ring: final total, original struck through, «после апелляции»; «История»: Оценка ИИ → Апелляция → Решение → Итог |
| Checklist version | `change_note`, version number, base version; audit row with role and name | versions table: «v2 · Черновик · «C7: вес 5 → 10» · 07.11, 21:50» |
| Evaluation run or apply | audit row `evaluation.run` with role, name, request id | job card «Запустил: Методолог · Бекзат Н.» (P1) |
| Settings change | audit row with old → new values | not shown tonight (audit UI is cut; Swagger `/api/audit` answers a question) |

### 7.3 Four eyes

When the reviewer's signature equals the appeal author's (case-insensitive), the decision panel shows «Вы оспариваете и решаете сами: передайте апелляцию коллеге» and «Сохранить решение» asks for a second confirmation. P0 (cheap, from the enterprise proposal).

### 7.4 Errors carry the request id

Every error toast shows the API message, the code and the request id with «Копировать»: «Недостаточно прав для этого действия · forbidden · запрос 8f2c41ab12cd». The same id is in the audit row and the server log. P0.

## 8. Hands-on safety (a juror clicks alone)

- Администратор is not in the role menu unless `?role=admin` was opened in that browser; system settings are read-only for every other role.
- Checklist v1 (organizer baseline) opens read-only; edits become a new version, saved as a draft by default; results.json is always built from v1 and the AI's answers.
- A pasted dialog never overwrites an existing one (no id is sent; a 409 offers «Сохранить как копию»).
- The few-shot switch («Использовать как пример для ИИ») is off and disabled with the reason «Отключено до фиксации набора примеров» until the ML owner freezes the bank; the hosted setting `fewshot_from_reviews` is false.
- Run guards on the hosted API (team lead, B-6): at most 25 dialogs per run, 2 concurrent jobs, a daily budget; runs above 20 dialogs ask for confirmation with the dry-run estimate.
- No delete endpoints exist. After each hands-on session the team restores the `baseline` snapshot and reruns `scripts/seed_demo.py` (idempotent) and the readiness check.

## 9. The presenter's answers

| Question | RU answer | EN answer |
|---|---|---|
| «Кто угодно может выбрать роль ревьюера?» | Это демо без входа, как разрешили организаторы. Роль и имя — это подпись и ограничения интерфейса, а не аутентификация. Сервер проверяет права роли на каждое изменение, пишет журнал действий с кодом запроса и отклоняет апелляцию оператора на чужой диалог (403). В продакшене пользователь и роль приходят из Entra ID и Dynamics 365, а параметры `?role=` и `?as=` отключаются. | It is a demo without sign-in, as the organizers allow. The role and name are a signature plus interface guard rails, not authentication. The server checks the role's permission on every change, writes an audit trail with the request id, and refuses an operator's appeal on someone else's dialog (403). In production the user and the role come from Entra ID and Dynamics 365, and the `?role=` and `?as=` parameters are switched off. |
| «Откуда берутся операторы?» | Из самих диалогов: имя оператора определяется по представлению в первой реплике. Для неопознанных диалогов апелляцию подаёт QA-специалист. | From the conversations themselves: the operator's name comes from the introduction in the first reply. For unidentified dialogs a QA specialist files the appeal. |
| «Почему оператор не видит чужие оценки?» | Оператор работает со своими диалогами: список фильтруется по его имени, чужая карточка открывается только для чтения, а сервер не примет апелляцию на чужой диалог. | Operators work with their own dialogs: the list is filtered by name, someone else's card is read-only, and the server refuses an appeal on another operator's dialog. |
| «Кто увидит решение?» | Оператор — в «Мои апелляции» с причиной; карточка показывает исходную оценку, решение и итог; руководитель видит итог в обзоре. | The operator in «Мои апелляции» with the reason; the card shows the original score, the decision and the final result; the lead sees the outcome in the overview. |

## 10. The four live asks, by role

| Live ask (ORG §3) | Roles and clicks | What proves it |
|---|---|---|
| 1. All dialogs with gross errors and why | any staff role → sidebar «Грубые нарушения 8» → «Почему так классифицировано» → «Открыть в диалоге» | type chips 1 / 4 / 3, the rule, the explanation, the quote «№10 · 01:23», the card opens on C8 with turn №10 in peach |
| 2. An operator sends a result to appeal | role pill → Оператор → «Нина» → «Мои оценки» → p1-ru-005 → «Почему снижена оценка» → «Перейти к фрагменту» → «Оспорить оценку» (C6, proposed 7, comment) → role pill → Ревьюер → «Апелляции» → «Изменить → 7», reason → «Сохранить решение» → role pill → Оператор → «Мои апелляции» | «Апелляция · Оператор · Нина»; preview 78,46 → 85,38 before saving; card «4 → 7 · Изменено ревьюером · Айгерим С.»; «Мои апелляции»: «Изменено: 4 → 7 · итог 78,46 → 85,38 · <причина>» |
| 3. Change the checklist | role pill → Методолог → «Чек-листы» → «Создать версию на основе v1» → «+ Добавить критерий» (C9) / edit a row / C7 weight 5 → 10 with «Нормализовать до 100» → «Сохранить как черновик» → «Применить к диалогам» (3 dialogs, partial) | next free code C9; Σ and shares; what-if line from `…/impact`; dry run «0 вызовов модели · $0,00» for a weight-only change; old → new totals |
| 4. The result of one dialog with its justification | QA-аналитик → any row, or `#/conversations/p1-ru-029?criterion=C8&turn=10` | ring «51,25 / 100», rows «earned / weight», «Почему снижена оценка», N/A with redistributed weight, «Как посчитан итог», evidence №10 in peach; for p4-ru-002 the two-track waveform |
| Hands-on | anyone → «Сценарии для жюри» (7 scenarios, each switches the role and opens the target) | no dead ends, safe defaults (§8) |

The «Сценарии для жюри» drawer and the landing section use the same scenario list (`web/src/features/jury/scenarios.ts`, WP-8).

## 11. Cut on purpose

Top bar, `#/start` workplace page, blocking role picker, notifications bell, help and glossary panel, keyboard shortcuts, teams, coaching notes and acknowledgements, operator assignment, the director's critical-only appeal permission, a lead relabelled «Руководитель QA», admin home `#/admin`, visitor home `#/overview`, audit and admin consoles, calibration UI, access codes for hands-on (DEMO-18), appeals inside the Dynamics 365 embed. Each is either answered by Swagger and the CLI or not needed for the four asks.
