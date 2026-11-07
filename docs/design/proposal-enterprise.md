# CQA design proposal: enterprise and Dynamics 365 first

Angle: **enterprise / Dynamics 365 first.** Real roles without a login (Operator, QA analyst, Reviewer, QA lead, Director, Methodologist, Admin, plus a read-only Guest), chosen with a role switcher or a `?role=` link and remembered; operators pick their own name from the operators found in the data; every appeal, decision and checklist change is signed and audited; the evaluation card and the main registers embed into Dynamics 365; a coaching loop and checklist governance turn scores into management actions; lists and queues stay usable at thousands of dialogs.

| | |
|---|---|
| Status | Proposal for the team lead (owner of product, design, landing, roles, frontend and backend). 2026-11-07, about 20:30 UTC+5. |
| Binding rules | `docs/ORGANIZER_UPDATES.md` wins over this file and over every older doc. |
| Inputs read | `docs/USER_STORIES.md` (196 canonical stories plus the 27 added by the two completeness reviews; story IDs are cited in brackets), `docs/design/organizer-mockup.png`, `docs/ARCHITECTURE.md`, `docs/ARCHITECTURE_V2.md`, `docs/API.md`, `docs/WEB.md`, `docs/INTEGRATION_D365.md`, the Figma kit `GLRFuWwhmuXArmiCgHz9Wr` (read-only: Landing `91:237`, Hero Options `145:560`, Components `3:2`, Design Book `0:1` chapters «03 Color» and «04 Type»), the web app at `810c3b5` and the API working tree at `99a1c9e` with its uncommitted packages. Hosted state checked on 07.11 at 20:27: `GET /api/health` ok, `GET /api/operators` 56 rows, `GET /api/roles` and `GET /api/version` 404 (the new backend is not deployed yet), CORS preflight accepts `X-CQA-Role` and `X-CQA-User`. |
| Not planned here | Judge prompts, judge specs, gold set, harness and local-model tuning (ML teammate, `docs/HANDOFF_ML.md`). The results.json builder, validator, CLI, endpoint and control-run script (team lead, in progress): only the UI entry point is planned (§5.13). |
| Priorities | **P0** = demo-critical slice (the four live asks, hands-on safety, the results entry point, the corporate look). **P1** = should. **P2** = could or post-hackathon. |

Conventions: UI copy in «», Russian first; turn numbers are 1-based everywhere (results.json `evidence_turns` = API `idx + 1`); RU and KK use a decimal comma. Reference data is the hosted DEV set: 65 dialogs (RU 35 / KZ 15 / EN 15, 60 chats / 5 calls), mean 82,04, median 95,00, 8 gross violations (insult 1: p1-ru-029; paid service bypassing the company 4: p1-ru-022, p1-ru-023, p1-kz-013, p1-en-013; disclosure 3: p1-ru-009, p1-kz-015, p1-en-014); p1-ru-029 = 51,25 (C3 N/A); p1-ru-005 = 78,46 → 85,38 after C6 4 → 7; p4-ru-002 = 80,50 → 89,44 after C1 Нет → N/A.

---

## 0. The proposal on one page

1. **Eight workplaces, no login.** The role is chosen in the top bar or by `?role=`, remembered per browser and sent with every request (`X-CQA-Role`, `X-CQA-User`). The API enforces it (403) and audits it. This is attribution with guard rails, stated honestly; in production the identity comes from Dynamics 365 / Entra ID.
2. **Operators act as themselves.** «Кто вы?» lists the operators detected in the evaluated dialogs; appeals are accepted only on the operator's own dialogs; unassigned dialogs (all five calls among them) are assigned by the QA lead.
3. **Corporate light by default, from the organizer mockup.** Deep-green sidebar, light neutral page, white cards, peach evidence, score ring «85,5 / 100», criteria rows «15 / 20», «Почему снижена оценка», «Перейти к фрагменту» / «Оспорить оценку», two-track waveform. Dark theme optional. The mascot is off unless an admin switches it on.
4. **The card is the product.** Every number on every screen drills down to a card that opens at the right criterion and turn, explains N/A and the total, and shows original → decision → final with names.
5. **The four live asks are one click each** from «Сценарии для жюри» (§1.6), and hands-on access cannot break the shared instance (no deletes, server caps, read-only settings, snapshots).
6. **Checklist governance:** drafts and active versions, a required change note and an author, a diff with old → new values and the cost of re-evaluation, a protected organizer baseline v1, apply to a chosen scope with partial re-evaluation, results.json compatibility per version.
7. **Coaching loop:** «Мои оценки» → «Ознакомлен(а)» or an appeal → reviewer decision → operator scorecard with a coaching focus → coaching note → trend → the methodologist fixes the wording or the knowledge base → new version → apply.
8. **Dynamics 365 first:** the card, the violations register, the review queue, the operator scorecard and the overview render as embeddable views that read D365 parameters (`data=`, `id=`, `userlcid=`), with a compact host density and operator appeals inside the tab when the host passes the user.
9. **Scale:** server paging, sorting and filtering; filters in the URL; bulk actions over «все N по фильтру» with estimates and batches; indexes. Postgres and a durable queue are the production path.
10. **Delivery:** two foundation packages first, then seven web and seven backend packages with disjoint files, then one integration pass. The P0 slice is about 6,5 hours of wall time with parallel agents (§7).

---

## 1. Role model

### 1.1 Roles and homes

| Code (API) | Label RU / EN / KK | Who at Freedom Broker | Home | Demo profile (editable) |
|---|---|---|---|---|
| `operator` | Оператор / Operator / Оператор | Contact-centre agent (chats, calls) | `#/my/evaluations` | picked from the data, e.g. «Нина» |
| `analyst` | QA-аналитик / QA analyst / QA-талдаушы | QA specialist: imports, runs evaluations, checks cards, files appeals | `#/conversations` | «Жанар Б.» |
| `reviewer` | Ревьюер / Reviewer / Ревьюер | Senior QA: decides appeals, confirms gross violations, gold labels | `#/review` | «Айгерим С.» |
| `lead` | Руководитель QA / QA lead / QA жетекшісі | Head of QA or of a support group: overview, operators, coaching, co-owner of the checklist | `#/overview` | «Ерлан К.» |
| `director` | Директор / Director / Директор | Director of client service: KPIs, risks, cost; read-only | `#/overview` | «Гульнара А.» |
| `methodologist` | Методолог / Methodologist / Әдіскер | Owns checklists, criterion wording, knowledge base, calibration | `#/checklists` | «Бекзат Н.» |
| `admin` | Администратор / Administrator / Әкімші | Platform owner: settings, limits, integrations, snapshots, audit export | `#/admin` | «Администратор стенда» |
| `visitor` | Гость / Visitor / Қонақ | Audience with a public link: read-only | `#/overview` | none |

The demo profile names do not collide with the 55 operator names in DEV, so an audit row never looks like an operator acted. `demo` (a request without a role header) stays in the API only for old clients: the redesigned web always sends a role, so the server permission check is real from the first click. Backend change B7 (§6) relabels `lead` «Руководитель QA», moves the admin home to `#/admin` and the visitor home to `#/overview`, and aligns the nav hints of `GET /api/roles` with §2.3; role codes do not change.

### 1.2 Choosing a role without a login

- **Role switcher (top bar, P0).** A pill «QA-аналитик · Жанар Б. ▾». The menu lists the eight roles with one line each («Решает апелляции и подтверждает грубые нарушения»), the home it opens, a check on the current role, «Указать своё имя» (staff roles), «Сменить оператора» (operator) and «Скопировать ссылку для этой роли» (SHELL-04, SHELL-05).
- **Link parameter (P0).** `?role=<code>` and `?as=<name>` in the search part or the hash query apply the role and identity on load, persist them, then leave the address bar (`history.replaceState`), so a later copy of the URL does not carry a role by accident; «Скопировать ссылку для этой роли» adds them on purpose. An unknown role shows «Роль "xyz" не найдена» and the picker; an unknown operator opens «Кто вы?» with «Оператор не найден».
- **Persistence.** `localStorage` keys `cqa.role` and `cqa.identity.v1` (role → name), every access in try/catch; when storage is blocked the choice lives in memory for the session.
- **Navigation on switch.** The new role's home opens unless the current route is allowed for it (then the page stays and re-renders with the new permissions).
- **Drafts on switch.** An unsaved appeal comment, decision reason or checklist draft is kept per author and the switch asks «Есть несохранённый текст. Сохранить черновик и переключиться?»; nothing is ever submitted under the new role (SHELL-04).
- **First open of an app route** without a stored role (an old link to `#/conversations` from the README): the role picker dialog opens over the page with «QA-аналитик» preselected; Enter accepts. One click, and it shows that filing an appeal and deciding it are separate roles, as the organizers ask.
- **Embed.** No switcher. Role and user come only from `window.CQA_CONFIG` (set by the host page); without them the embedded view is read-only (`visitor`) (D365-01, D365-03).
- **Requests.** The client adds `X-CQA-Role`, `X-CQA-User` (`encodeURIComponent`) and `X-Request-ID` to every call, and also puts `author_role` / `author_name` and `reviewer_role` into appeal and decision bodies. The deployed API already accepts those body fields (commit `9c1e787`), so attribution works before and after the security middleware ships; the translated `author_label` is no longer sent (ARCHITECTURE_V2 §15.1).
- **Enforcement.** The API refuses writes a role lacks with 403 `forbidden` and audits the attempt; the UI hides or disables the action with a reason, and a 403 shows the API message while keeping the user's input (SHELL-06).
- **What it is not.** Anyone can pick any role on the demo; this is attribution and guard rails, not authentication. In production, Entra ID / the Dynamics 365 user decides the role and the data scope on the server, and `?role=` / `?as=` are ignored (D365-06).

### 1.3 Operator identity

- **«Кто вы?» (IdentityPicker, P0)** opens when the operator role is chosen without an identity. Source: `GET /api/operators` (56 rows on hosted DEV: 55 names plus «—»; «—» is never listed). Row: initials avatar, name, language chips, channel icons, «1 диалог». Search is case-insensitive, treats ё as е and accepts Kazakh letters («мұр» finds Мұрат). Namesakes stay apart with their language: «Нина · RU», «Nina · EN».
- **No free text for operators**: the API accepts an operator's appeal only when the name matches the dialog's `operator_name` or `agent_name` (403 `not_your_dialog`). Staff roles may type any display name of up to 128 characters.
- **Demo hint**: with demo mode on, «Нина · RU · 1 диалог — сценарий апелляции» is pinned at the top (DEMO-02).
- **Unassigned dialogs** (10 on DEV: the 5 calls with masked names and 5 chats without an introduction) are listed for the QA lead under «Операторы → Не закреплены». Assignment writes `agent_name` (source identity, never sent to the judge) with `operator_source = manual` and an audit event (OPR-06). That is how the call p4-ru-002 becomes appealable by a demo operator for the call path of DEMO-02.
- **Shown as** «Оператор · Нина» in the top bar, «Апелляция · Оператор · Нина · 07.11 18:40» on the card and in the queue, «Оператор · Нина» in the audit log.
- Later (P2): an operators registry with aliases, teams and source ids from Dynamics 365 (OPR-08, ANL-08).

### 1.4 Permission matrix

Legend: **Y** can · **Own** own dialogs or own appeals only · **R** read-only · **—** hidden (a typed URL opens a read-only page «Раздел доступен роли …» with «Переключить роль»). The UI mirrors the API permissions of `api/cqa/security/roles.py`; the API is the enforcement point.

| Capability | Operator | QA analyst | Reviewer | QA lead | Director | Methodologist | Admin | Guest | API permission | Stories |
|---|---|---|---|---|---|---|---|---|---|---|
| Dialog list, search, filters | Own | Y | Y | Y | R | Y | Y | R | read | CONV-10, OPR-04 |
| Open a card: scores, evidence, «Почему снижена оценка» | Own; others R | Y | Y | Y | R | Y | Y | R | read | CARD-01..07 |
| Listen to a call, jump to the fragment | Own | Y | Y | Y | R | Y | Y | R | read | CALL-01, CALL-02 |
| Import by group_id, upload, paste | — | Y | — | Y | — | Y | Y | — | conversations.import | CONV-01..06 |
| Evaluate or re-evaluate (spends budget) | — | Y | Y (one dialog) | Y | — | Y | Y | — | evaluations.run | CONV-07, CARD-11 |
| Technical details and Trace | collapsed | Y | Y | Y | collapsed | Y | Y | collapsed | read | CARD-08 |
| Gross-violations register | Own | Y | Y | Y | Y | R | Y | R | read | GV-01..03 |
| Confirm or remove a gross violation (spot-check) | — | — | Y | Y | — | — | Y | — | appeals.create + appeals.decide | GV-04, GV-05 |
| «Запросить подтверждение» of a gross violation (P2) | — | — | — | — | Y | — | — | — | appeals.create, critical criteria only (new constraint) | APL-01, SHELL-06 |
| File an appeal on a criterion | Own | Y | spot-check | Y | — | — | Y | — | appeals.create | APL-01, APL-02 |
| Withdraw or edit an open appeal | Own | Own | Own | Own | Own | — | Y | — | appeals.create + author check | APL-04 |
| Decide an appeal (confirm, change, N/A) | — | — | Y | Y | — | — | Y | — | appeals.decide | APL-05..08 |
| «Мои апелляции», unread decisions | Y | Y (tab «Мои») | Y (tab «Мои») | Y | — | — | — | — | read | OPR-05 |
| Read the checklist (weights, scales, N/A, call rules) | R | R | R | R | R | Y | Y | R | read | CHK-01 |
| Add, edit, reweight, replace criteria; save; activate; roll back | — | — | — | Y | — | Y | Y | — | checklists.write | CHK-02..15 |
| Apply a version to dialogs (partial re-evaluation) | — | Y | — | Y | — | Y | Y | — | evaluations.run | CHK-16, DEMO-12 |
| Test a criterion draft on a dialog | — | — | — | Y | — | Y | Y | — | checklists.write | CHK-17 |
| Analytics: criteria, languages, loss drivers, cost | own summary | Y | Y | Y | R | Y | Y | R | read | ANL-01..12 |
| Executive overview | — | R | R | Y | Y | R | Y | R | read | EXE-01..07 |
| Operator scorecards | own (in «Мои оценки») | R | R | Y | R | R | Y | — | read | ANL-05, ANL-06 |
| Coaching notes; assign unattributed dialogs | read own; «Ознакомлен(а)» | — | — | Y | R | — | Y | — | team.manage (new) | ANL-07, OPR-06 |
| CSV and PDF exports | own PDF | Y | Y | Y | Y | Y | Y | — | read | EXP-05, EXP-06 |
| «Экспорт results.json» dialog | — | Y | — | Y | — | — | Y | — | read (GET) | EXP-01 |
| Calibration: gold labels, agreement | — | R | Y | Y | R | Y | Y | — | calibration.write | CAL-01..14 |
| Organization settings: judge, PII, limits, mascot | — | R | R | R | — | R | Y | — | settings.write | ADM-01..04, SHELL-02 |
| Personal settings: language, theme, presentation mode, my name | Y | Y | Y | Y | Y | Y | Y | Y | none (browser) | SHELL-08, SHELL-10 |
| Audit log | — | R | R | R | R | R | Y (+ CSV) | — | read; admin for CSV | ADM-05 |
| Snapshots, demo reset, hands-on mode | — | — | — | — | — | — | Y | — | admin | ADM-06, DEMO-04 |
| Dynamics 365 integration setup | — | — | — | — | — | — | Y | — | admin | D365-02 |
| Control-hour console (P2) | — | — | — | R | R | — | Y | — | admin | DEMO-13 |

Read scope: the API's `read` permission is global (any role may GET any dialog), so «Own» for operators is a UI filter on the demo (`?operator=` on lists, a read-only card «Диалог другого оператора» for someone else's dialog). Production closes this on the server with SSO (D365-06). This is the honest answer if the jury asks.

### 1.5 Attribution, audit and four eyes

Every write carries the actor (role, name, request id). The security middleware writes one `audit_events` row per successful or refused write; entities also keep their own author fields so screens do not depend on the log.

| Entity | Stored | Shown on |
|---|---|---|
| Appeal | `author_role`, `author_name`, comment, requested answer, cited turns, reason category (B1) | queue card «Автор: Нина (оператор) · 2 ч назад», criterion on the card, History, «Мои апелляции» |
| Decision | `reviewer_role`, reviewer name, action, reason, old → new answer, totals before and after, violation type for C8 | «Изменено проверяющим · Айгерим С. · 07.11 18:52», History, decisions CSV |
| Checklist version | author role and name (B5), change note, base version, time | version timeline, diff header, audit |
| Activation, roll back, apply job | actor in job params and audit | version timeline, jobs monitor |
| Operator assignment, coaching note, acknowledgement | author and time (B3, B4) | card header «назначено руководителем», scorecard, audit |
| Settings change | audit with old → new (secrets masked) | audit log; settings history (ADM-09, P2) |

- **Four eyes.** When the reviewer's name equals the appeal author's, the decision panel warns «Вы оспариваете и решаете сами: передайте апелляцию коллеге» and asks for a second confirmation; both names are stored (SHELL-05).
- **Immutability.** The AI verdict is never overwritten; decisions live next to it; a correction is a new decision that supersedes the old one (APL-14, P2); checklist versions are never edited in place (CHK-15).
- **Traceability.** Every error toast carries the API code and request id («budget_exceeded · ID r_8f2c41», copyable), the same id as the audit row and the access log line (SYS-08).
- **History on the card** is one timeline per criterion: Оценка ИИ (model, prompt version, checklist version, time) → Апелляция (author, comment) → Решение (reviewer, action, reason) → Итог (final answer and total), times in local time with the zone, plus «Все события диалога» → `#/admin/audit?q=<id>` (APL-09, ADM-05).
- **Provenance line** under the ring, collapsed by default: «Официальная оценка · claude-opus-5-5 · промпт v1:<hash> · чек-лист v1 · 07.11 16:12 (UTC+5)» (CARD-08, CHK-19).

### 1.6 What the jury sees on first open

1. `https://cqa-freedom-pi.vercel.app` opens `#/`: the corporate landing (§3). A deep-green hero with the headline, a live quality card of p1-ru-005 on the right, «Открыть рабочее место» and «Как это работает», and below it the numbers strip «65 диалогов · RU 35 / KZ 15 / EN 15 · 8 грубых нарушений · средний итог 82,04 · ≈ $0,22 за оценку · по данным API на 20:41 (UTC+5)». No login, no cookie wall, no mascot.
2. «Открыть рабочее место» → `#/start`: eight role cards, each with «Что вы увидите» in one line. Оператор continues to «Кто вы?»; every other role lands on its home in one click. A returning visitor sees «Продолжить как QA-аналитик · Жанар Б.».
3. In the app the top bar always shows the role pill and «Сценарии». «Сценарии для жюри» (DEMO-01) lists six scenarios; each switches the role when needed and says so first («переключит роль на Оператор · Нина»):

| Scenario | Opens | Role |
|---|---|---|
| Грубые нарушения и почему | `#/violations`: 8 dialogs, chips 1 / 4 / 3 | Руководитель QA |
| Апелляция оператора | `#/my/evaluations?as=Нина` → p1-ru-005 → «Оспорить оценку» on C6 | Оператор · Нина |
| Решение ревьюера | `#/review`: Нина's appeal first, preview 78,46 → 85,38 | Ревьюер · Айгерим С. |
| Изменить чек-лист | Checklist Studio on the active version: «Добавить критерий», weights and Σ 100 above the fold at 1280×720 | Методолог |
| Результат диалога с обоснованием | `#/conversations/p1-ru-029?criterion=C8&turn=10`: 51,25 and the gross-error block | QA-аналитик |
| Оценить новый диалог | `#/conversations?paste=1`: paste, parse preview, cost estimate | QA-аналитик |

4. A target without data (no open appeal, p1-ru-029 missing on a fresh instance) disables its entry with the reason or points to the first dialog with a gross violation; the readiness check (DEMO-03) makes sure this never happens on the hosted instance.

### 1.7 Coaching loop and checklist governance

```
Оценка ИИ ──▶ Оператор: «Мои оценки» — где теряю баллы, среднее по команде
                 ├─ «Ознакомлен(а)» ──────────────────────────────────────────┐
                 └─ «Оспорить оценку» ──▶ Ревьюер: решение + причина + пересчёт ┤
                                                                                ▼
Руководитель QA: карточка оператора — фокус коучинга (потери баллов, цитаты), нарушения, решения
                 └─ заметка коучинга (критерий, диалог, срок) ──▶ оператор видит её под критерием
                                                                                ▼
Методолог: критерии, где ревьюеры часто меняют ответ ИИ; отклонения от базы знаний (C4)
                 └─ новая версия чек-листа ──▶ применение с частичной переоценкой ──▶ тренд
```

| Step | Who | Screen | Stories |
|---|---|---|---|
| See where points are lost | Operator | «Мои оценки» header, criterion detail | OPR-04, CARD-03 |
| Acknowledge or dispute | Operator | card «Ознакомлен(а)» (P2), «Оспорить оценку» (P0) | ANL-07, APL-01 |
| Decide with precedents | Reviewer | decision panel «Похожие решения» (P1) | APL-07, APL-11 |
| Coach on concrete moments | QA lead | scorecard «Фокус коучинга», coaching note (P2) | ANL-06, ANL-07 |
| Fix the rule, not only the person | Methodologist | analytics overturn rates by criterion, knowledge-base triage (P2) | ANL-03, CARD-10, CHK-24 |
| Change and re-apply | Methodologist or QA lead | Checklist Studio save → apply | CHK-12, CHK-16 |
| Check the effect | QA lead, Director | analytics compare v1 ↔ v2, trend | ANL-09, ANL-10 |

Checklist governance (who may do what):

| Action | Methodologist | QA lead | Admin | Others | Guard |
|---|---|---|---|---|---|
| Create a draft version from any version | Y | Y | Y | — | change note required at save |
| Activate a version | Y | Y | Y | — | confirmation «Новые оценки будут использовать v2. Оценки на v1 не изменятся.» (CHK-14) |
| Roll back (activate an older version) | Y | Y | Y | — | same confirmation; nothing deleted |
| Apply to dialogs | Y | Y | Y | QA analyst | dry run with cost first; partial preset after a change (CHK-16) |
| Edit organizer baseline v1 | — | — | — | — | locked: «Базовая версия организаторов · только чтение» (CHK-15) |
| Approve a methodologist draft (P2) | — | Y | Y | — | draft → «На согласовании» → approved → active (CHK-23) |

---

## 2. Information architecture

### 2.1 Route map

Hash routes stay (the HashRouter works inside a Dynamics 365 iframe without server rewrites). Feature pages are lazy-loaded from `web/src/routes.tsx`.

| Route | Page title | Nav label | In the nav of | Stories |
|---|---|---|---|---|
| `#/` | Landing | — | public | LAND-01..03 |
| `#/start` | «Выберите рабочее место», then «Кто вы?» | — | public | SHELL-04, SHELL-05, OPR-01 |
| `#/overview` | «Обзор качества» | Обзор | lead, director, admin, guest | EXE-01..08 |
| `#/my` → `#/my/evaluations` | «Мои оценки» | Мои оценки | operator | OPR-01..04 |
| `#/my/appeals` | «Мои апелляции» | Мои апелляции | operator | OPR-05, APL-04 |
| `#/my/checklist` | «Чек-лист» (read-only) | Чек-лист | operator | OPR-02, CHK-01 |
| `#/conversations` | «Диалоги» | Диалоги | analyst, reviewer, lead, director, methodologist, admin, guest | CONV-01..16 |
| `#/conversations/:id` | «Анализ разговора» | — | everyone (operator: own; others read-only) | CARD-01..17, CALL-01..16 |
| `#/violations` | «Грубые нарушения» | Нарушения | analyst, reviewer, lead, director, methodologist, admin, guest | GV-01..08 |
| `#/review` | «Апелляции» | Апелляции | reviewer, lead, analyst, admin | APL-05..17 |
| `#/review/:appealId` | «Апелляция №42» | — | same | APL-13 |
| `#/operators` | «Операторы» | Операторы | lead, director, analyst, admin | ANL-05, OPR-06 |
| `#/operators/:name` | «Оператор · Алексей» (scorecard and coaching) | — | same | ANL-06, ANL-07 |
| `#/analytics` | «Аналитика» | Аналитика | analyst, reviewer, lead, director, methodologist, admin, guest | ANL-01..13 |
| `#/checklists` | «Чек-листы» (active checklist, versions) | Чек-листы | everyone except operator | CHK-01, CHK-14, CHK-20 |
| `#/checklists/:id/versions/:vid` | «Чек-лист v2 · Черновик» (Checklist Studio) | — | same; edit for methodologist, lead, admin | CHK-02..13, CHK-15..21 |
| `#/checklists/:id/diff/:a/:b` | «Сравнение v1 и v2» | — | same | CHK-13 |
| `#/calibration` (P2) | «Калибровка ИИ» | Калибровка | reviewer, lead, methodologist, admin | CAL-01..14 |
| `#/knowledge` (P2) | «База знаний» | База знаний | methodologist, lead | CHK-24 |
| `#/settings?tab=` | «Настройки» | Настройки | everyone (personal tabs); admin edits organization tabs | ADM-01..04, SHELL-02 |
| `#/admin` → `#/admin/system` | «Состояние системы» and readiness | Администрирование | admin | ADM-07, DEMO-03 |
| `#/admin/jobs` | «Задания» | — | admin; lead read | ADM-07, SYS-03 |
| `#/admin/audit` (alias `#/audit`) | «Журнал действий» | Журнал действий | admin, lead | ADM-05 |
| `#/admin/backups` | «Снимки базы» | — | admin | ADM-06 |
| `#/admin/integrations` | «Интеграции: Dynamics 365 и API организаторов» | — | admin | D365-02, ADM-08 |
| `#/admin/control` (P2) | «Контрольный час» | — | admin; lead and director read | DEMO-13 |
| `*` | «Страница не найдена» with a way back | — | — | SHELL-07 |

The embedded views run without the shell: `index.html?embed=1&view=<view>&…` (§2.4).

### 2.2 Global parameters and deep links

Parameters are read from the search part or from the hash query (the existing `param()` in `web/src/lib/config.ts`).

| Parameter | Example | Effect |
|---|---|---|
| `role` | `?role=reviewer` | applies and remembers the role, then leaves the address bar |
| `as` | `?as=Нина` | identity for the role; for operators the name must exist in `GET /api/operators` |
| `lang` | `?lang=kk` | UI language RU / KK / EN |
| `theme` | `?theme=dark` | one-off theme for this tab (print and embed are always light) |
| `present` | `?present=1` | presentation mode: base size 112 %, secondary metadata hidden (SHELL-10) |
| `api`, `mock` | `?api=https://…`, `?mock=1` | API override and offline mock (existing; mock now shows a permanent badge, DEMO-15) |

Deep links that must keep working (each one is checked by the integration package WI):

| Purpose | Link | Stories |
|---|---|---|
| Card at a criterion and turn | `#/conversations/p1-ru-029?criterion=C8&turn=10` | CARD-04 |
| Call at a moment | `#/conversations/p4-ru-002?criterion=C6&t=120` | CALL-01, CARD-12 |
| Card pinned to an evaluation | `#/conversations/p1-ru-029?evaluation=59` | CARD-17 |
| Filtered list | `#/conversations?language=KZ&critical=1&sort=total_asc` | SHELL-13 |
| Drill-down from analytics | `#/conversations?criterion=C5&answer=no&language=RU` | ANL-02 |
| Register filter | `#/violations?type=disclosure&language=KZ` | GV-01 |
| One appeal; a queue filter | `#/review/42`; `#/review?code=C7&language=KZ` | APL-13 |
| Operator portal | `#/my/evaluations?role=operator&as=Нина` | OPR-01 |
| Scorecard | `#/operators/Алексей` | ANL-06 |
| Studio at a criterion | `#/checklists/1/versions/2?criterion=C9` | CHK-02 |
| Version diff | `#/checklists/1/diff/1/2` | CHK-13 |
| Director overview | `#/overview?role=director&days=30` | EXE-01 |

### 2.3 Navigation per role

The sidebar order is fixed; each role sees a subset. The four items of the organizer mockup (Диалоги, Чек-листы, Апелляции, Аналитика) keep the mockup's line icons.

| Role | Sidebar, top group (badge) | Bottom group | Home |
|---|---|---|---|
| Оператор | Мои оценки · Мои апелляции (unread decisions) · Чек-лист | Настройки | `#/my/evaluations` |
| QA-аналитик | Диалоги · Нарушения · Апелляции (open) · Операторы · Аналитика · Чек-листы | Настройки | `#/conversations` |
| Ревьюер | Апелляции (open) · Диалоги · Нарушения (to confirm) · Аналитика · Чек-листы · Калибровка | Настройки | `#/review` |
| Руководитель QA | Обзор · Диалоги · Нарушения (to confirm) · Апелляции (open) · Операторы · Аналитика · Чек-листы · Калибровка | Журнал действий · Настройки | `#/overview` |
| Директор | Обзор · Нарушения · Операторы · Аналитика · Диалоги · Чек-листы | Настройки | `#/overview` |
| Методолог | Чек-листы · Калибровка · Диалоги · Нарушения · Аналитика (· База знаний, P2) | Настройки | `#/checklists` |
| Администратор | Обзор · Диалоги · Нарушения · Апелляции · Операторы · Аналитика · Чек-листы · Калибровка | Администрирование · Журнал действий · Настройки | `#/admin` |
| Гость | Обзор · Диалоги · Нарушения · Аналитика · Чек-листы | — | `#/overview` |

- Icons (lucide, 20 px, stroke 1.75): Обзор `LayoutDashboard`; Мои оценки `ClipboardCheck`; Мои апелляции and Апелляции `Scale` (the mockup's scales); Диалоги `MessageSquare` (mockup bubble); Нарушения `ShieldAlert`; Операторы `Users`; Аналитика `BarChart3` (mockup bars); Чек-листы and Чек-лист `SquareCheck` (mockup checkbox); Калибровка `Target`; База знаний `BookOpen`; Журнал действий `ScrollText`; Администрирование `ServerCog`; Настройки `Settings`.
- Labels: the sidebar says «Нарушения» (KK «Бұзушылықтар», EN «Violations») so the long Kazakh label fits; the page title is «Грубые нарушения».
- Badges: open appeals and unread decisions use a white pill with green-800 text; the «Нарушения» badge (violations awaiting confirmation) is red and appears only for reviewer and QA lead, so red stays rare.

### 2.4 Embedded views for Dynamics 365

| View | URL (`index.html?embed=1&…`) | Content | Writes |
|---|---|---|---|
| Card (default) | `view=card&conversation=<id>`, or the D365 record `id=<GUID>` | header, ring, criteria, «Почему снижена оценка», transcript, call player, History; Trace under «Технические детали» | appeal only when the host passes `role: 'operator'` and a user who owns the dialog (D365-03) |
| Gross violations | `view=violations` | compact register | none; rows open the full app in a new tab |
| Review queue | `view=review` | queue with the decision panel | decisions when the host passes `role: 'reviewer'` (D365-04) |
| Operator scorecard | `view=operator&operator=<name>` | scorecard and coaching focus | none |
| My evaluations | `view=my` + host user | operator portal list | appeals through the card |
| Overview | `view=overview` | KPI tiles and the risk tile | none |

- Dynamics 365 passes only `id`, `typename`, `type`, `orgname`, `userlcid`, `orglcid` and `data` to an HTML web resource. `lib/config.ts` reads `data` (URL-encoded `conversation=…&api=…&view=…`), `id` (GUID without braces, lower case), `userlcid` (1049 → RU, 1033 → EN, 1087 → KK) and `window.CQA_CONFIG = {apiUrl, embed, view, conversation, role, user: {name}, host: 'd365'}` (D365-01, `docs/INTEGRATION_D365.md` §5).
- Role and user come only from `CQA_CONFIG`, never from the URL; without them the embed is read-only.
- `host=d365` (or `CQA_CONFIG.host`) switches the compact host density (§4.8): Segoe UI on Windows, 14 px base, 8 px radius, 16 px card padding, one column below 768 px.
- Every view has «Открыть в CQA» (new tab, same deep link) and compact states: «Диалог не найден в CQA», «Оценки пока нет», «CQA недоступен · Повторить». Storage blocked by the iframe sandbox falls back to memory.

### 2.5 Cross-screen flows

- **Live ask 1, gross violations (ORG-3.1).** Нарушения → type chips → row «Почему так классифицировано» → «Открыть в диалоге» (C8 expanded, turn №10 in peach) → «← К нарушениям» keeps filters and scroll (GV-01, GV-02, CARD-04).
- **Live ask 2, operator appeal (ORG-3.2).** Оператор · Нина → Мои оценки → p1-ru-005 → «Почему снижена оценка» → «Перейти к фрагменту» (№12) → «Оспорить оценку» (C6, requested 7, comment) → toast «Апелляция отправлена ревьюеру» → switch to Ревьюер → Апелляции → «Изменить → 7», preview 78,46 → 85,38, reason → save → switch back → Мои апелляции «Изменено: 4 → 7 · итог 78,46 → 85,38» (DEMO-02).
- **Live ask 3, checklist change (ORG-3.3).** Методолог → Чек-листы → «Создать новую версию на основе v1» → «Добавить критерий» (C9) or C7 weight 5 → 10 (Σ 105, «Нормализовать до 100») → «Сохранить версию» (note, draft or active) → «Применить к диалогам» (scope, partial, dry run «0 вызовов · $0,00» for weights) → summary of old → new totals (DEMO-05).
- **Live ask 4, one dialog with its justification (ORG-3.4).** Search «p1-ru-029» (Enter on an exact id opens the card) → mockup card → «Как посчитан итог» → criterion detail → History (CONV-10, CARD-01..07).
- **New dialog (hands-on).** Диалоги → «Импорт ▾ → Вставить текст» → parse preview → «Создать и оценить» → the card fills criterion by criterion → Trace timestamps after the import time (CONV-04, CARD-08).
- **Coaching (§1.7).** Обзор → «Операторы, требующие внимания» → scorecard → coaching focus with quotes → coaching note → the operator sees it under the criterion and presses «Ознакомлен(а)».
- **Dynamics 365.** The conversation closes in D365 → Power Automate pushes the transcript → evaluation job → the agent's form tab shows the card → «Оспорить» inside the tab (D365-01..05).

### 2.6 Patterns for thousands of dialogs

| Concern | In this proposal (P0/P1) | Production path (P2) |
|---|---|---|
| Lists | server paging (50 per page), sorting and filters on `GET /api/conversations`; one page rendered; total and «Показать ещё» (SYS-09) | SQL-side latest evaluation, Postgres, keyset paging (SYS-10) |
| View state | every filter, sort, tab and selection scope in the hash query; «Скопировать ссылку» (SHELL-13) | saved views per role |
| Bulk work | «Выбрать все N по фильтру» → Evaluate dialog with estimate, budget and batches of at most 200 (CONV-07, SYS-09) | background batches with ETA and throughput |
| Queues | appeals queue with paging, filters and a stable link per appeal (APL-13) | claims, sampling plan for reviewers (APL-17, APL-20) |
| Long calls | collapsed IVR, consultation and duplicates; follow mode (CALL-04, CALL-06) | virtualized transcript for 1 500 turns, zoomable waveform (SYS-10) |
| Aggregates | overview and analytics from the API, n on every number, small-sample guards (ANL-03, ANL-05) | SQL aggregation, nightly rollups |
| Intake | synchronous import for 65 or 10 dialogs | import as a background job with batches and resume (CONV-14) |
| Identity | greeting name plus `agent_name` from the source system | operators registry with aliases and teams (OPR-08, ANL-08) |

---

## 3. Landing page (`#/`)

### 3.1 Principles and Figma mapping

- **Structure from the Figma Landing** (`91:237`, frame «Rota Landing / Desktop», 1440 × 11 957) and its parts in Components → «Block / Landing» (`92:235`: CTA Button Primary and Glass, Glass Chip, Problem Card, Language Card, Bullet, Compare Row, FAQ Item open and closed). It is rendered corporate: the kit's black theme, liquid glass, red backdrop and characters are dropped.
- **Hero = Hero Options → «Option C / Product stage»** (`145:589`): a real product window on a backdrop. The backdrop becomes a restrained deep-green band; the window is a live CQA card. Option D («Mac desktop», the site as a desktop) is rejected as too playful for a broker.
- **Product visuals are live React components** fed by the API (small fixtures when offline), never screenshots of characters. The 24 mascot poses are not used; the landing does not import `BrandMascot` at all.
- **Rhythm:** deep-green hero → white → #F5F7F5 → white …, a deep-green final call to action, a light footer. Max content width 1200 px; section padding 96 px (64 px below 768 px).
- **Honest numbers:** every figure is live from the API or labelled («допущение», «по состоянию на …»); comparison rows list verifiable capabilities only (LAND-02).
- **Languages:** RU, KK and EN for every text; the choice carries into the app. At 375 px nothing scrolls sideways and sections stack (LAND-01).

| Figma frame or part | CQA section |
|---|---|
| Menu-bar navigation, hero widget, product stage, keycaps | 3.2 Header, 3.3 Hero (product stage = live quality card; keycaps dropped) |
| «The problem» heading and the chapter index pill 01–08 | 3.5 Problem (the index pill becomes section chips in the header) |
| Chapters 01–03: problems with outlined numerals | 3.5 three problem cards with outlined numerals 01–03 |
| Chapter 04: steps 1–3 | 3.6 How it works, five steps |
| Chapters 05–06: product window plus bullets | 3.7 Capabilities: two feature rows and four tiles |
| new, from LAND-03 | 3.8 Audience |
| Chapter 07: languages, conversion chips, multilingual marquee | 3.9 Languages and channels |
| Chapter 08 «It keeps quiet»: privacy bullets, floating chips | 3.10 Security, local model, models and cost |
| new, from LAND-02 | 3.11 Dynamics 365 |
| Compare table: «By hand» vs the product pill | 3.12 Manual QA and CQA |
| FAQ accordion | 3.13 FAQ |
| Final CTA panel | 3.14 Final call to action |
| Footer: logo, columns, copyright | 3.15 Footer with verification links and build info |

### 3.2 Header

- Sticky, 64 px. Over the hero: transparent, white text. After 80 px of scroll: white with a 1 px #E3E8E3 bottom line and dark text.
- Left: wordmark «CQA» (22/28, 800) with «Freedom Broker» (13/16, 500) beside it.
- Centre links (scroll to sections; the hash router is not touched): RU «Как это работает · Возможности · Для кого · Безопасность · Dynamics 365 · Вопросы» / EN «How it works · Features · Who it is for · Security · Dynamics 365 · FAQ».
- Right: language switch RU · KK · EN; «Сценарии для жюри» (ghost; EN «Jury scenarios») → opens the scenarios panel; primary «Открыть рабочее место» (EN «Open the workspace») → `#/start`, or «Продолжить как QA-аналитик» for a returning visitor → that role's home.
- Below 1024 px the centre links move into a menu sheet.

### 3.3 Hero (deep-green band)

- Background: #0F5431 with a soft radial highlight of #1F7028 at 20 % opacity behind the product card and a fade to #092818 at the bottom edge. No swirls, no glass, no pattern.
- Eyebrow (13/16, 600, uppercase, tracking 0.06em, #BEE3B5): «Conversation Quality Analytics · Freedom Broker».
- Headline (display 56/60, 700, white), 7 words:
  - RU: «Каждый диалог оценён по чек-листу — с доказательствами»
  - EN: «Every conversation scored against your checklist, with evidence»
  - Alternative RU (A/B in rehearsal): «Оценка качества каждого диалога: баллы, причина и реплика»
- Subline (19/28, rgba(255,255,255,.86)):
  - RU: «CQA проверяет чаты и звонки на русском, казахском и английском по чек-листу компании. По каждому критерию — ответ, баллы, объяснение и реплика-основание. Оператор оспаривает оценку, ревьюер принимает решение, итог пересчитывается автоматически.»
  - EN: «CQA reviews chats and calls in Russian, Kazakh and English against the company checklist. Every criterion gets an answer, points, a justification and the exact turn behind it. The operator disputes a score, a reviewer decides, and the total is recalculated automatically.»
- Buttons: «Открыть рабочее место» (white fill, green-800 text) → `#/start`; «Как это работает» (outline white) → scroll to 3.6. EN «Open the workspace», «How it works».
- Note under the buttons (13/18, rgba(255,255,255,.72)): RU «Без регистрации · демо на данных организаторов (DEV) · модель Claude Opus 5.5»; EN «No sign-up · demo on the organizers' DEV data · model Claude Opus 5.5».
- **Product stage (right column, 560 px):** a white `QualityCardPreview` (radius 16, shadow-3) for p1-ru-005, built from the design-system atoms: header «Анализ разговора · Чат · p1-ru-005 · Русский»; ring «78,46 / 100»; eight criterion lines with points (Установление контакта 15,38 / 15,38; Выявление потребности N/A; Идентификация N/A; Консультация 23,08 / 30,77; Эмпатия 23,08 / 23,08; Ведение диалога 9,23 / 23,08; Завершение 7,69 / 7,69; Грубые ошибки — нет); «Почему снижена оценка: Ведение диалога −13,85» with the first sentence of the stored explanation; the two mockup buttons, inert. Behind it, offset up and left by 32 px, a narrower `TranscriptPreview` with three rows, the evidence turn №12 in peach. Data: `GET /api/conversations/p1-ru-005/evaluation` and `/api/conversations/p1-ru-005`; fixture snapshot when offline.
- Chips under the stage (white 12 % fill, white text): «RU · KZ · EN», «Чаты и звонки», «Апелляция и решение».
- 1280×720: headline, subline, both buttons and the card are above the fold. 375 px: text first, the card scaled to the width below it, the transcript card hidden.

### 3.4 Numbers strip (white, directly under the hero)

Five stat cells in one row (two rows on tablets, a list on phones), numbers 32/38 700 tabular, labels 13/18 #5B675F:

| Cell RU | Cell EN | Source | Fallback |
|---|---|---|---|
| «65» «диалогов оценено» · «60 чатов · 5 звонков» | «65» «conversations evaluated» · «60 chats · 5 calls» | `GET /api/analytics/summary` → `count`, `by_channel[].n` | 65 · 60 / 5 |
| «RU 35 · KZ 15 · EN 15» «три языка» | «RU 35 · KZ 15 · EN 15» «three languages» | same → `by_language[].n` | as shown |
| «8» «грубых нарушений найдено» · «оскорбление 1 · платная услуга 4 · раскрытие 3» | «8» «gross violations found» · «insult 1 · paid service 4 · disclosure 3» | `GET /api/violations` → `total`, `counts` | as shown |
| «82,04» «средний итог из 100» · «медиана 95,00» | «82.04» «mean score out of 100» · «median 95.00» | `/api/analytics/summary` → `mean_total`, `median_total` | as shown |
| «≈ $0,22» «за полную оценку диалога» · «все вызовы модели включены» | «≈ $0.22» «per full evaluation» · «all model calls included» | `GET /api/overview` → `cost.per_dialog_usd` once deployed, else the n-weighted `providers[].mean_cost_usd` | ≈ $0,22 |

Stamp under the strip: «По данным API на 20:41 (UTC+5)» or, offline, «По состоянию на 07.11.2026 · API недоступен». Requests run with `Promise.allSettled`, a 4 s timeout and a 5-minute cache; a failed call shows its fallback with the stamp, never 0 (LAND-01, LAND-02).

### 3.5 Problem («Проблема», #F5F7F5)

- Eyebrow «Проблема» / «The problem». H2 (40/44, 700): RU «Ручной контроль видит малую часть разговоров»; EN «Manual QA sees a small share of conversations».
- Three cards (Figma Problem Card, made white with a 1 px border; outlined numerals 01–03 in #E3E8E3, 72 px, behind the title):
  1. RU «Выборка вместо полной картины» — «Специалист успевает прочитать лишь часть обращений. Остальные диалоги остаются без оценки, а выводы строятся на малой выборке.» Label «допущение: ручной контроль обычно охватывает несколько процентов диалогов». EN «A sample instead of the full picture» — «A specialist reads only part of the conversations. The rest are never scored, and conclusions rest on a small sample.»
  2. RU «Спор без фактов» — «Без цитаты и ссылки на реплику апелляция превращается в спор мнений, а решение трудно объяснить оператору.» EN «Disputes without facts» — «Without a quote and a link to the exact turn, an appeal becomes opinion against opinion and the decision is hard to explain.»
  3. RU «Риски видны поздно» — «Оскорбление клиента, платная консультация в обход компании или раскрытие данных без идентификации обнаруживаются только после жалобы.» EN «Risks surface late» — «An insult, a paid consultation bypassing the company or data disclosed without identification is found only after a complaint.»

### 3.6 How it works («Как это работает», white)

- H2 RU «Пять шагов от импорта до решения»; EN «Five steps from import to decision».
- A horizontal stepper (vertical on phones), each step with a small live crop:
  1. «Импорт» — «Набор по group_id из API организаторов, файл или вставленный текст. Чаты и звонки, RU / KZ / EN.» Crop: import preview «65 записей · RU 35 / KZ 15 / EN 15». EN «Import» — «A set by group_id from the organizers' API, a file or pasted text. Chats and calls, RU / KZ / EN.»
  2. «Оценка по чек-листу» — «Каждый критерий оценивается отдельно. Бот и системные события — контекст, оценивается оператор.» Crop: three criterion lines. EN «Checklist evaluation» — «Each criterion is judged separately. Bot turns and system events are context; the operator is evaluated.»
  3. «Обоснование и реплика» — «Ответ, баллы, объяснение и номер реплики. Ссылка ведёт к сообщению или к фрагменту записи.» Crop: evidence chip and the peach turn. EN «Justification and turn» — «Answer, points, explanation and turn number. The link opens the message or the recording fragment.»
  4. «Апелляция» — «Оператор выбирает критерий, пишет комментарий и отправляет оценку на проверку.» Crop: the appeal form. EN «Appeal» — «The operator picks a criterion, writes a comment and sends the score for review.»
  5. «Решение» — «Ревьюер подтверждает или меняет ответ с причиной. Итог пересчитывается по правилам чек-листа.» Crop: «78,46 → 85,38». EN «Decision» — «A reviewer confirms or changes the answer with a reason. The total is recalculated by the checklist rules.»
- Link button «Открыть пример: p1-ru-029» → `#/conversations/p1-ru-029?role=analyst&criterion=C8&turn=10`.

### 3.7 Capabilities («Возможности», #F5F7F5)

- H2 RU «Всё, что нужно отделу качества»; EN «Everything a QA team needs».
- Two feature rows (Figma chapters 05–06: text with bullets on one side, a product window on the other):
  - «Карточка диалога, которой можно верить» / «A conversation card you can trust». Bullets (Figma Bullet): «Почему снижена оценка — в одну строку» · «Переход к реплике или фрагменту записи» · «N/A с причиной и перераспределением веса» · «Как посчитан итог — для любого диалога». Visual: card crop of p1-ru-029 with the gross-error block.
  - «Чек-лист — это продукт, а не файл» / «The checklist is a product, not a file». Bullets: «Добавить, изменить или заменить критерий без кода» · «Веса с суммой и «Нормализовать до 100»» · «Версии, сравнение, базовая версия организаторов под защитой» · «Применение к выбранным диалогам: переоценивается только изменённое». Visual: Studio crop with the weights panel.
- Four tiles (white, radius 16), each with a live crop and a link that opens the screen in the right role:
  - «Звонки с двумя дорожками» — waveform crop of p4-ru-002 → `#/conversations/p4-ru-002?role=analyst`.
  - «Реестр грубых нарушений» — one register row → `#/violations?role=lead`.
  - «Апелляция и решение» — queue card crop → `#/review?role=reviewer`.
  - «Аналитика и стоимость» — KPI tile crop → `#/overview?role=director`.

### 3.8 Audience («Для кого», white)

- H2 RU «Одна система для всей службы качества»; EN «One system for the whole quality team».
- Four cards (LAND-03), each with «Открыть как …»:
  - «Директор» — «Индекс качества, риски и стоимость оценки на одном экране.» → `#/overview?role=director`.
  - «Руководитель QA» — «Операторы, коучинг, нарушения и апелляции команды.» → `#/overview?role=lead`.
  - «QA-специалист» — «Импорт, оценка, проверка и решения по апелляциям.» → `#/conversations?role=analyst`, with a second link «или как ревьюер» → `#/review?role=reviewer`.
  - «Оператор» — «Мои оценки, причины снижения и апелляция по критерию.» → `#/start?role=operator` (opens «Кто вы?»).
- A line below: «Также: Методолог (чек-листы и критерии) · Администратор (настройки и интеграции) · Гость (только просмотр)». The card of the remembered role shows «Продолжить как …».

### 3.9 Languages and channels («Языки и каналы», #F5F7F5)

- H2 RU «Три языка и два канала»; EN «Three languages, two channels».
- Three language cards (Figma Language Card): «Русский · 35 диалогов», «Қазақша · 15 диалогов», «English · 15 диалогов», each with «Пример: p1-ru-001 / p1-kz-001 / p1-en-001» linking to a card. Counts are live from `by_language`.
- Two channel cards: «Чаты — время MM:SS; бот и системные события — контекст» and «Звонки — расшифровка с отметками времени, запись с двумя дорожками «Клиент / Оператор», переход к фрагменту».
- Note: RU «Цитаты остаются на языке диалога; интерфейс — на русском, казахском или английском.» EN «Quotes stay in the conversation's language; the interface speaks Russian, Kazakh or English.»
- The kit's multilingual marquee becomes a static row of three status chips: «Оценено · Бағаланды · Evaluated».

### 3.10 Security, local model, models and cost («Безопасность», white)

- Eyebrow «Безопасность» / «Security». H2 RU «Данные клиента под контролем»; EN «Client data stays under control».
- Bullets (Figma chapter 08 pattern, each true today or labelled):
  - «Перед отправкой в облачную модель маскируются телефоны, ИИН, номера карт, e-mail и IBAN.» / «Phones, IINs, card numbers, e-mails and IBANs are masked before text reaches a cloud model.»
  - «Локальная модель (OpenAI-совместимый сервер: vLLM, Ollama) — данные не покидают контур компании.» / «A local model (OpenAI-compatible server: vLLM, Ollama) keeps data inside the company network.» The live state comes from `GET /api/health`; when it is not connected the line reads «Локальная модель — опция, на демо-стенде не подключена» in neutral grey.
  - «Каждое действие записывается в журнал: кто подал апелляцию, кто принял решение, кто изменил чек-лист.» / «Every action is logged: who filed an appeal, who decided, who changed the checklist.»
  - «На демо-стенде нет входа; в Dynamics 365 пользователь и права берутся из учётной записи организации.» / «The demo has no sign-in; inside Dynamics 365 the user and rights come from the organization account.»
- Visual: a «Что увидела модель» card (one turn with «[PHONE_1]» next to the original with a PII marker) and three chips «ПДн маскируются», «Локальная модель — опция», «Журнал действий».
- «Модели и стоимость» card (LAND-02, EXP-03): «Судья: Claude Opus 5.5 · в ансамбле: Claude Sonnet 5.5 · локальная модель — по выбору · results.json: mode external_api» and «≈ $0,21 за чат и ≈ $0,31 за звонок, включая все вызовы модели (измерено на DEV, n = 65)». EN «Judge: Claude Opus 5.5 · ensemble member: Claude Sonnet 5.5 · local model optional · results.json mode external_api» and «≈ $0.21 per chat and ≈ $0.31 per call, all model calls included (measured on DEV, n = 65)». Values come from the API (EXP-03 statement when available); the fallback carries its date.

### 3.11 Dynamics 365 («Встраивается в Dynamics 365», #F5F7F5)

- H2 RU «Оценка там, где уже работает контакт-центр»; EN «Quality results inside Dynamics 365».
- Bullets:
  - «Карточка оценки — вкладка формы «Разговор»: один HTML-файл web resource (≈ 1,7 МБ).» / «The evaluation card is a tab on the Conversation form: one HTML web resource (≈ 1.7 MB).»
  - «Power Automate передаёт расшифровку после закрытия разговора; оценка запускается автоматически.» / «Power Automate sends the transcript when the conversation closes; the evaluation starts automatically.»
  - «Оператор оспаривает оценку прямо во вкладке, если форма передаёт пользователя.» / «The operator disputes a score inside the tab when the form passes the user.»
- Visual: a neutral frame labelled «Пример встраивания: вкладка формы» containing a scaled iframe of `index.html?embed=1&conversation=p1-ru-029&host=d365` (no Microsoft logos, no imitation of the D365 chrome).
- Link «Руководство для администратора» → `docs/INTEGRATION_D365.md` in the public repository.

### 3.12 Manual QA and CQA («Сравнение», white)

- H2 RU «Ручной контроль и CQA»; EN «Manual QA and CQA». Figma Compare Row table on a white card, the CQA column header as a green pill.

| Row RU (EN) | Вручную | CQA |
|---|---|---|
| Какие диалоги проверяются (Which conversations are checked) | выборка | каждый импортированный |
| Обоснование оценки (Justification) | комментарий проверяющего | объяснение и номер реплики по каждому критерию |
| Переход к фрагменту записи (Jump to the recording) | поиск вручную | один клик |
| N/A и веса (N/A and weights) | пересчёт в таблице | по правилам чек-листа, автоматически |
| Изменение критерия (Changing a criterion) | пересчитать всё заново | новая версия, переоценивается только изменённое |
| Апелляция (Appeal) | письмо или чат | по критерию, с решением и пересчётом итога |
| Языки (Languages) | зависит от специалиста | RU · KZ · EN |
| Стоимость оценки диалога (Cost per conversation) | время специалиста | ≈ $0,22, измерено |

### 3.13 FAQ («Вопросы», #F5F7F5)

Accordion (Figma FAQ Item open and closed), one open at a time, the first open by default:

1. «Откуда берутся баллы?» — «Модель отвечает по каждому критерию отдельно (Да, Нет, шаг шкалы или N/A) и приводит реплики-основания. Баллы и итог считает не модель, а движок по правилам чек-листа: вес, шкала, N/A, округление.» (EN «Where do the points come from?»)
2. «Что значит N/A?» — «Критерий неприменим к этому диалогу, например идентификация не требовалась. Его вес перераспределяется на остальные критерии, максимум остаётся 100.» («What does N/A mean?»)
3. «Почему грубая ошибка не обнуляет итог?» — «Так требует чек-лист: грубая ошибка отмечается отдельно. Диалог попадает в реестр нарушений с типом и цитатой, а итог остаётся оценкой остальных критериев.» («Why does a gross error not zero the total?»)
4. «Как оспорить оценку?» — «Оператор открывает свой диалог, нажимает «Оспорить оценку» у критерия и пишет комментарий. Ревьюер видит спорный балл, комментарий и фрагмент, подтверждает или меняет ответ; итог пересчитывается.» («How do I dispute a score?»)
5. «Как изменить чек-лист?» — «В разделе «Чек-листы»: добавить или заменить критерий, изменить формулировку или вес, сохранить новую версию и применить её к выбранным диалогам. Переоцениваются только изменённые критерии.» («How do I change the checklist?»)
6. «Уходят ли данные в облако?» — «С облачной моделью — да, после маскирования персональных данных. Локальная модель работает в контуре компании без передачи данных.» («Does data leave the company?»)
7. «Работает ли со звонками?» — «Да: расшифровка, запись с дорожками «Клиент» и «Оператор», переход от обоснования к фрагменту записи. Распознавание речи выполняет источник данных.» («Does it work with calls?»)
8. «Сколько стоит оценка?» — «≈ $0,21 за чат и ≈ $0,31 за звонок с Claude Opus 5.5, включая все вызовы (измерено на DEV). Локальная модель — $0 без учёта оборудования.» («What does an evaluation cost?»)
9. «Нужна ли регистрация?» — «Нет. Роль выбирается в интерфейсе; в Dynamics 365 пользователь берётся из учётной записи.» («Do I need an account?»)

### 3.14 Final call to action (deep-green panel)

- Figma final CTA panel without characters: radius 24, #0F5431 with a soft #1F7028 radial glow at 25 %, white text, centred.
- H2 RU «Проверьте любой диалог за минуту»; EN «Check any conversation in a minute».
- Text RU «Откройте рабочее место, выберите роль и пройдите путь от оценки до решения по апелляции.»; EN «Open the workspace, pick a role and walk from the score to the appeal decision.»
- Buttons «Открыть рабочее место» (white) → `#/start`; «Сценарии для жюри» (outline white) → scenarios panel.
- Caption «Без регистрации · данные DEV организаторов · RU / KK / EN».

### 3.15 Footer (#F5F7F5, top border)

- Left: «CQA» wordmark, «Conversation Quality Analytics для Freedom Broker», «Команда «Я Тебя Могну» · хакатон Freedom Broker, 2026».
- Columns: «Продукт» (Как это работает, Возможности, Безопасность, Dynamics 365) · «Рабочие места» (Обзор, Диалоги, Апелляции, Чек-листы) · «Для проверки» (Репозиторий, README, API · Swagger `/docs`, `results/dev/results.json`, `results/prod/results.json` «появится после контрольного этапа» until it exists) · «О системе» («Веб <commit> · API <commit> · сборка <дата>», and the `stop-code` tag once pushed, DEMO-16).
- Bottom line: «Демонстрационный прототип · данные: набор DEV организаторов хакатона».

### 3.16 Build notes

- Route `#/` is its own lazy chunk; it reuses the design-system atoms (no extra charting library); no bitmap images except `web/public/og-card.png` (1200 × 630, the light card, SHELL-15).
- Section scrolling uses `scrollIntoView` from buttons; the hash route stays `#/` (with `?section=how` support for links from slides).
- The single-file D365 build does not need the landing; it is excluded from the embed entry (§4.8).

---

## 4. Design system in code

### 4.1 Principles

- **Corporate light is the default**; the organizer mockup is the visual reference, the Figma Design Book (`0:1`, «03 Color», «04 Type») gives the green ramp and the type rhythm (SHELL-01, SHELL-03).
- **One primary action per view** (Figma Button rule); secondary for the rest; ghost inside rows and toolbars.
- **Bright greens never carry text.** Green text and the primary fill use green-700 #1F7028 (white on it 6,2:1); #2F8F2A and #51AF3D are for rings, icons, bars and charts only.
- **Red means a gross violation, a failure or a destructive action** and nothing else.
- **States never rely on colour alone:** every Да / Нет / N/A, status and heatmap cell has text and an icon (SHELL-09).
- **No mascot, no glow, no glass.** Empty states use a neutral icon tile (SHELL-02).
- **Numbers are tabular** and formatted with `Intl.NumberFormat`: «51,25» in RU and KK, «51.25» in EN; trailing zeros trimmed in rings («85,5 / 100»), two decimals in tables.

### 4.2 Tokens

Stored as CSS custom properties in `web/src/styles/tokens.css`; colours used with Tailwind opacity modifiers are also exposed as RGB channels (the current pattern in `web/src/index.css`).

```css
:root {
  /* Brand ramp, Figma Design Book «03 Color» */
  --green-50: #F0F8ED; --green-100: #DFF1DC; --green-200: #BEE3B5; --green-300: #8ED47F; --green-400: #68C155;
  --green-500: #51AF3D; --green-600: #2F8F2A; --green-700: #1F7028; --green-800: #0F5431; --green-900: #092818;

  /* Neutrals: green-tinted greys of the mockup */
  --page: #F5F7F5;            /* page background */
  --surface: #FFFFFF;          /* cards, dialogs, tables */
  --surface-muted: #F7F9F7;    /* table header, row hover, muted cards */
  --surface-sunken: #EEF2EE;   /* tracks, N/A tiles */
  --line: #E3E8E3;             /* card borders and dividers (decorative) */
  --line-strong: #C5CEC7;      /* input borders */
  --line-control: #8A958D;     /* input bottom edge, checkbox outline: 3,1:1 on white (WCAG 1.4.11) */
  --ink: #16201A;              /* headings and body */
  --ink-2: #5B675F;            /* secondary text: 5,9:1 on white, 5,5:1 on the page */
  --ink-3: #6B766E;            /* timestamps and meta, on white only: 4,7:1 */
  --ink-inverse: #FFFFFF;

  /* Brand roles */
  --primary: #1F7028;          /* primary fill, links, green text */
  --primary-hover: #185D21;
  --primary-press: #0F5431;
  --accent-graphic: #2F8F2A;   /* ring arc, progress, play button, check icons */
  --accent-bright: #51AF3D;    /* operator waveform, chart fills; never text */
  --accent-tint: #F0F8ED;      /* selected rows, success chips */

  /* Sidebar (mockup) */
  --nav-bg: #0F5431;
  --nav-active: #1F7028;
  --nav-hover: rgba(255, 255, 255, .08);
  --nav-text: rgba(255, 255, 255, .80);
  --nav-text-active: #FFFFFF;
  --nav-divider: rgba(255, 255, 255, .12);

  /* Status: background, foreground (text), icon */
  --success-bg: #F0F8ED;  --success-fg: #1F7028;  --success-icon: #2F8F2A;
  --warning-bg: #FDF2DD;  --warning-fg: #9A5C06;  --warning-icon: #C27A0E;
  --critical-bg: #FDECEA; --critical-fg: #B3362F; --critical-icon: #C93C33;
  --info-bg: #EAF2FB;     --info-fg: #1F5FA8;
  --na-bg: #EEF2EE;       --na-fg: #5B675F;

  /* Evidence highlight (mockup peach) */
  --evidence-bg: #FDEBD3;
  --evidence-border: #F2B66D;

  /* Score bands (checklist default 85 / 60, CHK-28) */
  --band-good: #2F8F2A; --band-mid: #D08A1C; --band-low: #C93C33; --ring-track: #E9EEE9;

  /* Waveform (mockup) */
  --wave-client: #0F5431; --wave-operator: #51AF3D; --wave-other: #C5CEC7;

  /* Focus, overlay, elevation */
  --focus: #1F7028;            /* 2 px ring with a 2 px white offset */
  --overlay: rgba(10, 10, 10, .40);
  --shadow-1: 0 1px 2px rgba(16, 32, 22, .06);
  --shadow-2: 0 4px 12px rgba(16, 32, 22, .08);
  --shadow-3: 0 12px 32px rgba(16, 32, 22, .14);
}

/* Optional dark theme: deep green-black. Chosen in Settings, never the default; print and embed stay light. */
.dark {
  --page: #0B120E; --surface: #111A15; --surface-muted: #16201A; --surface-sunken: #0E1611;
  --line: #243129; --line-strong: #34443A; --line-control: #6E7C72;
  --ink: #E7EDE8; --ink-2: #A9B5AD; --ink-3: #93A097;
  --primary: #1F7028; --primary-hover: #25802F; --primary-press: #2F8F2A;   /* white text keeps 6,2:1 */
  --accent-graphic: #51AF3D; --accent-bright: #68C155; --accent-tint: rgba(81, 175, 61, .14);
  --nav-bg: #07160D; --nav-active: #1F7028;
  --success-bg: rgba(81, 175, 61, .14);  --success-fg: #8ED47F; --success-icon: #68C155;
  --warning-bg: rgba(226, 167, 74, .14); --warning-fg: #E8B86A; --warning-icon: #E2A74A;
  --critical-bg: rgba(226, 103, 95, .14); --critical-fg: #F08A82; --critical-icon: #E2675F;
  --info-bg: rgba(95, 168, 211, .14);    --info-fg: #8CC2E6;
  --na-bg: #16201A; --na-fg: #A9B5AD;
  --evidence-bg: rgba(242, 182, 109, .18); --evidence-border: #C98A3E;
  --band-good: #51AF3D; --band-mid: #E2A74A; --band-low: #E2675F; --ring-track: #1E2A23;
  --wave-client: #DFF1DC; --wave-operator: #51AF3D; --wave-other: #34443A;
  --focus: #8ED47F; --overlay: rgba(0, 0, 0, .55);
  --shadow-1: none; --shadow-2: 0 4px 12px rgba(0, 0, 0, .35); --shadow-3: 0 12px 32px rgba(0, 0, 0, .50);
}
```

Tailwind names (`web/tailwind.config.ts`): `bg-page`, `bg-surface`, `bg-surface-muted`, `bg-surface-sunken`, `border-line`, `border-line-strong`, `border-line-control`, `text-ink`, `text-ink-2`, `text-ink-3`, `bg-primary`, `hover:bg-primary-hover`, `text-primary`, `bg-nav`, `bg-nav-active`, `text-on-nav`, `bg-evidence`, `border-evidence`, `{bg,text}-{success,warning,critical,info,na}-{bg,fg}`, `stroke-band-{good,mid,low}`, `fill-wave-{client,operator,other}`, radii `rounded-chip` (6), `rounded-control` (10), `rounded-card` (16), shadows `shadow-1..3`. The existing `shadow-float`, `shadow-glow`, `.glow` and the green `.turn-highlight` glow are removed.

### 4.3 Typography

Font stack (SF Pro on Apple, Segoe UI on Windows and inside Dynamics 365, Inter elsewhere):
`-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable Text", "Segoe UI", Inter, Roboto, "Helvetica Neue", Arial, sans-serif`; display: the same with `"SF Pro Display"` and `"Segoe UI Variable Display"`; mono (IDs, JSON, request ids only): `"SF Mono", ui-monospace, "Cascadia Mono", Consolas, monospace`. Inter is self-hosted as an optional latin + cyrillic subset (about 100 KB) for Linux projectors and excluded from the single-file build. All of them cover the Kazakh letters ә ғ қ ң ө ұ ү һ і.

| Token | Size / line | Weight | Tracking | Use | Figma source |
|---|---|---|---|---|---|
| `display` | 56/60 (40/44 below 768 px) | 700 | −0,02em | landing hero only | Display/L 72 scaled for a corporate tone |
| `landing-h2` | 40/44 (30/36) | 700 | −0,02em | landing section titles | Heading/H1 48 |
| `h1` | 32/38 (28/34 at 1024–1279 px) | 700 | −0,015em | page title «Анализ разговора» | mockup |
| `h2` | 22/28 | 700 | −0,01em | card title «Расшифровка», «Оценка качества» | Heading/H2 28 |
| `h3` | 17/24 | 600 | 0 | block title «Почему снижена оценка» | Heading/H3 19 |
| `body-lg` | 16/24 | 400 | 0 | transcript text, criterion titles | Body/M rounded up for reading |
| `body` | 15/22 | 400 | 0 | default | Body/M |
| `small` | 13/18 | 400 | 0 | meta, hints, timestamps | — |
| `label` | 12/16 | 600 | 0,01em | field labels, table headers, sentence case | Label/Eyebrow |
| `eyebrow` | 13/16 | 600 | 0,06em uppercase | landing only | Label/Eyebrow |
| `score-xl` | 40/44 | 700 | −0,02em, tabular | ring value «85,5» | mockup |
| `score` | 16/24 | 600 | tabular | points «15 / 20» | mockup |
| `mono` | 13/18 | 500 | 0 | IDs, JSON | Mono/S |

### 4.4 Spacing, radius, elevation, motion, breakpoints

- Spacing scale 4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 48 · 64 · 96. Page padding 32 px (24 px at 1024–1279 px, 16 px on phones); gap between cards 24 px; card padding 24 px (20 px compact).
- Radius: chips 6; inputs and buttons 10; cards and dialogs 16; pills 999.
- Elevation: cards `shadow-1` plus the 1 px border; hover on clickable cards `shadow-2`; popovers and the landing product stage `shadow-3`; no blur on overlays.
- Motion: 120 ms ease-out for hover and press, 180 ms for drawers and dialogs, 400 ms for the ring fill; `prefers-reduced-motion` makes jumps and fills instant.
- Breakpoints: 360 (phone), 768 (tablet), 1024 (small laptop), 1280 (projector; the sidebar collapses to icons below it), 1440 (desktop).
- Z-index: sticky headers 10, sidebar 20, top bar 30, dropdowns 40, drawers 50, dialogs 60, toasts 70.

### 4.5 Component inventory

Owners refer to the work packages of §7. «Atom» = implemented in the foundation (W0a) and used everywhere.

**Shell and layout**

| Component | File | Props and variants | Stories |
|---|---|---|---|
| `AppShell` | `components/layout/AppShell.tsx` | reads route and session; renders Sidebar, TopBar, global banners and the page; variants: default, mobile (top bar plus menu sheet below 768 px); none in embed | SHELL-01, SHELL-10, SHELL-16 |
| `Sidebar` | `components/layout/Sidebar.tsx` | `items: NavItem[]` (`{key, to, icon, label, badge?: {count, tone: 'neutral' or 'critical'}}`), `collapsed`, `onToggle`; widths 232 / 72 px; deep green per the mockup | SHELL-01, SHELL-04 |
| `TopBar` | `components/layout/TopBar.tsx` | slots for ScenariosButton, JobIndicator, NotificationsBell, LanguageSwitch, RoleSwitcher, UserMenu | SHELL-04, SHELL-12, SYS-03 |
| `PageHeader` | `components/layout/PageHeader.tsx` | `title`, `meta: ReactNode[]` (joined by « · »), `back?: {label, to}`, `chips?`, `actions?` | CARD-01 |
| `Card` | `components/ui/card.tsx` | `title?`, `subtitle?`, `actions?`, `padding: 'none', 'md' or 'lg'`, `tone: 'default', 'muted', 'critical' or 'warning'`, `interactive?` | SHELL-03 |
| `Banner` | `components/states/Banner.tsx` | `tone`, `icon?`, `title`, children, `actions?`, `dismissible?`; used for the gross-error block, the C1 call rule, offline, mock, frozen configuration, control window, budget | GV-03, CALL-09, SHELL-07 |
| `EmptyState` | `components/states/EmptyState.tsx` | `icon` (lucide tile on #F0F8ED, 48 px), `title`, `hint?`, `actions?`, `steps?: string[]`; never a mascot | SHELL-02, SHELL-07 |
| `ErrorState` | `components/states/ErrorState.tsx` | `error` (shows the API message, code and request id), `onRetry?` | SHELL-07, SYS-08 |
| `Skeleton*`, `NotFound`, `OfflineBanner` | `components/states/*` | skeleton blocks per page; «Сервер просыпается…» after 3 s | SHELL-07 |
| `Logo` | `components/brand/Logo.tsx` | `variant: 'onDark' or 'onLight'`, `compact?`; text wordmark «CQA», no image | SHELL-02 |
| `BrandMascot` | `components/brand/BrandMascot.tsx` | `pose`, `size`; renders only when `settings.mascot_enabled`; lazy `import.meta.glob`; compiled out when `VITE_MASCOT=0` (single-file build) | SHELL-02, D365-02 |

**Domain atoms (W0a) and shared domain components (stubs by W0a, owned by W2)**

| Component | Props and variants | Stories |
|---|---|---|
| `ScoreRing` (atom) | `value: number or null`, `original?`, `size: 'sm' (48), 'md' (96) or 'lg' (152)`, `state: 'ok', 'running', 'partial' or 'none'`, `critical?`, `caption?` («после апелляции»), `onClick?`; band colour from the checklist bands; «/ 100» under the value; aria label «Итоговая оценка 51,25 из 100, есть грубая ошибка» | CARD-01, CARD-06, SHELL-09 |
| `ScorePill` (atom) | `value`, `original?` (tooltip «исходный: 51,25»), `critical?`, `size` | CONV-10, APL-09 |
| `CriterionIcon` (atom) | `state: 'pass', 'partial', 'fail', 'na', 'critical', 'clear', 'pending' or 'error'`; 28 px rounded-square tile: green check on #F0F8ED, amber minus or cross on #FDF2DD, grey dash on #EEF2EE, red shield on #FDECEA | CARD-01 |
| `CriterionLine` (atom) | `code`, `title`, `shortTitle?`, `state`, `earned?`, `max?`, `answerLabel?`, `review?: 'none', 'under_review', 'confirmed' or 'changed'`, `selected?`, `onClick?`; the mockup row «Консультация 15 / 20» | CARD-01, LAND-01 |
| `LossSummary` (atom) | `items: {code, title, lost, reason}[]`, `naItems`, `critical?: {typeLabel}`, `max?` (3), `onJump(code)`; renders «Почему снижена оценка» | CARD-02 |
| `EvidenceChip` (atom) | `number` (1-based), `speaker`, `time?`, `quote` (≤ 90 characters), `role: 'supports', 'contradicts' or 'context'` written as text, `outOfRecording?`, `onClick` | CARD-03, CARD-04 |
| `StatusChip`, `CountBadge`, `FilterChip`, `AnswerLabel`, `KpiTile` (atoms) | tones success, warning, critical, info, na, neutral, accent; chips 24 px, radius 6; KPI with `label`, `value`, `hint?`, `help?` («Как посчитано»), `to?` | SHELL-03, ANL-02 |
| `TranscriptList` (W2) | `turns`, `evidence?: number[]`, `highlight?: {turnIdx?, span?}`, `range?` (fragment mode), `groups?` (До оператора / Сегмент оператора / После разговора), `follow?`, `currentTime?`, `compact?`, `onTurnClick?` | CARD-01, CARD-04, CARD-07, APL-06 |
| `TranscriptRow` (W2) | `turn`, `number`, `time`, `speakerLabel`, `state: 'default', 'evidence', 'span', 'active', 'dim' or 'outOfRecording'`, `maskChips?` | CARD-04, CALL-06 |
| `WaveformPlayer` (W2) | `conversationId`, `highlight?: {start, end, label?}`, `holds?`, `markers?`, `variant: 'full' or 'compact'`, `window?`, `onTime?`; imperative `seek(t, play)`, `play()`, `pause()` | CALL-01, CALL-02, APL-06 |

**Controls, overlays, data (W0a)**

| Component | Props and variants | Stories |
|---|---|---|
| `Button` | `variant: 'primary', 'secondary', 'ghost', 'danger', 'link' or 'onDark'`; `size: 'sm' (32), 'md' (40) or 'lg' (48)`; `loading?`; radius 10 | SHELL-03 |
| `Input`, `Textarea`, `Select`, `SearchInput`, `NumberStepper`, `SegmentedControl`, `Switch`, `Checkbox`, `Field` | 40 px controls (32 in toolbars); `Textarea` with `counter: {min, max}`; `SegmentedControl` is a radio group with `aria-checked`; `Field` = label, hint, error; Switch per the Figma «Switch» (capsule, accent when on) | SHELL-03, SHELL-09 |
| `Dialog`, `Drawer`, `Sheet`, `ConfirmDialog`, `Popover`, `Tooltip`, `DropdownMenu` | dialog sizes sm 440, md 560, lg 720, xl 960; drawer 480 or 640 px from the right; `ConfirmDialog` with `typedConfirm?` for restores | SHELL-03, ADM-06 |
| `toast()` | `{tone, title, description?, action?, requestId?}`; bottom right; white card with a 3 px tone bar | SYS-08 |
| `DataTable<T>` | `columns`, `rows`, `rowKey`, `sort?`, `onSort?`, `selection?`, `density: 'comfortable' (52) or 'compact' (40)`, sticky 40 px header, numbers right-aligned, `empty`, `loading`, `onRowClick?` (whole row is a link with a focus ring) | SHELL-03, CONV-10 |
| `Pagination`, `Heatmap`, `MiniBar`, `Timeline` | `Timeline` events `{kind: 'ai', 'appeal', 'decision', 'version', 'note' or 'assignment', actor, at, title, body?}` | ANL-03, APL-09 |

**Feature composites** (owned by features, listed for completeness): `QualityCard`, `CriterionDetail`, `TotalBreakdown` (W2); `AppealDialog`, `DecisionPanel`, `AppealCard` (W3); `IdentityPicker`, `RoleSwitcher`, `ScenariosPanel`, `NotificationsBell`, `HelpPanel` (W1); `ScopeBar`, `ViolationRow`, `ScorecardHeader` (W4); `ChecklistOverview`, `CriterionEditorDrawer`, `WeightPanel`, `ApplyDialog`, `VersionTimeline` (W5); `EvaluateDialog`, `ImportDialog`, `JobIndicator`, `ExportResultsDialog` (W6); `SettingsRow`, `EmbedRoot` (W7).

### 4.6 Key component specs

**Sidebar (mockup).** Background #0F5431, full height. Top: «CQA» 24/28 800 white, 24 px padding, 72 px tall. Items 44 px, radius 10, 12 px gap between icon and label, 15/22 500 at 80 % white; hover rgba(255,255,255,.08); active: #1F7028 pill with white 600 text and `aria-current="page"`. Section divider between the top and bottom groups. Collapsed: 72 px, icons centred, label in a tooltip, badge as a dot with the number in the tooltip. No glowing dot, no mascot.

**Top bar.** 56 px, background = page, no border; right-aligned cluster: «Сценарии» (ghost, demo mode only), JobIndicator «Оценка: 12 из 65 · $2,10» with a progress ring, NotificationsBell, LanguageSwitch RU · KK · EN (segmented), RoleSwitcher pill, UserMenu (Профиль, Режим презентации, Справка, О системе).

**PageHeader (mockup).** Optional back link «← Диалоги» (13/18, green-700) above the title; title 32/38 700 #16201A; meta line 15/22 #5B675F, items joined by « · » («Чат · p1-ru-029 · Русский · оператор Алексей · 14 реплик · Чек-лист v1»); chips (review state, «Тестовый») after the meta; actions on the right (one primary at most).

**ScoreRing (mockup).** 152 px in the card; 10 px stroke on the #E9EEE9 track; arc colour by band (≥ 85 green #2F8F2A, 60–85 amber #D08A1C, < 60 red #C93C33), round caps, fill animates 400 ms. Centre: value 40/44 700 #16201A with trimmed decimals («85,5», «51,25», «100»); «/ 100» 15/22 #6B766E below. States: no total → «—» and «Итог не рассчитан»; running → spinning arc and «Оценивается»; partial → dashed arc and «Неполная оценка»; reviewed → «после апелляции» caption with the AI total struck through; critical → a small red shield badge at the top right «Грубая ошибка». Click → «Как посчитан итог» drawer.

**CriterionLine (mockup).** 44 px row: CriterionIcon 28 px, title 16/24 500, points right-aligned «15 / 20» (earned in green-700 when full, amber #9A5C06 when lower, «N/A» in #5B675F; maximum in #6B766E). With N/A in the dialog the maximum is the applied weight («7,5 / 18,75») with the tooltip «вес 15 → 18,75: перераспределён вес критериев N/A». C8 shows «Да» with a red shield or «Нет» with a green check, never points. Under review: amber dot and «На рассмотрении»; changed: «0 → N/A» with the original struck through.

**LossSummary.** Heading «Почему снижена оценка» (17/24 600); a red line first when C8 = Да: «Грубая ошибка: оскорбление клиента · баллы не снимаются»; then up to three lines, largest loss first: «Выявление потребности −18,75 · <first sentence of the stored explanation, after the «Критерий — ответ.» prefix, ≤ 120 characters>»; a grey line «Не применимо: Идентификация — вес перераспределён»; «Оценка не снижалась» at 100,00. Buttons under it: primary «Перейти к фрагменту», secondary «Оспорить оценку» (white with a 1,5 px green-700 border and dark text, as in the mockup).

**TranscriptRow and evidence highlight (mockup).** Rows separated by 1 px #E3E8E3; line one: time 15/22 #6B766E, speaker 15/22 600 (Клиент #0F5431, Оператор #1F7028, Бот and Система #5B675F), «№10» 13/18 #6B766E on the right; line two: text 16/24. Evidence state: the whole row on #FDEBD3 with a 1 px #F2B66D border, radius 10, kept until another selection; span state: the same peach on every turn of the checked range with «Проверенный фрагмент №10–№11». Bot and system turns are dimmed and grouped «До оператора / Сегмент оператора / После разговора»; masked data renders as chips «Имя», «ПДн», «Идент. данные».

**WaveformPlayer (mockup).** White card, radius 16, padding 24, full width above the two cards. Two lanes 56 px tall with labels «Клиент» and «Оператор» (15/22 600, 96 px label column); bars 2 px wide with 2 px gaps and round caps, client #0F5431, operator #51AF3D; an optional muted third lane «IVR и система» 24 px tall, hidden when empty. Evidence fragment: peach #FDEBD3 band across both lanes with #F2B66D 1 px edges and a label chip «№15 · Ведение диалога». Holds: hatched grey band «Удержание 7:06». Controls row: 44 px round play button #2F8F2A with a white triangle; time «02:18 / 06:33» 15/22 tabular; progress track 4 px #E3E8E3, played part #2F8F2A, 14 px thumb with a white ring; evidence ticks on the track; speed 0,75–2×; «Следовать за записью»; «Следующий фрагмент ]». Data: `GET /api/audio/{id}/peaks?buckets=600` (`tracks.client`, `tracks.operator`, `tracks.other`, `spans`); without peaks, lanes are drawn from transcript spans with the tooltip «Дорожки построены по разметке расшифровки»; without audio, the card shows «Запись недоступна: источник не отвечает. Оценка и расшифровка доступны.» with «Повторить». Keyboard: Space, ← / → 5 s, [ and ]; the track is a slider with `aria-valuetext` «02:18 из 06:33». Compact variant for the review queue: two 28 px lanes, ± 10 s around the fragment.

**Chips and badges.** 24 px, radius 6, 12/16 600 text with a 14 px icon, solid status tokens. Filter chips 32 px, radius 8, removable. Nav count badges 20 px pills.

**Tables.** Header 40 px on #F7F9F7, 12/16 600 sentence case, sticky; rows 52 px (40 compact); hover #F7F9F7; selected #F0F8ED; numbers right-aligned; truncated ids and names with a tooltip; a «Колонки» menu for optional columns.

**Forms.** Label above the control (13/18 600); controls 40 px, radius 10, 1 px #C5CEC7 border with a #8A958D bottom edge; focus: 2 px #1F7028 ring with a white offset; errors in #B3362F with an icon under the field; textareas show a counter that turns red outside the limits.

**Dialogs and drawers.** Radius 16; header 22/28 700; footer actions right-aligned (secondary, then primary); overlay rgba(10,10,10,.40) without blur; focus trapped and returned. Drawers for the criterion editor, «Как посчитан итог» and appeal details.

**Toasts.** Bottom right, white card, 3 px left bar in the tone colour, title 15/22 600, description 13/18; errors add «код · ID r_8f2c41» with copy.

**Empty states.** A 48 px icon tile (lucide `Inbox`, `SearchX`, `AlertTriangle`) on #F0F8ED, title 17/24 600, hint 15/22 #5B675F, one primary and one secondary action, optional numbered steps («1 Импорт → 2 Оценка → 3 Карточка»). With «Фирменный персонаж» on, the mascot may replace the icon tile here and nowhere else.

### 4.7 Refactor map of the current `web/src`

| Current file | Change |
|---|---|
| `web/index.html` | remove `class="dark"`; the inline script only adds `dark` when stored; `theme-color` #F5F7F5; favicon `/favicon.svg` (white «CQA» monogram on #0F5431); `og:image` (SHELL-01, SHELL-02, SHELL-15) |
| `web/src/index.css` | import `styles/tokens.css`; light default; delete `.glow` and the green glow `.turn-highlight`; add `.evidence` (peach); `.label` becomes sentence case; `.card` radius 16 |
| `web/tailwind.config.ts` | token colours, radii, font stacks with Segoe UI, the type scale of §4.3, soft shadows; drop `shadow-float` and `shadow-glow` |
| `web/src/store/ui.tsx` | theme default light; density and presentation mode; the role moves to `store/session.ts` with eight roles and identities |
| `components/layout/AppShell.tsx` | split into `Sidebar`, `TopBar`, `AppShell`; deep-green sidebar per the mockup; no mascot wordmark, no glowing dot; auto-collapse below 1280 px |
| `components/common.tsx` | split into atoms: `ScoreRing` (mockup ring, decimal comma, «/ 100»), `ScorePill`, `StatusChip` (from `ReviewBadge`), confidence as words («высокая · 0,95») instead of `ConfidenceBar`, `EmptyState` and `ErrorState` without a mascot, `PageHeader` 32 px, `Card` instead of `Section`, `KpiTile` instead of `Kpi`; `font-mono` removed from scores and KPIs |
| `components/Mascot.tsx` | becomes `components/brand/BrandMascot.tsx`, gated by `mascot_enabled`, lazy, out of the single-file build; its uses in 11 files are removed in W0a |
| `components/ErrorBoundary.tsx` | RU / KK / EN texts, «Перезагрузить раздел» (SHELL-07) |
| `components/ui/button.tsx` | variants per the Figma Button (primary, secondary, ghost) plus danger and onDark; heights 32 / 40 / 48; radius 10 |
| `components/ui/badge.tsx` | `StatusChip` spec: 24 px, radius 6, solid status tokens |
| `components/ui/table.tsx`, `input.tsx`, `select.tsx`, `dialog.tsx`, `tabs.tsx` | §4.6 specs; tabs scroll instead of clipping at 1024 px (SHELL-10) |
| `features/card/ConversationCard.tsx` | mockup layout (§5.4); `EvaluationPanel.tsx` → `QualityCard.tsx`; `CriterionRow.tsx` → `CriterionLine` plus `CriterionDetail`; `Transcript.tsx` → `TranscriptList` (1-based, peach, groups); `AudioPlayer.tsx` → `WaveformPlayer`; `HistoryTab.tsx` → `Timeline` with names and version numbers; `TraceTab.tsx` under «Технические детали»; `AppealDialog.tsx` moves to `features/appeals` |
| `features/review/ReviewPage.tsx` | two-pane queue and `DecisionPanel`: sends "N/A", offers N/A by `na_allowed_for_channel`, reason minimum, few-shot switch off by default |
| `features/conversations/*` | server paging and URL state; Evaluate dialog with the budget line; import preview; multi-record upload fix |
| `features/checklists/*` | overview plus governance editor (§5.10) |
| `features/analytics/AnalyticsPage.tsx` | scope bar and drill-downs; headline KPIs move to `#/overview` |
| `features/settings/SettingsPage.tsx` | tabbed settings with the Figma Settings Row pattern; read-only for non-admins |
| `features/card/EmbedPage.tsx` | becomes `features/embed/EmbedRoot.tsx` with views and D365 parameters |
| `lib/format.ts` | `Intl.NumberFormat` (comma in RU and KK), trimmed ring decimals, plurals, time zone label |
| `lib/config.ts` | D365 parameters (`data`, `id`, `userlcid`, `host`, `view`), role and user from `CQA_CONFIG` |
| `api/client.ts` | actor headers, request id capture, error envelope with `errors[]` and the request id |
| `i18n/*` | namespaces per feature, plural forms, criterion `short_title` |

### 4.8 Host density, presentation mode and print

- `data-host="d365"` on `<html>` (embed with `host=d365`): base 14 px, radii 8, card padding 16, the font stack starting with Segoe UI, one column below 768 px. The look stays CQA, the metrics match the D365 Unified Interface.
- `data-present="1"` (user menu «Режим презентации» or `?present=1`): base 112 %, tokens, cache reads and the provider line hidden (SHELL-10).
- Print and PDF: always light, no sidebar, no top bar; cards keep borders; the card prints the header, the ring, the criteria with explanations and the History (EXE-08, EXP-06).
- Single-file D365 build (`pnpm build:single`): embed entry only, `VITE_MASCOT=0`, no Inter, no landing; budget below 4,5 MB, checked in CI (D365-02).

---

## 5. Screen-by-screen changes

Each screen lists its priority, layout, roles, states and the stories it closes. Hosted reference numbers are used as acceptance values.

### 5.1 App shell and global behaviour (P0)

- **First paint light** for a new visitor; a stored dark choice is respected; print and embed always light (SHELL-01).
- **Sidebar** per §4.6 and §2.3, badges from polling (§5.16); auto-collapse below 1280 px; below 768 px a top bar with a menu sheet (SHELL-10, SHELL-16).
- **Top bar** per §4.6: «Сценарии», job indicator, notifications, language, role pill, user menu (SHELL-04, SHELL-12, SYS-03).
- **Global banners**: offline «Нет связи с сервером. Повторяем попытку…» with «Повторить» and loaded data marked «может быть устаревшим»; cold start «Сервер просыпается, это может занять до 30 с» after 3 s of skeletons; mock badge «Офлайн-демо на сохранённых данных — новые оценки недоступны»; budget at 80 % (SHELL-07, DEMO-15, ADM-03). P2: frozen configuration and control window banners (DEMO-07, DEMO-14).
- **Route guards**: a route the role may not use opens read-only or shows «Раздел доступен роли Ревьюер» with «Переключить роль» (SHELL-06). Unknown routes and ids show «Страница не найдена» / «Диалог не найден» with a way back, never a silent redirect (SHELL-07).
- **Tab titles** follow the screen: «p1-ru-029 · Анализ разговора · CQA», «Грубые нарушения (8) · CQA» (SHELL-15).
- **Keyboard**: J / K between criteria, Enter jumps to the first evidence, A opens «Оспорить оценку» where allowed, Space plays or pauses audio, `?` lists shortcuts; all off while typing except Ctrl+Enter to save a decision (SHELL-09).
- **Errors**: toasts show the API message, code and request id (SYS-08).
- **Missing configuration**: without a Claude key, an organizer URL, a local endpoint or ffmpeg the app still starts; the affected options are disabled with the reason («Ключ Claude не задан: выберите локальную модель…», «API организаторов не настроен»), never a blank screen (SYS-02).

### 5.2 Role picker and identity (P0)

- `#/start`: title «Выберите рабочее место»; eight cards in a 4 × 2 grid (one column on phones). Each card: role icon, label, one line «Что вы увидите», the home it opens, «Открыть». The remembered role carries «Продолжить». Footer note: «Демо без входа: роль определяет доступные действия и подписывает их в журнале; в Dynamics 365 пользователь берётся из учётной записи.»
- Operator → «Кто вы?» (§1.3) → `#/my/evaluations`. `?next=` returns to the requested route after the choice.
- RoleSwitcher and IdentityPicker are the same components in the top bar (§1.2).
- Acceptance: `?role=director` on a fresh browser lands on `#/overview` and survives a reload; picking Нина shows «Оператор · Нина» and her one dialog p1-ru-005 at 78,46; «мұр» finds Мұрат; «—» is never listed (SHELL-04, SHELL-05, OPR-01).

### 5.3 Conversations list and intake (P0 list, filters in the URL, export entry; P1 work queue, previews)

```
Диалоги
Набор: DEV · 65 диалогов · чек-лист v1                       [Импорт ▾] [Оценить выбранные (3)] [Экспорт ▾]
┌ Сегодня: Не оценены 0 · Ошибки оценки 0 · Грубые нарушения 8 · Требуют проверки N · Решения по моим апелляциям 0 ┐
[Поиск по ID, оператору, тексту]  Набор ▾  Язык ▾  Канал ▾  Статус ▾  Итог ▾  Критерий и ответ ▾  Оператор ▾  [Колонки] [Ссылка]
 □ ID           Канал и язык   Оператор   Итог      Грубое нарушение          Апелляция       Чек-лист
 □ p1-kz-015    Чат · KZ       Мұрат      24,71     Раскрытие информации      —               v1
 □ p1-ru-029    Чат · RU       Алексей    51,25     Оскорбление клиента       На рассмотрении v1
 ...                                                                1–50 из 65   [Показать ещё]
```

- Default columns at 1024 px: ID, Канал и язык, Оператор, Итог (pill; tooltip «исходный: …» after a decision), Грубое нарушение (type chip, not a bare «C8»), Апелляция или проверка, Чек-лист (version number). Optional under «Колонки»: реплик, 1-й ответ, модель, стоимость, дата оценки (SHELL-10, CHK-19, GV-03).
- Filters in the hash query (`language`, `channel`, `status`, `critical`, `dataset_id`, `operator`, `criterion` + `answer`, `sort`); «Скопировать ссылку»; Back from a card restores filters and scroll (SHELL-13, CONV-10). The search keeps transcript matches the API returns and shows the snippet; Enter on an exact id opens the card (CONV-10).
- Server paging (50) and sorting; «Выбрать все N по фильтру» feeds the Evaluate dialog with the scope instead of ids (SYS-09).
- Work queue tiles «Сегодня», each opening its filter (CONV-09, P1).
- Status «Ошибка оценки» rows show the reason and «Повторить для всех (N)»; a dialog whose last re-evaluation failed keeps its last valid result with «Последняя переоценка не удалась»; never 0,00 for a failure (CONV-08).
- «Импорт ▾»: «По group_id» with a preview step (counts by language, channel, new / present / changed) and readable failures with file and URL fallbacks (CONV-01..03, P1 for preview); «Вставить текст» with a parse preview, speaker mapping and language detection, then «Создать и оценить» (CONV-04); «Файл» importing every record of an organizer JSON and filtering to them (fixes «Диалог undefined создан», CONV-05); an existing id is never overwritten: «Создать как p1-ru-029-2» (CONV-06).
- Evaluate dialog: selection, checklist version («v1 · Freedom Broker · активная»), provider, full or partial, optional «Только критерии», estimate, budget line «Бюджет: потрачено $16,22 из $75 · осталось $58,78», confirmation above 20 dialogs, the 429 message inline with «Уменьшить выборку», «Sonnet», «Локальная модель» (CONV-07, ADM-03).
- «Экспорт ▾»: «CSV текущего вида» (filters respected, EXP-05) and «results.json для организаторов…» (§5.13, EXP-01); hidden for roles without access.
- P2: the per-criterion matrix toggle (CONV-11), «Тестовый» chips and archive (CONV-13), background bulk import (CONV-14).
- Acceptance: `#/conversations?language=KZ&critical=1` lists exactly p1-kz-013 (61,54) and p1-kz-015 (24,71); typing «читать умеете» finds p1-ru-029 with the snippet; `#/conversations/unknown-id` shows «Диалог не найден».

### 5.4 Conversation card (P0)

The mockup layout, for a chat at 1440 px:

```
┌ sidebar ┐  [Сценарии] [Оценка 3 из 10] [Уведомления 2] [RU KK EN] [QA-аналитик · Жанар Б. ▾]
│ CQA     │  ← Диалоги
│ Обзор   │  Анализ разговора                                          [Экспорт PDF] [Переоценить] [⋯]
│ Диалоги │  Чат · p1-ru-029 · Русский · оператор Алексей · 14 реплик · Чек-лист v1
│ Наруш.  │  ┌ [!] Грубое нарушение: оскорбление клиента ──────────────────────────────────────────────┐
│ Апелл.  │  │ №10 · 01:23 «Вы вообще читать умеете? Бот всё написал.» Итог не обнуляется: по чек-листу  │
│ Аналит. │  │ грубая ошибка отмечается отдельно.                                     [Перейти к реплике] │
│ Чек-л.  │  └─────────────────────────────────────────────────────────────────────────────────────────┘
│         │  ┌ Расшифровка ─────────────────────┐ ┌ Оценка качества ─────────────────────────────────────┐
│         │  │ [Поиск]        До оператора (9) ▸│ │   ╭───────╮   v Установление контакта   12,5 / 12,5  │
│         │  │ 01:23  Оператор             №10  │ │   │ 51,25 │   x Выявление потребности     0 / 18,75  │
│         │  │ [peach] … Вы вообще читать       │ │   │ / 100 │   – Идентификация                   N/A  │
│         │  │ [peach] умеете? Бот всё написал. │ │   ╰───────╯   v Консультация               25 / 25   │
│         │  │ …      Клиент               №11  │ │               x Эмпатия                    0 / 18,75 │
│         │  │ …      Оператор             №12  │ │               ◐ Ведение диалога          7,5 / 18,75 │
│         │  │ …                                │ │               v Завершение              6,25 / 6,25  │
│         │  │                                  │ │               ! Грубые ошибки                   Да   │
│         │  │                                  │ │ Почему снижена оценка                                 │
│         │  │                                  │ │ Грубая ошибка: оскорбление клиента · баллы не снимаются│
│         │  │                                  │ │ Выявление потребности −18,75 · …                      │
│         │  │                                  │ │ Эмпатия −18,75 · …   Ведение диалога −11,25 · …        │
│         │  │                                  │ │ [Перейти к фрагменту]  [Оспорить оценку]              │
│         │  └──────────────────────────────────┘ └──────────────────────────────────────────────────────┘
│         │  ┌ [Критерии подробно] [История] [Технические детали] ─────────────────────────────────────────┐
└─────────┘
```

Transcript lines in the wireframe are illustrative; the reference values (51,25, turn №10 at 01:23, the criterion points of p1-ru-029 with C3 N/A) are the hosted ones.

- **Header**: PageHeader «Анализ разговора» with the meta line «Чат · p1-ru-029 · Русский · оператор Алексей · 14 реплик · Чек-лист v1» (version number and checklist name, never the database id), chips for the review state; actions «Экспорт PDF», «Переоценить» (roles with `evaluations.run`), «⋯» (Скопировать ссылку, Копировать JSON, Технические детали) (CARD-01, CHK-19).
- **Gross-error block** above the cards when C8 = Да: type, quoted turn with number and time, «Итог не обнуляется: по чек-листу грубая ошибка отмечается отдельно», for the operator «Вы можете оспорить отметку»; overturned → «Отметка о грубом нарушении снята решением ревьюера» with the reason; C8 failed → «C8 не оценён». No mascot (GV-03).
- **Two cards**: «Расшифровка» (5 of 12 columns) and «Оценка качества» (7 of 12), equal height, each scrolling inside. At 1024–1279 px the same two columns at 1:1; below 1024 px tabs «Расшифровка | Оценка» (SHELL-10, SHELL-11).
- **Оценка качества**: ScoreRing (§4.6) on the left, eight CriterionLines on the right (short titles, earned / applied weight), section subtotals S1–S4 as small group labels that add up to the total (CARD-01); LossSummary and the two mockup buttons below (CARD-02). «Перейти к фрагменту» scrolls the transcript to the first contradicting evidence of the selected or worst criterion and, on calls, plays it; «Оспорить оценку» opens the appeal dialog with that criterion preselected, only for roles that may appeal (operator on own dialogs, analyst, QA lead, reviewer as a spot-check, admin).
- **Criterion detail** (click a line, or the «Критерии подробно» tab): answer, the scale anchor of the given step and the next step up («4 из 10 · Клиенту приходится переспрашивать… · следующий шаг 7: один заметный сбой»), the explanation in the checklist format with clickable «[n]» references, evidence chips (number, speaker, time, quote, role in words), N/A block «Не применимо · Причина N/A: … · вес 20 → 0, перераспределён на применимые критерии», confidence in words («Низкая уверенность модели — если не согласны, подайте апелляцию» below 0,70), knowledge-base claims once per dialog («Отклонения от базы знаний: 1», P1), «Копировать ссылку», «Копировать обоснование» (CARD-03, CARD-05, CARD-09, CARD-10, CARD-12).
- **Evidence jumps**: chips, «[n]» references and «Перейти к фрагменту» centre the turn in peach, move focus to it and write `?criterion=C8&turn=10` into the URL; a cold link restores that state; an unknown criterion or turn opens the card with «Фрагмент не найден» (CARD-04).
- **«Как посчитан итог»** (click the ring): the table of base weight, applicability, answer, share, applied weight and points, with the footer «Применимый вес 80 из 100 → коэффициент 1,25 · сумма 51,25 · округление до 2 знаков, половина вверх»; C8 «Без баллов — отмечается отдельно»; after a decision two columns «Исходно» and «После решения» (51,25 → 45,00). Computed from the evaluation payload (`weight`, `weight_applied`, `earned`, `final_earned`); «Пересчитать» calls `POST /api/scoring/preview` (P1) (CARD-06).
- **One numbering**: the transcript, chips, explanations, PDF and results.json all show 1-based numbers; the internal idx appears only in raw Trace JSON with «В запросе реплики пронумерованы с 0» (CARD-07).
- **History tab**: the timeline of §1.5 per criterion; «Сравнить» two evaluations side by side (CARD-11, P1); copied criteria read «Скопировано из оценки #59, без вызова модели».
- **Технические детали tab**: provider, model, prompt version, tokens, cost, latency, Trace entries with local times and the zone, «Модель видела маскированный текст» when PII masking applied (CARD-08); collapsed for operator, director and guest.
- **States**: no evaluation «Оценки пока нет» with «Оценить» only for roles that may run; running: the ring spins and lines fill as `criterion.done` events arrive; partial: «Оценка неполная · C4: модель не ответила» and «Повторить критерий C4»; failed: the reason and «Повторить» (CARD-01, CONV-08).
- **Operator mode**: own dialog → «Оспорить оценку» and «Экспорт PDF», no «Переоценить»; another operator's dialog → read-only with «Диалог другого оператора» (OPR-02).
- Acceptance at 1280×720: header, the gross-error block, the ring «51,25 / 100», «Почему снижена оценка» and the first three criterion lines are visible without scrolling; nothing overlaps; tabs do not clip (SHELL-10).

### 5.5 Calls (P0 waveform and the C1 rule; P1 holds, sync and listening aids)

- **Waveform card** above the two cards, full width (mockup); header meta «Звонок · p4-ru-002 · Русский · 10:20 · оператор не определён» (CALL-02).
- Evidence click shades the fragment from the turn start to the next turn and plays from 2 s before it, stopping at the next turn (8 s at most); «Следующий фрагмент» steps through a criterion's evidence «1 из 6»; the transcript follows playback and a manual scroll pauses following with «К текущей реплике» (CALL-01).
- **C1 rule banner** when the operator speaks before the recording-start event: «Запись началась после начала разговора оператора: приветствие могло не попасть в запись. Правило организаторов для звонков: C1 = N/A», and «Оспорить оценку» prefilled with that reason and the range №2–№5 (CALL-09).
- **Fallbacks**: no recording → transcript-only mode with «Повторить»; no peaks → lanes from transcript spans; a turn outside the recording reads «Фрагмент не попал в аудиозапись» and is not seekable (CALL-12, CALL-01).
- P1: recording-start anchoring and a manual offset nudge, so p4-ru-002 №15 plays at 02:00 instead of about 30 s early (CALL-03, backend B3); hold bands with etiquette markers (CALL-05); IVR, consultation and post-hang-up grouping (CALL-04); ASR-aware chips and readable flags (CALL-06); calls quick filter «Слушать» (CALL-14).
- Acceptance: p4-ru-002 shows two lanes «Клиент» (#0F5431) and «Оператор» (#51AF3D) from `/api/audio/p4-ru-002/peaks`, a round green play button and «00:00 / 10:20»; a C6 chip shades its fragment in peach and plays; audio errors never block the card.

### 5.6 Gross violations (P0 register and «why»; P1 confirm and remove; P2 near misses and follow-up)

```
Грубые нарушения
8 диалогов · 8 нарушений · DEV · чек-лист v1 · итог не обнуляется              [CSV] [Скопировать ссылку]
[Оскорбление клиента 1] [Платная услуга в обход компании 4] [Раскрытие конфиденциальной информации 3] [Иное 0]
Язык ▾  Канал ▾  Оператор ▾  Статус: Требуют подтверждения 8 · Подтверждено 0 · Снято 0 · На апелляции 0
┌ Раскрытие информации · p1-kz-015 · Чат · KZ · Мұрат · 24,71 (итог не обнуляется) · ИИ, не подтверждено ┐
│ «Грубые ошибки — Да. Оператор сообщил данные счёта до идентификации…» (2 строки)                         │
│ №N · время «цитата на языке диалога»     [Почему так классифицировано ▾] [Открыть в диалоге]  [Подтвердить] [Снять] │
└───────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- `#/violations` on `GET /api/violations`: rows sorted by final total ascending (p1-kz-015 24,71 first, p1-ru-023 100,00 last); type chips with counts filter the list; language, channel, operator and status filters live in the URL (`#/violations?type=disclosure&language=KZ`) (GV-01).
- Each row: type chip(s) in the UI language, dialog id, channel, language, operator, final total with «итог не обнуляется», confidence in words, review state, the first two lines of the C8 explanation, the quoted turn with 1-based number and time; «Открыть в диалоге» → `#/conversations/<id>?criterion=C8&turn=<n>`; «← К нарушениям» keeps filters and scroll (GV-01, GV-03).
- «Почему так классифицировано» (expander; drawer on narrow screens): the checklist rule with the matched category highlighted, the justification, the quotes with speaker and time, confidence «высокая · 0,95», related criterion block for disclosure («C3 Идентификация = Нет» with the team rule «C3 = Нет и C8 = Да при раскрытии данных счёта до идентификации»), and «Как определён тип» (judge, keywords or reviewer) (GV-02).
- P1 for reviewer and QA lead: «Подтвердить» / «Снять» open the C8 decision without an operator appeal (spot-check); header «8 требуют подтверждения»; removed rows leave the default list and appear struck through under «Показать снятые»; an open C8 appeal shows «На апелляции» with a link (GV-04, GV-05). Director (P2): «Запросить подтверждение».
- P2: tab «Проверено ИИ: не нарушение» for C3 / C8 contradictions and low-confidence C8 (GV-08); follow-up status, owner and incident summary (GV-07).
- Empty states: «Грубых нарушений не найдено» with the scope and the three types checked; «В звонках грубых нарушений не найдено · проверено 5 звонков».

### 5.7 Appeals and review queue (P0 queue, decision, N/A fix, authorship; P1 filters, context, withdraw; P2 claim, escalate, revise)

```
Апелляции
Открытые 3 · Решённые 1 · Все · Мои        Критерий ▾  Язык ▾  Канал ▾  Автор ▾  Сортировка: сначала грубые ▾
┌ список (360 px) ─────────────────┐ ┌ Апелляция №42 · p1-ru-005 · C6 Ведение диалога ──────────────────────┐
│ ● p1-ru-005 · C6 · Нина (опер.)  │ │ Автор: Нина (оператор) · 07.11 18:40 · 12 мин назад                  │
│   «Клиент не переспрашивал…» 12м │ │ Ответ ИИ: 4 из 10 · 6 / 15 (вес 15 → 23,08) · уверенность средняя   │
│ ○ p1-ru-029 · C7 · Алексей (оп.) │ │ «Управление диалогом — 4. …» [№12] [№13]                             │
│ ○ p4-ru-002 · C1 · Жанар Б. (QA) │ │ Комментарий: «…»   Оператор просит: 7                                 │
│                                   │ │ ┌ Фрагмент диалога: №11–№14, доказательство в персиковом ┐            │
│                                   │ │ Правило критерия ▸  Почему ИИ так решил ▸  Похожие решения (0) ▸     │
│                                   │ │ (•) Подтвердить  ( ) Изменить:  [10] [7] [4 — ответ ИИ] [0]          │
│                                   │ │ Причина [Шаблон]  «Управление диалогом — 7. …»            0 / 1000   │
│                                   │ │ Итог: 78,46 → 85,38 · раздел S3: …        [ ] Использовать как пример │
│                                   │ │                                           [Сохранить решение]         │
└───────────────────────────────────┘ └──────────────────────────────────────────────────────────────────────┘
```

- **Queue** (APL-05): open appeals first (gross-violation appeals pinned, then oldest), decided ones in «Решённые» newest first; each item shows the dialog, criterion, author chip «Нина (оператор)» or «Автор не указан» for legacy rows, age and the first line of the comment; nav label «Апелляции» with the open count. Paging and `#/review/42` (APL-13, P1); filters by criterion, language, channel, author role and sort in the URL (P1).
- **Detail**: AI answer with points and applied weight, confidence, explanation and evidence; the author's comment, requested answer and cited turns (APL-02, P1); the dialog fragment with context turns and evidence in peach, «Показать весь диалог», and for calls the compact two-lane player cued at the fragment (APL-06); «Открыть карточку» → the card at the criterion and turn, with «← К апелляции 42» keeping the draft.
- **Decision panel** (APL-07, APL-08): «Подтвердить» or «Изменить»; under «Изменить» only checklist options with their anchors (C4 20/15/10/5/0, C6 10/7/4/0); the AI's own answer disabled with «Совпадает с ответом ИИ — выберите «Подтвердить»»; N/A offered when `na_allowed_for_channel` is true (C1 on calls) and sent as "N/A" (today the UI sends `null`, which the API refuses); a live preview within 1 s from `POST /api/appeals/preview` with section changes and, for N/A, the weights table; a required reason of at least 15 characters with «Шаблон» in the checklist format («Завершение — Нет. … Основание — …») and click-to-insert turn references (APL-12, P1); «Использовать как пример для ИИ» off by default (DEMO-04, DEMO-07); Ctrl+Enter saves; after saving, «Решение сохранено. Итог: 85,38» and focus moves to the next appeal.
- **Context sections** (P1, APL-11): «Правило критерия» (wording, anchors, N/A condition, call override, version used), «Почему ИИ так решил» (confidence, readable flags, votes, claims), «Похожие решения» (same criterion, same language first), «Толкование команды» from ORGANIZER_UPDATES §2.
- **Guard rails**: four-eyes warning (§1.5); a colleague's decision first → «Апелляцию уже решил(а) Ерлан К.: Изменено на Нет» with the draft kept (APL-12); preview failure → «Предпросмотр недоступен — итог будет пересчитан при сохранении»; a C8 decision shows «Грубая ошибка: Да → Нет · итог без изменений (C8 не даёт баллов)» and a type picker on confirm (GV-05, P1).
- **Roles**: QA analyst and operator see the queue read-only with «Решение принимает ревьюер · Переключиться на роль ревьюера» (SHELL-06).
- **Staleness** (P1, APL-10): an appeal on a criterion that was re-judged or replaced reads «Критерий заменён в версии v2» or «Оценка устарела: диалог переоценён (#70)» with «Закрыть: вопрос снят переоценкой». Decided appeals on unchanged criteria are already carried into partial re-evaluations by the working-tree runner.
- P2: withdraw and edit from the author's side live in «Мои апелляции» (APL-04, P1 there); claims and paging for many reviewers (APL-17); escalation (APL-16); revising a decision (APL-14); reviewer spot-check from any criterion (APL-15).
- Acceptance: the DEMO-02 path with hosted data; p4-ru-002 C1 → N/A, preview 80,50 → 89,44, saved; p1-ru-029 C7 Да → Нет, preview 51,25 → 45,00.

### 5.8 Operator portal (P0)

- **«Мои оценки»** (`#/my/evaluations`): PageHeader «Мои оценки», meta «Оператор · Нина · RU · чаты»; a dismissible orientation panel «Как оцениваются ваши диалоги» with four points (8 критериев чек-листа; ИИ ставит оценку и цитирует реплики; итог из 100, N/A исключаются с перераспределением веса; грубые нарушения отмечаются отдельно и не обнуляют итог) and «Открыть чек-лист», «Как подать апелляцию» (OPR-03, P1); summary tiles «Оценок: 1 · Средний итог: 78,46 · Грубых нарушений: 0 · Апелляций на рассмотрении: 0», «Больше всего баллов теряю: Управление диалогом −13,85 · Корректная и полная консультация −7,69», «Среднее по команде: 79,88» (no colleague names), «Мало данных для выводов» below 3 evaluations (OPR-04).
- List rows: dialog, channel, language, date (chats: the evaluation date with «В данных чата нет даты контакта»; calls: the contact date), total pill, gross badge, appeal state, checklist version; a row opens the card on the result.
- **«Мои апелляции»** (`#/my/appeals`): tabs «На рассмотрении», «Решённые», «Все» with counts; rows «p1-ru-005 · C6 Ведение диалога · подана 07.11 18:40 · На рассмотрении»; decided «Изменено: 4 → 7 · итог 78,46 → 85,38» with the reason one click away, «Оценка подтверждена · Повторная апелляция возможна с новым аргументом», «Отозвана»; unread marks and the nav badge; «Апелляции QA по моим диалогам» as a section (OPR-05). P1: «Отозвать» and «Изменить комментарий» on open appeals (APL-04).
- **Appeal dialog** (W3, used from the card): «Апелляция по критерию C6 · Управление диалогом, скорость и грамотность», the current answer «4 из 10», points «6 / 15», explanation and evidence; «Ваш комментарий» (10–2 000 characters; never «Комментарий аналитика» for an operator); P1 «Какой ответ вы считаете верным» with the effect preview «Если апелляцию удовлетворят: 78,46 → 85,38 · до решения итог не меняется», cited turns and a reason category; drafts per criterion survive closing (APL-01..03).
- **«Чек-лист»** (`#/my/checklist`): the read-only overview of §5.10 (OPR-02).
- Acceptance: as Нина the nav holds exactly «Мои оценки», «Мои апелляции», «Чек-лист» (and «Настройки»); import, run, edit and settings actions are not rendered; `#/review` redirects to `#/my/appeals` (OPR-02).

---

### 5.9 Operators, scorecards and coaching (P1 directory and scorecard; P2 notes, acknowledgement, teams)

- **«Операторы»** (`#/operators`): table of operators with dialogs, evaluated, mean final, gross errors, open appeals, languages, channels, last evaluation; default sort «По риску» (gross errors first: Мұрат 24,71, Кирилл 25,63, Tyler 41,00, Руслан 41,00, Алексей 51,25 …); «мало данных (n=1)» with a greyed mean below 3 evaluations; «Оператор не определён» (10 dialogs) pinned at the bottom and excluded from rankings; search with Kazakh letters (ANL-05). A tab «Не закреплены» lists unattributed dialogs with the first operator line as a hint and «Назначить оператора» for the QA lead (OPR-06, backend B3).
- **Scorecard** (`#/operators/Алексей`, `GET /api/operators/{name}`): header (dialogs 1, RU, chat, mean 51,25, gross errors 1 «Оскорбление клиента», «мало данных»); «Фокус коучинга» with the top criteria where points are lost, each with the explanation, up to two quotes and «Перейти к фрагменту»; C1–C7 bars with the team average; C8 as a count; lists of dialogs, violations and appeals (ANL-06).
- **Coaching** (P2, backend B4): «Заметки коучинга» panel: criterion, dialog, text, due date, status «Запланировано / Проведено»; the operator sees the note under that criterion on the card with the author and date; «Ознакомлен(а)» on the operator's card records the acknowledgement and shows on the scorecard; a note never changes a score; edits keep history (ANL-07).
- P2: teams and a team page (ANL-08); team-lead notes on the team's appeals and filing on behalf of an operator, stored with «от имени: Дина» (OPR-07); the PDF pack for a 1:1 (ANL-06, EXP-06).

### 5.10 Checklist Studio and governance (P0 add, edit, reweight, normalise, replace, save, diff, apply, v1 lock; P1 sections, test on a dialog, impact, history; P2 approval, knowledge base)

**Overview** (`#/checklists`, CHK-01, CHK-14):

```
Чек-листы
Freedom Broker · Чек-лист качества чатов                         [Создать новую версию на основе v1] [Сравнить] [⋯]
v1 · Активна · Базовая версия организаторов · только чтение · 8 критериев · 4 раздела · Σ весов 100 · Оценено: 65
S1 Начало и понимание запроса      C1 Установление контакта    Да/Нет        10 · 10 %   N/A нет   contact   Звонок: N/A, если приветствие не в записи
                                    C2 Выявление потребности    Да/Нет/N/A    15 · 15 %   N/A: данных хватало с начала   discovery
S2 Безопасность и решение          C3 Идентификация            Да/Нет/N/A    20 · 20 %   …   identification
                                    C4 Консультация             20/15/10/5/0  20 · 20 %   …   consultation
S3 Ведение и завершение диалога    C5 Эмпатия … C6 Ведение диалога 10/7/4/0 … C7 Завершение …
S4 Грубые нарушения                C8 Грубые ошибки            флаг · без баллов · отмечается отдельно   critical
Правила подсчёта: N/A исключается, вес перераспределяется до 100 · округление 2 знака, половина вверх ·
нет применимых критериев — итог не считается · грубая ошибка отмечается отдельно · порог первого ответа 3 мин (не в итоге)
Версии: v2 · Черновик · «C7 вес 10» · Методолог · Бекзат Н. · 07.11 21:40 · из v1 · оценено 0 · апелляций 0   [Открыть] [Активировать]
        v1 · Активна · «Чек-лист организаторов» · система · оценено 65                                        [Открыть]
```

- Plain-language rules instead of raw keys (CHK-11); export ids next to every criterion and a compatibility badge per version «Совместима с JSON 1.0» or «Не совместима с JSON 1.0: нет closure» (CHK-21); version rows with status, note, author, time, base, usage counts (CHK-14, backend B5); «Активировать» with the confirmation of §1.7 and «Вернуть v1» afterwards (CHK-14, DEMO-04). Read-only for every role without `checklists.write`.

**Editor** (`#/checklists/:id/versions/:vid`):

```
← Чек-листы   Чек-лист v2 · Черновик (из v1)                      [Проверить] [Сохранить версию…] [Применить к диалогам…]
┌ Критерии ────────────────────────────────────────────────────────┐ ┌ Веса ───────────────────────────────┐
│ S1  C1 Установление контакта   Да/Нет      [10] 9,52 %   ⋯       │ │ Σ 105 · веса относительные: итог      │
│     C2 Выявление потребности   Да/Нет/N/A  [15] 14,29 %  ⋯       │ │ всегда нормируется к 100              │
│ S2  C3 Идентификация           Да/Нет/N/A  [20] 19,05 %  ⋯       │ │ C1 ███ 9,52 %   C2 ████ 14,29 %  …    │
│ …                                                                 │ │ [Нормализовать до 100]  [Отменить]    │
│ S3  C7 Завершение  Изменено    Да/Нет/N/A  [10] 9,52 %   ⋯       │ ├ Что изменится ──────────────────────┤
│ [+ Добавить критерий]                                             │ │ C7 · вес 5 → 10 · только пересчёт   │
└───────────────────────────────────────────────────────────────────┘ │ Переоценка моделью не нужна · $0,00 │
                                                                        └──────────────────────────────────────┘
```

- **Add a criterion** (P0, CHK-02): «Добавить критерий» opens the criterion drawer focused, with the next free code (C9 even after a deletion; today the code is `C${criteria.length + 1}` and duplicates), section and position chosen, defaults Да/Нет, weight 10, N/A off; empty title or instruction blocks saving with field messages.
- **Edit** (P0, CHK-03): title, instruction with a word-level diff against the saved version and «Изменено — потребуется оценка моделью», answer type with a confirmation before a scale is reset, options grid (value, label, points, anchor, «баллов в итог» at the current weight: C6 at 15 → 10 · 15,00, 7 · 10,50, 4 · 6,00, 0 · 0,00), N/A switch with a required condition, «Чат | Звонок» tabs with the effective wording and the call N/A override (editing the call instruction keeps `na_allowed` and its condition, CHK-06), the gross-error flag with «без баллов» and its violation types (CHK-07, P1), export id with the warning «код C7 связан с export id closure; новый смысл — новый код» (CHK-21), optional short title for compact rows.
- **Weights** (P0, CHK-04): number steppers; the panel shows Σ, each share and «Нормализовать до 100» by largest remainder with one-click undo (C7 = 10 gives Σ 105, then 10/14/19/19/14/14/10); weight 0 allowed with «критерий не влияет на итог»; all zero blocks saving; the C8 weight field disabled with «Грубые ошибки не дают баллов».
- **Replace** (P0, CHK-05): row menu «Заменить критерий» opens the drawer with code C9, the same section, position and weight 5, title and instruction empty; C7 shows «будет удалён в новой версии»; open appeals on C7 are listed («2 открытые апелляции по C7 останутся на версии v1»); replacing C8 warns that the register needs a critical criterion.
- **Safe editing** (P1, CHK-09): deletions confirmed with a 10-second «Вернуть»; undo and redo; «Вернуть как было» per criterion; leave guard; a local draft restored after a reload with «Восстановить черновик от 21:42».
- **Validate** (P0, CHK-10): client checks plus the API's 422 `errors[{field, message}]` shown next to each field, the first scrolled into view; warnings (Σ ≠ 100, a scale step without an anchor, a missing export id) still allow saving; a valid draft shows «Проверка пройдена · 3 изменения».
- **Save** (P0, CHK-12): the dialog lists the changes («C7: вес 5 → 10; C1: формулировка»), requires a note of at least 5 characters, «Сделать активной сейчас» unchecked by default (`activate: false` saves a draft), compile progress «Компиляция спецификаций…», a conflict warning when someone saved a newer version (backend B5); then offers «Применить к диалогам…».
- **Diff** (P0, CHK-13): `#/checklists/:id/diff/:a/:b` or a dialog: per criterion old → new values with highlighted text, added and removed, rule changes «без переоценки моделью», «похоже на замену» for a removed and an added criterion with similar titles, and the cost footer «Переоценка моделью: C9 · ≈ $0,30 на 10 диалогах» from a dry run.
- **Apply** (P0, CHK-16): «Применить версию к диалогам» with the scope «Все · Набор DEV (65) · Контрольная группа (10) · Выбранные в списке · Список ID», «Частичная (только изменённые критерии)» and the base version preselected after a change; the dry run says «10 диалогов · переоценка C9 · 10 вызовов · ≈ $0,30 · ≈ 1 мин» or «Переоценка моделью не нужна · 0 вызовов · $0,00»; dialogs without a base evaluation listed as «будут оценены полностью»; live progress; a summary table of old → new totals per dialog with links (backend B2 job summary).
- **Protected baseline** (P0, CHK-15): v1 inputs read-only, «Базовая версия организаторов · только чтение», primary action «Создать новую версию на основе v1»; «Перегенерировать» disabled on locked versions (API 409 `locked`, backend B5); the results.json dialog states «Выгрузка по версии v1».
- P1: test a draft criterion on chosen dialogs without saving (`POST /api/checklists/test-criterion`, CHK-17), sections editing (CHK-08), the what-if calculator (CHK-11) and the impact panel (CHK-18), export and print (CHK-20). P2: approval workflow (CHK-23), knowledge base (CHK-24), drafting a checklist from a document (CHK-27), several checklists on one dialog (CHK-25), a dedicated call checklist with channel assignment (CHK-22), rollback of an applied job (CHK-26), quality bands per version (CHK-28).
- Acceptance (DEMO-05): from any screen the editor opens in two clicks; C7 weight 5 → 10 plus a C1 wording change saved as v2 in under 60 s; apply partial to p1-ru-029, p1-ru-022 and p4-ru-002 finishes in under 2 minutes and the list shows the new totals with «v2».

### 5.11 Analytics (P1)

- `#/analytics` with one **ScopeBar** shared by analytics, violations, operators and overview: set (DEV, control group), checklist version, language, channel, operator, period, basis «Итог после апелляций / Оценка ИИ»; filters in the URL; a scope stamp on every view (ANL-01).
- Tabs: «Критерии» (pass rates, mean points, N/A rates, low confidence, overturns by language, channel and section, n in every cell, hatched cells below n = 5, C8 inverted so 0 % is green, ANL-03); «Потери баллов» (loss drivers in points out of 100 with examples, «Итого потеряно 17,97 ≈ 100 − 82,04», ANL-04); «Скорость и события» (first response against the 3-minute threshold, terminal events, holds, ANL-11); «Стоимость и модели» (spend, cost per evaluation by channel and model, prompt-version mix next to the results.json model block, ANL-12); «Сравнение версий» (v1 ↔ v2 on common dialogs, ANL-09, P2).
- Every number drills down to the filtered list with a chip «Из аналитики: C5 Эмпатия = Нет · RU» and «← К аналитике» (ANL-02).
- Empty scope: «Нет оценённых диалогов для выбранных фильтров» with «Сбросить фильтры», never empty axes.

### 5.12 Executive overview (P1; P0 minimum for the director role: KPI row and risk tile)

```
Обзор качества
DEV · 65 диалогов · чек-лист v1 · claude-opus-5-5 · обновлено 20:41 (UTC+5)          [Сводка (PDF)] [Скопировать ссылку]
[ScopeBar …  Основа: После проверки ▾]
┌ Индекс качества ──────┐ ┌ Грубые нарушения ──────┐ ┌ Покрытие ─────────────┐ ┌ Стоимость оценки ──────┐
│  (82,04 / 100) n = 65 │ │ 8 из 65 (12,3 %)        │ │ 65 из 65 оценено        │ │ ≈ $0,22 за диалог       │
│ ниже 60: 13 · 85+: …  │ │ оскорбление 1 · платная │ │ ошибок 0 · проверено    │ │ всего $16,22 · модель   │
│ ⓘ как посчитано       │ │ услуга 4 · раскрытие 3  │ │ людьми: 0               │ │ Opus 5.5 · прогноз ▸    │
└───────────────────────┘ └─────────────────────────┘ └─────────────────────────┘ └─────────────────────────┘
┌ Где теряем баллы ─────────────────────────┐ ┌ Качество по языкам и каналам ───────────────────────────┐
┌ Операторы, требующие внимания ────────────┐ ┌ Апелляции: открыто · решено · изменено ревьюером · медиана ┐
┌ Доверие к оценке: согласие с экспертами, стабильность, модели и данные ─────────────────────────────────┐
```

- Data: `GET /api/overview` (coverage, quality, critical by type, appeals funnel, criteria with lost points, operators at risk, cost, trend) and `GET /api/violations` (EXE-01..05, EXE-07).
- Director role: only GET requests while clicking every tile, link and tab (EXE-01); QA lead sees the same page with links into queues.
- Definitions behind ⓘ (mean of final totals; N/A redistributed; half-up rounding; gross errors flagged separately); RU and KK decimals with a comma; failed dialogs left out and counted «без итога: N».
- Trust tile without a published gold comparison says «Сверка с эталонной разметкой ещё не опубликована» (no placeholder numbers); provenance «Claude Opus 5.5 · облако · 75 оценок»; «Перед отправкой в облако маскируются телефоны, ИИН, номера карт, e-mail и IBAN»; local model state in neutral styling (EXE-05).
- P1 «Прогноз» drawer with labelled assumptions (EXE-04); P2 the one-page brief for print (EXE-08), «С моего прошлого визита» (EXE-06) and a weekly digest by e-mail or Teams once real identity exists (EXE-09).

### 5.13 Settings, appearance, mascot and the results.json export (P0 appearance, mascot, export entry; P1 organization settings with validation)

- `#/settings` follows the Figma Settings window pattern (`Product Screens`, Components «Settings Row» and «Nav Item»): a left list of tabs and rows with a title, an optional description and one control on the right, divided by hairlines.
- Tabs and rows:
  - **Профиль**: display name for the current role, the operator identity («Сменить оператора»), the role description.
  - **Оформление** (SHELL-01, SHELL-02, SHELL-10): «Тема» Светлая (по умолчанию) / Тёмная / Как в системе; «Плотность» Обычная / Компактная; «Режим презентации»; «Язык интерфейса» RU / KK / EN; **«Фирменный персонаж»** (organization setting `mascot_enabled`, admin only, default off) with the note «Для неформальных презентаций. В продукте и на лендинге по умолчанию выключен.»; switching it on brings poses back only in empty states, without a reload; never in banners, scores, tables, dialogs or errors.
  - **Судья и модели** (admin; read-only «Изменяет администратор» for others): provider, model, ensemble members, effort, explanation language; P1 «Проверить подключение» for the local endpoint (ADM-01, ADM-02); P2 «Маршрутизация судьи» per criterion and channel, decided with the ML owner from measured agreement (ADM-11).
  - **Приватность**: PII masking with a confirmation and a top-bar chip «ПДн: выкл.» when off (ADM-04).
  - **Лимиты**: daily budget, run size, concurrency, the hands-on cap (ADM-03, DEMO-04).
  - **Интеграции**: Dynamics 365 status from the manifest and the organizer API state «настроен · хост …» (never the URL) with a link to `#/admin/integrations`.
  - **Выгрузка результатов**: a card that opens the export dialog below.
  - **О системе**: web and API commits, build time, the `stop-code` tag when present (DEMO-16).
- **«Экспорт results.json» dialog** (W6, EXP-01): entry points in Диалоги «Экспорт ▾ → results.json для организаторов…», in Settings → Выгрузка результатов and in the admin console; roles analyst, QA lead, admin.
  - Set: «DEV (65)», «Контрольная группа (10)», «Выбранные диалоги», «Список ID»; expected count preset (65 or 10; anything else for the control set is an error).
  - «Проверить» → `GET /api/export/results/validate?group_id=…&expect=…` → «Файл валиден: schema 1.0, 65 записей, ok 65, error 0, грубых ошибок 8», the model block «external_api · claude-opus-5-5» and the team «Я Тебя Могну».
  - Errors as rows (conversation id linked to the card, criterion id, plain reason); «Переоценить только эти (N)» opens the Evaluate dialog with v1 preselected; the download then reads «Скачать с ошибками».
  - «Скачать» → `GET /api/export/results.json?group_id=…&download=true` (byte-identical to the API, reads the `X-Results-*` headers).
  - P2: «Расхождение с файлом в репозитории» when the live export no longer matches the committed file (EXP-04).
  - Notes: «Построено по v1 (организаторский); активная версия: v2» when relevant; target paths `results/dev/results.json` and `results/prod/results.json` with copy buttons; «Закоммитьте и запушьте файл до стоп-кода» — the dialog never claims the file is pushed.

### 5.14 Administration (P1 system, readiness, audit, backups, integrations; P2 control console)

- `#/admin/system`: version of web and API (red banner when they differ), uptime, database, providers, disk, budget, warnings from `GET /api/system/health`; «Готовность к демонстрации» from `GET /api/admin/readiness` (backend B6): each check green or red with the reason and a link; «Подготовить демо» runs the idempotent demo seed (DEMO-03, ADM-07).
- `#/admin/jobs`: jobs with type, actor, version, provider, progress, failures, cost, duration, status; log, «Остановить», «Возобновить» for interrupted jobs, «Повторить неудачные» (ADM-07, SYS-03, DEMO-10).
- `#/admin/audit` (alias `#/audit`): `GET /api/audit` newest first, local time with the zone, actor role and name, localised action («Апелляция подана», «Решение по апелляции», «Новая версия чек-листа», «Изменение настроек»), entity link, summary, request id; filters by action, entity, actor and period in the URL; «Загрузить ещё» by 100; CSV for admin (ADM-05).
- `#/admin/backups`: snapshots list with size, time and counts; «Создать снимок» with a note; «Скачать»; «Восстановить» with the typed name, the list of what will be rolled back and the automatic pre-restore snapshot; «Режим работы жюри» on and off (ADM-06, DEMO-04).
- `#/admin/integrations`: from `GET /api/integrations/dynamics/manifest`: build command, the `CQA_CONFIG` snippet with copy, the loader, form-tab steps, the Power Automate body, a framed preview of `?embed=1&conversation=p1-ru-029&host=d365` at about 900 px, «Скачать web resource» when the build is published (D365-02); the organizer API card (ADM-08, P2 for rotation).
- P2: `#/admin/control` with the countdown, pre-flight and steps of the control hour (DEMO-13); until then the CLI `scripts/control_run.py` is the path (team lead).

### 5.15 Embedded views for Dynamics 365 (P1 card; P2 other views and appeals inside D365)

- `features/embed/EmbedRoot.tsx` renders the view of §2.4 without the shell; a MemoryRouter keeps internal steps (register → card) inside the iframe, and «Открыть в CQA» opens the full app in a new tab.
- Light theme and no mascot always; `host=d365` density; container-aware layout: two columns from 768 px of iframe width, one below; text never under 14 px (D365-01, SHELL-16).
- Card view: read-only by default; «Оспорить» enabled only when `CQA_CONFIG.role === 'operator'` and the user owns the dialog (the API returns 403 otherwise) (D365-03); the provenance line «Официальная оценка · claude-opus-5-5 · чек-лист v1 · дата».
- Compact states: «Диалог не найден в CQA», «Оценки пока нет», «CQA недоступен · Повторить»; storage blocked by the sandbox falls back to memory.
- Acceptance: `index.html?embed=1&conversation=p1-ru-029&userlcid=1033&host=d365` renders the English light card at 900 px without horizontal scroll; `?embed=1&data=conversation%3Dp1-ru-029` does the same; `view=violations` lists the 8 rows and opens the full app in a new tab.

### 5.16 Scenarios, help and notifications

- **«Сценарии для жюри»** (P0, DEMO-01): a right-side panel (400 px; a bottom sheet on phones) with the six scenarios of §1.6; each «Открыть» says which role it switches to, opens the target and highlights the control to press with a dismissible hint («Нажмите «Оспорить оценку» у критерия C6»); done scenarios are ticked per browser with «Начать заново»; the panel never covers the ring or the primary buttons. P2: a «Защита за 5 минут» entry with a block timer in presentation mode (DEMO-17).
- **Notifications** (P1, SHELL-12): polling every 30 s with back-off to 2 min of `GET /api/appeals?status=open` and the operator's appeals and `GET /api/violations` (no new endpoint needed); badges on «Апелляции», «Мои апелляции» and «Нарушения»; toasts «Новая апелляция: p1-ru-029 · C7 · оператор Алексей» with «Открыть»; read state per identity in `localStorage`; a hidden tab shows «(2) CQA · Апелляции». P2: `GET /api/notifications` derived from the audit trail (backend B4).
- **«Справка»** (P2, SHELL-14): «?» in the top bar opens a panel with «Как работать: <роль>» in three to five steps and a glossary (N/A, вес и перераспределение, итог и округление, грубое нарушение, апелляция, решение ревьюера, версия чек-листа, частичная переоценка, уверенность модели) with the worked example contact Да, discovery N/A, identification N/A, consultation 15, empathy Да, dialogue 7, closure Да → 85,38.

---

## 6. Backend changes

All changes are additive and keep `docs/API.md` valid: no field is renamed or removed, array responses stay arrays, requests without a role header keep the `demo` behaviour. Each row names its package (§7.3).

### 6.0 Prerequisite: ship the working tree (B0, P0)

The roles, audit, overview, scorecards, guards, checklist governance, snapshots, system, calibration and Dynamics 365 packages exist only in the uncommitted working tree; on the hosted API `GET /api/roles` and `GET /api/version` answer 404 (checked 07.11, 20:27). Before the web relies on them:

- commit and deploy the working tree with `APP_VERSION` set, run the 539 offline tests, take the `baseline` snapshot;
- verify on the hosted API: `GET /api/roles` (8 roles + demo), `GET /api/me` with `X-CQA-Role: operator` and `X-CQA-User: %D0%9D%D0%B8%D0%BD%D0%B0`, `GET /api/overview`, `GET /api/operators/%D0%9D%D0%B8%D0%BD%D0%B0`, a 403 for `POST /api/checklists` as `director`, a 429 guard on a dry run;
- pre-register the router slots `coaching`, `notifications` and `scoring` in the `main.py` tuple (a missing module is skipped with a warning, so packages can land in any order);
- apply the additive schema of §6.2 in one commit (`db.ADDITIVE_COLUMNS`, `db.ADDITIVE_INDEXES`, `models.py`).

The web stays compatible either way: it feature-detects `GET /api/roles` (404 → the built-in role registry), and the appeal and decision bodies carry `author_role`, `author_name` and `reviewer_role`, which the deployed API already accepts.

### 6.1 Endpoint changes

| Pkg | Endpoint or change | Fields and behaviour | Stories | Priority |
|---|---|---|---|---|
| B1 | `POST /api/appeals` | body adds `requested_answer`, `cited_turn_idx[]`, `fragment_start_s`, `fragment_end_s`, `reason_category`; comment 10–2 000 characters → 422 field error | APL-02, APL-03 | P1 |
| B1 | `GET /api/appeals` | filters `code`, `language`, `channel`, `q`, `sort` (`critical_first`, `oldest`, `low_confidence`), `limit`, `offset` with an `X-Total-Count` header; items add `confidence`, `weight_applied`, `evidence`, `checked_span`, `is_latest_evaluation`, `checklist_version_no`, `requested_answer`, `cited_turn_idx`, `withdrawn_at`, `edited_at`, `author_seen_at`, `stale_reason` | APL-05, APL-06, APL-10, APL-11, APL-13 | P1 |
| B1 | `POST /api/appeals/{id}/withdraw`, `PATCH /api/appeals/{id}` | author only (403 `not_author`); open only (409 `already_decided`); edits kept in the audit trail | APL-04 | P1 |
| B1 | `POST /api/appeals/{id}/decision` | reason of at least 15 characters (422); 409 `already_decided` with the existing decision in the body; `violation_type` for critical criteria stored on the decision and used by the register; `add_to_fewshot` default unchanged in the API, the UI sends `false` unless switched on | APL-07, APL-12, GV-05, DEMO-04 | P1 (reason, 409); P1 (type) |
| B1 | `POST /api/appeals/{id}/seen` | the author marks a decision read (operator portal unread marks across browsers) | OPR-05 | P2 (P0 keeps read state in `localStorage`) |
| B1 | `POST /api/appeals/{id}/close` | closes an open appeal whose criterion was re-judged or replaced, with a reason; list shows `stale_reason` | APL-10 | P1 |
| B2 | `POST /api/evaluations/run` | `conversation_ids: []` → 400 `empty_selection` (`null` with `all: true` still means every dialog, as documented); body adds `dataset_id` and `filters` (list keys) for «все N по фильтру»; above `MAX_DIALOGS_PER_RUN` the dry run returns `batches`; job params store the actor | SYS-06, DEMO-06, SYS-09, CHK-16 | P0 (empty selection), P1 (scope) |
| B2 | `GET /api/jobs/{id}/summary` | per dialog `{conversation_id, before_total, after_total, changed_codes, new_answers}` for the apply summary | CHK-16, ANL-09 | P1 |
| B2 | job lifecycle | a job whose dialogs all failed ends `failed`; a dialog already in a running job is refused («уже оценивается»); failure paths update the evaluation by id | SYS-04 | P1 |
| B3 | `GET /api/conversations` | filters `criterion` + `answer` (`yes`, `no`, `na` or a scale step), `lost=1`, `status` (`failed`, `partial`, `not_evaluated`, `running`, `evaluated`), `checklist_version_id`, `review_state`, `has_appeal`, `low_confidence=1`, `sort` (`total_asc`, `total_desc`, `id`, `turns`, `first_response`, `evaluated_at`); `operator` also matches `agent_name`; items add `checklist_version_no`, `checklist_name`, `critical_types[]`, `low_confidence_codes[]`, `match_snippet`, `agent_name`, `operator_source` | CONV-10, ANL-02, CHK-19, CARD-09, GV-03, OPR-04 | P1 (P0: `operator`, `critical`, `language`, `sort`) |
| B3 | `PATCH /api/conversations/{id}/operator` | `{name, reason}` sets `agent_name` (identity only, never judge input) and `operator_source = manual`; audited; permission `team.manage` | OPR-06 | P1 |
| B3 | ingest normaliser | «менің атым X» greeting pattern; recording-start anchoring of call offsets with `meta.recording_start_idx` and `turn.in_recording` (no re-evaluation needed, offsets only) | OPR-06, CALL-03, CALL-09 | P1 |
| B4 | `GET /api/violations` | items add `rule_text`, `classified_by` (`judge`, `keywords`, `reviewer`), `related[]` (the C3 row for disclosure), `open_appeal_id`, `decision {reviewer_name, reason, at}`; filters `dataset_id`, `review_state` (`unconfirmed`, `confirmed`, `overturned`, `under_appeal`); the critical criterion is selected by `is_critical`, not by the code C8 | GV-01, GV-02, GV-04, GV-06 | P1 |
| B4 | `GET /api/violations/candidates` | near misses: C3 = Нет with C8 = Нет where the C3 rationale describes disclosure, low-confidence or split C8 | GV-08 | P2 |
| B4 | `GET /api/overview`, `GET /api/analytics/summary`, `GET /api/operators` | accept `dataset_id`, `language`, `channel`, `operator`, `basis=final` or `ai`; operators add `min_n` handling and the unassigned count | ANL-01, EXE-02, ANL-05 | P1 |
| B4 | `GET/POST /api/operators/{name}/notes`, `PATCH /api/coaching-notes/{id}` | coaching notes (new table); permission `team.manage`; soft delete with history | ANL-07 | P2 |
| B4 | `POST /api/evaluations/{id}/ack` | the operator acknowledges an evaluation of an own dialog (own-dialog check as for appeals); shown on scorecards | ANL-07 | P2 |
| B4 | `GET /api/notifications?since=` | derived from `audit_events` plus counts `{open_appeals, unread_decisions, violations_unconfirmed}` for the actor | SHELL-12 | P2 |
| B5 | checklist versions | add `created_by {role, name}`, `evaluations_count`, `open_appeals_count`, `locked` (organizer v1 and versions referenced by results files) | CHK-01, CHK-14, CHK-15 | P1 (`locked` P0) |
| B5 | `POST …/judge-spec/{code}/regenerate` | 409 `locked` on locked versions or versions with evaluations | CHK-15 | P0 |
| B5 | version save | judge specs compile outside the database write transaction; progress writes retried; a running job never fails with «database is locked» while a version is saved | SYS-05 | P0 |
| B5 | `POST /api/checklists/{id}/versions` | accepts `base_version_id`; 409 `version_conflict` with the newer version when someone saved meanwhile | CHK-12 | P1 |
| B5 | criteria | optional `short_title` and `title_i18n` (not judge input, excluded from re-judge fields) | CARD-01, SHELL-08 | P1 |
| B5 | `POST /api/scoring/preview` (new router `scoring`) | wraps `compute_score`: answers in, total, sections, applied weights, critical flag and rounding note out; no model call | CHK-11, CARD-06 | P1 |
| B5 | `POST /api/checklists/{id}/versions/{base}/impact` | recomputes stored verdicts under draft weights and rules; mean before and after, dialogs moving > 2 points, top changes | CHK-18 | P2 |
| B5 | `GET /api/checklists/{id}/versions/{vid}/compatibility` | JSON 1.0 compatibility: the 8 organizer ids present with their value types | CHK-21 | P1 |
| B6 | `POST /api/admin/demo/seed` | idempotent: an open appeal by «Оператор · Алексей» on p1-ru-029 C7; an open analyst appeal on p4-ru-002 C1 asking for N/A; a decided appeal on p1-ru-023 C8 confirmed by «Ревьюер · Айгерим С.»; `add_to_fewshot = false`; labelled «демо-пример»; never touches control dialogs or Нина's p1-ru-005 C6 | DEMO-03 | P0 |
| B6 | `GET /api/admin/readiness` | no paid call, under 5 s: API, database, Claude key, organizer URL, v1 with Σ 100, DEV 65 / 65 evaluated, DEV results valid, p1-ru-029, p1-ru-022 and p4-ru-002 present, audio cached, one decided appeal, no open appeal on p1-ru-005 C6, snapshot younger than 2 h, budget ≥ $10, web and API commits equal | DEMO-03, DEMO-02 | P0 |
| B6 | `POST /api/admin/hands-on` | `{on: true}`: snapshot «pre-hands-on», run cap 10, hands-on budget, few-shot off; `{on: false}`: restore or keep; audited | DEMO-04 | P1 |
| B6 | settings keys | `demo_mode` (shows «Сценарии»), `confidence_threshold` (0,70), `hands_on` | DEMO-01, CARD-09 | P0 (`demo_mode`), P1 |
| B7 | `GET /api/roles` hints | `lead` labelled «Руководитель QA» / «QA lead» / «QA жетекшісі»; admin home `#/admin`; visitor home `#/overview`; nav hints of §2.3 | SHELL-04 | P1 |
| B7 | permissions | new `team.manage` (lead, admin); constraint `critical_only` on `appeals.create` for director | OPR-06, ANL-07, APL-01 | P1, P2 |

Write-route rules: every package registers the `RouteRule` of each new write route through `register_route()` from its own module, so the route-map coverage test stays green and only B7 edits `api/cqa/security/roles.py`.

Already done in the working tree (no change needed, only deployment): weight-only partial runs copy every criterion with no model call (`api/cqa/jobs/runner.py`, `only_codes = []` is respected); reviewer decisions on unchanged criteria are carried into partial re-evaluations (`api/cqa/judge/runner.py`); `activate: false` drafts; 422 validation with field paths; budget, run-size and concurrency guards; job resume.

### 6.2 Data model additions

| Table | Column or table | Type | Package | Stories |
|---|---|---|---|---|
| `appeals` | `requested_answer`, `cited_turn_idx` | JSON | B1 | APL-02 |
| `appeals` | `fragment_start_s`, `fragment_end_s` | FLOAT | B1 | APL-02 |
| `appeals` | `reason_category` | VARCHAR(32) | B1 | APL-02 |
| `appeals` | `withdrawn_at`, `edited_at`, `author_seen_at` | DATETIME | B1 | APL-04, OPR-05 |
| `appeals` | `closed_reason` | VARCHAR(64) | B1 | APL-10 |
| `decisions` | `violation_type` | VARCHAR(32) | B1 | GV-05 |
| `checklist_versions` | `created_by_role`, `created_by_name` | VARCHAR(32), VARCHAR(128) | B5 | CHK-14 |
| `criteria` | `short_title` | VARCHAR(64) | B5 | CARD-01 |
| `criteria` | `title_i18n` | JSON | B5 | SHELL-08 |
| `conversations` | `operator_source` | VARCHAR(16) (`greeting`, `source`, `manual`) | B3 | OPR-06 |
| new `coaching_notes` | `id, operator_name, criterion_code, conversation_id, evaluation_id, text, due_date, status, author_role, author_name, created_at, updated_at, deleted_at` | table | B4 | ANL-07 |
| new `evaluation_acks` | `evaluation_id, operator_name, acked_at` (unique pair) | table | B4 | ANL-07 |
| indexes | `evaluations (conversation_id, id)`, `evaluations (checklist_version_id, status)`, `evaluation_criteria (evaluation_id)`, `appeals (status, created_at)`, `conversations (dataset_id)`, `conversations (operator_name)`, `conversations (agent_name)` | index | B0 | SYS-09 |

Columns on existing tables go through B0 (the shared files `models.py` and `db.py`); new tables are declared in the owning package's own module, imported by its router, so the table-creation step at start-up sees them without touching `models.py`.

### 6.3 Compatibility rules

- Only additive fields, parameters, endpoints and error codes. The one behaviour change is a bug fix aligned with the documented contract: `conversation_ids: []` no longer means "every dialog" (`docs/API.md` documents `null` with `all`).
- Array endpoints (`GET /api/appeals`, `GET /api/operators`) stay arrays; paging uses `limit`, `offset` and `X-Total-Count`.
- New error codes: 400 `empty_selection`; 403 `not_author`; 409 `already_decided` (with the decision), `version_conflict`, `locked`; 422 field errors as today.
- B0 adds one section «Design proposal additions» to `docs/API.md` at merge time; `docs/WEB.md` is rewritten by WI.

### 6.4 Requests to other owners (not planned here)

- ML teammate (judge contract and prompts): a structured violation type with the proving turn for critical criteria (GV-06); a steering-attempt flag and the escaped transcript rule (SYS-12); stored validation issue details and the model's original quote (CARD-15); an optional request summary, outcome and topic (CARD-16, ANL-13); which configuration to freeze for the control run (DEMO-07).
- Team lead (results.json, in progress): optional structured validator errors next to the strings, so the export dialog can link conversation and criterion (EXP-01); the 1-based renumbering of rationale references (CARD-07, EXP-02).

---

## 7. Delivery plan

### 7.1 Rules for parallel agents in one repository

1. **Contracts first.** Wave 0 publishes the component and hook signatures of §7.4 (typed stubs that render something sensible) and the additive schema of §6.2. After that, every package codes against stubs and never waits for another package to start.
2. **Disjoint ownership.** Every file has one owner package (globs in §7.3). A package edits only its globs and may read anything. A file not listed belongs to the integration owner: WI for `web/` and for `docs/WEB.md` and `docs/DEMO.md`, B0 for `api/` and the rest of `docs/`.
3. **Shared files are frozen after Wave 0:** `web/package.json`, `web/pnpm-lock.yaml`, `web/vite.config.ts`, `web/tailwind.config.ts`, `web/src/index.css`, `web/src/App.tsx`, `web/src/routes.tsx`, `web/src/i18n/index.ts`, the legacy dictionaries `web/src/i18n/{ru,en,kk}.ts`, `api/cqa/main.py`, `api/cqa/models.py`, `api/cqa/db.py`, `api/cqa/config.py`, `docs/API.md`. A needed change is a one-line request to the integration owner, applied in a short serialized window.
4. **Strings**: each feature has its own i18n namespace (`web/src/features/<x>/i18n.ts` with RU, KK and EN); no new keys in the legacy dictionaries; `pnpm i18n:check` must pass.
5. **API modules**: each domain module in `web/src/api/` has one owner; a consumer that needs another call adds a private hook inside its own feature folder.
6. **Mock mode**: each domain owner keeps its own `web/src/mock/handlers/<domain>.ts` working for P0 screens.
7. **Never touched by these packages:** `results/**`, `api/cqa/export/**` (team lead, results.json), `api/cqa/judge/**` and `api/eval/**` (ML teammate and team lead), `scripts/control_run.py`.
8. **Commits**: small, per package, rebased on main, message prefixed with the package id (`W5: replace criterion`); no force pushes; no deploy from a package branch.

### 7.2 Waves and timeline

T0 = the moment the team lead approves this plan (tonight). The UI freezes before the control set opens.

| Wave | Wall time | Web | Backend | Gate to the next wave |
|---|---|---|---|---|
| 0 | T0 → T0 + 1 h 30 | W0a design system and shell ∥ W0b platform plumbing | B0a: ship the working tree, router slots, additive schema (§6.0) | `pnpm typecheck && pnpm build` green; every route renders a placeholder; hosted `GET /api/roles` = 200 |
| 1 | T0 + 1 h 30 → T0 + 5 h | W1–W7 in parallel | B1–B7 in parallel (start at T0 + 0 h 30, after the schema commit) | each package's P0 acceptance checks |
| 2 | T0 + 5 h → T0 + 6 h 30 | WI: contracts wired, smoke test, i18n gaps, deploy to Vercel | B0b: merge, `docs/API.md`, deploy to Railway, demo seed, readiness green, snapshot `before-jury` | two timed rehearsals of the live asks (DEMO-17) |
| 3 (optional) | until H + 21 | P1 items by priority, one deploy per hour at most | P1 items | readiness green after every deploy |
| Freeze | H + 21 → stop-code | fixes only, no redeploy during the control hour | no deploy during the control hour; the control run stays CLI-first (`scripts/control_run.py`, team lead) | `results/prod/results.json` pushed and verified |

Cut order when time runs short (dropped first → dropped last): P2 items → W7 admin pages except readiness → analytics tabs (keep overview) → landing capabilities and D365 sections (keep hero, numbers, how it works, audience, FAQ, CTA, footer) → call holds and sync (keep the waveform and the C1 banner). Never cut: light shell, roles with operator identity, the card per the mockup, violations register, appeal and decision, Studio add / reweight / replace / apply, the results.json dialog, the demo seed and readiness.

### 7.3 Work packages

#### W0a · Design system and shell (Wave 0, one agent)

- **Owns:** `web/index.html`, `web/tailwind.config.ts`, `web/postcss.config.js`, `web/public/favicon.svg`, `web/src/index.css`, `web/src/styles/**`, `web/src/components/{ui,layout,states,brand,domain}/**` (the three stubs `TranscriptList`, `TranscriptRow` and `WaveformPlayer` pass to W2 at the end of Wave 0), `web/src/components/ErrorBoundary.tsx`, `web/src/i18n/core/**`. In Wave 0 only, a mechanical pass over existing feature files: remove the `<Mascot>` uses in 11 files and the removed token classes (`glow`, `shadow-float`, `font-mono` on scores).
- **P0:** tokens of §4.2 (light default, dark optional), the type scale, Tailwind mapping; primitives restyled (Button, inputs, Select, Switch, Checkbox, SegmentedControl, Dialog, Drawer, Tabs, Table, Tooltip, toasts); Sidebar, TopBar with slots, PageHeader, Card, Banner, EmptyState, ErrorState, skeletons, NotFound; atoms ScoreRing, ScorePill, CriterionIcon, CriterionLine, LossSummary, EvidenceChip, StatusChip, CountBadge, KpiTile, AnswerLabel, Timeline; BrandMascot gate; typed stubs `TranscriptList`, `TranscriptRow`, `WaveformPlayer` (handed to W2).
- **Depends on:** nothing. **Provides:** every visual building block.
- **Acceptance:** a fresh browser paints light with no `dark` class and `theme-color` #F5F7F5; the sidebar is #0F5431 with a #1F7028 active pill and white 600 text, no glowing dot, 72 px below 1280 px; `grep -rn "Mascot" web/src` finds only `components/brand/BrandMascot.tsx` and its settings preview, and no `/mascot/` URL reaches the DOM with `mascot_enabled = false`; ScoreRing shows «85,5 / 100» in RU and «85.5 / 100» in EN, «—» with «Итог не рассчитан» for null; contrast of body, secondary, green and warning text ≥ 4,5:1; `pnpm typecheck && pnpm build && pnpm build:single` pass and `dist-single/index.html` < 4,5 MB.

#### W0b · Platform plumbing (Wave 0, one agent)

- **Owns:** `web/package.json`, `web/pnpm-lock.yaml`, `web/vite.config.ts`, `web/scripts/**`, `web/src/main.tsx`, `web/src/App.tsx`, `web/src/routes.tsx`, `web/src/store/**`, `web/src/roles/**`, `web/src/lib/**`, `web/src/api/{client,core,types,endpoints,queries}.ts`, `web/src/i18n/index.ts`, `web/src/i18n/{ru,en,kk}.ts` (frozen legacy), `web/src/mock/server.ts`. Creates, then hands over: the domain API modules `web/src/api/<domain>.ts` (existing calls moved, the old files kept as re-exporting barrels), `web/src/mock/handlers/<domain>.ts`, and a placeholder page plus an `index.ts` with the §7.4 stub exports in every feature folder of this section. Before the hand-over it moves `features/card/AppealDialog.tsx` to `features/appeals/` and `features/card/EmbedPage.tsx` to `features/embed/EmbedRoot.tsx`, so no package edits another package's folder later.
- **P0:** the route table of §2.1 with lazy pages; session store (role, identity, persistence, `?role=`, `?as=`, first-open picker trigger); role registry mirroring `roles.py` (permissions, nav per role, homes) with `useActor`, `useCan`, `useNavItems`, `RequireRole`; feature detection of `GET /api/roles` and `GET /api/me`; client headers `X-CQA-Role`, `X-CQA-User`, `X-Request-ID` and the error envelope with `errors[]` and the request id; `lib/format` with `Intl` (comma decimals, plurals, time zone); `useUrlState`; D365 parameter parsing (`data`, `id`, `userlcid`, `host`, `view`, `CQA_CONFIG.role/user`); i18n namespaces and `pnpm i18n:check`; `build:single` with the embed entry and `VITE_MASCOT=0`; build commit injected for «О системе».
- **Depends on:** nothing. **Provides:** routing, session, permissions, API modules, i18n plumbing.
- **Acceptance:** every route of §2.1 renders its placeholder with a title and an unknown route shows NotFound; `?role=director` on a fresh browser lands on `#/overview` and survives a reload; `?role=xyz` shows the toast and the picker; every request carries `X-CQA-Role` and `X-Request-ID`, and `X-CQA-User` URL-encoded when a name is set; 51.25 formats as «51,25» in RU and KK and «51.25» in EN; «1 диалог · 2 диалога · 5 диалогов»; `pnpm i18n:check` fails on a missing KK key; no existing feature file breaks (barrels).

#### W1 · Entry, roles, notifications, help (Wave 1)

- **Owns:** `web/src/features/{landing,start,roles,notifications,help}/**`, `web/src/api/{roles,notifications}.ts`, `web/src/mock/handlers/roles.ts`, `web/public/og-card.png`.
- **P0:** landing §3 (header, hero with the live card, numbers strip, problem, how it works, audience, compare, FAQ, final CTA, footer); `#/start`; RoleSwitcher; IdentityPicker; ScenariosPanel and the top-bar «Сценарии» button. **P1:** capabilities, languages, security and D365 landing sections; NotificationsBell and nav badges (polling). **P2:** HelpPanel and glossary; QR for the slides.
- **Depends on:** W0a atoms, W0b session and registry. **Provides:** `RoleSwitcher`, `IdentityPicker`, `ScenariosButton`, `NotificationsBell`, `useNavBadges` (§7.4).
- **Acceptance:** `#/` at 1280×720 shows the headline, subline, both buttons and the live card without scrolling, and has no horizontal scroll at 375 px; with the API blocked the numbers strip shows the fallback values with «по состоянию на 07.11.2026», never 0; `#/start` → Оператор → «Кто вы?» lists 55 names without «—», «мұр» finds Мұрат, picking Нина opens `#/my/evaluations` with «Оператор · Нина» in the top bar; the scenarios panel lists six entries and each one opens its target and announces the role switch; (P1) an appeal filed in another tab shows on the «Апелляции» badge within 30 s.

#### W2 · Conversation card and calls (Wave 1)

- **Owns:** `web/src/features/card/**` (without `AppealDialog`, moved to W3 in Wave 0), `web/src/components/domain/{TranscriptList,TranscriptRow,WaveformPlayer}.tsx`, `web/src/api/{evaluations,audio}.ts`, `web/src/mock/handlers/{evaluations,audio}.ts`.
- **P0:** §5.4 (mockup layout, gross-error block, QualityCard, LossSummary with the two buttons, criterion detail with anchors, N/A block, evidence jumps with peach and URL state, «Как посчитан итог» from the payload, 1-based numbering, History timeline with names and version numbers, technical details tab, states, operator mode); §5.5 P0 (WaveformPlayer from `/peaks`, fragment shading and seek, C1 rule banner, fallbacks). **P1:** recording-start sync once B3 ships, holds and etiquette markers, IVR and consultation grouping, ASR chips, compare evaluations, copy link and justification, knowledge-base claims, low-confidence badges. **P2:** translation on demand, good examples, step through the list.
- **Depends on:** W0a, W0b; consumes `AppealDialog` (W3) and `EvaluateDialog` (W6) through their stubs. **Provides:** `ConversationCard`, `TranscriptList`, `WaveformPlayer`.
- **Acceptance:** the p1-ru-029 acceptance of §5.4 at 1280×720; the C8 chip «№10 · 01:23 …» centres turn 10 in #FDEBD3 with a #F2B66D border and writes `?criterion=C8&turn=10`, and the cold link restores it; «Как посчитан итог» reads «Применимый вес 80 из 100 → коэффициент 1,25 · сумма 51,25 · округление до 2 знаков, половина вверх»; C3 shows «Не применимо» with «вес 20 → 0, перераспределён»; p4-ru-002 shows the two lanes, the round play button and «00:00 / 10:20», a C6 chip shades and plays its fragment, and an audio failure leaves the card usable.

#### W3 · Appeals, review and operator portal (Wave 1)

- **Owns:** `web/src/features/{appeals,review,operator}/**`, `web/src/api/appeals.ts`, `web/src/mock/handlers/appeals.ts`.
- **P0:** `AppealDialog` (role-aware copy, author fields in the body, «Ваш комментарий», validation, drafts); `#/review` two-pane queue with authors, `DecisionPanel` (options with anchors, AI answer disabled under «Изменить», N/A by `na_allowed_for_channel` sent as "N/A", live preview with sections, reason ≥ 15 characters, template, few-shot switch off by default, four-eyes warning, conflict handling); `#/my/evaluations`, `#/my/appeals`, `#/my/checklist`. **P1:** filters, sort and `#/review/:appealId`; context sections (rule, AI reasoning, precedents); requested answer with effect preview and cited turns; withdraw and edit; staleness and close; C8 type picker. **P2:** claim, escalation, revising a decision, spot-check from any criterion, multi-criterion appeal (APL-18), appeal window and response target (APL-19).
- **Depends on:** W0a, W0b; consumes `TranscriptList` and `WaveformPlayer` (W2), `ChecklistOverview` (W5). **Provides:** `AppealDialog`.
- **Acceptance:** the DEMO-02 path on hosted data in under 2 minutes (appeal signed «Оператор · Нина», the queue shows the author, preview 78,46 → 85,38, «Мои апелляции» reads «Изменено: 4 → 7 · итог 78,46 → 85,38»); p4-ru-002 C1 → N/A sends "N/A", previews 80,50 → 89,44 and saves; a reason under 15 characters disables saving; Нина opening p1-ru-029 sees a read-only card without «Оспорить».

#### W4 · Insights: violations, overview, analytics, operators (Wave 1)

- **Owns:** `web/src/features/{violations,overview,analytics,operators}/**`, `web/src/api/{violations,overview,analytics,operators}.ts`, `web/src/mock/handlers/{violations,overview,analytics,operators}.ts`.
- **P0:** `#/violations` register with type chips, filters in the URL, rows with explanation and quote, «Почему так классифицировано», «Открыть в диалоге» and back with filters; `#/overview` minimum for director and QA lead (KPI row, risk tile, scope line). **P1:** confirm and remove from the register; ScopeBar; analytics tabs with drill-downs; operators directory with the unassigned tab and assignment; scorecard with the coaching focus; full overview. **P2:** near misses, follow-up, coaching notes and acknowledgements, teams, version comparison, the printable brief.
- **Depends on:** W0a, W0b; consumes `AppealDialog` (W3) for spot-checks. **Provides:** `ScopeBar`.
- **Acceptance:** `#/violations` lists 8 rows from p1-kz-015 (24,71) to p1-ru-023 (100,00, «итог не обнуляется») with chips «Оскорбление клиента 1 · Платная услуга в обход компании 4 · Раскрытие конфиденциальной информации 3 · Иное 0»; Раскрытие + KZ leaves only p1-kz-015 and the URL shows both filters; p1-ru-029 expanded shows the rule with «оскорбление» highlighted, the justification, quote №10 and «высокая · 0,95»; as Директор, clicking every tile of `#/overview` sends only GET requests; (P1) a heatmap cell opens the filtered list.

#### W5 · Checklist Studio (Wave 1)

- **Owns:** `web/src/features/checklists/**`, `web/src/api/checklists.ts`, `web/src/mock/handlers/checklists.ts`.
- **P0:** §5.10 overview, add with the next free code, edit (wording diff, options with weighted points, N/A, call override kept intact, gross flag, export id), weights with Σ, shares and «Нормализовать до 100», replace, client validation plus 422 mapping, save dialog (note, draft by default, compile progress), diff with old → new, apply dialog (scope, partial preset, dry run, progress, summary), locked v1. **P1:** sections, test on a dialog, safe editing, calculator, version timeline with authors and usage, compatibility badge, export and print. **P2:** approval, knowledge base, drafting from a document, impact panel, rollback of an applied job.
- **Depends on:** W0a, W0b; consumes `api/jobs.ts` and the job progress hook (W6). **Provides:** `ChecklistOverview`.
- **Acceptance:** v1 reads «Базовая версия организаторов · только чтение» with «Создать новую версию на основе v1»; after deleting C5 «Добавить критерий» still proposes C9; C7 weight 10 → Σ 105 in amber with shares, «Нормализовать до 100» → 10/14/19/19/14/14/10 with undo; «Заменить» on C7 → C9 in S3 at position 7 with weight 5, and the diff shows exactly «C7 удалён» and «C9 добавлен · нужна оценка моделью»; saving with a note and the activate box unchecked creates «v2 · Черновик» while v1 stays active; applying a weight-only change to three dialogs shows «Переоценка моделью не нужна · 0 вызовов · $0,00» and a summary of old → new totals; 422 errors appear next to their fields.

#### W6 · Conversations, jobs and results export (Wave 1)

- **Owns:** `web/src/features/{conversations,jobs,export}/**`, `web/src/api/{conversations,jobs,export}.ts`, `web/src/mock/handlers/{conversations,jobs,export}.ts`.
- **P0:** §5.3 list with server paging, sorting, filters in the URL, default columns, search that keeps transcript matches, Enter on an exact id; Evaluate dialog with the budget line, confirmations and inline 429; JobIndicator; `ExportResultsDialog` (§5.13). **P1:** work queue tiles, import preview and readable failures, paste preview with speaker mapping, multi-record upload, id conflict prompt, failure filter and retry, «Выбрать все N по фильтру». **P2:** criterion matrix, test chips and archive, background import.
- **Depends on:** W0a, W0b. **Provides:** `EvaluateDialog`, `JobIndicator`, `useJobProgress`, `ExportResultsDialog`.
- **Acceptance:** `#/conversations?language=KZ&critical=1` lists exactly p1-kz-013 and p1-kz-015, and Back from a card restores filters and scroll; «Экспорт ▾ → results.json для организаторов…» validates DEV with expect 65 and shows «Файл валиден: schema 1.0, 65 записей, ok 65, error 0, грубых ошибок 8», the model block, both target paths and the commit reminder, and the download matches the API bytes; a run refused by a guard shows the API message in the dialog.

#### W7 · Settings, administration and embed (Wave 1)

- **Owns:** `web/src/features/{settings,admin,embed}/**`, `web/src/api/{settings,admin,system,audit,integrations}.ts`, `web/src/mock/handlers/{settings,admin,system,audit,integrations}.ts`.
- **P0:** Settings tabs «Профиль», «Оформление» (theme, density, presentation mode, language, «Фирменный персонаж» for admin), «Выгрузка результатов» (opens W6's dialog), «О системе»; organization tabs read-only for non-admins. **P1:** organization settings with 422 errors inline; `#/admin/system` with readiness and «Подготовить демо»; jobs; audit log; backups with typed restore; integrations from the manifest; the embed card with D365 parameters and host density. **P2:** other embed views, operator appeals inside D365, control console, settings history.
- **Depends on:** W0a, W0b; consumes `ConversationCard` (W2), `ExportResultsDialog` (W6), violations and overview views (W4) for embed views.
- **Acceptance:** a new visitor gets the light theme, a chosen dark theme persists after reload; «Фирменный персонаж» is off by default and, switched on by an admin, brings poses back only in empty states without a reload; non-admin roles see «Изменяет администратор» on organization rows and the API 403 never shows as a raw error; (P1) `index.html?embed=1&conversation=p1-ru-029&userlcid=1033&host=d365` renders the English light card at 900 px without horizontal scroll; after the DEMO-02 path the audit log shows `appeal.create` by «Оператор · Нина» and `appeal.decide` by «Ревьюер · Айгерим С.».

#### WI · Integration (Wave 2, team lead or one agent)

- **Owns:** everything in `web/` not owned above after Wave 1 (fixes in `routes.tsx`), `web/tests/**`, `docs/WEB.md`, `docs/DEMO.md`.
- **Tasks:** replace remaining stubs; check every deep link of §2.2; Playwright smoke in mock mode (landing → start → card → appeal → decision → checklist add → apply dry run → violations, SYS-11); `pnpm i18n:check` and KK gaps; a 1280×720 and 1024×768 pass on the card, the queue and Studio; a dark-theme pass; `pnpm build:single` budget; deploy to Vercel; with B0: demo seed, readiness green, snapshot `before-jury`; rewrite `docs/DEMO.md` for the light theme, the operator role, 1-based turns and hosted numbers (DEMO-02, DEMO-17); two timed rehearsals under 4 min 45 s.

#### B0 · Backend integration and deploy (team lead)

- **Owns:** `api/cqa/main.py`, `api/cqa/models.py`, `api/cqa/db.py`, `api/cqa/config.py`, `docs/API.md`, `docs/ARCHITECTURE_V2.md`, deploy configuration; plus `api/cqa/export/**` (results.json, already in progress).
- **Wave 0 (B0a):** §6.0 (ship the working tree, router slots, additive schema and indexes). **Wave 2 (B0b):** merge packages, run all tests, update `docs/API.md`, deploy with `APP_VERSION`, seed, snapshot.
- **Acceptance:** 539 existing tests plus the new ones pass offline; hosted `GET /api/version` shows the deployed commit; hosted `GET /api/roles` returns the labels and homes of §1.1.

#### B1 · Appeals (Wave 1)

- **Owns:** `api/cqa/review/**`, `api/cqa/routers/appeals.py`, `api/tests/test_appeals_v2.py`. Extends appeal payloads inside the router (wrapping `appeal_dict`), without editing `serializers.py`.
- **Scope:** §6.1 B1 rows (P1), registering new write routes with `register_route()`.
- **Acceptance:** tests for comment length, withdraw and edit (403 for another author, 409 once decided), the reason minimum, `violation_type` stored and used by `GET /api/violations`, `seen`, `close`; the existing appeal tests stay green.

#### B2 · Runs (Wave 1)

- **Owns:** `api/cqa/jobs/**`, `api/cqa/routers/evaluations.py`, `api/tests/test_runs_v2.py`.
- **Scope:** `empty_selection` (P0), run scope by dataset and filters with batches, actor in job params, `GET /api/jobs/{id}/summary` (P1); honest job statuses (a job where every dialog failed ends `failed`) and no two jobs on one dialog at once (SYS-04, P1).
- **Acceptance:** `conversation_ids: []` → 400 `empty_selection` and no job; a weight-only partial run writes no LLM trace rows and costs $0 (regression test); the job summary returns before and after totals for each dialog.

#### B3 · Conversations, identity and ingest (Wave 1)

- **Owns:** `api/cqa/routers/conversations.py`, `api/cqa/serializers.py` (additive keys only; `evaluation_dict` and `latest_evaluation` keep their behaviour because the results.json builder uses them), `api/cqa/ingest/**`, `api/tests/test_conversations_v2.py`.
- **Scope:** list filters, sort and fields (P1, with `sort` and `operator` → `agent_name` first), operator assignment, the «менің атым» pattern, recording-start anchoring for calls.
- **Acceptance:** C3 = Нет lists p1-ru-009, p1-kz-015 and p1-en-014 among others; `sort=total_asc` starts with p1-ru-018 (10,00); assigning p4-ru-002 to Нина lets Нина appeal it (the own-dialog check passes) and writes an audit event; after anchoring, p4-ru-002 turn №15 has an offset near 120 s.

#### B4 · Insights, coaching and notifications (Wave 1)

- **Owns:** `api/cqa/analytics/**`, `api/cqa/routers/{insights,analytics}.py`, `api/cqa/coaching/**` (new), `api/cqa/routers/{coaching,notifications}.py` (new), `api/tests/test_insights_v2.py`.
- **Scope:** violations fields and filters, selection by `is_critical` (P1); scope filters on overview, analytics and operators (P1); coaching notes, acknowledgements and notifications (P2).
- **Acceptance:** `GET /api/violations` items carry `rule_text`, `classified_by`, `related` for disclosure cases and `open_appeal_id`; a renamed critical criterion still feeds the register; notes need `team.manage` (403 for analyst); an acknowledgement on another operator's dialog returns 403.

#### B5 · Checklist governance and scoring preview (Wave 1)

- **Owns:** `api/cqa/checklist/**`, `api/cqa/routers/checklists.py`, `api/cqa/routers/scoring.py` (new), `api/tests/test_checklists_v2.py`; `api/cqa/scoring/engine.py` is read-only.
- **Scope:** `locked` and the regenerate refusal (P0); judge-spec compilation outside the write transaction so saving a version never blocks a running job (SYS-05, P0 for live ask 3); authors, usage counts, version conflict, short titles, scoring preview, compatibility (P1); impact (P2).
- **Acceptance:** regenerate on v1 → 409 `locked`; a stale `base_version_id` → 409 `version_conflict` naming the newer version; `POST /api/scoring/preview` returns 85,38 for contact Да, discovery N/A, identification N/A, consultation 15, empathy Да, dialogue 7, closure Да, and 50,63 for an unrounded 50,625; a version without `closure` is reported incompatible with JSON 1.0.

#### B6 · Admin: demo seed, readiness, hands-on mode (Wave 1)

- **Owns:** `api/cqa/admin/**`, `api/cqa/routers/{admin,system}.py`, `api/tests/test_admin_v2.py`.
- **Scope:** demo seed and readiness (P0); hands-on mode and settings keys (P1).
- **Acceptance:** running the seed twice creates no duplicates and never touches control dialogs or p1-ru-005 C6; readiness answers in under 5 s with no paid call and is all green on a seeded DEV instance; switching hands-on mode on takes the «pre-hands-on» snapshot and lowers the run cap to 10.

#### B7 · Roles (Wave 1)

- **Owns:** `api/cqa/security/**`, `api/cqa/routers/{roles,audit}.py`, `api/tests/test_roles_audit.py`.
- **Scope:** labels, homes and nav hints of §1.1 and §2.3; `team.manage`; the director `critical_only` constraint (P2).
- **Acceptance:** `GET /api/roles` returns «Руководитель QA», admin home `#/admin`, visitor home `#/overview`; the matrix test includes `team.manage`; a director appeal on a non-critical criterion returns 403.

### 7.4 Contracts published in Wave 0

```ts
// web/src/roles/registry.ts (W0b)
export type RoleCode = 'operator' | 'analyst' | 'reviewer' | 'lead' | 'director' | 'methodologist' | 'admin' | 'visitor';
export type Permission = 'read' | 'conversations.import' | 'evaluations.run' | 'appeals.create' | 'appeals.decide'
  | 'checklists.write' | 'settings.write' | 'calibration.write' | 'admin' | 'integrations.ingest' | 'team.manage';
export interface Actor { role: RoleCode; name: string | null }
export function useActor(): Actor & { setRole(role: RoleCode, opts?: { as?: string }): void; setName(name: string): void };
export function useCan(permission: Permission, ctx?: { ownerName?: string | null; critical?: boolean }): boolean;
export function useNavItems(): NavItem[];                       // ordered for the role, badges from useNavBadges()

// web/src/features/notifications/index.ts (W1)
export function useNavBadges(): Partial<Record<NavKey, { count: number; tone: 'neutral' | 'critical' }>>;
export function NotificationsBell(): JSX.Element;
// web/src/features/roles/index.ts (W1)
export function RoleSwitcher(): JSX.Element;
export function ScenariosButton(): JSX.Element;
export function IdentityPicker(p: { open: boolean; onOpenChange(open: boolean): void; onPicked?(name: string): void }): JSX.Element;

// web/src/features/card/index.ts (W2)
export interface ConversationCardProps {
  id: string; mode?: 'page' | 'embed' | 'readonly';
  initialCriterion?: string; initialTurn?: number; initialTime?: number; evaluationId?: number; onBack?(): void;
}
export function ConversationCard(p: ConversationCardProps): JSX.Element;
// web/src/components/domain (stubs by W0a, implemented by W2)
export interface TranscriptListProps {
  turns: Turn[]; evidence?: number[]; highlight?: { turnIdx?: number; span?: [number, number] }; range?: [number, number];
  groups?: boolean; follow?: boolean; currentTime?: number; compact?: boolean; onTurnClick?(turn: Turn): void;
}
export interface WaveformHandle { seek(seconds: number, play?: boolean): void; play(): void; pause(): void }
export interface WaveformPlayerProps {
  conversationId: string; highlight?: { start: number; end: number; label?: string };
  holds?: { start: number; end: number; label: string }[]; markers?: number[];
  variant?: 'full' | 'compact'; window?: [number, number]; onTime?(seconds: number): void;
}

// web/src/features/appeals/index.ts (W3)
export interface AppealDialogProps {
  open: boolean; onOpenChange(open: boolean): void; conversationId: string; channel: string;
  criterion: EvaluationCriterion; presetAnswer?: string | number | 'N/A'; presetTurns?: number[]; onCreated?(appealId: number): void;
}
export function AppealDialog(p: AppealDialogProps): JSX.Element;

// web/src/features/conversations/index.ts and web/src/features/jobs/index.ts (W6)
export interface EvaluateDialogProps {
  open: boolean; onOpenChange(open: boolean): void; conversationIds?: string[];
  scope?: { datasetId?: number; filters?: Record<string, string> }; versionId?: number;
  mode?: 'full' | 'partial'; baseVersionId?: number; onlyCodes?: string[]; title?: string; onStarted?(jobId: string): void;
}
export function EvaluateDialog(p: EvaluateDialogProps): JSX.Element;
export function JobIndicator(): JSX.Element;
export function useJobProgress(jobId: string | null): { status: string; done: number; total: number; failed: number; costUsd: number };
// web/src/features/export/index.ts (W6)
export function ExportResultsDialog(p: { open: boolean; onOpenChange(open: boolean): void;
  preset?: { groupId?: string; expect?: number; conversationIds?: string[] } }): JSX.Element;

// web/src/features/checklists/index.ts (W5)
export function ChecklistOverview(p: { checklistId?: number; versionId?: number; readOnly?: boolean }): JSX.Element;
// web/src/features/analytics/index.ts (W4)
export function ScopeBar(p: { fields: ScopeField[] }): JSX.Element;   // reads and writes the hash query
```

### 7.5 Definition of done (every package)

- RU, KK and EN strings in the package namespace; no raw keys; plurals correct.
- Light and dark checked; print light; no mascot unless the admin setting is on.
- 1280×720 and 1024×768 checked for the package's screens; no horizontal scroll; the primary answer of each screen above the fold.
- Keyboard focus visible; dialogs trap and return focus; states readable without colour.
- Loading, empty, error and offline states with a next step; API errors show the message and request id.
- `pnpm typecheck` and `pnpm build` (web) or the offline pytest suite (API) green; no edits outside the owned globs; mock handler updated for P0 screens.

### 7.6 Decisions for the team lead before Wave 1

1. Label `lead` as «Руководитель QA» (instead of «Руководитель группы»).
2. Admin home `#/admin`, guest home `#/overview`.
3. Demo profile names per role (§1.1), or empty names until typed.
4. A role picker dialog on the first open of an app route (proposed) rather than a silent QA-analyst default.
5. «Использовать как пример для ИИ» off by default for every decision on the hosted instance.
6. Hero card on p1-ru-005 (neutral) rather than p1-ru-029 (insult).
7. Director «Запросить подтверждение» kept as P2 or dropped.
8. Sidebar label «Нарушения» with the page title «Грубые нарушения».
9. Guest by default plus access codes for public links (DEMO-18) after the demo, not before.

---

## 8. Risks

| # | Risk | Effect if it happens | Mitigation | Owner |
|---|---|---|---|---|
| 1 | The backend working tree is not deployed in time (today `GET /api/roles`, `/api/version`, `/api/overview` are 404 on the hosted API) | role enforcement, overview, scorecards, audit and guards missing on the demo | B0a is the first task of the night; the web feature-detects `GET /api/roles` and falls back to its own registry; appeal and decision bodies carry author fields the deployed API already accepts; screens that need a missing endpoint say «доступно после обновления API» instead of erroring | team lead |
| 2 | Self-declared roles: «а кто угодно может выбрать ревьюера?» | the jury doubts the process | say it plainly: the organizers asked for no login and separate actions; every action is signed and audited; production takes identity and scope from Dynamics 365 / Entra ID (D365-06); optional access codes for public links (DEMO-18) | presenter |
| 3 | Operators' «own dialogs» is a UI filter (`read` is global in the API) | an operator can open another dialog by URL | the other dialog is read-only with «Диалог другого оператора» and the API refuses the appeal (403 `not_your_dialog`); production scopes reads server-side | W3, B7 |
| 4 | Hands-on jurors change the shared instance (appeals, a v2 activated, re-evaluations) | the next juror or the presenter sees a different state; numbers drift | no deletes anywhere; snapshot `before-jury` and a typed restore; hands-on mode caps (run size 10, its own budget, few-shot off); withdraw for the team; v1 locked and results files built from v1 AI answers; readiness check after every session; rehearsals on a copy, never on the hosted instance (DEMO-08) | B6, W7, team lead |
| 5 | The plan is larger than the remaining hours | half-finished screens on stage | P0 slice and the cut order of §7.2; each package ships P0 before P1; WI integrates whatever is green at T0 + 5 h | team lead |
| 6 | Merge conflicts between ten parallel agents | broken builds late at night | Wave 0 splits the shared files (routes, i18n namespaces, API modules, mock handlers) and freezes them; disjoint globs; small rebased commits; WI as the only editor of leftovers | W0b, WI |
| 7 | The live-ask order breaks a demo state (appeal on C7, then a partial re-evaluation re-judges C7) | an open appeal points at a superseded evaluation | the working-tree runner already carries decided appeals on unchanged criteria; open appeals on re-judged criteria are marked stale with «Закрыть» (B1); rehearse the order ask 2 → ask 3 with different criteria | B1, presenter |
| 8 | The weight-only and empty-selection bugs return on the hosted API | a reweight costs about $14 and flips verdicts; an empty selection evaluates every dialog during the control hour | deploy the working-tree fix for weight-only runs; B2 adds `empty_selection` with a regression test; the Apply dialog always shows the dry run first; saving a version while a job runs must not lock the database and every job ends with an honest status (SYS-04, SYS-05) | B2, B5, B0 |
| 9 | The call path misleads: p4-ru-002 has no assigned operator and its audio starts about 30 s after the transcript | an evidence jump plays the wrong moment; the operator cannot appeal the call | the chat path p1-ru-005 is the primary demo; B3 anchors offsets on the recording-start event and the QA lead assigns the call (OPR-06) before the backup path is shown | B3, W2 |
| 10 | Live numbers on the landing change after rehearsals or juror runs (9 violations instead of 8) | the presenter's script disagrees with the page | numbers always come from the API with an «по данным API на …» stamp; the script says «на момент показа»; a snapshot restore resets the instance | W1, presenter |
| 11 | Dynamics 365 specifics: CSP, the 5 MB web resource limit, blocked storage, narrow panes; no validation against a real org | the embedded card fails at a customer | single-file embed build under 4,5 MB checked in CI, storage in try/catch, light theme and no mascot forced, host density, container-aware layout down to 360 px; say «проверено на примере, не на рабочей организации» (INTEGRATION_D365 §8); production restricts `CORS_ORIGINS` to the web origin and the D365 org (SYS-07) and adds retention and local-only processing rules (ADM-10) | W0b, W7 |
| 12 | KK translations are machine-assisted | awkward Kazakh in front of a Kazakh jury | the i18n check blocks missing keys; a native speaker on the team reviews the landing, the card, the appeal flow and the operator portal first; missing KK falls back to RU with a small «RU» marker | WI |
| 13 | Brand greens reduce contrast | unreadable text on the projector | token rules: bright greens never carry text, green text is #1F7028, warning text #9A5C06, secondary text #5B675F; contrast checks in W0a acceptance; presentation mode at 112 % | W0a |
| 14 | The mascot sneaks back through an old component or the single-file build | the organizers' guidance is broken on stage | one `BrandMascot` gate, a grep check in CI, `VITE_MASCOT=0` for the D365 build, the default `mascot_enabled = false` | W0a, W0b |
| 15 | A cold start or a slow venue network makes the first click hang | the product looks broken | skeletons and «Сервер просыпается…» after 3 s, retries, the readiness check warms the demo cards, the local Docker app and `?api=` as the fallback, an honest mock badge (DEMO-15) | W0a, WI |
| 16 | Namesakes and greeting-only identity (Нина / Nina, 10 unassigned dialogs) | the wrong operator appears in «Мои оценки» or cannot appeal | language chips in the picker, assignment by the QA lead writing `agent_name`, the operators registry later (OPR-08) | B3, W1 |
| 17 | Time zones mix UTC and UTC+5 | «Решение в 13:52» looks wrong next to the clock | every time shown in local time with the zone; the API returns offsets (SYS-08) | W0b |
| 18 | Notification polling adds load during the control hour | slower job progress | 30 s polling with back-off to 2 min and pause in hidden tabs; the control run itself is CLI-first | W1 |
| 19 | The landing claims something the system does not do | credibility loss on the business check | every claim maps to a working screen or is labelled («опция», «допущение», «по состоянию на»); the local-model line reads its state from `GET /api/health`; costs come from measured data (EXP-03) | W1 |
| 20 | Work on the UI disturbs the results submission | a broken or late `results/prod/results.json` | no package touches `results/**`, `api/cqa/export/**`, `api/cqa/judge/**` or `scripts/control_run.py`; UI freeze at H + 21; no deploy during the control hour; the control run stays with the team lead (DEMO-09, DEMO-11); the secret and personal-data scans before pushes stay with the team lead (EXP-07, EXP-08); a clean clone of the stop-code commit must still start with the README commands (SYS-01) | team lead |
