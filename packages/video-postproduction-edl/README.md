# Video Post-Production EDL

This directory owns the versioned EDL JSON contract used by the video
post-production API, worker, and renderers.

`edl.schema.json` is EDL v1. It is intentionally extensible for future audio,
color, asset, graphic, and generated-media operations. A renderer must validate
the required capabilities and fail explicitly when an operation is not
implemented; it must never silently discard an instruction.

The API's Pydantic models are kept in sync with this schema by contract tests.
Breaking changes require a new schema version and an explicit migration path.

V1B output settings may declare `render_profile` as `vertical_centered` (the
default deterministic 9:16 center crop) or `source` (scale without the
vertical crop). Unsupported visual, audio, color, and asset operations must
fail before rendering rather than being silently ignored.
