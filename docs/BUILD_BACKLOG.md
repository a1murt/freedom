# Build backlog (final): the corporate redesign in 12 work packages

The execution plan for the redesign of `web/` before the stop-code. It synthesises the three design proposals in `docs/design/` (winner: `proposal-demo.md`, «win the live demo») with the judges' grafts and cuts. Companion specs, all final: `docs/ROLES.md` (roles, permissions, attribution), `docs/IA.md` (routes, navigation, deep links, embed), `docs/DESIGN_SYSTEM.md` (tokens, type, components), `docs/LANDING.md` (landing structure and copy). Binding over everything: `docs/ORGANIZER_UPDATES.md`. Story IDs: `docs/USER_STORIES.md`.

Written 2026-11-07 ≈ H+7,5 (H+0 ≈ 14:00 UTC+5). Control set at H+23, stop-code at H+24.

## 0. Read this first

- **Backend: no coding package tonight.** The working tree already holds everything the redesign needs: roles middleware with `GET /api/roles` and `/api/me`, `GET /api/version`, run guards and job resume, checklist governance (`activate` flag, 422 `invalid_checklist`, `weights`, `export_ids`), `POST /api/checklists/{id}/versions/{vid}/impact` (what-if, zero model calls), `GET /api/overview`, `GET /api/operators/{name}` (scorecard with coaching points), `GET /api/violations` with rules, related C3, open appeal and decision, `GET /api/violations/candidates`, `GET /api/system/readiness`, snapshots, the empty-selection fix (400 `no_conversations`), call offsets anchored at the recording start (`meta.not_in_recording`) with `python -m cqa.ingest.renormalize`, 1-based turn references in every explanation the API returns (`api/cqa/turnrefs.py`), paste dry-run preview with steering detection and 409 `conversation_exists`, appeal withdraw. The team lead deploys it first (OP-1, §5). `docs/API.md` stays valid; nothing here changes the API contract.
- **Not ours:** `api/**` (team lead; `api/cqa/judge/**`, `api/seeds/**`, `api/eval/**` belong to the ML teammate), `results/**`, `scripts/control_run.*`, the results.json builder and CLI. The UI only adds the export dialog.
- **Each package brief in §4 is self-contained**: an agent reads its package, §3 (shared contracts) and the spec sections it names.

## 1. Decisions (how the judges' conflicts were resolved)

| # | Topic | Decision |
|---|---|---|
| D1 | Shell | The mockup's deep-green sidebar, no top bar. Role pill at the **top** of the sidebar (corporate graft; the role switch of live ask 2 must be visible on a projector). |
| D2 | Admin and guest | «Ещё роли» holds Гость always and Администратор only after `?role=admin` was opened in that browser: the corporate structure plus the demo's rule that a juror cannot reach admin by accident. |
| D3 | First open | QA-аналитик by default; no blocking role picker, no `#/start`, no role dialog behind «Открыть демо». |
| D4 | Role contract | Codes, homes and permissions exactly as `api/cqa/security/roles.py`: lead stays «Руководитель группы», lead and director home `#/overview` (a real page), admin home `#/settings`, visitor home `#/`. |
| D5 | Turn numbers | Explanations are rendered exactly as the API returns them (already 1-based). No `explanation_display`, no client renumbering. Chips, transcript and URLs use `idx + 1` through `fmtTurn`. |
| D6 | Backend | Only B-0 (deploy the working tree) and configuration. All other backend features from the runner-up proposals are cut. |
| D7 | Dynamics 365 | P0: parameter parsing (`data=`, `id=`, `userlcid` 1049/1033/1087, `window.CQA_CONFIG` role/user/host), the read-only single-card embed with «Открыть в CQA», and a landing block with a live framed embed. P1: host density (`data-host="d365"`, one CSS rule). Cut: multi-view embeds, appeals in the embed. |
| D8 | Management story | `#/overview` is the director's and lead's home (band, risk tile, cost, languages). P1: a read-only scorecard «Фокус коучинга» at `#/operators/:name` on `GET /api/operators/{name}`, sharing the «Мои оценки» summary. Cut: operators directory, coaching notes, teams. |
| D9 | Reproducibility | «Как посчитан итог» (P0) from the payload; «Пересчитать» (P1) via `POST /api/appeals/preview` with the current answers (read permission), not a new endpoint. |
| D10 | What-if | The weights panel shows a live line from the shipped `…/impact` endpoint (P0 when the API supports it). |
| D11 | Readiness and seed | `GET /api/system/readiness` (not `/api/admin/readiness`), `scripts/smoke.py`, new read-only `scripts/smoke_asks.py`, `scripts/seed_demo.py` (already leaves p1-ru-005 and p1-ru-029 untouched), snapshot name `baseline`. No UI seeding. |
| D12 | Requested answer | First comment line «Предлагаемый ответ: 7 из 10» (P0); the review card parses it and also reads `requested_answer` if the team lead ever adds it (OP-7, P1). |
| D13 | Headers | Role headers and `X-Request-ID` on writes only; reads header-free (no CORS preflight to Railway); the response `X-Request-ID` (exposed) is shown on every error toast (P0). `/api/me` is not used. |
| D14 | Four eyes | P0 warning plus second confirmation when the reviewer's signature equals the appeal author. |
| D15 | Few-shot | The UI always sends `add_to_fewshot: false` (the API default is `true`); the switch is shown off and disabled with the reason; the hosted setting stays false until the ML owner freezes the bank. |
| D16 | Cost copy | «≈ $0,22 за оценку · среднее по 75 оценкам», never «все вызовы учтены»; one «Модель и стоимость» statement generated from live data with «Копировать для README». |
| D17 | Bands | Defined once in `web/src/lib/bands.ts`: ≥ 85 Норма, 60–85 Внимание, < 60 Критично. Checklist-defined bands (CHK-28 proper) are cut. |
| D18 | Card at 1280×720 | Sticky footer plus automatic compact density (viewport height ≤ 800 px): ring, gross strip, all 8 rows, «Почему снижена оценка» and both buttons without page scroll. |
| D19 | Ownership | Routes, shell slots, i18n namespaces and (P1) mock handlers are discovered with `import.meta.glob`; each package owns its `api/<domain>.ts`; the card is split into two packages that meet at frozen props. Nobody edits a shared registry after the WP-0 contract commit. |
| D20 | Landing | «Сценарии для жюри» is the second section (cards shared with the in-app drawer). Proof sections live in a separate package and are the first thing to sacrifice. |
| D21 | Fonts and dependencies | System stack (SF Pro on Mac, Segoe UI on Windows/D365); no new npm dependencies (no Inter, Playwright, axe). |
| D22 | Staffing | Four lanes of agents (§2.2); 12 packages, each under 2 hours. |

## 2. Plan

### 2.1 Packages

| WP | Title | Order | Depends on | Lane | Estimate | Merge gate |
|---|---|---|---|---|---|---|
| WP-0 | Foundation: tokens, shell, roles, i18n, API client, DS primitives | 0 | — | A | 2 h (contract commit at 45 min) | all existing routes work |
| WP-1 | Card: «Оценка качества» panel and appeal dialog | 1 | WP-0 | B | 1 h 50 | p1-ru-029 at 1280×720 |
| WP-3 | Gross violations register and operator portal | 1 | WP-0 | C | 1 h 55 | live asks 1 and 2 (operator half) |
| WP-5 | Checklist Studio and scoped apply | 1 | WP-0 | D | 1 h 55 | live ask 3 |
| WP-8 | «Сценарии для жюри» | 1 | WP-0 | A | 1 h | 7 scenarios open their targets |
| WP-2 | Card: page layout, transcript, calls and embed | 2 | WP-0 | B | 1 h 55 | p4-ru-002 jump, embed |
| WP-4 | Appeals review queue and decisions | 2 | WP-0 | C | 1 h 30 | live ask 2 (reviewer half) |
| WP-6 | Conversations, intake, runs and results.json export | 2 | WP-0 | D | 1 h 55 | list, paste, export dialog |
| WP-7 | Overview, analytics and settings | 2 | WP-0 | A | 1 h 50 | director home, mascot toggle |
| WP-9 | Landing: core sections | 3 | WP-0, WP-1, WP-8 | A | 1 h 55 | landing first screen |
| WP-10 | Landing: proof sections | 4 | WP-9 | C | 1 h 30 | — (sacrificable) |
| WP-11 | Demo readiness, docs and repository hygiene | 5 | WP-1 … WP-9 | team lead + free lane | 1 h 30 + rehearsals | go / no-go twice |

### 2.2 Lanes and clock

| Window (H) | Lane A | Lane B | Lane C | Lane D | Team lead |
|---|---|---|---|---|---|
| 7,5 → 8,25 | WP-0 contract commit (tag `wp0-contract`) | — | — | — | OP-1 deploy B-0, OP-2, OP-3 |
| 8,25 → 9,5 | WP-0 look (tokens, shell, primitives) | may branch from `wp0-contract` | same | same | smoke |
| 9,5 → 11,5 | WP-8 (≈ 9,5 → 10,5), then WP-7 | WP-1 | WP-3 | WP-5 | checkpoint C1 after WP-0 |
| 11,5 → 13,5 | WP-7 (until ≈ 12,5), then WP-9 | WP-2 | WP-4 (until ≈ 13) | WP-6 | checkpoint C2 |
| 13,5 → 15,5 | WP-9 (until ≈ 14,5), then integration fixes | integration fixes | WP-10 (starts when WP-9 is merged) | WP-11 helper | checkpoint C3, OP-4 seed |
| 15,5 → 18 | integration fixes | integration fixes | WP-10 (cut at H+17) | WP-11 helper | checkpoint C4, rehearsals ×2, OP-5 |
| 18 | **feature freeze**: nothing new starts | | | | |
| 18 → 21 | fixes from rehearsals only | | | | |
| 21 | **UI freeze**: no web or API deploys after this | | | | |
| 22 → 24 | — | | | | config freeze, control run, push, tag (OP-8) |

With more than four agents, every order-1 package and every order-2 package can run in parallel; WP-1 and WP-2 are parallel-safe (they meet at frozen props, §3.12).

### 2.3 Rules for every agent

1. **Ownership.** Edit only the files your package owns (§4). Import anything else read-only. Need a change in a file you do not own? Ask its owner; never patch it.
2. **Contracts are frozen** after the WP-0 contract commit (§3). Additive changes to a contract need WP-0's owner (or the team lead).
3. **Registration without registries.** New routes go into `web/src/features/<feature>/route.tsx`, shell contributions into `features/<feature>/slot.tsx`, strings into `web/src/i18n/<namespace>/{ru,en,kk}.ts`, API calls into `web/src/api/<domain>.ts`. Never edit `App.tsx`, `i18n/index.ts` or another package's module.
4. **No new npm dependencies.** Stack unchanged: Vite, React 18, TypeScript, Tailwind 3, Radix, TanStack Query, HashRouter, i18next, recharts, sonner, lucide.
5. **Never touch** `api/**`, `results/**`, `scripts/control_run.*`, `docs/API.md`, the five final specs (`ROLES.md`, `IA.md`, `DESIGN_SYSTEM.md`, `LANDING.md`, this file).
6. **Look rules** (`docs/DESIGN_SYSTEM.md` §11): tokens only (no hex in components), no mascot imports, icon + word for every status, numbers through `lib/format.ts`, turn numbers through `fmtTurn`, explanations rendered as the API returns them, honest claims.
7. **Never break the live-demo path.** Existing page files keep their names and default exports; shared component props change only additively; every state has a next step; nothing shows 0 for missing data.
8. **Gate before merge:** rebase on the integration branch, `pnpm typecheck && pnpm build` green (`pnpm build:single` for WP-0 and WP-2), your acceptance list checked on the local or preview build against the hosted API (`?api=https://api-production-58ab4.up.railway.app`), no console errors on your routes.
9. **Branching and deploys.** One branch (worktree) per package from `wp0-contract` or later; merges go to the integration branch `redesign`; the team lead promotes `redesign` to production at checkpoints C1–C4 only after the smoke check (OP-5). Nothing is deployed after H+21.
10. **Writes to the hosted instance** while testing: only on dialogs outside the demo set (never p1-ru-005 C6, p1-ru-029); the team lead restores `baseline` after test rounds.

## 3. Shared contracts (WP-0 creates them first)

Everything in this section is created in the WP-0 contract commit (≈ 45 minutes), then frozen.

### 3.1 File layout and ownership map

```
web/
  index.html, public/favicon.svg, vite.config.ts, tailwind.config.ts      WP-0
  src/
    main.tsx, App.tsx, index.css, vite-env.d.ts                           WP-0
    app/routes.ts, app/slots.ts                                           WP-0 (discovery)
    store/ui.tsx, store/role.tsx                                          WP-0
    lib/*  (config, roles, format, bands, url, capabilities, toast, utils) WP-0
    api/client.ts, api/types.ts, api/endpoints.ts, api/queries.ts, api/shell.ts   WP-0 (legacy modules frozen)
    api/quality.ts WP-1 · api/media.ts WP-2 · api/violations.ts, api/operator.ts WP-3 · api/appeals.ts WP-4
    api/studio.ts WP-5 · api/dialogs.ts WP-6 · api/insights.ts WP-7
    components/ui/**, components/layout/**, components/ds/*.{ts,tsx},
      components/common.tsx, components/Mascot.tsx, components/ErrorBoundary.tsx   WP-0
    components/ds/quality/**  WP-1 · components/ds/media/**  WP-2
    features/system/**  WP-0 (NotFoundPage, ReadOnlyNotice helpers)
    features/card/{EvaluationPanel,CriterionRow,AppealDialog,HowTotalDialog}.tsx, quality.ts   WP-1
    features/card/{ConversationCard,ConversationCardPage,Transcript,AudioPlayer,AudioInsightsTab,
                   HistoryTab,TraceTab,EmbedPage,CallBanner}.tsx, useCardUrlState.ts               WP-2
    features/violations/**, features/operator/**  WP-3 · features/review/**  WP-4
    features/checklists/**  WP-5 · features/conversations/**, features/jobs/**, features/export/**  WP-6
    features/overview/**, features/analytics/**, features/settings/**  WP-7 · features/jury/**  WP-8
    features/landing/*.{ts,tsx,json}, features/landing/sections/**  WP-9 · features/landing/proof/**  WP-10
    i18n/index.ts, i18n/types.ts, i18n/namespaces.ts, i18n/ru.ts, i18n/en.ts, i18n/kk.ts, i18n/shell/**  WP-0
    i18n/<namespace>/**  owner per §3.7
    mock/**, assets/**  WP-0
docs/DEMO.md, docs/WEB.md, README.md (live demo, model and cost), scripts/seed_demo.py, scripts/smoke_asks.py   WP-11
```

### 3.2 Routes (`web/src/app/routes.ts`, `web/src/App.tsx`)

```ts
// web/src/app/routes.ts
import type { ComponentType, ReactNode } from "react";
export type Layout = "app" | "bare";
export interface RouteDef {
  path: string;          // react-router path inside the hash: "/violations", "/my/appeals", "/operators/:name", "/"
  element: ReactNode;    // build with lazyPage()
  layout?: Layout;       // default "app" (inside AppShell); "bare" = no shell (landing)
}
export interface RouteModule { routes: RouteDef[] }
export function lazyPage(load: () => Promise<{ default: ComponentType }>): ReactNode; // React.lazy + Suspense skeleton
export function routeTable(): RouteDef[];   // built-ins first, then every features/*/route.tsx (eager glob); same path → discovered wins
export function hasRoute(path: string): boolean; // true when a route with this exact path pattern is registered
```

- Discovery: `import.meta.glob<RouteModule>("../features/*/route.tsx", { eager: true })`; page components stay lazy inside the route files.
- Built-in table in `App.tsx` (existing screens, unchanged imports): `/conversations` → `features/conversations/ConversationsPage`, `/conversations/:id` → `features/card/ConversationCardPage`, `/checklists` → `features/checklists/ChecklistsPage`, `/checklists/:id/versions/:vid` → `features/checklists/ChecklistEditorPage`, `/review` → `features/review/ReviewPage`, `/analytics` → `features/analytics/AnalyticsPage`, `/settings` → `features/settings/SettingsPage`, `/` → `HomeRedirect` (role home), `*` → `features/system/NotFoundPage`. **Owners keep these page files and their default exports.**
- Embed branch: `App.tsx` checks `embedConfig().embed` first and renders `features/card/EmbedPage` (WP-2's file, default export, prop `conversationId`).
- Example (WP-3):

```tsx
// web/src/features/violations/route.tsx
import { lazyPage, type RouteDef } from "@/app/routes";
export const routes: RouteDef[] = [{ path: "/violations", element: lazyPage(() => import("./ViolationsPage")) }];
```

Route files by package: WP-3 `/violations`, `/my`, `/my/appeals`, `/operators/:name` (P1); WP-4 `/review/:appealId` (P1); WP-7 `/overview`; WP-9 `/` (layout `bare`).

### 3.3 Shell slots (`web/src/app/slots.ts`)

```ts
export interface ShellSlot {
  id: string;                                   // "jury"
  placement: "sidebar-bottom" | "overlay";      // sidebar-bottom renders above «Настройки»; overlay renders at the shell root
  order?: number;
  Component: ComponentType<{ collapsed?: boolean }>;
}
export interface SlotModule { slots: ShellSlot[] }   // discovered from features/*/slot.tsx (eager glob)
export function useShellPanel(): { active: string | null; open(id: string): void; close(): void }; // one overlay panel at a time
```

The shell opens the panel `jury` when the URL carries `?tour=1` and a slot with that id exists.

### 3.4 Roles (`web/src/lib/roles.ts`, `web/src/store/role.tsx`)

```ts
// lib/roles.ts — mirrors api/cqa/security/roles.py
export type RoleCode = "operator" | "analyst" | "reviewer" | "methodologist" | "lead" | "director" | "visitor" | "admin";
export type Permission = "read" | "conversations.import" | "evaluations.run" | "appeals.create" | "appeals.decide"
  | "checklists.write" | "settings.write" | "calibration.write" | "integrations.ingest" | "admin";
export type Capability = Permission | "results.export" | "readiness.view";   // UI-only extras: analyst/lead/admin · lead/admin
export type NavKey = "my" | "myAppeals" | "overview" | "conversations" | "violations" | "review" | "checklists" | "analytics";
export interface RoleDef { code: RoleCode; home: string; nav: NavKey[]; permissions: Permission[];
  signature: string | null;            // default staff signature: Жанар Б., Айгерим С., Бекзат Н., Ерлан К., Гульнара А., «Администратор стенда»
  menu: "main" | "more" | "unlock"; }  // visitor: more; admin: unlock
export const ROLES: Record<RoleCode, RoleDef>;
export const NAV: Record<NavKey, { path: string; icon: LucideIcon; labelKey: string; badge?: "violations" | "openAppeals" | "unreadDecisions" }>;
export function roleCan(role: RoleCode, cap: Capability): boolean;
export function sameName(a?: string | null, b?: string | null): boolean;   // trim, case-insensitive, ё = е

// store/role.tsx
export interface RoleState {
  role: RoleCode; def: RoleDef;
  operatorName: string | null;     // chosen operator (role operator)
  signature: string | null;        // sent as X-CQA-User: operatorName for operators, the staff display name otherwise
  home: string;                    // def.home when registered, else "/conversations"
  adminUnlocked: boolean;
  can(cap: Capability): boolean;
  isOwnDialog(d: { operator_name?: string | null; agent_name?: string | null }): boolean; // operator: sameName on either field; other roles: false
  setRole(code: RoleCode, opts?: { operator?: string; navigate?: "home" | "stay-if-allowed" | "none" }): void; // default "stay-if-allowed"
  setSignature(name: string): void;
  openIdentityPicker(): void;      // «Кто вы?»
}
export function RoleProvider(props: { children: ReactNode }): JSX.Element;
export function useRole(): RoleState;
export function getActor(): { role: RoleCode; name: string | null };   // non-React snapshot for api/client.ts
```

- `?role=`, `?as=` (implies operator), unknown values, admin unlock and persistence keys: `docs/ROLES.md` §3.5–§3.6.
- `useUi()` keeps `theme`, `setTheme`, `navCollapsed`, `setNavCollapsed`; its `role` field becomes a read-only alias of `useRole().role` so old code compiles.
- `features/system` exports `ReadOnlyNotice({ cap, suggest }: { cap: Capability; suggest: RoleCode })` («Раздел доступен роли «X». Вы работаете как «Y» — изменения недоступны.» + switch button).
- Route policy (operator redirects, `#/my` for staff): implemented once in the shell (`docs/ROLES.md` §4).

### 3.5 API client and domain modules

```ts
// web/src/api/client.ts
export class ApiError extends Error {
  status: number; code: string;
  requestId: string | null;                         // from the X-Request-ID response header or error.request_id
  errors: { field: string; message: string }[];     // 422 field errors (paths like "criteria[2].weight")
  details: Record<string, unknown>;                 // rest of the error envelope (role, permission, suggested_id, …)
}
export function get<T>(path: string, query?: Query, signal?: AbortSignal): Promise<T>;  // no custom headers (no preflight)
export function post<T>(path: string, body?: unknown): Promise<T>;   // + X-CQA-Role, X-CQA-User (encodeURIComponent), X-Request-ID
export function put<T>(path: string, body?: unknown): Promise<T>;    // same headers
export function patch<T>(path: string, body?: unknown): Promise<T>;
export function del<T>(path: string): Promise<T>;
export function postForm<T>(path: string, form: FormData): Promise<T>;
export function fetchBlob(path: string, query?: Query): Promise<{ blob: Blob; headers: Headers }>; // downloads
export function actorFields(kind: "appeal" | "decision"): Record<string, string>;
// appeal → {author_role, author_name}; decision → {reviewer_role}; on a legacy API (no /api/version) also author_label / reviewer_label = «Имя (Роль)»
```

- **Domain modules**: each package creates `web/src/api/<domain>.ts` with its functions, TanStack hooks and response types, using only `client.ts`. Owners: `shell.ts` WP-0 (version, operators list, nav badges, health dot), `quality.ts` WP-1, `media.ts` WP-2, `violations.ts` + `operator.ts` WP-3, `appeals.ts` WP-4, `studio.ts` WP-5, `dialogs.ts` WP-6, `insights.ts` WP-7. WP-8, WP-9 and WP-10 call `get()` from inside their feature folder.
- **Legacy modules** `endpoints.ts`, `queries.ts`, `types.ts` stay (owned by WP-0, read-only for others). In the contract commit WP-0 adds optional fields to `types.ts` that several packages read: conversation `meta.not_in_recording?: number[]` and `meta.recording_start_idx?: number | null`; conversation summary `match?: {field, turn, turn_idx, speaker, snippet}`, `last_attempt_failed?: boolean`; appeal `author_role?`, `author_name?`, `operator_name?`, `status` incl. `"withdrawn"`, `na_allowed_for_channel?`; settings `mascot_enabled?`, `default_theme?`, `fewshot_from_reviews?`; run body `all?`, `dry_run?`, `only_codes?`, `mode?`, `base_version_id?`; run result `budget?`, `guard?`, `rejudge_codes?`; decision body `new_answer: string | number | null` and `add_to_fewshot: boolean` (required).
- Errors: `lib/toast.ts` `notifyError(err)` shows «{message} · {code} · запрос {requestId}» with «Копировать»; errors stay until dismissed.

### 3.6 Query keys and invalidation

| Key prefix | Data | Owner |
|---|---|---|
| `["conversations", filters]`, `["conversation", id]`, `["evaluation", id]`, `["evaluations", id]`, `["trace", evaluationId]` | legacy hooks | WP-0 |
| `["appeals", …]` | review queue and legacy list | WP-4 / WP-0 |
| `["violations", params]` | register, badges, joins | WP-3 (badge via WP-0) |
| `["operator", name]`, `["my-appeals", name]` | scorecard, operator appeals | WP-3 |
| `["overview", params]`, `["readiness"]` | overview, readiness | WP-7 |
| `["checklists"]`, `["version", c, v]`, `["impact", c, v, hash]` | studio | WP-0 / WP-5 |
| `["datasets"]` | export and apply scopes | WP-6 |
| `["settings"]`, `["health"]`, `["version-info"]`, `["operators-list"]`, `["nav-badges"]` | shell | WP-0 |

`useInvalidateConversation()(id)` (WP-0, `queries.ts`) invalidates `conversation`, `evaluation`, `evaluations`, `conversations`, `appeals`, `analytics`, `violations`, `operator`, `my-appeals`, `overview` and `nav-badges`. Every package calls it after a write that can change a result (appeal, decision, run end).

### 3.7 i18n namespaces

- Loader (`web/src/i18n/index.ts`): the legacy dictionaries (`ru.ts`, `en.ts`, `kk.ts`) stay the default namespace `translation`; every `web/src/i18n/<ns>/{ru,en,kk}.ts` with a default-exported object is discovered with `import.meta.glob("./*/*.ts", { eager: true })` and registered as namespace `<ns>`; fallback language `ru`; `document.documentElement.lang` follows the language.
- Types (`web/src/i18n/types.ts`): `NsShape<T>` (same keys, string leaves) for `en.ts`, `NsPartial<T>` for `kk.ts`. Pattern: `ru.ts` → `const ru = {…}; export default ru;` · `en.ts` → `const en: NsShape<typeof ru> = {…}` · `kk.ts` → `const kk: NsPartial<typeof ru> = {…}` (missing KK keys fall back to RU).
- Usage: `const { t } = useTranslation("violations"); t("title")`.
- Skeleton: WP-0 creates the loader, `i18n/types.ts`, the `shell` dictionary and `i18n/namespaces.ts`, the registry of every reserved namespace with its owner (the loader warns in dev about an unregistered folder). WP-0 does not create files inside other packages' namespace folders; each package creates `i18n/<ns>/{ru,en,kk}.ts` on its first commit and from then on only adds keys there.
- Reserved namespaces (owner): `shell` WP-0 · `quality` WP-1 · `transcript` WP-2 · `violations`, `operator` WP-3 · `appeals` WP-4 · `studio` WP-5 · `dialogs` WP-6 · `overview`, `prefs` WP-7 · `jury` WP-8 · `landing` WP-9 · `landingProof` WP-10. A package adds keys only in its own namespace. The legacy dictionaries change only in WP-0 and only additively (seven role labels, nav «Апелляции»); nobody deletes keys tonight. Namespace names never equal legacy top-level keys (`card`, `review`, `checklists`, `analytics`, `settings`, `conversations`, `embed`).
- Copy for the four asks, roles and landing is final in `docs/ROLES.md`, `docs/IA.md`, `docs/LANDING.md`; EN complete; KK where available, checked by a Kazakh speaker on the hero, navigation, roles and the card.

### 3.8 Formatting and bands (`web/src/lib/format.ts`, `web/src/lib/bands.ts`)

```ts
fmtScore(v: number | null | undefined, digits = 2): string     // "51,25" (ru, kk) · "51.25" (en) · "—" for null
fmtPoints(v: number | null | undefined): string                // trimmed: "7,5", "18,75", "0"
fmtPointsPair(earned, max): string                             // "7,5 / 18,75"
turnNo(idx: number): number                                    // idx + 1 (the only place 1-based numbering lives)
fmtTurn(idx: number): string                                   // "№10"
fmtMoney(v, digits = 2): string                                // "$0,22" · "$0.22"
fmtDuration(seconds): string                                   // "10:20", "1:02:03"
fmtDateTime(iso): string                                       // "07.11, 21:44" in local time
fmtPct(v, digits = 0): string; plural(n, forms): string        // RU/KK plural forms
bandOf(total: number | null): "norm" | "attention" | "critical" | null   // ≥ 85 · 60–85 · < 60
bandTone(band): "success" | "warning" | "critical" | "neutral"
```

Existing helpers (`fmtSeconds`, `fmtTurnTime`, `scoreTone`, `answerText`, `truncate`) keep their names; `scoreTone` delegates to `bandOf`.

### 3.9 URL helpers (`web/src/lib/url.ts`)

```ts
useUrlParam(key: string): [string | null, (value: string | null, opts?: { replace?: boolean }) => void]  // merge-safe, default replace
linkState(path: string, labelKey: string): { from: { path: string; labelKey: string } }               // pass as <Link state>
useBackLink(defaultPath: string, defaultLabelKey: string): { to: string; label: string }
usePageTitle(parts: (string | null | undefined)[]): void   // "p1-ru-029 · Анализ разговора · CQA" (P1 everywhere)
```

### 3.10 Runtime config and embed (`web/src/lib/config.ts`)

```ts
export interface EmbedConfig { embed: boolean; conversation: string | null; lang: "ru" | "en" | "kk" | null;
  role: RoleCode | null; user: string | null; host: "d365" | null; }
export function embedConfig(): EmbedConfig;
// sources: ?embed=1, ?conversation=, ?data=<URL-encoded "conversation=…&api=…&lang=…">, ?id=<GUID> (braces removed, lower case;
// used when conversation is absent), ?userlcid= (1049 ru · 1033 en · 1087 kk), ?host=d365, and window.CQA_CONFIG
// {apiUrl, embed, conversation, lang, role, user, host}. role and user come only from CQA_CONFIG.
export const BUILD: { commit: string; builtAt: string };  // from vite `define` (__APP_COMMIT__ from VERCEL_GIT_COMMIT_SHA or git, __APP_BUILT_AT__)
export function apiBase(): string;   // ?api= → CQA_CONFIG.apiUrl → data=api → VITE_API_URL (existing order plus data=)
```

`main.tsx` sets `<html data-host="d365">` when `host` is `d365` and forces the light theme in embed mode.

### 3.11 Tokens and density variables

Names and values: `docs/DESIGN_SYSTEM.md` §3 and §8. Contract for components: Tailwind colours `bg surface surface-2 line line-strong text text-2 text-3 text-disabled accent (= --accent-strong) accent-fill (= --accent) accent-hover accent-subtle tint selection accent-fg deep focus nav-bg nav-active success success-mark success-bg success-line warning … critical … info … neutral … evidence-bg evidence-line evidence-active track-client track-operator track-other ring-track`; CSS variables `--row-h --crit-row-h --ring-lg --card-pad --page-pad --h1-size` (compact under `max-height: 800px`); utilities `.evidence` (peach turn), `.evidence-span` (3 px amber left rule), `.num` (tabular). Removed: `.glow`, `shadow-glow`, `shadow-float`, `.turn-highlight`, uppercase `.label`.

### 3.12 Cross-package component contracts (frozen props)

```ts
// features/card/EvaluationPanel.tsx — owner WP-1, rendered by WP-2's ConversationCard (props exactly as today)
export function EvaluationPanel(p: { evaluation: Evaluation; onEvidence: (e: Evidence) => void;
  onSpan: (span: { from: number; to: number } | null) => void; canAppeal: boolean;
  onAppeal: (c: EvaluationCriterion) => void; hasAudio?: boolean }): JSX.Element;
// The panel selects the current criterion itself and mirrors it to ?criterion= via useUrlParam (replace).
// It is `flex h-full min-h-0 flex-col`; its footer is `position: sticky; bottom: 0`, so it works in a fixed-height card and in a scrolling page.

// features/card/AppealDialog.tsx — owner WP-1, opened by WP-2's ConversationCard (props exactly as today)
export function AppealDialog(p: { criterion: EvaluationCriterion | null; open: boolean;
  onOpenChange: (o: boolean) => void; onCreated: () => void }): JSX.Element;

// features/conversations/EvaluateDialog.tsx — owner WP-6, used by WP-2 («Переоценить»): EvaluateDialogProps as today; additive changes only
// features/jobs/JobProgress.tsx — owner WP-6, used by WP-2 and WP-5: props as today; additive changes only

// components/ds/quality/QualityScoreCard.tsx — owner WP-1, used by WP-9 (hero) and optionally WP-10
export function QualityScoreCard(p: { evaluation: Evaluation; variant: "full" | "compact"; maxRows?: number;
  onOpen?: () => void }): JSX.Element;

// features/jury/scenarios.ts and JuryScenarioCards.tsx — owner WP-8, used by WP-9
export interface JuryScenario { id: string; titleKey: string; sentenceKey: string; role: RoleCode; operator?: string;
  target: string | (() => Promise<string>); }
export const SCENARIOS: JuryScenario[];
export function useRunScenario(): (s: JuryScenario) => Promise<void>;   // setRole(navigate "none") + navigate(target)
export function JuryScenarioCards(p: { variant: "landing" | "panel" }): JSX.Element;
```

### 3.13 Design-system primitives

WP-0 ships the components of `docs/DESIGN_SYSTEM.md` §9 owned by WP-0, with the props listed there (`Button`, `Card`, `PageHeader`, `Chip` and presets, `IconTile`, `ScoreRing`, `ScorePill`, `KpiTile`, `EmptyState`, `InlineAlert`, `ErrorState`, `Field`, `SegmentedControl`, `NumberStepper`, `Drawer`, `Dialog`, `Tabs`, table styles, inputs, toaster, `BrandMascot`, shell). Import path `@/components/ds/<Name>` (barrel `@/components/ds`). `components/common.tsx` keeps its exports as wrappers so untouched screens compile.

### 3.14 Capability probe (`web/src/lib/capabilities.ts`)

```ts
export function useCapabilities(): { ready: boolean; apiVersion: string | null; legacy: boolean };  // legacy = GET /api/version → 404
```

On `legacy` (an API without the B-0 deploy): hide «Сохранить как черновик» (the old API activates every version), hide the what-if line, the readiness panel and scorecards; fall back from `/api/overview` to `/api/analytics/summary` + `/api/violations`; send labels via `actorFields`.

### 3.15 Mock mode (P1)

`?mock=1` keeps working for the existing screens and shows a permanent «Офлайн-демо» badge. A package may add `features/<feature>/mock.ts` exporting `handlers: { method: string; path: RegExp; respond(match: RegExpMatchArray, body: unknown): unknown }[]`; `mock/server.ts` tries them first. Screens whose endpoints have no mock show their offline empty state. Mock mode is not the jury fallback (local Docker with a snapshot is).

## 4. Work packages

Each package: owner files, stories, a self-contained brief, P0 / P1 scope and acceptance checks. P1 is built only when P0 is merged and green; at H+16 every package drops its remaining P1.

### WP-0 · Foundation: tokens, shell, roles, i18n, API client, DS primitives

- **Order** 0 · **depends on** — · **lane** A · **estimate** 2 h (contract commit, tag `wp0-contract`, within 45 min)
- **Owns:** `web/index.html`, `web/public/favicon.svg`, `web/vite.config.ts`, `web/tailwind.config.ts`, `web/src/index.css`, `web/src/main.tsx`, `web/src/App.tsx`, `web/src/vite-env.d.ts`, `web/src/app/**`, `web/src/store/**`, `web/src/lib/**`, `web/src/api/client.ts`, `web/src/api/types.ts`, `web/src/api/endpoints.ts`, `web/src/api/queries.ts`, `web/src/api/shell.ts`, `web/src/components/ui/**`, `web/src/components/layout/**`, `web/src/components/ds/*.ts`, `web/src/components/ds/*.tsx`, `web/src/components/common.tsx`, `web/src/components/Mascot.tsx`, `web/src/components/ErrorBoundary.tsx`, `web/src/features/system/**`, `web/src/i18n/index.ts`, `web/src/i18n/types.ts`, `web/src/i18n/namespaces.ts`, `web/src/i18n/ru.ts`, `web/src/i18n/en.ts`, `web/src/i18n/kk.ts`, `web/src/i18n/shell/**`, `web/src/mock/**`, `web/src/assets/**`
- **Stories:** SHELL-01, SHELL-02, SHELL-03, SHELL-04, SHELL-05, SHELL-06, SHELL-07, SHELL-08, SHELL-10, SHELL-15, OPR-01, OPR-02, CARD-07, CHK-28, D365-01, SYS-08, EXP-08, DEMO-04

**Brief.** Foundation for the corporate redesign of `web/` (Vite, React 18, TypeScript, Tailwind 3, TanStack Query, HashRouter, i18next). Read `docs/DESIGN_SYSTEM.md`, `docs/ROLES.md`, `docs/IA.md` and §3 of this file first: §3 is the contract you implement and then freeze. Work in two commits. **Commit 1, the contract (≤ 45 min, tag `wp0-contract`):** `app/routes.ts` and `app/slots.ts` with `import.meta.glob` discovery; `App.tsx` with the built-in table of the existing pages (unchanged imports), `HomeRedirect`, `features/system/NotFoundPage`, the embed branch; `lib/roles.ts` mirroring `api/cqa/security/roles.py` (8 roles, homes, permissions, nav, default signatures Жанар Б. / Айгерим С. / Бекзат Н. / Ерлан К. / Гульнара А.); `store/role.tsx` (`useRole`, `getActor`, `?role=` / `?as=` handling, persistence, admin unlock); `api/client.ts` (role headers and `X-Request-ID` on writes only, `ApiError.requestId/errors/details`, `actorFields`, `fetchBlob`, `patch`, `del`); `api/shell.ts`; `lib/url.ts`, `lib/format.ts` (Intl, locale comma, `fmtTurn` = «№» + idx + 1, `fmtPoints`), `lib/bands.ts`, `lib/capabilities.ts`, `lib/config.ts` (D365 `data=` / `id=` / `userlcid` / `host` and `CQA_CONFIG` role/user, `BUILD`); the i18n loader for `i18n/<ns>/{ru,en,kk}.ts`, `i18n/types.ts`, the namespace registry `i18n/namespaces.ts` and the `shell` dictionary (all shell, role, identity, notice and error strings); additive fields in `api/types.ts`; `useInvalidateConversation` extended. Every existing screen must keep working after commit 1. **Commit 2, the look (≤ 75 min):** retune the tokens in `index.css` (keep the variable names, add the new ones, light default, dark under `.dark`, density variables, `[data-host="d365"]`, `.evidence`, remove glow, neon and uppercase labels), Tailwind mapping (`accent` → `--accent-strong`, `accent-fill` → `--accent`), `index.html` (no `dark` class, pre-paint script, theme-color `#0F5431`, monogram favicon); `AppShell` without a top bar: deep-green `SideNav` 240/72 with the role pill at the top, the role menu with «Ещё роли», «Кто вы?» on `GET /api/operators`, nav per role hiding unregistered routes, badges, the lower slot group, «Настройки», RU · KK · EN switch, API dot, auto-collapse below 1280 px; the role route policy; the `BrandMascot` gate (null unless `settings.mascot_enabled`, lazy poses); DS primitives in `components/ds/*.tsx` and restyled `ui/*`; `common.tsx` re-implemented on them with the same exports and no mascot; sonner theme and `notifyError` with code and request id. Remove the real call transcript p4-ru-001 from `mock/fixtures/conversation_details.json`. No new npm dependencies; do not edit `api/**`, `docs/**` or other packages' files; run `pnpm typecheck`, `pnpm build`, `pnpm build:single`.

**P0:** everything in the brief. **P1:** offline banner after two failed health checks and the cold-start notice («Сервер просыпается, это может занять до 30 с»); `usePageTitle` adoption in the shell; mock handler discovery (§3.15); unsaved-changes guard on role switch; «Скопировать ссылку для этой роли».

**Acceptance:**
1. `pnpm typecheck`, `pnpm build` and `pnpm build:single` green; `dist-single/index.html` below 4,5 MB.
2. A fresh browser shows no `dark` class on `<html>` and no `img[src*="mascot"]` on any route; the favicon is the CQA monogram.
3. All seven existing routes render as before (now light); an unknown route shows «Страница не найдена» with «К моей главной» and «На лендинг».
4. Sidebar `#0F5431`, no top bar; the role pill at the top reads «Роль / QA-аналитик · Жанар Б.»; analyst nav shows only registered routes; below 1280 px the sidebar collapses to 72 px.
5. `#/conversations?role=operator&as=Нина` → pill «Оператор · Нина», address bar loses `role` and `as`, the app redirects to `#/my` (or to the not-found/notice page until WP-3 lands); `?role=xyz` → toast «Роль «xyz» не найдена», role unchanged; after `?role=admin`, «Ещё роли» lists Администратор.
6. DevTools: a POST carries `X-CQA-Role`, URL-encoded `X-CQA-User` and `X-Request-ID`; GETs carry no custom headers.
7. A forced API error toast shows message, code and request id with «Копировать».
8. A throw-away `features/demo/route.tsx` and `i18n/demo/ru.ts` (not committed) appear without editing `App.tsx` or `i18n/index.ts`.
9. `embedConfig()` returns `conversation = p1-ru-029` and `lang = en` for `index.html?embed=1&data=conversation%3Dp1-ru-029&userlcid=1033`.
10. No horizontal scroll at 1164 px width; `?mock=1` still renders the list and p1-ru-029; no real-call transcript left in `web/src/mock`.

### WP-1 · Card: «Оценка качества» panel and appeal dialog

- **Order** 1 · **depends on** WP-0 · **lane** B · **estimate** 1 h 50
- **Owns:** `web/src/features/card/EvaluationPanel.tsx`, `web/src/features/card/CriterionRow.tsx`, `web/src/features/card/AppealDialog.tsx`, `web/src/features/card/HowTotalDialog.tsx`, `web/src/features/card/quality.ts`, `web/src/components/ds/quality/**`, `web/src/api/quality.ts`, `web/src/i18n/quality/**`
- **Stories:** CARD-01, CARD-02, CARD-03, CARD-04, CARD-05, CARD-06, CARD-07, CARD-09, CARD-12, GV-03, APL-01, APL-02, APL-09, CALL-09

**Brief.** Rebuild the right half of the conversation card into the organizer mockup's «Оценка качества» (`docs/design/organizer-mockup.png`; `docs/DESIGN_SYSTEM.md` §4, §9, §10). WP-2 owns `ConversationCard.tsx` and renders your `EvaluationPanel` and `AppealDialog`: keep their current props exactly (§3.12). The panel selects the current criterion itself (default: the largest loss) and mirrors it to `?criterion=` with `useUrlParam` (replace); a cold `?criterion=C8` selects C8. Build: (1) `quality.ts`, pure helpers on the evaluation payload: rows grouped by section with subtotals, lost points = `weight_applied − final_earned`, reasons sorted by loss with the first sentence of the explanation after the «Критерий — ответ.» prefix (≤ 120 characters), the N/A list, and the breakdown for «Как посчитан итог» (applied weight, factor, rounding note); (2) `components/ds/quality`: `CriterionRowCompact`, `SectionHeader`, `ReasonSummary`, `EvidenceChip` (1-based «№»), `GrossViolationBlock`, `DecisionNote`, `QualityScoreCard` (full and compact; WP-9 uses compact); (3) `EvaluationPanel`: gross strip when the critical criterion is Да (type from `GET /api/violations?…` item for this dialog, quote with №, «Итог не обнуляется: по чек-листу грубая ошибка отмечается отдельно»), `ScoreRing lg` with «Как посчитан итог →», rows with icon + word + «earned / applied», sticky footer with «Почему снижена оценка» and the two 48 px buttons «Перейти к фрагменту» (calls `onEvidence` with the first contradicting evidence of the selected criterion, else its first evidence) and «Оспорить оценку» (`onAppeal`; disabled with a reason when `canAppeal` is false or an appeal is open), compact density from the CSS variables, one grey provenance line «Оценено: Claude Opus 5.5 · чек-лист v1 · 07.11, 17:47 · $0,18 · 14 с», technical details folded; (4) `CriterionRow` becomes the criterion detail: answer chip «4 из 10», points and weight («7,5 / 18,75 · вес 15 → 18,75»), scale anchor of the answer and of the next step up (`options[].anchor`), the explanation exactly as returned (already 1-based; never renumber), evidence chips, checked span, N/A block «Причина N/A: … · вес перераспределён на применимые критерии», appeal and decision thread with `DecisionNote`; (5) `HowTotalDialog` with the footer «Применимый вес 80 из 100 → коэффициент 1,25 · сумма 51,25 · округление до 2 знаков, половина вверх» and «Исходно / После решения» columns after a decision; (6) `AppealDialog`: title «Апелляция по критерию C6 · Управление диалогом, скорость и грамотность», «Ответ ИИ: 4 из 10 · 9,23 / 23,08», optional proposed answer (`SegmentedControl` from options, current answer disabled, N/A only when the channel allows it) with the live preview from `POST /api/appeals/preview` («78,46 → 85,38 · до решения итог не меняется»), comment ≥ 10 characters, body `comment` = «Предлагаемый ответ: 7 из 10» line + text, plus `actorFields("appeal")`; toast «Апелляция отправлена ревьюеру»; Russian messages for 403 `not_your_dialog` and 409 `already_open`; `useInvalidateConversation`. No mascot; numbers via `lib/format`.

**P0:** items 1–6 above except «Пересчитать». **P1:** «Пересчитать» in `HowTotalDialog` (`POST /api/appeals/preview` per criterion with its current final answer → «Пересчитано движком: 51,25 — совпадает»); low-confidence badge below 0,70 (`attention.low_confidence`); «Копировать ссылку» and «Копировать обоснование» per criterion.

**Acceptance:**
1. p1-ru-029 in a 1280×720 full-screen viewport: gross strip «Грубое нарушение: оскорбление клиента · №10», ring «51,25 / 100», all 8 rows, «Почему снижена оценка» and both buttons visible without page scroll; at 1280×650 rows scroll inside the card and the footer stays visible.
2. Rows read «earned / applied» with a decimal comma; C3 «N/A · вес перераспределён»; C8 «Грубое нарушение»; every status has an icon and a word.
3. Reasons for p1-ru-029: Выявление потребности −18,75 · Эмпатия −18,75 · Управление диалогом −11,25, then the N/A line; a 100,00 dialog without a gross error says «Оценка не снижалась».
4. «Как посчитан итог» footer for p1-ru-029 matches the line above.
5. Selecting C5 sets `?criterion=C5` (replace); a cold load with `?criterion=C8` selects C8.
6. As Оператор · Нина on p1-ru-005 C6: proposing 7 previews «78,46 → 85,38»; a 9-character comment cannot be sent; the POST carries the role headers, `author_role=operator`, `author_name=Нина` and the first line «Предлагаемый ответ: 7 из 10»; the row turns «На рассмотрении» without a reload.
7. After a reviewer decision: the row shows «4 → 7 · 9,23 → 16,15 · Изменено ревьюером · Айгерим С. · дата», the ring shows 85,38 with 78,46 struck through and «после апелляции».
8. No 0-based turn number and no mascot anywhere in the panel.

### WP-2 · Card: page layout, transcript, calls and embed

- **Order** 2 · **depends on** WP-0 (parallel-safe with WP-1 through §3.12) · **lane** B · **estimate** 1 h 55
- **Owns:** `web/src/features/card/ConversationCard.tsx`, `web/src/features/card/ConversationCardPage.tsx`, `web/src/features/card/Transcript.tsx`, `web/src/features/card/AudioPlayer.tsx`, `web/src/features/card/AudioInsightsTab.tsx`, `web/src/features/card/HistoryTab.tsx`, `web/src/features/card/TraceTab.tsx`, `web/src/features/card/EmbedPage.tsx`, `web/src/features/card/CallBanner.tsx`, `web/src/features/card/useCardUrlState.ts`, `web/src/components/ds/media/**`, `web/src/api/media.ts`, `web/src/i18n/transcript/**`
- **Stories:** CARD-01, CARD-04, CARD-08, CALL-01, CALL-02, CALL-03, CALL-09, CALL-12, CONV-08, SHELL-10, D365-01

**Brief.** Rebuild the conversation card page around WP-1's panel, as in the organizer mockup. Render `EvaluationPanel` and `AppealDialog` with their current props (§3.12; do not edit them). **Header:** `PageHeader` with the back link from router state (`useBackLink`, default «← Диалоги»), title «Анализ разговора», meta «Чат · p1-ru-029 · Русский · оператор Алексей · 14 реплик» (calls «Звонок · p4-ru-002 · Русский · 10:20»), actions «Экспорт PDF», «Переоценить» (`can("evaluations.run")`, opens the existing `EvaluateDialog`), «⋯» (Копировать JSON, Копировать ссылку). **Layout:** for calls the waveform card across the full width; then «Расшифровка» (5/12) and the quality card (7/12, min 520 px, fixed height = viewport minus header, body scrolls inside; tabs «Оценка · История · Трассировка», plus «Звонок» for calls); below 1100 px the quality card comes first. Compute `canAppeal = can("appeals.create") && (role !== "operator" || isOwnDialog(conversation))`; for an operator on someone else's dialog show «Диалог другого оператора». **URL:** read `?turn=` (1-based) on load → scroll and peach highlight, `?t=` → seek, `?tab=`; write them with `useUrlParam` (replace); unknown values give a quiet «Фрагмент не найден». **Transcript:** the mockup's divided list built from `ds/media/TranscriptRow` (time `--text-3`, speaker label coloured, «№n» right, text below); the pre-operator segment folded into «До оператора: бот и системные события · N реплик» and unfolded automatically when an evidence points inside; system events as small grey lines; evidence turn peach (`.evidence`), checked span with an amber left rule, duplicates collapsed, search behind an icon; turns listed in `meta.not_in_recording` labelled «не в записи» and never seek. **Calls:** `WaveformBars` from `GET /api/audio/{id}/peaks?buckets=600` (lanes «Клиент» / «Оператор», thin third lane for other), the peach region from the selected evidence turn's offset to the next turn's, `PlayerBar` (40 px round play, «02:00 / 10:20», progress, speed 0,75–2×); click on a lane, a row or a chip seeks and plays with a 2 s pre-roll; fallback lanes from turn spans labelled «по расшифровке» when peaks fail; «Запись недоступна: источник не отвечает. Оценка и расшифровка доступны.» + «Повторить» when audio fails. `CallBanner` when operator turns precede the recording start: «Запись началась после начала разговора оператора. «Установление контакта» засчитано по правилу: снижать балл можно только по фрагменту, который можно прослушать. Звонок отмечен как пробел записи.» (updated 2026-11-08: C1 has no N/A, see FRONTEND_INTEGRATION.md «Calls») **Embed** (`EmbedPage`): light whatever is stored, no sidebar, read-only (`canAppeal` false, no evaluate buttons), «Открыть в CQA» (new tab; deep link plus `?role=&as=` from `embedConfig()`), states «Диалог не найден в CQA», «Оценки пока нет», «CQA недоступен · Повторить». History and Trace: restyle only; mark Trace «сырые данные модели». No mascot; never 0 for a missing total (running, failed and not-evaluated states with a next step).

**P0:** header, layout, `canAppeal`, URL state, transcript, waveform and player, call banner, recording fallback, embed. **P1:** follow mode with «К текущей реплике», «Следующий фрагмент» with a counter, Space to play or pause, History timeline polish (Оценка ИИ → Апелляция → Решение → Итог with names), `data-host="d365"` checks at 900 px iframe width, `usePageTitle`.

**Acceptance:**
1. Header and meta as above; opened from the register, the back link reads «← Грубые нарушения» and returns with the filters.
2. Cold `#/conversations/p1-ru-029?criterion=C8&turn=10`: turn №10 centred in peach and C8 selected; chip clicks update `turn` with replace.
3. Transcript numbers are 1-based and match results.json `evidence_turns`; the pre-operator segment is folded and opens when evidence points inside.
4. p4-ru-002: two lanes, 10:20 total; with C6 selected «Перейти к фрагменту» plays turn №15 at ≈ 02:00 (after OP-2); turns before the recording start show «не в записи»; the C1 banner is shown.
5. Chats show no waveform; a blocked audio URL shows the transcript-only notice and «Повторить».
6. At 1280 px the transcript and quality cards sit side by side (quality ≥ 520 px); below 1100 px the quality card is first; no horizontal scroll at 1164 px.
7. `index.html?embed=1&conversation=p1-ru-029` is light, read-only and offers «Открыть в CQA»; `?embed=1&data=conversation%3Dp1-ru-029&userlcid=1033` renders in English; an unknown id shows «Диалог не найден в CQA».
8. Оператор · Нина can appeal on p1-ru-005; on p1-ru-029 (Алексей) the card says «Диалог другого оператора» and «Оспорить оценку» is disabled with the reason.

### WP-3 · Gross violations register and operator portal

- **Order** 1 · **depends on** WP-0 · **lane** C · **estimate** 1 h 55
- **Owns:** `web/src/features/violations/**`, `web/src/features/operator/**`, `web/src/api/violations.ts`, `web/src/api/operator.ts`, `web/src/i18n/violations/**`, `web/src/i18n/operator/**`
- **Stories:** GV-01, GV-02, GV-03, GV-04, GV-08, OPR-01, OPR-02, OPR-03, OPR-04, OPR-05, APL-04, ANL-06, DEMO-02

**Brief.** Build the two new screens for live asks 1 and 2 on endpoints that already exist; register them in `features/violations/route.tsx` (`/violations`) and `features/operator/route.tsx` (`/my`, `/my/appeals`, P1 `/operators/:name`). **(A) `#/violations` «Грубые нарушения»** on `GET /api/violations?type=&language=&channel=` (`{total, counts, types, items}`; items carry `types`, `type_labels`, `explanation` already 1-based, `confidence`, `rules[]`, `classified_by`, `criterion_code`, `open_appeal_id`, `decision`, `related.C3`, `evidence[]` with `turn_idx`, `timestamp`, `quote`, `final_total_score`, `review_state`): header meta «8 диалогов · проверено 65 · чек-лист v1 · по данным на HH:MM»; type chips with counts «Все 8 · Оскорбление клиента 1 · Платная услуга в обход компании 4 · Раскрытие конфиденциальной информации 3 · Иное 0»; language and channel selects; all filters in the URL (`useUrlParam`); one card per dialog in API order (final total ascending): type chips, id, language and channel chips, operator, `ScorePill` + «итог не обнуляется», review-state chip («ИИ · не подтверждено», «Подтверждено ревьюером», «На апелляции»), two lines of the explanation, a quote block «№10 · 01:23 · Оператор: «…»» from `evidence[0]`, «Открыть в диалоге» → `#/conversations/<id>?criterion=<criterion_code>&turn=<turn_idx + 1>` with `linkState`; expander «Почему так классифицировано»: the rule text, the full explanation, confidence in words and number («высокая · 0,95»), `classified_by` in words, and for disclosure the related C3 answer with the team rule «C3 = Нет и C8 = Да при раскрытии данных до идентификации». Empty states per filter («В звонках грубых нарушений не найдено · проверено 5 звонков»). **(B) Operator portal**: `#/my` «Мои оценки» on `GET /api/operators/{encodeURIComponent(name)}` (`mean_total`, `team_mean_total`, `coaching_points`, `strengths`, `violations`, `appeals`, `conversations`), falling back to `GET /api/conversations?operator=<name>` on a legacy API: header «Оператор · Нина · RU · 1 диалог»; tiles «Средний итог 78,46», «Грубых нарушений 0», «Апелляций на рассмотрении 0», «Больше всего баллов теряю: Управление диалогом −13,85 · …», «Среднее по всем диалогам 82,04» (no colleague names), «Мало данных для выводов» below three evaluations; the own dialogs list (id, channel, language, total, gross chip, appeal state, version) → card with `linkState`. `#/my/appeals` «Мои апелляции» on `GET /api/appeals?operator=<name>`: tabs «На рассмотрении / Решённые / Все» (`?status=`), decided rows «Изменено: 4 → 7 · итог 78,46 → 85,38 · <причина>» or «Оценка подтверждена · <причина>», an unread dot until opened (`cqa.my.seen.<operator>`), refetch on window focus. Staff roles on `#/my` get WP-0's notice. The badge «Мои апелляции» comes from WP-0; keep its query key `["my-appeals", name]` warm.

**P0:** (A) without the P1 items; (B) `#/my` and `#/my/appeals`. **P1, in this order:** «Фокус коучинга» on `#/my` and the read-only scorecard `#/operators/:name` for staff roles (same summary + `coaching_points` with examples and «Перейти к фрагменту»); «Отозвать» on open own appeals (`POST /api/appeals/{id}/withdraw`); «Показать снятые ревьюером» (`include_reviewed_out=true`) and the CSV link `/api/export/violations.csv` with the same filters; a first-visit panel «Как оцениваются ваши диалоги»; the tab «Проверено ИИ: не нарушение» on `GET /api/violations/candidates`.

**Acceptance:**
1. 8 cards; p1-kz-015 (24,71) first, p1-ru-023 (100,00) last with «итог не обнуляется».
2. Chips «Раскрытие» + language KZ → only p1-kz-015 and the URL `#/violations?type=disclosure&language=KZ`; a cold load of that URL restores the filters.
3. «Открыть в диалоге» on p1-ru-029 opens the card with C8 selected and №10 in peach; Back returns to the filtered register.
4. «Почему так классифицировано» on a disclosure row shows the rule, the confidence and the related C3 answer.
5. As Нина, `#/my` lists p1-ru-005 at 78,46 with the summary tiles; after the reviewer's decision «Мои апелляции» shows «Изменено: 4 → 7 · итог 78,46 → 85,38» with the reason on window focus, with an unread dot until opened.
6. Staff roles on `#/my` see «Раздел оператора» with «Войти как оператор»; no screen shows a colleague's name in the operator portal.

### WP-4 · Appeals review queue and decisions

- **Order** 2 · **depends on** WP-0 · **lane** C · **estimate** 1 h 30
- **Owns:** `web/src/features/review/**`, `web/src/api/appeals.ts`, `web/src/i18n/appeals/**`
- **Stories:** APL-05, APL-06, APL-07, APL-08, APL-09, APL-12, APL-13, GV-05, CAL-12, SHELL-05, DEMO-02

**Brief.** Rebuild `#/review` «Апелляции» (`features/review/ReviewPage.tsx` keeps its name and default export) for the reviewer half of live ask 2. Data: `GET /api/appeals?status=open|decided` (items carry `author_role`, `author_name`, `operator_name`, code, title, answer, `options` with points and anchors, `na_allowed_for_channel`, evidence, explanation already 1-based, `total_score`, `final_total_score`, language, channel), `GET /api/conversations/{id}` for the fragment, `POST /api/appeals/preview`, `POST /api/appeals/{id}/decision`. Header «Апелляции» · «{N} открыто · {M} решено»; tabs «Открытые» / «Решённые» in `?status=`; order: critical-criterion appeals first, then newest first; the first open appeal expanded, the others one line each. **Appeal card:** «p1-ru-005 · RU · Чат · оператор Нина», «C6 · Управление диалогом, скорость и грамотность», «подана 5 мин назад»; «Ответ ИИ: 4 из 10 · 9,23 / 23,08 · уверенность 0,88»; the explanation; the author block «Оператор · Нина» with the comment and a chip «Просит: 7 из 10» parsed from its first line «Предлагаемый ответ: …» (read `requested_answer` first if the API returns it); «Фрагмент диалога»: evidence turns with one context turn on each side, evidence in peach, 1-based «№»; «Открыть в карточке» → `#/conversations/<id>?criterion=<code>&turn=<n>` with `linkState`. **Decision panel** (`can("appeals.decide")`): «Подтвердить» / «Изменить»; under «Изменить» only the checklist options with points and anchors (`SegmentedControl`), the AI's answer disabled with «Совпадает с ответом ИИ — выберите «Подтвердить»», N/A only when `na_allowed_for_channel` and sent as the string `"N/A"` (never `null`); reason ≥ 15 characters with a counter; a live preview «Итог: 78,46 → 85,38» or «Итог не изменился»; the switch «Использовать как пример для ИИ» off and disabled with «Отключено до фиксации набора примеров»; the body always has `add_to_fewshot: false` plus `actorFields("decision")`. **Four eyes:** when the reviewer's signature equals the appeal author (`sameName`), show «Вы оспариваете и решаете сами: передайте апелляцию коллеге» and ask for a second confirmation. «Сохранить решение» → toast «Решение сохранено. Итог: 85,38» and `useInvalidateConversation`. Roles without the permission see «Решение принимает ревьюер» with «Переключиться на роль Ревьюер» (`setRole("reviewer")`, stays on the page). Empty state «Открытых апелляций нет · оператор или QA-специалист подаёт апелляцию из карточки разговора» with «Открыть диалоги» and «Грубые нарушения (8)». Remove every mascot use.

**P0:** queue, card, fragment, decision panel, N/A fix, preview, reason rule, few-shot off, four eyes, empty state. **P1:** `features/review/route.tsx` with `/review/:appealId` (one appeal expanded); a reason template in the checklist's justification format («Управление диалогом — 7. … Основание — реплики №…»); section deltas in the preview; a call mini-player in the fragment (±3 s around the evidence); the type picker for C8 decisions.

**Acceptance:**
1. With Нина's appeal filed, `#/review` as Ревьюер shows it first and expanded, with author, comment, «Просит: 7 из 10» and the fragment in peach.
2. «Изменить → 7» previews «Итог: 78,46 → 85,38» before saving; the AI's own answer cannot be chosen; a 14-character reason cannot be saved; the saved request has `X-CQA-Role: reviewer` and `add_to_fewshot: false`.
3. p4-ru-002 C1: the appeal dialog offers only Да / Нет (C1 has no N/A on calls since 2026-11-08), and the row shows the «Засчитано правилом: начало звонка вне записи» badge.
4. The same signature for author and reviewer triggers the four-eyes warning and a second confirmation.
5. After saving, the card and «Мои апелляции» show the decision; the open count in the header and in the sidebar badge drops by one.
6. As Директор the page is read-only with the switch button; with no open appeals the empty state appears.

### WP-5 · Checklist Studio and scoped apply

- **Order** 1 · **depends on** WP-0 · **lane** D · **estimate** 1 h 55
- **Owns:** `web/src/features/checklists/**`, `web/src/api/studio.ts`, `web/src/i18n/studio/**`
- **Stories:** CHK-01, CHK-02, CHK-03, CHK-04, CHK-05, CHK-06, CHK-09, CHK-10, CHK-12, CHK-13, CHK-15, CHK-16, CHK-18, CHK-21, SYS-06, DEMO-05, DEMO-12

**Brief.** Make live ask 3 obvious and cheap. Keep `ChecklistsPage.tsx` and `ChecklistEditorPage.tsx` (names, default exports). Existing endpoints: `GET /api/checklists`, `GET /api/checklists/active`, `GET …/versions/{vid}` (with `weights {total, by_code, normalized}` and `export_ids`), `POST /api/checklists/{id}/versions {criteria, sections?, scoring_rules?, change_note, activate}` (422 `invalid_checklist` with `errors[]` paths like `criteria[2].weight`), `POST …/versions/{vid}/impact` (what-if, no model calls), `GET …/diff/{b}`, `POST /api/evaluations/run` (`dry_run`, `mode: "partial"`, `base_version_id`, `conversation_ids`). **List:** card «Freedom Broker · Чек-лист качества чатов» · «v1 · Активна · 8 критериев · 4 раздела · Σ весов 100 · базовая версия организаторов», versions table (status «Активна» / «Черновик», note, date), «Открыть активную версию». **Editor:** v1 and any version that has evaluations open read-only with the banner «Базовая версия организаторов. Изменения сохраняются как новая версия — v1 и выгрузка results.json не меняются.» and «Создать версию на основе v1» (local draft). Rows grouped by section: code, title, type summary («Да / Нет», «Шкала 20 / 15 / 10 / 5 / 0»), N/A chip, «флаг» for C8, a `NumberStepper` weight and the share %; a click expands the inline editor (title, instruction, answer type with confirm before a reset, options with points and anchors, N/A + condition, «Чат | Звонок» tabs for the call instruction and call N/A, critical flag). «+ Добавить критерий» per section and at the bottom: next free code = highest numeric suffix + 1 (C9 even after deleting C5), Да/Нет, weight 10, expanded with focus on the title. Row menu «Заменить критерий»: the old row greyed «будет исключён из новой версии» + a new row in the same place and section with the next code and the old weight; note that results.json stays on v1. Delete with a 10 s undo. **Weights panel:** weight and share per criterion, Σ; amber «Σ 105 · веса относительные: итог всё равно нормируется к 100» with «Нормализовать до 100» (largest remainder) and undo; C8 weight disabled; a what-if line debounced 400 ms from `…/impact`: «30 диалогов изменятся · средний 82,04 → 81,90 · только пересчёт весов, без вызова модели» (hidden on a legacy API). **Fix** the call override: editing the call instruction keeps `na_allowed` and `na_condition` (merge; `CriterionEditor.tsx` replaces them today). Client validation (empty fields, duplicate codes or values, < 2 options, N/A without a condition, Σ = 0) plus 422 mapping next to the fields. **Save dialog:** change summary computed in the browser («C7: вес 5 → 10 · C9 добавлен»), note ≥ 5 characters, «Сделать активной сейчас» off by default; toast «Создана версия v2 (черновик)» with «Применить к диалогам». **ApplyDialog** (new file; replaces the `EvaluateDialog` use here): scopes «Пример: 3 диалога (RU · KZ · EN)» = p1-ru-022, p1-kz-013, p1-en-002 · «Набор DEV (65)» (ids from `GET /api/conversations?dataset_id=`) · «Список ID»; mode «Частичная — только изменённые критерии» (base = the active version) or «Полная»; dry run first («Только пересчёт весов · 0 вызовов модели · $0,00» or calls and cost from the API, budget line, guard message); run with the existing `JobProgress`; summary table «Диалог · было · стало» with links. Never send `conversation_ids: []`.

**P0:** everything above except value diffs. **P1:** diff dialog with old → new values and a cost footer; export-id badge «JSON 1.0: contact … critical» per criterion; «Проверить на диалоге» (`POST /api/checklists/test-criterion`) for a new criterion; version author line.

**Acceptance:**
1. As Методолог: v1 is read-only with the banner; «Создать версию на основе v1» unlocks a draft; as Директор everything stays read-only with the notice.
2. «+ Добавить критерий» yields C9 (also after deleting C5) in the chosen section, expanded, title focused.
3. C7 weight 5 → 10 shows Σ 105 in amber; «Нормализовать до 100» gives 10 / 14 / 19 / 19 / 14 / 14 / 10 with undo; the what-if line reports the changed dialogs and the new mean without a model call.
4. «Заменить критерий» on C5 greys C5 and adds C9 with weight 15 in the same section.
5. After editing C1's call instruction and saving, C1 still offers only Да / Нет on calls (no N/A since 2026-11-08), and the other keys of the voice override are kept.
6. Saving requires a note; v2 is saved as a draft and v1 stays active.
7. Apply to «Пример: 3 диалога», partial: a weight-only change dry-runs «0 вызовов модели · $0,00»; with C9 the dry run shows calls and cost from the API; the run ends with the was/now table; no request contains `conversation_ids: []`.
8. Add, replace, reweight + normalise, save and apply each take ≤ 10 clicks.

### WP-6 · Conversations, intake, runs and results.json export

- **Order** 2 · **depends on** WP-0 · **lane** D · **estimate** 1 h 55
- **Owns:** `web/src/features/conversations/**`, `web/src/features/jobs/**`, `web/src/features/export/**`, `web/src/api/dialogs.ts`, `web/src/i18n/dialogs/**`
- **Stories:** CONV-01, CONV-03, CONV-04, CONV-05, CONV-06, CONV-07, CONV-08, CONV-10, SHELL-13, SYS-03, SYS-06, EXP-01, EXP-02, EXP-05

**Brief.** Own the work list and the intake, run and export dialogs. Keep `ConversationsPage.tsx`, `EvaluateDialog.tsx` (WP-2 imports it), `JobProgress.tsx` and `useJobProgress.ts` (WP-2 and WP-5 import them) with backward-compatible props. **List `#/conversations`:** header «Диалоги» · «65 диалогов · 65 оценено · набор DEV»; default columns ID · Канал и язык · Оператор · Итог (`ScorePill`, tooltip «исходный …» after a decision) · Грубое нарушение (`ViolationTypeChip`, joined client-side from `GET /api/violations`) · Апелляция (`ReviewStateChip`) · Оценено; turns, first response, provider and cost move to the card. One-row filter bar (search, Язык, Канал, Статус, «Только с грубыми нарушениями») mirrored in the URL (`q`, `language`, `channel`, `status`, `critical`); the server search keeps transcript hits: remove the client-side filter that drops them and show `match.snippet` with «№n»; an exact id + Enter opens the card (`linkState`). Toolbar by capability: «Импорт» (group_id, shows created / updated / unchanged counts), «Загрузить или вставить», «Оценить выбранные (N)», «Экспорт ▾» (CSV · results.json…). Empty database → «1 Импорт → 2 Оценка → 3 Карточка» with «Импорт по group_id» prefilled «DEV». **Paste** (`?paste=1` opens it): never send a `conversation_id`; first `POST /api/conversations/paste {dry_run: true}` for a preview (turns, speakers, language, steering warnings), then «Создать и оценить» → paste, run, open the card; 409 `conversation_exists` offers «Сохранить как копию» (`on_conflict: "rename"`) only. **Upload:** show `items[]` statuses; ask before any `on_conflict`. **EvaluateDialog:** scope selected ids or «все» via `all: true` (never `conversation_ids: []`); dry run first with the estimate and the budget line; readable guard messages (429 `budget_exceeded`, `too_many_dialogs`, `too_many_jobs`); confirmation above 20 dialogs; provider choices with honest availability from `/api/health`. **JobProgress:** restyled, no mascot, completion summary («Оценено 3 · ошибок 0 · $0,61»). **ResultsExportDialog** (`features/export/`, opened by «Экспорт ▾» and by `?export=results`; `can("results.export")`): dataset picker from `GET /api/datasets` (DEV 65; the control set 10 once imported; expected count editable), «Проверить» → `GET /api/export/results/validate?group_id=&expect=` → «Файл валиден: schema 1.0 · 65 записей · ok 65 · error 0», model block and team name, errors listed with links to the dialogs; «Скачать results.json» via `fetchBlob("/api/export/results.json", {group_id, download: true})` reading `X-Results-*`; notes «Выгрузка строится по чек-листу v1 и по ответам модели без правок ревьюеров», the target paths `results/dev/results.json` and `results/prod/results.json` with copy buttons, «Закоммитьте и отправьте файл до стоп-кода» (never claim it was pushed). The results.json backend, CLI and control-run script belong to the team lead: do not edit `api/**` or `scripts/**`.

**P0:** list, filters, search fix, toolbar, paste with preview, evaluate dialog rules, export dialog, job progress restyle. **P1:** «Набор» filter from `/api/datasets`; status «Ошибка оценки» with «Повторить для всех (N)» (`status=failed`); «Возобновить» on interrupted jobs (`POST /api/jobs/{id}/resume`); upload preview for multi-record JSON; sortable columns (`sort`).

**Acceptance:**
1. «читать умеете» lists p1-ru-029 with the snippet «№10 …»; `?language=KZ&critical=1` lists exactly p1-kz-013 and p1-kz-015; an exact id + Enter opens the card; Back keeps the filters.
2. Paste never sends an id; the preview shows turns and language; «Создать и оценить» opens the new card with a running evaluation; a conflicting paste offers only «Сохранить как копию».
3. «Оценить выбранные» above 20 dialogs asks for confirmation with the dry-run estimate; a guard refusal reads as a sentence, not a code; no request contains `conversation_ids: []`.
4. The export dialog validates DEV as «Файл валиден: schema 1.0 · 65 записей · ok 65 · error 0» and downloads `results.json`; it is hidden for roles without `results.export`.
5. No mascot in the list, dialogs or job cards; no horizontal scroll at 1024 px.

### WP-7 · Overview, analytics and settings

- **Order** 2 · **depends on** WP-0 · **lane** A · **estimate** 1 h 50
- **Owns:** `web/src/features/overview/**`, `web/src/features/analytics/**`, `web/src/features/settings/**`, `web/src/api/insights.ts`, `web/src/i18n/overview/**`, `web/src/i18n/prefs/**`
- **Stories:** EXE-01, EXE-02, EXE-04, ANL-02, ANL-03, ANL-04, ANL-12, SHELL-02, ADM-01, ADM-02, ADM-07, EXP-03, DEMO-03, DEMO-16

**Brief.** Give directors and leads their home and finish the settings for hands-on safety. Keep `AnalyticsPage.tsx` and `SettingsPage.tsx` names and default exports; register `#/overview` in `features/overview/route.tsx`. **`#/overview` «Обзор»** (home of Руководитель группы and Директор) on `GET /api/overview?days=30` (`coverage`, `quality {mean, median, p25, p75, share ≥ 85, share < 60}`, `critical.by_type`, `appeals`, `by_language`, `by_channel`, `criteria[]`, `top_failing[]`, `cost {per_dialog_usd, by_model}`, `operators.at_risk`), falling back to `GET /api/analytics/summary` + `GET /api/violations` when `useCapabilities().legacy` or on 404: the quality ring «82,04 / 100 · 65 диалогов» with a definition popover (mean of final totals after review, N/A redistributed, half-up rounding, gross errors separate); band chips from `lib/bands.ts` with the legend «Норма ≥ 85 · Внимание 60–85 · Критично < 60»; the risk tile «Грубые нарушения: 8 из 65 · Оскорбление 1 · Платная услуга 4 · Раскрытие 3» linking to `#/violations?type=…`; RU / KZ / EN and chats / calls means with n, linking to filtered lists (calls «малая выборка, n = 5»); the cost tile «≈ $0,22 за оценку · среднее по 75 оценкам · Claude Opus 5.5» (never «все вызовы учтены»); an appeals tile (open / decided); stamp «DEV · чек-лист v1 · по данным на HH:MM». Директор sees no write action anywhere. **`#/analytics`:** restyle on the new tokens; every number links to its filtered list or names its source in a tooltip; heatmap cells show n; the distribution uses the bands legend; operator rows link to `#/operators/<name>` when `hasRoute("/operators/:name")`. **`#/settings`** with `?section=`: «Оформление» (personal theme light/dark via `useUi`; «Фирменный персонаж» = `PUT /api/settings {mascot_enabled}`, admin only, default off, hint «Только в пустых состояниях; не используется на лендинге, в карточке, в отчётах и во встраивании»); «Профиль» (signature via `useRole().setSignature`; operators get «Сменить оператора»); «Модель и стоимость» (read-only for non-admin with «Изменяет администратор»: provider, model, ensemble, explanation language, PII masking, local model status from `/api/health` «поддерживается, на стенде не подключена» in grey; few-shot shown off with «Отключено до фиксации набора примеров (решение ML-владельца)» and no toggle; a generated statement in RU and EN with «Копировать для README»: models used, results.json mode, «≈ $X за оценку — среднее по N оценкам на стенде; стоимость считается по токенам каждого вызова; компиляция критериев выполняется один раз при сохранении версии и в стоимость оценки не входит»); «Экспорт результатов» (link `#/conversations?export=results`); «Готовность стенда» (lead and admin: `GET /api/system/readiness?group_id=DEV` as a green/red list with messages); «О системе» (web commit and build time from `BUILD`, API version from `GET /api/version`).

**P0:** overview (with fallback), analytics restyle, settings sections listed. **P1:** «Где теряем баллы» (`top_failing` with an example link); «Операторы в зоне риска» (`operators.at_risk` → `#/operators/<name>` when registered); KZ/RU/EN scope selects on the overview.

**Acceptance:**
1. `#/overview?role=director` is the director's home with the ring, band chips, the risk tile 1 / 4 / 3, language and channel tiles with n and the honest cost tile; no write action is visible anywhere for Директор.
2. With `/api/overview` unavailable the page renders from the summary and violations endpoints, with the same stamp.
3. Every number on the overview and in analytics either links to a filtered list or names its source.
4. Settings: theme switches light/dark and persists; «Фирменный персонаж» is read-only except for Администратор and, when on, the mascot appears only in empty states.
5. «Копировать для README» copies the statement in the current language; no «все вызовы учтены» text.
6. «Готовность стенда» shows the readiness checks for lead and admin; «О системе» shows the web commit and the API version.

### WP-8 · «Сценарии для жюри»

- **Order** 1 · **depends on** WP-0 · **lane** A · **estimate** 1 h
- **Owns:** `web/src/features/jury/**`, `web/src/i18n/jury/**`
- **Stories:** DEMO-01, DEMO-04

**Brief.** Build «Сценарии для жюри», the one-click entry to every live ask. Contribute to the shell only through `features/jury/slot.tsx` (§3.3): a `sidebar-bottom` button «Сценарии для жюри» (`PlayCircle`; icon-only with a tooltip when the sidebar is collapsed) and an `overlay` `JuryPanel` shown when `useShellPanel().active === "jury"`. **JuryPanel:** right `Drawer` 400 px, title «Сценарии для жюри», subtitle «Каждый вопрос жюри — в один клик. Роль переключится автоматически.», seven rows from `scenarios.ts` with the copy of `docs/LANDING.md` §3.4: 1 Грубые нарушения и почему → analyst `#/violations`; 2 Оператор подаёт апелляцию → operator Нина `#/my`; 3 Ревьюер принимает решение → reviewer `#/review`; 4 Изменить чек-лист → methodologist, target resolved from `GET /api/checklists/active` → `#/checklists/<checklist_id>/versions/<version_id>` (fallback `#/checklists`); 5 Результат диалога с обоснованием → analyst `#/conversations/p1-ru-029?criterion=C8&turn=10`; 6 Звонок: переход к моменту записи → analyst `#/conversations/p4-ru-002?criterion=C6`; 7 Оценить новый диалог → analyst `#/conversations?paste=1`. Each row: number, title, one sentence, the chip «переключит роль на …» when the role differs, «Открыть». `useRunScenario` calls `useRole().setRole(role, {operator, navigate: "none"})`, navigates to the target, closes the drawer and marks the row done (tick; `cqa.jury.done`; «Начать заново» resets). **Live hints** (each GET with a 3 s timeout, silent on failure): row 1 «8 диалогов: оскорбление 1 · платная услуга 4 · раскрытие 3» from `GET /api/violations` counts; row 2 «Апелляция уже подана — откройте «Мои апелляции»» when `GET /api/appeals?operator=Нина&status=open` has a C6 appeal on p1-ru-005; row 3 «Открытых апелляций: N» or «Открытых апелляций нет — начните со сценария 2». The drawer opens automatically once on the first app visit in a browser (`cqa.jury.autoOpened`) and whenever the URL has `?tour=1` (the shell calls `open("jury")`). Also export `JuryScenarioCards({variant})`: the same seven scenarios as a 4 + 3 card grid (2 per row ≤ 1024 px, 1 per row ≤ 640 px) with the role chip and «Открыть →», for WP-9's landing section. Keyboard accessible; no mascot.

**P0:** all of the above. **P1:** a coach-mark outline on the control named in the scenario (e.g. «Оспорить оценку»); «Прогреть сценарии» for lead/admin (prefetches every target's GETs before the slot).

**Acceptance:**
1. The sidebar button opens a 400 px drawer with 7 scenarios; each «Открыть» switches the role (scenario 2: «Оператор · Нина») and opens its target; ticks persist; «Начать заново» clears them.
2. `?tour=1` opens the drawer; a fresh browser opens it once on the first app visit and never again.
3. Scenario 4 lands on the active version's editor; scenario 3 reports when no appeal is open; scenario 2 switches its hint when Нина's C6 appeal is already open.
4. `JuryScenarioCards` renders the same scenarios as cards and works outside the app shell.

### WP-9 · Landing: core sections

- **Order** 3 · **depends on** WP-0, WP-1, WP-8 · **lane** A · **estimate** 1 h 55
- **Owns:** `web/src/features/landing/*.tsx`, `web/src/features/landing/*.ts`, `web/src/features/landing/*.json`, `web/src/features/landing/sections/**`, `web/src/i18n/landing/**`
- **Stories:** LAND-01, LAND-02, LAND-03, D365-01, DEMO-01

**Brief.** Build the corporate landing at `#/` (bare layout, outside the app shell) from `docs/LANDING.md`: copy, order, data contract and acceptance are final there. `route.tsx` registers path `/` with layout `bare` (it replaces the role-home redirect). **LandingPage** renders the core sections and the proof sections merged by an `order` number: discover proof sections with `import.meta.glob("./proof/*.tsx", { eager: true })` (each exports `section = {id, order, navLabelKey?, Component}`), sort, alternate white and `--bg` backgrounds by rendered position (hero, CTA panel and footer excluded) and render header anchors only for the present sections. **Core sections:** Header (sticky white, wordmark, anchors, RU · KK · EN shared with the app, primary «Открыть демо» → `useRole().home`, or «Продолжить как {роль}» for a returning visitor); Hero on the deep-green band (eyebrow, H1, subline, «Открыть демо» inverse, «Сценарии для жюри» scrolls to `#scenarios`, reassurance line, «Работает с» chips) with the product shot: WP-1's `QualityScoreCard` variant compact for p1-ru-029 (`GET /api/conversations/p1-ru-029/evaluation`, `maxRows` 3, `onOpen` → `#/conversations/p1-ru-029`) over your own two-lane strip drawn from `GET /api/audio/p4-ru-002/peaks?buckets=120` (numbers only, no transcript text); Live counters (`GET /api/analytics/summary` + `GET /api/violations`; values, sources, links and stamps per `docs/LANDING.md` §3.3); «Сценарии для жюри» (`id="scenarios"`, WP-8's `JuryScenarioCards` variant landing); «Как это работает» (five icon steps); «Для кого» (four role cards with «Открыть как …» links using `?role=`); «Встраивается в Dynamics 365» (bullets + a neutral frame with a lazy iframe of `index.html?embed=1&conversation=p1-ru-029&host=d365&userlcid=1049`, `1033` in EN, rendered when the section enters the viewport); the final CTA panel; the footer (links, the results files with a HEAD check for prod, Swagger, the build commit from `BUILD` and the API version). **Every live block:** parallel GETs without custom headers, a 3 s budget, skeletons of the final size, a fallback to `snapshot.json` (synthetic chats and aggregate numbers only, stamped «по состоянию на …»), never 0. No mascot whatever the setting. App routes stay lazy, so the landing does not ship recharts.

**P0:** all core sections. **P1:** real UI crops in «Как это работает»; KK copy for the hero and the jury section after the native check; `og:image` is out of scope.

**Acceptance:** `docs/LANDING.md` §6, items 1–7, with the proof sections absent or present.

### WP-10 · Landing: proof sections

- **Order** 4 · **depends on** WP-9 · **lane** C · **estimate** 1 h 30 · **first to sacrifice**
- **Owns:** `web/src/features/landing/proof/**`, `web/src/i18n/landingProof/**`
- **Stories:** LAND-02, LAND-03

**Brief.** Add the landing's proof sections as self-contained files in `web/src/features/landing/proof/` (WP-9's page discovers them). Each file exports `section = { id, order, navLabelKey?, Component }`; the page sorts by order and alternates backgrounds, so do not set section backgrounds yourself (use a transparent wrapper with the standard section padding and the 1200 px content width). Copy is final in `docs/LANDING.md` §3.5, §3.7, §3.9, §3.10, §3.12 and §3.13 (RU source, EN complete, KK optional with RU fallback). Build in this order and stop where the time runs out: (1) `security.tsx`, order 70, id `security`: «Данные под контролем» bullets and the «Модели и стоимость» card with live rows from `GET /api/settings`, `GET /api/health` and `GET /api/analytics/summary` (providers: Σ `total_cost_usd` ÷ Σ `n` → «≈ $0,22 за оценку · среднее по 75 оценкам»; local model «поддерживается, на стенде не подключена» unless health reports it; results.json mode derived from the provider), status dots green / grey / amber, never red, and the link «Как устроена оценка» → `#/conversations/p1-ru-029?tab=trace`; (2) `problem.tsx`, order 30: three numbered cards; (3) `features.tsx`, order 50: three chapters alternating text and a live crop (chapter A may use WP-1's compact `QualityScoreCard`; chapter C the top three rows of `GET /api/violations`), «Открыть пример →» links; (4) `languages.tsx`, order 60: three neutral quotes from synthetic RU, KZ and EN DEV chats kept in a constant (never a gross-violation quote, never a call), a marquee that stops under `prefers-reduced-motion`; (5) `compare.tsx`, order 80: the seven-row table in a radius-24 card that scrolls inside at 375 px, the CQA header as a deep-green pill, the cost row live; (6) `faq.tsx`, order 90: an accessible accordion with the ten questions, the cost answer live. Every live value has a 3 s budget and a static fallback; never write «все вызовы учтены»; never show an accuracy figure. No mascot.

**P0:** `security.tsx`. **P1:** the other five, in the order above.

**Acceptance:**
1. Each section appears in the documented order without any edit to `LandingPage`.
2. Copy matches `docs/LANDING.md`; cost copy is honest; the local-model row follows `/api/health`.
3. At 375 px nothing scrolls sideways; the comparison table scrolls inside its card; the FAQ works with the keyboard.

### WP-11 · Demo readiness, docs and repository hygiene

- **Order** 5 · **depends on** WP-1, WP-2, WP-3, WP-4, WP-5, WP-6, WP-7, WP-8, WP-9 (the two scripts may start any time after OP-1; the docs need the merged UI) · **owner** team lead + one free agent · **estimate** 1 h 30 + two rehearsals
- **Owns:** `docs/DEMO.md`, `docs/WEB.md`, `README.md` (sections «Live demo» and «Model and cost» only, wording agreed with the team lead), `docs/USER_STORIES.md` (the first acceptance line of CALL-07 only), `scripts/seed_demo.py`, `scripts/smoke_asks.py`
- **Stories:** DEMO-02, DEMO-03, DEMO-05, DEMO-16, DEMO-17, EXP-08, SYS-01

**Brief.** Prepare the defence and the hands-on session once the P0 packages are merged; the team lead runs every deploy. (1) New `scripts/smoke_asks.py`: a read-only check of the four live asks against a CQA API (default the hosted one, `--api` for local), using only GETs and the computing POSTs that store nothing, printing one line per check like `scripts/smoke.py` and exiting 1 on a failure: `GET /api/violations` → total 8 and counts insult 1 / paid_bypass 4 / disclosure 3, and p1-ru-029's item has an evidence `turn_idx` 9 (№10); `POST /api/appeals/preview` for p1-ru-005 C6 with `new_answer` 7 → `final_total_score` 85.38 while the current total is 78.46 (find the `evaluation_criterion_id` via `GET /api/conversations/p1-ru-005/evaluation`); `POST /api/checklists/{id}/versions/{vid}/impact` with the active criteria and C7 weight 10 → `changes.weight_only` true and no re-judge codes; `GET /api/export/results/validate?group_id=DEV&expect=65` → valid; `GET /api/system/readiness?group_id=DEV` → `ready_for_demo`. (2) `scripts/seed_demo.py`: sign as «Жанар Б.» (analyst) and «Айгерим С.» (reviewer); keep `add_to_fewshot: false` and the exclusions of p1-ru-029 and p1-ru-005; `--dry-run` lists what it would create. (3) `docs/DEMO.md`: rewrite for the final UI (role pill at the top of the sidebar, no top bar, «Сценарии для жюри», staff signatures), the five-minute defence in the guide's order with timings (≤ 4:45 in total, each live ask ≤ 1:30), the four live asks with the deep links of `docs/IA.md` §7, hands-on rules (`docs/ROLES.md` §8), fallbacks (local Docker with a downloaded `baseline` snapshot, a screen recording), the go/no-go list of §8 and the results of two timed rehearsals. (4) `docs/WEB.md`: routes, roles, tokens, discovery (`route.tsx`, `slot.tsx`, i18n namespaces, api domain modules), the light default and the mascot gate. (5) Reword the first acceptance line of CALL-07 in `docs/USER_STORIES.md` so it no longer quotes a spelled-out name from a real call. (6) Grep `web/src/mock`, `docs` and the landing snapshot for real-call transcript text and report anything left. Do not edit `api/**`, `results/**` or `scripts/control_run.*`.

**Acceptance:**
1. `scripts/smoke_asks.py` passes against the hosted API after the B-0 deploy and writes nothing (the appeal count is unchanged before and after).
2. `scripts/seed_demo.py --dry-run` names the two seeded appeals, never p1-ru-005 or p1-ru-029, and signs with the staff names.
3. `docs/DEMO.md` matches the final UI and records two rehearsals under 4:45 with each live ask under 1:30.
4. No real-call transcript text or spelled-out client name in `web/src` fixtures, the landing snapshot or `docs/USER_STORIES.md`.

## 5. Team-lead operations (outside the packages)

| ID | When | Action | Stories |
|---|---|---|---|
| OP-1 | now, before WP-0 merges | **B-0**: `cd api && .venv/bin/pytest -q`; copy the SQLite volume; deploy the working tree to Railway; `scripts/smoke.py` green; `GET /api/version`, `/api/roles`, `/api/overview` answer. The web keeps working on the old API through the capability probe, but every P0 acceptance assumes B-0. | SHELL-04, SHELL-06, ADM-03, CHK-10, CHK-12, EXE-01 |
| OP-2 | right after OP-1 | Run `python -m cqa.ingest.renormalize` against the hosted volume (calls only; turn idx and evaluations unchanged); open p4-ru-002 and check turn №15 at ≈ 02:00; play every call once so the audio is cached. Tell the ML owner that call C6 acoustic facts shift. | CALL-03, CALL-12 |
| OP-3 | with OP-1 | Railway variables: `CORS_ORIGINS` = the Vercel origin + localhost; `MAX_DIALOGS_PER_RUN=25`, `MAX_CONCURRENT_JOBS=2`, `DAILY_BUDGET_USD=60` for the jury period; `CQA_ADMIN_TOKEN`; settings `fewshot_from_reviews=false` until the ML owner freezes the bank; `SEED_SYNC` as the ML owner decides. | SYS-07, ADM-03, DEMO-04, DEMO-07 |
| OP-4 | after WP-11's seed change; again after every rehearsal and hands-on session | `scripts/seed_demo.py` → `POST /api/admin/snapshots {"name": "baseline", "note": "демо-данные для жюри"}` → `GET /api/system/readiness?group_id=DEV` green. Restore with `POST /api/admin/snapshots/baseline/restore {"confirm": "baseline"}`. | DEMO-03, ADM-06 |
| OP-5 | after every web or API deploy (checkpoints C1–C4) | `scripts/smoke.py` + `scripts/smoke_asks.py`; p1-ru-029 at 1280×720; open the seven jury scenarios once (warm-up). Promote `redesign` to production only when green. | DEMO-17 |
| OP-6 | architecture side, 2026-11-08 | Re-judge C1 on p4-ru-002 under the new rule (opening outside the recording → Да by code; `mode: "partial"`, `base_version_id` v1, `only_codes: ["C1"]`) and regenerate `results/dev/results.json` (contact true everywhere). | CALL-09 |
| OP-7 | optional, P1, only before H+18 | Additive `requested_answer` on appeals (nullable column through the existing additive-migration helper, accepted by `POST /api/appeals`, returned in items). WP-1 and WP-4 already handle both forms. | APL-02 |
| OP-8 | H+21 → H+24 | H+21 UI freeze (no web or API deploys); H+22 configuration freeze (provider, prompt version, few-shot bank); H+23 control run by `scripts/control_run.py`; push before the stop-code; tag the stop-code commit; check that «О системе» and `/api/version` show that commit. | DEMO-06 … DEMO-11, DEMO-16 |

## 6. Cut on purpose (not built before the stop-code)

| Area | Cut | Why it is safe |
|---|---|---|
| Shell | top bar, `#/start`, blocking role picker, first-open role dialog, notifications bell and polling, keyboard shortcuts, help and glossary panel, defence timer, presentation mode | the sidebar holds role, language, status and the jury panel; the mockup has no top bar |
| Management depth | operators directory, coaching notes and acknowledgements, teams, the executive A4 print brief, v1 ↔ v2 analytics, the matrix view, side-by-side evaluation compare, trend charts | `#/overview` and the P1 scorecard tell the management story |
| Governance and admin UI | approval workflow, knowledge-base editor, calibration UI, control-hour console, audit, jobs, backups and integrations consoles | Swagger and the CLI answer any jury question; the team operates via scripts |
| Backend surface | coaching, acknowledgement and notification tables; appeal edit, seen and close; import dry-run tokens and content hashing; import-url; audio sync PUT/DELETE, refresh and prewarm; public stats; scoring preview endpoint; cost-statement endpoint; the director `critical_only` permission; any role relabel or home change in `roles.py` | no live ask needs them; the working tree already covers the rest |
| ML-owned code | any edit to `api/cqa/judge/**` before the control run (incl. carry-forward and incomplete codes) | the working tree already carries decided appeals forward; accuracy is the main criterion |
| Dependencies and dev pages | self-hosted Inter, Playwright and axe in CI, `#/__ds` gallery, chart-palette validation tooling | system fonts cover Kazakh; the build stays small |
| Layouts | phone and tablet app layouts, dark-theme polish beyond the tokens, multi-view Dynamics 365 embeds, appeals inside the embed | the defence runs on a laptop and a projector; the landing is responsive |
| Claims | «все вызовы учтены» / «включая все вызовы» on cost; any accuracy percentage before the ML owner's gold-set comparison; «локальная модель подключена» unless `/api/health` says so | credibility with the jury |
| Data | few-shot learning from rehearsal and jury decisions until the ML owner freezes the bank; the real call transcript in `web/src/mock/fixtures`; the spelled-out name in CALL-07 | accuracy and the public repository check |
| Process | deploys after the H+21 UI freeze or during the control hour; team writes to the hosted instance during the jury slot | the control run and the demo stay stable |

## 7. Order of sacrifice (if time runs out)

1. WP-10 P1 sections: FAQ → Сравнение → Языки и каналы → Возможности → Проблема.
2. WP-3 P1 (scorecard, withdraw, candidates tab), WP-7 P1 («Где теряем баллы», operators at risk).
3. WP-5 P1 (value diffs, export-id badge, test on a dialog), WP-1 P1 («Пересчитать», copy actions), WP-2 P1 (follow mode, History polish).
4. WP-9 «Как это работает» and «Для кого» (keep the hero, counters, jury section, D365 block, CTA, footer).
5. WP-10 `security.tsx`.

Never cut: WP-0, WP-1, WP-2 P0, WP-3 P0, WP-4 P0, WP-5 P0, WP-6 P0, WP-8, the export dialog, the B-0 deploy, the seed and the snapshot.

## 8. Go / no-go before the defence (run twice)

1. Hosted web and API on the merged commit: `/api/version` answers; Settings → «О системе» shows the same web commit as the repository.
2. Light theme and no mascot on the landing and on five app routes (`document.querySelector('img[src*="mascot"]') === null`).
3. `#/violations` shows 8 = 1 / 4 / 3; «Открыть в диалоге» on p1-ru-029 lands on №10 in peach.
4. p1-ru-005 C6 has no open appeal; the seeded appeals exist (one open, one decided); no few-shot example was added.
5. Checklist v1 is active; no rehearsal drafts are visible, or `baseline` was restored.
6. p4-ru-002 is renormalised; turn №15 plays at ≈ 02:00; every call was played once.
7. Budget left ≥ $20; run guards on; `fewshot_from_reviews` as the ML owner decided.
8. `baseline` snapshot younger than two hours; the restore command tested once.
9. Card, register, studio and review checked at 1280×720 full screen and at 110 % zoom: no horizontal scroll, the card footer visible.
10. `scripts/smoke.py` and `scripts/smoke_asks.py` green; readiness `ready_for_demo`.
11. The five-minute defence under 4:45 twice; each live ask under 1:30.
12. Fallbacks ready: local Docker with a downloaded `baseline` snapshot, a screen recording of the run, the README links.

## 9. Risks

| # | Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|---|
| 1 | B-0 deploy breaks the hosted instance or lands late | medium | high | deploy first (OP-1), copy the volume, smoke, keep the previous Railway deployment for rollback; the capability probe keeps the web running on the old API | team lead |
| 2 | WP-0 overruns and blocks everyone | medium | high | contract commit and tag within 45 min; packages branch from `wp0-contract` and rebase on the final WP-0 | lane A |
| 3 | The token retune changes untouched screens in unexpected ways | medium | medium | WP-0 checks all seven existing routes; production only after checkpoint C1 on a preview | WP-0, team lead |
| 4 | Merge conflicts between parallel agents | medium | high | disjoint ownership (§4), discovery instead of registries, namespaced i18n, frozen props (§3.12) | everyone |
| 5 | Rehearsal or jury decisions become few-shot examples before the control run | high | high | the UI always sends `add_to_fewshot: false`; hosted setting off (OP-3); the seed adds none | WP-4, team lead, ML owner |
| 6 | Jurors change the shared instance | high | medium | v1 lock, drafts by default, paste never overwrites, admin hidden, run guards, `baseline` restore after each session | WP-5, WP-6, WP-0, team lead |
| 7 | The card does not fit a 1280×720 projector | medium | medium | compact density and the sticky footer (D18); acceptance at 1280×720 and 110 %; present in full screen | WP-1, WP-2 |
| 8 | Call jumps land wrong or C1 contradicts the call rule on p4-ru-002 | medium until OP-2 | medium | OP-2 now, the C1 banner, OP-6 if the ML owner agrees; otherwise answer ask 4 on p1-ru-029 | team lead, WP-2 |
| 9 | Over-claims on the landing (cost, local model, accuracy) | medium | high for credibility | copy rules (`docs/DESIGN_SYSTEM.md` §11.8); live values with stamps | WP-9, WP-10, WP-7 |
| 10 | Kazakh copy wrong or missing | medium | low to medium | RU fallback; native check of hero, navigation, roles and the card | WP-0, team |
| 11 | Real client data in the public repository | certain today | high | WP-0 removes the fixture; WP-11 rewords CALL-07 and greps | WP-0, WP-11 |
| 12 | A deploy during the control hour | low | high | OP-8 freeze at H+21 | team lead |
| 13 | Slow first clicks (cold start, cross-origin) | medium | medium | header-free reads (no preflight), skeletons, warm-up through the jury scenarios ten minutes before the slot | WP-0, WP-11 |
| 14 | The landing fails without the API | low | medium | snapshot fallback with stamps; the text never waits for the API | WP-9 |

## 10. Must-demo coverage

| Story | Where it is built |
|---|---|
| SHELL-01, SHELL-02, SHELL-04, SHELL-05, SHELL-07, SHELL-10 | WP-0 (SHELL-02 toggle in WP-7; SHELL-05 four eyes in WP-4; SHELL-10 card in WP-1, WP-2) |
| CONV-01, CONV-03, CONV-04, CONV-06, CONV-07, CONV-10 | WP-6 (re-import idempotency and 409 are in the working tree, OP-1) |
| CONV-02 | working tree (OP-1); WP-6 shows created / updated / unchanged |
| CONV-08 | WP-2 (card states), WP-6 (P1 failed filter), working tree |
| CARD-01 … CARD-07 | WP-1, WP-2 |
| CARD-08 | WP-2 |
| CALL-01, CALL-02, CALL-03, CALL-09, CALL-12 | WP-2 (+ OP-2, OP-6; C1 rule badge and Да / Нет only in appeals and decisions: WP-1, WP-4) |
| GV-01, GV-02, GV-03 | WP-3 (card block: WP-1) |
| APL-01, APL-09 | WP-1 (queue side of APL-09: WP-4) |
| APL-05 … APL-08 | WP-4 |
| APL-10 | working tree (partial runs carry decided appeals forward) |
| CHK-01 … CHK-06, CHK-10, CHK-12, CHK-13, CHK-15, CHK-16, CHK-21 | WP-5 |
| OPR-01, OPR-02, OPR-04, OPR-05 | WP-0 (identity, route policy), WP-3 |
| EXP-01 | WP-6 |
| EXP-02 | team lead (builder), WP-6 (notes) |
| EXP-03 | WP-7 (statement), WP-11 (README wording) |
| ADM-03, ADM-06 | OP-3, OP-4 (API only) |
| DEMO-01 | WP-8 (+ WP-9 landing section) |
| DEMO-02, DEMO-03, DEMO-05 | WP-3, WP-4, WP-5, WP-11, OP-4 |
| DEMO-04 | WP-0, WP-4, WP-5, WP-6, OP-3, OP-4 |
| DEMO-06 … DEMO-11, DEMO-12 (submission side) | team lead (`scripts/control_run.py`); DEMO-12 UI: WP-5 |
| DEMO-16 | WP-7 («О системе»), OP-8 |
| DEMO-17 | WP-11, OP-5 |
| SYS-01 | WP-11, team lead |
| SYS-03 | WP-6 |
| SYS-05, SYS-06 | working tree; WP-5 and WP-6 never send an empty id list |
