# Visual macro editor plan and design

The requested result is a sixth navigation page, Macro Editor, using existing Qt styling and workers. Preserve every existing page, JSON root shape (object or step array), metadata, extra step properties, `${...}` literals, target mappings, and the existing macro executor. No push or merge.

## Inspected contracts

MacroPlayer accepts click (x/y, optional button/clicks), press (key), hotkey (keys array), text (text), sleep (seconds), and wait_image (image, condition appear/disappear, timeout default60, poll_interval0.5, confidence0.9, consecutive_matches2). Each step can have delay. `type` is an existing alias for action. Images resolve relative to the project with bundled fallback; absolute/traversal paths are prohibited. Macro limits are 1MB, 1–1000 steps, MAX_MACRO_SECONDS for input/fixed delays. Detector.image_visible returns a boolean using grayscale OpenCV matching on the primary screen. It does not expose a confidence score.

The executor currently always raises on image timeout. Add optional wait_image `on_timeout: stop|continue`, default stop. Continue only on the step's own expired wait deadline; cancellation, outer LINE deadlines, image errors and other timing limits still stop. Add a public validate(data) wrapper to reuse the exact executor validator without input actions.

## Components

- macro_editor.py: lossless document edits and summaries; repository loading, executor validation, stale fingerprints, existing project-idle locks, private backups and atomic replacement; portable image validation, thumbnail bytes and one-shot matching.
- qt_macro_editor.py: file/metadata controls, splitter with numbered step list and per-action forms, management buttons, reference preview, single-check detection, unsaved Save/Discard/Cancel continuations. Use the panel's existing QThread job lifecycle for all I/O/validation/detection.
- qt_panel.py: add page, refresh file choices without resetting edits, disable writes/detection during automation, preserve drafts across start/settings workflows and include editor close protection. Existing programmatic confirm=False cleanup remains an explicit discard path.
- tests: document/repository tests, timeout behavior tests, Qt integration tests; preserve existing regression suite.

Unknown/malformed steps load read-only with a warning, preserving raw data (sensitive text is masked in summaries). Recognized actions retain extra fields. Unknown actions cannot be saved as an executable macro while rejected by the current executor; users can preserve the open document or explicitly delete/repair the incompatible step. Step edits validate on selection changes; invalid drafts remain selected and cannot be silently lost. Save validates the entire document before replacing a file. Empty new macros show an empty state but cannot be saved as executable until a step is added.

Images inside the project use portable project-relative paths. Other project directories warn about portability; outside-project images are rejected with instructions to explicitly copy them into assets, because the executor cannot resolve outside paths. No automatic copying. Preview/matching use the same resource resolver; image tests run exactly one matching check and never invoke MacroPlayer.play or desktop input.

Unsaved transitions use asynchronous continuations: Save completes before Open/New/Close continues; failure or Cancel retains the document. Edits, file controls and detection are disabled during jobs; no overlapping tests. Existing panel-owned workers are retained during shutdown.

## Execution

- [x] Add red tests for lossless edits/saves, validation, stale/atomic writes and detection isolation; implement model/repository.
- [x] Add red tests for default stop, optional continue, deadline/error/cancellation protection; implement minimal executor extension.
- [x] Add the page/forms, preview/detection jobs, dirty transitions and panel integration; cover with offscreen tests.
- [x] Run full suite/static checks and smoke layouts; independent review and fix material findings.
- [x] Document actual fields, manual testing and unavailable Windows/BMS checks. Keep branch local.


## Compatibility decision

In-memory editor drafts do not block the existing Start Bot or Settings Save & Apply workflow. This allows changing MAX_MACRO_SECONDS to repair a long draft without discarding it. Disk saves and snapshot detection remain disabled during active automation; existing project-idle locks provide the final write guard. An unsaved transition that explicitly chooses Save cooperatively stops active work before saving and continuing.


## Verification and review record

- Full offline suite: `XDG_CACHE_HOME=/tmp/bms-qt-cache .venv/bin/python -m unittest discover -s tests -q` — **282 tests: 267 passed, 15 skipped** on Python 3.12. Existing localhost-port tests require socket permission and pass with that permission; skips are legacy Tk desktop and Windows file-sharing checks.
- Added 13 document/repository tests and 17 Qt editor tests. Extended wait-image tests cover optional Continue, invalid policies and unchanged deadline/cancellation/error behavior. Existing Qt page navigation expectations now include Macro Editor.
- Ruff E4/E7/E9/F/I checks and formatting pass for new Python/editor integration code; all 43 project Python files compile, pip check passes, and git diff --check passes.
- Offscreen manual smoke: editor at 1100×800 and 760×560, existing action forms, shared reference thumbnail, scrolling and clean worker shutdown. No real matching or desktop input was invoked in smoke checks.
- Independent read-only review identified literal comma-key editing, async Save→Open/Close ordering, a post-replacement fingerprint race, Save-close during active automation, and raw private configuration remaining revealed across step selections. Each was reproduced before fixing and pinned by regression tests. No material findings remain unresolved.
- Additional regressions protect missing-reference warnings, absent metadata properties, text re-masking, Cancel replacing a queued Save-close, and adjusting settings/starting the bot while retaining a draft.

Actual wait fields: action=wait_image, image, condition=appear|disappear, timeout, on_timeout=stop|continue, poll_interval, confidence, consecutive_matches, plus common delay. The only executor additions are a public in-memory validate wrapper and optional on_timeout handling; omission retains Stop. Unknown actions remain intact/read-only and cannot be saved as executable until supported or explicitly repaired. No target mappings, .env values, detector algorithm, LINE commands, schedules, capture workflow or launcher files were changed.

Remaining local acceptance: actual Windows rendering/DPI/batch startup, packaged Windows execution, real BMS screen matching, real input/macros, login/logout, LINE/ngrok and end-to-end scheduled deliveries. These were not tested live. No new dependencies and no executable build. Changes remain on feat/pyside6-gui without push or merge.
