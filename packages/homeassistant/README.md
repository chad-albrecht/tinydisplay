# tinydisplay-homeassistant

**Not yet implemented — planned for [Phase 5](../../docs/roadmap.md).**

The Home Assistant custom integration: entity state in, rendered frames out.

Planned scope:

- A custom component with config-flow setup.
- Entity subscription and a change-driven render loop.
- YAML dashboard definitions, Lovelace-flavoured.
- HACS packaging.

It lands last on purpose. By the time this package exists the rendering stack
is already proven against the simulator and real hardware, so an integration
bug is unambiguously an integration bug.

Nothing below this package may import it, and it is the only package permitted
to import `homeassistant`.

This directory holds no code yet, and is excluded from the uv workspace until
it does.
