# Settings redesign QA

## Comparison target

- Source visual truth: `/Users/foxmacstudio/Projects/mlx-indextts/docs/design/settings-search-layout-reference.png`
- Desktop implementation: `/Users/foxmacstudio/Projects/mlx-indextts/output/playwright/settings-acceptance/settings-desktop-1440x1024-v2.png`
- Mobile implementation: `/Users/foxmacstudio/Projects/mlx-indextts/output/playwright/settings-acceptance/settings-mobile-390x844-v2.png`
- Full-view comparison: `/Users/foxmacstudio/Projects/mlx-indextts/output/playwright/settings-acceptance/settings-desktop-comparison-v2.png`
- Focused comparison: `/Users/foxmacstudio/Projects/mlx-indextts/output/playwright/settings-acceptance/settings-desktop-focused-comparison-v2.png`
- Viewports: desktop 1440 × 1024; mobile 390 × 844
- State: dark theme, `/settings`, common settings selected, saved state, real local API data

## Findings

No actionable P0, P1, or P2 findings remain.

The implementation preserves the selected source's search-first hierarchy, top save state, six horizontal sections, dark neutral surfaces, blue primary action, green success state, light list rows, and local-secret reassurance. It intentionally uses real selects and checkboxes instead of the source image's mostly navigational chevrons because the production page must retain direct editing.

## Required fidelity surfaces

- Fonts and typography: system UI / PingFang-compatible stack is consistent with the product shell. Heading, section label, row title, description, status, and path text have distinct readable levels. No desktop or mobile clipping was found.
- Spacing and layout rhythm: search, save controls, tab rail, section heading, and rows align to a consistent grid. Desktop rows match the source's light list rhythm. Mobile uses a visible 3 × 2 section grid and single-column controls with no horizontal overflow.
- Colors and visual tokens: near-black background, blue action/selection, green saved/configured states, amber missing-credential states, and restrained gray borders match the selected direction and existing product tokens.
- Image quality and asset fidelity: the selected source's blue waveform product mark was cropped from the source visual and saved as `/Users/foxmacstudio/Projects/mlx-indextts/frontend/static/voice-studio-mark.png`. UI icons use the installed Lucide library. No placeholder, emoji, CSS-drawn, or handwritten SVG assets are used.
- Copy and content: labels explain effect in plain Chinese. MiMo, Doubao API Key, and Volcengine AK/SK remain explicitly separate. LLM copy says connection configuration does not imply business integration. Secret fields say they are local-only and never echoed.

## Interaction and browser evidence

- Search for `日志` navigated from common settings to advanced settings and focused the real log-directory field.
- Changing the default language enabled `保存更改`; restoring the original value returned the page to `已保存` without sending a write.
- All six section buttons worked on desktop and mobile.
- Common, cloud, AI, files/storage, and advanced sections were captured from the real local service.
- Password inputs were empty after loading even though configured-state badges were present.
- Desktop and mobile checks found zero horizontal page overflow and zero unlabeled controls.
- Browser console errors: 0; page errors: 0; failed requests: 0; HTTP responses >= 400: 0.

## Comparison history

### Iteration 1

- P2: mobile section navigation clipped later sections and relied on horizontal discovery.
- P2: the saved primary action became too visually weak when disabled.
- P2: the common section omitted the source's persistent local-secret reassurance.
- P2: the existing clock brand mark did not match the selected visual's waveform identity.

Fixes:

- Reworked mobile navigation into a visible 3 × 2 grid.
- Raised saved-button contrast while keeping the no-op state disabled.
- Added the local-only, no-echo security note to common and advanced settings.
- Reused the selected source's waveform mark for the sidebar and favicon.
- Added saved-snapshot comparison so reverting an edit clears the dirty state.

Post-fix evidence:

- `/Users/foxmacstudio/Projects/mlx-indextts/output/playwright/settings-acceptance/settings-desktop-1440x1024-v2.png`
- `/Users/foxmacstudio/Projects/mlx-indextts/output/playwright/settings-acceptance/settings-mobile-390x844-v2.png`
- `/Users/foxmacstudio/Projects/mlx-indextts/output/playwright/settings-acceptance/settings-desktop-comparison-v2.png`

### Iteration 2

- Rechecked the full view and focused main-content region against the selected source.
- Rechecked all six sections at desktop and mobile widths.
- No actionable P0, P1, or P2 mismatch remained.

## Follow-up polish

- P3: the production sidebar remains slightly denser than the source because it preserves the existing collapse control and application navigation.
- P3: cloud and storage sections contain more detail than the source mock because all current production capabilities are retained.

final result: passed

## Cloud connection iteration (2026-07-15)

- Renamed the provider to `Xiaomi MiMo API` and made pay-as-you-go the recommended default.
- Added an access-mode select for pay-as-you-go, Token Plan, and custom/saved URLs. The Token Plan option fills the China example while keeping the field editable for the subscription-specific URL.
- Added scoped connection tests for MiMo model access, Doubao TTS, and the Volcengine speaker catalog. The UI states what each probe verifies and whether it can consume a minimal amount of quota.
- Desktop evidence: `/Users/foxmacstudio/Projects/mlx-indextts/output/playwright/settings-cloud-expanded.png`
- Mobile evidence: `/Users/foxmacstudio/Projects/mlx-indextts/output/playwright/settings-cloud-mobile.png`
- Verified the mode select switches URLs and returns to the saved state without writing when restored.
- Mobile viewport: 390 x 844; document width: 390; horizontal overflow: false.
- Browser console errors: 0; warnings: 0.
- Live backend route check passed after restarting the existing backend tmux pane.
- Live non-generating probes passed: MiMo returned 6 available models; Volcengine `ListSpeakers` authorization passed. The billable Doubao TTS probe was intentionally not triggered during QA.
- Automated verification: backend 754 passed / 4 skipped; frontend 144 passed; Svelte diagnostics 0 errors / 0 warnings; production build passed; Ruff passed.

## Compact layout and overview merge (2026-07-15)

- Removed the redundant settings eyebrow and help drawer, then normalized the desktop hierarchy around a 42 px search/save toolbar, 40 px section rail, 36 px form controls, 62 px setting rows, and 62 px provider summaries.
- Replaced browser-default checkboxes with the same compact bordered control across common, cloud, and AI settings. Related options use wrapping horizontal clusters; mobile hit areas are 42–44 px.
- Removed the standalone sidebar overview. `/` now redirects to `/generate`, while Settings > Overview keeps only actionable configuration health and links the default-engine readiness card to `/engine-hub`.
- Desktop evidence: `output/playwright/settings-compact-final/common-desktop.png`, `cloud-desktop.png`, `ai-desktop.png`, and `files-desktop.png`.
- Responsive evidence: `files-mid-900.png`, `cloud-mobile.png`, `overview-mobile.png`, `files-mobile.png`, and `advanced-mobile.png`.
- Measured at 390 px: document width 390 px, no horizontal overflow; cloud fields 44 px, cloud test buttons 42 px, checkbox option rows 42 px, storage buttons at least 42 px.
- Measured at 1440 px: path fields 36 px, storage rows 82 px; AI and cloud controls stay on a consistent compact grid.
- Settings browser console: 0 errors and 0 warnings. Frontend tests: 147 passed. Production build: passed. `git diff --check`: passed.
- Full `svelte-check` remains blocked by one pre-existing, unrelated Lucide component type error in `video-localization/timeline-context-menu.ts:35`; the production build succeeds.

final result: passed with one unrelated repository-wide type-check blocker
