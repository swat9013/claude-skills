# Browser Visual Verification (Playwright MCP)

Tier 2 of verification: actually render the HTML and look at it. Use when Playwright MCP tools are available. If they are not, do not fake it — ask the user to run `! open <file.html>` and report what they see.

**Re-verify stale assumptions.** A handoff note saying "browser can't render here" reflects the environment it was written in. Environments differ (Linux sandbox vs local macOS). Re-test the claim in the *current* environment before accepting it.

## Steps

1. **Install the browser if missing (one-time, host-side).** Navigating may error with `Browser ... is not installed`. The binary downloads from the Playwright CDN (~260 MB), so it needs both network reach and write access to the npm cache — in-sandbox it fails with `EPERM`/`EROFS`. **Do not plan to disable the sandbox for it**: policy commonly forbids unsandboxed commands outright, and the npm registry is usually outside the sandbox's allowed hosts, so neither route is open to you. Ask the user to run it once themselves:

   ```
   ! npx @playwright/mcp install-browser chrome-for-testing
   ```

   Treat this as a precondition, not a step you own. If the user declines, Tier 2 is unavailable — hand off per case 3 rather than claiming visual correctness.

2. **Serve over HTTP — `file:` is blocked.** Playwright MCP refuses the `file:` protocol. Start a static server in the file's directory (background), then navigate to `http://localhost:PORT/...`. This binds loopback only and needs no sandbox exemption:

   ```
   python3 -m http.server 8765      # run from the HTML's directory, in the background
   ```

3. **Navigate and screenshot.**
   - `browser_resize` to a realistic width (e.g. 1100x900).
   - `browser_navigate` to `http://localhost:8765/<file>.html`.
   - `browser_take_screenshot` with `fullPage:true`, then `Read` the PNG to actually look at it.

4. **Inspect individual figures at full detail.** Full-page shots are small. Inject ids and screenshot elements:
   - `browser_evaluate`: find target cards by text, set `el.id = '...'` on each `svg`/element.
   - `browser_take_screenshot` with `target: '#that-id'` for a crisp close-up.

5. **Check the console.** `browser_console_messages` with `level:"error"`. A `favicon.ico` 404 is harmless noise from the local server; a real script error is not — fix it.

6. **Fix → re-navigate → re-screenshot** until the figures read correctly (no overlapping labels, no empty/`NaN` paths, intended layout).

## Cleanup (when done)

- `browser_close`.
- Stop the HTTP server (`pkill -f "http.server 8765"`).
- Remove temp screenshots and the `.playwright-mcp/` artifacts the tools drop in the working dir.

**Deletion gotchas:**
- Permission harnesses typically match the **literal command string**, so `/bin/rm` and an absolute-path spelling can forfeit the auto-allow that plain `rm` gets. Prefer `rm <explicit paths>` — flags narrow the match too, and `-f` is itself a guard trigger in some harnesses. If a target may be absent, `rm <path> 2>/dev/null || true` beats `-f`.
- Globs defeat that matching too (a `*` makes the target set undecidable up front) — list the files instead of `dir/*.png`.
- `find ... -delete` is commonly blocked outright — enumerate with `-print` first, then delete the paths explicitly.
- Keep temp artifacts under a tmp root (`/tmp/...` or `<cwd>/tmp/...`); deletion inside a tmp root is what auto-allow rules are usually scoped to.

## Quick reference

| Symptom | Cause / fix |
|---|---|
| `Browser ... is not installed` | Precondition unmet — ask the user to run install-browser once (step 1). |
| `Access to "file:" protocol is blocked` | Serve over HTTP; navigate to localhost. |
| `EPERM`/`EROFS` during install | Sandbox blocked the npm cache. This is not yours to work around — hand install to the user. |
| Figure renders blank | Likely `NaN` in an SVG path — run the headless builder test (cookbook). |
| `favicon.ico 404` console error | Harmless; ignore. |
