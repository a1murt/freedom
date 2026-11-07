# Design system prompt (Figma)

Paste into Figma Make, Claude Design, or a Claude session with the Figma MCP (`figma-generate-library` / `figma-use`). It derives a new system from the existing "ext" kit (file `GLRFuWwhmuXArmiCgHz9Wr`: pages Design Book, Components, Product Screens, QOSTANAI deck) and recolors it for Freedom Broker. Replace the file link if you duplicate the kit first.

---

You are building the design system for **CQA by Freedom**, a Conversation Quality Analytics console used by QA analysts and reviewers at Freedom Broker (a Kazakhstan brokerage). Source material: the Figma file https://www.figma.com/design/GLRFuWwhmuXArmiCgHz9Wr/ext (pages "Design Book", "Components", "Product Screens", "QOSTANAI"). Keep its character exactly: premium, minimal, dark-first, generous whitespace, soft glows, one accent color, rounded 8–12 px radii, macOS-grade polish. Change only what is listed below. Create a new page "CQA · Design System" and build everything there with Figma variables, styles and components, not loose frames.

**1. Color.** Replace the red accent family with Freedom green. Primary accent `#51AF3D`; deep green `#0F5431` for pressed states and section bands; soft tint `#DFF1DC` (light) and `#1B2F1D` (dark) for selected rows and chips. Keep the dark neutrals from the kit but shift them toward a green-black: background `#0F1411`, surface `#161D18`, surface-2 `#1E2721`, line `#2A352D`, text `#E8EDE7`, text-2 `#A3B0A6`. Add a light theme with the same tokens: background `#F7F8F6`, surface `#FFFFFF`, surface-2 `#EEF1EC`, line `#D5DCD3`, text `#16201A`, text-2 `#4B5A50`, accent `#2F8F2A` (darkened for contrast on white). Semantic colors, separate from the accent: success `#51AF3D`, warning `#E2A74A` (dark) / `#B7791F` (light), critical `#E2675F` (dark) / `#B3362F` (light), info `#5FA8D3`. Define every color as a variable in a "Theme" collection with Light and Dark modes.

**2. Typography.** Use **SF Pro** throughout: SF Pro Display for headings 24 px and above, SF Pro Text for body and UI, SF Mono for timestamps, scores, turn indices and JSON. Type scale: Display 40/44 semibold, H1 32/36 semibold, H2 24/28 semibold, H3 18/24 semibold, Body 15/22 regular, Body-strong 15/22 medium, Caption 13/18 regular, Label 12/16 medium with 0.04 em tracking uppercase, Mono 13/18. Numbers tabular. Create text styles for each.

**3. Mascot.** Reuse the octopus mascot set from "Components → Mascot" with its body recolored from red to the Freedom green `#51AF3D` (highlights `#8ED47F`, shadows `#2F8F2A`, eyes and outlines unchanged). Keep all poses. Export the following as components: Idle, Thinking (judging), Reviewing (magnifier), Alert (critical error), Done (check), Sleeping (empty state). Add a "mascot-mini" 24 px variant for the app header.

**4. Brand marks.** Build a "CQA" wordmark lockup in SF Pro Display semibold with a small green dot before the letters (mirroring the kit's logo lockup proportions). Provide app-icon squares 1024 px dark and light.

**5. Spacing and shape.** 4-pt grid; spacing tokens 4, 8, 12, 16, 24, 32, 48, 64. Radius tokens: 6 (chips, inputs), 10 (cards), 14 (modals), full (pills). Elevation: one subtle shadow for floating panels only; cards use a 1 px line, not a shadow. Keep the kit's soft radial glow as a decorative token used only on hero and empty states.

**6. Components (with variants and properties).** Build from the kit's existing Button, Switch, Checkbox, Settings Row, Nav Item, Keycap, HUD and Chip, recolored, then add:
- Button: primary (green fill), secondary (line), ghost, destructive; sizes S/M/L; states default/hover/pressed/disabled/loading.
- Chip: default, selected, evidence (mono text, link icon), score (shows "18/20"), status (success/warning/critical/neutral).
- Score Ring: 72 px and 120 px, arc in accent, number in SF Pro Display, caption below; variants for 0–59 (critical color), 60–84 (warning), 85–100 (success).
- Criterion Row: code, title, answer segment (Да/Нет/N/A or scale steps), points "x / max", weight, confidence bar, explanation text, evidence chips, appeal button; states default, N/A (dimmed with reason), under review, changed by reviewer (shows original struck through and final).
- Transcript Bubble: speaker variants client / operator / bot (dimmed) / system (centered mono caption); properties timestamp, turn index, highlighted (evidence), duplicate (collapsed).
- Audio Player: waveform band, play/pause, time, speed selector, evidence markers on the waveform.
- Table: header, row, sortable, selected, with a status pill column and a score column.
- Side Nav (collapsible) with items Conversations, Checklists, Review Queue, Analytics, Settings; Top Bar with role toggle (QA analyst / Reviewer) and provider badge ("Claude · ensemble", "Local · Qwen3").
- Progress Bar with label (batch evaluation), Toast, Modal, Empty State (with mascot), Diff Row (added/removed/changed), Tabs.

**7. Screens (desktop 1440 and tablet 1024).** Conversations list; Conversation Card (transcript left, verdicts right, tabs Result / History / Trace / Audio); Checklist Studio (criteria table, Advanced drawer, version diff, Apply dialog with cost estimate); Review Queue (list and decision panel with recalculated total); Analytics (distribution, criterion heatmap, operator leaderboard, agreement chart); Settings. Use real content from the project: a RU chat about finding the tariff in «Кабинет», a KZ chat about bank requisites, a RU call with a 7-minute hold. Show one N/A row, one critical-error banner, one reviewer change.

**8. Deck.** Duplicate the 10-slide QOSTANAI template, recolor to the green system, and retitle: 01 Title "CQA by Freedom", 02 Problem, 03 Roles (analyst, reviewer, QA lead), 04 Flow (import → judge → review → learn), 05 Measured (gold-set agreement, cost per evaluation), 06 AI modules (judge, ensemble, audio, claims), 07 Analytics, 08 Architecture and security (PII masking, local model, Dynamics 365 path), 09 Effect and adoption, 10 Team. Keep the chip rows, glows and layout rhythm of the template.

**9. Deliverables.** Variables (Theme collection with Light/Dark), text styles, effect styles, components with documented properties, the six screens, the deck, and a "Design Book" page that explains tokens and usage in one screen each. Name layers semantically (Button/Primary/M, Chip/Evidence). Export tokens as JSON (W3C design tokens format) so the web app can consume them.

---

Notes for the person running it: duplicate the kit before running so the original stays intact; SF Pro must be installed locally for Figma desktop to render it (it ships with macOS); if the tool cannot recolor vector fills on the mascot, select all mascot vectors and use Selection colors to replace the red (`#FF3B30`-family) with `#51AF3D` in one step.
