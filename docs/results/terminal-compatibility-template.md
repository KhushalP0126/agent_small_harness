# Terminal compatibility record

Use this template only after testing a real Rust TUI session. It records the
manual evidence that automated unit tests cannot provide.

| Date | Terminal and version | OS | Selected renderer | Resize/scroll result | Mermaid result | Evidence link |
| --- | --- | --- | --- | --- | --- | --- |
| YYYY-MM-DD | Kitty / iTerm2 / WezTerm / Apple Terminal | | native / fallback | | | |

## Procedure

1. Launch `make start REPO_ROOT=.` in the named terminal.
2. Open a repository map and Mermaid diagram.
3. Resize the terminal, scroll the stream, then reopen the diagram.
4. Record whether native Kitty/iTerm2 output was selected and visually
   correct. Apple Terminal verifies only the quadrant-block fallback.

Do not mark native protocol support verified without a real Kitty and iTerm2
session. Attach a screenshot or session log from each verified terminal.
