# CQA design proposal: win the live demo

> **Superseded for C1 on calls (2026-11-08).** Wherever this proposal offers N/A for C1 on a call whose greeting is not in the recording, read: C1 has no N/A. The opening outside the recording is credited by a code rule, with the badge «Засчитано правилом: начало звонка вне записи». See `docs/ORGANIZER_UPDATES.md` §5 and `docs/FRONTEND_INTEGRATION.md` «Calls».

Angle: optimise for the four live asks, a few minutes of hands-on access and the jury criteria (adequate and reasoned scores, a complete process, usability, extras, local/cloud balance). Smallest set of changes with the biggest visible effect; everything else is cut on purpose. A corporate landing explains the product in ten seconds and leads into the app.

Status: proposal for the team lead, written 2026-11-07 ≈ 20:30 (UTC+5), ≈ H+6.5 if H+0 was ≈ 14:00. Binding source: `docs/ORGANIZER_UPDATES.md`. Out of scope by design: judge prompts, gold set, harness and local-model setup (ML teammate), and the results.json builder, CLI, endpoint and control-run script (team lead, in progress); this proposal only adds the UI entry point for the latter.

Inputs read: `docs/ORGANIZER_UPDATES.md`, `docs/USER_STORIES.md` (all 223 stories), `docs/design/organizer-mockup.png`, `docs/ARCHITECTURE.md`, `docs/ARCHITECTURE_V2.md`, `docs/ARCH_CONTRACTS.md`, `docs/WEB.md`, `docs/API.md`, `docs/DEMO.md`, `docs/PLAN.md`, `docs/DATASET_NOTES.md`, the Figma Landing frame `91:238` (read-only), `web/src` (unchanged since `810c3b5`), `api/` (HEAD `99a1c9e` plus the uncommitted working tree), and read-only GETs against the hosted API at ≈ 20:30.

Facts this proposal is built on (verified 07.11 ≈ 20:30):

- Hosted API: `/api/violations` returns 8 items (insult 1, paid_bypass 4, disclosure 3, other 0); `/api/operators` 56 rows; `/api/appeals` 0 items; `/api/datasets` one DEV import (65); `/api/health` ok, local model not configured; `/api/version`, `/api/roles`, `/api/overview` answer 404 (the roles, guards, overview, snapshots and governance packages exist only in the working tree). A CORS preflight with `x-cqa-role, x-cqa-user` is accepted.
- Demo dialogs: p1-ru-029 = 51,25 (C3 N/A, C8 Да, insult at turn №10); p1-ru-005 = 78,46 (operator Нина, C6 = 4 of 10 → 9,23 / 23,08; changing it to 7 gives 85,38); p1-ru-023 = 100,00 with C8 Да; p4-ru-002 = 80,50 (call, 10:20, C4 15 / 20 and C6 10,5 / 15 like the organizer mockup, C1 Нет although the recording starts only at turn №5, about 30 s after the first transcript line).
- Summary: 65 dialogs, mean 82,04, median 95,00, RU 78,56 (35) / KZ 85,55 (15) / EN 86,62 (15), chats 82,91 (60) / calls 71,53 (5), $16,22 over 75 evaluations ≈ $0,22 per evaluation, ≈ 14,9 s latency.
- The judge sees 0-based labels (`pack_transcript` writes `[idx]`), so explanations cite «реплика [9]» while results.json `evidence_turns` say 10 and the UI shows `[9]`.
- The web app is dark by default (`index.html` ships `class="dark"`), shows the mascot in 7 files, knows two roles, sends N/A decisions as `null` (400), defaults the few-shot switch to on, numbers new criteria `C${n+1}` (duplicates after a delete), applies a new version to every dialog, and drops the call N/A rule when the call instruction of C1 is edited (`CriterionEditor.tsx:113`).

## The demo in one screen

The jury spends 5–10 minutes: the guide's 5-minute defence, then the four live asks, then possibly hands-on access. The backend already answers almost every ask; the web app does not show it. So nearly all the visible value per hour is in `web/`.

| Live ask (ORG §3) | Path after this proposal | Clicks | Today | Stories |
|---|---|---|---|---|
| 1. All dialogs with gross errors and why | Sidebar «Нарушения 8» → `#/violations`: type chips 1 / 4 / 3, each row with explanation and the quoted turn → «Открыть в диалоге» lands on C8 with the turn in peach | 1 (+1 per dialog) | no page; a checkbox on the list, then card by card, no type, no quote | GV-01, GV-02, GV-03, CARD-04 |
| 2. How an operator sends a result to appeal | Role card → «Оператор» → «Нина» → p1-ru-005 → «Оспорить оценку» (C6 preselected, proposed 7, preview 78,46 → 85,38) → «Отправить»; role «Ревьюер» → «Апелляции» (badge +1) → «Изменить → 7» with reason → «Сохранить»; role «Оператор» → «Мои апелляции» shows the decision | ≈ 12 + two typed fields, ≈ 90 s | no operator role; analyst appeals as a translated label; N/A decisions fail with 400 | SHELL-04, SHELL-05, OPR-01, OPR-02, OPR-04, OPR-05, APL-01, APL-05, APL-07, APL-08, APL-09, DEMO-02 |
| 3. Change the checklist: add, change, reweight | «Чек-листы» → active version → «Создать версию на основе v1» → «Добавить критерий» (C9) or «Заменить» or weight stepper with Σ and «Нормализовать до 100» → «Сохранить как v2» → «Применить к диалогам» (3 dialogs, partial, dry run «1 критерий · 3 вызова · ≈ $0,07» or «0 вызовов · $0,00») → table old → new totals | ≈ 10, ≈ 2 min incl. the job | ≈ 9 steps; duplicated codes; Apply re-evaluates all 65; every save activates | CHK-01..05, CHK-10, CHK-12, CHK-15, CHK-16, SYS-06, DEMO-05 |
| 4. Results of a specific dialog with the justification | Any row → «Анализ разговора»: ring «51,25 / 100», criteria rows «earned / weight», «Почему снижена оценка», «Перейти к фрагменту», «Оспорить оценку», «Как посчитан итог», 1-based turns, peach evidence; for calls the two-track waveform | 1 | dark card, 0-based numbers, no reason summary, no breakdown | CARD-01..07, CALL-01, CALL-02 |
| Hands-on (ORG §3.5) | «Сценарии для жюри» in the sidebar: 7 one-click scenarios that switch the role and open the target; safe defaults; no dead ends | 1 per scenario | none | DEMO-01, DEMO-04, SHELL-07 |

| Jury criterion | What they see | Where |
|---|---|---|
| Closeness to experts (per criterion), adequacy | Per-criterion answer with its scale anchor, N/A only where the checklist allows it and always with a reason, quotes checked against the transcript, «Как посчитан итог» that recomputes the total by hand, results.json validated in the UI | card, export dialog |
| Process completeness | import → evaluate → card → operator appeal → reviewer decision → recalculated total → checklist change → partial re-apply → export | jury panel, sidebar order |
| Usability | one click per ask, role card always visible, everything readable at 1280×720, empty and error states with a next step | everywhere |
| Interesting extras | two-track waveform with jump to the moment, Trace of every model call, partial re-evaluation with a cost estimate, weight-only change at $0, PII masking, Dynamics 365 embed, results.json check in the UI | card, studio, settings |
| Local / cloud balance | provider badge on every card, «Модель и стоимость» with cloud, local and ensemble and their honest status, a live local evaluation if the ML teammate's endpoint is up | card, settings, evaluate dialog |
| Corporate look | light theme by default, deep-green sidebar, white cards, no mascot, landing with real product UI and live numbers | all |
| New dialogs really analysed | paste → «Создать и оценить» → card in ≈ 20 s, Trace timestamps from that minute | conversations, card |

Scope: one web foundation package, seven parallel web feature packages, one small backend package, one readiness package (§7). Cut on purpose: calibration UI, audit and admin consoles, the control-hour console, teams and coaching, notifications, phone layouts of the app, knowledge-base editor, archive and rollback, manual evaluation, extra Dynamics 365 views (full list in §7.6).

## 1. Role model

### 1.1 Roles shown to the jury

No login. Seven roles exist in the UI; six are in the switcher, the administrator is reachable only by URL so a juror cannot change system settings by accident. Codes and labels follow `api/cqa/security/roles.py` (the UI reads `GET /api/roles` when it is deployed and falls back to the same built-in list).

| Role (code) | RU / EN label | Home | Represents | Used in the demo for |
|---|---|---|---|---|
| `operator` | Оператор / Operator | `#/my` | support operator, sees own work | live ask 2 (files the appeal) |
| `analyst` | QA-аналитик / QA analyst | `#/conversations` | QA specialist | default role; asks 1, 4, new dialog, export |
| `reviewer` | Ревьюер / Reviewer | `#/review` | senior QA who decides appeals | live ask 2 (decides) |
| `methodologist` | Методолог / Methodologist | `#/checklists` | owner of checklists | live ask 3 |
| `lead` | Руководитель группы / Team lead | `#/analytics` | QA or team lead | overview, appeals, checklists |
| `director` | Директор / Director | `#/analytics` | executive, read-only | «corporate» view, risk tile |
| `admin` | Администратор / Administrator | `#/settings` | IT, the team itself | settings, mascot toggle, snapshots; only via `?role=admin` |

`visitor` and `demo` exist in the backend but are never offered: `demo` is what a request without a role header gets, `visitor` is reserved for a later read-only share link (DEMO-18, cut).

### 1.2 Choosing a role without login (SHELL-04)

- The role card sits in the sidebar footer: initials tile, role label, the person's name if set, chevron. Click opens the role menu: six roles, each with a one-line description and its home, the current one checked.
- First open with no stored role: role = `analyst`. No blocking picker on app routes; the landing offers role entries instead (§3).
- URL: `?role=<code>` (inside the hash query, e.g. `#/my?role=operator&as=Нина`) sets the role and `?as=<name>` sets the operator identity; both are persisted and then removed from the address bar (`history.replaceState`). Unknown role: ignored, toast «Роль «xyz» не найдена». Unknown operator: the picker opens with «Оператор «xyz» не найден».
- Persistence: `localStorage` `cqa.role`, `cqa.user` (operator name), `cqa.displayName` (signature for other roles), each read and written in try/catch (storage blocked → session state only).
- Switching the role navigates to the new role's home unless the current route is allowed for it; open dialogs close; an unsaved checklist draft asks «Есть несохранённые изменения. Уйти?» (P1).
- Requests: `X-CQA-Role` and `X-CQA-User` (`encodeURIComponent`) are sent on writes only (POST/PUT/PATCH/DELETE). Reads stay header-free, so they are not preflighted (the API is cross-origin on Railway) and run as `demo` with read permission. Bodies keep `author_role`, `author_name`, `reviewer_role`, `reviewer_label` too, so attribution works on the API that is deployed now (no middleware yet) and on the next one (headers authoritative).
- Capability probe: on start the app calls `GET /api/version`; 404 means the working-tree backend is not deployed yet, and the UI uses the built-in role list, hides «Сохранить как черновик» (the old API activates every version) and falls back from `/api/overview` to `/api/analytics/summary`.

### 1.3 Operator identity (SHELL-05, OPR-01)

- Choosing «Оператор» without a stored name opens «Кто вы?»: names from `GET /api/operators` without the «—» group, searchable (case-insensitive, Kazakh letters included: «мұр» finds Мұрат), each line «Нина · RU · 1 диалог». «Отмена» returns to the previous role.
- After the choice the role card reads «Оператор · Нина» with «Сменить». A hover note: «Демо без входа: выбор пользователя фильтрует данные и подписывает апелляции; в Dynamics 365 пользователь берётся из учётной записи».
- Other roles may set a display name in the role menu («Ваше имя для подписи», ≤ 128 characters). Without it, signatures use the role label.

### 1.4 Permission matrix (UI, mirrors the backend permissions)

Legend: **Y** allowed · **Own** own dialogs only · **R** read-only · **—** not rendered (typed URLs open read-only with «Раздел доступен роли …»).

| Capability | Оператор | QA-аналитик | Ревьюер | Методолог | Руководитель | Директор | Админ |
|---|---|---|---|---|---|---|---|
| Dialog list and cards | Own | Y | Y | Y | Y | R | Y |
| Gross-violations register | — | Y | Y | Y | Y | R | Y |
| File an appeal («Оспорить оценку») | Own | Y | Y (spot check) | — | Y | — | Y |
| Decide an appeal | — | — | Y | — | Y | — | Y |
| Import, upload, paste | — | Y | — | Y | Y | — | Y |
| Evaluate, re-evaluate | — | Y | Y | Y | Y | — | Y |
| Read the checklist | R | R | R | Y | Y | R | Y |
| Add, change, replace, reweight; save version; activate | — | — | — | Y | Y | — | Y |
| Apply a version to dialogs | — | Y | — | Y | Y | — | Y |
| Export CSV | — | Y | Y | Y | Y | Y | Y |
| Export and validate results.json | — | Y | — | — | Y | — | Y |
| Analytics and overview | own summary | Y | Y | Y | Y | Y | Y |
| Personal settings: theme, language, name | Y | Y | Y | Y | Y | Y | Y |
| System settings: model, PII masking, few-shot, mascot | R | R | R | R | R | R | Y |
| «Сценарии для жюри» | Y | Y | Y | Y | Y | Y | Y |

Server side the same rules are enforced by the role middleware once it is deployed (B-0); until then they are UI rules, and the budget, run-size and concurrency guards are what protects the shared instance.

### 1.5 Attribution of appeals and decisions (APL-01, APL-05, APL-09)

| Event | Stored | Shown |
|---|---|---|
| Appeal | `author_role`, `author_name` (operator name or display name), `author_label` (legacy) | «Апелляция · Оператор · Нина · 07.11, 21:40» and the comment; a legacy appeal without a role reads «Автор не указан» |
| Proposed answer | first line of the comment «Предлагаемый ответ: 7» (P0); `requested_answer` field (P1, B-4) | «Оператор просит: 7 из 10» on the review card |
| Decision | `reviewer_role`, `reviewer_label` (display name or role label), action, new answer, reason | on the criterion: «Изменено ревьюером · Айгерим · 07.11, 21:44 · 4 → 7 · <причина>»; on the ring: final total with the original struck through and «после апелляции» |
| Four-eyes (P1) | — | when the reviewer's name equals the appeal author's: «Вы оспариваете и решаете сами: передайте апелляцию коллеге», second confirmation |
| Audit (after B-0) | `audit_events` with role, name, request id | not built in the UI (cut); Swagger `/api/audit` is enough to answer a question |

### 1.6 What the jury sees on first open

1. `https://cqa-freedom-pi.vercel.app` → `#/` landing, light, no mascot. At 1280×720 without scrolling: header with «Открыть демо», the headline, the subline naming RU/KZ/EN, chats and calls, appeal and review, two buttons, a live product card (ring 51,25, three criteria rows, «Почему снижена оценка»), the top of the live counters.
2. «Открыть демо» → `#/conversations` as QA-аналитик (or «Продолжить как <роль>» for a returning visitor). Deep-green sidebar: Диалоги (active) · Нарушения 8 · Чек-листы · Апелляции · Аналитика; Настройки and «Сценарии для жюри» lower; role card at the bottom.
3. On the first app visit the «Сценарии для жюри» drawer opens once (right side, 400 px, never over the page header actions); closing it is remembered.
4. A deep link (README, slide, DEMO.md) opens its screen directly; only `#/` exactly shows the landing.

## 2. Information architecture

### 2.1 Routes (hash router)

| Route | Screen | Roles | Parameters | Status | Package |
|---|---|---|---|---|---|
| `#/` | Landing | public, no app shell | — | new | WP-7 |
| `#/conversations` | Диалоги | all but Оператор | `q`, `language`, `channel`, `critical=1`, `paste=1` (opens «Вставить диалог») | changed | WP-2 |
| `#/conversations/:id` | Анализ разговора (card) | all; Оператор only own (others read-only) | `criterion=C8`, `turn=10` (1-based), `t=120` (seconds, calls), `tab=history` / `trace` / `audio` | changed | WP-1 |
| `#/violations` | Грубые нарушения | all but Оператор | `type=insult` / `paid_bypass` / `disclosure` / `other`, `language`, `channel` | new | WP-3 |
| `#/review` | Апелляции | decide: Ревьюер, Руководитель, Админ; others read | `status=open` / `decided` | changed | WP-4 |
| `#/review/:appealId` | one appeal, expanded | as above | — | new, P1 | WP-4 |
| `#/my` | Мои оценки | Оператор (other roles get the identity picker) | `tab=evaluations` / `appeals` | new | WP-3 |
| `#/my/appeals` | Мои апелляции (alias of `#/my?tab=appeals`) | Оператор | — | new | WP-3 |
| `#/checklists` | Чек-листы | all; write: Методолог, Руководитель, Админ | — | changed | WP-5 |
| `#/checklists/:id/versions/:vid` | version editor, read-only view for other roles | all | `apply=1` (opens Apply), `diff=<vid>` | changed | WP-5 |
| `#/analytics` | Аналитика with the «Обзор» band on top | all but Оператор | `language`, `channel` (P1) | changed | WP-6 |
| `#/overview` | alias → `#/analytics` (the backend nav item points here) | Руководитель, Директор | — | new alias | WP-0 |
| `#/settings` | Настройки | all; system sections read-only except Админ | `section=appearance` / `profile` / `model` / `export` | changed | WP-6 |
| `*` | «Страница не найдена» with links to the role home and the landing | all | — | new | WP-0 |
| `index.html?embed=1&conversation=<id>` | embedded card for a Dynamics 365 form tab | read-only | `criterion`, `turn`, `api`, `lang` (P1) | changed | WP-1 |

Global parameters on any route: `role`, `as` (operator name), `tour=1` (opens «Сценарии для жюри»), `api` (API base override, exists), `mock=1` (offline fixtures, exists; shows a persistent «Офлайн-демо» badge).

### 2.2 Navigation per role

The sidebar follows the mockup: deep green, «CQA» at the top, line icons, the active item as a lighter green pill. The mockup's four items keep their order; «Нарушения» goes right after «Диалоги» because it is a list of dialogs too. «Сценарии для жюри» and «Настройки» sit in the lower group above the role card.

| Role | Upper group, in order | Badges |
|---|---|---|
| Оператор | Мои оценки · Мои апелляции · Чек-лист | Мои апелляции: decisions not yet opened (local read state) |
| QA-аналитик | Диалоги · Нарушения · Чек-листы · Апелляции · Аналитика | Нарушения (total), Апелляции (open) |
| Ревьюер | Апелляции · Диалоги · Нарушения · Чек-листы · Аналитика | Апелляции, Нарушения |
| Методолог | Чек-листы · Диалоги · Нарушения · Аналитика | Нарушения |
| Руководитель | Аналитика · Диалоги · Нарушения · Апелляции · Чек-листы | Апелляции, Нарушения |
| Директор | Аналитика · Нарушения · Диалоги · Чек-листы | Нарушения |
| Админ | Диалоги · Нарушения · Чек-листы · Апелляции · Аналитика | Апелляции, Нарушения |

Icons (lucide, 20 px, stroke 1.75): Диалоги `MessageSquareText`, Нарушения `ShieldAlert`, Чек-листы `SquareCheckBig`, Апелляции `Scale`, Аналитика `ChartColumn`, Мои оценки `ClipboardCheck`, Мои апелляции `Scale`, Сценарии для жюри `PlayCircle`, Настройки `Settings`. Badge: pill 20 px, `rgba(255,255,255,.16)` with white 12/16 600 digits.

### 2.3 Deep links (jury panel, `docs/DEMO.md`, slides)

| Purpose | Link |
|---|---|
| Live ask 1 | `#/violations`, filtered example `#/violations?type=disclosure&language=KZ` (only p1-kz-015) |
| Live ask 2, operator | `#/my?role=operator&as=Нина`, then `#/conversations/p1-ru-005?criterion=C6` |
| Live ask 2, reviewer | `#/review?role=reviewer` |
| Live ask 3 | `#/checklists?role=methodologist` (the jury panel resolves the active version and opens `#/checklists/1/versions/<id>`) |
| Live ask 4 | `#/conversations/p1-ru-029?criterion=C8&turn=10` |
| Call with jump to the moment | `#/conversations/p4-ru-002?criterion=C6` |
| New dialog live | `#/conversations?paste=1&role=analyst` |
| Executive view | `#/analytics?role=director` |
| Dynamics 365 embed | `index.html?embed=1&conversation=p1-ru-029` |

### 2.4 Shell layout

```
┌────────────────┬─────────────────────────────────────────────────────────────────┐
│ CQA            │ ← Диалоги                                                       │
│ Freedom Broker │ Анализ разговора                          [Экспорт PDF] [ ⋯ ]  │
│                │ Чат · p1-ru-029 · Русский · оператор Алексей · 14 реплик       │
│ ▣ Диалоги      │ ┌──────────────────────────┐ ┌────────────────────────────────┐ │
│ ◆ Нарушения  8 │ │ Расшифровка            ⌕ │ │ Оценка качества   Оценка ▾     │ │
│ ▤ Чек-листы    │ │ До оператора · 9 реплик ▾│ │ ▌Грубое нарушение: оскорбление │ │
│ ◇ Апелляции  1 │ │ бот и системные события  │ │  ╭─────╮ ✓ Установление  12,5/12,5│
│ ▥ Аналитика    │ │░01:23  Оператор     №10 ░│ │  │51,25│ ✕ Выявление потр.  0/18,75│
│                │ │░Вы вообще читать умеете?░│ │  │/ 100│ … 8 rows grouped by S1–S4 │
│                │ │ 01:52  Клиент       №11  │ │  ╰─────╯ Почему снижена оценка   │
│ ▷ Сценарии     │ │ …                        │ │ [Перейти к фрагменту] [Оспорить]│
│ ◎ Настройки    │ └──────────────────────────┘ └────────────────────────────────┘ │
│ (QA) QA-аналит.│                                                                 │
│ RU · KK · EN ● │                                                                 │
└────────────────┴─────────────────────────────────────────────────────────────────┘
```

- No top bar: at 1280×720 a projector leaves ≈ 620 px of height, and every pixel goes to content (the mockup has none either). Language switch, connection dot (green, red when `/api/health` fails twice), role card and «Сценарии для жюри» live in the sidebar.
- Sidebar 240 px; below 1280 px it collapses to 72 px icons with tooltips (auto, toggle remembered); content padding 32 px (24 px below 1280), max width 1440 px.
- Page header (all screens): optional back link, title 32/38 700, grey subtitle with « · » separators, actions on the right. Detail pages put their actions there; there is no second toolbar.
- Below 768 px the app shows the same pages stacked without horizontal scroll; phone-grade layouts of the app are cut (SHELL-11, SHELL-16). The landing is fully responsive.

## 3. Landing page (`#/`)

### 3.1 Principles

- Ten-second read: what (AI evaluation of every operator dialog against the company checklist), for whom (QA, leads, directors, operators), why trust it (each score has an explanation and the exact message; disputes go through appeal and review), where to click.
- Structure from the Figma Landing frame (`91:238`: floating nav and hero with a product window and «works with» chips → numbered problem chapters with a timeline → three-step «how it works» → feature chapters with product UI and bullets → languages with chips → «it keeps quiet» privacy chapter → comparison table → FAQ accordion → gradient CTA panel → footer). Rendered corporate: light sections alternating with two deep-green bands (hero, final CTA panel); product UI instead of characters; restrained green gradients only inside the deep-green bands; no glow, no numerals as decoration larger than the section title.
- Real product, not pictures: the hero card, the feature crops and the counters are live React components fed by the API; each has a bundled snapshot (`features/landing/snapshot.json`, synthetic chats only, no call transcript) used when the API is slow (> 3 s) or down, stamped «по состоянию на …».
- Truthful claims only: numbers come from the API or carry «допущение»; the local model is «поддерживается» until the stand reports it reachable; no accuracy percentage until the ML teammate publishes a gold-set comparison.
- No mascot on the landing, whatever the setting.

### 3.2 Sections

| # | Section (Figma source) | Background | Content | Visual | Data | Calls to action |
|---|---|---|---|---|---|---|
| 0 | Header (floating pill nav) | white, sticky, 64 px, bottom border `#E3E8E3` | «CQA» wordmark + «Freedom Broker»; anchors Как это работает · Возможности · Языки · Безопасность · FAQ; RU · KK · EN | — | — | «Открыть демо» (primary) |
| 1 | Hero (hero + product window + «works with») | deep-green band `#0F5431 → #092818` (vertical, subtle) | eyebrow, H1 ≤ 12 words, subline, two buttons, reassurance line, chips row «Работает с» | live `QualityScoreCard` of p1-ru-029 (ring 51,25, three criteria rows, «Почему снижена оценка», both buttons) overlapping a `WaveformBars` strip of p4-ru-002; white cards, radius 16, soft shadow | `GET /api/conversations/p1-ru-029/evaluation`, `GET /api/audio/p4-ru-002/peaks?buckets=120` | «Открыть демо» → role home (default `#/conversations`); «Сценарии для жюри» → `#/conversations?tour=1`; text link «Как это работает» → anchor; the card itself → `#/conversations/p1-ru-029` |
| 2 | Live counters (new, between the hero and the chapters) | white | five figures with an as-of stamp | numbers in 32/38 700 tabular | `GET /api/analytics/summary` | each figure links: dialogs → `#/conversations`, violations → `#/violations`, cost → `#/settings?section=model` |
| 3 | Problem (chapters 01–03) | `#F5F7F5` | three numbered cards «01 / 02 / 03» | line icon per card, no illustration | — | — |
| 4 | How it works (chapter 04, steps 1–3) | white | five steps in a row, connected by a thin line | each step a small real UI crop (import dialog, criterion row, evidence chip, appeal dialog, decision preview) | — | «Посмотреть на примере» → `#/conversations/p1-ru-029` |
| 5 | Features (chapters 05–06) | `#F5F7F5` | three chapters alternating left/right: text with three or four bullets, real UI on the other side | live crops: the card, the checklist weights panel, the violations register | `GET /api/violations` (top 3 rows), `GET /api/checklists` | «Открыть пример →» per chapter |
| 6 | Who it is for (new, LAND-03) | white | four audience cards with «Открыть как …» | role icon tile | — | role entry links (§2.3) |
| 7 | Languages and channels (chapter 07) | white | text + three evidence chips, one per language; marquee row «Оценено · Бағаланды · Evaluated» | three evidence chips (one RU, one KZ, one EN quote from DEV chats) | a fixed trio of neutral quotes from synthetic DEV chats in the snapshot (never a gross-violation quote) | — |
| 8 | Data under control (chapter 08 «It keeps quiet») | `#F5F7F5` | three bullets + «Модели и стоимость» card | white card with model rows and status dots | `GET /api/settings`, `GET /api/health`, summary providers | «Как устроена оценка» → `#/conversations/p1-ru-029?tab=trace` |
| 9 | Comparison (compare table) | white | table «Вручную / CQA», 7 rows | CQA column header as a deep-green pill | cost row live | — |
| 10 | FAQ (accordion) | `#F5F7F5` | 10 questions | accordion rows, radius 14 | cost answer live | — |
| 11 | Final CTA (gradient panel) | white page, deep-green panel radius 24 with a soft radial lift `#2F8F2A` at 20 % | headline, subline, two buttons | — | — | «Открыть демо», «Оценить свой диалог» → `#/conversations?paste=1&role=analyst` |
| 12 | Footer | `#092818`, white text | tagline, three link columns, build line | — | `GET /api/version` (after B-0) and the web build commit | links (§3.3) |

Layout: content width 1200 px, section padding 96 px top and bottom (64 px below 1024 px, 48 px at 375 px). Hero two columns 6/6 at ≥ 1024 px, stacked below; counters 5 across, 2 per row at 375 px; comparison table scrolls inside its card at 375 px (never the page); FAQ single column.

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ CQA  Freedom Broker   Как это работает  Возможности  Языки  FAQ   RU KK EN [Открыть демо] │
├────────────────────────────────────────────────────────────────────────────────┤
│▓ Freedom Broker · контроль качества обслуживания                              ▓│
│▓ Оценка каждого диалога по чек-листу —       ┌ waveform p4-ru-002 ─────────┐   ▓│
│▓ с обоснованием, цитатой и апелляцией        │ Клиент  ▌▌▌▍▌▌▌░░░▌▌▌      │   ▓│
│▓ CQA проверяет чаты и звонки на русском,     ┌─────────────────────────────┐ ▓│
│▓ казахском и английском. По каждому…        │ Оценка качества              │ ▓│
│▓ [Открыть демо] [Сценарии для жюри]          │ (51,25)  ✕ Выявление 0/18,75 │ ▓│
│▓ Без входа · DEV уже оценён · модель и       │  / 100   ✕ Эмпатия   0/18,75 │ ▓│
│▓ стоимость видны в карточке                  │ Почему снижена оценка …      │ ▓│
│▓ Работает с: Русский · Қазақша · English · Чаты · Звонки с записью          ▓│
├────────────────────────────────────────────────────────────────────────────────┤
│ 65 диалогов оценено │ RU 35 · KZ 15 · EN 15 │ 60 чатов и 5 звонков │ 8 нарушений │ ≈ $0,22 │
└────────────────────────────────────────────────────────────────────────────────┘
```

### 3.3 Copy deck (RU is the source; EN ships in the same commit; KK is translated in `i18n` and checked by a Kazakh speaker on the team)

**Header.** RU: «Как это работает · Возможности · Языки · Безопасность · FAQ · Открыть демо». EN: «How it works · Features · Languages · Security · FAQ · Open the demo».

**Hero.**

| Element | RU | EN |
|---|---|---|
| Eyebrow | Freedom Broker · контроль качества обслуживания | Freedom Broker · service quality control |
| H1 (10 words) | Оценка каждого диалога по чек-листу — с обоснованием, цитатой и апелляцией | Every conversation scored against the checklist — with reasons, quotes and appeals |
| Subline | CQA проверяет чаты и звонки на русском, казахском и английском. По каждому критерию — ответ, баллы, объяснение и реплика, на которой основан вывод. Оператор оспаривает оценку, ревьюер принимает решение, итог пересчитывается автоматически. | CQA reviews chats and calls in Russian, Kazakh and English. Every criterion gets an answer, points, an explanation and the exact message it rests on. Operators appeal, reviewers decide, and the total is recalculated automatically. |
| Primary / secondary | Открыть демо · Сценарии для жюри | Open the demo · Jury scenarios |
| Returning visitor | Продолжить как QA-аналитик | Continue as QA analyst |
| Reassurance | Без входа в систему · данные DEV уже оценены · модель и стоимость каждой оценки видны в карточке | No sign-in · DEV data already evaluated · model and cost of every evaluation on the card |
| Chips | Работает с: Русский · Қазақша · English · Чаты · Звонки с записью | Works with: Russian · Kazakh · English · Chats · Recorded calls |

KK draft of the H1 for the native check: «Әр диалог чек-лист бойынша бағаланады — негіздемесімен, дәйексөзімен және апелляциясымен».

**Live counters.** RU: «{65} диалогов оценено» · «RU {35} · KZ {15} · EN {15}» · «{60} чатов и {5} звонков» · «{8} грубых нарушений найдено» · «≈ ${0,22} за оценку · ≈ {15} с». Stamp «по данным API на {20:31}», fallback «по состоянию на 07.11.2026, 20:30». EN: «{65} conversations evaluated» · «RU {35} · KZ {15} · EN {15}» · «{60} chats and {5} calls» · «{8} gross violations found» · «≈ ${0.22} per evaluation · ≈ {15} s», «live from the API at {20:31}», «as of 07.11.2026, 20:30». Decimal comma in RU and KK, dot in EN.

**Problem** — RU «Почему контроль качества буксует» / EN «Why quality control stalls».

| # | RU | EN |
|---|---|---|
| 01 | **Выборка вместо охвата.** Вручную специалист успевает прочитать и прослушать лишь часть обращений; остальные остаются без оценки и без обратной связи. | **A sample, not coverage.** By hand, a QA specialist reads and listens to only part of the contacts; the rest get no score and no feedback. |
| 02 | **Оценка без доказательств.** Оператор не видит, за какую фразу снижен балл, и спорит с оценкой, а не с фактами. | **Scores without evidence.** Operators cannot see which message cost them points, so they argue with the score instead of the facts. |
| 03 | **Риски видны поздно.** Грубость, платные услуги в обход компании и раскрытие данных без идентификации всплывают из жалоб, а не в день разговора. | **Risks surface late.** Rudeness, paid side services and disclosure without identification come up through complaints, not on the day of the conversation. |

**How it works** — RU «Как это работает · пять шагов от диалога до окончательной оценки» / EN «How it works · five steps from a conversation to a final score».

| Step | RU | EN |
|---|---|---|
| 1 Импорт / Import | Диалоги приходят из API по group_id, из файла или вставкой текста. Бот и системные события сохраняются как контекст. | Conversations come from the API by group_id, from a file or pasted text. Bot turns and system events stay as context. |
| 2 Оценка по чек-листу / Checklist evaluation | Каждый критерий оценивается отдельным вызовом модели; баллы и итог считает движок по правилам чек-листа, а не модель. | Each criterion is judged by its own model call; points and the total are computed by the engine from the checklist rules, not by the model. |
| 3 Обоснование и реплика / Reason and message | У каждого критерия — ответ, баллы, объяснение и номер реплики; клик ведёт к сообщению или к моменту записи. | Every criterion has an answer, points, an explanation and a message number; a click opens the message or the moment in the recording. |
| 4 Апелляция / Appeal | Оператор или QA-специалист оспаривает критерий с комментарием. | An operator or QA specialist disputes a criterion with a comment. |
| 5 Решение / Decision | Ревьюер подтверждает или меняет оценку с причиной; итог пересчитывается, исходная оценка остаётся в истории. | A reviewer confirms or changes the score with a reason; the total is recalculated and the original stays in the history. |

**Features** — RU «Возможности» / EN «What it does».

| Chapter | RU title and bullets | EN title and bullets | Link |
|---|---|---|---|
| A | **Карточка разговора: всё обоснование на одном экране.** Ответ, баллы и вес по каждому критерию · «Почему снижена оценка» с переходом к реплике · N/A с причиной и перераспределением веса · звонки: дорожки «Клиент» и «Оператор», переход к моменту записи | **The conversation card: the whole justification on one screen.** Answer, points and weight per criterion · «Why the score was lowered» with a jump to the message · N/A with its reason and weight redistribution · calls: client and operator tracks, jump to the moment | `#/conversations/p1-ru-029` |
| B | **Чек-листы без программирования.** Добавить, изменить или заменить критерий и его вес — итог всегда нормируется к 100 · каждое сохранение — новая версия, версии можно сравнить · переоцениваются только изменённые критерии; новые веса пересчитываются без вызова модели | **Checklists without code.** Add, edit or replace a criterion and its weight — the total is always normalised to 100 · every save is a new version you can compare · only changed criteria are re-judged; new weights are recalculated without a model call | `#/checklists?role=methodologist` |
| C | **Грубые нарушения и апелляции.** Реестр по типам: оскорбление клиента, платная услуга в обход компании, раскрытие конфиденциальной информации · оператор оспаривает оценку своего диалога · ревьюер подтверждает или меняет её с причиной, исходная и итоговая оценки видны | **Gross violations and appeals.** A register by type: insulting the client, paid service bypassing the company, disclosure of confidential information · operators appeal scores of their own conversations · reviewers confirm or change them with a reason; original and final scores stay visible | `#/violations` |

**Who it is for** — RU «Для кого» / EN «Who it is for». Buttons «Открыть как …» / «Open as …».

| Card | RU | EN | Entry |
|---|---|---|---|
| Директор | Индекс качества, риски и стоимость оценки — только главное | Quality index, risks and evaluation cost — only what matters | `#/analytics?role=director` |
| Руководитель группы | Операторы, грубые нарушения и апелляции команды | Operators, gross violations and team appeals | `#/analytics?role=lead` |
| QA-специалист | Импорт, оценка, чек-листы и выгрузка результатов | Import, evaluation, checklists and results export | `#/conversations?role=analyst` |
| Оператор | Мои оценки и апелляции по своим диалогам | My scores and appeals on my own conversations | `#/my?role=operator` (identity picker) |

**Languages and channels** — RU «Три языка, чаты и звонки»: «Модель оценивает диалог на его языке и цитирует реплики дословно, а объяснения пишет на языке интерфейса. Русские названия разделов приложения в казахских и английских чатах ошибкой не считаются. Для звонков критерии адаптированы к устной речи.» EN «Three languages, chats and calls»: «The model judges each conversation in its own language and quotes messages verbatim, while explanations follow the interface language. Russian app labels inside Kazakh and English chats are not treated as errors. For calls, the criteria are adapted to spoken language.»

**Data under control** — RU «Данные под контролем», bullets: «Перед облачным вызовом телефоны, ИИН, номера карт, e-mail и IBAN заменяются метками» · «Локальная модель через OpenAI-совместимый API (vLLM, Ollama): данные не покидают контур» · «Каждый вызов модели записан: модель, версия промпта, токены, стоимость и время» · «Роли и журнал действий разделяют подачу апелляции и решение по ней» (this bullet ships only once B-0, the role middleware and audit, is deployed). EN «Data under control»: «Phone numbers, IINs, card numbers, e-mails and IBANs are replaced with placeholders before any cloud call» · «A local model through an OpenAI-compatible API (vLLM, Ollama): data stays inside the perimeter» · «Every model call is recorded: model, prompt version, tokens, cost and time» · «Roles and an audit trail keep filing an appeal and deciding it apart».
Card «Модели и стоимость» / «Models and cost»: «Основной судья: Claude Opus 5.5 · облако» · «Второй голос в ансамбле: Claude Sonnet 5.5» · «Локальная модель: {подключена · <model> | поддерживается, на стенде не подключена}» · «≈ ${0,22} за оценку чата, все вызовы учтены» · «Маскирование ПДн: {включено}». Rows render from `/api/settings` and `/api/health`; the status dot is green, grey or amber, never red for «not connected».

**Comparison** — RU «Ручная проверка и CQA» / EN «Manual QA and CQA».

| RU row | Вручную | CQA | EN row | By hand | CQA |
|---|---|---|---|---|---|
| Сколько диалогов оценено | выборка | каждый загруженный диалог | Conversations scored | a sample | every imported conversation |
| Обоснование оценки | заметка проверяющего | объяснение и номер реплики по каждому критерию | Justification | the reviewer's note | explanation and message number per criterion |
| Звонки | прослушать запись целиком | переход к моменту записи | Calls | listen to the whole recording | jump to the moment |
| Спорная оценка | переписка и служебные записки | апелляция по критерию, решение с причиной, пересчёт итога | Disputed score | e-mails and memos | appeal per criterion, decision with a reason, recalculated total |
| Изменение чек-листа | новая методичка и перепроверка | новая версия и переоценка только изменённого критерия | Checklist change | a new manual and re-checking | a new version, only the changed criterion re-judged |
| Грубые нарушения | по жалобам | реестр по типам в день оценки | Gross violations | through complaints | a register by type on the day of evaluation |
| Стоимость одной оценки | время специалиста | ≈ ${0,22} в облаке · $0 на локальной модели (без учёта оборудования) | Cost per evaluation | specialist time | ≈ ${0.22} in the cloud · $0 on a local model (hardware excluded) |

**FAQ** (RU answers; EN translates them one to one in the same namespace file).

| Question RU / EN | Answer RU |
|---|---|
| Откуда берутся баллы? / Where do the points come from? | Модель отвечает на каждый критерий отдельно: ответ, объяснение и реплики-основания. Баллы и итог считает движок по правилам чек-листа: «Да» — полный вес, «Нет» — ноль, шкала — пропорционально весу; итог округляется до двух знаков, половина вверх. |
| Что значит N/A? / What does N/A mean? | Критерий неприменим к этому диалогу — например, идентификация не нужна для общего вопроса о тарифе. N/A разрешён только там, где его допускает чек-лист; в карточке видна причина, а вес перераспределяется между применимыми критериями, максимум остаётся 100. |
| Почему грубая ошибка не обнуляет итог? / Why doesn't a gross error zero the total? | Так требует чек-лист: оскорбление, платная услуга в обход компании и раскрытие конфиденциальной информации отмечаются отдельно и попадают в реестр «Грубые нарушения»; итог считается по остальным критериям. |
| Как оспорить оценку? / How do I dispute a score? | В карточке разговора нажмите «Оспорить оценку», выберите критерий и напишите комментарий. Ревьюер видит спорный балл, комментарий и фрагмент диалога, подтверждает или меняет оценку с причиной; итог пересчитывается, исходная оценка остаётся в истории. |
| Как изменить чек-лист? / How do I change the checklist? | В разделе «Чек-листы» добавьте или измените критерий, вес, тип ответа и правило N/A, сохраните новую версию и примените её к выбранным диалогам. Переоцениваются только изменённые критерии; новые веса пересчитываются без вызова модели. |
| Это заранее посчитанные результаты? / Are these results precomputed? | Нет. Вставьте любой диалог — оценка появится примерно через 20 секунд; во вкладке «Трассировка» видны время, модель, токены и стоимость каждого вызова. |
| Уходят ли данные в облако? / Does data go to the cloud? | С облачной моделью — да, но телефоны, ИИН, номера карт, e-mail и IBAN заменяются метками до отправки. Для данных, которые не должны покидать контур, есть локальная модель через OpenAI-совместимый API. |
| Работает ли со звонками? / Does it work with calls? | Да: расшифровка с ролями, запись с дорожками «Клиент» и «Оператор», переход от обоснования к моменту записи. Критерии адаптированы к устной речи: например, приветствие, не попавшее в запись, — N/A. |
| Сколько стоит оценка? / What does an evaluation cost? | Около ${0,22} за чат на Claude Opus 5.5 по данным стенда, все вызовы учтены; на локальной модели — $0 без учёта оборудования. Стоимость каждой оценки видна в карточке. |
| Можно ли встроить в Dynamics 365? / Can it run inside Dynamics 365? | Да: интерфейс собирается в один HTML-файл, карточка открывается во вкладке формы обращения, транскрипты принимаются из Power Automate. |

**Final CTA.** RU «Проверьте любой диалог сами» / «Без входа в систему. {65} диалогов DEV уже оценены — откройте карточку или вставьте свой диалог: оценка появится примерно через 20 секунд.» Buttons «Открыть демо» · «Оценить свой диалог». EN «Check any conversation yourself» / «No sign-in. {65} DEV conversations are already evaluated — open a card or paste your own: the score appears in about 20 seconds.» · «Open the demo» · «Evaluate your own conversation».

**Footer.** Tagline RU «CQA · Conversation Quality Analytics для Freedom Broker» / EN «CQA · Conversation Quality Analytics for Freedom Broker». Columns: «Продукт / Product» (Как это работает, Возможности, FAQ); «Для жюри / For the jury» (Сценарии для жюри; `results/dev/results.json`; `results/prod/results.json` with «появится после контрольного этапа» until it exists; API Swagger `/docs`); «Проект / Project» (GitHub `github.com/k4ssymzhomart/freedom`, README, Архитектура). Bottom line: «Прототип команды «Я Тебя Могну» · хакатон Freedom Broker, 2026 · web {commit} · API {version}».

### 3.4 Behaviour

- Data: summary, violations, settings, health, the hero evaluation and the waveform peaks are fetched in parallel on mount with a 3 s budget each; slower or failing requests switch that block to the snapshot with its stamp; the page never shows 0 or an empty chart for missing data.
- Language: the landing uses the same RU / KK / EN switch and `cqa.lang`, so the choice carries into the app.
- Performance: app routes are lazy-loaded (`React.lazy`), so the landing does not ship recharts; no web fonts (§4.2); live cards render after first paint with skeletons of the same size (no layout shift).
- Returning visitor: if `cqa.role` exists, the primary button reads «Продолжить как <роль>» and opens that role's home.
- Accessibility: one H1, section H2s, anchors with visible focus, contrast ≥ 4.5:1 on the deep-green bands (white 9,0:1, secondary white at 78 % ≈ 6,3:1).

## 4. Design system in code

Corporate light is the default; dark is an option in Settings (personal). Tokens keep the existing CSS variable names in `web/src/index.css` so existing classes keep working, retune their values and add the missing ones. Values are written as hex here; in code they stay RGB channels for Tailwind opacity modifiers.

### 4.1 Colour tokens

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#F5F7F5` | `#0B1510` | page background |
| `--surface` | `#FFFFFF` | `#111D16` | cards, dialogs, tables |
| `--surface-2` | `#F0F3F0` | `#17261D` | table header, subtle fills, N/A tile |
| `--line` | `#E3E8E3` | `#22352A` | card borders, dividers |
| `--line-strong` (new) | `#C9D1C9` | `#2F4637` | input borders |
| `--text` | `#16201A` | `#E6EEE8` | headings, body |
| `--text-2` | `#5B6B60` (5,6:1 on white) | `#A7B6AC` | subtitles, timestamps, labels |
| `--text-3` (new) | `#8A968D` | `#6F8075` | placeholders and disabled only |
| `--accent` | `#2F8F2A` | `#51AF3D` | fills only: ring arc, progress, icons, chart series |
| `--accent-strong` (new) | `#1F7028` (6,2:1) | `#68C155` | primary button, green text, links, focus ring |
| `--accent-hover` | `#0F5431` | `#8ED47F` | primary hover and pressed |
| `--accent-subtle` | `#F0F8ED` | `#0F2A1A` | pass tile, selected row |
| `--selection` / `--tint` | `#DFF1DC` | `#1B3A26` | text selection, tinted chips |
| `--accent-fg` | `#FFFFFF` | `#0B1510` | text on primary |
| `--nav-bg` (new) | `#0F5431` | `#07130D` | sidebar |
| `--nav-fg` / `--nav-fg-muted` (new) | `#FFFFFF` / `rgba(255,255,255,.78)` | same | active label / items (6,3:1) |
| `--nav-active` / `--nav-hover` (new) | `#1F7028` / `rgba(255,255,255,.08)` | same | active pill / hover |
| `--evidence-bg` / `--evidence-line` (new) | `#FDEBD3` / `#F2B66D` | `#3A2A14` / `#B07A2E` | evidence turn, waveform region |
| `--success` / `-bg` / `-line` | `#1F7028` / `#F0F8ED` / `#BEE3B5` | `#8ED47F` / `#12301D` / `#2F5A3A` | earned points, confirmed |
| `--warning` / `-bg` / `-line` | `#A16207` (4,9:1) / `#FDF3E1` / `#F2D199` | `#E2A74A` / `#33270F` / `#6B4E1F` | lost points, under review |
| `--critical` / `-bg` / `-line` | `#B3362F` (6,0:1) / `#FDECEA` / `#F3C1BC` | `#F08A82` / `#3A1715` / `#6E2A26` | gross violations, failures, destructive |
| `--info` / `-bg` / `-line` | `#1F5FA8` / `#EAF2FB` / `#BCD3EE` | `#7DB8E8` / `#10263A` / `#24456A` | changed by reviewer, neutral notices |
| `--track-client` / `--track-operator` / `--track-other` (new) | `#0F5431` / `#51AF3D` / `#C9D1C9` | `#8ED47F` / `#51AF3D` / `#3B5244` | waveform lanes |
| `--ring-track` (new) | `#E8EDE8` | `#22352A` | score ring background |
| `--overlay` (new) | `rgba(10,20,14,.40)` | `rgba(0,0,0,.60)` | dialog backdrop, no blur |

The Figma ramp `--green-50..900` (`#F0F8ED #DFF1DC #BEE3B5 #8ED47F #68C155 #51AF3D #2F8F2A #1F7028 #0F5431 #092818`) stays as raw tokens for the landing and charts.

Tailwind mapping: the `accent` colour maps to `--accent-strong`, so the existing `text-accent`, `bg-accent` and `border-accent` classes become AA-safe without edits; a new `accent-fill` maps to `--accent` for rings, progress bars and charts.

### 4.2 Typography

Font stack: `-apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Segoe UI Variable Text", "Segoe UI", Inter, Roboto, "Helvetica Neue", Arial, sans-serif` (display headings put `"SF Pro Display"` first). Mono `"SF Mono", ui-monospace, Menlo, Consolas, monospace`. No font files are bundled: the presenting Mac renders SF Pro, Windows and Dynamics 365 render Segoe UI, both cover Kazakh letters (ә ғ қ ң ө ұ ү һ і), and the single-file build and the venue network stay unaffected.

| Token | Size / line | Weight | Tracking | Use |
|---|---|---|---|---|
| `display` | 56/60 (40/44 ≤ 1024 px, 32/38 ≤ 640 px) | 700 | −0,02em | landing H1 |
| `h1` | 32/38 (28/34 below 1280 px) | 700 | −0,02em | page titles («Анализ разговора»), landing section titles at 40/44 |
| `h2` | 22/28 | 700 | −0,01em | card titles («Расшифровка», «Оценка качества») |
| `h3` | 17/24 | 600 | 0 | sub-blocks («Почему снижена оценка») |
| `body` | 15/22 | 400 | 0 | text, transcript |
| `body-strong` | 15/22 | 600 | 0 | criterion names, speaker labels, points |
| `small` | 13/18 | 400 | 0 | timestamps, metadata, hints |
| `label` | 12/16 | 500 | 0,01em | table headers, field labels, sentence case (no uppercase) |
| `score-xl` | 40/44 | 700 | −0,02em | ring value «51,25» |
| `mono` | 12/16 | 500 | 0 | ids, JSON, request ids |

Numbers are tabular everywhere and formatted with `Intl.NumberFormat`: decimal comma in RU and KK, dot in EN; totals show up to two decimals («85,5», «51,25», «100»); results.json and CSV keep the dot.

### 4.3 Shape, spacing, elevation, motion, icons

- Radius: 6 chips and icon tiles · 10 inputs and buttons · 16 cards and dialogs · 24 landing CTA panel · full for pills and the play button.
- Spacing (4-pt): 4, 8, 12, 16, 20, 24, 32, 48, 64, 96; card padding 24 (20 in compact cards); gaps between cards 24 (16 below 1280 px).
- Elevation: cards `0 1px 2px rgba(16,32,24,.04)` plus the 1 px border; popovers and drawers `0 12px 32px -12px rgba(16,32,24,.24)`. Remove `.glow`, `shadow-glow`, `shadow-float` at 45 % black and the neon `.turn-highlight`.
- Motion: 150–200 ms ease-out on hover, press and expand; the evidence highlight fades in over 200 ms; scroll to a turn is smooth; `prefers-reduced-motion` makes both instant.
- Focus: 2 px `--accent-strong` outline with 2 px offset (white outline inside the sidebar); never removed.
- Icons: lucide line icons, 18 px in content, 20 px in the sidebar, stroke 1.75; icon-only buttons carry `aria-label`.

### 4.4 Component inventory

Foundation components live in `web/src/components/ds/` (WP-0); presentational quality components in `web/src/components/ds/quality/` (WP-1, reused by WP-3, WP-4, WP-7). Feature components stay in their feature folders.

| Component | File | Props | Variants and states | Owner |
|---|---|---|---|---|
| `Button` | `components/ui/button.tsx` (refactor) | `variant`, `size`, `loading`, `icon`, `asChild` | primary (accent-strong fill, white), secondary (white, 1 px accent-strong border and text, the mockup «Оспорить оценку»), ghost, danger, link; sm 32 / md 40 / lg 48; hover, pressed, disabled, loading | WP-0 |
| `Card` | `ds/Card.tsx` (new, replaces `.card` and `Section`) | `title`, `subtitle`, `actions`, `tabs`, `padding`, `tone` | default, critical (red border), muted; padding md 20 / lg 24 | WP-0 |
| `PageHeader` | `ds/PageHeader.tsx` (refactor of `common.tsx`) | `back {to,label}`, `title`, `meta: string[]`, `actions` | with or without back link; meta joined by « · » | WP-0 |
| `ScoreRing` | `ds/ScoreRing.tsx` (refactor) | `value`, `max`, `size`, `original`, `caption` | sm 56 / md 96 / lg 144; band colour by value (≥ 85 accent, 60–85 warning, < 60 critical, none «—»); «/ 100» grey under the value; original struck through with «после апелляции»; `aria-label` «Итоговая оценка 51,25 из 100, есть грубое нарушение» | WP-0 |
| `ScorePill` | `ds/ScorePill.tsx` (refactor) | `value`, `original`, `critical` | band tone; red dot and «грубое» when critical; tooltip «исходный: …» | WP-0 |
| `Chip` | `ds/Chip.tsx` (refactor of `ui/badge.tsx`) | `tone`, `size`, `icon`, `count` | success, warning, critical, info, neutral, brand, outline; sm 22 / md 26; presets `LanguageChip`, `ChannelChip` (Чат / Звонок), `ReviewStateChip` (ИИ / На рассмотрении / Подтверждено / Изменено ревьюером), `ViolationTypeChip`, `ProviderChip` | WP-0 |
| `IconTile` | `ds/IconTile.tsx` (new) | `kind`, `size` | pass (green check on `--success-bg`), fail (amber cross on `--warning-bg`), na (grey dash), critical (red shield), info; 24 / 32 / 48 | WP-0 |
| `KpiTile` | `ds/KpiTile.tsx` (refactor of `Kpi`) | `label`, `value`, `hint`, `stamp`, `to` | static or drillable (whole tile is a link with an `aria-label`) | WP-0 |
| `EmptyState` | `ds/EmptyState.tsx` (refactor) | `icon`, `title`, `hint`, `actions`, `mascotPose` | neutral 48 px icon tile; mascot only through `BrandMascot` | WP-0 |
| `InlineAlert` / `ErrorState` | `ds/InlineAlert.tsx` (refactor) | `tone`, `title`, `message`, `onRetry`, `requestId` | info, warning, critical, offline | WP-0 |
| `DataTable` styles | `components/ui/table.tsx` (refactor) | `density`, `stickyHeader`, `onRowClick` | header 40 px on `--surface-2`, rows 52 px (compact 44), numbers right-aligned, hover row | WP-0 |
| Form controls | `ui/input.tsx`, `select.tsx`, `switch.tsx`, `checkbox.tsx` (refactor); `ds/Field.tsx`, `ds/SegmentedControl.tsx`, `ds/NumberStepper.tsx` (new) | `label`, `hint`, `error`; `options [{value,label,sublabel,disabled,reason}]`; `min`, `max`, `step`, `suffix` | 40 px height, `--line-strong` border, radius 10; error state with message; segmented control is a radio group | WP-0 |
| `Dialog` / `Drawer` | `components/ui/dialog.tsx` (refactor) | `size`, `side` | sm 480 / md 560 / lg 720; right drawer 400–560; radius 16; `--overlay`, no blur | WP-0 |
| `Tabs` | `components/ui/tabs.tsx` (refactor) | `variant` | underline (2 px accent-strong indicator, 15/22 500), segmented | WP-0 |
| Toasts | sonner theme in `App.tsx` | `tone`, `title`, `description`, `action` | light; error toasts show the error code (request id P1) | WP-0 |
| Evidence highlight | `.evidence` utility in `index.css` with the `--evidence-*` tokens (new) | — | turn: peach fill, 1 px line, radius 10, fades in over 200 ms; waveform: band across both lanes with 1 px edges; quote blocks in the register and the review fragment | WP-0 |
| `BrandMascot` | `components/Mascot.tsx` (refactor) | `pose`, `size` | renders `null` unless `mascot_enabled`; poses lazy-imported, out of the main bundle | WP-0 |
| `SideNav`, `RoleCard`, `RoleMenu`, `IdentityPicker`, `LanguageSwitch`, `ConnectionDot`, `NotFoundPage` | `components/layout/*`, `features/system/*` (new) | role-driven items and badges; roles list; operators list | expanded 240 / collapsed 72; active pill; picker with search | WP-0 |
| `CriterionRowCompact` | `ds/quality/CriterionRowCompact.tsx` (new) | `code`, `title`, `iconKind`, `earned`, `max`, `naReason`, `reviewState`, `selected`, `onSelect` | points «7,5 / 18,75» (earned green if > 0, amber if 0, max grey); N/A «N/A» grey with «вес перераспределён»; C8 «флаг» / «нет»; selected (accent-subtle fill, 2 px left bar); chips «На рассмотрении», «Изменено» | WP-1 |
| `SectionHeader` | `ds/quality/SectionHeader.tsx` | `title`, `earned`, `max`, `flag` | «Начало и понимание запроса · 12,5 / 31,25»; S4 «флаг» | WP-1 |
| `ReasonSummary` | `ds/quality/ReasonSummary.tsx` | `lost [{code,title,lost,reason}]`, `na`, `onPick` | lines sorted by loss; «Оценка не снижалась»; N/A line | WP-1 |
| `EvidenceChip` | `ds/quality/EvidenceChip.tsx` | `number`, `speaker`, `time`, `quote`, `role`, `inRecording` | supports / contradicts / context written in the tooltip and `aria-label`; «не в записи» | WP-1 |
| `TranscriptRow` | `ds/quality/TranscriptRow.tsx` | `number`, `time`, `speaker`, `text`, `state`, `dimmed` | default, evidence (peach), span (amber left rule), playing; bot and system dimmed | WP-1 |
| `WaveformBars` + `PlayerBar` | `ds/quality/WaveformBars.tsx`, `PlayerBar.tsx` | `tracks`, `duration`, `regions`, `playhead`, `onSeek`; `playing`, `current`, `speed` | two lanes «Клиент» / «Оператор» (+ thin third lane for IVR and consultation), peach region with `--evidence-line` edges, round 40 px play button, «02:18 / 06:33», progress with 12 px knob; fallback lanes from turn spans labelled «по расшифровке» | WP-1 |
| `GrossViolationBlock` | `ds/quality/GrossViolationBlock.tsx` | `types`, `quote`, `number`, `time`, `state` | AI flag, confirmed, overturned («Отметка снята решением ревьюера») | WP-1 |
| `QualityScoreCard` | `ds/quality/QualityScoreCard.tsx` | `total`, `original`, `sections`, `rows`, `reasons`, `critical`, `selectedCode`, `onSelect`, `onPrimary`, `onSecondary`, `compact` | full (the card's «Оценка качества») and compact (landing hero: ring, three rows, reasons, buttons as links); composes ScoreRing, SectionHeader, CriterionRowCompact, ReasonSummary, GrossViolationBlock | WP-1 |

### 4.5 Existing files: what happens to them

| File | Action | Owner |
|---|---|---|
| `web/index.html` | drop `class="dark"`; `theme-color` `#0F5431`; `color-scheme: light dark`; favicon `/favicon.svg` (white «CQA» on `#0F5431`, new file); keep title and description | WP-0 |
| `web/src/index.css`, `web/tailwind.config.ts` | token values and new tokens (§4.1), type scale, radii, shadows; delete glow and neon utilities; `.label` loses uppercase; new `.evidence` utility | WP-0 |
| `web/src/store/ui.tsx` | 7-role union, light default (dark only when `cqa.theme = dark`), identity and display name, URL parameters | WP-0 |
| `web/src/components/layout/AppShell.tsx` | rewritten: deep-green `SideNav`, role card, no top bar | WP-0 |
| `web/src/components/common.tsx` | keeps its exports for compatibility, re-implemented on the DS (ScoreRing, ScorePill, EmptyState, ErrorState, PageHeader, Section, Kpi); no mascot | WP-0 |
| `web/src/components/Mascot.tsx`, `ErrorBoundary.tsx` | mascot gate; error boundary text in three languages | WP-0 |
| `web/src/components/ui/*` | restyled per tokens | WP-0 |
| `web/src/lib/format.ts` | `fmtScore` with Intl and locale, `fmtTurn(idx)` → «№{idx+1}», `fmtPoints(earned, max)` | WP-0 |
| `web/src/features/card/*` | rebuilt (§5.3) | WP-1 |
| `web/src/features/review/ReviewPage.tsx` | rebuilt (§5.7), mascot removed | WP-4 |
| `web/src/features/checklists/*`, `web/src/features/jobs/*` | rebuilt (§5.8), mascot removed from the save dialog and job cards | WP-5 |
| `web/src/features/analytics/*`, `web/src/features/settings/*` | restyled (§5.9, §5.10) | WP-6 |

### 4.6 Rules every package follows

- Red only for gross violations, failures and destructive actions; amber for lost points and «на рассмотрении»; green for earned points, primary actions and brand.
- No mascot unless `mascot_enabled` (admin setting, default off), and then only inside empty states.
- Turn numbers are 1-based everywhere («№10»); the internal idx appears only in raw Trace JSON.
- Monospace only for ids, JSON and request ids, never for scores or KPIs.
- Sentence-case labels, Russian quotes «…», locale decimal separator, «$0,22».
- Every empty, loading and error state names the next step (SHELL-07); 0 is never shown for «no data».

## 5. Screen-by-screen changes

P0 = needed for the four asks, hands-on access or the submission; P1 = only if the package is green early. Story IDs refer to `docs/USER_STORIES.md`.

### 5.1 App shell, roles and «Сценарии для жюри» (WP-0; the panel WP-7)

Stories: SHELL-01, SHELL-02, SHELL-04, SHELL-05, SHELL-06, SHELL-07, SHELL-10, DEMO-01, DEMO-04.

- P0: light default, mascot off, deep-green sidebar with role-specific items and badges (Нарушения from `/api/violations` total, Апелляции from `/api/appeals?status=open`); role card, role menu, identity picker, `?role=` / `?as=`, write headers (§1.2); forbidden routes render read-only with «Раздел доступен роли …» and a switch link; «Страница не найдена»; sidebar collapse below 1280 px.
- P0 «Сценарии для жюри» (right drawer, 400 px, opened from the sidebar or `?tour=1`), seven rows, each with one sentence, a chip «переключит роль на …» and «Открыть»:
  1. «Грубые нарушения и почему» → `#/violations` («8 диалогов: оскорбление 1 · платная услуга 4 · раскрытие 3»).
  2. «Оператор подаёт апелляцию» → Оператор · Нина → `#/my` («Откройте p1-ru-005 и нажмите «Оспорить оценку»»; if Нина already has an open C6 appeal the hint becomes «Апелляция уже подана — откройте «Мои апелляции»»).
  3. «Ревьюер принимает решение» → Ревьюер → `#/review`.
  4. «Изменить чек-лист» → Методолог → the active version (resolved from `GET /api/checklists`).
  5. «Результат диалога с обоснованием» → `#/conversations/p1-ru-029?criterion=C8&turn=10`.
  6. «Звонок: переход к моменту записи» → `#/conversations/p4-ru-002?criterion=C6`.
  7. «Оценить новый диалог» → QA-аналитик → `#/conversations?paste=1`.
  Done rows get a tick (per browser) and «Начать заново» resets them.
- P1: offline banner with «Повторить» after two failed health checks; `document.title` per route («p1-ru-029 · Анализ разговора · CQA»); coach-mark outline on the control named in the scenario; «Есть несохранённые изменения» guard on role switch; four-eyes warning.
- Acceptance: a fresh browser shows no `dark` class and no `img[src*="mascot"]` on any route; `#/my?role=operator&as=Нина` shows «Оператор · Нина» and the operator nav; `POST /api/appeals` carries `X-CQA-Role: operator` and `X-CQA-User: %D0%9D%D0%B8%D0%BD%D0%B0` while GETs carry none; at 1164 px width (1280 px at 110 % zoom) nothing scrolls sideways; every scenario opens its target.

### 5.2 Conversations `#/conversations` (WP-2)

Stories: CONV-04, CONV-06 (UI part), CONV-07 (entry), CONV-10, SHELL-10, EXP-05 (CSV link).

- P0: header «Диалоги» · «65 диалогов · 65 оценено · набор DEV»; default columns ID · Канал и язык · Оператор · Итог (pill, tooltip «исходный: …» after a decision) · Грубое нарушение (type chip) · Апелляция (state chip); turns, first response, provider and cost move to the card.
- P0: one-row filter bar (search, Язык, Канал, Статус, «Только с грубыми нарушениями»), mirrored in the URL (`q`, `language`, `channel`, `critical`); the client filter that drops transcript matches returned by the backend goes (`ConversationsPage.tsx:99-102`); an exact id + Enter opens the card.
- P0: toolbar by role — «Импорт», «Загрузить или вставить», «Оценить выбранные (N)», «Экспорт ▾» (CSV · results.json…); evaluate opens the shared Apply/Evaluate dialog (WP-5); runs above 20 dialogs need a confirmation with the estimate.
- P0: the paste dialog never sends a conversation id (always `manual-…`, nothing can be overwritten); a file upload whose ids already exist asks for an explicit confirmation listing them.
- P0: empty database → «1 Импорт → 2 Оценка → 3 Карточка» with «Импорт по group_id» prefilled «DEV».
- P1: «Набор» filter from `/api/datasets`; status «Ошибка оценки» with «Повторить для всех (N)»; filters and scroll kept on Back.
- Acceptance: «читать умеете» lists p1-ru-029; `?language=KZ&critical=1` lists exactly p1-kz-013 and p1-kz-015; no horizontal scroll at 1024 px; Оператор is sent to `#/my`.

### 5.3 Conversation card `#/conversations/:id` (WP-1) — the organizer mockup

Stories: CARD-01, CARD-02, CARD-03, CARD-04, CARD-05, CARD-06, CARD-07, CARD-08 (placement), GV-03, APL-01, APL-09, SHELL-10.

Layout from 1100 px: page header; for calls the waveform card across the full width; then «Расшифровка» (5/12) and «Оценка качества» (7/12, min 520 px). Below 1100 px the quality card comes first, the transcript second.

- Header: «← Диалоги» (or «← Грубые нарушения» when opened from the register), title «Анализ разговора», meta «Чат · p1-ru-029 · Русский · оператор Алексей · 14 реплик» (calls «Звонок · p4-ru-002 · Русский · 10:20»), actions «Экспорт PDF», «Переоценить» (roles that may run), «⋯» (Копировать JSON; Копировать ссылку P1).
- «Расшифровка»: divided list as in the mockup — time (grey 13 px), speaker label (Клиент `#0F5431`, Оператор `#1F7028`, both 600; Бот and Система grey), «№n» on the right, text 15/22 below; the pre-operator segment (bot greeting, consent, escalation) folded into «До оператора: бот и системные события · 9 реплик», unfolded automatically when an evidence points inside; system events as small grey lines; search behind an icon; evidence turn in peach (`#FDEBD3`, 1 px `#F2B66D`, radius 10); checked span with an amber left rule; duplicates collapsed.
- «Оценка качества», tabs in the card header «Оценка · История · Трассировка» (+ «Звонок» for calls):
  1. C8 = Да: `GrossViolationBlock` «Грубое нарушение: оскорбление клиента» (type from `/api/violations`), quote «№10 · 01:23 · «Вы вообще читать умеете? Бот всё написал.»», note «Итог не обнуляется: по чек-листу грубая ошибка отмечается отдельно»; for the operator «Вы можете оспорить отметку» (C8 preselected); overturned: «Отметка снята решением ревьюера» with the reason.
  2. Ring (144 px) with the criteria rows to its right, grouped by section headers with subtotals (S1–S4); rows are a master list, one selected at a time.
  3. «Почему снижена оценка»: lines by lost points — for p1-ru-029 «Выявление потребности −18,75», «Эмпатия −18,75», «Управление диалогом −11,25», each with the first sentence of its explanation after the «Критерий — ответ.» prefix (≤ 120 characters); «Не применимо: Идентификация — вес перераспределён»; «Оценка не снижалась» at 100. A line selects its criterion.
  4. Buttons (48 px): primary «Перейти к фрагменту» (first contradicting evidence of the selected criterion, else the first one; on calls it plays from 2 s before) and secondary «Оспорить оценку» (selected criterion; roles that may appeal; disabled with «Апелляция уже на рассмотрении»).
  5. «Как посчитан итог» under the ring → dialog with base weight, applicability, answer, share, applied weight and points per criterion; footer «Применимый вес 80 из 100 → коэффициент 1,25 · сумма 51,25 · округление до 2 знаков, половина вверх»; C8 «Без баллов — отмечается отдельно»; after a decision two columns «Исходно / После решения». Computed from the evaluation payload, no new endpoint.
  6. Criterion detail (default: the largest loss): answer chip («4 из 10»), «7,5 / 18,75 · вес 15 → 18,75», the anchor of the answer and of the next step up (`options[].anchor`), the explanation (`explanation_display`, B-3), evidence chips, «Проверенный фрагмент №10–№11», N/A block «Причина N/A: … · вес 20 → 0, перераспределён на применимые критерии», appeal and decision thread, «Технические детали» folded (flags, votes, claims); P1 low-confidence badge below 0,70.
  7. One grey line: «Оценено: Claude Opus 5.5 · чек-лист v1 · 07.11, 17:47 · $0,18 · 14 с»; everything else is in «Трассировка».
- Deep links: `?criterion=C8&turn=10` selects C8 and highlights №10 on load; chip clicks update the URL (`replaceState`); unknown values give a quiet «Фрагмент не найден».
- Appeal dialog (APL-01): «Апелляция по критерию C6 · Управление диалогом, скорость и грамотность»; «Ответ ИИ: 4 из 10 · 9,23 / 23,08»; explanation and evidence; «Какой ответ вы считаете верным? (необязательно)» with anchors (current answer disabled; N/A only where the channel allows it) and the preview «Если апелляцию удовлетворят: 78,46 → 85,38 · до решения итог не меняется» (`POST /api/appeals/preview`); «Ваш комментарий» (≥ 10 characters); «Отправить на апелляцию». The request carries `author_role`, `author_name` and the proposed answer as the first comment line; toast «Апелляция отправлена ревьюеру»; the row turns «На рассмотрении» without a reload; 403 `not_your_dialog` reads «Оператор может оспаривать только оценки своих диалогов».
- After a decision (APL-09): the row reads «4 → 7 · 9,23 → 16,15 · Изменено ревьюером · Айгерим · 07.11, 21:44»; the ring shows the final total with the original struck through and «после апелляции»; «История» shows Оценка ИИ → Апелляция → Решение → Итог; the card refetches on window focus.
- States: running → progress bar in the card (P1: rows fill in from `criterion.done`); no evaluation → «Оценки пока нет» with «Оценить» only for roles that may run; failed → banner with the reason, never a 0.
- Acceptance: on p1-ru-029 at 1280×720 the header, ring «51,25 / 100», gross block and the first three rows are visible without scrolling, «Почему снижена оценка» and both buttons within one scroll of the card; chip №10 centres turn №10 in peach and sets `?criterion=C8&turn=10`; opening that URL cold reproduces it; the «Как посчитан итог» footer reads as above; p1-ru-005 C6 shows «4 из 10» with «Клиенту приходится переспрашивать из-за формулировок или порядка ответов»; no «[9]» style 0-based number is visible anywhere on the card once B-3 is deployed.

### 5.4 Calls (WP-1, with B-2)

Stories: CALL-01, CALL-02, CALL-03, CALL-09, CALL-12.

- P0: the waveform card of the mockup above the transcript: lanes «Клиент» / «Оператор» from `GET /api/audio/{id}/peaks?buckets=600` downsampled to the width, a thin third lane for IVR and the colleague consultation when present, the selected evidence shaded peach from the turn start to the next turn, round play button, «02:00 / 10:20», progress bar, speed 0,75–2×; clicking a lane, an evidence chip or a transcript row seeks and plays.
- P0: offsets are recording time after B-2; turns before the recording start read «до начала записи» and do not seek.
- P0: banner on calls whose operator segment starts before the recording (p4-ru-002): «Запись началась после начала разговора оператора: приветствие могло не попасть в запись. Правило организаторов для звонков: C1 = N/A»; the appeal dialog and the review panel offer N/A for C1 on calls (`na_allowed_for_channel`).
- P0: recording unavailable → «Запись недоступна: источник не отвечает. Оценка и расшифровка доступны.» + «Повторить»; lanes drawn from turn spans with «по расшифровке».
- P1: follow mode, «Следующий фрагмент» with a counter, Space to play or pause, hold bands.
- Acceptance: p4-ru-002 shows two lanes and 10:20; C6 selected → «Перейти к фрагменту» plays turn №15 «Сейчас прошу вас ожидать на линии» at ≈ 02:00; chats show no waveform.

### 5.5 Gross violations `#/violations` (WP-3)

Stories: GV-01, GV-02 (P0 subset), GV-03, GV-04 (state chip only).

- P0: header «Грубые нарушения» · «8 диалогов · проверено 65 · чек-лист v1 · по данным на 20:31»; type filter chips «Все 8 · Оскорбление клиента 1 · Платная услуга в обход компании 4 · Раскрытие конфиденциальной информации 3 · Иное 0»; language and channel selects; filters in the URL.
- P0: one card per dialog, sorted by final total ascending: type chip(s), id, channel and language chips, operator, total pill with «итог не обнуляется», review state («ИИ · не подтверждено», «Подтверждено ревьюером», «На апелляции»), first two lines of the C8 explanation, quote block «№10 · 01:23 · Оператор: «Вы вообще читать умеете? Бот всё написал.»», «Открыть в диалоге» → `#/conversations/<id>?criterion=C8&turn=<n>`.
- P0: expander «Почему так классифицировано»: the C8 rule from the active checklist with the matched category highlighted, the full explanation, confidence in words and number («высокая · 0,95»), and for disclosure cases the same dialog's C3 verdict (fetched on expand) with the team rule «C3 = Нет и C8 = Да при раскрытии данных до идентификации». Quotes stay in the dialog's language.
- P0 empty states: «Звонки» → «В звонках грубых нарушений не найдено · проверено 5 звонков»; nothing at all → «Грубых нарушений не найдено» with the three types listed.
- P1: «Показать снятые ревьюером» (`include_reviewed_out=true`), reviewer «Подтвердить» / «Снять», CSV.
- Acceptance: 8 cards, p1-kz-015 (24,71) first, p1-ru-023 (100,00) last; «Раскрытие» + KZ → only p1-kz-015 and `#/violations?type=disclosure&language=KZ`; «Открыть в диалоге» lands on the turn in peach; Back keeps the filters.

### 5.6 Operator portal `#/my` (WP-3)

Stories: OPR-01 (page), OPR-02, OPR-04, OPR-05; OPR-03 (P1).

- P0: header «Мои оценки» · «Оператор · Нина · RU · 1 диалог»; tiles «Средний итог 78,46», «Грубых нарушений 0», «Апелляций на рассмотрении 0», «Больше всего баллов теряю: Управление диалогом −13,85 · Корректная и полная консультация −7,69» (from the operator's evaluations), «Среднее по всем диалогам 82,04» (no colleague names); «Мало данных для выводов» below three evaluations.
- P0: own dialogs (`GET /api/conversations?operator=`): id, channel, language, total, gross chip, appeal state, checklist version → card.
- P0: tab «Мои апелляции» (`GET /api/appeals?operator=`): criterion, dialog, date, status; decided ones «Изменено: 4 → 7 · итог 78,46 → 85,38 · <причина>» or «Оценка подтверждена · <причина>»; unread dot until opened; refetch on focus. «Чек-лист» opens the active version read-only. Another operator's card opens read-only with «Диалог другого оператора».
- P1: first-visit panel «Как оцениваются ваши диалоги» (8 критериев · ИИ цитирует реплики · итог из 100, N/A с перераспределением · грубые нарушения отдельно) with «Понятно».
- Acceptance: as Нина the list holds p1-ru-005 at 78,46; after the reviewer's decision «Мои апелляции» shows it on focus with the badge.

### 5.7 Appeals review `#/review` (WP-4)

Stories: APL-05, APL-06 (chat fragment P0, call player P1), APL-07, APL-08, APL-09, SHELL-06, CAL-12 (switch default off), GV-05 (P1).

- P0: header «Апелляции» · «{N} открыто · {M} решено»; tabs «Открытые» / «Решённые»; C8 appeals pinned, then newest first, so an appeal just filed is on top («Сначала старые» is a P1 sort); the first open appeal is expanded, the others are one line each.
- P0 appeal card: «p1-ru-005 · RU · Чат · оператор Нина», «C6 · Управление диалогом, скорость и грамотность», «подана 5 мин назад»; left: «Ответ ИИ: 4 из 10 · 9,23 / 23,08 · уверенность 0,88», explanation, author block «Оператор · Нина» with the comment (proposed answer on top), «Фрагмент диалога» (evidence turns with one context turn on each side, evidence in peach), «Открыть в карточке» deep link.
- P0 decision panel (Ревьюер, Руководитель, Админ): «Подтвердить / Изменить»; under «Изменить» only the checklist options with points and anchors, the AI's answer disabled («Совпадает с ответом ИИ — выберите «Подтвердить»»), N/A only when `na_allowed_for_channel` and sent as `"N/A"`; reason ≥ 15 characters with a counter; live preview «Итог: 78,46 → 85,38» or «итог не изменился»; «Использовать как пример для ИИ» off by default; «Сохранить решение» → toast «Решение сохранено. Итог: 85,38». Other roles see «Решение принимает ревьюер» and «Переключиться на роль Ревьюер».
- P0 empty: «Открытых апелляций нет · оператор или QA-специалист подаёт апелляцию из карточки разговора» + «Открыть диалоги», «Грубые нарушения (8)».
- P1: reason template in the checklist's justification format, section deltas, call mini-player, `#/review/:id`, four-eyes warning.
- Acceptance: Нина's appeal is first; «Изменить → 7» shows 78,46 → 85,38 before saving; p4-ru-002 C1 «Изменить → N/A» previews 80,50 → 89,44 and saves without a 400; a 10-character reason cannot be saved; the decision shows on the card and in «Мои апелляции».

### 5.8 Checklists `#/checklists` and the version editor (WP-5)

Stories: CHK-01 (P0-lite), CHK-02, CHK-03, CHK-04, CHK-05, CHK-06, CHK-10, CHK-12, CHK-13 (P1), CHK-15, CHK-16, SYS-06, DEMO-05, DEMO-12.

- List: card «Freedom Broker · Чек-лист качества чатов» · «v1 · Активна · 8 критериев · 4 раздела · Σ весов 100 · базовая версия организаторов», versions table (status «Активна» / «Черновик», note, date), «Открыть активную версию».
- v1 lock (CHK-15): banner «Базовая версия организаторов. Изменения сохраняются как новая версия — v1 и выгрузка results.json не меняются.» with «Создать версию на основе v1» (unlocks editing locally); «Перегенерировать» judge spec hidden on versions that have evaluations.
- Criteria list grouped by section; compact row: code, title, type summary («Да / Нет», «Шкала 20 / 15 / 10 / 5 / 0»), N/A chip, «флаг» for C8, weight stepper, share %; click expands the inline editor (title, instruction, answer type, options with points and anchors, N/A + condition, «Чат | Звонок» tab with the call instruction and call N/A, critical flag, read-only judge spec drawer).
- Add (CHK-02): «+ Добавить критерий» at the end of each section and at the bottom → next free code (max numeric suffix + 1, so C9 even after deleting C5), that section, Да/Нет, weight 10, N/A off; the row opens expanded with focus on the title.
- Replace (CHK-05): row menu «Заменить критерий» → the old row greyed «будет удалён в новой версии» with «Отменить»; a new row in the same place and section with the next code and the old weight; open appeals on the old criterion are named; replacing C8 warns that the register needs a critical criterion.
- Delete: «C5 «Эмпатия» будет исключён из новой версии; v1 и её оценки не изменятся» + 10 s undo toast; the last weighted criterion cannot be deleted.
- Weights (CHK-04): right panel «Веса» with bar, weight and share per criterion and Σ: «Σ 100», or amber «Σ 105 · веса относительные: итог всё равно нормируется к 100» with «Нормализовать до 100» (largest remainder: C7 = 10 gives 10 / 14 / 19 / 19 / 14 / 14 / 10) and «Отменить»; the C8 weight is disabled («Грубые ошибки не дают баллов»).
- Calls (CHK-06): editing the call instruction keeps `na_allowed` and `na_condition` of the override (fix `CriterionEditor.tsx:113`; `stripCriterion` keeps overrides without an instruction).
- Validation (CHK-10): client checks before saving (empty title or instruction, duplicate code, < 2 options, duplicate values, N/A without a condition, Σ = 0) next to the fields; a 422 `errors[]` from the API maps to the same fields.
- Save (CHK-12): change summary computed in the browser («C7: вес 5 → 10 · C9 добавлен · C1: формулировка»), required note (≥ 5 characters), «Сделать активной сейчас» off by default (shown only when the API supports `activate`; otherwise the note «Новая версия станет активной»), toast «Создана версия v2 (черновик)» with «Применить к диалогам».
- Apply (CHK-16, DEMO-12) in the shared Apply/Evaluate dialog: scope «Пример: 3 диалога (RU · KZ · EN)» = p1-ru-022, p1-kz-013, p1-en-002 (no appeals on them) · «Выбранные в списке (N)» · «Набор DEV (65)» · «Контрольный набор» once imported · «Список ID» (pasted, validated); mode «Частичная — только изменённые критерии» preselected with the active version as base, or «Полная»; dry run «Переоценка моделью: C9 · 3 диалога · 3 вызова · ≈ $0,07 · ≈ 30 с» or «Только пересчёт весов · 0 вызовов модели · $0,00», budget line when returned; progress; summary «Диалог · было (v1) · стало (v2) · новый критерий» with links. The dialog never sends `conversation_ids: []`.
- Diff: P0 as today (added, removed, changed, re-judge flags); P1 old → new values and a cost footer.
- Acceptance: add, replace, reweight + normalise, save and apply each take ≤ 10 clicks on hosted data; a weight-only partial run writes no model trace rows and costs $0,00; v1 is never modified; C1 keeps its call N/A rule after any save.

### 5.9 Analytics and the executive overview `#/analytics` (WP-6)

Stories: EXE-01, EXE-02 (P0-lite), ANL-02 (drill links), ANL-03 (restyle), ANL-12 (cost tile); ANL-04 (P1).

- P0 «Обзор» band (home of Директор and Руководитель): ring «82,04 / 100» with «65 диалогов» and a definition tooltip (mean of totals after review, N/A redistributed, half-up rounding, gross errors separate); band chips «≥ 85 · 60–85 · < 60: 13» (`/api/overview` shares when deployed, else the existing 10-point buckets); risk tile «Грубые нарушения: 8 из 65 · Оскорбление 1 · Платная услуга 4 · Раскрытие 3» linking to `#/violations?type=…`; RU / KZ / EN means with n linking to `#/conversations?language=…`; chats / calls (calls «малая выборка, n = 5»); cost tile «≈ $0,22 за оценку · $16,22 за 75 оценок · Claude Opus 5.5»; stamp «DEV · чек-лист v1 · по данным на 20:31».
- P0: existing sections restyled light (distribution, criteria × language heatmap with n, operators table, worst dialogs, AI-vs-reviewer agreement with «Решений пока мало (n < 5)»); Директор sees no write actions anywhere.
- P1: «Где теряем баллы» from `/api/overview` (lost points per criterion with an example), coverage tile, risk-first operators sort.
- Acceptance: every number either links to its filtered list or names its source in a tooltip; the analytics chunk is lazy (not in the landing bundle).

### 5.10 Settings `#/settings` (WP-6)

Stories: SHELL-02, ADM-01, ADM-02 (status only), DEMO-04; EXP-03 (P1).

- P0 «Оформление»: theme light / dark (personal); «Фирменный персонаж» (org setting `mascot_enabled`, Админ only, default off, hint «Только в пустых состояниях; не используется на лендинге и в отчётах»).
- P0 «Профиль»: display name for signatures.
- P0 «Модель и стоимость»: provider and model, ensemble members, explanation language, PII masking, few-shot from decisions, local endpoint status from `/api/health` («поддерживается, на стенде не подключена» in grey, never red); read-only with «Изменяет администратор» for every role but Админ.
- P0 «Экспорт результатов» → the results.json dialog (§5.11); «О системе»: web commit and build time (injected at build), API version (`/api/version` once deployed).
- P1: «Проверить подключение» to the local model (needs a backend endpoint), snapshot card for Админ on top of the existing snapshot API.

### 5.11 Results export dialog (WP-2; entries in the list toolbar and in Settings)

Stories: EXP-01, EXP-02 (notes only), DEMO-06 (UI fallback to the CLI).

- P0: set selector from `/api/datasets` (DEV; the control group once imported) with the expected count (65 / 10, editable); «Проверить» → `GET /api/export/results/validate?group_id=&expect=` → «Файл валиден: schema 1.0 · 65 записей · ok 65 · error 0», model block («external_api · claude-opus-5-5»), team name; errors listed with links to the dialogs; «Скачать results.json» → `GET /api/export/results.json?group_id=&download=true`.
- P0 notes: «Выгрузка строится по чек-листу v1 (организаторский) и по ответам модели без правок ревьюеров»; target paths `results/dev/results.json` and `results/prod/results.json` with copy buttons; «Закоммитьте и отправьте файл до стоп-кода» (the dialog never claims the file was pushed). Roles: QA-аналитик, Руководитель, Админ.

### 5.12 Dynamics 365 embed (WP-1)

Stories: D365-01 (P0 subset).

- P0: `index.html?embed=1&conversation=<id>` renders the same card in the light theme whatever is stored, without mascot, read-only (no Evaluate, Re-evaluate, Appeal), with «Открыть в CQA» opening the full app in a new tab and «Диалог не найден в CQA» for an unknown id.
- P1: Dynamics web-resource parameters `data=` and `userlcid`.

## 6. Backend changes

Everything below is additive and keeps `docs/API.md` valid; the only behaviour change is B-1 (an empty id list), which the contract never defined as «all». Not touched by this proposal: `api/cqa/export/**`, `api/cqa/routers/export.py` and `scripts/control_run.*` (team lead, results.json), `api/cqa/judge/**`, `api/seeds/**`, `api/eval/**` (ML teammate). No new endpoint is needed for the four asks: violations, operators, datasets, appeal authorship, preview, decisions, peaks and results validation are already deployed; roles, overview, guards, version and checklist governance exist in the working tree.

| ID | Change | Files | Stories | Compatibility | Priority and owner |
|---|---|---|---|---|---|
| B-0 | Deploy the working tree: role middleware, `GET /api/roles`, `GET /api/me`, `GET /api/version`, run guards, job resume, checklist governance (`activate` flag, 422 `invalid_checklist`, `weights`, `export_ids`), overview and scorecards, snapshots, system health. Before: `pytest -q` green and a copy of the SQLite volume; after: snapshot `baseline`. | deploy | SHELL-04, SHELL-06, ADM-03, ADM-06, CHK-10, CHK-12, EXE-01, EXE-02, DEMO-04, DEMO-10, DEMO-16 | additive per «Architecture phase additions»; requests without a role header stay `demo` | P0 gate · team lead |
| B-1 | `conversation_ids_for`: `[]` returns `[]`, so `POST /api/evaluations/run` answers 400 `no_conversations`; `all: true` still means all, and `null` keeps today's meaning for old clients | `api/cqa/jobs/runner.py` (that function only), test | SYS-06, DEMO-06 | only `[]` changes | P0 · backend agent |
| B-2 | Call offsets anchored at the recording-start event («Запись и транскрибирование запущены» / «Жазу және транскрипция басталды» / «Recording and transcription started»); earlier turns get `offset_seconds = null`, `meta.recording_start_idx` is added, pauses as today. `python -m cqa.ingest.renormalize --channel "voice call"` rewrites offsets of stored calls in place (no re-fetch, turn idx unchanged, evaluations untouched) and deletes their peaks sidecars (`*.peaks*.json`) and cached features | `api/cqa/ingest/normalize.py`, new `api/cqa/ingest/renormalize.py`, test | CALL-03, CALL-01, CALL-02, CALL-09 | offsets change only for calls with a start event (p4-ru-002 ≈ −30 s; per CALL-03 the other calls move < 0,3 s) | P0 · backend agent; the ML owner is told that call acoustic facts for C6 shift (chats and the control set are unaffected) |
| B-3 | Evaluation payloads add `explanation_display` and `na_reason_display`: bracketed turn references `[n]` and ranges `[n]–[m]` inside the transcript length are shifted to 1-based when the evaluation's prompt version labels turns from 0 (all current ones: `pack_transcript` writes `[idx]`); stored text unchanged | `api/cqa/serializers.py` (+ helper), test | CARD-07, CARD-04, EXP-02 | additive fields | P0 · backend agent; the helper is offered to the results.json builder — renumbering rationales in the file is the team lead's call with the ML owner |
| B-4 | `requested_answer` (nullable) on appeals: accepted by `POST /api/appeals`, returned in appeal items | `api/cqa/models.py` (additive column, existing additive-migration helper), `api/cqa/routers/appeals.py`, serializer | APL-02 | additive; the UI falls back to the first comment line | P1 · at the integration step (models.py is shared) |
| B-5 | Violation items add `rule` (C8 text of the version), `turn_number` (idx + 1) and `classified_by` (`judge` / `keyword`) | `api/cqa/analytics/violations.py` | GV-02 | additive | P1 · backend agent |
| B-6 | Hosted configuration: `CORS_ORIGINS` = the Vercel origin + localhost; `MAX_DIALOGS_PER_RUN=25`, `MAX_CONCURRENT_JOBS=2`, `DAILY_BUDGET_USD=60` for the jury period; `CQA_ADMIN_TOKEN` set; `fewshot_from_reviews=false` until the ML owner freezes the few-shot bank | Railway variables, settings | SYS-07, ADM-03, DEMO-04, DEMO-07 | configuration | P0 · team lead (the few-shot value is the ML owner's decision) |
| B-7 | Re-judge the five DEV calls on C1 under the voice-call override: `POST /api/evaluations/run {conversation_ids: [p4-ru-001…005], mode: "partial", base_version_id: 1, only_codes: ["C1"]}`, ≈ $0,10 | operation | CALL-09 | — | P1 · ML owner decides (it changes judge output) |
| B-8 | Demo data through the UI after B-0: operator Алексей appeals p1-ru-029 C7 (stays open), a QA-аналитик appeals p4-ru-002 C1 asking N/A (stays open), a reviewer confirms an appeal on p1-ru-023 C8 (decided, few-shot off); then snapshot `before-jury`. Нина's p1-ru-005 C6 stays free for live ask 2 | operation, recorded in `docs/DEMO.md` | DEMO-03 | — | P0 · team lead with WP-9 |

## 7. Delivery plan

### 7.1 Rules for parallel agents in one repo

- One git worktree per package, branched from the WP-0 contract commit. A package edits only the files it owns (7.2); files owned by WP-0 (web core) and by the team lead (`api/` shared files) are frozen for everyone else after the contract commit — a package that needs a change there asks the owner.
- Contract first: within ≈ 45 minutes WP-0 commits the route table with stub pages, the API functions, hooks and types for every endpoint in 7.3, the role helpers, the DS component signatures (§4.4 props, minimal render) and the i18n namespace files. The styled implementation of the foundation follows by ≈ T+2,5 h; feature packages pick it up on rebase.
- Merge order: WP-0 → WP-8 (independent) → WP-1…WP-7 in any order → WP-9. A merge needs `pnpm typecheck && pnpm build` (web) or `pytest -q` (api) green after a rebase on main; Vercel previews per branch, production deploy at integration.
- Cut lines: at T+5 h every package drops its remaining P1 items; at T+7 h integration starts; nothing new after H+18 and only fixes until H+22 (`docs/PLAN.md`); the control hour follows the team lead's runbook on a frozen app.

### 7.2 Work packages (disjoint file ownership)

| WP | Owner | Owns (exclusive) | Needs | Stories | Estimate | Done when |
|---|---|---|---|---|---|---|
| WP-0 Foundation and shell | agent 1 | `web/index.html`, `web/public/favicon.svg`, `web/tailwind.config.ts`, `web/src/index.css`, `web/src/main.tsx`, `web/src/App.tsx`, `web/src/store/**`, `web/src/lib/**`, `web/src/api/**`, `web/src/components/**` except `components/ds/quality/**`, `web/src/i18n/index.ts`, `ru.ts`, `en.ts`, `kk.ts`, `web/src/i18n/ns/shell.ts`, `web/src/features/system/**`, `web/src/mock/**`, the stub files of 7.3 | — | SHELL-01, 02, 04, 05, 06, 07, 10; EXP-08 (drop the real call transcript from `mock/fixtures`) | 2–2,5 h | contract commit at ≈ T+45 min; §5.1 acceptance; typecheck and build green |
| WP-1 Card and calls | agent 2 | `web/src/features/card/**`, `web/src/components/ds/quality/**`, `web/src/i18n/ns/card.ts` | WP-0 contract; B-2, B-3 for full acceptance | CARD-01..08, CALL-01, 02, 09, 12, GV-03, APL-01, APL-09, D365-01 | 3,5 h | §5.3, §5.4, §5.12 acceptance |
| WP-2 Conversations, intake, results export | agent 3 | `web/src/features/conversations/**` (EvaluateDialog excluded), `web/src/features/export/**`, `web/src/i18n/ns/lists.ts`, `ns/export.ts` | WP-0 | CONV-04, 06, 07, 10, SHELL-10, EXP-01, EXP-05 | 2 h | §5.2, §5.11 acceptance |
| WP-3 Violations and operator portal | agent 4 | `web/src/features/violations/**`, `web/src/features/operator/**`, `ns/violations.ts`, `ns/operator.ts` | WP-0 | GV-01..04, OPR-01, 02, 04, 05 | 2,5 h | §5.5, §5.6 acceptance |
| WP-4 Appeals review | agent 5 | `web/src/features/review/**`, `ns/review.ts` | WP-0 | APL-05..09, SHELL-06, CAL-12 | 1,5–2 h | §5.7 acceptance |
| WP-5 Checklist Studio and Apply | agent 6 | `web/src/features/checklists/**`, `web/src/features/jobs/**` (EvaluateDialog moved here by WP-0, JobProgress, useJobProgress), `ns/checklists.ts`, `ns/jobs.ts` | WP-0; B-1; B-0 for drafts and 422 (falls back without) | CHK-01..06, 10, 12, 15, 16, SYS-06, DEMO-05, DEMO-12 | 3 h | §5.8 acceptance |
| WP-6 Analytics, overview, settings | agent 7 | `web/src/features/analytics/**`, `web/src/features/settings/**`, `ns/analytics.ts`, `ns/settings.ts` | WP-0; B-0 for `/api/overview` (falls back to the summary); imports WP-2's `ResultsExportDialog` (stub from WP-0) | EXE-01, 02, ANL-02, 03, 12, SHELL-02, ADM-01, DEMO-04 | 2 h | §5.9, §5.10 acceptance |
| WP-7 Landing and jury scenarios | agent 8 | `web/src/features/landing/**`, `web/src/features/jury/**`, `ns/landing.ts`, `ns/jury.ts`, `web/public/og-card.png` (P1) | WP-0; WP-1 quality components for the hero card (a snapshot render until they land) | LAND-01..03, DEMO-01; SHELL-15 (P1) | 3 h | §3 and the jury-panel part of §5.1; P0 sections: header, hero, counters, how it works, audience, data under control, final CTA, footer; P1: problem, features, languages, comparison, FAQ |
| WP-8 Backend for the demo | agent 9, reviewed by the team lead | `api/cqa/jobs/runner.py` (`conversation_ids_for` only), `api/cqa/ingest/normalize.py`, `api/cqa/ingest/renormalize.py` (new), `api/cqa/serializers.py`, `api/cqa/analytics/violations.py` (P1), `api/tests/test_demo_backend.py` (new) | — | SYS-06, CALL-03, CARD-07, GV-02 | 1,5 h | B-1…B-3 tests green offline; renormalisation run on the hosted calls |
| WP-9 Readiness and docs | team lead + one agent | `docs/DEMO.md`, `docs/WEB.md`, the «Live demo» lines of `README.md`, B-6 and B-8 operations | all merged | DEMO-02, 03, 04, 17 | 2 h + rehearsals | the go / no-go list (7.5) passes twice |

### 7.3 Contracts WP-0 publishes in the contract commit

- Routes in `App.tsx` with stub pages: `features/landing/LandingPage.tsx`, `features/violations/ViolationsPage.tsx`, `features/operator/MyPage.tsx`, `features/system/NotFoundPage.tsx`, `features/jury/JuryPanel.tsx`, `features/export/ResultsExportDialog.tsx`, `components/ds/quality/index.ts`; `features/conversations/EvaluateDialog.tsx` moves to `features/jobs/EvaluateDialog.tsx` with a re-export at the old path (props unchanged; WP-5 extends them additively: `scopeOptions`, `defaultScope`, `onCompleted(summary)`).
- `web/src/api`: `violations(params)`, `operators()`, `datasets()`, `listConversations({operator, dataset_id, …})`, `appeals({status, operator, conversation_id, author_role})`, `createAppeal({evaluation_criterion_id, comment, author_role, author_name, author_label})`, `decide(id, {action, new_answer, reason, reviewer_role, reviewer_label, add_to_fewshot})`, `preview(...)`, `peaks(id, buckets)`, `roles()`, `me()`, `version()`, `overview(params)`, `checklistActive()`, `createVersion(id, body & {activate?})`, `validateResults({group_id, conversation_ids, expect})`, `resultsDownloadUrl({group_id})`, `run(body & {only_codes?})`, with TanStack hooks and types; role headers added by `client.ts` on writes only.
- Roles: `lib/roles.ts` (catalogue mirroring `api/cqa/security/roles.py`: code, labels, description, home, nav, permissions), `useRole()` (role, setRole, identity, setIdentity, displayName), `can(permission)`, `<RequireRole>` (read-only fallback), `useCapabilities()` (the `/api/version` probe).
- Formatting: `fmtScore`, `fmtPoints`, `fmtTurn`, `fmtMoney`, `fmtDuration`, all locale-aware.
- i18n: `web/src/i18n/ns/<name>.ts` export `{ ru, en, kk }` through a `defineNs` helper that type-checks EN and KK against RU, merged by `i18n/index.ts`; the old dictionaries change only in WP-0 (nav «Апелляции», seven role labels).

### 7.4 Schedule

Indicative clock, assuming H+0 ≈ 14:00 on 07.11 and T = WP-0 start ≈ 20:45 (H+6,75).

| Window | Work |
|---|---|
| T → T+0:45 | WP-0 contract commit; WP-8 starts; team lead prepares B-0 (tests, volume copy) |
| T+0:45 → T+2:30 | WP-1…WP-7 build on the stubs; WP-0 finishes the styled foundation; WP-8 reviewed and merged; B-0 deployed with B-6 |
| T+2:30 → T+5:00 | P0 of every feature package done and merged; renormalisation run on the hosted calls |
| T+5:00 → T+7:00 (≈ H+13,75) | P1 only where a package is green; integration, production deploy, cross-package fixes |
| T+7:00 → T+9:00 (≈ H+15,75) | WP-9: demo data (B-8), snapshot `before-jury`, `docs/DEMO.md` in the guide's order, two timed rehearsals at 1280×720, bug list |
| until H+18 | fixes from rehearsals only, then freeze |
| H+22 → H+24 | control hour by the team lead's runbook; the app is not redeployed; `before-jury` restored after any rehearsal on the hosted instance |

With only four coding agents: A takes WP-0 then WP-6; B WP-1 then WP-7 (reusing the quality components); C WP-3 then WP-4; D WP-5 then WP-2; WP-8 goes to the team lead or a fifth agent. If time runs out, the order of sacrifice is: landing P1 sections → analytics P1 → settings P1 → studio P1 (diff values) → operator portal P1; the four asks are never cut.

### 7.5 Go / no-go before the defence (run twice)

1. Hosted web and API on the merged commit (`/api/version` answers; the web footer shows the same commit).
2. Light theme and no mascot on the landing and on five app routes.
3. `#/violations` shows 8 with 1 / 4 / 3; «Открыть в диалоге» lands on the peach turn.
4. p1-ru-005 C6 has no open appeal; the seeded appeals exist (p1-ru-029 C7 and p4-ru-002 C1 open, p1-ru-023 C8 decided).
5. Checklist v1 active; no rehearsal drafts visible, or `before-jury` restored.
6. p4-ru-002 audio played once (cached) and renormalised (turn №15 at ≈ 02:00).
7. Budget left ≥ $20; guards on; few-shot setting as the ML owner decided.
8. Snapshot `before-jury` younger than two hours; the restore command tested once.
9. Card, violations page and studio checked on a 1280×720 projector and at 110 % zoom.
10. The 5-minute defence (guide order: interface and a dialog → evaluation, justification, jump, N/A, total → appeal, decision, new total → criterion change and re-apply) under 4:45 twice; each live ask under 1:30.
11. Fallbacks ready: local Docker with a downloaded snapshot, a screen recording of the run, the README links.

### 7.6 Not built before the stop-code (cut on purpose)

| Area | Stories cut | Why it is safe |
|---|---|---|
| Calibration UI | CAL-01..14 | the API exists; the ML teammate works with the harness |
| Admin consoles | ADM-05, ADM-07, ADM-08, ADM-09, ADM-10, DEMO-13, DEMO-18 | the team operates through the API and `scripts/control_run.py`; the jury never needs them |
| Teams and coaching | ANL-05 page, ANL-06, ANL-07, ANL-08, OPR-06, OPR-07, OPR-08 | no live ask depends on them |
| Notifications, sharing, help | SHELL-12, SHELL-13 (beyond list and register filters), SHELL-14 | the jury panel covers orientation |
| Phone and tablet app layouts | SHELL-11, SHELL-16 | the defence runs on a laptop and a projector; the landing is responsive |
| Checklist depth | CHK-07, CHK-08, CHK-09 (except delete confirm and undo), CHK-11, CHK-14, CHK-17..20, CHK-22..28 | add, change, replace, reweight, save, apply and the v1 lock answer the ask |
| Card depth | CARD-09 (P1 badge only), CARD-10, CARD-11, CARD-13..17 | the card already answers ask 4 |
| Call depth | CALL-04..08, CALL-10, CALL-11, CALL-13..16 | the waveform, the jump and the C1 rule are enough for a call |
| Appeal depth | APL-02 storage (B-4, P1), APL-03, APL-04, APL-11..20 | decided appeals are carried by partial runs in the working tree (APL-10); the demo applies changes only to dialogs without open appeals |
| Intake depth | CONV-01 preview, CONV-02, CONV-03 (CLI has the fallbacks), CONV-05 (P1), CONV-08 (P1), CONV-09, CONV-11..16 | DEV and the control set import by group_id today |
| Analytics depth | ANL-01, ANL-04 (P1), ANL-05..11, ANL-13, EXE-03..09 | the overview band answers the director question |
| Dynamics 365 and exports | D365-02..06, EXP-03 (P1), EXP-04, EXP-06, EXP-07 | the embed works; results files are the team lead's flow |
| Platform | SYS-04, SYS-05, SYS-07 (beyond CORS), SYS-08..12 | the guards and resume in the working tree cover the demo |

## 8. Risks

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| 1 | Deploying the large uncommitted backend (B-0) late breaks the hosted instance | medium | high | deploy in the first three hours, not at H+20; copy the volume first; smoke-test the four asks with curl; the web app's capability probe keeps it working against the old API; rollback = previous Railway deployment + the copy | team lead |
| 2 | 0-based numbers in explanations («реплика [9]») next to 1-based chips («№10»); results.json rationales cite [9] while `evidence_turns` say 10 | certain today | medium: a juror reading closely sees two numbers for one message | B-3 display renumbering on the card; the file's rationales are the team lead's and ML owner's decision | backend agent, team lead, ML owner |
| 3 | Rehearsal and jury decisions become few-shot examples and shift the judge before the control run (hosted `fewshot_from_reviews = true`, UI switch defaults to on) | high | high: accuracy is the main criterion | switch off by default (WP-4), `fewshot_from_reviews=false` until the ML owner freezes the bank (B-6), seeded decisions without few-shot | ML owner decides, team lead applies |
| 4 | Jurors change the shared instance during hands-on (activate a v2, file appeals, paste dialogs) | high | medium | v1 lock, saves as drafts, paste never overwrites, results.json built from v1, `before-jury` restored afterwards, administrator not in the switcher, `CQA_ADMIN_TOKEN` on restores | team lead |
| 5 | Budget burnt by «Оценить все» or repeated runs | medium | medium | B-6 limits, confirmation above 20 dialogs with the estimate, dry run first; local and fake providers never blocked | team lead |
| 6 | p4-ru-002 shows C1 Нет against the organizers' call rule, and call jumps land ≈ 30 s late | certain until B-2 and B-7 | medium if the jury picks the call | B-2 now; the C1 banner explains the rule; B-7 if the ML owner agrees; otherwise ask 4 is answered on p1-ru-029 and the call is used for the player only | backend agent, ML owner |
| 7 | Slow first clicks: cold start and a cross-origin API at a European edge | medium | medium | reads without custom headers (no preflight), skeletons, a warm-up pass through the seven scenarios ten minutes before the slot, audio prefetched | WP-0, WP-9 |
| 8 | Recording links expire (signed for ≈ 1 h) | medium | low once cached | play each call once after deploy (cached on the volume); transcript-only fallback (§5.4) | WP-9 |
| 9 | Eight parallel agents produce conflicts or a red build late | medium | high | disjoint ownership, contract commit, namespaced i18n, merge order, build gate per merge, a two-hour integration window | team lead |
| 10 | Kazakh copy is wrong or missing | medium | low to medium | a Kazakh speaker checks the hero, navigation, role labels and the card; RU fallback for missing keys, typed namespaces catch gaps | team |
| 11 | The landing over-claims (cost, local model, accuracy) | medium | high for credibility | only live numbers with stamps, «поддерживается» for the local model until it is reachable, no accuracy figure until the ML owner publishes one | WP-7, team lead review |
| 12 | Real client data in the public repository: the real call transcript in `web/src/mock/fixtures`, a spelled-out name in the CALL-07 acceptance of `docs/USER_STORIES.md` | certain today | high for the repository check | WP-0 removes or masks the fixture; the team lead rewords CALL-07 before committing the stories file | WP-0, team lead |
| 13 | A 1280×720 projector or browser zoom hides the card's buttons | medium | medium | acceptance at 1280×720 and 110 %; sidebar collapse; quality card first below 1100 px | WP-1, WP-9 |
| 14 | Typed text is lost when the role is switched in the middle of a live ask | low | medium | the script finishes each action before switching; P1 guard | WP-9 |
| 15 | A teammate rehearses on the hosted instance while the jury clicks | low to medium | medium | no writes on the hosted instance during the slot; rehearse locally or before `before-jury` | team lead |
| 16 | Contradictory verdicts weaken the answer to «а точно других грубых нет?» (p1-ru-018 and p4-ru-005: C3 rationale describes disclosure, C8 = Нет) | medium | medium | handed to the ML owner now (GV-08 evidence); the register shows the related C3 verdict on disclosure rows and nothing invented | ML owner |
| 17 | Mock mode lacks the new endpoints | certain | low | mock stays labelled «Офлайн-демо» and is not the jury fallback; the local Docker app with a downloaded snapshot is | WP-0 |
| 18 | The single-file Dynamics 365 build grows | low | low | no font files, lazy routes, mascot assets out of the main bundle; check `dist-single` < 4,5 MB at integration | WP-0 |

### Decisions for the team lead

| Decision | Recommendation |
|---|---|
| First open of an app route | QA-аналитик by default, no blocking picker; the landing offers role entries |
| Administrator in the switcher | hidden; reachable with `?role=admin` |
| Hero card on the landing | p1-ru-029 (51,25, gross error) — shows reasons, evidence and the violation flag at once |
| Apply scope for live ask 3 | p1-ru-022, p1-kz-013, p1-en-002 (RU · KZ · EN, no appeals) |
| Few-shot from reviewer decisions | off until the ML owner freezes the bank |
| Rationale numbering in results.json | decide with the ML owner whether to use the B-3 helper in the builder |
| Live local-model evaluation in the defence | only if the ML endpoint answers at the go / no-go check |
| When to deploy B-0 | now, before the feature packages merge |
