# Design system (final): corporate light

The visual system of the CQA web app and landing. It supersedes `docs/DESIGN_SYSTEM_PROMPT.md` (the older dark-first draft). Direction: the organizer's example mockup (`docs/design/organizer-mockup.png`) rendered with the Freedom-green ramp of the Figma kit (file `GLRFuWwhmuXArmiCgHz9Wr`), for an audience of directors, managers, QA specialists and operators (`docs/ORGANIZER_UPDATES.md` §4). Built in code by WP-0 (tokens, primitives, shell), WP-1 (quality components) and WP-2 (media components); see `docs/BUILD_BACKLOG.md`.

## 1. Direction in one paragraph

Light by default: a `#F5F7F5` canvas, white cards with a 1 px `#E3E8E3` border and 16 px radius, near-black `#16201A` headings, a deep-green `#0F5431` sidebar with a lighter `#2B7C36` active pill, Freedom green used sparingly and at AA contrast, peach `#FDEBD3` for the evidence fragment, amber for lost points, red only for gross violations and failures. The score ring «51,25 / 100», criteria rows with check and cross tiles and «earned / applied weight», «Почему снижена оценка» and the primary/secondary pair «Перейти к фрагменту» / «Оспорить оценку» are the signature pattern, reused across the card, the review queue, the operator portal and the landing hero. Dark theme stays available as a personal option (tokens only, no extra polish). No mascot, no glow, no game-like styling.

## 2. Implementation rules for tokens

- Tokens live in `web/src/index.css` as CSS custom properties on `:root` (light, default) and `.dark` (dark). **The existing variable names are kept and retuned** (`--bg`, `--surface`, `--surface-2`, `--line`, `--text`, `--text-2`, `--accent`, `--accent-hover`, `--accent-subtle`, `--selection`, `--accent-fg`, `--deep`, `--tint`, `--success`, `--warning`, `--critical`, `--info`, `--green-50…900`), so every untouched screen turns corporate-light at once. New variables are added next to them.
- Colour values are stored as space-separated RGB channels (`--surface: 255 255 255;`) so Tailwind opacity modifiers keep working; the hex is in a comment. Exceptions stored as full values: `--overlay`, `--nav-hover`, `--nav-divider`.
- `web/tailwind.config.ts` maps them. **`accent` now maps to `--accent-strong`** (AA-safe green for text, links and primary buttons), so existing `text-accent`, `bg-accent`, `border-accent` classes become AA without edits; the new `accent-fill` maps to `--accent` for rings, progress and charts.
- `web/index.html` ships without `class="dark"`; an inline pre-paint script adds `dark` only when `localStorage["cqa.theme"] === "dark"`. `theme-color` `#0F5431`; favicon `/favicon.svg` (white «CQA» monogram on a `#0F5431` rounded square).
- No hex values inside components. The only exception is the landing's two deep-green gradients, which reference ramp tokens.

## 3. Colour tokens

### 3.1 Neutrals, text, accent

| Token (CSS) | Tailwind | Light | Dark | Use | Contrast (light) |
|---|---|---|---|---|---|
| `--bg` | `bg` | `#F5F7F5` | `#0B1510` | page canvas | — |
| `--surface` | `surface` | `#FFFFFF` | `#111D16` | cards, dialogs, tables, inputs | — |
| `--surface-2` | `surface-2` | `#F0F3F0` | `#17261D` | table header, wells, hovered row, N/A tile | — |
| `--line` | `line` | `#E3E8E3` | `#22352A` | card borders, dividers (decorative) | — |
| `--line-strong` (new) | `line-strong` | `#8C978E` | `#6B7D71` | input, select, checkbox and switch borders | 3,0:1 on white (WCAG 1.4.11) |
| `--text` | `text` | `#16201A` | `#E6EEE8` | headings, body | 16,7:1 |
| `--text-2` | `text-2` | `#4B5A50` | `#A7B6AC` | secondary text, labels | 7,3:1 (6,8:1 on `--bg`) |
| `--text-3` (new) | `text-3` | `#687469` | `#8E9B90` | meta lines, timestamps, «/ 100» | 4,9:1 on white, 4,5:1 on `--bg`; never on `--surface-2` (4,4:1), use `--text-2` there |
| `--text-disabled` (new) | `text-disabled` | `#9AA59C` | `#5E6B61` | disabled controls only | not text |
| `--accent` | `accent-fill` | `#2F8F2A` | `#51AF3D` | graphics only: ring arc «Норма», progress, check icons, play button, chart series | 4,1:1 (graphics ≥ 3:1) |
| `--accent-strong` (new) | `accent` | `#1F7028` | `#68C155` | primary button fill, links, green text, earned points | 6,2:1; white on it 6,2:1 |
| `--accent-hover` | `accent-hover` | `#0F5431` | `#8ED47F` | primary hover and pressed | white on it 9,0:1 |
| `--accent-subtle` | `accent-subtle` | `#F0F8ED` | `#0F2A1A` | pass tile, selected row, secondary-button hover | `#1F7028` on it 5,7:1 |
| `--selection` / `--tint` | `selection` / `tint` | `#DFF1DC` | `#1B3A26` | text selection, tinted chips | `#1F7028` on it 5,2:1 |
| `--accent-fg` | `accent-fg` | `#FFFFFF` | `#0B1510` | text on the primary fill | — |
| `--deep` | `deep` | `#0F5431` | `#0F5431` | deep-green bands (landing), track «Клиент» | white on it 9,0:1 |
| `--focus` (new) | `focus` | `#1F7028` | `#8ED47F` | 2 px focus outline, 2 px offset | ≥ 3:1 everywhere |
| `--overlay` (new) | — | `rgba(10,20,14,.40)` | `rgba(0,0,0,.60)` | dialog and drawer backdrop, no blur | — |

### 3.2 Sidebar

| Token | Light | Dark | Use |
|---|---|---|---|
| `--nav-bg` | `#0F5431` | `#07130D` | sidebar background (mockup) |
| `--nav-fg` | `#FFFFFF` | `#FFFFFF` | labels at 78 % opacity (6,2:1), active label at 100 % 600 |
| `--nav-active` | `#2B7C36` | `#1F7028` | active item pill (white 600 on it: 5,2:1) |
| `--nav-hover` | `rgba(255,255,255,.08)` | same | item hover |
| `--nav-divider` | `rgba(255,255,255,.12)` | same | divider between groups |
| badge | white fill, `#0F5431` digits | same | counts on nav items (9,0:1) |
| focus | 2 px white outline | same | focus inside the sidebar |

### 3.3 Status, evidence, audio, ring

| Group | fg (text) | mark (icons, arcs) | bg | line | Dark fg / mark / bg / line | Reserved for |
|---|---|---|---|---|---|---|
| `success` | `#1F7028` | `#2F8F2A` | `#F0F8ED` | `#BEE3B5` | `#8ED47F` / `#51AF3D` / `#12301D` / `#2F5A3A` | earned points, «Подтверждено», «Файл валиден» |
| `warning` | `#A16207` (4,9:1) | `#C27A0E` (3,5:1) | `#FDF3E1` | `#F2D199` | `#E2A74A` / `#D9922E` / `#33270F` / `#6B4E1F` | lost points, «На рассмотрении», low confidence, Σ ≠ 100 |
| `critical` | `#B3362F` (6,0:1) | `#B3362F` | `#FDECEA` | `#F3C1BC` | `#F08A82` / `#E2675F` / `#3A1715` / `#6E2A26` | gross violations, failed evaluations, destructive actions; nothing else is red |
| `info` | `#1F5FA8` (6,4:1) | `#2A78D6` | `#EAF2FB` | `#BCD3EE` | `#7DB8E8` / `#4A90E8` / `#10263A` / `#24456A` | «Изменено ревьюером», provider and local-model notes |
| `neutral` | `#4B5A50` | `#9AA59C` | `#F0F3F0` | `#E3E8E3` | `#A7B6AC` / `#5E6B61` / `#17261D` / `#22352A` | N/A, unknown, «ИИ · не подтверждено» |
| `evidence` | text stays `--text` | `#F2B66D` (edge) | `#FDEBD3`; active `#FBDDB4` | `#F2B66D` | `#3A2A14` bg, `#B07A2E` line, `#4A3518` active | evidence turn, waveform fragment, review fragment, landing quote |
| `track` | — | client `#0F5431`, operator `#51AF3D`, other `#C9D1C9` | — | — | client `#8ED47F`, operator `#51AF3D`, other `#3B5244` | waveform lanes «Клиент» / «Оператор» / IVR and consultation |
| `ring-track` | — | `#E8EDE8` | — | — | `#22352A` | score ring background circle |

CSS names: `--success`, `--success-mark`, `--success-bg`, `--success-line`, and the same for `warning`, `critical`, `info`, `neutral`; `--evidence-bg`, `--evidence-line`, `--evidence-active`; `--track-client`, `--track-operator`, `--track-other`; `--ring-track`. Tailwind: `success`, `success-mark`, `success-bg`, `success-line`, … `evidence-bg`, `evidence-line`, `track-client`, `ring-track`. The peach pair `#FDEBD3` / `#F2B66D` supersedes `#FFEFDB` / `#F3D9AE` (CARD-04) and `#E8B867` (CALL-02).

### 3.4 Score bands (defined once)

| Band | Range | Label RU / EN / KK | Ring arc | Pill |
|---|---|---|---|---|
| `norm` | total ≥ 85 | Норма / Normal / Қалыпты | `--accent` | success tone |
| `attention` | 60 ≤ total < 85 | Внимание / Attention / Назар | `--warning-mark` | warning tone |
| `critical` | total < 60 | Критично / Critical / Сыни | `--critical` | critical tone |
| none | null (not evaluated or failed) | Итог не рассчитан | dashed `--ring-track`, «—» | neutral |

One module, `web/src/lib/bands.ts` (WP-0): `bandOf(total)`, `bandTone(band)`, `BAND_LABEL_KEYS`. The ring, score pills, the overview band chips, the distribution chart and the landing hero all use it. Legend text everywhere: «Норма ≥ 85 · Внимание 60–85 · Критично < 60». The ring number stays ink; only the arc carries the band colour (mockup). Checklist-defined bands (CHK-28) are cut.

### 3.5 Charts

Single series `--accent`. Two series (AI ↔ after appeal): A `#2A78D6`, B `#C0398A` (dark `#4A90E8` / `#D9589A`). Sequential (heatmap low → high): `#68C155 → #2F8F2A → #1F7028 → #0F5431 → #092818` (dark reversed from `#1F5A22` to `#BEE3B5`). Grid `#E3E8E3`, axis text `--text-3`. Values in text tokens, never in the series colour; a legend for two or more series; n shown per cell; cells with n < 5 hatched «мало данных».

### 3.6 Token guard (enforced in review)

- `#51AF3D` (green/500) is 2,8:1 on white: graphics only (operator track, landing gradients), never text, never a fill under white text.
- Green text is `#1F7028` (`text-accent`); text on a `#51AF3D` fill is ink.
- Status is never colour alone: every Да / Нет / N/A, band, review state and violation carries an icon and a word (success and warning are nearly indistinguishable for protan readers).
- Red is reserved for gross violations, failures and destructive actions.

## 4. Mapping to the organizer mockup

| Mockup element | Token / component | Spec |
|---|---|---|
| Deep-green sidebar, «CQA» wordmark, white items, lighter active pill | `--nav-bg`, `--nav-active`, `SideNav` (WP-0) | 240 / 72 px, item 40 px radius 10, label 15/500 white 78 %, active white 600 |
| Title «Анализ разговора» + grey meta «Звонок 024 · Русский · 06:33» | `PageHeader` (WP-0) | title `h1` 32/38 700, meta `small` `--text-3`, separator « · » |
| Waveform card: lanes «Клиент» (deep) / «Оператор» (bright), peach fragment with tan edges, round play, «02:18 / 06:33», green progress | `WaveformBars` + `PlayerBar` (WP-2) | lanes 40 px, bars 2 px / gap 2 px; `--track-client`, `--track-operator`; region `--evidence-bg` with 1 px `--evidence-line` edges; play 40 px `--accent` fill; progress 4 px with a 12 px knob |
| «Расшифровка»: time grey, speaker green bold, text below, divided list, evidence row in peach | `TranscriptRow` (WP-2) | time `small` `--text-3`, speaker `body-strong` (Клиент `--deep`, Оператор `--accent-strong`), «№n» right, text `body`; evidence: `--evidence-bg`, 1 px `--evidence-line`, radius 10 |
| «Оценка качества» ring «85,5 / 100» | `ScoreRing` (WP-0) | lg 144 (compact 120), 10 px stroke, band arc, value `score-xl` ink, «/ 100» `--text-3` |
| Criteria rows: green check tile / amber cross tile, title, «15 / 20» with earned coloured and max grey | `IconTile` (WP-0), `CriterionRowCompact` (WP-1) | tile 28 px radius 6; earned `--success` when > 0, `--warning` when 0; max `--text-3` |
| «Почему снижена оценка» + one line of reason | `ReasonSummary` (WP-1) | `h3` 17/24; lines «Выявление потребности −18,75 · <first sentence>» |
| «Перейти к фрагменту» (filled) / «Оспорить оценку» (outlined) | `Button` primary / secondary lg (WP-0) | 48 px, radius 10, 15/600; primary `--accent-strong`; secondary white with 1 px `--accent-strong` border and text |
| White cards with soft border | `Card` (WP-0) | radius 16, 1 px `--line`, padding 24 (20 compact), shadow `0 1px 2px rgba(16,32,24,.04)` |

## 5. Mapping to the Figma kit

| Figma (file `GLRFuWwhmuXArmiCgHz9Wr`) | CQA token |
|---|---|
| green/50 `#F0F8ED` | `--green-50`, `--accent-subtle`, `--success-bg` |
| green/100 `#DFF1DC` | `--green-100`, `--selection`, `--tint` |
| green/200 `#BEE3B5` | `--green-200`, `--success-line` |
| green/300 `#8ED47F` | `--green-300`, dark `--accent-hover`, dark `--focus` |
| green/400 `#68C155` | `--green-400`, dark `--accent-strong` |
| green/500 `#51AF3D` | `--green-500`, `--track-operator`, dark `--accent` (graphics only in light) |
| green/600 `#2F8F2A` | `--green-600`, light `--accent` (graphics) |
| green/700 `#1F7028` | `--green-700`, `--accent-strong`, `--success`, `--focus` |
| green/800 `#0F5431` | `--green-800`, `--nav-bg`, `--deep`, `--accent-hover`, `--track-client` |
| green/900 `#092818` | `--green-900`, footer band, dark gradient stop |
| Design Book bg/canvas · bg/elevated · bg/subtle · bg/inverse | `--bg` · `--surface` · `--surface-2` · `--nav-bg` |
| text/primary · text/secondary · text/accent | `--text` · `--text-2` · `--accent-strong` |
| border/default · border/focus | `--line` · `--focus` |
| SF Pro Display / Text type styles | §6 scale (system stack, no bundled font) |
| Landing frame 91:237: floating nav, hero with product window and «works with» chips, numbered chapters, steps, feature chapters, languages, «It keeps quiet», compare table, FAQ, gradient CTA, footer | `docs/LANDING.md` structure, rendered light with two deep-green bands |
| Components → Mascot (green octopus) | not used by default; gated by «Фирменный персонаж» (§11) |

## 6. Typography

- Sans stack: `-apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Segoe UI Variable Text", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`. Display headings put `"SF Pro Display"` first. Mono: `"SF Mono", ui-monospace, Menlo, Consolas, monospace`.
- No font files are bundled (self-hosted Inter is cut): the presenting Mac renders SF Pro, Windows and Dynamics 365 render Segoe UI, both cover the Kazakh letters ә ғ қ ң ө ұ ү һ і, and the single-file build stays small.

| Token | Size / line | Weight | Tracking | Use |
|---|---|---|---|---|
| `display` | 56/60 (40/44 ≤ 1024 px, 32/38 ≤ 640 px) | 700 | −0,02em | landing H1 |
| `display-sm` | 40/44 (28/34 ≤ 640 px) | 700 | −0,02em | landing section titles |
| `h1` | 32/38 (28/34 below 1280 px or compact) | 700 | −0,02em | page titles («Анализ разговора») |
| `h2` | 22/28 | 700 | −0,01em | card titles («Расшифровка», «Оценка качества») |
| `h3` | 17/24 | 600 | 0 | sub-blocks («Почему снижена оценка») |
| `body` | 15/22 | 400 | 0 | text, transcript |
| `body-strong` | 15/22 | 600 | 0 | criterion names, speaker labels, points |
| `small` | 13/18 | 400 | 0 | meta, timestamps, hints |
| `label` | 12/16 | 500 | 0,01em | table headers and field labels, sentence case (uppercase labels removed) |
| `score-xl` | 40/44 (32/36 compact) | 700 | −0,02em | ring value «51,25» |
| `kpi` | 32/36 | 700 | −0,02em | KPI values, landing counters |
| `mono` | 12/16 | 500 | 0 | ids, JSON, request ids only |

Numbers are tabular everywhere and formatted through `web/src/lib/format.ts` (`Intl.NumberFormat`): decimal comma in RU and KK, dot in EN; totals «51,25», «85,38», «100»; points trimmed «7,5 / 18,75»; money «$0,22»; durations «10:20»; dates «07.11, 21:44» in local time. Exports (CSV, results.json) keep the dot. Monospace is never used for scores or KPIs.

## 7. Space, shape, elevation, motion, icons

- Spacing (4-pt): 4, 8, 12, 16, 20, 24, 32, 48, 64, 96. Card padding 24 (20 compact); gap between cards 24 (16 below 1280 px); page padding 32 (24 below 1280 px).
- Radius: 6 chips and icon tiles · 10 inputs, buttons, transcript highlight · 16 cards, dialogs, drawers · 24 landing CTA panel and comparison card · full for pills, the play button and the role circle.
- Elevation: cards = 1 px border + `0 1px 2px rgba(16,32,24,.04)`; popovers and drawers `0 12px 32px -12px rgba(16,32,24,.24)`. Removed: `.glow`, `shadow-glow`, `shadow-float` at 45 % black, the neon `.turn-highlight`.
- Motion: 150–200 ms ease-out on hover, press and expand; the evidence highlight fades in over 200 ms; scroll to a turn is smooth; the ring arc animates 600 ms on first render; `prefers-reduced-motion` makes all of it instant.
- Focus: 2 px `--focus` outline, 2 px offset (white inside the sidebar); never removed.
- Icons: lucide line icons, 1,75 stroke; 18 px in content, 20 px in the sidebar. Fixed criterion icons: `Check` (pass), `X` (fail), `Minus` (N/A), `ShieldAlert` (gross violation), `ShieldCheck` (no gross violation), `AlertTriangle` (error), `Loader2` (pending). Icon-only buttons carry an `aria-label`.

## 8. Density

| Mode | Trigger | Changes |
|---|---|---|
| Comfortable | default | table rows 52 px, criterion rows 44 px, ring lg 144, card padding 24, `h1` 32/38 |
| Compact | `@media (max-height: 800px)`, automatic | table rows 44 px, criterion rows 36 px, ring lg 120, `score-xl` 32/36, card padding 20, `h1` 28/34, section headers of the quality card become 20 px dividers with the subtotal |
| Dynamics 365 host (P1) | `<html data-host="d365">` (embed with `host=d365` or `CQA_CONFIG.host`) | Segoe UI first, 14 px base, radii 8, card padding 16, one column below 768 px of iframe width |

Implemented with CSS variables set in `index.css` (`--row-h`, `--crit-row-h`, `--ring-lg`, `--card-pad`, `--page-pad`, `--h1-size`) that components read (`h-[var(--crit-row-h)]`). Presentation mode is cut.

Projector acceptance: in a 1280×720 full-screen viewport the p1-ru-029 card shows the page header, the gross-violation strip, the ring, all 8 criteria rows, «Почему снижена оценка» and both buttons without page scroll; in a windowed 1280×650 viewport the criteria list scrolls inside the card and the sticky footer (reasons + buttons) never leaves the screen.

## 9. Component inventory

Owner: WP-0 = `web/src/components/ui/**`, `web/src/components/ds/*.tsx`, `web/src/components/layout/**`, `web/src/components/common.tsx`, `web/src/components/Mascot.tsx`; WP-1 = `web/src/components/ds/quality/**`; WP-2 = `web/src/components/ds/media/**`. Feature components stay in their feature folders. `components/common.tsx` keeps its current exports as thin wrappers over the DS so untouched screens compile.

| Component | File | Props | Variants and states | Owner |
|---|---|---|---|---|
| `Button` | `ui/button.tsx` | `variant`, `size`, `loading`, `icon`, `disabledReason`, `asChild` | primary (accent-strong fill, white) · secondary (white, 1 px accent-strong border and text) · ghost · danger · link · inverse (white fill, deep-green text, on green bands); sizes sm 32 · md 40 · lg 48 · icon; hover, pressed, focus, disabled with tooltip from `disabledReason`, loading | WP-0 |
| `Card` | `ds/Card.tsx` | `title`, `subtitle`, `actions`, `tabs`, `footer`, `stickyFooter`, `padding: md \| lg`, `tone: default \| critical \| muted`, `bodyScroll` | header without divider (mockup); body may scroll inside with a sticky footer | WP-0 |
| `PageHeader` | `ds/PageHeader.tsx` | `back?: {to, label}`, `title`, `meta: ReactNode[]`, `status?: ReactNode`, `actions?` | with or without back link; meta joined by « · » | WP-0 |
| `Chip` + presets | `ds/Chip.tsx` | `tone: success \| warning \| critical \| info \| neutral \| brand \| outline`, `size: sm 22 \| md 26`, `icon`, `count` | presets `LanguageChip` (RU/KZ/EN), `ChannelChip` (Чат / Звонок with icon), `ReviewStateChip` (ИИ · не подтверждено / На рассмотрении / Подтверждено / Изменено ревьюером), `ViolationTypeChip` (Оскорбление клиента / Платная услуга в обход компании / Раскрытие конфиденциальной информации / Иное), `ProviderChip`, `VersionChip` («Чек-лист v1»), `BandChip` | WP-0 |
| `IconTile` | `ds/IconTile.tsx` | `kind: pass \| fail \| na \| critical \| clean \| error \| pending \| info`, `size: 24 \| 28 \| 32 \| 48` | pass: green check on `--success-bg`; fail: amber cross on `--warning-bg`; na: grey dash; critical: red shield on `--critical-bg` | WP-0 |
| `ScoreRing` | `ds/ScoreRing.tsx` | `value`, `original?`, `size: sm 56 \| md 96 \| lg 144`, `critical?`, `caption?` («/ 100»), `onClick?` | band arc from `lib/bands.ts`; original struck through + «после апелляции»; null → dashed track and «—» «Итог не рассчитан»; red shield badge at 4 o'clock when critical; `aria-label` «Итоговая оценка 51,25 из 100, есть грубое нарушение» | WP-0 |
| `ScorePill` | `ds/ScorePill.tsx` | `value`, `original?`, `critical?` | band tone; shield + «грубое» when critical; tooltip «исходный: 78,46» | WP-0 |
| `KpiTile` | `ds/KpiTile.tsx` | `label`, `value`, `unit?`, `sub?`, `n?`, `definition?`, `to?`, `state: ready \| loading \| empty \| error`, `smallSample?` | static or drillable (whole tile is a link); «—» for no data, never 0; ⓘ popover with the definition | WP-0 |
| `EmptyState` | `ds/EmptyState.tsx` | `icon`, `title`, `hint?`, `steps?`, `primary?`, `secondary?`, `variant: first-run \| filtered \| no-access \| offline \| not-found` | 48 px icon tile (green/50 fill, green/700 icon); mascot only through `BrandMascot` in `first-run` | WP-0 |
| `InlineAlert` / `ErrorState` | `ds/InlineAlert.tsx`, `ds/ErrorState.tsx` | `tone`, `title`, `message`, `action?`, `requestId?` · `error`, `onRetry` | info, warning, critical, success, offline; error shows the code and the request id with «Копировать» | WP-0 |
| `Field` | `ds/Field.tsx` | `label`, `hint?`, `error?`, `required?`, `children` | error text with icon under the control | WP-0 |
| `SegmentedControl` | `ds/SegmentedControl.tsx` | `options: {value, label, sublabel?, disabled?, reason?}[]`, `value`, `onChange`, `size` | radiogroup semantics; used for answers («Да / Нет / N/A», scale steps) | WP-0 |
| `NumberStepper` | `ds/NumberStepper.tsx` | `value`, `min`, `max`, `step`, `suffix?`, `onChange`, `disabled?` | − / + buttons, typed input, clamps | WP-0 |
| `Drawer` | `ui/drawer.tsx` | `open`, `onOpenChange`, `side: right`, `width: 400 \| 480 \| 560`, `title`, `footer?` | radius 16 on the open side, `--overlay` backdrop | WP-0 |
| `Dialog` | `ui/dialog.tsx` | `size: sm 480 \| md 560 \| lg 720` | radius 16, `--overlay`, no blur | WP-0 |
| `Tabs` | `ui/tabs.tsx` | `variant: underline \| segmented` | underline: 2 px `--accent-strong` indicator, 15/22 500 | WP-0 |
| Table styles | `ui/table.tsx` | `density`, `stickyHeader`, `onRowClick` | header 40 px on `--surface-2`, rows `var(--row-h)`, numbers right-aligned, hover row | WP-0 |
| Inputs | `ui/input.tsx`, `select.tsx`, `switch.tsx`, `checkbox.tsx` | standard + `error` | 40 px, `--line-strong` border, radius 10, focus ring | WP-0 |
| Toaster | `App.tsx` + `lib/toast.ts` | `notify`, `notifyError(err)` | white card, radius 12, 3 px left status rule; errors stay until dismissed and show code + request id + «Копировать» | WP-0 |
| `BrandMascot` | `components/Mascot.tsx` | `pose`, `size` | renders `null` unless `mascot_enabled`; poses lazy-imported | WP-0 |
| Shell | `layout/AppShell.tsx`, `SideNav.tsx`, `RolePill.tsx`, `RoleMenu.tsx`, `IdentityPicker.tsx`, `LanguageSwitch.tsx`, `ConnectionDot.tsx`, `ReadOnlyNotice.tsx` | role store, nav registry, shell slots | sidebar expanded 240 / collapsed 72 | WP-0 |
| `CriterionRowCompact` | `ds/quality/CriterionRowCompact.tsx` | `code`, `title`, `iconKind`, `earned`, `max`, `naReason?`, `reviewState?`, `decision?`, `selected`, `onSelect` | «7,5 / 18,75»; N/A «N/A · вес перераспределён»; C8 «Грубое нарушение» / «Нет»; selected: `--accent-subtle` fill + 3 px left bar; chips «На рассмотрении», «Изменено» | WP-1 |
| `SectionHeader` | `ds/quality/SectionHeader.tsx` | `title`, `earned`, `max`, `flag?` | «Начало и понимание запроса · 12,5 / 31,25»; compact: 20 px divider | WP-1 |
| `ReasonSummary` | `ds/quality/ReasonSummary.tsx` | `lost: {code, title, lost, reason}[]`, `na: string[]`, `maxLines`, `onPick` | sorted by loss; «Оценка не снижалась»; N/A line; «ещё N» | WP-1 |
| `EvidenceChip` | `ds/quality/EvidenceChip.tsx` | `turnIdx`, `speaker`, `time?`, `quote`, `role?: supports \| contradicts \| context`, `inRecording?`, `onClick` | «№10 · Оператор · 01:23 · «quote»»; role in words in tooltip and `aria-label`; «не в записи» disables seek | WP-1 |
| `GrossViolationBlock` | `ds/quality/GrossViolationBlock.tsx` | `types`, `quote?`, `turnIdx?`, `time?`, `state: ai \| confirmed \| overturned`, `collapsed?` | 40 px `--critical-bg` strip; expanded quote + «Итог не обнуляется: по чек-листу грубая ошибка отмечается отдельно»; overturned «Отметка снята решением ревьюера» | WP-1 |
| `QualityScoreCard` | `ds/quality/QualityScoreCard.tsx` | `evaluation`, `variant: full \| compact`, `maxRows?`, `selectedCode?`, `onSelect?`, `onPrimary?`, `onSecondary?`, `onOpen?` | full = the card's panel; compact = landing hero (ring, 3 rows, reasons, buttons as links) | WP-1 |
| `DecisionNote` | `ds/quality/DecisionNote.tsx` | `from`, `to`, `pointsFrom?`, `pointsTo?`, `reviewer`, `at`, `reason` | «4 → 7 · 9,23 → 16,15 · Изменено ревьюером · Айгерим С. · 07.11, 21:44» | WP-1 |
| `WaveformBars` | `ds/media/WaveformBars.tsx` | `tracks: {client, operator, other?}`, `duration`, `regions: {from, to, tone}[]`, `playhead`, `onSeek?`, `static?` | 2 lanes + thin third; fallback lanes from turn spans labelled «по расшифровке» | WP-2 |
| `PlayerBar` | `ds/media/PlayerBar.tsx` | `playing`, `current`, `duration`, `speed`, `onToggle`, `onSeek`, `onSpeed` | 40 px round play, «02:18 / 06:33», progress with 12 px knob, speed 0,75–2× | WP-2 |
| `TranscriptRow` | `ds/media/TranscriptRow.tsx` | `turnIdx`, `time`, `speaker`, `text`, `state: default \| evidence \| active \| span \| playing`, `dimmed?`, `notInRecording?`, `onClick?` | divided list row; bot/system dimmed; system events as centred 13 px lines | WP-2 |

## 10. Key component specs

- **Sidebar and role pill**: `docs/ROLES.md` §3.1 and `docs/IA.md` §3.
- **Quality panel** (WP-1): ring column (`ScoreRing lg`, «Как посчитан итог →» link under it) and criteria column (rows grouped by S1–S4 section headers with subtotals); above them the gross strip when C8 = Да; below them the **sticky footer**: `ReasonSummary` (3 lines + «ещё N», N/A line) and the two 48 px buttons side by side (stacked on narrow cards, primary first). The panel is `flex h-full min-h-0 flex-col`, the middle scrolls, the footer is `position: sticky; bottom: 0` with a top border, so it works both inside a fixed-height card and inside a scrolling page.
- **«Как посчитан итог»** (WP-1): dialog 720 px, table criterion · base weight · applicable · answer · share earned · applied weight · points; footer «Применимый вес 80 из 100 → коэффициент 1,25 · сумма 51,25 · округление до 2 знаков, половина вверх»; C8 «Без баллов — отмечается отдельно»; after a decision two columns «Исходно / После решения». P1: «Пересчитать» calls `POST /api/appeals/preview` with each criterion's current final answer and shows «Пересчитано движком: 51,25 — совпадает».
- **Transcript** (WP-2): the mockup's divided list. Pre-operator segment folded into «До оператора: бот и системные события · 9 реплик», unfolded automatically when an evidence points inside; system events as small grey lines; evidence turn peach; checked span with a 3 px amber left rule; duplicates collapsed «дубликат · показать»; search behind an icon; turns before the recording start labelled «не в записи».
- **Waveform card** (WP-2): above the two cards on calls, full width; see §4.
- **Buttons in the mockup**: «Перейти к фрагменту» primary lg, «Оспорить оценку» secondary lg; a disabled button always explains itself («Апелляция уже на рассмотрении», «Оператор может оспаривать только оценки своих диалогов», «Доступно роли Ревьюер»).
- **Toasts**: errors read «<message> · <code> · запрос <id> [Копировать]».

## 11. Usage rules

1. **No mascot by default.** `BrandMascot` renders nothing unless the organisation setting `mascot_enabled` is true. Only Администратор changes it, in Settings → Оформление → «Фирменный персонаж» (default off, hint «Только в пустых состояниях; не используется на лендинге, в карточке, в отчётах и во встраивании»). When on, poses appear only in first-run empty states, never in the gross-violation strip, score areas, tables, dialogs, errors, the landing, print or the embed. If settings fail to load, it stays off. The assets stay in `web/public/mascot/` and `web/src/assets/mascot/` and leave the main bundle (lazy import).
2. Red only for gross violations, failures and destructive actions; amber for lost points and «На рассмотрении»; green for earned points, primary actions and brand.
3. Icon plus word for every status; never colour alone.
4. Turn numbers are 1-based everywhere («№10»), through `fmtTurn(idx)`. Explanation texts are rendered exactly as the API returns them (already 1-based); the UI never renumbers text. Raw Trace JSON keeps the judge's 0-based labels and is marked «сырые данные модели».
5. Sentence-case labels, Russian quotes «…», locale decimal separator, «$0,22». Verbs on buttons («Оспорить оценку», «Сохранить решение»). No exclamation marks.
6. Every empty, loading and error state names the next step; 0 is never shown for «no data»; skeletons keep the final size.
7. Monospace only for ids, JSON and request ids.
8. Claims shown in the UI are verifiable: costs are «≈ $0,22 · среднее по 75 оценкам», never «все вызовы учтены»; the local model is «поддерживается, на стенде не подключена» unless `/api/health` reports it; no accuracy percentage until the ML owner publishes a gold-set comparison.

## 12. Accessibility

- WCAG AA for text (contrast column in §3); ≥ 3:1 for meaningful graphics and control borders.
- Focus is always visible; dialogs and drawers trap and return focus.
- The ring, chips and the player slider carry labels; recalculated totals and job progress use `aria-live="polite"`.
- 200 % zoom loses nothing; minimum text 12 px; pinch zoom never disabled; at 1164 px (1280 px at 110 % zoom) no horizontal scroll.
- `prefers-reduced-motion` respected.

## 13. Removed

Dark default, `class="dark"` in `index.html`, the mascot in 7 files, `.glow`, `shadow-glow`, `shadow-float`, the neon `.turn-highlight`, uppercase `.label`, `font-mono` on scores and KPIs, the top bar with the two-role toggle, the mascot favicon. Not added (cut): self-hosted Inter, a `#/__ds` gallery, axe/Playwright in CI, presentation mode, chart-palette validation tooling, dark-theme polish beyond the tokens.
