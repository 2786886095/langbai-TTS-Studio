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
