---
name: code
description: Use when writing or changing a script or program in any language, including shell inside YAML, Dockerfiles and CI files.
---
<!-- agent-prompt -->
- Write scripts in Python, including small glue.
- Write a shell script (POSIX sh that runs on Alpine and Bash) only when it mostly runs commands. Put any regex, escaping or quoting in Python.
- Write services in Go or TypeScript.
- Write only executable instructions in code. Put explanations in a README.md, or in a markdown prompt tagged <!-- agent-prompt -->.
