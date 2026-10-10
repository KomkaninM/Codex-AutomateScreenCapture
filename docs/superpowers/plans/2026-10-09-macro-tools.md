# Windows macro tools implementation plan

> Execute inline with tests, then request a focused code review.

Goal: let the operator record, export, convert, and play the same JSON macros the bot uses, retaining the supplied DH09D inputs privately.

Architecture: `macro_recorder.py` owns event aggregation and optional Windows listeners; `macro_tool.py` owns CLI/menu, conversion, export, and playback. Playback uses the existing `MacroPlayer`. Both recording and playback use the bot's OS instance lock. `launcher.py --macro-tool` supplies the managed Python environment without starting the bot or ngrok.

Constraints: Windows x64 Python 3.11–3.14, 100% DPI, primary-monitor coordinates, bounded macros, no credentials in Git. Login detection uses only `assets/login_anchor.png`; an absent anchor means assumed active session with the BMS visible.

1. Finish single-anchor detection tests and implementation; test fresh image recognition, missing/oversized anchors, targeted/default relogin, and logout confirmation.
2. Add failing tests in `tests/test_macro_tools.py` for legacy action/delay conversion, Unicode/clipboard recording, timing and modifier handling, reserved recording controls, path confinement and non-overwriting export, desktop metadata validation before playback, and launcher dispatch.
3. Implement `RecordedMacro` event aggregation with injected clock/clipboard; lazy pynput listeners reserve F8/F9 for record/stop/cancel and export only supported primitives. Reject unsupported drags, scrolling, modifier-clicks, and clicks outside the primary desktop.
4. Implement `convert_legacy(data)`, `save_macro(directory,name,data,max_seconds,overwrite=False)`, argparse record/play/convert modes and a beginner menu. Export validates through `MacroPlayer`, writes privately under `scripts/macros`, and preserves existing files by default. Old JSON may be read from a file or explicitly pasted clipboard contents. Desktop metadata preserves the recording resolution and blocks mismatched playback.
5. Add `pynput` dependency and `macro_tool.bat`; reuse the launcher bootstrap, propagate the tool option through `start_bot.bat`, and keep instance locks for recording/playback. Document limitations, clipboard capture, display resolution, and password privacy.
6. Convert the supplied eight-step DH09D script into ignored `scripts/macros/DH09D.json`, preserving its original URL and credentials locally. It is a complete login-and-navigation macro; configure it as the default login rather than publishing a target entry that would replay login clicks on an active session. Verify ignored credential files are absent from staged changes.
7. Run the complete tests on Python 3.12 and 3.14, dependency checks, focused review, push the existing branch and update PR #1. Windows input hooks and live BMS/LINE remain operator deployment checks.

Review focus: held/repeated modifier events, Ctrl+V control-character keycodes, duplicate F8 repeat events, export failures preserving old files, competing bot/tool desktop access, unsupported mouse gestures, and never including credentials in tracked output.

Ruling: do not register this full-login script as a target — its first five actions require the login page, while target navigation also runs on an already-active session. Use it as `LOGIN_MACRO_SCRIPT=DH09D.json` with untargeted `capture`. Standalone playback starts on the login page, preserving the supplied behavior.

Completion evidence: 68 tests pass on Python 3.12 and 3.14, with both environments passing `pip check`. The actual private DH09D macro was converted through the CLI and replayed with desktop adapters; all eight supplied steps were preserved. Windows pynput/six wheels were downloaded and their platform dependency markers inspected. The review's mouse-gesture finding was reproduced with listener adapters, fixed by checking modifiers on mouse-down and tracking pointer excursion, and covered by failing-then-passing regression tests. Windows input hooks and live BMS/LINE delivery remain local deployment checks.
