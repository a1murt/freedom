# CQA design proposal · Corporate-premium, faithful to the organizer mockup

> **Angle.** Light by default; deep-green sidebar; white cards; score ring; criteria rows with check and cross tiles; two-track waveform; peach evidence highlight; «Почему снижена оценка»; a primary and a secondary button. Freedom green tokens, SF Pro with an Inter fallback, one design system in code, no cartoons, an optional dark theme, and executive-grade dashboards.

| | |
|---|---|
| Status | Proposal for the team lead's decision, 2026-11-07, about H+6.5. Nothing in it is implemented yet. |
| Binding inputs | `docs/ORGANIZER_UPDATES.md` (wins on every conflict), `docs/USER_STORIES.md` (223 stories; the IDs are used throughout), `docs/design/organizer-mockup.png` |
| Other inputs | `docs/ARCHITECTURE.md`, `docs/ARCHITECTURE_V2.md`, `docs/API.md`, `docs/WEB.md`, `docs/arch/roles.md`, `docs/arch/overview.md`, `docs/INTEGRATION_D365.md`, the Figma kit `GLRFuWwhmuXArmiCgHz9Wr` (Design Book «03 Color», Components, Landing `91:237`; read only), the current `web/src` and `api/cqa` (HEAD `99a1c9e` plus the working tree), and read-only GETs against the hosted API |
| Out of scope | The results.json builder, validator, CLI and endpoints and the control-run script. The team lead is building these; §5.10 designs only the UI entry point. Judge prompts, specs, the gold set, the harness and the local model belong to the ML owner, so this proposal covers only the product UI and the API around them. |

**How to read.** §0 is the decision summary. Coding agents should read §7.1–§7.2 first (the rules and the frozen contracts), then their work package in §7.3, then the §4 and §5 sections it links to.

**Facts checked on the hosted instance** (07.11, about 20:30 UTC+5, GETs only):
- 65 dialogs evaluated: RU 35, KZ 15, EN 15; 60 chats and 5 calls. Mean 82,04, median 95,00, 13 dialogs below 60.
- 8 gross violations: insult 1, paid service bypassing the company 4, disclosure 3. 0 appeals.
- Provider anthropic · claude-opus-5-5: 75 evaluations, $16,22 in total, mean $0,2162, mean latency 14,9 s. First response: mean 71,5 s, 0 % over 180 s.
- Settings: `default_theme = light`, `mascot_enabled = false`.
- `/api/audio/p4-ru-002/peaks` works: 620,38 s, with client, operator and other tracks.
- `/api/roles`, `/api/overview` and `/api/version` return 404, because the working-tree backend is not deployed yet.

Every screen below works against both the deployed API and the working-tree API (feature detection, §6.4).

---

## 0. Summary

### 0.1 Thesis

1. **Look like the organizer drew it.** The conversation card reproduces the mockup one to one: the title with its grey meta line, the two-track waveform with the peach fragment, the «Расшифровка» and «Оценка качества» cards, the ring «51,25 / 100», criteria rows with check and cross tiles and «earned / max», «Почему снижена оценка», and «Перейти к фрагменту» next to «Оспорить оценку». Every other screen is built from the same parts.
2. **Every live ask in two clicks or fewer, from anywhere.** «Грубые нарушения» is a sidebar item. «Апелляция оператора» is one role switch away (Оператор · Нина → «Мои оценки»). Adding, editing and reweighting criteria is the main surface of the checklist editor. The card itself is the dialog result.
3. **Roles are visible, separate actions without login.** A role selector sits at the top of the sidebar, and operators pick their name from the operators found in the data. Every request carries `X-CQA-Role` and `X-CQA-User`, and every appeal and decision shows who made it.
4. **Corporate, not cute.** Light theme by default. Freedom green used sparingly and at AA contrast. No mascot unless an administrator turns on «Фирменный персонаж». An executive overview where every number carries its definition, n and scope, plus a printable one-page brief.
5. **Built for parallel delivery.** Tokens and component contracts are frozen first. Then 12 frontend and 6 backend work packages run with disjoint file ownership. Routes, i18n namespaces and mock handlers are auto-discovered and every API domain has its own module, so no two agents edit the same file.

### 0.2 Decisions (each lives in one place, so it is a one-line change)

| # | Decision | Where |
|---|---|---|
| D1 | Light theme by default: `#F5F7F5` canvas, white cards with a 1 px `#E3E8E3` border and 16 px radius, `#16201A` text. Dark theme (green-black) is opt-in. Print and embed are always light. | §4.2 |
| D2 | Sidebar `#0F5431` (green/800) with an active pill `#2B7C36` and white 600 text. No top bar: the content starts with the page title, as in the mockup. Role, language, help and API status live in the sidebar. | §2.3, §4.7 |
| D3 | Primary button `#1F7028` with white text (6,2:1). Secondary button is white with a 1 px `#1F7028` border and `#1F7028` text. `#51AF3D` is for non-text graphics only (2,8:1 on white). Text on a `#51AF3D` fill is ink, never white. | §4.2 |
| D4 | Evidence is peach `#FDEBD3` with a `#F2B66D` border. One token pair, which supersedes `#FFEFDB`/`#F3D9AE` in CARD-04 and `#E8B867` in CALL-02. | §4.3 |
| D5 | Fonts: SF Pro through the system stack, then self-hosted Inter (latin, cyrillic, and cyrillic-ext for ә ғ қ ң ө ұ ү һ і). Tabular figures for every number. Monospace only for IDs and JSON. | §4.4 |
| D6 | Totals always show two decimals with the locale separator (51,25 in RU/KK, 51.25 in EN). Points are trimmed (7,5 / 18,75). Exports keep the dot. | §4.4 |
| D7 | Criterion icon: green check when the criterion earned at least 50 % of its applied weight, amber cross below that, grey dash for N/A, red shield for a gross violation. Points show «earned / applied weight». Partial losses still appear in «Почему снижена оценка». | §4.7 |
| D8 | Score bands are defined once: ≥ 85 «Норма», 60–85 «Внимание», < 60 «Критично» (CHK-28 defaults). The ring, score pills, overview and charts all use them. | §4.3 |
| D9 | `components/Mascot.tsx` renders nothing unless `mascot_enabled` is on, and its assets leave the main bundle. The favicon becomes a neutral monogram. | §4.10 |
| D10 | Roles: 7 working roles plus Гость. Deep links default to QA-аналитик. `?role=` and `?as=` set and persist the actor. The embedded card sends `visitor`. | §1 |
| D11 | A landing page at `#/` with live counters and a «Сценарии для жюри» block. The app is behind «Открыть демо». | §3 |
| D12 | The executive overview `#/overview` is the home of Руководитель and Директор. On the deployed API it falls back to `/api/analytics/summary`, `/api/violations` and the conversation list. | §5.8 |
| D13 | Feature routes, i18n namespaces and mock handlers are auto-discovered with `import.meta.glob`, and each API domain has one module with one owner, so work packages own disjoint files. | §7.1 |
| D14 | Backend changes are additive: new optional fields, parameters and endpoints, plus one batch of nullable columns. The only tightening is that comments and reasons must not be blank. | §6 |

### 0.3 What changes for the jury

| Moment | Today (hosted) | After this proposal |
|---|---|---|
| First open | Redirects to `#/conversations`. Dark theme, a mascot in the header and in empty states, two roles in a toggle. | `#/` is a corporate landing: what CQA does, live numbers, «Открыть демо», «Сценарии для жюри». The app opens in the light theme with the deep-green sidebar and no mascot. |
| Ask 1 · gross errors | A «Только с грубыми ошибками» checkbox and a bare C8 badge. | Sidebar «Грубые нарушения 8». Type tiles 1 / 4 / 3. Each dialog shows type, quote with №, explanation, «Почему так классифицировано» and «Открыть в диалоге». |
| Ask 2 · operator appeal | No operator role. Appeals are signed with a translated role name. | Role selector → Оператор → «Кто вы?» → Нина → «Мои оценки» → card → «Оспорить оценку» with the preview 78,46 → 85,38. The reviewer decides in «Апелляции», and «Мои апелляции» shows the outcome. |
| Ask 3 · change the checklist | The editor exists, but adding duplicates codes and «Apply» always targets every dialog. | Checklist editor: «+ Добавить критерий» with the next free code (C9), «Заменить», a weight stepper with Σ and «Нормализовать до 100», save as draft or active with a note, a diff of old → new values, and «Применить к диалогам» with a scope and a $0 estimate for weight-only changes. |
| Ask 4 · dialog result | A dense two-pane card, 0-based `[9]` chips, no «Почему снижена оценка». | The mockup card: ring 51,25 / 100, gross-violation strip, rows like «0 / 18,75», «Почему снижена оценка», «Перейти к фрагменту», «Как посчитан итог», and 1-based №10 everywhere. |
| Hands-on | Anyone can change settings; there are no scenarios. | A «Сценарии для жюри» panel, settings read-only outside Admin, budget and run caps (backend guards), snapshot restore. |

---

## 1. Role model

### 1.1 Principles

- **No login**, as the organizer FAQ says. The actor is self-declared in the UI and sent as headers. The API attributes every write to that actor and checks the permission matrix on writes (`api/cqa/security/roles.py`, ARCHITECTURE_V2 §6). This is attribution plus guardrails, not authentication. Production SSO is D365-06 (later).
- **The organizers want submitting an appeal and reviewing it shown as separate actions.** Each role therefore has its own home, its own navigation and its own action set, and switching role is a visible, deliberate act.
- **The UI mirrors the backend role codes** (`operator`, `analyst`, `reviewer`, `lead`, `director`, `methodologist`, `admin`, `visitor`), so a 403 from the API and a hidden button in the UI always agree. The `demo` role (no header, every permission) is never offered: once the new UI is live it always sends a concrete role.

### 1.2 Roles

| Code | Label RU / EN | Who it represents | Home | Primary jobs | Stories |
|---|---|---|---|---|---|
| `operator` | Оператор / Operator | A contact-centre operator (Нина, Алексей, Мұрат…) | `#/my/evaluations` | Read own evaluations, understand point losses, dispute a criterion, follow appeal outcomes | OPR-01..05, APL-01 |
| `analyst` | QA-аналитик / QA analyst | QA specialist | `#/conversations` | Import, evaluate, read results, file appeals, export (CSV, PDF, results.json) | CONV-*, CARD-*, EXP-01 |
| `reviewer` | Ревьюер / Reviewer | Senior QA who decides disputes | `#/review` | Decide appeals (confirm, change, set N/A), confirm or overturn gross violations, spot-check criteria | APL-05..13, GV-04/05 |
| `lead` | Руководитель группы / Team lead | QA lead or team lead | `#/overview` | Overview, operators, violations, appeals, checklist changes | EXE-*, ANL-*, CHK-* |
| `director` | Директор / Director | Director of client service | `#/overview` | Read KPIs and risks, print the brief; read-only | EXE-01..08 |
| `methodologist` | Методолог / Methodologist | Owner of the checklist and its rules | `#/checklists` | Add, edit, reweight and replace criteria; versions, diff, apply | CHK-01..21, DEMO-05/12 |
| `admin` | Администратор / Administrator | Platform administrator | `#/admin` (the backend currently says `#/settings`; BE-F aligns it) | Settings, models, limits, snapshots, readiness, audit | ADM-*, DEMO-03/04 |
| `visitor` | Гость / Visitor | The audience, an embed, a read-only link | `#/` | Read only | DEMO-18, D365-01 |

**One-line descriptions** for the picker and the tooltips (RU / EN). The KK strings go into the i18n namespace and need native review.

- Оператор: «Мои оценки и апелляции по своим диалогам» / «My evaluations and appeals on my own dialogs»
- QA-аналитик: «Импорт и оценка диалогов, обоснования, экспорт» / «Import and evaluate dialogs, reasoning, exports»
- Ревьюер: «Разбор апелляций: подтвердить или изменить оценку» / «Resolve appeals: confirm or change the score»
- Руководитель группы: «Качество команды, операторы, нарушения, решения» / «Team quality, operators, violations, decisions»
- Директор: «Ключевые показатели и риски, только просмотр» / «KPIs and risks, read-only»
- Методолог: «Чек-листы: критерии, веса, версии и применение» / «Checklists: criteria, weights, versions, apply»
- Администратор: «Настройки, модели, лимиты, снимки базы» / «Settings, models, limits, database snapshots»
- Гость: «Только просмотр» / «View only»

### 1.3 Choosing, switching and persisting the role

- **Role selector** at the top of the sidebar, right under the «CQA» wordmark. It is a pill: «QA-аналитик ▾», with the name on a second line when one is set («Оператор · Нина»). The menu lists the 8 roles, each with its description and home. The current role is checked. Choosing a role navigates to its home unless the current route is allowed for the new role, in which case the page stays and re-renders with the new permissions.
- **First-visit picker** (`RolePickerDialog`) opens from the landing's «Открыть демо» when no role is stored.
  - Title: «Выберите роль».
  - Subtitle: «Демо без входа: роль определяет доступные действия и подписывает ваши апелляции и решения».
  - Body: 2×4 role cards. «QA-аналитик» carries the badge «рекомендуем начать».
  - Choosing «Оператор» goes on to step 2, «Кто вы?» (§1.4).
  - Footer: «Роль можно сменить в любой момент в левом меню».
- **URL parameters** apply on load and are written to storage:
  - `?role=<code>` sets the role. An unknown code shows the toast «Роль “xyz” не распознана» and keeps the current role.
  - `?as=<name>` sets the operator identity and implies `role=operator` when no role is given.
  - `?lang=ru|kk|en`, `?present=1` (presentation mode), `?theme=dark|light` (session only).
- **Persistence**, all through one `safeStorage` wrapper with try/catch and an in-memory fallback when storage is blocked: `cqa.role`, `cqa.identity.<role>`, `cqa.lang`, `cqa.theme`, `cqa.nav`, `cqa.present`, `cqa.scenarios.done`, `cqa.seen.<hintId>`.
- **Defaults.** A fresh browser that deep-links into the app (for example `#/conversations/p1-ru-029`) gets QA-аналитик and a one-time hint on the selector: «Вы работаете как QA-аналитик. Роль можно сменить здесь.» A stored role that the build no longer knows falls back to QA-аналитик. The embedded card always sends `visitor`, unless the host passes `CQA_CONFIG.role` (D365-03).
- **Switching mid-draft.** If an appeal form, a decision form or an unsaved checklist draft is open, a confirm dialog appears: «Есть несохранённые изменения. Переключить роль?» with «Остаться» and «Переключить; черновик сохранится». Drafts are kept per role in `sessionStorage`, and nothing is ever submitted under the new role.
- **Headers.** `api/client.ts` adds `X-CQA-Role`, `X-CQA-User: encodeURIComponent(name)` (when set) and `X-Request-ID` to every request. Error toasts show the code and the request id (SYS-08). The client stops sending `author_label` / `reviewer_label`, because the API derives them from the headers (API.md, "Architecture phase additions").
- **Source of truth.** `web/src/lib/roles.ts` mirrors `roles.py`: codes, RU/EN/KK labels, permissions, home and nav order. When `GET /api/me` exists (working tree; 404 on hosted today), the UI cross-checks the permissions and logs a mismatch in dev. It never depends on that endpoint to render.

### 1.4 Operator identity: «Кто вы?»

- The list comes from `GET /api/operators`: 55 named operators plus the «—» group on DEV, and «—» is excluded. It shows the name, language and number of dialogs («Нина · RU · 1 диалог», «Nina · EN · 1 диалог»), sorted alphabetically.
- Search is case-insensitive and handles Kazakh letters («мұр» finds Мұрат).
- The demo persona is pinned at the top as «Рекомендуем для демонстрации: Нина (p1-ru-005, 78,46)» (DEMO-02).
- Footer note: «Демо без входа: выбор пользователя фильтрует данные и подписывает апелляции; в Dynamics 365 пользователь берётся из учётной записи». A link «Нет в списке?» explains that a team lead assigns dialogs without a detected operator (OPR-06, later).
- States: loading (skeleton rows); API offline (a free-text name is allowed, with a warning that appeals may be refused); empty DB («Операторы появятся после импорта и оценки диалогов»); a name that no longer exists in `?as=` (the picker opens with «Оператор не найден»).
- **Other roles** may set an optional display name (role menu «Как вас подписывать?», or Settings → Профиль) for attribution. It is sent as `X-CQA-User` and is never required.

### 1.5 Permission matrix

Legend: **Y** can do · **Own** own dialogs or own appeals only · **R** read-only · **—** hidden, or shown disabled with the reason «Доступно роли …». Enforcement: **API** = 403 from the middleware or handler (working tree; deploy pending, §6.1); **UI** = hidden in the UI only.

| Capability | Опер. | Аналит. | Ревьюер | Руковод. | Директор | Методолог | Админ | Гость | Enforced | Stories |
|---|---|---|---|---|---|---|---|---|---|---|
| Browse dialogs, open cards, listen to calls | Own¹ | Y | Y | Y | R | Y | Y | R | UI (API read is global) | CONV-10, CARD-01 |
| Import by group_id, upload, paste | — | Y | — | Y | — | Y | Y | — | API `conversations.import` | CONV-01..06 |
| Evaluate, re-evaluate, apply a checklist version | — | Y | Y | Y | — | Y | Y | — | API `evaluations.run` + guards | CONV-07, CHK-16 |
| File an appeal on a criterion | Own | Y | Y² | Y | —³ | — | Y | — | API `appeals.create` + own-dialog check | APL-01 |
| Withdraw or edit an open appeal | Own | Own | Own | Own | — | — | Y | — | API (new, author check) | APL-04 |
| Decide an appeal (confirm, change, N/A) | — | — | Y | Y | — | — | Y | — | API `appeals.decide` | APL-05..08 |
| Confirm or overturn a gross violation | — | — | Y | Y | — | — | Y | — | API `appeals.decide` | GV-04, GV-05 |
| Read the active checklist | R | R | R | R | R | Y | Y | R | — | CHK-01 |
| Add, change, reweight, replace criteria; save, activate | — | — | — | Y | — | Y | Y | — | API `checklists.write` | CHK-02..15 |
| Overview, analytics, operator scorecards | own summary | Y | Y | Y | R | Y | Y | R | UI | EXE-*, ANL-* |
| CSV and PDF exports | own PDF | Y | Y | Y | Y | Y | Y | R | UI | EXP-05/06 |
| Export and validate results.json | — | Y | — | Y | — | — | Y | — | UI | EXP-01 |
| Personal settings (language, theme, display name, presentation mode) | Y | Y | Y | Y | Y | Y | Y | Y | — | SHELL-08 |
| System settings (judge, privacy, limits, integrations, «Фирменный персонаж») | R | R | R | R | — | R | Y | R | API `settings.write` | ADM-01, SHELL-02 |
| Snapshots, readiness, audit CSV | — | — | — | — | — | — | Y | — | API `admin` | ADM-05/06 |
| Audit log (read) | — | R | R | R | R | R | Y | — | — | ADM-05 |
| Calibration labels | — | R | Y | Y | R | Y | Y | — | API `calibration.write` | CAL-* (later) |

¹ The API lets every role read every dialog (ARCHITECTURE_V2 §16). The operator portal lists only the operator's own dialogs. Another operator's card opens read-only with the note «Диалог другого оператора» and no «Оспорить».
² Ревьюер files only as a spot-check (APL-15, could).
³ «Запросить подтверждение» for directors (APL-01) needs `appeals.create` limited to the critical criterion in `roles.py`. It is marked *could* and is not in the must-demo slice.

### 1.6 Attribution

| Event | Stored (API) | Shown in the UI | Notes |
|---|---|---|---|
| Appeal filed | `author_role`, `author_name`, `author_label`, `created_at` (+ `requested_answer`, `cited_turns`, `reason_category`, §6) | «Апелляция · Оператор · Нина · 07.11 18:40» on the criterion, in the queue, in History | Default from headers; operators must match the dialog's `operator_name` or `agent_name` (403 `not_your_dialog`) |
| Appeal withdrawn / edited | `withdrawn_at`, `edited_at`, `original_comment` (new) | «Отозвана · Оператор · Нина · 18:52», «изменено 18:42» | History keeps the original |
| Decision | `reviewer_role`, `reviewer_label` (+ `reviewer_name`, `violation_type`, new) | «Изменено проверяющим · Ревьюер · Айгерим · 07.11 18:55 · причина…» on the card; «Подтверждено проверяющим» | Four-eyes: if the reviewer's name equals the author's, a warning «Вы оспариваете и решаете сами: передайте апелляцию коллеге» appears and saving needs a second confirmation (SHELL-05) |
| Checklist version | `change_note` + `created_by_role`, `created_by_name` (new) | «v2 · Черновик · «C7 вес 10» · Методолог · Айгерим · 07.11 21:40 · из v1» | CHK-14 |
| Evaluation run / apply | audit `evaluation.run` with actor (working tree) | Job card «Запустил: QA-аналитик · Айгерим» | ADM-07 |
| Settings change, snapshot, restore | audit with actor | Settings history (later), Admin | ADM-05 |

### 1.7 What the jury sees on first open (1280×720 projector or a 14" laptop)

1. **`https://cqa-freedom-pi.vercel.app/` opens `#/`, the landing, in RU.**
   - Above the fold: the white header (CQA wordmark, anchors, RU · KK · EN, «Сценарии для жюри», «Открыть демо») and the deep-green hero with the headline «Качество каждого диалога — по чек-листу и с доказательствами».
   - Two CTAs, and on the right a live product card: the two-track waveform of a real evaluated call with the peach fragment, the score ring, three criteria rows and «Почему снижена оценка».
   - The live counters strip begins at the fold: «65 диалогов оценено · RU 35 · KZ 15 · EN 15 · 8 грубых нарушений · ≈ $0,22 за оценку».
2. **«Открыть демо» opens the role picker** (first visit) or «Продолжить как QA-аналитик» (returning visitor). One click lands on the role's home.
3. **«Сценарии для жюри»** (the second section of the landing, and a sidebar button inside the app) offers six cards, each opening the right screen with the right role set:
   - Грубые нарушения и почему
   - Апелляция оператора
   - Решение ревьюера
   - Изменить чек-лист
   - Результат диалога с обоснованием
   - Оценить новый диалог
4. **Inside the app**: the deep-green sidebar with «CQA» at the top, the role pill, then Диалоги · Грубые нарушения (8) · Апелляции · Чек-листы · Аналитика · Настройки. Each page starts with a bold 32 px title and a grey meta line, exactly as in the mockup. There is no mascot anywhere.
5. **Offline or mock mode** shows a permanent badge «Офлайн-демо на сохранённых данных — новые оценки недоступны» (DEMO-15). A cold start shows «Сервер просыпается, это может занять до 30 с» (SHELL-07).

### 1.8 The four live asks (and «new dialog») as click paths

| Ask (ORGANIZER_UPDATES §3) | Role | Path | What proves it on screen | Stories |
|---|---|---|---|---|
| 1 · All dialogs with gross errors and why | Any (Руководитель by default) | Sidebar «Грубые нарушения» → «Почему так классифицировано» on a row | 8 dialogs sorted by final total; tiles «Оскорбление клиента 1 · Платная услуга в обход компании 4 · Раскрытие конфиденциальной информации 3 · Иное 0»; type, quote with №/time, explanation, rule; «Открыть в диалоге» opens C8 with the turn in peach | GV-01..03 |
| 2 · Operator sends a result to appeal | Оператор · Нина, then Ревьюер | Role pill → Оператор → Нина → «Мои оценки» → p1-ru-005 → «Почему снижена оценка» → «Перейти к фрагменту» → «Оспорить оценку» (C6 preselected) → requested answer 7 → comment → «Отправить на апелляцию». Then role pill → Ревьюер → «Апелляции» → Нина's appeal first → «Изменить → 7» with a reason → «Сохранить решение» | Appeal signed «Оператор · Нина»; preview «78,46 → 85,38»; card shows «4 → 7», struck 78,46 and «после апелляции»; «Мои апелляции» reads «Изменено: 4 → 7 · итог 78,46 → 85,38» | OPR-01..05, APL-01..09, DEMO-02 |
| 3 · Change the checklist (add, edit, weight) | Методолог (or Руководитель) | «Чек-листы» → «Изменить» (creates the v2 draft from v1) → «+ Добавить критерий» / edit a row / weight stepper → «Сохранить версию…» → «Применить к диалогам…» | C9 added at the next free code; Σ 105 in amber with «Нормализовать до 100»; diff «C7 · вес 5 → 10 · только пересчёт баллов»; dry run «0 вызовов модели · $0,00» for weight-only; summary of old → new totals | CHK-01..16, CHK-21, DEMO-05 |
| 4 · Results of a dialog with the justification | QA-аналитик (any) | «Диалоги» → search «p1-ru-029» (or the scenario card) | Ring «51,25 / 100»; gross strip «Оскорбление клиента · №10»; rows «0 / 18,75»; «Почему снижена оценка»; C3 «N/A · вес перераспределён»; chip №10 → peach turn; «Как посчитан итог» with coefficient 1,25 | CARD-01..08 |
| Extra · Analyse a new dialog | QA-аналитик | «Диалоги» → «Импорт ▾ → Вставить текст» → preview → «Создать и оценить» | Parse preview, criterion rows filling live, Trace timestamps after the import | CONV-04, CARD-08 |

---

## 2. Information architecture

### 2.1 Route map (hash router)

Layouts: **bare** means no app shell (landing, embed, print). **app** means the sidebar shell. Writes are always permission-checked (§1.5). Read-only roles see the same page with the write actions removed or disabled with a reason.

| Route | Screen (page title) | Layout | Key parameters (URL state, SHELL-13) | Stories |
|---|---|---|---|---|
| `#/` | Landing (CQA · Conversation Quality Analytics) | bare | `role`, `lang` | LAND-01..03, DEMO-01 |
| `#/overview` | Обзор качества | app | `dataset`, `version`, `language`, `channel`, `period`, `basis` (`final` default, or `ai`), `print=1` | EXE-01..05, EXE-07, EXE-08, ANL-01 |
| `#/conversations` | Диалоги | app | `q`, `dataset`, `language`, `channel`, `status`, `band`, `critical`, `operator`, `criterion`, `answer`, `lost`, `version`, `review`, `sort`, `view` (`table` or `matrix`), `new` (`paste`, `upload`, `import`), `export` (`results`) | CONV-01..11, EXP-01 |
| `#/conversations/:id` | Анализ разговора | app | `criterion` (C1..Cn), `turn` (1-based), `t` (seconds), `evaluation` (pin), `tab` (`history`, `trace`, `audio`, `tech`), `compare` (`a,b`) | CARD-*, CALL-*, APL-09 |
| `#/violations` | Грубые нарушения | app | `type`, `language`, `channel`, `operator`, `review`, `overturned=1`, `tab` (`list` or `near`) | GV-01..05, GV-08 |
| `#/review` | Апелляции | app | `status` (`open`, `decided`, `mine`, `all`), `code`, `language`, `channel`, `author`, `sort` | APL-05..13 |
| `#/review/:appealId` | Апелляция №… | app | — | APL-13 |
| `#/my` → `#/my/evaluations` | Мои оценки | app | `as` | OPR-01, OPR-03, OPR-04 |
| `#/my/appeals` | Мои апелляции | app | `status` | OPR-05 |
| `#/checklists` | Чек-листы | app | — | CHK-01, CHK-14, CHK-20 |
| `#/checklists/:id/versions/:vid` | Чек-лист · vN (editor, read-only for non-writers) | app | `criterion`, `diff` (`a,b`), `apply=1` | CHK-02..21, DEMO-05, DEMO-12 |
| `#/analytics` | Аналитика | app | `tab` (`summary`, `criteria`, `operators`, `cost`, `agreement`, `speed`) plus the scope parameters | ANL-01..12 |
| `#/operators` | Операторы | app | scope parameters, `sort` (`risk` default) | ANL-05 |
| `#/operators/:name` | Оператор · {name} | app | `version` | ANL-06 |
| `#/settings` | Настройки | app | `section` (`profile`, `appearance`, `language`, `judge`, `privacy`, `limits`, `integrations`, `model`, `about`) | ADM-01..04, SHELL-02, EXP-03, DEMO-16 |
| `#/admin` | Администрирование | app | `tab` (`system`, `snapshots`, `jobs`, `control`) | ADM-06, ADM-07, DEMO-03, DEMO-04 |
| `#/audit` | Журнал действий | app | `action`, `entity`, `actor`, `q` | ADM-05 |
| `#/calibration` | Калибровка ИИ (later; ML-facing) | app | — | CAL-* |
| `*` | Страница не найдена | app | — | SHELL-07 |
| `index.html?embed=1&conversation=<id>` | Embedded card | bare | `api`, `data` (D365 custom parameter), `userlcid` (1049 RU · 1033 EN · 1087 KK), `view` (later: `violations`, `overview`, `operator`, `review`) | D365-01, D365-04 |
| `#/overview?print=1` | Сводка качества (A4) | print | scope parameters | EXE-08 |

Unknown routes and unknown ids never redirect silently. They show «Страница не найдена» or «Диалог не найден» with a way back (SHELL-07).

### 2.2 Navigation per role

The order follows SHELL-04 with the mockup's labels and icons (lucide line icons, 1,75 stroke). Badges: «Грубые нарушения» shows the register total; «Апелляции» shows open appeals; «Мои апелляции» shows unread decisions.

| Role | Main group (in order) | Bottom group |
|---|---|---|
| Оператор | Мои оценки (`ClipboardList`) · Мои апелляции (`MessageSquareWarning`, unread badge) · Чек-лист (`SquareCheckBig`) | Настройки · Справка и сценарии |
| QA-аналитик | Диалоги (`MessageSquare`) · Грубые нарушения (`ShieldAlert`, count) · Апелляции (`Scale`, open count) · Чек-листы (`SquareCheckBig`) · Аналитика (`ChartColumn`) | Настройки · Справка и сценарии |
| Ревьюер | Апелляции · Диалоги · Грубые нарушения · Чек-листы · Аналитика | Настройки · Справка и сценарии |
| Руководитель группы | Обзор (`LayoutDashboard`) · Диалоги · Грубые нарушения · Апелляции · Чек-листы · Аналитика · Операторы (`Users`) | Журнал действий · Настройки · Справка и сценарии |
| Директор | Обзор · Грубые нарушения · Аналитика · Операторы · Диалоги · Чек-листы | Настройки · Справка и сценарии |
| Методолог | Чек-листы · Диалоги · Грубые нарушения · Аналитика | Настройки · Справка и сценарии |
| Администратор | Обзор · Диалоги · Грубые нарушения · Апелляции · Чек-листы · Аналитика | Администрирование (`ServerCog`) · Журнал действий (`History`) · Настройки · Справка и сценарии |
| Гость | Обзор · Диалоги · Грубые нарушения · Аналитика | Справка и сценарии |

Calibration («Калибровка ИИ») appears for Ревьюер, Руководитель, Методолог and Администратор only once WP-M (later) ships. Until then its route does not exist, so its item is hidden automatically (§7.1).

### 2.3 Sidebar anatomy (mockup-faithful)

```
┌──────────────────────────┐  width 224 (≥1280) · 64 collapsed (1024–1279, auto) · top bar <768
│  CQA                      │  wordmark 24/800 white; collapsed: "Q" monogram tile
│  ┌──────────────────────┐ │
│  │ ● QA-аналитик      ▾ │ │  role pill: white/10 bg, 40 px, radius 10; 2nd line = name
│  └──────────────────────┘ │
│  ▢ Диалоги                │  item 40 px, radius 10, icon 20, label 15/500 white/80
│  ⛨ Грубые нарушения    8 │  count badge: white bg, green/800 text, 20 px
│  ⚖ Апелляции          2  │
│  ☑ Чек-листы             │
│ ▐▌Аналитика              │  active: #2B7C36 pill, label white 600, aria-current="page"
│                           │
│  ─────────────────────── │  divider white/12
│  ⚙ Настройки             │
│  ? Справка и сценарии     │  opens JuryScenariosPanel (sheet)
│  RU · KK · EN     ● API   │  language segmented (white/10), status dot + host tooltip
└──────────────────────────┘
```

There is no top bar. Page utilities (job indicator, notifications later, «Скопировать ссылку») sit at the right end of the page header row (§4.7 PageHeader). Below 768 px the sidebar becomes a 56 px deep-green top bar with the wordmark, the role pill (compact) and a menu button that opens the navigation as a sheet (SHELL-16).

### 2.4 Deep links

| Link | Opens |
|---|---|
| `#/conversations/p1-ru-029?criterion=C8&turn=10` | Card with C8 expanded, turn №10 in peach and focused (CARD-04) |
| `#/conversations/p4-ru-002?criterion=C6&t=120` | Card with the player cued at 02:00 (CARD-12) |
| `#/conversations/p1-ru-029?evaluation=59` | Pinned evaluation with «Есть более новая оценка · Открыть» (CARD-17) |
| `#/violations?type=disclosure&language=KZ` | Register filtered to p1-kz-015 (GV-01) |
| `#/review/42` | One appeal, expanded (APL-13) |
| `#/my/evaluations?as=Нина` | Operator portal as Нина on a fresh browser (OPR-01) |
| `#/checklists/1/versions/2?diff=1,2` | Studio with the diff open (CHK-13) |
| `#/overview?role=director&language=KZ` | Director overview scoped to KZ (EXE-01, ANL-01) |
| `#/conversations?role=analyst&new=paste` | Paste dialog open (CONV-04, DEMO-01) |
| `#/conversations?export=results&dataset=DEV` | results.json dialog open (EXP-01) |
| `index.html?embed=1&conversation=p1-ru-029&api=<host>` | Embedded card (D365-01) |

The «Скопировать ссылку» action (on lists, the register, the overview and the card) copies the absolute URL and keeps an `?api=` override (SHELL-13).

### 2.5 Cross-screen flows and Back

- The card remembers where it was opened from (router state `from`: list, register, operator portal, review, scorecard). Its back link reads accordingly: «← Диалоги», «← К нарушениям», «← Мои оценки», «← К апелляции 42». Back restores filters (from the URL) and the scroll position (session cache by route key).
- Every number on the overview and in analytics drills down to a filtered list (ANL-02) and shows a removable chip «Из сводки: C5 Эмпатия = Нет · RU ×».
- After a decision in the review workspace, focus moves to the next matching appeal (APL-13).
- `document.title` follows the screen and the entity: «p1-ru-029 · Анализ разговора · CQA», «Грубые нарушения (8) · CQA», «Чек-лист v2 · черновик · CQA». A running job prefixes «(7/10)» (SHELL-15, SYS-03).

---

## 3. Landing page (`#/`)

### 3.1 Goals and rules

- Explain CQA in ten seconds to a juror, a director or an operator, and get them into the right role in one click (LAND-01, LAND-03, DEMO-01).
- Every claim can be checked: live counters from the API carry an as-of stamp, and assumptions are labelled «допущение» (LAND-02). Comparison rows list only capabilities you can verify on the running product.
- **No mascot, no illustrations, no stock photos.** The visuals are real UI components rendered with live data, using the same components as the app, with a bundled snapshot as fallback.
- The structure reuses the Figma Landing (`91:237`, "Rota Landing / Desktop"), rendered corporate: white and very light sections alternate with a deep-green hero and a deep-green CTA band, and gradients stay restrained.
- RU / KK / EN switch; the choice carries into the app. At 375 px nothing scrolls sideways. With the API down the page still renders, with numbers stamped «по состоянию на …».
- The landing is always light, whatever the app theme.

### 3.2 Figma section → CQA section

| Figma Landing frame | Figma content | CQA section | Corporate rendering |
|---|---|---|---|
| Menu bar + hero card + app window + keycaps | "Wrong layout? Tap Shift twice." | §3.3.1 Header, §3.3.2 Hero | White header. Deep-green hero band. The product shot replaces the app window. No mascot. |
| — (added) | — | §3.3.3 Сценарии для жюри | Six scenario cards right after the hero (the jury's most direct need) |
| THE PROBLEM + chapter pills 01–08 | "Does this sound like your typing?" | §3.3.4 Проблема, plus an in-page anchor bar | Three problem cards with UI crops. The pills become section anchors. |
| Chapters 01–03 (slip, backspace spiral, ping-pong timeline) | Mascot illustrations | Problem cards | Crops: a coverage tile, an evidence chip, a violation row |
| Chapter 04 "Fixed before you finish the thought" + steps 1–3 | Three steps | §3.3.5 Как это работает | Five numbered steps with micro-crops |
| Chapters 05–06 (menu bar, layouts) | Product panels + bullets | §3.3.6 Возможности | Two alternating feature chapters plus four tiles |
| — (added) | — | §3.3.7 Для кого | Four audience cards with «Открыть как …» |
| Chapter 07 "Every layout you type in" + chip marquee | Language chips | §3.3.8 Языки и каналы | Three language cards with live counts. A static chip row instead of a marquee. |
| Chapter 08 "It keeps quiet" | Privacy bullets | §3.3.9 Безопасность и модели | Bullets, a models-and-cost card, Dynamics 365 |
| COMPARE | By hand vs Rota | §3.3.10 Сравнение | «Ручная проверка» vs «CQA», verifiable rows only |
| FAQ accordion | Five questions | §3.3.11 Вопросы | Eight questions |
| Final CTA panel (green gradient + mascots) | "Stop retyping words." | §3.3.12 Final CTA band | Restrained green gradient, no characters |
| Footer | Logo + columns | §3.3.13 Footer | Product, developer, results and team columns, plus build info |

### 3.3 Sections

Layout grid: max width 1200 px, 12 columns, 24 px gutters. Section padding is 96 px top and bottom on desktop and 56 px on mobile. Section eyebrows are 13/16 600, letter-spacing .02em, green/700, sentence case. Section titles are «display» 40/46 700 −0.025em (28/34 on mobile). Background order: green (hero), white, `#F5F7F5`, white, `#F5F7F5`, and so on, then the green CTA band, then the white footer.

#### 3.3.1 Header (sticky, white, 64 px, bottom border on scroll)

- **Left:** a monogram tile (28 px, radius 8, `#0F5431`, with a white speech-bubble-and-check line glyph), then «CQA» 20/800 `#16201A`, then «Conversation Quality Analytics» 12/500 text-3. This is a wordmark, not the Freedom Broker logo. Freedom Broker appears only as text, never as a logo.
- **Centre (anchors):** «Как это работает · Возможности · Языки · Безопасность · Вопросы» / «How it works · Features · Languages · Security · FAQ».
- **Right:** RU · KK · EN segmented; ghost button «Сценарии для жюри» / «Jury scenarios»; primary «Открыть демо» / «Open the demo».
- **Mobile:** the anchors and the language switch move into a menu sheet, and only «Открыть демо» stays visible.

#### 3.3.2 Hero (deep-green band)

- **Background:** `linear-gradient(180deg, #0F5431 0%, #0B4127 100%)` plus one `radial-gradient(900px 480px at 85% 0%, rgba(81,175,61,.28), transparent 60%)`. No particles, no images.
- **Layout:** 5 columns of text on the left, 7 columns of product shot on the right. The shot overlaps the next section by 48 px for depth. On mobile the text comes first and the shot is scaled to fit the width.

| Element | RU | EN |
|---|---|---|
| Eyebrow pill (white/12 bg, white text) | CQA · Conversation Quality Analytics | CQA · Conversation Quality Analytics |
| H1 (56/60 700 −0.03em, white; 36/40 on mobile) | Качество каждого диалога — по чек-листу и с доказательствами | Every conversation scored against your checklist, with evidence |
| Subline (19/28, white/80) | ИИ оценивает чаты и звонки на русском, казахском и английском, показывает реплику-основание для каждого балла и проводит спорные оценки через апелляцию и решение проверяющего. | AI evaluates chats and calls in Russian, Kazakh and English, shows the turn behind every score, and takes disputed scores through appeal and a reviewer's decision. |
| Primary CTA (inverse: white fill, `#0F5431` text) | Открыть демо | Open the demo |
| Secondary CTA (1 px white/60 border, white text) | Как это работает | How it works |
| Note (13, white/70) | Без регистрации · роли переключаются в интерфейсе | No sign-up · switch roles inside the app |

**Product shot (`LandingProductShot`).** This is a real composition of app components, not a screenshot:
- **Top:** a `WaveformPlayer` in static mode (§4.7), with peaks from `GET /api/audio/{HERO_ID}/peaks?buckets=160`, lanes «Клиент» and «Оператор», and the peach fragment over the evidence of the top-loss criterion.
- **Bottom:** a compact «Оценка качества» card with the ring (the real total), the first three criteria rows (icon, title, «earned / applied»), «Почему снижена оценка» showing the top-loss criterion title and its point loss, and both buttons (visual only; clicking anywhere opens the demo at that card).
- `HERO_ID` is a constant (`p4-ru-002` by default, or `p1-ru-005`). The team chooses it after CALL-09 re-judging.
- **Privacy:** no transcript text from real calls appears on the landing (EXP-08). Real calls contribute only peaks, criterion titles and points. Transcript snippets on the landing come only from synthetic `p1-*` chats.
- **Fallback:** `features/landing/snapshot.json`, which holds the same composition's numbers and peaks (no text) with an `as_of`. It shows «Снимок на 07.11.2026, 20:30».

**Counters strip** (first white section, directly under the hero): six metrics in a row (3×2 on mobile), each a value 32/700 tabular with a label 13 text-2.

| Metric | RU label | EN label | Source (`GET /api/public/stats`; fallback `/api/analytics/summary` + `/api/violations`) |
|---|---|---|---|
| 65 | диалогов оценено | conversations evaluated | `evaluated` / `count` |
| RU 35 · KZ 15 · EN 15 | языки диалогов | conversation languages | `by_language` |
| 60 · 5 | чатов · звонков | chats · calls | `by_channel` |
| 8 | грубых нарушений найдено ИИ | gross violations flagged by AI | `gross_violations.total` |
| ≈ $0,22 | за оценку, среднее по 75 оценкам | per evaluation, mean of 75 evaluations | `cost.per_evaluation_mean` (providers mean; from the cost statement once EXP-03 lands) |
| ≈ 15 с | на диалог | per conversation | `latency_mean_ms` |

Under the row: «Данные API на {дата, время} (UTC+5) · набор DEV организаторов». A tooltip names the endpoint. While loading, skeleton numbers are shown, never 0. On error, the snapshot is used with its own stamp.

#### 3.3.3 Сценарии для жюри / Jury scenarios (`id="scenarios"`)

Title «Сценарии для жюри» / «Jury scenarios». Subtitle: «Каждый вопрос жюри — в один клик. Роль переключится автоматически.» / «Each jury question in one click. The role switches automatically.» Six cards in a 3×2 grid, each with an icon tile, a title, one sentence and an «Открыть →» link. The same data (`features/help/scenarios.ts`) feeds the in-app panel.

| # | Title RU / EN | Sentence RU | Target |
|---|---|---|---|
| 1 | Грубые нарушения и почему / Gross violations and why | Все диалоги с грубыми ошибками: тип, цитата и обоснование. | `#/violations?role=lead` |
| 2 | Апелляция оператора / An operator's appeal | Оператор Нина оспаривает оценку по критерию. | `#/my/evaluations?role=operator&as=Нина` |
| 3 | Решение ревьюера / A reviewer's decision | Ревьюер подтверждает или меняет балл, итог пересчитывается. | `#/review?role=reviewer` |
| 4 | Изменить чек-лист / Change the checklist | Добавьте критерий, измените формулировку или вес и примените. | `#/checklists?role=methodologist` |
| 5 | Результат диалога с обоснованием / A dialog's result with reasoning | Баллы, обоснования и переход к реплике. | `#/conversations/p1-ru-029?role=analyst&criterion=C8&turn=10` |
| 6 | Оценить новый диалог / Evaluate a new dialog | Вставьте текст — оценка появится примерно через минуту. | `#/conversations?role=analyst&new=paste` |

When a target has no data (for example, no open appeals for scenario 3), the card says so («Открытых апелляций нет — начните со сценария 2») instead of linking to an empty page.

#### 3.3.4 Проблема / The problem (`id="problem"`, white)

Eyebrow «Проблема» / «The problem». Title «Контроль качества, который не успевает за контакт-центром» / «Quality control that can't keep up with the contact centre». An anchor bar like the Figma chapter pills: «01 Проблема · 02 Как это работает · 03 Возможности · 04 Для кого · 05 Языки · 06 Безопасность · 07 Сравнение · 08 Вопросы».

Three cards (number, title, text, UI crop):

| # | Title RU / EN | Text RU | Text EN | UI crop |
|---|---|---|---|---|
| 01 | Проверяется выборка, а не поток / A sample is checked, not the flow | Специалисты читают и слушают малую часть обращений; остальные диалоги остаются без оценки. | QA staff read and listen to a small share of contacts; the rest is never scored. | A coverage tile «65 из 65 оценено» |
| 02 | Оценку трудно отстоять / Scores are hard to defend | Без ссылки на конкретную реплику оператор и проверяющий спорят о впечатлениях, а не о фактах. | Without a link to the exact turn, operators and reviewers argue about impressions, not facts. | An evidence chip «№10 · Оператор · 01:23» next to a peach transcript row (synthetic chat) |
| 03 | Грубые нарушения находят поздно / Gross violations surface late | Оскорбление клиента, платная услуга в обход компании или раскрытие данных всплывают через жалобы. | Insults, paid services bypassing the company or data disclosure come to light through complaints. | A register row with the type chip |

Proof line under the cards, with live numbers: «В наборе DEV ИИ отметил грубое нарушение в 8 из 65 диалогов — это оценка ИИ до проверки людьми.» / «In the DEV set the AI flagged a gross violation in 8 of 65 conversations — an AI verdict before human review.»

#### 3.3.5 Как это работает / How it works (`id="how"`, `#F5F7F5`)

Title «От диалога до решения — пять шагов» / «From conversation to decision in five steps». A horizontal stepper on desktop, a vertical one on mobile. Each step has a number, a title, a sentence and a micro-crop.

| Step | RU | EN | Micro-crop |
|---|---|---|---|
| 1 Импорт / Import | Набор по group_id из API организаторов, файл JSON или вставленный текст. | A set by group_id from the organizer API, a JSON file or pasted text. | Import chip «DEV · 65» |
| 2 Оценка по чек-листу / Checklist evaluation | Каждый критерий оценивается отдельно; итог считает движок правил, а не модель. | Each criterion is judged separately; the rules engine, not the model, computes the total. | Small ring «82,04» |
| 3 Обоснование и реплика / Reasoning and evidence | Ответ, баллы, вес, объяснение и ссылка на реплику или фрагмент записи. | Answer, points, weight, explanation and a link to the turn or the recording fragment. | Evidence chip |
| 4 Апелляция / Appeal | Оператор или аналитик оспаривает критерий с комментарием. | An operator or analyst disputes a criterion with a comment. | Secondary button «Оспорить оценку» |
| 5 Решение / Decision | Ревьюер подтверждает или меняет балл; итог пересчитывается, исходная оценка и решение видны. | A reviewer confirms or changes the score; the total is recalculated and both the original and the decision stay visible. | «78,46 → 85,38» |

#### 3.3.6 Возможности / Features (`id="features"`, white)

Two alternating chapters (text on one side, a real component on the other, as in the Figma chapters 05 and 06), followed by four tiles.

**Chapter A.** «Обоснование, которое можно проверить» / «Reasoning you can verify». Visual: the «Оценка качества» card of p1-ru-029 (synthetic) next to a transcript fragment with a peach turn.
- «Ссылка на реплику из каждого обоснования» / «A link to the turn from every justification»
- «N/A с причиной и перераспределением веса» / «N/A with a reason and weight redistribution»
- «“Как посчитан итог” — по шагам, с округлением» / «"How the total is computed", step by step, with rounding»

**Chapter B.** «Чек-лист меняется за минуту» / «Change the checklist in a minute». Visual: a crop of the checklist editor showing the weight panel with «Σ 105 · Нормализовать до 100».
- «Добавить, заменить или перевесить критерий» / «Add, replace or reweight a criterion»
- «Версии, сравнение и возврат к прежней» / «Versions, comparison and rollback»
- «Частичная переоценка: только изменённые критерии» / «Partial re-evaluation: changed criteria only»

**Tiles (2×2).** Each tile has a crop and a link «Открыть» that opens the screen with the right role.
- «Звонки с плеером» / «Calls with a player»: «Две дорожки — клиент и оператор — и переход к фрагменту записи» / «Two tracks, client and operator, and a jump to the fragment».
- «Грубые нарушения» / «Gross violations»: «Отдельный реестр: тип, цитата, статус проверки» / «A separate register: type, quote, review status».
- «Апелляция и решение» / «Appeal and decision»: «Подача и разбор — разные роли и разные действия» / «Filing and reviewing are separate roles and actions».
- «Аналитика и стоимость» / «Analytics and cost»: «Качество по языкам, каналам и операторам, стоимость оценки» / «Quality by language, channel and operator; cost per evaluation».

#### 3.3.7 Для кого / Who it's for (`#F5F7F5`)

Four cards. Each opens its role's home through «Открыть как …»; Оператор goes through the identity picker. A returning visitor also sees «Продолжить как <роль>» (LAND-03).

| Card | RU text | EN text | CTA |
|---|---|---|---|
| Директор / Director | Индекс качества, риски и стоимость оценки на одном экране. | Quality index, risks and the cost per evaluation on one screen. | Открыть как директор → `#/overview?role=director` |
| Руководитель группы / Team lead | Операторы, нарушения и апелляции команды. | Operators, violations and the team's appeals. | Открыть как руководитель → `#/overview?role=lead` |
| QA-специалист / QA specialist | Оценка диалогов, обоснования, чек-листы. | Dialog evaluation, reasoning, checklists. | Открыть как QA-аналитик → `#/conversations?role=analyst` |
| Оператор / Operator | Мои оценки и апелляция по своему диалогу. | My evaluations and an appeal on my own dialog. | Открыть как оператор → identity picker |

#### 3.3.8 Языки и каналы / Languages and channels (`id="languages"`, white)

Title «Русский, казахский, английский — в диалогах и в интерфейсе» / «Russian, Kazakh and English, in conversations and in the interface».
- Three language cards: «Русский · 35 диалогов», «Қазақша · 15», «English · 15» (live numbers). Each holds one short quoted operator line from a synthetic chat in that language (selected at build time and stored in `snapshot.json`; never a real call).
- A channel row: «Чаты 60 · Звонки 5».
- Static chips: «Да · Иә · Yes», «Нет · Жоқ · No», «N/A».
- Note: «Цитаты остаются на языке диалога; интерфейс переключается: RU · KK · EN.» / «Quotes stay in the dialog's language; the interface switches: RU · KK · EN.»

#### 3.3.9 Безопасность и модели / Security and models (`id="security"`, `#F5F7F5`)

Title «Данные под контролем, модель — на выбор» / «Data under control, the model is your choice». A bullet column on the left and a models-and-cost card on the right.

| Bullet RU | Bullet EN |
|---|---|
| Перед отправкой в облачную модель маскируются телефоны, ИИН, номера карт, e-mail и IBAN. | Phones, IINs, card numbers, e-mails and IBANs are masked before anything reaches a cloud model. |
| Локальная модель через OpenAI-совместимый endpoint (Ollama, vLLM): данные не покидают контур. | A local model through an OpenAI-compatible endpoint (Ollama, vLLM) keeps data inside the perimeter. |
| Каждый вердикт с трассировкой: модель, версия промпта, токены, стоимость, время. | Every verdict is traced: model, prompt version, tokens, cost, time. |
| Встраивается в Dynamics 365 как веб-ресурс — один HTML-файл. | Embeds into Dynamics 365 as a web resource: a single HTML file. |

**Models-and-cost card**, all values live:
- «Судья: Claude Opus 5.5 (облако)»
- «Ансамбль: + Claude Sonnet 5.5»
- «Локальная модель: {настроена / не подключена на этом стенде}», from `/api/health` `providers.local`. Neutral styling, never red.
- «Стоимость ≈ $0,22 за оценку (среднее по 75 оценкам)», switching to the cost statement's figure with every call included once BE-C C3 lands (EXP-03)
- «Режим в results.json: external_api»

#### 3.3.10 Сравнение / Compare (white)

Eyebrow «Сравнение». Title «Ручная проверка привычна. Вот чего ей не хватает» / «Manual review is familiar. Here is what it lacks». It uses the Figma compare-table anatomy rendered light: a white card with radius 24, header cells «Ручная проверка» (text-2) and a «CQA» pill (green/700, white text). CQA cells use a check icon with text, never an icon alone.

| Row RU / EN | Ручная проверка / Manual review | CQA |
|---|---|---|
| Охват / Coverage | Выборка / A sample | Каждый импортированный диалог / Every imported dialog |
| Основание оценки / Basis of a score | Комментарий проверяющего / A reviewer's comment | Цитата и переход к реплике или фрагменту записи / A quote and a jump to the turn or recording fragment |
| Единые правила / Consistent rules | Зависят от проверяющего / Depend on the reviewer | Версия чек-листа в каждой оценке / The checklist version in every evaluation |
| Грубые нарушения / Gross violations | Находят при прослушивании / Found while listening | Отдельный реестр: тип, цитата, статус / A separate register: type, quote, status |
| Спор об оценке / Disputing a score | Переписка / Email threads | Апелляция → решение → пересчёт итога / Appeal → decision → recalculated total |
| Изменение чек-листа / Changing the checklist | Переобучение команды / Retraining the team | Новая версия и переоценка только изменённого / A new version and re-evaluation of changes only |
| Стоимость оценки / Cost of an evaluation | Время специалиста / Specialist time | ≈ $0,22 за диалог, измерено / ≈ $0.22 per dialog, measured |

#### 3.3.11 Вопросы / FAQ (`id="faq"`, `#F5F7F5`)

An accordion (one open at a time, keyboard accessible).

| Question RU | Answer RU | Question EN (answer is a faithful translation) |
|---|---|---|
| Откуда берутся баллы? | Каждый критерий оценивает модель по формулировке из чек-листа и обязательно указывает реплики-основания. Итог считает движок правил: «Да» — полный вес, «Нет» — ноль, шкала — пропорционально весу. | Where do the points come from? |
| Что значит N/A? | Критерий неприменим к диалогу — например, идентификация не требовалась. Его вес перераспределяется на применимые критерии, максимум остаётся 100. Причина N/A видна в карточке. | What does N/A mean? |
| Почему грубая ошибка не обнуляет итог? | Так требует чек-лист: грубые ошибки отмечаются отдельно и попадают в реестр нарушений, а итог по остальным критериям сохраняется. | Why doesn't a gross error zero the total? |
| Как оспорить оценку? | Оператор выбирает критерий, пишет комментарий и отправляет апелляцию. Ревьюер видит спорный балл, комментарий и фрагмент диалога и подтверждает или меняет оценку с указанием причины. | How do I dispute a score? |
| Как изменить чек-лист? | В разделе «Чек-листы»: добавьте, измените или замените критерий, задайте вес, сохраните новую версию и примените её к диалогам. Переоцениваются только изменённые критерии. | How do I change the checklist? |
| Уходят ли данные в облако? | Только при облачной модели и только после маскирования персональных данных. С локальной моделью данные остаются в контуре. | Does data go to the cloud? |
| Работает ли со звонками? | Да: расшифровка, запись с двумя дорожками и переход от обоснования к фрагменту записи. Если приветствие не попало в запись, «Установление контакта» получает N/A. | Does it work with calls? |
| Сколько стоит одна оценка? | Около $0,22 за диалог — среднее по 75 оценкам на Claude Opus 5.5; полная сумма со всеми вызовами модели — в «Настройки → Модель и стоимость». У локальной модели нет платы за вызовы. | How much does one evaluation cost? |

#### 3.3.12 Final CTA band (deep green, radius 24, inset 120 px)

- Background `#0F5431` with a `radial-gradient(800px 400px at 50% 0%, rgba(81,175,61,.30), transparent 70%)`, as in the Figma final-CTA panel minus the characters.
- Title (40/46 white): «Проверьте на реальных диалогах» / «Try it on real conversations».
- Subtitle (white/80): «Без регистрации: выберите роль — оператор, ревьюер, руководитель — и пройдите процесс от оценки до решения.» / «No sign-up: pick a role (operator, reviewer, team lead) and walk the process from evaluation to decision.»
- Buttons: inverse primary «Открыть демо», outline «Сценарии для жюри».
- Note (13, white/70): «Набор DEV организаторов · 65 диалогов · данные на {as-of}».

#### 3.3.13 Footer (white, top border)

- **Brand column:** monogram and «CQA»; the line «Conversation Quality Analytics для Freedom Broker»; «Команда «Я Тебя Могну»».
- **«Продукт»:** Как это работает · Возможности · Сценарии для жюри · Открыть демо.
- **«Разработчикам»:** Репозиторий на GitHub · README · API (Swagger `/docs`) · Интеграция с Dynamics 365 (`docs/INTEGRATION_D365.md` on GitHub).
- **«Результаты»:** `results/dev/results.json` and `results/prod/results.json`. The prod link reads «появится после контрольного этапа» until the file exists on GitHub main (checked by a HEAD request; LAND-02).
- **Bottom line:** «Хакатон Freedom Broker · 2026» on the left; «web {sha7} · API {version} · сборка {время}» on the right (DEMO-16), with a red dot and «Версии web и API различаются» when they differ.

### 3.4 Live data contract

- **One call:** `GET /api/public/stats` (new, §6). It is cached for 60 s, read-only and carries no PII. Response: `{evaluated, conversations, by_language, by_channel, gross_violations{total, by_type}, cost{per_evaluation_mean, total, n}, latency_mean_ms, models[], local_provider{configured, reachable}, as_of, api_version}`.
- **Fallback while the endpoint is not deployed:** `GET /api/analytics/summary` + `GET /api/violations` + `GET /api/health`. These are three GETs, all deployed today.
- **Second fallback:** `features/landing/snapshot.json`, refreshed by a script before the stop-code. It holds numbers and peaks only, no transcripts.
- **Product shot:** `GET /api/conversations/{HERO_ID}/evaluation` + `GET /api/audio/{HERO_ID}/peaks?buckets=160`, lazy-loaded below the hero text so the headline paints first.
- **Cost honesty:** recorded costs today miss cache writes and checklist-compiler calls (about 13 % low; ANL-12, EXP-03). The landing therefore says «среднее по 75 оценкам» until the cost statement (BE-C C3) includes every call, then shows that figure.

### 3.5 Responsive and performance

- **Breakpoints:** ≥ 1280 two-column hero; 768–1279 the shot moves under the text at 90 % width; < 768 single column, 16 px gutters, CTAs full-width and stacked.
- **Bundle:** the landing is a lazy route chunk. Charts and the waveform load on visibility (`IntersectionObserver`). The Inter subsets are preloaded, and `font-display: swap` is set.
- **Accessibility:** there is one H1; sections are `<section aria-labelledby>`; the anchor bar is a `<nav>`; the accordion uses `button[aria-expanded]`. All text on green is white or white/80, which is AA, and nothing on the green band is smaller than 13 px.
- **No cookie banner** (no tracking).
---

## 4. Design system in code

### 4.1 Token architecture

| File (owner WP-A) | Contents |
|---|---|
| `web/src/styles/tokens.css` | Every colour, radius, shadow, motion and font token as CSS custom properties: `:root` holds the light theme (default) and `.dark` the dark theme. Colours are stored as space-separated RGB channels (as `index.css` does today), so Tailwind opacity modifiers (`bg-critical-bg/60`) keep working. The hex value is in a comment. |
| `web/src/styles/fonts.css` | `@import "@fontsource-variable/inter/wght.css"` (subsets latin, cyrillic, cyrillic-ext) |
| `web/src/styles/print.css` | Print overrides: always light, no sidebar, A4 margins, `break-inside: avoid` on cards |
| `web/src/index.css` | Imports the three files above, the base layer, and legacy aliases (`--bg`, `--line`, `--tint`, `--deep`, `--accent-hover`) mapped to the new tokens, so pages that have not migrated keep rendering. INT-1 removes the aliases. |
| `web/tailwind.config.ts` | Maps tokens to utilities: colours, the `fontSize` scale, radii, shadows, `fontFamily` |

Naming follows the kit's Design Book («03 Color», Light/Dark appearance), so designers and code share one vocabulary:

| Kit token | CQA variable | Light | Dark |
|---|---|---|---|
| bg/canvas | `--canvas` | `#F5F7F5` | `#0B120E` |
| bg/subtle | `--surface-2` | `#F2F5F2` | `#18221C` |
| bg/elevated | `--surface` | `#FFFFFF` | `#121A15` |
| bg/inverse | `--sidebar-bg` | `#0F5431` | `#0A2A1A` |
| bg/accent | `--accent` (graphics) | `#2F8F2A` | `#51AF3D` |
| text/primary | `--text` | `#16201A` | `#E8EDE7` |
| text/secondary | `--text-2` | `#4B5A50` | `#B3BFB5` |
| text/accent | `--accent-text` | `#1F7028` | `#8ED47F` |
| border/default | `--border` | `#E3E8E3` | `#25322A` |
| border/focus | `--focus` | `#1F7028` | `#8ED47F` |

The kit's own contrast rules, recoloured, become CQA rules:
- **green/500 `#51AF3D` on white is 2,8:1.** Use it for graphics only, never for text.
- **green/700 `#1F7028` on white is 6,2:1.** Use it for accent text, links and primary buttons with white text.
- **Text on a green/500 fill is ink** (`#0B120E` gives 6,8:1), never white.

### 4.2 Colour tokens

| Token | Light (default) | Dark (optional) | Use | Contrast note |
|---|---|---|---|---|
| `--canvas` | `#F5F7F5` | `#0B120E` | Page background | — |
| `--surface` | `#FFFFFF` | `#121A15` | Cards, sheets, inputs, dialogs | — |
| `--surface-2` | `#F2F5F2` | `#18221C` | Table header, wells, hovered rows | — |
| `--surface-3` | `#E9EEE9` | `#1F2B23` | Pressed, skeleton | — |
| `--border` | `#E3E8E3` | `#25322A` | Card borders, dividers | — |
| `--border-strong` | `#C5CEC6` | `#3A4A3F` | Inputs, segmented controls | 1,6:1. If an a11y audit flags 1.4.11, switch to `#8C978E` (3,0:1). It is a one-token change. |
| `--text` | `#16201A` | `#E8EDE7` | Body and headings | 16,7:1 on white |
| `--text-2` | `#4B5A50` | `#B3BFB5` | Secondary text, labels | 7,3:1 |
| `--text-3` | `#687469` | `#8E9B90` | Timestamps, meta lines («Звонок 024 · Русский · 06:33») | 4,9:1 light, 6,1:1 dark |
| `--text-disabled` | `#9AA59C` | `#5E6B61` | Disabled | not required |
| `--accent` | `#2F8F2A` | `#51AF3D` | Ring arc, progress fill, check icons, primary chart series, play button | 4,1:1 (graphics ≥ 3:1) |
| `--accent-text` | `#1F7028` | `#8ED47F` | Speaker labels, links, earned points, accent text | 6,2:1 |
| `--accent-subtle` | `#F0F8ED` | `#15251A` | Selected row, secondary-button hover | — |
| `--accent-tint` | `#DFF1DC` | `#1B3320` | Check tile background | check icon on it 3,5:1 |
| `--brand-bright` | `#51AF3D` | `#51AF3D` | Operator waveform track, landing gradients (non-text only) | 2,8:1, graphics only |
| `--primary` / `--primary-hover` / `--primary-active` | `#1F7028` / `#195E22` / `#0F5431` | `#51AF3D` / `#68C155` / `#8ED47F` | Primary buttons | — |
| `--primary-fg` | `#FFFFFF` | `#0B120E` | Text on primary | 6,2:1 light, 6,8:1 dark |
| `--focus` | `#1F7028` | `#8ED47F` | 2 px focus outline, 2 px offset (white on the sidebar) | ≥ 3:1 on all surfaces |
| `--sidebar-bg` | `#0F5431` | `#0A2A1A` | Sidebar, landing bands | white on it 9,0:1 |
| `--sidebar-fg` | `rgba(255,255,255,.80)` | same | Nav labels and icons | 6,2:1 |
| `--sidebar-hover` | `rgba(255,255,255,.08)` | same | Nav hover | — |
| `--sidebar-active` | `#2B7C36` | `#1F7028` | Active nav pill (white 600 text) | white 5,2:1 / 6,2:1 |
| `--sidebar-divider` | `rgba(255,255,255,.12)` | same | Divider | — |
| `--overlay` | `rgba(10,20,14,.40)` | `rgba(0,0,0,.60)` | Dialog backdrop, no blur | — |

### 4.3 Status, evidence, score bands, audio tracks and data-viz

| Token group | Light (fg / bg / border / mark) | Dark (fg / bg / border / mark) | Reserved for |
|---|---|---|---|
| `success-*` | `#1F7028` / `#F0F8ED` / `#BEE3B5` / `#2F8F2A` | `#6CC35A` / `#15251A` / `#2F5A2A` / `#51AF3D` | Passed criterion, «Подтверждено», valid |
| `warning-*` | `#B45309` / `#FDF1DC` / `#F5D49A` / `#C27A0E` | `#E8B04B` / `#2E2412` / `#6B5320` / `#D9922E` | Lost points, «На рассмотрении», low confidence, budget at 80 % |
| `critical-*` | `#B3362F` / `#FDECEA` / `#F2B8B3` / `#B3362F` | `#F08A82` / `#2E1614` / `#7A3530` / `#E2675F` | Gross violations, failed evaluations, destructive actions. Nothing else is red. |
| `info-*` | `#1F5FA8` / `#EAF2FB` / `#B9D3F0` / `#2A78D6` | `#7DB7E8` / `#12212F` / `#2D4E6E` / `#4A90E8` | «Изменено проверяющим», provider and local-model notes |
| `neutral-*` | `#4B5A50` / `#F0F3F0` / `#E3E8E3` / `#9AA59C` | `#B3BFB5` / `#18221C` / `#25322A` / `#5E6B61` | N/A, unknown, AI-only (not yet confirmed) |
| `evidence-*` | bg `#FDEBD3`, border `#F2B66D`, active bg `#FBDDB4` | bg `#3A2A14`, border `#B9844A`, active bg `#4A3518` | Evidence turns, the waveform fragment, the review fragment |

**Status needs words and icons as well as colour.** The validator (`dataviz/scripts/validate_palette.js`) gives success `#2F8F2A` and warning `#C27A0E` a protan ΔE of 0,9, which is indistinguishable to many colour-blind readers. Every status mark therefore ships with an icon (check, cross, dash, shield) and a word («Да», «Нет», «N/A», «Грубая ошибка»). This is the mockup's own pattern (SHELL-09).

**Score bands (CHK-28 defaults).**
- «Норма» for totals ≥ 85: ring arc `success-mark`.
- «Внимание» for 60–84,99: ring arc `warning-mark` (`#C27A0E`, 3,4:1 on white).
- «Критично» below 60: ring arc `critical-mark`.
- `--ring-track`: `#E6EBE6` light, `#25322A` dark.
- The thresholds live in one module, `lib/bands.ts`, which reads `scoring_rules.bands` from the checklist version when present and falls back to 85/60.
- The ring number stays ink. Only the arc carries the band colour, as in the mockup.

**Audio tracks.**
- Light: client `#0F5431` (deep), operator `#51AF3D` (bright), other (IVR, system, consultation) `#B7C2B8`, hold band `#E9EEE9` with 45° hatching.
- Dark: client `#BEE3B5`, operator `#51AF3D`, other `#4A5A4E`.
- Lane labels «Клиент» and «Оператор» always sit next to the bars, so identity never depends on colour (validated pair ΔE 29).

**Data-viz palette** (validated with the dataviz script; WP-I re-runs it on any change):

| Role | Light | Dark | Validation |
|---|---|---|---|
| Single series (distribution, loss drivers, KPI bars) | `--accent` `#2F8F2A` | `#51AF3D` | — |
| Two-series comparison (v1 ↔ v2, AI ↔ after appeal, DEV ↔ control) | A `#2A78D6`, B `#C0398A` | A `#4A90E8`, B `#D9589A` | All-pairs PASS: light ΔE 13,5 CVD / 25,4 normal; dark 13,0 / 24,2 |
| Three series (rare; always direct-labelled) | `#1F7028`, `#2A78D6`, `#C0398A` | `#4CA83A`, `#4A90E8`, `#D9589A` | All-pairs PASS in light and dark. More than three series means small multiples or «Прочее». |
| Sequential (heatmap pass rate, low → high) | `#68C155` → `#2F8F2A` → `#1F7028` → `#0F5431` → `#092818` | `#1F5A22` → `#2F8F2A` → `#51AF3D` → `#8ED47F` → `#BEE3B5` | Ordinal PASS in both modes (monotone, ΔL ≥ 0,06, end ≥ 2:1) |
| Bands in charts | success, warning and critical marks, plus direct labels «Норма ≥ 85 · Внимание 60–85 · Критично < 60» | same | Status, never categorical |
| Grid and axis | `#E3E8E3` / `#687469` | `#25322A` / `#8E9B90` | — |

Chart rules (from the dataviz method): one y-axis only; 2 px lines; ≥ 8 px markers; bars with 4 px rounded data ends and 2 px gaps; values in text tokens, never in the series colour; a legend for two or more series; a hover tooltip on every mark; a «Таблица» toggle on every chart (table view); n < 5 cells hatched and labelled «мало данных».

### 4.4 Typography

**Stack**
- `--font-sans: "SF Pro Text", -apple-system, BlinkMacSystemFont, "Inter Variable", "Inter", "Segoe UI", Roboto, system-ui, sans-serif`
- `--font-display: "SF Pro Display", -apple-system, BlinkMacSystemFont, "Inter Variable", "Inter", "Segoe UI", system-ui, sans-serif`
- `--font-mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace` (IDs, JSON, request ids only)

SF Pro is never bundled; it is only referenced through the system stack, which respects Apple's licence. Inter is self-hosted with the cyrillic-ext subset for Kazakh. It adds about 0,3–0,4 MB to the single-file D365 build, inside the 4,5 MB budget.

| Style | Size / line | Weight | Tracking | Use |
|---|---|---|---|---|
| `display-xl` | 56/60 (36/40 mobile) | 700 | −0.03em | Landing H1 |
| `display` | 40/46 (28/34 mobile) | 700 | −0.025em | Landing section titles |
| `h1` | 32/40 (28/36 below 1440 px wide) | 700 | −0.02em | Page title («Анализ разговора»), as in the mockup |
| `h2` | 22/28 | 700 | −0.01em | Card titles («Расшифровка», «Оценка качества») |
| `h3` | 17/24 | 700 | 0 | Sub-blocks («Почему снижена оценка») |
| `body-lg` | 16/24 | 400 / 500 | 0 | Transcript text, criterion titles in the card |
| `body` | 15/22 | 400 | 0 | Default UI text |
| `body-strong` | 15/22 | 600 | 0 | Speaker labels, table emphasis |
| `small` | 13/18 | 400 / 500 | 0 | Meta, timestamps, hints |
| `label` | 12/16 | 600 | .01em, sentence case | Table headers, field labels, eyebrows in the app |
| `num-xl` | 40/44 | 700, tabular | −0.02em | Ring value «51,25» |
| `num-lg` | 32/36 | 700, tabular | −0.02em | KPI values |
| `num-md` | 16/24 | 600, tabular | 0 | Points «15 / 20» (max in text-3, weight 400) |

**Numbers and locales.** `lib/format.ts` (WP-A) formats every number through `Intl.NumberFormat(locale)`:
- `formatTotal(51.25)` gives «51,25» (ru, kk) and «51.25» (en), always with 2 decimals.
- `formatPoints(7.5)` gives «7,5» (trimmed, up to 2 decimals).
- `formatPercent(0.123)` gives «12,3 %».
- `formatMoney(0.2162)` gives «$0,22».
- Durations come out as «10:20»; dates in the locale with the zone (UTC+5).
- `turnNo(idx)` returns `idx + 1`, the one place the 1-based numbering lives (CARD-07).
- `plural(n, forms)` handles RU/KK plurals («1 диалог / 2 диалога / 5 диалогов»).
- Exports (CSV, results.json) keep the dot (SHELL-08).

### 4.5 Space, shape, elevation, motion, icons, density

- **Spacing:** a 4 px grid: 4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 96. Page padding is 32 px (24 px below 1280). Gap between cards 24 px. Card padding 24 px (20 px in compact density).
- **Radius:**
  - `xs` 6: chips, icon tiles.
  - `sm` 8: buttons, inputs.
  - `md` 12: popovers, small cards, transcript highlight.
  - `lg` 16: cards, dialogs.
  - `xl` 24: landing panels.
  - `full`: pills and the play button.
- **Elevation:**
  - Cards: border plus `0 1px 2px rgba(16,32,20,.04)`.
  - Popovers: `0 8px 24px rgba(16,32,20,.12)`.
  - Dialogs: `0 24px 48px rgba(16,32,20,.18)`.
  - The dark-theme glow utilities (`.glow`, `shadow-glow`, the neon `turn-highlight`) are removed (SHELL-03).
- **Motion:** 150 ms ease-out for hover and press; 200 ms for sheets and accordions; 600 ms for the ring arc. `prefers-reduced-motion` makes all of them instant, and evidence jumps scroll without smooth behaviour.
- **Icons:** lucide-react line icons, 1,75 stroke, 20 px in the nav and 16 px inline. The criterion icons are fixed: `Check` (pass), `X` (fail), `Minus` (N/A), `ShieldAlert` (gross violation), `AlertTriangle` (error), `Loader2` (pending).
- **Density:**
  - «Обычная»: rows 52 px, criterion rows 44 px.
  - «Компактная»: rows 44 px, criterion rows 36 px. Applied automatically when the viewport height is ≤ 800 px, so the p1-ru-029 card fits 1280×720.
- **Presentation mode** (`?present=1` or the sidebar footer): base font 112 %, secondary metadata (tokens, cache, provider ids) hidden, the sidebar forced collapsed below 1440 (SHELL-10).

### 4.6 Component inventory

Owner WP in brackets. "Replaces" names the current file or the pattern that goes away.

| Component | File | Key props | Variants / states | Replaces |
|---|---|---|---|---|
| Sidebar | `components/shell/Sidebar.tsx` (B) | `items`, `collapsed`, `onToggle`, `badges` | expanded 224, collapsed 64, mobile top bar; item: default, hover, active (`aria-current`), focus, disabled with reason; badge: count, dot | `AppShell.tsx` SideNav |
| RoleSwitcher | `components/shell/RoleSwitcher.tsx` (B) | `role`, `identity`, `onChange` | pill with name; menu with descriptions; collapsed avatar | TopBar role toggle |
| RolePickerDialog, IdentityPicker | `components/shell/*` (B) | `open`, `onPicked`, `initialRole` · `operators`, `value`, `onChange`, `recommended` | step 1 roles, step 2 name; loading, offline (free text), empty, not found | — |
| PageHeader | `components/ds/PageHeader.tsx` (A) | `title`, `meta: ReactNode[]` (joined with « · »), `back?: {to, label}`, `status?`, `actions?`, `tabs?` | default 32/40, compact 24 (embed, sheets); right slot hosts `GlobalUtilities` | `common.tsx` PageHeader |
| Card | `components/ds/Card.tsx` (A) | `title?`, `titleAddon?`, `actions?`, `footer?`, `padding: none·md·lg`, `tone: default·subtle·critical·warning`, `scrollBody?` | header without divider (mockup), sticky footer | `.card`, `Section` |
| ScoreRing | `components/ds/ScoreRing.tsx` (A) | `value`, `original?`, `size: sm 56·md 96·lg 136·xl 152`, `critical?`, `caption?` («/ 100»), `onClick?`, `ariaLabel` | band arcs; struck original + «после апелляции»; «Итог не рассчитан» (null); skeleton | `common.tsx` ScoreRing |
| ScoreBadge | `components/ds/ScoreBadge.tsx` (A) | `value`, `original?`, `critical?` | band-tinted 24 px pill; tooltip «исходный: 51,25»; shield icon when critical | `ScorePill` |
| CriterionIcon | `components/ds/CriterionIcon.tsx` (A) | `state: pass·fail·na·critical·clean·error·pending` | 24/28 px, radius 6 | — |
| PointsText | `components/ds/PointsText.tsx` (A) | `earned`, `max`, `state`, `original?` | earned green or amber, max text-3; «N/A»; struck original | inline code |
| EvidenceChip | `components/ds/EvidenceChip.tsx` (A) | `no`, `speaker`, `time?`, `quote`, `role: supports·contradicts·context`, `verified?`, `disabledReason?`, `onClick` | role written as a word («Подтверждает», «Противоречит», «Контекст»); out of recording | ad-hoc chips |
| Highlight | `components/ds/Highlight.tsx` (A) | `tone: evidence·evidence-active·range` | peach fill/border; range = 3 px peach left rule | `.turn-highlight`, `.turn-span` |
| Chips | `components/ds/Chip.tsx` (A) | `StatusChip{state}`, `TypeChip{type}`, `LangChip`, `ChannelChip`, `VersionChip`, `ProviderChip`, `CountBadge` | tones neutral·success·warning·critical·info·accent; sizes 20/24; icon + text | `Badge`, `ReviewBadge`, `ProviderBadge` |
| Button | `components/ui/button.tsx` (A) | `variant: primary·secondary·neutral·ghost·destructive·link·inverse`, `size: sm 32·md 40·lg 48·icon`, `loading`, `disabledReason` | default, hover, active, focus, disabled (with tooltip and `aria-describedby`), loading | current button |
| SegmentedControl | `components/ui/segmented.tsx` (A) | `options`, `value`, `onChange`, `size` | radiogroup semantics | inline toggles |
| DataTable | `components/ds/DataTable.tsx` (A) | `columns[{key, header, align, width, sortable, render, hiddenByDefault}]`, `rows`, `sort`, `onSort`, `selection?`, `onRowClick`, `density`, `empty`, `loading`, `error` | sticky 40 px header, `aria-sort`, column chooser «Колонки», stacked cards below 768 | `ui/table.tsx` + ad hoc |
| FilterBar | `components/ds/FilterBar.tsx` (A) | `children`, `activeCount`, `onReset`, `copyLink?` | one row that wraps; active-filter chips with ×; «Сбросить фильтры» | ad hoc |
| Form fields | `components/ui/{input,textarea,select,combobox,checkbox,switch,number-stepper,field}.tsx` (A) | standard + `error`, `hint`, `required` | 40 px (32 in toolbars), 3 px focus ring, error text with icon | current primitives |
| Dialog, Sheet, ConfirmDialog, Popover, Tooltip, Accordion | `components/ui/*` (A) | Dialog sizes sm 440·md 560·lg 720·xl 960; Sheet right 480·640; Confirm `typedConfirmation?` | focus trap and return; overlay without blur; dark tooltip `#16201A` | `dialog.tsx`, `misc.tsx` |
| EmptyState | `components/ds/EmptyState.tsx` (A) | `icon`, `title`, `hint?`, `steps?`, `primary?`, `secondary?`, `variant: first-run·filtered·no-access·offline·not-found` | 48 px icon tile (green/50 bg, green/700 icon); never a mascot unless `BrandMascot` is enabled and the variant is first-run | `common.tsx` EmptyState |
| ErrorState, OfflineBanner, ColdStartNotice | `components/ds/ErrorState.tsx` (A), `components/shell/*` (B) | `error`, `onRetry` | code and request id with «Копировать»; RU/EN/KK text | `common.tsx` ErrorState |
| KpiTile | `components/ds/KpiTile.tsx` (A) | `label`, `value`, `unit?`, `sub?`, `n?`, `definition?` (ⓘ popover), `delta?`, `tone?`, `onDrill?`, `state` | loading, empty («—», never 0), error («нет данных · Повторить»), small sample («малая выборка») | `Kpi` |
| Charts | `components/charts/*` (I) | `DistributionBands`, `Heatmap`, `BarList`, `SmallMultiples`, `TrendLine`, `ScopeBar` | tooltips, table view, n per cell | recharts ad hoc |
| CriterionRow, CriterionDetails, WhyLowered, GrossStrip, TotalBreakdown, QualityCard | `features/card/*` (C) | §4.7 | §4.7 | `EvaluationPanel.tsx`, `CriterionRow.tsx` |
| TranscriptRow, Transcript, TranscriptFragment | `features/card/*` (C) | §4.7 | §4.7 | `Transcript.tsx` |
| WaveformPlayer, MiniPlayer | `components/media/*` (C) | §4.7 | §4.7 | `AudioPlayer.tsx` |
| JobIndicator, JobProgress | `features/jobs/*` (B) | `jobId` | running ring «Оценка: 12 из 65 · $2,10», done with findings, failed, interrupted with «Возобновить» | `JobProgress.tsx` |
| JuryScenariosPanel, HelpPanel | `features/help/*` (B) | `open` | sheet; ticks per browser; «Начать заново» | — |
| Wordmark, BrandMascot | `components/brand/*` (A) | `collapsed` · `pose`, `size` | mascot renders only when `mascot_enabled` | `Mascot.tsx` |

### 4.7 Key component specs

**Sidebar (D2).** `--sidebar-bg` fills the full height.
- Wordmark «CQA» 24/800 white at 20 px from the top (the mockup). Below it, the role pill (§1.3).
- Items are 40 px tall with radius 10, a 20 px icon and a 15/500 label in `--sidebar-fg`. Hover is `--sidebar-hover`. Active is a `--sidebar-active` pill with white 600 text. Focus is a 2 px white outline.
- Count badges are white with green/800 text, 20 px tall.
- Collapsed (64 px) shows icons only, with right-side tooltips and badges as 6 px dots. The role pill becomes an avatar circle with the role's initial.
- Footer: «Настройки», «Справка и сценарии», the language segmented control, and the API status dot (green online, red offline, amber for mock), whose tooltip shows the host (DEMO-15).

**PageHeader.**
- Title in `h1`. The meta line below in `small` text-3 with « · » separators, for example «Чат · p1-ru-029 · Русский · оператор Алексей · 14 реплик» or «Звонок · p4-ru-002 · Русский · 10:20 · 03.11.2026».
- An optional back link above the title (13/500, text-2, «← К нарушениям»).
- The right side holds page actions (at most one primary) and the global utilities (job indicator, «Скопировать ссылку»).
- Status chips follow the title («Черновик», «Активна», «Базовая версия организаторов»).

**Card.**
- White, a 1 px `--border`, radius 16, padding 24.
- Title in `h2` with no divider under it (the mockup). Optional `titleAddon` (version chip, ⓘ).
- With `scrollBody`, the body scrolls inside the card and a sticky footer stays visible. The quality card uses this so «Почему снижена оценка» and its buttons never scroll away.

**ScoreRing.**
- `xl` (152): 10 px stroke on the `--ring-track` circle; the arc in the band colour with round caps, drawn clockwise from 12 o'clock.
- Centre: «51,25» in `num-xl` ink, with «/ 100» below in 15/500 text-3.
- After a decision changed the total, «было 51,25» appears struck under it in 12 px text-3, with the label «после апелляции».
- When `critical`, a 24 px red shield badge sits at the 4 o'clock position on the ring, with the label «Грубое нарушение».
- When the value is null: a dashed track and «—» with «Итог не рассчитан».
- `aria-label`: «Итоговая оценка 51,25 из 100, есть грубое нарушение».
- Clicking opens «Как посчитан итог» (CARD-06).

**CriterionRow (compact, the mockup) and CriterionDetails (expanded).**
- Collapsed row, 44 px (36 compact): `CriterionIcon` · title (`body-lg` 500, truncated with a tooltip) · state badge (`StatusChip`: «На рассмотрении», «Изменено», «Подтверждено», «Перенесено из прошлой оценки») · `PointsText` right-aligned (`num-md`).
- Points are **earned / applied weight**: «0 / 18,75», «7,5 / 18,75», «12,5 / 12,5». A tooltip explains the redistribution: «Базовый вес 15 → применённый 18,75: N/A перераспределён».
- N/A rows show «N/A» in text-3 with «вес перераспределён» in the tooltip.
- C8 shows «Нет грубых ошибок» (clean) or «Грубое нарушение» (critical) instead of points.
- Icon rule (D7): pass when earned ≥ 0,5 × applied; fail when below; N/A; critical when the critical criterion has its critical value; error when the criterion call failed («Не оценён»); pending while running.
- Selecting a row sets the *current criterion* (green/50 background and a 3 px `--accent` left rule) and expands `CriterionDetails` inline as an accordion, one open at a time:
  - **Answer**: «4 из 10», the scale anchor of that step, and the next step up («7 — один заметный сбой…») (CARD-03).
  - **Explanation** (`ExplanationText`): `[n]`, `№n` and ranges `[11]–[14]` become turn links with a hover preview. Amounts such as «$25» and times such as «09:00» are never linked (CARD-04). Until the rationales are renumbered (EXP-02), a reference set that equals the evidence `turn_idx` set is treated as 0-based. This interim heuristic is removed once DEV is regenerated.
  - **Evidence chips** (`EvidenceChip`, 1-based №); «Проверенный фрагмент №10–№11» for `checked_span`.
  - **N/A block**: «Причина N/A: …» and «вес 20 → 0, перераспределён на применимые критерии» (CARD-05).
  - **Readable flags**: «Шум распознавания», with the code in a tooltip (CALL-06).
  - **Confidence**: shown as words only when low, «Низкая уверенность модели · 0,62 — если не согласны, подайте апелляцию» (CARD-09).
  - **Votes and claims**: «Голоса: 2 из 3 судей — Да». Claim statuses «Совпадает / Противоречит / Не проверено» use the contract value `consistent` (CARD-10).
  - **Appeal thread**: author, comment, decision, reviewer, reason (APL-09).
  - **Menu**: «Копировать ссылку», «Копировать обоснование» (CARD-12).

**TranscriptRow (the mockup's «Расшифровка»).**
- A divided list rather than chat bubbles. First line: the time `small` text-3 (`02:08`), then the speaker label `body-strong` in `--accent-text` («Клиент», «Оператор»), preceded by an 8 px dot in the track colour (client `#0F5431`, operator `#51AF3D`), and «№15» right-aligned in `small` text-3. Second line: the text in `body-lg`.
- Bot and system rows are dimmed (text-3). System events render as a centred 13 px line («Клиент находится на удержании»).
- Segments are grouped under quiet headers: «До оператора», «Сегмент оператора», «После разговора» (CARD-01). For calls: «IVR и ожидание · не оценивается» (collapsed), «Консультация с коллегой · клиент не слышит» (collapsed), «до начала записи», «после завершения — не засчитывается» (CALL-04).
- States:
  - `evidence`: peach fill and border, radius 12, 8 px inset.
  - `active` (the jump target): stronger peach and the 2 px focus outline; it receives focus and stays highlighted until something else is selected (CARD-04).
  - `in-range`: a 3 px peach left rule.
  - `duplicate`: collapsed «дубликат · показать».
  - `out-of-recording`: label, no seek.
  - `search-hit`: yellow `mark`.
- Masked-data tokens render as chips «Имя», «ПДн», «Идент. данные» (CALL-06).
- Rows are `button`-like `div`s with `role="listitem"`, `tabIndex=0` and an `aria-label` («№10, Оператор, 01:23: …»).

**WaveformPlayer (the mockup's audio card).**
- Two lanes, 40 px each, labelled «Клиент» and «Оператор» (`body-strong`, 96 px label column). Bars are 2 px wide with 2 px gaps and rounded caps, drawn on a `<canvas>` from `GET /api/audio/{id}/peaks?buckets=600` (`tracks.client`, `tracks.operator`). An optional thin third lane «Прочее» (`tracks.other`, 16 px).
- **Peach fragment**: the current evidence turn, from its offset to the next turn's offset, shaded `--evidence-bg` across all lanes with 1 px `--evidence-border` edges.
- Hold bands are hatched with the label «Удержание 7:06» (CALL-05). Evidence markers are 3 px ticks above the lanes in `--accent-text`.
- **Controls row**: a 40 px round play button (`--accent` fill, white icon); the time «02:18 / 06:33» in `num-md`; a slider (4 px `--border` track, `--accent` fill, 14 px knob with a 2 px white ring; `role="slider"` with `aria-valuetext` «2 минуты 18 секунд из 6 минут 33 секунд»); speed 0,75–2×; «Следующий фрагмент» (`]`); the follow-mode toggle «Следовать за записью».
- **Seek API**: `ref.seek(t, {play, preroll: 2, stopAt?})`. «Перейти к фрагменту» plays from 2 s before the turn and stops at the next turn (8 s at most) (CALL-01).
- **States**:
  - loading: a skeleton of bars.
  - `available: false`: lanes drawn as solid spans from transcript timing, labelled «по расшифровке» (CALL-02).
  - recording unavailable: «Запись недоступна: источник не отвечает. Оценка и расшифровка доступны.» with «Повторить» (CALL-12).
  - collapsed: a 56 px control row only («Показать дорожки»).
- `MiniPlayer` is the compact variant for the review fragment: from 3 s before to 5 s after, ±10 s, 1× and 1,5× (APL-06).
- Static mode (landing): no audio element; peaks and the highlight only.

**Buttons in the mockup.**
- «Перейти к фрагменту»: `primary` `lg`, 48 px tall, radius 8, 15/600.
- «Оспорить оценку»: `secondary` `lg`.
- Both sit side by side at the bottom of the quality card. On narrow cards they stack, primary first.
- A disabled button always explains itself through `disabledReason`: «Апелляция уже на рассмотрении», «Оператор может оспаривать только оценки своих диалогов», «Доступно роли Ревьюер».

**Toasts** (sonner, restyled): white card, radius 12, a 3 px left status rule, title 15/600, description 13 text-2, an optional action. Errors show `code · ID r_8f2c41` with a copy button (SYS-08). Errors stay until dismissed; success toasts last 4 s. Position bottom-right, or bottom-centre on mobile.

### 4.8 Refactor map for the current `web/src`

| Current file | Action | Owner |
|---|---|---|
| `web/index.html` | Remove `class="dark"`. Inline pre-paint script: light unless the stored choice is dark. `theme-color` `#F5F7F5`. Favicon `/favicon.svg` (monogram). Add `og:image` `/og-card.png` (SHELL-15). | A |
| `src/index.css`, `tailwind.config.ts` | Rebuild on `styles/tokens.css`; keep legacy aliases until INT-1; delete `.glow`, `shadow-glow`, neon `.turn-highlight` | A |
| `components/ui/*` (button, badge, checkbox, dialog, dropdown-menu, input, label, misc, select, switch, table, tabs) | Restyle to §4.6. Tabs become underline tabs (2 px `--accent` indicator). Add `segmented`, `popover`, `sheet`, `accordion`, `combobox`, `number-stepper`, `field`. | A |
| `components/common.tsx` | Becomes a re-export shim over `components/ds/*` (ScoreRing, ScorePill → ScoreBadge, ReviewBadge → StatusChip, ProviderBadge → ProviderChip, EmptyState, ErrorState, LoadingBlock → Skeletons, PageHeader, Section → Card, KeyValue, JsonView, Kpi → KpiTile, AnswerLabel, ConfidenceBar → ConfidenceNote). INT-1 deletes it. | A |
| `components/Mascot.tsx` | Gate: returns `null` unless `mascot_enabled`. Poses load through a dynamic `import()` map, so the SVGs leave the main bundle. Satisfies SHELL-02 everywhere on WP-A's first commit; feature WPs then delete their `<Mascot>` usages. | A |
| `components/layout/AppShell.tsx` | Rewritten: Sidebar + main with page header slot; no TopBar | B |
| `components/ErrorBoundary.tsx` | i18n text, «Перезагрузить раздел», request id | B |
| `store/ui.tsx` | Becomes a shim over `store/session.tsx` (role union widened, theme default light) | B |
| `api/client.ts` | Actor headers, `X-Request-ID`, error envelope with `request_id` and `errors[]` | B |
| `api/types.ts`, `api/endpoints.ts`, `api/queries.ts` | Frozen (legacy). New types and hooks go in domain modules `api/<domain>.ts` (§7.2). INT-1 folds what is left. | — / INT |
| `lib/format.ts`, `lib/utils.ts`, `lib/useThemeColors.ts` | Locale formatting, `turnNo`, plural, bands; theme colours from tokens | A |
| `lib/config.ts` | Parses `role`, `as`, `lang`, `present`, D365 `data` and `userlcid` | B |
| `lib/toast.ts` | Error toasts with code and request id | B |
| `features/card/*` | Rebuilt to the mockup (§5.2). `EvaluationPanel.tsx` → `QualityCard.tsx`; `AudioPlayer.tsx` → `components/media/WaveformPlayer.tsx`; `AppealDialog.tsx` removed once WP-F's `features/appeals/AppealDialog.tsx` lands | C |
| `features/conversations/*` | List with URL state, today tiles, intake previews, `EvaluateDialog` contract (§7.2) | D |
| `features/review/*` | Queue and decision workspace (§5.5) | G |
| `features/checklists/*` | Overview and editor (§5.7) | H |
| `features/analytics/*` | Tabs, ScopeBar, charts (§5.8) | I |
| `features/settings/*` | Sections incl. Оформление and «Фирменный персонаж» (§5.9) | K |
| `features/jobs/*` | Global indicator, `interrupted` + «Возобновить» | B |
| `i18n/ru.ts`, `en.ts`, `kk.ts` | Frozen as the legacy `translation` namespace (only WP-A may add common keys; nobody deletes keys before INT-1). New text goes into `i18n/<lang>/<namespace>.ts`. | A |
| `mock/server.ts`, `mock/fixtures/*` | Glob-registered handlers per domain; synthetic fixtures only (EXP-08) | L |

### 4.9 Accessibility, i18n and content rules

- **Contrast:** WCAG AA for all text (§4.2) and ≥ 3:1 for meaningful graphics.
- **Never colour alone:** Да/Нет/N/A, statuses, bands and heatmap cells always carry an icon and text (SHELL-09).
- **Keyboard on the card** (SHELL-09): J / K move between criteria; Enter jumps to the first evidence and focuses the turn; A opens «Оспорить оценку» where allowed; Space plays and pauses; `]` steps to the next fragment; Alt+← / Alt+→ go to the previous or next dialog (CARD-17); `?` lists the shortcuts. Shortcuts are off inside fields, except Ctrl+Enter to save a decision.
- **Screen readers:** the ring, chips and slider carry labels (§4.7); job progress and recalculated totals use `aria-live="polite"`; dialogs trap focus and return it.
- **Zoom:** 200 % zoom loses nothing. The minimum text size is 12 px (13 px on mobile), and pinch zoom is never disabled.
- **Language:** RU is the source; EN and KK cover every key, and CI fails on a missing key (WP-L). Quotes stay verbatim in the dialog's language. Criterion titles use `title_i18n` when the backend provides it, otherwise the RU title with a small «RU» marker.
- **Copy style:** sentence case; no exclamation marks; RU number formats; verbs on buttons («Оспорить оценку», «Сохранить решение»); never «Ошибка» without a next step.
- **Attribution** is always written as «Роль · Имя · дата время».

### 4.10 Mascot gate (SHELL-02)

- `mascot_enabled` comes from `GET /api/settings`; it is `false` by default and on the hosted instance.
- It is an organisation setting: only an Administrator changes it, in Settings → Оформление → «Фирменный персонаж». Everyone else sees it read-only with «Изменяет администратор».
- When on, poses appear only in first-run empty states. They never appear in the gross-violation strip, score areas, tables, dialogs, errors, the landing, print or the embed.
- If settings fail to load, the mascot stays off.
- The favicon is the neutral monogram. The assets stay in `web/public/mascot/` and outside the main bundle.
---

## 5. Screen-by-screen changes

Every screen uses the shell (§2.3), a `PageHeader` (title plus meta line, as in the mockup) and white cards on the `#F5F7F5` canvas. Each screen lists its story IDs; **bold** IDs are must-demo. Numbers in the wireframes are the hosted DEV values where they exist; appeal counts assume the demo seed (§6.3 F1) plus Нина's appeal, and «N» marks counts that depend on runtime state.

### 5.0 Shell and global states

- **Sidebar, role selector, identity picker, persistence, headers:** §1.3–§1.4, §2.3 (**SHELL-01**, **SHELL-04**, **SHELL-05**, **OPR-01**, **OPR-02**).
- **Route guard.** A route the role may not use opens read-only with a banner, for example «Раздел доступен роли «Методолог». Вы в роли «Директор» — изменения недоступны.», plus «Переключиться на роль Методолог». For forbidden pages it shows «Раздел доступен …» with «К моей главной» (SHELL-06).
- **Global states:**
  - Offline: banner «Нет связи с сервером. Повторяем попытку…» with «Повторить», a red status dot, loaded data marked «может быть устаревшим».
  - Cold start: after 3 s, «Сервер просыпается, это может занять до 30 с», with up to three retries.
  - Not found: a dedicated page.
  - Section crash: «Этот раздел не загрузился» with «Перезагрузить раздел». (**SHELL-07**)
- **Responsive.** At ≥ 1280 the sidebar is expanded. From 1024 to 1279 it auto-collapses. Below 1024 the card stacks its columns. Below 768 the sidebar becomes a top bar and dialogs become full-height sheets (**SHELL-10**, SHELL-11, SHELL-16).
- **Presentation mode** (**SHELL-10**). Job indicator with live progress, findings in the completion toast and «Возобновить» for interrupted jobs (**SYS-03**). «Сценарии для жюри» sheet (**DEMO-01**). «Справка» with a glossary (SHELL-14, could).
- **Nav badges** come from cheap GETs, polled every 30 s and backing off to 2 min when offline: open appeals, register total, unread decisions for the operator (SHELL-12).

### 5.1 Диалоги (`#/conversations`)

Stories: **CONV-01**, **CONV-02**, **CONV-03**, **CONV-04**, CONV-05, **CONV-06**, **CONV-07**, **CONV-08**, CONV-09, **CONV-10**, CONV-11, CONV-13, **EXP-01** entry, **ADM-03** budget line, **GV-03**.

```
Диалоги                                    [Импорт ▾] [Экспорт ▾] [Оценить выбранные (3)]
65 диалогов · 65 оценено · набор DEV · чек-лист v1
┌Не оценены 0┐┌Ошибки оценки 0┐┌Грубые нарушения 8┐┌Требуют проверки N┐┌Решения по моим апелляциям 0┐
[🔍 Поиск по ID, оператору или тексту]  [Набор ▾] [Язык ▾] [Канал ▾] [Итог ▾] [Статус ▾] [Оператор ▾] [Критерий ▾]  ☐ Только с грубыми   Колонки ▾  ⧉
☐ ID          Канал · язык   Оператор   Итог            Грубое нарушение          Апелляция          Оценено
☐ p1-kz-015   Чат · KZ       Мұрат      ⛨ 24,71         Раскрытие конф. инф.      —                  07.11 16:22
☐ p1-ru-029   Чат · RU       Алексей    ⛨ 51,25         Оскорбление клиента       На рассмотрении    07.11 16:20
☐ p4-ru-002   Звонок · RU    —          80,50           —                         —                  07.11 16:31
```

- **Header actions**, gated by role:
  - Primary «Оценить выбранные (n)» (`evaluations.run`).
  - «Импорт ▾»: «По group_id…», «Файл JSON или TXT…», «Вставить текст…» (`conversations.import`).
  - «Экспорт ▾»: «CSV текущего вида», «CSV выбранных», «results.json…» (§5.10).
  - Roles without the permission do not see the buttons. The empty state says who can («Оценку запускает QA-аналитик»).
- **«Сегодня» tiles** (CONV-09): counts that open filtered views. All zeros collapse to «Всё обработано».
- **Filters live in the URL** (SHELL-13): search (id, operator, transcript text with the matching snippet under the id; Enter on an exact id opens the card), «Набор» (from `GET /api/datasets`, plus «Загруженные вручную»), language, channel, total band, status (including «Ошибка оценки», «Оценка неполная», «Не оценены»), operator, criterion + answer (e.g. «C3 = Нет»), «Только с грубыми».
- **Default columns** fit 1024 px without horizontal scroll: ID, Канал · язык, Оператор, Итог (`ScoreBadge` with a shield when critical; tooltip «исходный: 51,25» after a review), Грубое нарушение (`TypeChip`), Апелляция (`StatusChip`), Оценено. Behind «Колонки»: Реплик, 1-й ответ, Модель, Стоимость, Версия чек-листа. Sortable: Итог, ID, Реплик, 1-й ответ, Оценено (**CONV-10**, **SHELL-10**).
- **Row states:** «Оценивается» (spinner), «Ошибка оценки · причина» with «Повторить», «Оценка неполная: C4» (no total shown), «Тестовый» chip for pasted dialogs (CONV-13, later). A row click opens the card with router state `from = list`.
- **Matrix view** (CONV-11, should): one row per dialog, columns `contact … critical` with Да/Нет/N/A or the raw step. A cell opens the card at that criterion.
- **Import by group_id** (**CONV-01**): a two-step dialog.
  1. «Проверить» calls the dry-run preview (BE-A) and shows 65 records, RU 35 · KZ 15 · EN 15, chat 60 · call 5, new / already present / changed, and per-record warnings.
  2. «Импортировать» uses the preview token.
  - On success: «Импортировано 65 диалогов»; the list switches to «Набор: DEV» with every row selected and «Оценить эти 65».
  - The control import warns when a call arrives or the count is not 10.
  - Errors are readable («API организаторов вернул HTTP 500», «Таймаут 60 с», signed link expired) with «Повторить». After two failures it offers «Загрузить JSON-файл» and «Загрузить по URL» (**CONV-03**).
- **Paste and upload** (**CONV-04**, CONV-05):
  - A preview table of parsed turns (№, speaker, time, text). Unrecognised labels are highlighted «Говорящий не распознан» with a mapping select. The language is pre-set from the text (KZ letters → KZ). JSON pasted in the organizer format is accepted.
  - «Создать и оценить» starts a single-dialog job and opens the card with live criterion progress.
  - An existing id raises a conflict prompt «Диалог p1-ru-029 уже есть» with «Создать как p1-ru-029-2» as the default. Replacing is offered only for `manual-*` dialogs without evaluations (**CONV-06**).
- **EvaluateDialog** (**CONV-07**):
  - Fields: scope (the selection, a set, a list of IDs), checklist version («v1 · Freedom Broker · активная», with a warning on inactive versions), provider (Claude / Локальная / Ансамбль, each with a disabled reason when unavailable), mode (Полная / Частичная with a base version), «Только критерии», dry-run estimate, and a budget line «Бюджет: потрачено $16,22 из $75 · осталось $58,78» (**ADM-03**).
  - Runs over 20 dialogs ask for confirmation. 429 guard codes show spent, limit and estimate with «Уменьшить выборку», «Sonnet», «Локальная модель».
  - `conversation_ids: []` is never sent (**SYS-06**).
- **Failed and partial evaluations** stay visible, filterable and retryable. «Повторить для всех (N)» can use another provider (**CONV-08**).

### 5.2 Анализ разговора (`#/conversations/:id`), laid out like the mockup

Stories: **CARD-01**, **CARD-02**, **CARD-03**, **CARD-04**, **CARD-05**, **CARD-06**, **CARD-07**, **CARD-08**, CARD-09, CARD-10, CARD-11, CARD-12, CARD-15, **GV-03**, **APL-09**, **APL-10** (UI), **CONV-08**, **SHELL-10**.

Chat layout at 1280×720 (content area ≈ 1000 px, compact density applies when the viewport height is ≤ 800):

```
← Диалоги
Анализ разговора                                                  [Переоценить] [Экспорт ▾] ⧉
Чат · p1-ru-029 · Русский · оператор Алексей · 14 реплик
┌ Расшифровка ─────────────── 🔍 ─┐ ┌ Оценка качества ───────────────── Чек-лист v1 ⓘ ┐
│ До оператора                     │ │ ⛨ Грубое нарушение: оскорбление клиента · №10 · 01:23 › │
│ 00:05  Бот                   №1  │ │ ╭────────╮ ✓ Установление контакта        12,5 / 12,5 │
│ Здравствуйте! Я бот…             │ │ │ 51,25  │ ✕ Выявление потребности           0 / 18,75 │
│ ──────────────────────────────── │ │ │ / 100 ⛨│ — Идентификация                         N/A │
│ 01:10  ● Клиент              №9  │ │ ╰────────╯ ✓ Корректная и полная консультация  25 / 25 │
│ Не могу найти вход…              │ │ Как посчитан ✕ Эмпатия                       0 / 18,75 │
│╭───────────────────────────────╮│ │ итог →      ✕ Управление диалогом          7,5 / 18,75 │
││01:23  ● Оператор          №10 ││ │             ✓ Завершение                  6,25 / 6,25 │
││Вы вообще читать умеете? Бот…  ││ │             ⛨ Грубые ошибки           Грубое нарушение │
│╰───────────────────────────────╯│ │ ───────────────────────────────────────────────────── │
│  (peach = evidence of current)   │ │ Почему снижена оценка                                  │
│ …                                │ │ Выявление потребности −18,75 · <first sentence>        │
│                                  │ │ Эмпатия −18,75 · <first sentence>                       │
│                                  │ │ Управление диалогом −11,25 · <first sentence>   ещё ▾  │
│                                  │ │ Не применимо: Идентификация — вес перераспределён      │
│                                  │ │ [ Перейти к фрагменту ]   [ Оспорить оценку ]          │
└──────────────────────────────────┘ └────────────────────────────────────────────────────────┘
┌ Детали оценки        История · Трассировка · Технические детали ─────────────────────────────┐
```

**Header** (`PageHeader`): title «Анализ разговора». Meta line «Чат · p1-ru-029 · Русский · оператор Алексей · 14 реплик» (calls: «Звонок · p4-ru-002 · Русский · 10:20 · 03.11.2026»). A back link that depends on where the card was opened from. Actions: «Переоценить» (`evaluations.run`; warns when an appeal is open), «Экспорт ▾» (PDF, «Копировать ссылку», «Копировать JSON» under Технические детали), and later «Предыдущий / Следующий» (CARD-17).

**Grid.** `minmax(0,1fr) minmax(0,1.1fr)`, 24 px gap, both cards the same height (viewport minus header), each scrolling inside. Below 1024 px: one column with tabs «Оценка | Расшифровка». Tapping an evidence chip switches to the transcript and shows «← К оценке» (SHELL-11, SHELL-16).

**«Оценка качества» card.**
- Header: «Оценка качества», `VersionChip` «Чек-лист v1 · Freedom Broker» (the version number, never the database id), ⓘ (scoring rules in words).
- **Gross strip** (GV-03), 40 px, `critical-bg`:
  - Collapsed: «Грубое нарушение: <тип> · №10 · 01:23».
  - Expanded: the quote, «Итог не обнуляется: по чек-листу грубая ошибка отмечается отдельно», and for the operator role «Вы можете оспорить отметку» (C8 preselected).
  - Overturned: «Отметка о грубом нарушении снята решением проверяющего» with the reason.
  - When C8 errored: «C8 не оценён».
- **Ring column**: `ScoreRing xl`; «Как посчитан итог →» opens `TotalBreakdown` (sheet, §below).
- **Criteria column**: compact `CriterionRow`s for every criterion (§4.7). The default current criterion is the one with the largest loss. If the URL carries `?criterion=` that one is expanded instead.
- **Sticky footer**:
  - «Почему снижена оценка» (CARD-02): the criteria that lost points, largest loss first, «Название −потеря · first sentence of the stored explanation (after the «Критерий — ответ.» prefix, cut to 120 characters)». Three lines, then «ещё N».
  - N/A line: «Не применимо: Идентификация — вес перераспределён».
  - 100,00 without a gross error: «Оценка не снижалась». Failed or partial: «Итог не рассчитан: C4 не оценён» plus «Повторить C4».
  - Buttons: **«Перейти к фрагменту»** (primary) scrolls the transcript to the current criterion's first contradicting evidence (else the first evidence), highlights it in peach, focuses it, and on calls plays it. **«Оспорить оценку»** (secondary) opens `AppealDialog` with the current criterion, or is hidden or disabled with a reason per role (§1.5).
- **After a decision** (**APL-09**): the row shows «Да → Нет» and «6,25 → 0» with «Изменено проверяющим · Ревьюер · Айгерим · дата» and the reason. The ring shows 45,00 with «было 51,25» and «после апелляции». The «Почему снижена» line reads «было 0 / 5 → стало N/A».
- **Carried decisions** after a partial re-run read «Решение перенесено из оценки #59» (**APL-10**, BE-B).
- **Running evaluation:** rows fill in as `criterion.done` SSE events arrive, with pending icons. No evaluation: an `EmptyState` «Оценки пока нет» with «Оценить» for roles that may run, otherwise «Оценку запускает QA-аналитик». Never zeros.

**`TotalBreakdown`** («Как посчитан итог», **CARD-06**), a 640 px sheet:
- A table with criterion, base weight, applicability, answer, share earned, applied weight and points.
- Footer «Применимый вес 80 из 100 → коэффициент 1,25 · сумма 51,25 · округление до 2 знаков, половина вверх».
- C8 is labelled «Без баллов — отмечается отдельно». If nothing applies: «Итог не рассчитан — нет применимых критериев».
- With a decision: «Исходно» and «После решения» columns.
- «Пересчитать» calls `POST /api/scoring/preview` (BE-D) and must return exactly the shown total.

**«Расшифровка» card:** `TranscriptRow` list (§4.7). The search field expands from the header icon and keeps transcript hits. The checked span is outlined. Duplicates expand before a jump lands on them. The 1-based № is the same in chips, explanations, PDF and results.json (**CARD-07**).

**Deep-link behaviour** (**CARD-04**): `?criterion=C8&turn=10` expands C8 and highlights №10 on a cold load. An unknown criterion or turn gives a quiet toast «Фрагмент не найден». Selecting a criterion and jumping update the URL with `replaceState`.

**«Детали оценки» card** (below the grid):
- Tabs: История (timeline AI evaluation → appeal → decision → final, local time with the zone), Трассировка (calls with model, prompt version, tokens, cost, latency, timestamps, and «Скопировано из оценки #59, без вызова модели» for copied criteria; **CARD-08**), Аудио-показатели (calls), Технические детали (provider, model, prompt hash, cost, tokens, latency, flags).
- For Оператор and Директор the card is collapsed («Показать технические детали»).
- Header chips for later stories: «Отклонения от базы знаний: 1» (CARD-10) and «Есть исправления ИИ» (CARD-15).

### 5.3 Calls (same route; the mockup's top card)

Stories: **CALL-01**, **CALL-02**, **CALL-03**, **CALL-09**, **CALL-12**, CALL-04, CALL-05, CALL-06, CALL-11, CALL-13, CALL-14.

```
Анализ разговора
Звонок · p4-ru-002 · Русский · 10:20 · 03.11.2026                  Синхронизация: по началу записи
┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│ Клиент    ▁▃▅▂▁▃▇▅▂▁▁▂▃▅▃▂▁░░░░░░░[▃▆▅▃▂]▁▂▃▅▂▁▂▃▁▁▂▅▇▅▃▂▁▂▃▁                               │
│ Оператор  ▂▃▂▁▁▂▃▅▃▂▁▂▃░░░░░░░░░░[▅▇▆▃▂]▃▂▁▂▃▅▃▂▁▂▃▅▃▂▁                                      │
│           ▲ evidence ticks          peach fragment     ▒▒▒ Удержание 7:06                       │
│ (▶)  02:00 / 10:20   ━━━━━━━━━●──────────────────────────────  1× ▾   Следующий фрагмент ]  ⟲ │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
┌ Расшифровка ┐ ┌ Оценка качества ┐   (as §5.2)
```

- **Waveform card** (§4.7 `WaveformPlayer`) above the grid, full width, collapsible to the control row. It streams `/api/audio/{id}` (Range, never the organizer URL) and supports 0,75–2× (**CALL-01**).
- **Evidence → audio.** «Перейти к фрагменту» and chips seek 2 s before the turn and stop at the next turn. «Следующий фрагмент» steps through the criterion's evidence with «1 из 6». Follow mode keeps the current turn centred; scrolling by hand pauses it and shows «К текущей реплике» (**CALL-01**). Evidence outside the recording reads «Фрагмент не попал в аудиозапись» and does not seek.
- **Two-track lanes** from `/peaks`; peach fragment; first-use legend «Как читать дорожки» (**CALL-02**).
- **Sync** (**CALL-03**, BE-E): a header indicator «Синхронизация: по началу записи (−30 с)», or an amber «Рассинхрон» when the transcript runs more than 3 s past the end of the audio. «Подстроить ±0,5 с / ±5 с» and «Сбросить к авто» are for Ревьюер, Руководитель and Админ.
- **C1 banner** (**CALL-09**) when the operator segment starts before the recording-start event: «Запись началась после начала разговора оператора: приветствие могло не попасть в запись. Правило организаторов для звонков: C1 = N/A.» «Оспорить оценку» on C1 is prefilled with that reason and the range.
- **Recording unavailable** (**CALL-12**): a transcript-timing timeline labelled «по расшифровке», the message above with «Повторить», and evidence links that still scroll.
- **Transcript structure:** IVR summary row, consultation group, «до начала записи», «после завершения» (CALL-04); hold bands and etiquette markers (CALL-05); mask chips and readable flags (CALL-06); the C6 metrics block with method and reliability (CALL-11, should).

### 5.4 Грубые нарушения (`#/violations`)

Stories: **GV-01**, **GV-02**, **GV-03**, GV-04, GV-05 (decision side in §5.5), GV-08 (tab, should).

```
Грубые нарушения                                               ⧉ Скопировать ссылку  [CSV]
8 диалогов · 8 нарушений · проверено ИИ: 65 диалогов · подтверждено проверяющим: 0 из 8
┌ Оскорбление клиента  1 ┐┌ Платная услуга в обход компании 4 ┐┌ Раскрытие конфиденциальной информации 3 ┐┌ Иное 0 ┐
[Язык ▾] [Канал ▾] [Оператор ▾] [Статус проверки ▾]   ☐ Показать снятые проверяющим      Сортировка: итог ↑
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│ (24,71)   p1-kz-015 · Чат · KZ · оператор Мұрат      [⛨ Раскрытие конфиденциальной информации] │
│  / 100    «<quote in Kazakh>» — Оператор · №10 · 01:21                    ИИ · не подтверждено │
│ итог не   Грубые ошибки — Да. <first two lines of the C8 explanation>…                       │
│ обнуляется Связанный критерий: Идентификация — Нет                                            │
│           [Открыть в диалоге]   Почему так классифицировано ▾     [Подтвердить] [Снять]       │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Data:** `GET /api/violations` (deployed). Rows are cards rather than table rows because each needs a quote and an explanation.
- **Sort:** final total ascending (p1-kz-015 24,71 first, p1-ru-023 100,00 last).
- **Type tiles:** counts and filters together, using type labels from the API in the UI language.
- **Each card:** a small ring with «итог не обнуляется»; dialog id, channel, language and operator; type chips (a dialog with two types appears once with both); the quote with speaker, 1-based № and time, in a quote block with a peach left rule; the first two lines of the C8 explanation; the review status (`StatusChip`: «ИИ · не подтверждено», «На апелляции → #42», «Подтверждено · Айгерим · дата», «Снято»).
- **«Почему так классифицировано»** (expander; a sheet on narrow screens; **GV-02**): the checklist rule for the three types with the matched one highlighted; the full justification; quotes; confidence in words and number («высокая · 0,95»); the related-criterion block for disclosure (C3 = Нет with its explanation and the team rule «C3 = Нет и C8 = Да при раскрытии данных счёта до идентификации»); the client's refusal as context for p1-kz-013 («отказ клиента флаг не снимает»); and «Как определён тип» (judge or keyword fallback, `classified_by`, BE-C).
- **Actions:**
  - «Открыть в диалоге» goes to `#/conversations/<id>?criterion=C8&turn=<n>`. «← К нарушениям» returns with filters and scroll kept.
  - Ревьюер and Руководитель get «Подтвердить» and «Снять», which run the spot-check flow (appeal by the reviewer plus decision; GV-04, APL-15). For other roles these are hidden.
  - The register stays one click from the card and the overview risk tile.
- **Empty states:**
  - «Грубых нарушений не найдено» with the scope and «Проверено: оскорбление клиента, платная услуга в обход компании, раскрытие конфиденциальной информации».
  - Calls filter: «В звонках грубых нарушений не найдено · проверено 5 звонков».
  - Type `other`: «Иное грубое нарушение — требует ручной классификации».
- **«Проверено ИИ: не нарушение»** tab (GV-08, should; BE-C candidates): C3/C8 contradictions (p1-ru-018, p4-ru-005), low-confidence C8, signal turns.

### 5.5 Апелляции: review queue and decisions (`#/review`, `#/review/:id`)

Stories: **APL-05**, **APL-06**, **APL-07**, **APL-08**, **APL-09**, **APL-10**, APL-11, APL-12, APL-13, GV-05, CAL-12, DEMO-14.

```
Апелляции                                                      Открытые 3 · Решённые 1 · Все
[Критерий ▾] [Язык ▾] [Канал ▾] [Автор ▾]  Сортировка: сначала грубые ▾
┌ queue (360) ─────────────────┐ ┌ decision workspace ──────────────────────────────────────────┐
│ ● p1-ru-005 · C6             │ │ p1-ru-005 · Чат · RU · оператор Нина · итог 78,46   Открыть карточку ↗ │
│   Оператор · Нина · 12 мин   │ │ C6 Управление диалогом, скорость и грамотность                │
│   ИИ: 4 из 10 → просит 7     │ │ Ответ ИИ: 4 из 10 · 9,23 / 23,08 · уверенность 0,71            │
│ ○ p4-ru-002 · C1             │ │ «Комментарий оператора…» — Оператор · Нина · 07.11 18:40      │
│   QA-аналитик · 1 ч          │ │ ┌ Фрагмент диалога (№11–№14, evidence in peach) ┐             │
│                              │ │ Правило критерия ▾   Почему ИИ так решил ▾   Похожие решения ▾ │
│                              │ │ ( Подтвердить | Изменить )                                     │
│                              │ │ ◉ 10 — ясные ответы…  ◉ 7 — один заметный сбой…  ○ 4 (ответ ИИ) ○ 0 │
│                              │ │ Итог: 78,46 → 85,38   раздел S3: +6,92                         │
│                              │ │ Причина решения [ Шаблон ]                                     │
│                              │ │ [ Управление диалогом — 7. …                              ]   │
│                              │ │ ☐ Использовать как пример для ИИ                [Сохранить решение] │
└──────────────────────────────┘ └──────────────────────────────────────────────────────────────┘
```

- **Queue** (`GET /api/appeals`): open first (gross-violation appeals pinned, then the oldest), decided in a collapsible «Решённые» block. Each item shows dialog · criterion, the author chip «Оператор · Нина» (legacy: «Автор не указан»), age, and AI answer → requested answer. On a phone the list and the workspace become two screens (**APL-05**).
- **Workspace** (**APL-06**):
  - Header: dialog, language, channel, operator, current total, «Открыть карточку» (deep link with `criterion` and `turn`; «← К апелляции 42» keeps the draft).
  - Disputed answer with points and applied weight, confidence, the explanation, and the author's comment with role, name and date.
  - **Фрагмент диалога**: `TranscriptFragment` from WP-C with every evidence turn in peach, one context turn on each side, the checked span, and «Показать весь диалог». For calls, `MiniPlayer` with the hold shaded. If audio fails: «Запись недоступна», and the decision can still be saved.
- **Context** (APL-11, should): «Правило критерия» (anchors, N/A condition, call override, from the version the evaluation used, plus «в активной версии v2 критерий изменён»); «Почему ИИ так решил» (confidence, readable flags, votes, claims; «Вердикт перенесён из предыдущей оценки без вызова модели»); «Похожие решения» (decided appeals on the same criterion).
- **Decision panel** (**APL-07**, **APL-08**):
  - `SegmentedControl` «Подтвердить | Изменить». Under «Изменить» the options are radio cards with points and anchors. The AI's own answer is disabled with «Совпадает с ответом ИИ — выберите «Подтвердить»».
  - N/A is offered only when `na_allowed_for_channel` is true (C1 on calls), and the UI sends the string "N/A" (the current UI sends `null`, which the API rejects with 400).
  - The live preview from `POST /api/appeals/preview` appears within 1 s: «Итог: 51,25 → 45,00», section deltas, and for N/A a weights table (C1 18,18 → 25,00 …). For C8: «Грубая ошибка: Да → Нет · итог без изменений 100,00 (C8 не даёт баллов)».
  - The C8 variant adds a type picker of the three checklist types (GV-05).
  - Reason: required, 15 characters in the UI (the API rejects a blank reason, §6). «Шаблон» prefills «Управление диалогом — 7. … Основание — реплика №12 «…»». Clicking a turn in the fragment inserts «реплика №14 «…»» (APL-12).
  - «Использовать как пример для ИИ» is **off by default** (CAL-12; DEMO-07 freeze).
  - **Four-eyes:** when the reviewer's name equals the author's, a warning appears and saving needs a second confirmation (SHELL-05).
  - Toast «Решение сохранено. Итог: 85,38». Focus moves to the next appeal.
  - A conflict shows «Апелляцию уже решил(а) …» and keeps the draft in «Ваш черновик» (APL-12).
- **Roles without `appeals.decide`** see the queue read-only, with «Решение принимает ревьюер» and «Переключиться на роль Ревьюер». The tab «Мои» lists appeals the current identity filed.
- **Stale appeals** (**APL-10**): «Оценка устарела: диалог переоценён (#70)» with «Решить по оценке #28» and «Закрыть: вопрос снят переоценкой» (BE-B). A replaced criterion shows «Критерий заменён в версии v2».
- **Control window banner** (DEMO-14): «Идёт контрольный прогон: не принимайте решения по контрольным диалогам до отправки results/prod/results.json».

### 5.6 Operator portal (`#/my/evaluations`, `#/my/appeals`) and filing an appeal

Stories: **OPR-01**, **OPR-02**, OPR-03, **OPR-04**, **OPR-05**, **APL-01**, APL-02, APL-03, APL-04, **DEMO-02**.

```
Мои оценки                                                            Оператор · Нина  Сменить
1 оценка · чек-лист v1
┌ Оценок 1 ┐┌ Средний итог 78,46 · команда 79,88 ┐┌ Грубых нарушений 0 ┐┌ Апелляций на рассмотрении 0 ┐
Где я теряю баллы:  Управление диалогом −13,85 · Корректная и полная консультация −7,69     (мало данных: n = 1)
┌ Как оцениваются ваши диалоги ───────────────────────────────────────────── Понятно ┐ (first visit)
ID          Канал · язык   Дата оценки   Итог     Грубое   Апелляция     Чек-лист
p1-ru-005   Чат · RU       07.11 16:12   78,46    —        —             v1
```

- **«Мои оценки»** (**OPR-04**): `GET /api/conversations?operator=<name>`.
  - Summary from `GET /api/operators/{name}` when deployed; otherwise computed from the list.
  - The team average is anonymous. Below 3 evaluations: «Мало данных для выводов».
  - Chats show the evaluation date with «В данных чата нет даты контакта»; calls show the contact date.
  - A row opens the card with `from = my`.
- **Orientation** (OPR-03): a dismissible panel per identity, no mascot. «Как оцениваются ваши диалоги» lists 8 criteria, «ИИ ставит оценку и цитирует реплики», «итог из 100, N/A исключаются с перераспределением веса», «грубые нарушения отмечаются отдельно и не обнуляют итог», with «Открыть чек-лист» and «Как подать апелляцию».
- **Card for an operator** (**OPR-02**): own dialog: «Оспорить оценку» enabled. Another operator's dialog: read-only with «Диалог другого оператора». Technical details collapsed.
- **`AppealDialog`** (owned by WP-F, opened from the card, the violations row and «Почему снижена оценка»; **APL-01**, APL-02, APL-03):
  - Title «Апелляция по критерию C6 · Управление диалогом, скорость и грамотность».
  - The current answer «4 из 10», points «9,23 / 23,08», the explanation, evidence chips.
  - «Какой ответ вы считаете верным» (optional; options with anchors; N/A only where the channel allows) with the effect preview «Если апелляцию удовлетворят: 78,46 → 85,38», and «До решения итог не меняется».
  - «Добавить реплики»: clicking turns in the transcript adds chips. On calls, a fragment selector preset to the evidence.
  - Reason category (неверно понят контекст · цитата вырвана из контекста · ошибка распознавания речи (calls only) · критерий неприменим · ответ соответствует базе знаний · другое).
  - «Ваш комментарий» (10–2 000 characters, with a counter). The label is role-aware; for an operator it is never «Комментарий аналитика».
  - Primary «Отправить на апелляцию». Toast «Апелляция отправлена ревьюеру». The row shows «На рассмотрении» without a reload.
  - Drafts per criterion are kept in local storage. A 409 shows «По этому критерию уже есть открытая апелляция (автор: …, 14:32)». A 403 shows «Оператор может оспаривать только оценки своих диалогов».
- **«Мои апелляции»** (**OPR-05**): tabs «На рассмотрении / Решённые / Все». Rows: dialog · criterion · filed at · status · outcome, e.g. «Изменено: 4 → 7 · итог 78,46 → 85,38» with the reason one click away, «Оценка подтверждена · повторная апелляция возможна с новым аргументом», or «Отозвана».
  - Unread decisions are marked per identity in local storage and counted on the nav badge.
  - «Отозвать» and «Изменить комментарий» are available on open appeals (APL-04, BE-B).
  - Empty: «Вы ещё не подавали апелляций» with a link to «Мои оценки».

### 5.7 Чек-листы (`#/checklists`) and the checklist editor (`#/checklists/:id/versions/:vid`)

Stories: **CHK-01**, **CHK-02**, **CHK-03**, **CHK-04**, **CHK-05**, **CHK-06**, **CHK-10**, **CHK-12**, **CHK-13**, **CHK-15**, **CHK-16**, **CHK-21**, CHK-07, CHK-08, CHK-09, CHK-11, CHK-14, CHK-17, CHK-18, CHK-19, CHK-20, **DEMO-05**, **DEMO-12**, **SYS-06**.

**Overview** (`#/checklists`, readable by every role; **CHK-01**):

```
Чек-листы                                                         [Новый чек-лист ▾] (writers)
┌ Freedom Broker · Чек-лист качества чатов ─────────── v1 · Активна · Базовая версия организаторов ┐
│ 8 критериев · 4 раздела · Σ весов 100 · оценено диалогов: 65 →            [Изменить] [Экспорт ▾] │
│ S1 Начало и понимание запроса                                                                  │
│   C1 Установление контакта        Да/Нет        вес 10 (10 %)  N/A недоступен · Звонок: N/A, если │
│                                                                приветствие не попало в запись   │
│   C2 Выявление потребности        Да/Нет        вес 15 (15 %)  N/A допустим: данных хватало…    │
│ S2 Безопасность и решение … S3 Ведение и завершение диалога … S4 Грубые нарушения                │
│   C8 Грубые ошибки               флаг · без баллов · отмечается отдельно                          │
│ Правила подсчёта: N/A исключается, вес перераспределяется до 100 · округление 2 знака, половина   │
│ вверх · нет применимых — итог не считается · грубая ошибка не обнуляет итог · первый ответ 3 мин  │
│ (информативно) · теги и вложения не оцениваются                                                    │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
Версии: v2 · Черновик · «C7 вес 10» · Методолог · Айгерим · 07.11 21:40 · из v1 · оценено 1 …
```

- Each criterion row shows its type, its weight and effective share, N/A with the condition, the call override badge, and the export id (`contact … critical`) in the tooltip (**CHK-21**).
- The version table shows status, note, author, time, base, «оценено N» (a link to the filtered list) and open appeals (CHK-14).
- «Активировать» asks to confirm: «Новые оценки будут использовать v1. Оценки на v2 не изменятся.»
- Operator and Director see the same content read-only.

**Editor** (writers: Методолог, Руководитель, Админ):

```
← Чек-листы
Чек-лист качества чатов · v2  [Черновик]                [Сравнить] [Проверить на диалоге] [Сохранить версию…]
из v1 · есть несохранённые изменения
┌ Критерии ─────────────────────────────────────────────────┐ ┌ Веса ─────────────── Σ 105 ⚠ ┐
│ S3 Ведение и завершение диалога                            │ │ Веса относительные: итог всё   │
│ ⋮⋮ C5 Эмпатия            Да/Нет   [ 15 ]  14,29 %  N/A —  ⋯│ │ равно нормируется к 100        │
│ ⋮⋮ C6 Управление диалогом 10/7/4/0 [ 15 ]  14,29 %  N/A — ⋯│ │ C1 ▇▇▇ 9,52 %  … C7 ▇▇▇ 9,52 % │
│ ⋮⋮ C7 Завершение  Изменено Да/Нет   [ 10 ]  9,52 %  N/A ✓ ⋯│ │ [Нормализовать до 100]  ↶      │
│   + Добавить критерий в S3                                  │ ├ Правила подсчёта ─────────────┤
│ S4 Грубые нарушения                                         │ │ (plain language, editable)    │
│ ⋮⋮ C8 Грубые ошибки  флаг · без баллов  [вес недоступен]   ⋯│ ├ Совместимость с results.json ┤
│ [+ Добавить критерий]                                       │ │ ✓ Совместима с JSON 1.0        │
└────────────────────────────────────────────────────────────┘ ├ Изменения ────────────────────┤
                                                              │ C7: вес 5 → 10                 │
                                                              └───────────────────────────────┘
```

- **Read-only v1** (**CHK-15**): inputs are disabled and the primary action is «Создать новую версию на основе v1», which opens a draft. «Перегенерировать» (judge spec) is disabled on v1 and on versions with evaluations (409 `locked`, BE-D).
- **Add a criterion** (**CHK-02**): «+ Добавить критерий» (in the header and at the end of each section) inserts an expanded, focused card with the **next free code** (C9, even after a deletion), the section, Да/Нет, weight 10, N/A off. Empty title or instruction block saving with field messages.
- **Edit** (**CHK-03**): inline title, instruction, answer type, and scale steps with points, anchors and a «баллов в итог» column (C6 at weight 15: 10 → 15,00, 7 → 10,50, 4 → 6,00, 0 → 0,00). A word-level diff against the saved version; the «Изменено» badge disappears when the text is restored; switching from a scale to Да/Нет asks to confirm.
- **Weights** (**CHK-04**): a stepper per row and live shares. Σ ≠ 100 shows in amber with «Веса относительные: итог всё равно нормируется к 100». «Нормализовать до 100» uses the largest remainder (10/14/19/19/14/14/10) and has one-click undo. Weight 0 shows «критерий не влияет на итог». The C8 weight field is disabled with «Грубые ошибки не дают баллов».
- **Replace** (**CHK-05**): row menu «Заменить…» opens a form prefilled with the same position, section and weight and a new code. The old row is greyed «будет удалён в новой версии», and its open appeals are listed. Keeping the old code warns: «код C7 связан с export id closure; новый смысл — новый код».
- **N/A and calls** (**CHK-06**): a «Чат | Звонок» toggle shows the effective wording and the N/A rule per channel. Editing the call instruction keeps `na_allowed` and `na_condition`; the current UI drops them, which is a bug. An empty condition blocks saving. «Как считается N/A» shows a worked example → 85,38.
- **Validation** (**CHK-10**): client checks plus the API's 422 `errors[{field, message}]` placed next to fields. Clicking an error scrolls to the field. A valid draft shows «Проверка пройдена · 2 изменения».
- **Save** (**CHK-12**): a dialog with the change list («C7: вес 5 → 10; C1: формулировка»), a required note (at least 5 characters) and «Сделать активной сейчас» (off by default). Buttons «Сохранить черновик» and «Сохранить и активировать». A newer version created meanwhile shows «С момента открытия создана v3…» with «Открыть сравнение» or «Сохранить как v4». A visible «Компиляция спецификаций…» state.
- **Compare** (**CHK-13**): a sheet with old → new values per field, added and removed criteria, «нужна оценка моделью» vs «только пересчёт баллов», and a cost footer from a dry run. Identical versions: «Различий нет».
- **Apply** (**CHK-16**, **DEMO-12**, **SYS-06**): «Применить к диалогам…» opens the run dialog (WP-D contract) preset to:
  - mode «Частичная (только изменённые критерии)» with the base version;
  - scope «Все (75) · Набор DEV (65) · Контрольная группа (10) · Выбранные · Список ID»;
  - a dry run «10 диалогов · переоценка C9 · 10 вызовов · ≈ $0,30 · ≈ 1 мин», or «Переоценка моделью не нужна · 0 вызовов · $0,00» for weight-only changes; dialogs without a base evaluation are listed as «будут оценены полностью».
  - Progress, then a summary table of old → new totals with links (`GET /api/jobs/{id}/summary`, BE-D).
- **Later stories:** test a draft on dialogs (CHK-17, `test-criterion` exists in the working tree), impact preview (CHK-18), sections editing (CHK-08), the gross-error criterion's types and mode (CHK-07), and a what-if calculator (CHK-11).

### 5.8 Обзор (executive), Аналитика, Операторы

Stories: EXE-01, EXE-02, EXE-03, EXE-04, EXE-05, EXE-07, EXE-08, ANL-01, ANL-02, ANL-03, ANL-04, ANL-05, ANL-06, ANL-11, ANL-12, CHK-28, **GV-03**.

**Executive overview** (`#/overview`; home of Руководитель and Директор):

```
Обзор качества                                                     [Сводка (PDF)] ⧉
DEV · 65 диалогов · чек-лист v1 · Claude Opus 5.5 · обновлено 20:31 (UTC+5)
[Набор ▾] [Версия ▾] [Язык ▾] [Канал ▾] [Период ▾]  Основа: (После апелляций | Оценка ИИ)
┌ Индекс качества ⓘ ────┐┌ Грубые нарушения ─────┐┌ Покрытие ─────────────┐┌ Стоимость оценки ─────┐
│  ( 82,04 )  65 диалогов││ 8 из 65 · 12,3 %       ││ 65 из 65 оценено       ││ ≈ $0,22 за диалог     │
│   / 100   медиана 95,00 ││ Оскорбление 1          ││ ошибок оценки 0        ││ всего $16,22 · 75     │
│ Норма 43 · Внимание 9 · ││ Платная услуга 4       ││ проверено людьми: 0    ││ ≈ 15 с на диалог      │
│ Критично 13             ││ Раскрытие 3            ││                        ││ Claude Opus 5.5       │
│                         ││ подтверждено: 0 из 8 → ││                        ││                       │
└─────────────────────────┘└────────────────────────┘└────────────────────────┘└───────────────────────┘
┌ Качество по языкам и каналам ──────────────────────┐┌ Где теряем баллы ─────────────────────────────┐
│ RU  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇ 78,56  n = 35                  ││ Эмпатия               −4,14  ▇▇▇▇▇▇▇▇           │
│ KZ  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇ 85,55 n = 15                  ││ Консультация          −3,85  ▇▇▇▇▇▇▇            │
│ EN  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇ 86,62 n = 15                  ││ Управление диалогом   −3,41  ▇▇▇▇▇▇             │
│ Чаты ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇ 82,91 n = 60                  ││ …  Итого потеряно 17,97 ≈ 100 − 82,04          │
│ Звонки ▇▇▇▇▇▇▇▇▇▇▇▇ 71,53 n = 5 · малая выборка     │└────────────────────────────────────────────────┘
└────────────────────────────────────────────────────┘
┌ Операторы, требующие внимания ─────────────────────┐┌ Апелляции ──────┐┌ Идентификация ──┐┌ Первый ответ ──┐
│ ⛨ Мұрат 24,71 · раскрытие · мало данных (n = 1) →  ││ открыто 0 …      ││ 8 из 13 · 62 %   ││ 71,5 с · >3 мин 0 %│
└────────────────────────────────────────────────────┘└─────────────────┘└─────────────────┘└────────────────┘
```

- **Rules:** every number has its definition (ⓘ), n, the scope stamp and a drill-down to the dialogs behind it (ANL-02). Small samples are flagged. Empty values read «—», never 0. The basis toggle switches between final values and AI values. RU and KK use the decimal comma. Directors send only GET requests (EXE-01).
- **Data:** `GET /api/overview` (working tree). If it returns 404 on the deployed API, the page computes from `GET /api/analytics/summary` (count, mean, median, language, channel, cost, first response), `GET /api/violations` (risk tile) and `GET /api/conversations?page_size=500` (bands 85/60, coverage). Tiles that need overview-only data (loss drivers, appeals funnel, identification compliance) show «Доступно после обновления API» (§6.4).
- **Print** (EXE-08): `?print=1` lays out one A4 page with the same numbers (print stylesheet; dark theme prints light).
- **Trust and cost later:** the trust panel (EXE-05) and the monthly projection (EXE-04) are should-stories in WP-I's second half.

**Analytics** (`#/analytics`, tabs; **ANL-01**: one `ScopeBar` shared with the overview, operators and violations):
- «Сводка»: KPI tiles, the distribution chart with band labels, and the criteria × language heatmap (sequential green, n per cell, n < 5 hatched, C8 inverted).
- «Критерии» (ANL-03): pass rate, mean points, N/A rate (> 15 % highlighted «Проверьте условие N/A»), lost points, appeal overturns.
- «Операторы» → `#/operators`.
- «Стоимость и модели» (ANL-12): total spend, mean and p95, by channel and provider, runs table, the model mix next to the results.json model block.
- «Согласие ИИ и людей» (the existing agreement table; a calibration link later).
- «Скорость ответа» (ANL-11): first response against the 3 min threshold with a histogram, «не входит в итог».

**Operators** (`#/operators`, ANL-05; `#/operators/:name`, ANL-06):
- The list is sorted by risk: gross errors first, then low means. «Оператор не определён (10)» is pinned at the bottom and excluded from rankings. Fewer than 3 dialogs reads «мало данных (n = 1)». Search handles Kazakh letters.
- The scorecard shows per-criterion results against the team, «Фокус коучинга» (top three point losses with quotes and «Перейти к фрагменту»), violations, appeals, and «PDF для встречи» (later).

### 5.9 Настройки (`#/settings`)

Stories: SHELL-02, SHELL-05, SHELL-08, ADM-01, ADM-02, **ADM-03**, ADM-04, **EXP-03**, **DEMO-04**, **DEMO-16**.

There is a left sub-navigation inside the page. Personal sections are editable by everyone; system sections are editable by Администратор only and read-only for everyone else, with «Изменяет администратор».

| Section | Content |
|---|---|
| Профиль | Display name (sent as `X-CQA-User`), «Роль по умолчанию», operator identity with «Сменить» |
| **Оформление** | Тема: Светлая (по умолчанию) · Тёмная · Как в системе. Плотность: Обычная · Компактная. «Режим презентации». **«Фирменный персонаж»**: a switch, off by default, organisation-level (admin), described as «Показывать персонажа в пустых состояниях. Для неформальных показов. По умолчанию выключено (рекомендация организаторов).» |
| Язык | UI language RU · KK · EN (personal). «Язык объяснений» (admin; `explanation_language`, with the note that explanations generated in Russian stay Russian) |
| Судья и модели (admin) | Default provider, model, ensemble members; local endpoint with «Проверить подключение» (ADM-02, needs BE); `confidence_threshold` (CARD-09) |
| Приватность (admin) | PII masking with a confirm on turning it off; an amber «PII: выкл.» chip in the sidebar footer when off (ADM-04) |
| Лимиты (admin) | Daily budget, run size, concurrency; spend so far; 80 % banner (**ADM-03**, BE-F) |
| Интеграции (admin) | Organizer API status (host only, never the URL); Dynamics 365 guide, manifest, `CQA_CONFIG` snippet and a framed preview (D365-02) |
| **Модель и стоимость** | Generated statement: models, mode, mean and p90 cost per evaluation by channel with n and dates, the formula and rates, and «Копировать для README» (**EXP-03**, BE-C) |
| О системе | Web commit and build time (injected at build), API version (`GET /api/version`), «Код совпадает с тегом stop-code» or a red mismatch warning (**DEMO-16**) |

### 5.10 results.json export (UI entry point only)

Stories: **EXP-01**; **EXP-02** and **DEMO-06**..**DEMO-11** belong to the team lead.

- **Entry points:** Диалоги → «Экспорт ▾ → results.json…» (Аналитик, Руководитель, Админ); Администрирование → «Контрольный час»; deep link `#/conversations?export=results&dataset=DEV`.
- **`ExportResultsDialog`** (WP-D), built on the team lead's endpoints only:
  - Set: «DEV (65)», «Контрольная группа (10)» or «Список ID»; the expected count is prefilled with 65 or 10.
  - «Проверить» calls `GET /api/export/results/validate?group_id=…&expect=…` and shows «Файл валиден: schema 1.0, 65 записей, ok 65, error 0, грубых ошибок 8», the model block (external_api · claude-opus-5-5) and the team «Я Тебя Могну».
  - Errors appear per row with the dialog link and criterion id. «Переоценить только эти (N)» opens the run dialog preset to v1.
  - «Скачать results.json» calls `GET /api/export/results.json?…&download=true` and reads the `X-Results-*` headers. With errors present the button reads «Скачать с ошибками».
  - It shows the target paths `results/dev/results.json` and `results/prod/results.json` with copy buttons, the note «Построено по v1 (организаторский); активная версия: v2» when it applies, and the reminder «Закоммитьте и запушьте до стоп-кода». It never claims a file was pushed.
  - If the endpoints return 404 (not deployed): «Выгрузка доступна через CLI: `python -m cqa.export.results_json`».

### 5.11 Администрирование and Журнал действий

Stories: **ADM-06**, ADM-05, ADM-07, **DEMO-03**, **DEMO-04**, DEMO-13 (later).

- **`#/admin`, tab «Система и готовность»**: the go/no-go list (DEMO-03) from `GET /api/admin/readiness` (BE-F), else `GET /api/system/health`:
  - API, DB, Claude, organizer API, v1 present with Σ 100;
  - DEV imported 65 and evaluated 65/65; DEV results.json valid; p1-ru-029, p1-ru-022 and p4-ru-002 present with audio cached;
  - a decided appeal exists; a snapshot younger than 2 h; at least $10 of budget left; web and API from the same commit.
  - Each failing item gives a reason and a fix. «Заполнить демо-данные» runs the seed (BE-F).
- **Tab «Снимки»** (**ADM-06**): list, «Создать снимок» with a name and note, «Скачать», and «Восстановить» with typed confirmation and the list of what will be rolled back, plus undo through `safety_snapshot`. An `X-CQA-Admin-Token` prompt appears when it is required.
- **Tab «Задания»** (ADM-07): jobs with type, actor, version, provider, progress, cost and status, and «Остановить», «Возобновить», «Повторить неудачные».
- **«Подготовить к работе жюри»** (DEMO-04, should; BE-F): takes the snapshot, lowers the caps, applies the hands-on budget, shows a banner.
- **`#/audit`** (ADM-05): a filterable list from `GET /api/audit` with localised actions, «Скачать CSV» (admin) and an entity link back to the card.

### 5.12 Embedded card (Dynamics 365)

Stories: D365-01, D365-02, D365-03 (later), D365-04 (later).

- `EmbedPage` renders only the card: no sidebar and no role switch. It always uses the light theme and sends `X-CQA-Role: visitor`. It is read-only: no «Переоценить» and no appeal (until D365-03 passes a role and user through `CQA_CONFIG`).
- The `PageHeader` is compact. A provenance line reads «Официальная оценка · claude-opus-5-5 · чек-лист v1 · дата». «Открыть в CQA» opens a new tab at the full card.
- It reads `data=` (D365 custom parameter) and `userlcid` (1049 → RU, 1033 → EN, 1087 → KK).
- In 600–900 px panes it shows one column with tabs. Compact states cover «Диалог не найден в CQA», «Оценки пока нет», «CQA недоступен · Повторить» and blocked storage.
- The single-file build budget is 4,5 MB, checked in CI by WP-L.

### 5.13 Сценарии для жюри and Справка

Stories: **DEMO-01**, **DEMO-02**, **DEMO-17**, SHELL-14.

- The **sheet** opens from the sidebar footer and `?` and holds the six scenarios of §3.3.3.
  - «Открыть» switches the role when needed, with the label «переключит роль на «Оператор · Нина»».
  - A one-time coach mark highlights the relevant control («Нажмите «Оспорить оценку» у критерия C6»).
  - Completed scenarios are ticked per browser, and «Начать заново» clears the ticks.
  - The sheet never covers the ring or the primary buttons. On mobile it becomes a bottom sheet.
- **«Защита»** entry (DEMO-17): «Подготовить защиту» runs the readiness check, warms the cards used, and shows a small «0:00 / 5:00» timer in presentation mode. The script lives in `docs/DEMO.md`; WP-L rewrites it for the light theme and the Operator role.
---

## 6. Backend changes

### 6.0 Principles

- **Backward compatible with `docs/API.md`:** only new optional request fields, new optional query parameters, new response fields and new endpoints. Existing fields, codes and defaults keep their meaning.
- **Two deliberate tightenings,** both fixing data-quality bugs:
  - A blank appeal comment or a blank decision reason is refused (`422 validation_error` / `400 reason_required`). The 10- and 15-character minimums live in the UI only.
  - Paste and upload no longer silently overwrite an organizer dialog or an evaluated dialog (`409 conversation_exists`).
- Every error uses the envelope `{"error": {"code", "message", …}}` with `X-Request-ID` (working tree). Headers `X-CQA-Role` and `X-CQA-User` attribute every write.
- **Not touched here:** `api/cqa/export/results_json.py`, `routers/export.py`, `scripts/control_run.py` (team lead), and the judge prompts, specs, packer, validate, ensemble and providers (ML owner).

### 6.1 Prerequisite (team lead, INT-0): deploy the working-tree backend

The architecture-phase packages are written but uncommitted and not deployed: roles and audit middleware, guards, resume, checklist governance, overview and scorecards, snapshots, system endpoints, calibration. The hosted `/api/roles`, `/api/me`, `/api/overview` and `/api/version` return 404. Must-demo items that rely on them are role enforcement (SHELL-06), budget caps (ADM-03), snapshots (ADM-06), the `activate` flag (CHK-12), weights and export ids (CHK-04, CHK-21) and the overview (EXE-01). INT-0 commits, tests (`pytest -q`) and deploys them first. The UI still works without them (§6.4).

### 6.2 Schema batch (INT-0, one additive migration in `models.py` and `db.py`)

| Table | New column | Type, default | For |
|---|---|---|---|
| `appeals` | `requested_answer` | JSON NULL | APL-02 |
| `appeals` | `cited_turns` | JSON NULL (1-based numbers) | APL-02 |
| `appeals` | `reason_category` | VARCHAR(32) NULL | APL-02 |
| `appeals` | `withdrawn_at`, `edited_at` | DATETIME NULL | APL-04 |
| `appeals` | `original_comment`, `closed_reason` | TEXT NULL | APL-04, APL-10 |
| `appeals.status` | new values `withdrawn`, `closed` | — | APL-04, APL-10 |
| `decisions` | `violation_type` | VARCHAR(32) NULL | GV-05 |
| `decisions` | `reviewer_name` | VARCHAR(128) NULL | SHELL-05 |
| `evaluations` | `incomplete_codes` | JSON NULL | CONV-08 |
| `evaluation_criteria` | `carried_decision_id` | INTEGER NULL | APL-10 |
| `evaluation_criteria` | `copied_from_evaluation_id` | INTEGER NULL | CARD-08 |
| `evaluation_criteria` | `validation_issues` | JSON NULL | CARD-15 (should; ML owner coordinates) |
| `checklist_versions` | `created_by_role`, `created_by_name` | VARCHAR NULL | CHK-14 |
| `checklist_versions` | `locked` | BOOLEAN 0 (seed sets v1 = 1) | CHK-15 |
| `conversations` | `content_hash`, `stale_since` | VARCHAR(64) NULL, DATETIME NULL | CONV-02 |
| `conversations` | `audio_offset_manual` | FLOAT NULL | CALL-03 |
| `conversations` | `is_test` | BOOLEAN 0 | CONV-13 (later) |
| `turns` | `in_recording` | BOOLEAN NULL | CALL-03, CALL-04 |

### 6.3 Endpoint changes by backend work package

Priority: **M** = must-demo, **S** = should, **C** = could.

**BE-A · Conversations and intake** (`routers/conversations.py`, `ingest/service.py`, `ingest/adapter.py`, `serializers.py`)

| # | Endpoint | Change | Stories | P |
|---|---|---|---|---|
| A1 | `GET /api/conversations` | New params: `criterion` + `answer` (`yes`, `no`, `na`, step), `lost=1`, `sort` (`total_asc`, `total_desc`, `id`, `turns`, `first_response`, `evaluated_at`); `status` also accepts `failed`, `incomplete`, `not_evaluated`, `running`; `review_state`, `has_open_appeal`, `checklist_version_id`; `page_size` up to 500. Items add `checklist_version_no`, `checklist_name`, `violation_types[]`, `open_appeals`, and `match {turn_idx, snippet}` when `q` hits transcript text. | **CONV-10**, **CONV-08**, CONV-09, ANL-02, CHK-19, **GV-03** | M |
| A2 | `POST /api/datasets/import` | `dry_run: true` returns `{preview_token, records, by_language, by_channel, new, updated, unchanged, warnings[]}`. `preview_token` imports the cached preview without a second organizer call. The dataset is upserted by `(group_id, source)`; per-record `content_hash`; changed transcripts set `stale_since`. Without `dry_run` the behaviour is unchanged. | **CONV-01**, **CONV-02** | M |
| A3 | `POST /api/conversations/parse` (new) | Dry-run parse of pasted text or JSON: `{turns[{no, speaker, timestamp, text, recognized}], suggested_language, warnings}`. Nothing is stored. | **CONV-04** | M |
| A4 | `POST /api/conversations/paste`, `/upload` | `on_conflict: error`, `rename` or `replace`. Organizer or evaluated ids answer `409 conversation_exists {suggested_id}`; `replace` is allowed only for `manual-*` without evaluations. Upload keeps returning `{imported, conversation_ids}`. | **CONV-06**, CONV-05 | M |
| A5 | `POST /api/datasets/import-url` (new) | https only, private ranges blocked, 20 MB cap, imported as `control-<name>` | **CONV-03** | S |
| A6 | `GET /api/conversations/{id}/evaluation` | Optional `evaluation_id` (pin). Payload adds `checklist_version_no`, `checklist_name`, `incomplete_codes`, `copied_from_evaluation_id` per criterion. | CARD-17, CHK-19, **CARD-08** | S |

**BE-B · Appeals, decisions, carry-forward** (`routers/appeals.py`, `review/service.py`, and only the partial-copy and error paths of `judge/runner.py`; tell the ML owner before merging)

| # | Endpoint | Change | Stories | P |
|---|---|---|---|---|
| B1 | `POST /api/appeals` | Optional `requested_answer`, `cited_turns[]`, `reason_category`. Comment non-blank, ≤ 2 000 (422). Operator on a dialog with no detected operator: `403 dialog_unassigned`. | **APL-01**, APL-02, APL-03 | M |
| B2 | `POST /api/appeals/preview` | Adds `criteria[{code, weight_applied_before, weight_applied_after, earned_before, earned_after}]` and keeps `sections`, `critical_error` | **APL-07**, **APL-08** | M |
| B3 | `POST /api/appeals/{id}/decision` | Optional `violation_type` (critical criterion) and `reviewer_name`. Blank `reason` → `400 reason_required`. An already decided appeal → `409 already_decided` with the existing decision in the body (was 400). | **APL-07**, GV-05, APL-12 | M |
| B4 | Partial re-evaluation copy path (`judge/runner.py`) | A criterion copied unchanged inherits `final_answer`, `final_score`, `final_earned`, `review_state` and gets `carried_decision_id`. The final total is recomputed with the new version's weights. | **APL-10** | M |
| B5 | Evaluation status | When a criterion call errors after the retry, `incomplete_codes` is set and `total_score` stays for compatibility. Readers (BE-C, UI) treat the total as not computed. | **CONV-08** | M |
| B6 | `GET /api/appeals` | New params `code`, `language`, `channel`, `q`, `sort` (`priority`, `oldest`, `confidence`); `status` adds `withdrawn` and `closed`. Items add `confidence`, `weight_applied`, `checked_span`, `is_latest_evaluation`, `stale_reason`, `requested_answer`, `cited_turns`, `reason_category`, `checklist_version_id`. | **APL-05**, **APL-06**, APL-11, APL-13 | M |
| B7 | `POST /api/appeals/{id}/withdraw`, `PATCH /api/appeals/{id}` (new) | Withdraw (author or admin; `409 already_decided`); edit the comment, keeping `original_comment` and `edited_at` | APL-04 | S |
| B8 | `POST /api/appeals/{id}/close` (new) | Closes stale appeals with a reason | **APL-10** | S |

**BE-C · Insights and public stats** (`analytics/*.py`, `routers/insights.py`, `routers/analytics.py`, new `routers/public.py`)

| # | Endpoint | Change | Stories | P |
|---|---|---|---|---|
| C1 | `GET /api/violations` | New params `dataset_id`, `review` (`ai_only`, `confirmed`, `under_appeal`, `overturned`). Items add `rule{ru,en,kk}`, `related[{code, answer, explanation}]` (C3 for disclosure), `classified_by` (`judge` or `keywords`), `open_appeal_id`, `decision{reviewer_label, at, reason}`, `evidence[].turn_no` (1-based). The critical criterion is selected by `is_critical`, not the code C8. The UI has static fallbacks for `rule` and `related`. | **GV-01**, **GV-02**, GV-04, GV-06 | M |
| C2 | Aggregates | Exclude evaluations with `incomplete_codes` from analytics, the overview, violations and operators | **CONV-08** | M |
| C3 | `GET /api/analytics/cost-statement` (new) | Models, mode, mean and p90 cost per full evaluation by channel with n and dates, formula and rates, partial re-evaluations shown separately, a README Markdown block | **EXP-03** | M |
| C4 | `GET /api/public/stats` (new, cached 60 s, read-only, no PII) | `{evaluated, conversations, by_language, by_channel, gross_violations{total, by_type}, cost{per_evaluation_mean, total, n}, latency_mean_ms, models[], local_provider{configured, reachable}, as_of, api_version}` | LAND-02 | S |
| C5 | `GET /api/overview`, `GET /api/analytics/summary`, `GET /api/operators` | Shared scope params `dataset_id`, `language`, `channel`, `basis` (`final` or `ai`); overview adds `bands{good, attention, critical}`; summary adds `cost_by_channel[]` and latency p50/p95; operators add `min_n` and `name_source` | ANL-01, EXE-02, EXE-04, ANL-05, ANL-12, CHK-28 | S |
| C6 | `GET /api/violations/candidates` (new) | C3/C8 contradictions, low-confidence or split C8, signal turns; no model call | GV-08 | S |

**BE-D · Checklists, scoring, runs and jobs** (`checklist/*.py`, `routers/checklists.py`, new `routers/scoring.py`, `jobs/*.py`, `routers/evaluations.py`)

| # | Endpoint | Change | Stories | P |
|---|---|---|---|---|
| D1 | `POST /api/evaluations/run` | `conversation_ids: []` → `400 empty_selection` (never "all"). Optional `dataset_id` scope. Partial runs with nothing to re-judge make zero model calls and recompute totals. The response adds `fallback_full[]` (dialogs without a base evaluation). | **SYS-06**, **CHK-16**, **CHK-04** | M |
| D2 | `GET /api/jobs/{id}/summary` (new) | `[{conversation_id, before_total, after_total, changed_codes, status}]` | **CHK-16**, **DEMO-12** | M |
| D3 | Checklist version payload | Adds `created_by_role`, `created_by_name` (from headers), `locked`, `evaluations_count`, `open_appeals_count`, `results_json_compatible`, `missing_export_ids[]`. `weights` and `export_ids` already exist in the working tree. | **CHK-01**, **CHK-15**, **CHK-21**, CHK-14 | M |
| D4 | `POST /api/checklists/{id}/versions` | Optional `base_version_id` → `409 version_conflict` when a newer version exists. The `activate` flag is in the working tree. | **CHK-12** | M |
| D5 | `POST …/judge-spec/{code}/regenerate` | `409 locked` on v1 and on versions with evaluations | **CHK-15** | M |
| D6 | `GET …/versions/{a}/diff/{b}` | `changed[].before` and `after` per field | **CHK-13** | M |
| D7 | `POST /api/scoring/preview` (new) | `{checklist_version_id or criteria + scoring_rules, answers{code: value}, channel}` → `{total, applied_weights, sections, critical}`. Pure engine call, nothing stored. | **CARD-06**, CHK-11, **CHK-06** | M |
| D8 | Version save | Compile judge specs outside the write transaction; busy timeout 30 s (`db.py` through INT) | **SYS-05** | M |
| D9 | `POST /api/checklists/{id}/versions/{base}/impact` (new) | Totals under draft weights and rules from stored verdicts | CHK-18 | S |

**BE-E · Audio sync and call structure** (`ingest/normalize.py`, `audio/*.py`, `routers/audio.py`)

| # | Endpoint | Change | Stories | P |
|---|---|---|---|---|
| E1 | Normalisation | Anchor call turn offsets on the recording-start event («Запись и транскрибирование запущены» and its KZ and EN forms). Set `turns.in_recording` and `meta.recording_start_idx`. A one-off re-normalisation of the 5 DEV calls; evidence offsets resolve from turns, so no re-evaluation is needed. | **CALL-03**, **CALL-09** | M |
| E2 | `GET /api/audio/{id}/sync`, `PUT` and `DELETE` (new) | Audio duration, transcript span, start index, automatic and manual offset; manual nudge (audited) | **CALL-03** | M |
| E3 | `POST /api/audio/{id}/refresh` (new) | Re-resolve an expired source link | **CALL-12** | S |
| E4 | `POST /api/audio/prewarm`, `GET /api/admin/audio` (new) | Cache recordings, peaks and features before the demo | CALL-13 | S |
| E5 | Hold segments from events, pause classification | `features.py`. Tell the ML owner if the acoustic summary passed to the judge changes. | CALL-05 | S |

**BE-F · Admin, settings, roles** (`admin/*.py`, `routers/admin.py`, `routers/settings_router.py`, `security/roles.py`)

| # | Endpoint | Change | Stories | P |
|---|---|---|---|---|
| F1 | `POST /api/admin/demo-seed` (new, idempotent) | An open appeal by «Оператор · Алексей» on p1-ru-029 C7; a QA-analyst appeal on p4-ru-002 C1 (decidable as N/A); a decided appeal on p1-ru-023 C8 (confirmed); `add_to_fewshot = false`; labelled «демо-пример»; never on control dialogs; running it twice creates nothing new | **DEMO-03**, **DEMO-02** | M |
| F2 | `GET /api/admin/readiness` (new) | The go/no-go checks of §5.11 in under 5 s with no paid call | **DEMO-03** | M |
| F3 | `PUT /api/settings` | Validation (422 per field), runtime limits `daily_budget_usd`, `max_dialogs_per_run`, `max_concurrent_jobs`; `confidence_threshold` | **ADM-03**, ADM-01, CARD-09 | M |
| F4 | `roles.py` | Nav label `review` → «Апелляции / Appeals / Апелляциялар»; admin home `#/admin`; nav orders as in §2.2. Optional, later: director `appeals.create` limited to the critical criterion. | **SHELL-04** | M |
| F5 | `POST /api/admin/hands-on` (new) | Snapshot, hands-on caps and budget, banner flag, audited | **DEMO-04** | S |

### 6.4 Feature detection in the UI

At start-up the UI probes `GET /api/version` once: 200 means the working-tree build, 404 means the deployed build. It also handles 404 per call:

| Missing | UI behaviour |
|---|---|
| `/api/roles`, `/api/me` | Local role registry (`lib/roles.ts`); headers are still sent; the old API ignores them |
| `/api/overview`, `/api/operators/{name}` | Computed from `/api/analytics/summary`, `/api/violations` and `/api/conversations?page_size=500`; overview-only tiles say «Доступно после обновления API» |
| `/api/public/stats` | Landing uses summary + violations + health, then the snapshot |
| `/api/export/results/*` | Dialog shows the CLI command |
| `/api/admin/*`, `/api/audit` | Tabs show «Недоступно в этой сборке API» |
| New appeal fields, `/withdraw`, `/close` | Fields are hidden unless echoed back; actions hidden on 404 |
| `/api/conversations/parse` | The paste preview parses on the client (same speaker rules) and marks it «предварительно» |

### 6.5 Requests to the ML owner (product hooks only, not planned here)

| Request | UI hook | Stories |
|---|---|---|
| Structured violation type and turn on critical verdicts | `TypeChip` uses it when present, keyword fallback otherwise | GV-06 |
| Rationale turn numbers 1-based (or agreed renumbering in the exporter) | `ExplanationText` links; the interim 0-based heuristic is removed after it lands | CARD-07, EXP-02 |
| Validator issue details per criterion | «Проверки ответа модели» block | CARD-15 |
| Summary, outcome and topic per evaluation | «Суть обращения» block and the topic chip are hidden until present | CARD-16, ANL-13 |
| Steering-attempt flag and transcript escaping | Row flag «Попытка повлиять на оценку» | SYS-12 |
| Acoustic summary changes (holds from events) | C6 «Факты о звонке» | CALL-05, CALL-11 |

---

## 7. Delivery plan

### 7.1 Rules for parallel agents in one repository

1. **One owner per file.** The ownership lists in §7.3 cover every file this plan touches. Any other file is frozen; ask the team lead.
2. **Auto-discovery instead of shared registries,** so no two packages edit the same list:
   - **Routes:** `App.tsx` (WP-B) loads `import.meta.glob('./features/*/route.tsx', { eager: true })`. Each feature's `route.tsx` exports `routes: AppRoute[]`. A feature without a `route.tsx` has no routes yet, and nav items whose route key is not registered are hidden.
   - **i18n:** `i18n/index.ts` (WP-A) loads `import.meta.glob('./{ru,en,kk}/*.ts', { eager: true })` as namespaces (`useTranslation('card')`). The legacy `ru.ts`, `en.ts` and `kk.ts` stay as the default namespace: add-only by WP-A, no deletions until INT-1.
   - **API:** each domain module `api/<domain>.ts` has one owner and holds its types, fetchers, hooks and query keys (prefix = domain). The legacy `api/types.ts`, `endpoints.ts` and `queries.ts` are frozen.
   - **Mock:** `mock/server.ts` (WP-L) loads `import.meta.glob('./handlers/*.ts')`. Each feature WP owns `mock/handlers/<domain>.ts` and `mock/fixtures/<domain>/*`.
3. **Shims keep untouched code compiling** until INT-1: `components/common.tsx`, `store/ui.tsx`, `components/Mascot.tsx` and the legacy api and i18n files.
4. **Contract first.** The props and signatures in §7.2 are frozen at T0. Changes are additive (new optional props only) and announced in the commit message with a `contract:` line.
5. **Commit hygiene.**
   - `git add <own paths>` only, never `git add -A`; `git pull --rebase` before every push.
   - Commit only states where `pnpm typecheck` passes.
   - A `route.tsx` is added last, once its page compiles.
   - If a file you do not own breaks the build, tell its owner rather than fixing it.
6. **Must-demo first** inside every package. Each package finishes with its acceptance checks (§7.3) on `VITE_MOCK=1` and on the local API.
7. **Copy.** RU comes from this document. EN and KK land in the same commit; KK lines get `// TODO native review` where unsure.
8. **Backend packages** keep their own test file, keep `pytest -q` green, never call the Claude API in tests, and never edit the shared files (`main.py`, `models.py`, `db.py`, `config.py`, `export/results_json.py`, `routers/export.py`, `docs/API.md`). They hand INT a short "API delta" note instead.

### 7.2 Frozen contracts (T0)

```ts
// web/src/lib/roles.ts (WP-B)
export type Role = 'operator' | 'analyst' | 'reviewer' | 'lead' | 'director' | 'methodologist' | 'admin' | 'visitor';
export type Permission = 'read' | 'conversations.import' | 'evaluations.run' | 'appeals.create' | 'appeals.decide'
  | 'checklists.write' | 'settings.write' | 'calibration.write' | 'admin' | 'integrations.ingest';
export type RouteKey = 'landing' | 'overview' | 'conversations' | 'card' | 'violations' | 'review' | 'reviewItem'
  | 'myEvaluations' | 'myAppeals' | 'checklists' | 'checklistEditor' | 'analytics' | 'operators' | 'operator'
  | 'settings' | 'admin' | 'audit' | 'calibration';
export const ROLES: Record<Role, { label: {ru: string; en: string; kk: string}; description: {ru: string; en: string; kk: string};
  permissions: Permission[]; home: string; nav: RouteKey[]; navBottom: RouteKey[] }>;

// web/src/app/types.ts (WP-B)
export interface AppRoute { key: RouteKey; path: string; element: React.ReactNode; layout?: 'app' | 'bare';
  requires?: Permission[];                       // otherwise read-only with a banner
  title: (t: TFunction, params: Record<string, string>) => string; }

// web/src/store/session.tsx (WP-B)
export function useSession(): { role: Role; identity: string | null;
  setRole(role: Role, opts?: { identity?: string | null; navigate?: boolean }): void; setIdentity(name: string | null): void;
  theme: 'light' | 'dark' | 'system'; setTheme(t: 'light' | 'dark' | 'system'): void;
  presentation: boolean; setPresentation(v: boolean): void; mascotEnabled: boolean };
export function useCan(p: Permission): boolean;
export function useCanAppeal(c: { operator_name?: string | null; agent_name?: string | null }): { ok: boolean; reason?: string };

// web/src/lib/url-state.ts (WP-B)
export function useUrlState<T extends Record<string, unknown>>(schema: UrlSchema<T>): [T, (patch: Partial<T>) => void];

// web/src/api/invalidate.ts (WP-B)
export function invalidateAfter(event: 'appeal.created' | 'appeal.decided' | 'appeal.withdrawn' | 'evaluation.done'
  | 'version.saved' | 'settings.saved' | 'import.done', ids?: { conversationId?: string }): void;

// web/src/lib/format.ts (WP-A)
export function formatTotal(v: number | null | undefined): string;       // «51,25» / «51.25» / «—»
export function formatPoints(v: number | null | undefined): string;      // «7,5»
export function formatPercent(v: number | null | undefined, digits?: number): string;
export function formatMoney(v: number | null | undefined): string;       // «$0,22»
export function formatDuration(s: number | null | undefined): string;    // «10:20»
export function formatDateTime(iso: string | null | undefined): string;  // «07.11.2026, 18:40 (UTC+5)»
export const turnNo = (idx: number) => idx + 1;
export function plural(n: number, forms: [string, string, string]): string;
// web/src/lib/bands.ts (WP-A)
export function bandOf(total: number | null | undefined, bands?: { good: number; attention: number }): 'good' | 'attention' | 'critical' | 'none';
```

| Shared component | Owner | Frozen props | Consumers |
|---|---|---|---|
| DS set (`ScoreRing`, `ScoreBadge`, `CriterionIcon`, `PointsText`, `EvidenceChip`, `Highlight`, chips, `PageHeader`, `Card`, `EmptyState`, `ErrorState`, `KpiTile`, `DataTable`, `FilterBar`, `SegmentedControl`, `Button`) | A | §4.6 | all |
| `EvaluateDialog` | D | `{open, onOpenChange, conversationIds?: string[] \| null, datasetId?: number \| null, defaultVersionId?: number \| null, defaultMode?: 'full' \| 'partial', baseVersionId?: number \| null, onlyCodes?: string[] \| null, allowScopeAll?: boolean, title?: string, onStarted(jobId: string): void}`, a superset of today's props | C, H |
| `ExportResultsDialog` | D | `{open, onOpenChange, preset?: {groupId?: string; ids?: string[]; expect?: number}}` | K |
| `AppealDialog` | F | `{open, onOpenChange, conversation: ConversationSummary, evaluation: Evaluation, criterion: EvaluationCriterion, prefill?: {requestedAnswer?: string \| number; comment?: string; citedTurns?: number[]; reasonCategory?: string}, onCreated(appeal: Appeal): void}` | C, E |
| `WaveformPlayer`, `MiniPlayer` | C | `WaveformPlayer {conversationId, highlight?: {start, end} \| null, markers?: {t, kind}[], mode?: 'full' \| 'collapsed' \| 'static', peaks?: Peaks, onTime?(t)}` with ref `{seek(t, {play?, preroll?, stopAt?})}`; `MiniPlayer {conversationId, from, to}` | G, J |
| `TranscriptFragment` | C | `{turns: Turn[], from: number, to: number, highlight: number[], onTurnClick?(turn: Turn): void}` (1-based numbers) | G |
| `QualityCardCompact` | C | `{evaluation: Evaluation, rows?: number, staticMode?: boolean}` | J |
| `JobProgress`, `useJob` | B | `{jobId, compact?, onDone?, onDismiss?}` | C, D, H |
| `ScopeBar` and charts | I | `{value, onChange}`; chart props per §4.3 | E |
| `scenarios.ts` | B | `{id, title, sentence, role, identity?, to}[]` | J |

API module owners: `session` (B) · `conversations`, `runs`, `export` (D) · `card` (C) · `violations` (E) · `appeals` (F) · `review` (G) · `checklists` (H) · `overview`, `analytics`, `operators` (I) · `public` (J) · `settings`, `admin`, `audit` (K).

### 7.3 Work packages

The frontend lives in `web/`, with paths below relative to it. The backend lives in `api/cqa/`. Story IDs in **bold** are must-demo and come first.

**WP-A · Design-system foundation**
- **Owns:** `index.html`, `tailwind.config.ts`, `package.json`, `pnpm-lock.yaml`, `public/favicon.svg`, `src/index.css`, `src/styles/**`, `src/components/ui/**`, `src/components/ds/**`, `src/components/brand/**`, `src/components/Mascot.tsx`, `src/components/common.tsx` (shim), `src/lib/{format,bands,utils,useThemeColors}.ts`, `src/i18n/index.ts`, `src/i18n/{ru,en,kk}.ts` (add-only), `src/i18n/{ru,en,kk}/ds.ts`, `src/features/ds-gallery/**` (a dev-only `#/__ds` page showing every component state in light and dark).
- **Delivers:**
  - P0, in 45 minutes: tokens, Tailwind mapping, light default, Mascot gate, favicon, DS files with final props and basic rendering, legacy aliases.
  - Then the full §4.6–§4.7 styling and the dependencies `@fontsource-variable/inter`, plus `@playwright/test` and `@axe-core/playwright` for WP-L.
- **Stories:** **SHELL-01**, **SHELL-02**, SHELL-03, SHELL-08, SHELL-09, CHK-28.
- **Depends on:** nothing.
- **Accept:**
  - `pnpm typecheck`, `pnpm build` and `pnpm build:single` pass; the single file is ≤ 4,5 MB.
  - A fresh browser paints light with no `dark` class.
  - `#/__ds` renders every state in both themes.
  - `formatTotal(51.25)` gives «51,25» in ru and «51.25» in en.
  - With `mascot_enabled = false`, no route has an `img[src*="/mascot/"]`.

**WP-B · Shell, roles, session, jobs, help**
- **Owns:** `src/App.tsx`, `src/main.tsx`, `src/app/**`, `src/components/layout/AppShell.tsx`, `src/components/shell/**`, `src/components/ErrorBoundary.tsx`, `src/store/**`, `src/lib/{roles,permissions,url-state,storage,config,toast}.ts`, `src/api/{client,session,invalidate}.ts`, `src/features/jobs/**`, `src/features/help/**`, `src/i18n/{ru,en,kk}/{shell,help}.ts`, `src/mock/handlers/session.ts`.
- **Delivers:**
  - Routing: route glob, guards and the read-only banner, NotFound.
  - Sidebar, RoleSwitcher, RolePickerDialog, IdentityPicker.
  - Session state: the store with URL params and persistence; headers and the error envelope in the API client.
  - Global UI: offline and cold-start notices, JobIndicator, JuryScenariosPanel, presentation mode, nav badges.
- **Stories:** **SHELL-04**, **SHELL-05**, **SHELL-07**, **SHELL-10**, **SYS-03**, **DEMO-01**, **OPR-01** (picker), **OPR-02** (nav and guards), SHELL-06, SHELL-12, SHELL-13, DEMO-15.
- **Depends on:** WP-A P0.
- **Accept:**
  - The role pill lists 8 roles with descriptions.
  - `#/?role=director` lands on `#/overview` as Директор and survives a reload.
  - Оператор opens «Кто вы?» with API operators (no «—»); «мұр» finds Мұрат; Нина → `#/my/evaluations`.
  - Every write request in the network log carries `X-CQA-Role` (and `X-CQA-User` when set).
  - An unknown route shows NotFound; with the API down the offline banner appears.
  - The sidebar is 224 px at 1280, 64 px at 1100, and a top bar at 375.
  - The scenarios sheet opens all six targets.

**WP-C · Conversation card, calls, embed**
- **Owns:** `src/features/card/**`, `src/components/media/**`, `src/api/card.ts`, `src/i18n/{ru,en,kk}/card.ts`, `src/mock/handlers/card.ts`.
- **Delivers:** §5.2, §5.3, §5.12. The cross-WP components `WaveformPlayer`, `MiniPlayer`, `TranscriptFragment` and `QualityCardCompact`, published early as stubs.
- **Stories:** **CARD-01**..**CARD-08**, **CALL-01**, **CALL-02**, **CALL-03** (UI), **CALL-09**, **CALL-12**, **GV-03**, **APL-09**, **APL-10** (UI), **CONV-08** (banner), D365-01; then CARD-09..12, CARD-15, CALL-04..06, CALL-11.
- **Depends on:**
  - WP-A, WP-B.
  - WP-F `AppealDialog`: keep the legacy dialog until it lands, then switch the import and delete the old file.
  - WP-D `EvaluateDialog` (legacy props work today).
  - BE-E: the sync indicator degrades gracefully without it.
- **Accept, p1-ru-029 at 1280×720 without page scroll:**
  - Title, the meta line, the ring «51,25 / 100» with the critical arc and shield.
  - Gross strip «Оскорбление клиента · №10 · 01:23».
  - 8 rows with «earned / applied» («0 / 18,75»).
  - «Почему снижена оценка» with «Выявление потребности −18,75», «Эмпатия −18,75», «Управление диалогом −11,25» and the N/A line; both buttons.
  - Chip №10 highlights the turn in peach and sets `?criterion=C8&turn=10`.
  - «Как посчитан итог» shows coefficient 1,25 and the sum 51,25.
- **Accept, p4-ru-002:** two-track lanes from `/peaks`, «02:00 / 10:20» after a chip jump, and the C1 banner when the operator segment starts before the recording.
- **Accept, everywhere:** 1-based numbers in transcript, chips and explanations. Embed: light, read-only, no sidebar.

**WP-D · Conversations list, intake, runs, export entry**
- **Owns:** `src/features/conversations/**`, `src/features/export/**`, `src/api/{conversations,runs,export}.ts`, `src/i18n/{ru,en,kk}/{conversations,export}.ts`, `src/mock/handlers/{conversations,export}.ts`.
- **Delivers:** §5.1, §5.10, and the `EvaluateDialog` and `ExportResultsDialog` contracts.
- **Stories:** **CONV-01**..**CONV-08**, **CONV-10**, **EXP-01**, **ADM-03** (budget line), **SYS-06** (UI); then CONV-09, CONV-11.
- **Depends on:** WP-A, WP-B; BE-A (A1–A4; degrades) and BE-D (D1).
- **Accept:**
  - Default columns fit 1024 px without horizontal scroll, and filters round-trip through the URL.
  - «читать умеете» finds p1-ru-029 with a snippet; KZ plus «Только с грубыми» gives p1-kz-013 and p1-kz-015.
  - The import preview shows counts before saving.
  - Pasting with an existing id offers «Создать как …-2».
  - results.json validation shows 65/65 on DEV and downloads.

**WP-E · Gross violations register**
- **Owns:** `src/features/violations/**`, `src/api/violations.ts`, `src/i18n/{ru,en,kk}/violations.ts`, `src/mock/handlers/violations.ts`.
- **Delivers:** §5.4.
- **Stories:** **GV-01**, **GV-02**, **GV-03**; then GV-04, GV-08.
- **Depends on:** WP-A, WP-B; BE-C C1 (static fallbacks for `rule` and `related`).
- **Accept:**
  - 8 cards sorted ascending; type tiles 1 / 4 / 3 / 0.
  - `?type=disclosure&language=KZ` leaves only p1-kz-015.
  - «Открыть в диалоге» lands on C8 with the turn in peach; Back keeps the filters.
  - «Почему так классифицировано» shows the rule, the quote, the confidence and the related C3 for disclosure cases.

**WP-F · Operator portal and appeal filing**
- **Owns:** `src/features/operator/**`, `src/features/appeals/**`, `src/api/appeals.ts`, `src/i18n/{ru,en,kk}/{operator,appeals}.ts`, `src/mock/handlers/appeals.ts`.
- **Delivers:** §5.6 and the `AppealDialog` contract (stub on the first commit).
- **Stories:** **OPR-01** (pages), **OPR-02**, **OPR-04**, **OPR-05**, **APL-01**, **DEMO-02**; then OPR-03, APL-02, APL-03, APL-04.
- **Depends on:** WP-A, WP-B; BE-B B1 and B7 (hidden when missing).
- **Accept:**
  - `#/my/evaluations?as=Нина` shows p1-ru-005 at 78,46 and the personal summary.
  - On the card, «Оспорить оценку» opens C6 «4 из 10 · 9,23 / 23,08»; requested answer 7 previews «78,46 → 85,38».
  - Submitting sends `author_role = operator` and `author_name = Нина`, and the row shows «На рассмотрении».
  - «Мои апелляции» lists the appeal and, after a decision, «Изменено: 4 → 7 · итог 78,46 → 85,38».

**WP-G · Review queue and decisions**
- **Owns:** `src/features/review/**`, `src/api/review.ts`, `src/i18n/{ru,en,kk}/review.ts`, `src/mock/handlers/review.ts`.
- **Delivers:** §5.5.
- **Stories:** **APL-05**, **APL-06**, **APL-07**, **APL-08**, **APL-10** (UI), GV-05, CAL-12 (switch default off); then APL-11..13, DEMO-14.
- **Depends on:** WP-A, WP-B; WP-C `TranscriptFragment` and `MiniPlayer` (a local list fallback until they land); BE-B B2, B3, B6.
- **Accept:**
  - As Ревьюер, Нина's appeal is first and shows the author chip, the disputed answer with points and weight, the comment and the peach fragment.
  - «Изменить → 7» previews «78,46 → 85,38» within 1 s; the reason needs at least 15 characters; saving toasts «Итог: 85,38».
  - p4-ru-002 C1 offers N/A, sends "N/A" and previews «80,50 → 89,44».
  - The few-shot switch is off by default.

**WP-H · Checklists overview and editor**
- **Owns:** `src/features/checklists/**`, `src/api/checklists.ts`, `src/i18n/{ru,en,kk}/checklists.ts`, `src/mock/handlers/checklists.ts`.
- **Delivers:** §5.7.
- **Stories:** **CHK-01**..**CHK-06**, **CHK-10**, **CHK-12**, **CHK-13**, **CHK-15**, **CHK-16**, **CHK-21**, **DEMO-05**, **DEMO-12**; then CHK-07..09, CHK-11, CHK-14, CHK-17..19.
- **Depends on:** WP-A, WP-B; WP-D `EvaluateDialog`; BE-D D1–D7.
- **Accept:**
  - From the sidebar, the editor opens in two clicks. v1 is read-only with «Создать новую версию на основе v1».
  - Adding a criterion gives code C9 even after a deletion.
  - C7 5 → 10 shows Σ 105 in amber with shares; «Нормализовать до 100» gives 10/14/19/19/14/14/10 with undo.
  - Editing the C1 call instruction keeps the call N/A.
  - Saving a draft keeps v1 active; the diff shows old → new.
  - Apply to 3 dialogs, partial: weight-only says «0 вызовов · $0,00»; the summary lists old → new totals.

**WP-I · Overview, analytics, operators, charts**
- **Owns:** `src/features/{overview,analytics,operators}/**`, `src/components/charts/**`, `src/api/{overview,analytics,operators}.ts`, `src/i18n/{ru,en,kk}/{overview,analytics,operators}.ts`, `src/mock/handlers/{overview,analytics,operators}.ts`.
- **Delivers:** §5.8.
- **Stories:** EXE-01..05, EXE-07, EXE-08, ANL-01..06, ANL-11, ANL-12 (all should; executive look is a corporate-look must).
- **Depends on:** WP-A, WP-B; BE-C C5 (computed fallbacks).
- **Accept:**
  - As Директор, `#/overview` shows 82,04 with n = 65 and its definition, «Норма 43 · Внимание 9 · Критично 13», the risk tile «8 из 65 · 12,3 %» with 1/4/3, language cards with n, and the cost tile.
  - Every tile drills down to a filtered list. Only GET requests are sent.
  - `?print=1` fits one A4 page.
  - Charts pass the dataviz validator for their palette.

**WP-J · Landing**
- **Owns:** `src/features/landing/**`, `src/api/public.ts`, `public/og-card.png`, `src/i18n/{ru,en,kk}/landing.ts`, `src/mock/handlers/public.ts`.
- **Delivers:** §3.
- **Stories:** LAND-01, LAND-02, LAND-03, DEMO-01 (block), SHELL-15.
- **Depends on:** WP-A; WP-B (role picker, scenarios); WP-C `WaveformPlayer` in static mode and `QualityCardCompact` (own snapshot rendering until they land); BE-C C4 (fallback).
- **Accept:**
  - At 1280×720 the header, H1, subline, both CTAs and the product shot show without scrolling.
  - At 375 px nothing scrolls sideways.
  - The counters carry an as-of stamp; with the API down, the snapshot stamp shows.
  - No mascot; RU/KK/EN switch.
  - «Открыть демо» opens the role picker; the scenario cards set role and route.

**WP-K · Settings, administration, audit**
- **Owns:** `src/features/{settings,admin,audit}/**`, `src/api/{settings,admin,audit}.ts`, `src/i18n/{ru,en,kk}/{settings,admin,audit}.ts`, `src/mock/handlers/{settings,admin,audit}.ts`.
- **Delivers:** §5.9, §5.11.
- **Stories:** **SHELL-02** (toggle), **ADM-03** (limits), **ADM-06**, **EXP-03**, **DEMO-03** (readiness UI), **DEMO-04**, **DEMO-16** («О системе»); then ADM-01, ADM-02, ADM-04, ADM-05, ADM-07.
- **Depends on:** WP-A, WP-B; BE-F, BE-C C3.
- **Accept:**
  - Theme is light by default and dark persists after choosing it.
  - «Фирменный персонаж» is off and read-only outside Admin.
  - System sections are read-only for non-admins with «Изменяет администратор».
  - Snapshots support create, download and typed-confirmation restore.
  - «Модель и стоимость» shows measured costs.
  - «О системе» shows the web and API commits.

**WP-L · Mock data, QA, CI, docs**
- **Owns:** `src/mock/server.ts`, `src/mock/fixtures/**` (shared fixtures), `tests/**`, `playwright.config.ts`, `scripts/{check-i18n,check-size}.mjs`, `docs/WEB.md`, `docs/DEMO.md` (team-lead sign-off). The CI job goes through INT.
- **Delivers:** §7.6 checks.
- **Stories:** **DEMO-17** (script), **DEMO-02** (runbook), DEMO-15, EXP-08, SYS-11.
- **Depends on:** everyone; runs continuously and makes the final pass in P2.
- **Accept:**
  - The smoke suite passes on mock and on the local API.
  - Fixtures hold no real-call transcript text (p4-ru-001 replaced by a masked, shortened excerpt).
  - i18n key parity across RU, EN and KK.
  - `docs/DEMO.md` uses the light theme, the Operator role and the hosted numbers.

**Backend packages** (owned files in §6.3; tests in `api/tests/test_<wp>.py`):
- **BE-A:** A1–A4 must, A5–A6 should.
- **BE-B:** B1–B6 must, B7–B8 should. Changes to `judge/runner.py` are announced to the ML owner before merging.
- **BE-C:** C1–C3 must, C4–C6 should.
- **BE-D:** D1–D8 must, D9 should.
- **BE-E:** E1–E2 must, E3–E5 should.
- **BE-F:** F1–F4 must, F5 should.
- **Accept, each package:** `pytest -q` green with new tests for every new endpoint and error code, plus an API-delta note for INT.

**INT · Integration (team lead)**
- **Owns:** `api/cqa/main.py` (router registration for `public` and `scoring`), `models.py`, `db.py`, `config.py`, `docs/API.md`, `docs/ARCHITECTURE_V2.md`, CI workflows, deploys. Also the team lead's own results.json and control-run work.
- **INT-0:** commit and deploy the working-tree backend; the schema batch (§6.2); `busy_timeout` 30 s.
- **INT-1:** merge API deltas into `docs/API.md`; remove shims and legacy aliases; fold the legacy api files; deploy web and API from one commit; run §7.6.

### 7.4 Order and timeline

T0 is the moment this proposal is accepted, at about H+7.

| Phase | Window | Work | Exit check |
|---|---|---|---|
| P0 Contracts | T0 → T0+0:45 | WP-A P0, WP-B skeleton (route glob, session, client headers, sidebar), INT-0 | Typecheck green; the app renders light with the new sidebar; the hosted API answers `/api/version` |
| P1 Build | T0+0:45 → T0+4:30 | WP-C…WP-L and BE-A…BE-F in parallel | Checkpoint at T0+2:30: the four live asks click through locally |
| P2 Integrate | T0+4:30 → T0+5:30 | INT-1 + WP-L | Hosted web and API from one commit; smoke and axe green; single file ≤ 4,5 MB |
| P3 Polish and rehearse | T0+5:30 → T0+7:30 | everyone | Two timed rehearsals under 4:45 (DEMO-17); bug list closed |
| Freeze | — | — | H+18 no new features (PLAN.md); H+20 UI freeze; H+22 configuration freeze (DEMO-07); H+23 control run by CLI, which does not depend on the UI; H+24 stop-code; no deploys afterwards (DEMO-16) |

Critical path:

```
INT-0 ──┐
WP-A P0 ┼─► WP-C card ─────────────┐
WP-B P0 ┘   WP-F operator + appeal ─┤
            WP-G review ────────────┼─► INT-1 deploy ─► rehearsal ×2 ─► freeze
            WP-E violations ────────┤
            WP-H checklists ────────┘
            BE-B (APL-10, CONV-08) and BE-D (SYS-06, CHK-16) feed WP-G and WP-H
WP-D, WP-I, WP-J, WP-K, WP-L and BE-A/C/E/F run alongside, off the critical path
```

**Staffing.**
- **With 6 frontend agents:**
  1. WP-A, then WP-J
  2. WP-B, then WP-K
  3. WP-C
  4. WP-F, then WP-G
  5. WP-H
  6. WP-D, then WP-E, then WP-I
- **Backend, 2 agents:** BE-B + BE-D; BE-A + BE-C + BE-F + BE-E.
- **WP-L:** the team lead or the first free agent.
- **With 12 or more agents:** one per package.

### 7.5 Must-demo slice and cut lines

- **Never cut:**
  - light default, no mascot;
  - the mockup card (ring, rows, «Почему снижена оценка», both buttons, peach evidence, 1-based numbers, N/A reason, «Как посчитан итог»);
  - the waveform on calls;
  - the violations register; Operator · Нина's appeal and the reviewer's decision with the N/A fix;
  - add, edit and reweight with save, diff and scoped apply;
  - attribution headers; read-only settings outside Admin; snapshots;
  - the results.json dialog; the jury scenarios.
- **Cut first if behind at T0+3:**
  - landing sections after «Сравнение» (keep hero, scenarios, how it works, FAQ, footer);
  - overview tiles beyond the KPI row and the languages card;
  - analytics tabs beyond «Сводка»;
  - matrix view, compare view, notifications;
  - CALL-04, CALL-05, CALL-06 polish; calibration; extra D365 views; the audit page.
- **If WP-I slips entirely:** Руководитель and Директор get `#/violations` and `#/analytics` (the existing page restyled) as home, so nobody lands on a missing route.

### 7.6 Integration and verification

- **Static:** `pnpm typecheck`, `pnpm build`, `pnpm build:single` with the size check (≤ 4,5 MB), `node scripts/check-i18n.mjs` (RU/EN/KK key parity), `cd api && pytest -q`.
- **Playwright smoke**, on mock and on the local API (WP-L):
  1. Landing → «Открыть демо» → role picker → QA-аналитик home.
  2. «Грубые нарушения»: 8 cards → p1-ru-029 at C8 with №10 in peach.
  3. Оператор · Нина → p1-ru-005 → appeal C6 requesting 7 → Ревьюер decides 7 → «Мои апелляции» reads 85,38.
  4. Checklist editor: v2 draft, add C9, C7 weight 10, normalise, save draft, diff, apply dry run with 0 calls for weight-only.
  5. p1-ru-029 at 1280×720: ring, «Почему снижена оценка» and both buttons inside the viewport.
  6. No `img[src*="/mascot/"]` anywhere.
  7. Every non-GET request has `X-CQA-Role`.
- **axe-core:** 0 serious issues on the list, the card, the register, the review queue and the overview.
- **Visual pass:** 1280×720, 1024×768, 375×812, and once on the venue projector.
- **Hosted readiness** (§5.11) all green before every rehearsal. Take the `pre-hands-on` snapshot before the jury's hands-on session (ADM-06).
---

## 8. Risks

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| R1 | Scope against time: 12 frontend packages in about 5 hours; agents collide on shared files | High | High | Must-demo slice and cut lines (§7.5); disjoint ownership with auto-discovered routes, i18n, API and mocks (§7.1); checkpoint at T0+2:30 | Team lead |
| R2 | The working-tree backend is not deployed (`/api/roles`, `/api/overview`, `/api/version` return 404 today), so enforcement, guards and the overview are missing on the hosted instance | High | High | INT-0 first; feature detection and computed fallbacks (§6.4); the must-demo UI never requires a new endpoint without a fallback | INT |
| R3 | Header enforcement breaks a flow after deploy (operator without `X-CQA-User` → 403 `operator_name_required`; methodologist cannot file appeals; director is read-only) | Medium | High | The UI permission matrix mirrors `roles.py` (§1.5); the IdentityPicker gates operator actions; smoke check 7; when `/api/roles` exists, WP-L compares it with `lib/roles.ts` | B, L |
| R4 | Self-declared roles: a juror picks Администратор and changes settings or restores a snapshot | Medium | High | In the picker, Администратор and Гость sit under «Ещё роли»; typed confirmation on restore; server caps and budget; snapshot `pre-hands-on`; optional access codes (DEMO-18) for the public QR after the event | B, K, BE-F |
| R5 | The p1-ru-029 card does not fit 1280×720 with the gross strip | Medium | High (live ask 4) | Compact density at viewport height ≤ 800; a 40 px strip collapsed by default; a sticky footer in the quality card; presentation mode; WP-C acceptance check on 1280×720 | C |
| R6 | 0-based numbers inside stored rationales («реплика [9]» for №10) contradict the 1-based UI (CARD-07) | High | Medium | Evidence chips (from `turn_idx`) are authoritative; the interim heuristic in `ExplanationText` (§4.7); the exporter or judge fix and the DEV regeneration (EXP-02, team lead and ML owner); the UI never rewrites stored text | C, team lead |
| R7 | Brand green used as text by habit (`#51AF3D` at 2,8:1) or status shown by colour alone (green and amber ΔE 0,9 for protan) | Medium | Medium | Tokens expose no text utility for `brand-bright`; a CI grep for `text-green-500` and raw `#51AF3D` in `.tsx`; icons plus words on every status (§4.3); axe in CI | A, L |
| R8 | Projector washes out the deep green, or the peach highlight becomes invisible | Medium | Medium | Projector check before the demo; one-token switches: `--sidebar-bg` to `#033A28` (SHELL-01 value) and `--evidence-bg-strong` for the active turn | A |
| R9 | Fonts or assets blocked at the venue or by the D365 CSP | Low | Medium | Self-hosted Inter in the bundle; system SF on Apple; no Google Fonts at runtime | A |
| R10 | Single-file D365 build exceeds 5 MB (fonts, landing, charts, mascot) | Medium | Medium | Lazy routes; mascot excluded from the bundle; a CI size check ≤ 4,5 MB | A, L |
| R11 | Real-call personal data reaches the public landing or the repository (EXP-08) | Medium | High | The landing shows peaks and numbers only for real calls; snapshots and fixtures hold no real-call text; WP-L review before commit | J, L |
| R12 | Mock or snapshot data mistaken for live analysis | Low | High | A permanent «Офлайн-демо…» badge; run buttons disabled in mock; landing stamps «Снимок на …» | B, J |
| R13 | During hands-on, a juror activates v2, files appeals or applies a version, and the next juror sees changed totals | Medium | Medium | Banner «Активная версия чек-листа изменена на v2 — обновить» with «Вернуть v1»; demo seed plus snapshot restore between sessions; rollback (CHK-26) later | H, K |
| R14 | Cold start makes the landing counters or the hero shot blank | Medium | Medium | Skeletons, then the snapshot with its stamp; the hero text never waits for the API | J |
| R15 | Carry-forward (APL-10) and incomplete handling (CONV-08) in `judge/runner.py` collide with the ML owner's changes | Medium | High | BE-B touches only the copy and error paths, announces before merging, adds offline tests; merge order agreed with the ML owner | BE-B, ML owner |
| R16 | `incomplete_codes` with a kept `total_score` is misread by an old client | Low | Medium | Only the new UI reads totals for display; aggregates exclude incomplete runs (C2); results.json is built by the team lead's exporter, which already writes error records | BE-B, BE-C |
| R17 | Kazakh copy of low quality | Medium | Low | Native review flags; RU fallback with a marker; key parity check; backend KK labels reused for nav | L |
| R18 | A deploy after the UI freeze or after the stop-code changes the demo build | Low | High | H+20 UI freeze; DEMO-16 tag, paused auto-deploy and an ignored-build rule for `results/**`; «О системе» shows the commit | Team lead |
| R19 | Fallback overview numbers differ from `/api/overview` | Low | Medium | One definition list (§5.8) shared by both paths; the fallback marks overview-only tiles as unavailable rather than estimating | I |
| R20 | Dark theme regressions eat time | Medium | Low | Dark is optional; light is the tested default; dark is checked only in the `#/__ds` gallery and on two screens | A |
| R21 | Mascot reappears through a forgotten `<Mascot>` call, against the organizer guidance | Low | High | The gate in `components/Mascot.tsx` returns null unless enabled; smoke check 6; never in the landing, print, embed, errors or score areas | A, L |

**Open decisions for the team lead**
1. `HERO_ID` for the landing product shot (`p4-ru-002` after the CALL-09 re-judge, or the synthetic `p1-ru-005`).
2. Whether to tuck Администратор and Гость under «Ещё роли» in the picker on the jury instance (recommended).
3. The RU label for the reviewer role: keep «Ревьюер» (matches the API and stories; recommended) or switch to «Проверяющий».
4. Sidebar `#0F5431` (the mockup) or the darker `#033A28` (SHELL-01), decided after the projector check. It is one token.
5. DEMO-18 access codes: off for the jury hands-on (recommended), on for an unattended public link after the event.
6. Server-side minimum lengths for comments and reasons: keep them in the UI only now (recommended) and tighten after the hackathon.

---

## Appendix A · Must-demo coverage (76 stories)

| Stories | Designed in | Work packages |
|---|---|---|
| SHELL-01 | §4.2, §2.3 | A, B |
| SHELL-02 | §4.10, §5.9 | A, K |
| SHELL-04 | §1.3, §2.2 | B, BE-F |
| SHELL-05 | §1.4, §1.6 | B, F, G |
| SHELL-07 | §5.0, §4.6 EmptyState | B (+ every page) |
| SHELL-10 | §4.5, §5.0, §5.2 | B, C, D, H |
| CONV-01, CONV-02, CONV-03, CONV-04, CONV-06 | §5.1, §6.3 A2–A5 | D, BE-A |
| CONV-07 | §5.1 | D (guards: INT-0) |
| CONV-08 | §5.1, §5.2, §6.3 B5, C2 | C, D, BE-B, BE-C |
| CONV-10 | §5.1, §6.3 A1 | D, BE-A |
| CARD-01, CARD-02, CARD-03, CARD-04, CARD-05 | §5.2, §4.7 | C |
| CARD-06 | §5.2 TotalBreakdown, §6.3 D7 | C, BE-D |
| CARD-07 | §4.4 `turnNo`, §4.7, §6.5 | C (+ team lead / ML owner for rationales) |
| CARD-08 | §5.2 «Детали оценки» | C, BE-A (A6) |
| CALL-01, CALL-02 | §5.3, §4.7 WaveformPlayer | C |
| CALL-03, CALL-09, CALL-12 | §5.3, §6.3 E1–E3 | C, BE-E |
| GV-01, GV-02 | §5.4, §6.3 C1 | E, BE-C |
| GV-03 | §5.1, §5.2, §5.4, §5.8 | C, D, E, I |
| APL-01 | §5.6, §6.3 B1 | F, BE-B |
| APL-05, APL-06, APL-07, APL-08 | §5.5, §6.3 B2, B3, B6 | G, BE-B |
| APL-09 | §5.2 | C |
| APL-10 | §5.2, §5.5, §6.3 B4, B8 | BE-B, G, C |
| CHK-01, CHK-02, CHK-03, CHK-04, CHK-05, CHK-06, CHK-10, CHK-12, CHK-13, CHK-15, CHK-16, CHK-21 | §5.7, §6.3 D1–D7 | H, BE-D |
| OPR-01, OPR-02 | §1.3–§1.5, §5.6 | B, F |
| OPR-04, OPR-05 | §5.6 | F |
| EXP-01 | §5.10 | D (endpoints: team lead) |
| EXP-02 | out of scope (§0) | Team lead (+ ML owner for rationale numbering) |
| EXP-03 | §5.9, §6.3 C3 | K, BE-C |
| ADM-03 | §5.1, §5.9, §6.3 F3 | D, K, BE-F (guards: INT-0) |
| ADM-06 | §5.11 | K (API: INT-0) |
| DEMO-01 | §3.3.3, §5.13 | B, J |
| DEMO-02 | §1.8, §5.6, §6.3 F1 | F, G, L, BE-F |
| DEMO-03 | §5.11, §6.3 F1–F2 | BE-F, K |
| DEMO-04 | §1.5, §5.11, §6.3 F3, F5 | B, K, BE-F |
| DEMO-05 | §5.7 | H |
| DEMO-06, DEMO-07, DEMO-08, DEMO-09, DEMO-10, DEMO-11 | out of scope (control run, results.json); UI entry §5.10 | Team lead |
| DEMO-12 | §5.7, §6.3 D1–D2 | H, BE-D |
| DEMO-16 | §3.3.13, §5.9 «О системе» | Team lead (tag, deploy freeze), J, K |
| DEMO-17 | §5.13 | L, team lead |
| SYS-01 | §6.1 | INT |
| SYS-03 | §5.0, §4.6 JobIndicator | B |
| SYS-05 | §6.3 D8 | BE-D, INT |
| SYS-06 | §5.1, §6.3 D1 | BE-D, D |

**Should, could and later stories by owner:**
- **J:** LAND-01..03, SHELL-15.
- **A:** SHELL-03, SHELL-08, SHELL-09, CHK-28 (with I).
- **B:** SHELL-06, SHELL-12..14, SHELL-16, DEMO-15.
- **C:** CARD-09..17; CALL-04..16 (with BE-E); D365-01..04 (D365-02 with K).
- **D:** CONV-05, CONV-09, CONV-11; CONV-13..16 later.
- **E:** GV-04, GV-06, GV-08 (with BE-C).
- **F:** APL-02..04, OPR-03; OPR-06..08 later.
- **G:** APL-11..20 (some later), GV-05.
- **H:** CHK-07..28 (with BE-D; CHK-24 knowledge base and CHK-27 document import later).
- **I:** ANL-01..13, EXE-01..09.
- **K:** ADM-01..11 (with BE-F), DEMO-13 later.
- **Team lead and L:** EXP-04..08.
- **INT and the backend packages:** SYS-02, SYS-04, SYS-07..12.
- **WP-M (later):** CAL-01..14, built on the existing calibration API and scoped with the ML owner.

## Appendix B · Navigation and key action labels (RU / EN / KK)

KK nav labels reuse `api/cqa/security/roles.py`; the KK action labels in the last four rows still need native review.

| Key | RU | EN | KK |
|---|---|---|---|
| overview | Обзор | Overview | Шолу |
| conversations | Диалоги | Conversations | Диалогтар |
| violations | Грубые нарушения | Gross violations | Өрескел бұзушылықтар |
| review | Апелляции | Appeals | Апелляциялар |
| checklists | Чек-листы | Checklists | Чек-листтер |
| analytics | Аналитика | Analytics | Аналитика |
| operators | Операторы | Operators | Операторлар |
| myEvaluations | Мои оценки | My evaluations | Менің бағаларым |
| myAppeals | Мои апелляции | My appeals | Менің апелляцияларым |
| settings | Настройки | Settings | Баптаулар |
| admin | Администрирование | Administration | Әкімшілік |
| audit | Журнал действий | Audit log | Әрекеттер журналы |
| action.jump | Перейти к фрагменту | Go to the fragment | Үзіндіге өту |
| action.appeal | Оспорить оценку | Dispute the score | Бағаға шағым беру |
| action.decide | Сохранить решение | Save the decision | Шешімді сақтау |
| action.openDemo | Открыть демо | Open the demo | Демоны ашу |
