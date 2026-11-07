# Information architecture (final)

Routes, navigation, deep links and the embed for the CQA web app. Final synthesis of `docs/design/proposal-demo.md` (winner) with the grafts and cuts from the judges; roles in `docs/ROLES.md`; who builds each screen in `docs/BUILD_BACKLOG.md`. Router: react-router `HashRouter` (unchanged). The backend contract (`docs/API.md`, `docs/FRONTEND_INTEGRATION.md`) is unchanged; every route below runs on endpoints that exist in the working tree (deployed with B-0) and degrades to the currently deployed API where noted.

## 1. Principles

- **Landing outside the app, app behind «Открыть демо».** `#/` is the corporate landing (bare layout, no sidebar). Every other route uses the app shell.
- **No top bar.** The shell is the mockup's deep-green sidebar plus the page. At a 1280×720 projector every pixel of height goes to content. Role, language, API status and «Сценарии для жюри» live in the sidebar.
- **Deep links are first-class.** Every live-ask state has a URL (`?criterion=C8&turn=10`, `?type=disclosure&language=KZ`, `?paste=1`). URLs are shareable and survive a cold load.
- **No dead ends, no silent redirects for unknown things.** An unknown route shows «Страница не найдена»; an unknown dialog shows «Диалог не найден» with a way back. Redirects exist only for role policy (§5).
- **Turn numbers in URLs and on screen are 1-based** (`turn=10` = transcript array index 9 = results.json `evidence_turns: [10]`).

## 2. Route map

Layouts: **bare** = no shell (landing, embed); **app** = sidebar shell. «Write roles» are the roles that see the screen's write actions; everyone else in the «Roles» column sees it read-only.

| Route | Screen (page title RU / EN) | Layout | Roles | Write roles | URL parameters | P | Owner | Stories |
|---|---|---|---|---|---|---|---|---|
| `#/` | Landing | bare | all | — | `role`, `lang` | P0 | WP-9 (+ WP-10 sections) | LAND-01..03 |
| `#/overview` | Обзор / Overview | app | lead, director (home), analyst, reviewer, methodologist, visitor, admin | — (read-only page) | `days` (30), P1: `language`, `channel` | P0 | WP-7 | EXE-01, EXE-02, ANL-12 |
| `#/conversations` | Диалоги / Conversations | app | all but operator | import: analyst, methodologist, lead, admin; evaluate: + reviewer | `q`, `language`, `channel`, `status`, `critical=1`, `sort`, `paste=1`, `export=results` | P0 | WP-6 | CONV-04..07, CONV-10, EXP-01 |
| `#/conversations/:id` | Анализ разговора / Conversation analysis | app | all; operator: own dialogs (others read-only) | appeal: operator (own), analyst, reviewer, lead, admin; re-evaluate: analyst, reviewer, methodologist, lead, admin | `criterion` (code), `turn` (1-based), `t` (seconds, calls), `tab` (`result` · `history` · `trace` · `audio`) | P0 | WP-2 (page, transcript, calls) + WP-1 (quality panel, appeal) | CARD-01..08, CALL-01..03, CALL-09, CALL-12, GV-03, APL-01, APL-09 |
| `#/violations` | Грубые нарушения / Gross violations | app | all but operator | — | `type` (`insult` · `paid_bypass` · `disclosure` · `other`), `language`, `channel`, P1: `reviewed=1`, `tab=near` | P0 | WP-3 | GV-01..04 |
| `#/review` | Апелляции / Appeals | app | analyst, reviewer, lead, admin (+ others read) | decide: reviewer, lead, admin | `status` (`open` · `decided`) | P0 | WP-4 | APL-05..09 |
| `#/review/:appealId` | Апелляция №… | app | as above | as above | — | P1 | WP-4 | APL-13 |
| `#/my` | Мои оценки / My evaluations | app | operator | appeal on own dialogs | — | P0 | WP-3 | OPR-01, OPR-02, OPR-04 |
| `#/my/appeals` | Мои апелляции / My appeals | app | operator | withdraw own open appeal (P1) | `status` (`open` · `decided` · `all`) | P0 | WP-3 | OPR-05 |
| `#/operators/:name` | Оператор · {имя} (scorecard «Фокус коучинга») | app | analyst, reviewer, methodologist, lead, director, admin | — | — | P1 | WP-3 | ANL-06 |
| `#/checklists` | Чек-листы / Checklists | app | all | methodologist, lead, admin | — | P0 | WP-5 | CHK-01, CHK-14 |
| `#/checklists/:id/versions/:vid` | Чек-лист · vN (editor; read-only for non-writers) | app | all | methodologist, lead, admin; apply: + analyst | `criterion`, `apply=1`, `diff=<vid>` | P0 | WP-5 | CHK-02..06, CHK-10, CHK-12, CHK-13, CHK-15, CHK-16, CHK-18 |
| `#/analytics` | Аналитика / Analytics | app | all but operator | — | `checklist_version_id` | P0 (restyle) | WP-7 | ANL-02, ANL-03 |
| `#/settings` | Настройки / Settings | app | all | personal: all; system: admin | `section` (`appearance` · `profile` · `model` · `export` · `readiness` · `about`) | P0 | WP-7 | SHELL-02, ADM-01, ADM-02, EXP-03 |
| `*` | Страница не найдена / Page not found | app | all | — | — | P0 | WP-0 | SHELL-07 |
| `index.html?embed=1&conversation=<id>` | Embedded card (Dynamics 365 form tab) | bare | host-provided role or visitor | none (read-only) | `conversation`, `data`, `id`, `userlcid`, `host`, `api`, `criterion`, `turn` | P0 | WP-2 (view) + WP-0 (parameters) | D365-01 |

Global parameters on any route (WP-0): `role`, `as` (operator name), `tour=1` (opens «Сценарии для жюри»), `lang` (`ru` · `kk` · `en`), `api` (API base override, exists), `mock=1` (offline fixtures, exists; shows a permanent «Офлайн-демо» badge in the sidebar). `role`, `as` and `tour` are consumed on load and removed from the address bar; `api` and `mock` stay.

Not built (cut): `#/start`, `#/operators` (directory), `#/admin/*`, `#/audit`, `#/calibration`, `#/team`, `#/knowledge`, print views (`?print=1`), multi-view embeds (`view=violations|review|overview|operator`). Nav items for them are never shown, because the shell hides items whose route is not registered.

## 3. Shell anatomy

```
┌──────────────────────┬───────────────────────────────────────────────────────────────────┐
│ CQA                  │ ← Грубые нарушения                                                 │
│ Freedom Broker       │ Анализ разговора                         [Экспорт PDF] [Переоценить] [⋯] │
│ ┌──────────────────┐ │ Чат · p1-ru-029 · Русский · оператор Алексей · 14 реплик          │
│ │ Роль             │ │ ┌ Расшифровка ──────────── ⌕ ┐ ┌ Оценка качества · История · Трассировка ┐ │
│ │ QA-аналитик ·    │ │ │ До оператора · 9 реплик ▾  │ │ ▌Грубое нарушение: оскорбление · №10    │ │
│ │ Жанар Б.       ▾ │ │ │░01:23 Оператор        №10░│ │ (51,25)  ✓ Установление контакта 12,5/12,5│ │
│ └──────────────────┘ │ │░Вы вообще читать умеете?░│ │ / 100    ✕ Выявление потребн.  0/18,75 │ │
│ ▣ Диалоги            │ │ 01:52 Клиент          №11 │ │          … all rows, grouped S1–S4     │ │
│ ⛨ Грубые нарушения 8 │ │ …                         │ │ Почему снижена оценка                  │ │
│ ⚖ Апелляции        1 │ │                           │ │ [Перейти к фрагменту] [Оспорить оценку]│ │
│ ☑ Чек-листы          │ └───────────────────────────┘ └────────────────────────────────────────┘ │
│ ▥ Аналитика          │                                                                     │
│ ──────────────────── │                                                                     │
│ ▷ Сценарии для жюри  │                                                                     │
│ ⚙ Настройки          │                                                                     │
│ RU · KK · EN     ● API│                                                                    │
└──────────────────────┴───────────────────────────────────────────────────────────────────┘
```

- Sidebar 240 px; auto-collapses to 72 px below 1280 px viewport width (toggle remembered in `cqa.nav`); content padding 32 px (24 px below 1280 px), max content width 1440 px.
- Order inside the sidebar: wordmark «CQA» (+ «Freedom Broker» small) → role pill (`docs/ROLES.md` §3.1) → upper nav group → divider → lower group (shell slots such as «Сценарии для жюри», then «Настройки») → footer row: RU · KK · EN segmented switch and the API status dot (green; red after two failed `GET /api/health`; amber «Офлайн-демо» in mock mode; tooltip shows the API host).
- Icons (lucide, 20 px, stroke 1.75): Мои оценки `ClipboardCheck`, Мои апелляции `Scale`, Обзор `LayoutDashboard`, Диалоги `MessageSquareText`, Грубые нарушения `ShieldAlert`, Апелляции `Scale`, Чек-листы `SquareCheckBig`, Аналитика `ChartColumn`, Сценарии для жюри `PlayCircle`, Настройки `Settings`.
- Badges: white pill 20 px with `#0F5431` digits 12/16 600. Sources: `GET /api/violations` → `total`; `GET /api/appeals?status=open` → length; «Мои апелляции» → decided appeals of the operator not yet opened (local read state). Refetched on window focus and after every appeal or decision mutation; no polling.
- Page anatomy: optional back link (13/500, `--text-2`), title 32/38 700 (28/34 below 1280 px or in compact density), meta line in `--text-3` joined by « · », actions on the right (at most one primary). There is no second toolbar.
- `document.title` per screen (P1, `usePageTitle`): «Грубые нарушения (8) · CQA», «p1-ru-029 · Анализ разговора · CQA», «Чек-лист v2 · черновик · CQA», «Мои оценки · Нина · CQA».

## 4. Navigation per role

| Role | Home | Upper group (in order) | Badges |
|---|---|---|---|
| Оператор | `#/my` | Мои оценки · Мои апелляции · Чек-лист | Мои апелляции |
| QA-аналитик | `#/conversations` | Диалоги · Грубые нарушения · Апелляции · Чек-листы · Аналитика | Грубые нарушения, Апелляции |
| Ревьюер | `#/review` | Апелляции · Диалоги · Грубые нарушения · Чек-листы · Аналитика | Апелляции, Грубые нарушения |
| Методолог | `#/checklists` | Чек-листы · Диалоги · Грубые нарушения · Аналитика | Грубые нарушения |
| Руководитель группы | `#/overview` | Обзор · Диалоги · Грубые нарушения · Апелляции · Чек-листы · Аналитика | Апелляции, Грубые нарушения |
| Директор | `#/overview` | Обзор · Грубые нарушения · Аналитика · Диалоги · Чек-листы | Грубые нарушения |
| Гость | `#/` | Обзор · Диалоги · Грубые нарушения · Аналитика | — |
| Администратор | `#/settings` | Обзор · Диалоги · Грубые нарушения · Апелляции · Чек-листы · Аналитика | Апелляции, Грубые нарушения |

The labels follow the organizer mockup («Апелляции», not the backend hint «Очередь ревью»). The operator's «Чек-лист» item opens the active version read-only.

## 5. Role policy and redirects

| Situation | Result |
|---|---|
| `#/` with the landing registered | landing |
| `#/` without the landing (before WP-9 lands) | redirect to the role's home |
| Оператор opens `#/conversations` | redirect to `#/my` |
| Оператор opens `#/review` | redirect to `#/my/appeals` |
| Оператор opens `#/violations`, `#/overview`, `#/analytics` | read-only notice «Раздел для сотрудников контроля качества» + «Вернуться к моим оценкам» |
| Any other role opens `#/my` | notice «Раздел оператора» + «Войти как оператор» (opens «Кто вы?») |
| A role without the screen's write permission | the screen renders; write controls hidden or disabled with «Доступно роли …»; write-only screens show «Раздел доступен роли «X». Вы работаете как «Y» — изменения недоступны.» + «Переключиться на роль X» |
| A role's home not registered yet | fallback `#/conversations` |
| Unknown route | «Страница не найдена» with «К моей главной» and «На лендинг» |
| Unknown dialog id | the card shows «Диалог не найден» with «← Диалоги»; never a redirect |
| Unknown `criterion` or `turn` on the card | the card loads normally and shows a quiet «Фрагмент не найден» |

## 6. Back links and cross-screen state

- A screen that opens the card passes router state `from: {path, label}` (helper `linkState()` from WP-0). The card's back link then reads «← Грубые нарушения», «← Мои оценки», «← Апелляции», «← Диалоги» (default).
- List and register filters live in the URL, so Back restores them. Scroll position is restored by the browser for the hash route.
- The card writes `criterion`, `turn` and `tab` with `replace` (no history spam); filters on lists write with `replace` too.

## 7. Deep links (jury panel, `docs/DEMO.md`, slides, README)

| Purpose | Link |
|---|---|
| Live ask 1 | `#/violations` · filtered: `#/violations?type=disclosure&language=KZ` (only p1-kz-015) |
| Live ask 2, operator | `#/my?role=operator&as=Нина`, then `#/conversations/p1-ru-005?criterion=C6` |
| Live ask 2, reviewer | `#/review?role=reviewer` |
| Live ask 3 | `#/checklists?role=methodologist` (the jury panel resolves the active version: `#/checklists/<id>/versions/<vid>`) |
| Live ask 4 | `#/conversations/p1-ru-029?criterion=C8&turn=10` |
| Call with jump to the moment | `#/conversations/p4-ru-002?criterion=C6` (turn №15 at ≈ 02:00 after renormalisation) |
| New dialog live | `#/conversations?paste=1&role=analyst` |
| results.json export | `#/conversations?export=results&role=analyst` |
| Executive view | `#/overview?role=director` |
| Scorecard (P1) | `#/operators/Алексей?role=lead` |
| Jury scenarios | any app route with `?tour=1` |
| Dynamics 365 embed | `index.html?embed=1&conversation=p1-ru-029` · D365 form: `index.html?id=<guid>&data=conversation%3Dp1-ru-029%26api%3D<host>&userlcid=1049` |

## 8. Cross-screen flows (click counts on hosted data)

| Flow | Path | Clicks |
|---|---|---|
| Ask 1: gross errors and why | sidebar «Грубые нарушения 8» → row «Почему так классифицировано» → «Открыть в диалоге» (C8 selected, №10 in peach) → «← Грубые нарушения» keeps filters | 1 (+2 per dialog) |
| Ask 2: operator appeal and decision | role pill → Оператор → Нина → p1-ru-005 → «Оспорить оценку» (C6, 7, comment) → «Отправить» → role pill → Ревьюер → «Апелляции» → «Изменить» → 7 → reason → «Сохранить решение» → role pill → Оператор → «Мои апелляции» | ≈ 12 + two typed fields, ≈ 90 s |
| Ask 3: change the checklist | role pill → Методолог → «Чек-листы» → «Создать версию на основе v1» → add C9 / edit / C7 weight → «Нормализовать до 100» → «Сохранить как черновик» → «Применить к диалогам» → «Пример: 3 диалога» → «Запустить» | ≈ 10, ≈ 2 min with the job |
| Ask 4: one dialog's result | any list row → card | 1 |
| New dialog | «Диалоги» → «Загрузить или вставить» → paste → preview → «Создать и оценить» → card fills in ≈ 20 s | 4 |
| Call | jury scenario «Звонок» → C6 «Перейти к фрагменту» → playback from ≈ 02:00 | 2 |
| Director | landing «Для кого» → Директор → `#/overview` → risk tile → `#/violations?type=paid_bypass` | 3 |

## 9. Embedded card for Dynamics 365

- Entry: `index.html?embed=1&conversation=<id>`, or the Dynamics 365 web-resource form: D365 passes only `id`, `typename`, `type`, `orgname`, `userlcid`, `orglcid` and `data` (`docs/INTEGRATION_D365.md` §5).
- Parameters parsed by `web/src/lib/config.ts` (WP-0): `data` (URL-encoded `conversation=…&api=…&lang=…`), `id` (record GUID, braces removed, lower case; used as the conversation id when `conversation` is absent), `userlcid` (1049 → RU, 1033 → EN, 1087 → KK), `host` (`d365`), and `window.CQA_CONFIG = {apiUrl, embed, conversation, lang, role, user, host}`. The role and the user come only from `CQA_CONFIG`, never from the URL.
- View (WP-2): the same card in the light theme whatever is stored, no mascot, no sidebar, read-only (no «Оценить», «Переоценить», «Оспорить оценку»), «Открыть в CQA» (new tab, same deep link plus `?role=&as=` from the host), compact states «Диалог не найден в CQA», «Оценки пока нет», «CQA недоступен · Повторить». Storage blocked by the iframe sandbox falls back to memory.
- Host density (P1): `host=d365` sets `data-host="d365"` on `<html>`: Segoe UI first in the font stack, 14 px base, 8 px radii, 16 px card padding (`docs/DESIGN_SYSTEM.md` §8).
- The single-file build (`pnpm build:single`) keeps working and stays below 4,5 MB.

## 10. How routes get registered (contract summary)

- `web/src/App.tsx` (WP-0) holds the built-in table for the existing screens (`/conversations`, `/conversations/:id`, `/checklists`, `/checklists/:id/versions/:vid`, `/review`, `/analytics`, `/settings`), the role-home redirect on `/` and the `*` not-found route.
- Every other route is contributed by a package in `web/src/features/<feature>/route.tsx` and discovered with `import.meta.glob`; a discovered route replaces a built-in route with the same path. Nobody edits `App.tsx` after the WP-0 contract commit.
- Owners keep the page files the built-in table imports (`ConversationsPage.tsx`, `ConversationCardPage.tsx`, `ChecklistsPage.tsx`, `ChecklistEditorPage.tsx`, `ReviewPage.tsx`, `AnalyticsPage.tsx`, `SettingsPage.tsx`, each with a default export).
- Full signatures: `docs/BUILD_BACKLOG.md` §3.
