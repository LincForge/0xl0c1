# Contributing

0xL0C1 was built for a one-day hackathon. It is small on purpose and intends to stay that way.

## Before you open a PR

```bash
just check
just test
just build
```

`just check` is the canonical contribution gate. From a clean checkout it uses uv to provision the
locked development dependencies, then checks the lock, lint, formatting, strict types, and tests.
`just test` is the canonical focused test command, and `just build` is the canonical container build
command (and therefore requires Docker).

Tests use xdist with a default and hard maximum of six workers. On a constrained shared machine,
select a smaller worker count with a positive integer:

```bash
PYTEST_XDIST_AUTO_NUM_WORKERS=2 just test
```

Invalid values are rejected and values above six are capped at six. When debugging ordering or
concurrency, use `just test-serial` or the equivalent `just test -- -n0` path. The Docker-backed
`just build` and `just smoke` checks are intentionally outside the fast `just check` gate.

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
