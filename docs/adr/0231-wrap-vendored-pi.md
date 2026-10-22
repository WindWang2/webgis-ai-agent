# Wrap vendored Pi; do not fork the core

The product hosts GIS on official Pi extension + spawn-flag + RPC surface: register tools, `--no-builtin-tools`, `before_agent_start` persona, persist SessionPlan ourselves. We do not fork `vendor/pi`, do not add a Pi-core Plan type, and do not require post-spawn RPC to change tools or the system prompt. Dynamic tool activation remains later work. Official Pi is a coding harness with a small core; a non-coding GIS host already fits that wrap.
