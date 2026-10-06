# Security Policy

## Supported Versions

FrameAtlas is an early release. Security fixes go into the latest release only.

| Version | Supported |
| ------- | --------- |
| 0.1.0 (latest release) | Yes |
| older releases | No |

## Reporting a Vulnerability

Please **do not** open a public issue for security vulnerabilities.

Report it privately by email to **fizzexual@gmail.com**. This keeps the details
confidential until a fix is available.

When reporting, please include:

- The FrameAtlas version (`python -m pip show frameatlas`) and how you installed it
- The OS, the Python version and the PyAV version
- Which part is affected: the CLI, the MCP server or the browser viewer
- Steps to reproduce, with the exact command and, if needed, a small sample
  video or dataset
- The impact: what an attacker can read, write or run, and what they need to
  control (the video file, the dataset, a web page, the MCP client)

You can expect an acknowledgement within 7 days. Once the issue is confirmed, a
fix will be prepared and published in a new release. Reporters are credited in
the release notes unless they ask to stay anonymous.

## Scope

In scope:

- **The CLI**: `extract`, `verify`, `serve`, `mcp` and the other commands.
  This includes handling of untrusted video files and paths, for example a
  crafted video or path that makes FrameAtlas write outside the chosen output
  folder, overwrite an existing dataset, or report an incomplete extraction as
  complete.
- **Dataset files**: `manifest.json`, `frames.jsonl`, `index.sqlite`,
  `coverage.sqlite` and the image folders. A crafted dataset must not let the
  tools read files outside the dataset root.
- **The MCP server**: the dataset is fixed at startup. Tools must not be able
  to choose other paths, run shell commands or reach a remote URL. Image and
  batch size limits must hold.
- **The browser viewer**: it binds only to `127.0.0.1` and checks the `Host`
  and `Origin` headers. A web page that can read frames or change coverage
  data through the viewer is in scope.

Out of scope:

- Bugs inside FFmpeg or PyAV themselves. Please report those upstream. If
  FrameAtlas makes such a bug easier to reach, report it here too.
- Running the viewer on a shared or public network. It is meant for a trusted
  local machine, not as a hosted multi-user service.
- How an AI agent or MCP host uses the images it receives.
