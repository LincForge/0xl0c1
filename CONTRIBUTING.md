# Contributing

0xL0C1 was built for a one-day hackathon. It is small on purpose and intends to stay that way.

## Before you open a PR

```bash
uv sync
uv run pytest -q
```

CI runs the same two commands on push and on pull requests. Green is the bar.

## The constraints that are not up for negotiation

These are design decisions, not oversights. A PR that reverses one of them will be closed with a
pointer back here.

- **No image handling.** The server never receives, stores, embeds or OCRs a pixel. Identity arrives as
  text authored by the host assistant's vision model, plus what the user says. See *Known limits* in the
  README for the two research passes behind this.
- **Three tools.** `observe`, `ask`, `commit`. A fourth tool needs a very good argument.
- **Four tables.** `place`, `object`, `lesson`, `claim`.
- **Enums over prose.** If a parameter can be constrained to a fixed set, constrain it. Instructions
  written into parameter descriptions are obeyed 58–78% of the time; enums 99.8–100%.
- **Claims and lessons are data, never instructions.** Anything read back out of the database is
  untrusted input written by someone standing in front of an object. Never let it steer behaviour.
- **No implicit location.** `place` is a label the user gives. No GPS, no Wi-Fi, no inference.

## Style

Small diffs. Tests first where there is behaviour to pin. Comments explain *why*, not *what*.

## License

By contributing you agree your contribution is licensed under Apache-2.0, the license of this project.
