# ASAT developer guide

This is the single-page orientation for anyone — human or AI — writing
code for the Accessible Spatial Audio Terminal. Read this once before
your first pull request, then keep it open alongside `FEATURE_REQUESTS.md`
as you work.

The user contract lives in [`USER_MANUAL.md`](USER_MANUAL.md). The
architecture tour lives in [`ARCHITECTURE.md`](ARCHITECTURE.md). The
event catalog lives in [`EVENTS.md`](EVENTS.md). The audio pipeline
lives in [`AUDIO.md`](AUDIO.md). The roadmap lives in
[`FEATURE_REQUESTS.md`](FEATURE_REQUESTS.md). This document is the
glue — the values every PR has to honour and the recipe every PR
follows.

---

## The design rule of thumb

Before writing a feature, ask: *what does the user hear?*

If the answer is "nothing new" or "more visual clutter," the feature
is wrong. ASAT exists so a blind developer can read, write, run, and
revise code using only sound and keystrokes. Every change that leaves
that narration loop unchanged is overhead.

---

## Guiding principles

These are non-negotiable. New contributions must honour all of them.

1. **Narration-first, sight-optional.** Every state change publishes
   an event; every event that matters to the user gets a cue or a
   spoken phrase. Visuals are secondary — a screen reader or a
   sighted viewer is consuming a text trace, not the primary UI.
2. **Core Python only.** Standard library is the entire dependency
   budget. `numpy` is the single optional accelerator (measured
   HRTFs) and the code falls back to pure Python without it. No
   other third-party runtime deps. Security, portability, and
   install simplicity all follow from this one rule.
3. **Extraordinarily simple, clear, concise.** Write the least
   code that solves the problem. Three similar lines beat a
   premature abstraction. No speculative generality; no "just in
   case" parameters; no helpers for hypothetical second call-sites.
   Plain English identifiers; plain English doc strings.
4. **Flat and shallow when possible.** A helper stays in the file
   that uses it until a second call-site needs it. A module stays
   flat unless a clear hierarchy makes navigation easier. One
   module per concern; no sub-packages unless the concern really
   has sub-concerns.
5. **Clear hierarchy when it helps.** When a module grows beyond
   what a reader can hold in one sitting, split it along the
   seams the code itself suggests. Don't force the split early;
   don't resist it late.
6. **Minimal on-screen chrome.** A screen reader already describes
   text; avoid duplicate UI. No decorative banners, no borders,
   no tree gutters. A single line announcing what just changed
   beats a repainting pane.
7. **Deterministic by construction.** Tests use `MemorySink` by
   default and inject every clock, runner, keyboard, and
   subprocess. No real audio in tests, no wall-clock sleeps, no
   sockets. New code must accept its dependencies through its
   constructor so a test can hand in fakes.
8. **One feature per PR.** Every pull request solves exactly one
   thing. If you find a tangential bug while shipping a feature,
   fix it in a separate PR. The diff everyone reviews is the diff
   that ships.
9. **Documentation lands with the code.** A feature is not
   shipped until its user-visible surface is in `USER_MANUAL.md`,
   its events are in `EVENTS.md`, and its entry in
   `FEATURE_REQUESTS.md` is marked shipped with a short
   "Sketch (shipped)" block. Pointers — file and line numbers —
   to the concrete implementation are required.
10. **No half-finished implementations.** Partial features shadow
    the bug they would have fixed and confuse the next author.
    If a PR can only land half a feature, the other half becomes
    a follow-up entry in `FEATURE_REQUESTS.md` before merge.

---

## How a feature lands

Each PR follows the same recipe. Smaller diffs are better diffs.

1. **Pick an entry in `FEATURE_REQUESTS.md`.** Read its Gap / Where
   it surfaces / Sketch sections. If the sketch is stale or wrong,
   fix the entry first in a tiny doc-only PR, then come back.
2. **Branch.** `claude/<short-feature-slug>` is the convention.
   One branch per PR.
3. **Write the test first when you can.** The default-bank
   `SAMPLE_PAYLOADS` fixture in `tests/test_default_bank.py` is
   the contract for every new event type — add your payload
   there before adding the event itself.
4. **Implement.** Keep diffs tight. If you find yourself needing
   a helper only once, inline it. If you find yourself copy-
   pasting three times, then extract.
5. **Run the full suite.** `python -m unittest discover -s tests -t .`.
   Zero failing tests, zero warnings. The count only goes up.
6. **Update docs.** `USER_MANUAL.md` for any user-visible change;
   `EVENTS.md` for any new event; `FEATURE_REQUESTS.md` to mark
   shipped; `HANDOFF.md` to bump the test count.
7. **Commit, push, open a PR.** Title starts with the feature id
   (e.g. `F21c: settings :reset …`). PR description includes a
   one-sentence summary, a short test-plan checklist, and the
   `https://claude.ai/code/session_…` trailer on commits made
   in Claude Code sessions.
8. **Respond to CI and review.** A Claude-driven session with
   webhook subscription will auto-notice CI failures and review
   comments; a human author handles them by refreshing the PR
   tab. Either way, fix the feedback in a new commit — never
   amend an already-pushed commit.

---

## Where to find things

| You want to know...                       | Look here                                          |
|-------------------------------------------|----------------------------------------------------|
| How ASAT is used at the keyboard          | [`USER_MANUAL.md`](USER_MANUAL.md)                 |
| How the code is organised                 | [`ARCHITECTURE.md`](ARCHITECTURE.md)               |
| Every event type and its payload          | [`EVENTS.md`](EVENTS.md)                           |
| How audio is rendered and spatialised     | [`AUDIO.md`](AUDIO.md)                             |
| What features are shipped / open          | [`FEATURE_REQUESTS.md`](FEATURE_REQUESTS.md)       |
| What the next Claude session should do    | [`../HANDOFF.md`](../HANDOFF.md)                   |
| How Claude Code modes interact with ASAT  | [`CLAUDE_CODE_MODES.md`](CLAUDE_CODE_MODES.md)     |

---

## The simplicity checklist

Before opening a PR, read the diff back and ask:

- Could any helper be inlined? Inline it.
- Could any new parameter be removed? Remove it.
- Could any new class be a function? Make it a function.
- Could any new module be a helper in an existing module? Move it.
- Could any new event be carried on an existing event? Carry it.
- Does any comment explain **what** the code does rather than
  **why** a reader might be surprised? Delete it — the code is
  its own what.
- Does the user-visible behaviour need a one-line note somewhere
  in `USER_MANUAL.md`? Write that line.

If every answer is "no further change," the diff is ready.
