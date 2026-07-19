# Design QA — 参数抽屉、模型广场、运行终端与更新提示

## Evidence

- Source visual truth: `F:\AI\peyin\langbai-TTS-Studio-src\docs\audit\v1.1.0\studio.png`
  - This archived v1.1.0 screen is the repository copy of the permanent narrow parameter panel shown in the user's reference. The original attachment path was no longer present during final QA.
- Implementation screenshots:
  - `F:\AI\peyin\langbai-TTS-Studio-src\docs\audit\2026-07-19\parameters-1316x921.png`
  - `F:\AI\peyin\langbai-TTS-Studio-src\docs\audit\2026-07-19\community-1316x921.png`
  - `F:\AI\peyin\langbai-TTS-Studio-src\docs\audit\2026-07-19\runtime-1316x921.png`
  - `F:\AI\peyin\langbai-TTS-Studio-src\docs\audit\2026-07-19\update-1316x921.png`
- Full-view comparison input: `F:\AI\peyin\langbai-TTS-Studio-src\docs\audit\2026-07-19\comparison-original-vs-parameters.png`
- Requested implementation viewport: 1316 × 921, 100% zoom, comfortable density, Windows desktop.
- Compared state: IndexTTS 2 selected; old permanent parameter panel versus new open right-side parameter drawer.

## Findings

- No actionable P0/P1/P2 findings remain.
- Typography: Chinese system UI stack, weights, line heights and hierarchy remain consistent with the existing product. Drawer labels and explanatory copy are readable at the target viewport; no clipping was found.
- Spacing and layout rhythm: the editor regains the full work area when the drawer is closed. The open 660px drawer uses a two-column field grid, fixed header/tools/footer and an independent scroll region. Primary actions remain visible.
- Colors and visual tokens: the pure-color navy, blue, white and green token system is retained. The scrim establishes focus without replacing the product palette with gradients or decorative effects.
- Image quality and assets: the existing raster application character icon is retained without stretching or replacement. Lucide icons remain visually consistent and aligned.
- Copy and content: model source/license language distinguishes direct download, external source path and unknown license. Runtime copy explicitly says the three engines do not start together.
- Accessibility and behavior: the drawer has dialog semantics, an explicit close button, scrim close, Escape close, searchable parameters and persistent actions. Visible controls at the target viewport do not overlap or fall below the fold.
- Focused region comparison: the combined image keeps both parameter regions legible at original height; a separate crop was not needed because labels, field widths, help copy and the action footer can be judged directly.

## Primary interactions tested

- Open and close the right-to-left parameter drawer.
- Search and jump among parameter groups; edit controls remain usable in the two-column layout.
- Open GPT model plaza and render the direct-source catalog.
- Open runtime terminal, poll all three engine states and render command/log panes.
- Render the global update-available notice and its immediate-download action.
- Backend runtime start/restart/stop and local model scan contracts through automated API tests.

## Console and build checks

- Electron development captures completed without renderer/React console errors.
- `npx tsc --noEmit`: passed.
- `npm run build`: passed.
- Backend pytest: 50 passed, 1 third-party deprecation warning.

## Comparison history

- Initial issue: the permanent narrow parameter panel compressed the editor and made full inference parameters difficult to operate.
- Fix: removed the permanent panel, restored a single-column editor, and added a 660px right-to-left drawer with search, group shortcuts, two-column fields and fixed actions.
- Post-fix evidence: `parameters-1316x921.png` and `comparison-original-vs-parameters.png` show the editor no longer permanently sacrifices width and the expanded parameter state remains usable.

## Follow-up polish

- P3: after the training-center scope is chosen, use the same runtime status and source/license language in the training flow for consistency.

final result: passed

---

# Design QA — 模型训练选择、训练指引与软件内工作台

## Evidence

- Source visual truth: `F:\AI\peyin\langbai-TTS-Studio-src\docs\audit\v1.1.0\studio.png`
  - Used as the existing product design-system baseline for navigation, pure-color surfaces, typography, density, buttons and status language. The new training flow is a requested new route, so there is no same-state source mock.
- Implementation screenshots:
  - `F:\AI\peyin\langbai-TTS-Studio-src\docs\audit\2026-07-20\training-hub-1316x921.png`
  - `F:\AI\peyin\langbai-TTS-Studio-src\docs\audit\2026-07-20\training-gpt-running-1316x921.png`
  - `F:\AI\peyin\langbai-TTS-Studio-src\docs\audit\2026-07-20\training-vox-1316x921.png`
- Full-view comparison input: `F:\AI\peyin\langbai-TTS-Studio-src\docs\audit\2026-07-20\comparison-training-flow.png`
- Viewport: 1316 × 921, 100% zoom, comfortable density, Windows desktop.
- States: training-engine selection; GPT-SoVITS official workbench running and embedded; VoxCPM2 empty training form with detected local paths.

## Findings

- No actionable P0/P1/P2 findings remain.
- Typography: Segoe UI Variable/Microsoft YaHei hierarchy, weights and line heights match the existing studio. Titles, status chips, tutorial metadata and form help text remain legible without unintended wrapping.
- Spacing and layout rhythm: the training selector uses two equal primary choices; onboarding and tutorial sections follow the existing 28px page gutter and compact card rhythm. The earlier VoxCPM2 base-model path overflow was fixed with a constrained, clipped path button and the revised screenshot shows no overlap or horizontal scrollbar.
- Colors and visual tokens: navy navigation, white surfaces, blue information states and green ready/primary states map to the existing pure-color token system. No gradients or decorative CSS imagery were introduced.
- Image quality and assets: the existing raster application character icon remains sharp and correctly cropped. All new UI icons use the existing Lucide icon family; no fake SVG, emoji or CSS-drawn visual asset substitutes were added.
- Copy and content: the selector clearly distinguishes GPT-SoVITS paired `.pth`/`.ckpt` training from VoxCPM2 LoRA/full SFT. The tutorial area distinguishes official documentation, a verified Bilibili tutorial and a dynamic Bilibili search entry instead of presenting an unverified video as official.
- Behavior and accessibility: navigation, engine choice, back flow, refresh, workbench start/stop, embedded local interface and external-link controls are keyboard buttons with focus styles. Disabled browser-open state is visible before the local server is reachable.
- Focused region comparison: the combined 2×2 comparison keeps the sidebar, engine cards, five-step guide, embedded GPT-SoVITS header and VoxCPM2 path fields readable at original capture density, so a separate crop was not needed.

## Primary interactions tested

- Open the single left-side `模型训练` route and choose either training engine.
- Start the bound GPT-SoVITS official workbench, wait until `http://127.0.0.1:9875` is reachable, and render it inside the Electron page.
- Verify the packaged startup wrapper creates zero new Edge/Chrome/Firefox processes while the embedded workbench becomes reachable.
- Stop GPT-SoVITS through the software and verify the workbench process exits.
- Enter VoxCPM2 training and render detected base model/output paths without cross-panel overflow.
- API contracts for GPT workbench start/stop/activity and VoxCPM2 LoRA/SFT training tasks.

## Console and build checks

- Electron captures completed without renderer/React console errors.
- Frontend TypeScript and Vite production build: passed.
- Backend pytest: 53 passed, 1 third-party deprecation warning on the confirming run.
- One earlier full-suite run hit the pre-existing transient installer `.tmp → .json` `WinError 5`; the immediate full rerun passed. This is recorded as a non-blocking reliability risk, not treated as fixed by this feature.
- PyInstaller backend build and Windows NSIS Setup build: passed.

## Comparison history

- Initial P2: the VoxCPM2 base-model path control expanded beneath the adjacent monitor panel.
  - Fix: constrained the path button with `min-width: 0` and `overflow: hidden` while preserving ellipsis on the text node.
  - Post-fix evidence: `training-vox-1316x921.png` shows the path contained within the left form column and no horizontal overflow.
- Initial behavior mismatch: the upstream GPT-SoVITS WebUI hardcodes Gradio `inbrowser=True`, causing an external browser to open.
  - Fix: added an isolated standard-library browser-hook wrapper used only by langbai TTS Studio; the local WebUI remains embedded and the user's GPT-SoVITS source is not modified.
  - Post-fix evidence: packaged runtime reached port 9875 with zero new browser processes; `training-gpt-running-1316x921.png` shows the interface inside the app.

## Follow-up polish

- P3: add a compact “已看完” state for tutorial cards if training onboarding later gains persistent progress tracking.

final result: passed
