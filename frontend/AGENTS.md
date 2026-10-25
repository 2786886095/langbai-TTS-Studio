# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

## Durable product decisions

- Role voice profiles are engine-specific assets and must never be silently shared across engines.
- IndexTTS2 and VoxCPM2 profiles are reference-audio or voice-design configurations; GPT-SoVITS profiles bind a paired GPT `.ckpt`, SoVITS `.pth`, reference audio, exact reference text, and model version.
- A bound local IndexTTS2 or VoxCPM2 installation must not show a generic downloadable-weight table as though the role voice came from that table.
- Do not expose an application UI zoom preference or zoom keyboard shortcuts. The product uses responsive layout plus the existing comfortable/compact density choice.
- Community GPT-SoVITS downloads must preserve source and license context and lead into role-profile creation, not automatically select a voice for generation.
- VoxCPM2 training configuration uses the full workspace by default; task progress and logs open in a large right-side drawer instead of permanently consuming form width.
- The approved Studio v2 layout uses six navigation items (创作、任务、成品、声音、训练、引擎) plus Settings. Keep native three-engine creation and GPT-SoVITS multi-speaker creation accessible inside the creation workspace; community models belong under Voices and runtime terminals under Engines.
- The default long-form workspace and the standalone long-form webpage share the v2 task API, progress, and recovery state. Display generation, merge, export and verification as separate phases; file completion is not a claim of word-for-word listening verification.
- Breeze TTS 2 defaults to the verified accelerated reference-cloning profile (CFG 1). Keep Breeze bindings and voice assets separate from the legacy three-engine installation/profile schemas; do not silently share references across engines.
- Remember last-used voices, temporary references and output/segmentation options separately for each engine. Verify saved voice IDs still exist before restoring them. Reuse a recorded license acceptance only for the same accepted license version.
