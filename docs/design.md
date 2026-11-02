# Design system: "Assay"

This file describes the frontend's visual design: direction, tokens, type, layout and component rules. The tokens are implemented in `frontend/src/app/globals.css` (CSS variables exposed to Tailwind through `@theme inline`); the components are in `frontend/src/components`.

## Direction

**What it is for.** Owners and managers of small and mid-sized businesses, in English and Arabic, upload their sales data. Before they act on the data they need to trust it. The first screens answer three questions:

- is this data fit to analyse?
- what is wrong with it?
- what does fixing it change?

**What it should feel like.** A laboratory assay report: precise, calm and measured, with evidence first and no decoration. Petrol ink carries the structure, and a saffron accent marks the "reading".

**The one bold element** is the **data-health dial**. It is a calibrated 270° gauge:

- a tick every 10 points;
- a saffron needle that settles once, after the page loads;
- the score in large tabular numerals;
- after cleaning, a dashed mark showing the previous version's score.

Everything around the dial stays quiet: tonal surfaces, 1px borders and one shadow.

Two directions were proposed. **A "Assay"** (this one) was chosen over **B "Atlas"** (a cartographic look: lapis and malachite, contour lines). A won for two reasons:

- One type family covers both scripts, so mixed English/Arabic tables keep matching numerals and x-height.
- A measuring instrument fits a data-health page better than a map does.

## Colour tokens

All text colours meet WCAG AA (at least 4.5:1) on `background`, `card` and `muted` in their theme. The table gives each colour's contrast against `background` in its own theme. Borders and input outlines meet 3:1 (WCAG 1.4.11). `signal-fill` is decorative only (needle, highlights) and never carries text.

| Token | Light | Dark | Use | Contrast (light / dark) |
| --- | --- | --- | --- | --- |
| `background` | #F5F7F6 | #0D1517 | Page | |
| `card` | #FFFFFF | #152124 | Panels, tables | |
| `muted` / `secondary` | #EBF0EE | #1C2B2F | Table heads, quiet fills | |
| `accent` | #E3EAE8 | #23353A | Hover fill | |
| `border` | #D3DCDA | #2C3D41 | Dividers | |
| `input` | #7C8C89 | #647A7E | Control outlines | 3.3 / 4.1 |
| `foreground` | #16201F | #E4ECEA | Text | 15.5 / 15.4 |
| `muted-foreground` | #4B5A58 | #9DB0AD | Secondary text | 6.7 / 8.1 |
| `primary` | #0B5C63 | #5CC2BE | Actions, links, the active nav item | 7.2 / 8.7 |
| `primary-foreground` | #FFFFFF | #0D1517 | Text on primary | 7.7 / 8.7 |
| `signal` (= `ring`) | #8A5A00 | #E8B547 | Focus ring, signal text | 5.5 / 9.8 |
| `signal-fill` | #F2C14E | #E8B547 | Needle (decorative) | |
| `good` / `good-soft` | #1B6E45 / #E2F1E8 | #5BC98E / #15302A | Ready, low risk | 5.4 / 6.9 on its own soft fill |
| `warn` / `warn-soft` | #8F4E00 / #FBEEDB | #F0A040 / #33260F | Medium risk, skipped | 5.6 / 6.9 on its own soft fill |
| `bad` / `bad-soft` | #B42318 / #FBE5E2 | #FF8A7A / #3A1A17 | High risk, failed, errors | 5.5 / 6.8 on its own soft fill |
| `info-soft` | #E0EEF0 | #14312F | Processing chips, empty-state icon | primary on it: 6.5 / 6.6 |

Contrast was measured with a WCAG relative-luminance script. axe (`color-contrast`) and Lighthouse confirm it on every page in both themes.

## Type

- **Families:** IBM Plex Sans (Latin) and IBM Plex Sans Arabic. They are one designed family with matched metrics and real tabular figures. They are self-hosted through `next/font`, so the browser makes no font requests at runtime. Arabic pages put Plex Sans Arabic first in the font stack.
- **Scale** (ratio 1.2, from a 15px body):

  | Step | Size |
  | --- | --- |
  | `xs` | 12.5 |
  | `sm` | 14 |
  | `base` | 15 |
  | `lg` | 18 |
  | `xl` | 21.6 |
  | `2xl` | 26 |
  | `3xl` | 31 |
  | `4xl` | 37 (the dial's score) |

- **Weights:** 400, 500 and 600 only.
- **Spacing:** headings use −0.01em letter-spacing in Latin and none in Arabic. Arabic body line-height is 1.65 (Latin is 1.55).
- **Numbers:** quantities, scores and counts use `tabular-nums` and are aligned to the end edge of table columns. Arabic uses Latin digits (`ar-u-nu-latn`) so the numbers match the uploaded data.

## Space, radius, elevation

- **Spacing:** a 4px grid. Pages use 24/40px side padding (phone/desktop), sections are 40px apart, and the content is at most 1200px wide.
- **Radius:** 4px on controls, 8px on panels. Only badges, chips and the avatar are fully round.
- **Elevation:** tonal surfaces plus 1px borders. One shadow (`--shadow-pop`) is used, for popovers, menus and dialogs only.

## Layout and RTL

- The sidebar sits on the start side and flips to the right in Arabic. On phones it becomes a sheet that slides in from the start.
- The header shows the company name, the language switcher, the theme menu and the account menu.
- **Use only logical CSS** (`ms-`/`me-`/`ps-`/`pe-`/`start-`/`end-`, `text-start`, `border-s`). An ESLint rule rejects physical classes (`ml-`, `pl-`, `left-`, `text-left`, ...).
- **Directional icons** (chevrons, arrows, sign-out) are mirrored with `rtl:-scale-x-100`.
- **The dial is not mirrored.** It is a measuring instrument, so scores rise clockwise in both directions.
- **Mixed-direction text:** dataset values and column names render with `dir="auto"` or `<bdi>`, so mixed Arabic and English values keep their own direction. Backend text (issues, fix descriptions, skip reasons) is English for now. It is wrapped in `lang="en" dir="ltr"` spans and labelled "Shown as reported by the analysis engine".

## Components

- **Status chip** (processing, checking health, ready, failed): an icon plus a word, never colour alone. The spinner stops under reduced motion.
- **Risk badge** (low, medium, high): a shield icon plus a word, in good/warn/bad soft fills.
- **Quality dial:** `role="meter"` with `aria-valuetext` ("Quality score 88 out of 100. Ready for analysis"). Score bands: 85 and above is "Ready for analysis", 60–84 is "Usable, with caveats", below 60 is "Needs cleaning first". Treat the score as relative between versions (see known-limits).
- **Breakdown:** five short gauges. Each one shows "x of max", so the bar is not the only carrier of the value.
- **Cleaning plan:**
  - Fixes are grouped by risk, and each group says what that risk level means.
  - **Only low-risk fixes the backend recommends are pre-selected.** Medium and high-risk fixes are never pre-selected.
  - Null handling shows its strategies, defaulting to "Keep them empty", and the risk of each option.
  - Ambiguous dates show day-first versus month-first with a worked example (03/04).
  - "Apply fixes" creates a new version, and the dial then shows how the score changed.
- **Columns table:** each column has a role dropdown, saved on change. After a change, a banner offers "Run analysis again", because role changes never re-run the analysis automatically. Personal-data columns carry a "Personal data" badge with an explanation.
- **Analysis progress:** one row per module, in run order (waiting, running, done, skipped, failed). Skipped and failed rows show the backend's plain-language reason.
- **Empty states:** say what the area is, why it is empty, and give one action. Sections that later steps will build (Dashboard, Insights, Ask the analyst, Reports) say so honestly and link back to Datasets.
- **Errors:** every backend error `code` maps to a translated message that says what happened and what to do next. Errors don't apologise. Form errors are linked to their fields with `aria-describedby`/`aria-invalid`.
- **Motion:** two moments only, the needle settling and a spinner while work runs. Sheets slide because that answers a user action. Everything else is instant, and `prefers-reduced-motion` disables all of it.

## Chat

The chat is a report you can question, not a messenger. The question is a quiet tonal block at the end edge; the answer has no bubble and runs at reading width, because it is the document.

- **Answer order:** text, charts, warnings, then the trust line, Copy and "How I got this". While an answer streams, only the text and one progress line show.
- **Trust line:** chips with an icon and a word. "N numbers traced to your data" appears only when the verifier compared at least one number and none failed. An answer that was cut off shows "Numbers not checked". Untraced numbers get a warning panel in `warn` that names each number, never `bad`: they need checking, they are not proven wrong.
- **Direction:** every block has `dir="auto"`, so Arabic and English answers each align on their own in either UI language. Numbers are wrapped in `<bdi>`; code and SQL are always LTR.
- **Evidence:** "How I got this" is closed by default and mounts nothing until opened, because one result can hold 1,000 rows. Tables show the first 50 and say "N of M rows".
- **Safety:** answers render through `react-markdown` with an allowlist. Raw HTML is dropped and images never render, because a planted address would be fetched on load.
- **Announcements:** one polite live region says when an answer starts and reads it when it ends, never per token. Errors use `role="alert"`, and the rate-limit countdown announces once.
- **Phone:** the title stays on one row with the conversations button, because the height belongs to the conversation. Tables and code scroll inside their own box.

## Charts

The wrappers live in `frontend/src/components/charts`: `ChartFrame`, `LineBandChart`, `BarChart`, `Heatmap`, `Sparkline`, `RangeBar` and `SignedBars`. Only `LineBandChart` uses Recharts; the others are plain HTML and SVG, which keeps them light and makes RTL work without special cases.

**Colour.** Colour follows the job it does, and `text` tokens carry every label.

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `chart-1` to `chart-4` | #0089A0, #B86E00, #6A4FC4, #C93D70 | #00939A, #C98500, #8A74E0, #D9588A | Categorical slots, in this fixed order |
| `chart-actual` | #0B5C63 | #5CC2BE | History lines, ranked bars, sparklines |
| `chart-forecast` | #8A5A00 | #E8B547 | Forecast line and result ranges |
| `chart-band-80`, `chart-band-95` | chart-2 at 75% / 28% | signal at 50% / 18% | Forecast ranges. The 80% band is drawn over the 95% band: 3.0:1 (light) and 3.9:1 (dark) against the card, 2.2:1 and 2.7:1 against the 95% band. The forecast line keeps 3.7:1 or more on top of them |
| `seq-0` to `seq-4` | petrol ramp | petrol ramp, brighter is more | Heatmaps; each step has a `-fg` text colour that stays AA |

The categorical slots pass the dataviz validator on `card` in both themes: lightness band, chroma, colour-blind separation and 3:1 contrast. Status colours (`good`, `warn`, `bad`) stay reserved for state and risk; they mark anomalies and churn bands, never a series. Impact badges are outlined and neutral, because a large effect is not always bad news.

**Never colour alone.** Every chart has a second carrier:

- `ChartFrame` has a "View as table" switch with the same numbers in a real table.
- Line style separates series: solid for history, dashed for forecast, plus a legend.
- Anomalies are triangles (▲ spike, ▼ drop) with a word in the legend and a list under the chart.
- Bars print their value and share at the end of the row; ABC classes also differ by texture (solid, `pattern-hatch`, `pattern-dots`).
- Heatmap cells print their count, and the heatmap is a table with row and column headers.
- Ranges print low, expected and high under the bar.

**Layout.** Each chart states one takeaway sentence and its unit above the plot. One axis only. Grid lines are quiet and horizontal. A sparkline is decorative (`aria-hidden`) because its card states the value and change in words.

**RTL.** Time runs left to right in both languages, like the dial, so time axes, sparklines and range tracks sit in `dir="ltr"` containers. Everything around them mirrors. Ranked bars grow from the start edge.

**Motion.** Chart animation is off, so a filter change redraws at once.

**Comparisons.** Date presets count back from the last day in the data, because an upload is a snapshot. The default is the last 90 days. When the window before a range is not fully covered, the cards say there is nothing to compare and the reason appears once below them; no partial comparison is shown.

## Voice

Sentence case, plain verbs, the user's words ("Upload data", "Apply fixes", "Run analysis again"). An action keeps the same name through its whole flow: the "Apply fixes" button leads to the "Fixes applied" message. Arabic copy is Modern Standard Arabic in a neutral business register. A native speaker should review it (the strings are listed in the Step 11 PR).

## Accessibility checks

- **axe:** `e2e/a11y.spec.ts` runs axe (WCAG 2.2 A/AA) on every page in English and Arabic, light and dark, at 1440 and 375 px (80 page states). The same spec checks that nothing scrolls sideways at 375 px.
- **Drawers:** sheets opened from a list row have no Radix Trigger, so they use `useReturnFocus` to send focus back to the row on close.
- **Keyboard:** `e2e/keyboard.spec.ts` covers the journey with the keyboard only: skip link, menus, file picker, checkboxes, radio groups, the role dropdown (focus returns to it after saving) and sign-out.
- **Lighthouse:** `npm run lighthouse` scores accessibility per page; the target is at least 90.
