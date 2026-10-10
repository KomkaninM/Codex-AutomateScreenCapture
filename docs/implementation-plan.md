# BMS Automation LINE Bot implementation plan

Goal: implement the supplied desktop-to-LINE automation specification in the existing checkout. No worktree is needed in this isolated cloud task.

Architecture: Flask accepts signed events and queues bounded background work. A shared reentrant lock covers every desktop operation. The scheduler uses cancellation tokens and checks sessions ten seconds before due jobs. Configuration, desktop adapters, time, and HTTP transport have explicit test seams.

Decisions resolving specification gaps:
- All interactive commands use their reply token; only scheduled jobs push. Replies never fall back to pushes.
- LINE supports JPEG/PNG image messages, so retain archival JPEG and compressed WebP and generate JPEG original/preview URLs for LINE.
- Group member count is an upper bound, not a claimed count of unblocked recipients. Unavailable metrics display as unavailable.
- No .env template was supplied. Generate one from the documented variables, plus deployment/security settings.
- Real macros and reference images are operator-supplied; do not invent BMS coordinates or credentials. Unknown visual states block automation.
- Times use configurable Asia/Bangkok by default. Jobs are in memory and must be recreated after restart. Interactive work has a 45-second deadline to accommodate reply-token expiry.

1. Write unittest checks covering signed ingress, replay suppression, reply-only routing, desktop serialization, targeted login replacement, screenshot partitioning, clipboard input, cancellation/prechecks, and transport payloads. Run them before implementation.
2. Implement config.py (project .env, validated paths, atomic runtime settings), macro_player.py (validate all steps before input), detector.py (logged-in/logged-out/unknown guard), capture.py (mss framebuffer, archival and delivery outputs). Exercise them with deterministic hardware substitutes.
3. Implement line_api.py (authenticated time-bounded requests, quota/reach, separate reply/push, idempotency keys) and scheduler.py (ten-second precheck, one-shot and recurring jobs, cancellation, no accumulated backlog). Run their tests.
4. Implement server.py plus focused commands.py and workflow.py: HMAC ingress, bounded dispatch, authorization, runtime tunnel update authentication, image confinement, command dictionary, startup dashboard, graceful shutdown. Run end-to-end simulated capture/delivery through Flask.
5. Add requirements.txt, .env.example, targets.json, macro/schema examples, .gitignore and README deployment guide. Install in a virtualenv, run the full unittest suite and HTTP smoke check, review code and diff. Document that Windows desktop and live LINE delivery require operator validation.

Review focus: duplicate redelivery; malformed/oversized signed requests; reply expiry and queue overload; unknown detector state; stop-capture racing with queued work. Tests must pin each behavior. Live credentials, real DPI/coordinates, ngrok image retrieval, and actual BMS login/logout remain deployment checks.
