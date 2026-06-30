# Design Constraints

This file is treated as static prompt context. Generated code must respect these design constraints when a task involves UI, style, components, or architecture.

## Visual & Architectural Design Constraints

You are provided with a `design.md` context. You must adhere to the design system described within it when generating code.

Instructions:

1. Token Adherence: When defining styles, spacing, or component properties, use the values defined in `design.md`. Do not invent new magic numbers or colors.
2. Component Logic: Use the component patterns defined in `design.md`. If a component exists in the design system, use it rather than building a custom implementation.
3. Validation: Generated code may be audited against the tokens in `design.md`. Any violation of the design system can be treated as a `STATIC_VIOLATION` by the harness.

## Default Tokens

- Spacing: use `4`, `8`, `12`, `16`, `24`, `32`, or `48`.
- Radius: use `4` or `8`.
- Layout: prefer explicit, readable hierarchy over decorative layout.
- Color: use named design tokens from the project when available. Do not invent raw hex colors unless the task explicitly requires a new token.

## Current Task Contract

Refactor target code to satisfy static engine rules, behavioral correctness, and design constraints when design constraints are relevant to the code being generated.
