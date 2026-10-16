# AGENTS.md

Standing checks for Cursor cloud agents working on this site.

## UI checks (required)

Before marking **UI Ready** or merging any UI change:

- Verify **both mobile and desktop viewports**.
- Confirm there is **no overflow, misalignment, hidden sections, or layout bugs**.
- Prefer checking the **live/preview UI**, not only code review.

Preview: `python3 -m http.server 8765` then open `http://127.0.0.1:8765/` (or the GitHub Pages URL).
