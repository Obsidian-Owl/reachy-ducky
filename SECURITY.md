# Security Policy

## Supported Versions

Reachy Ducky is pre-1.0 and under active development. Only the `main` branch receives security fixes.

## Reporting a Vulnerability

Please **do not** open public GitHub issues for security vulnerabilities.

Report privately via **GitHub Security Advisories**: https://github.com/Obsidian-Owl/reachy-ducky/security/advisories/new

Include:
- A description of the issue and its impact
- Steps to reproduce, or a proof-of-concept
- Affected files / commit SHA if known

You should receive an initial acknowledgement within a week. If you don't, you're welcome to follow up by opening a minimal public issue that does not disclose the vulnerability details and referencing your private advisory.

## Scope

This project handles:
- Access to your local source code (read-only)
- Outbound calls to Claude and OpenAI APIs
- A daemon HTTP endpoint on your Mac

Security-sensitive areas include:
- Secret redaction in diffs/file content sent to external APIs
- Daemon HTTP auth (Tailscale-primary, bearer-token fallback)
- Read-only tool-belt enforcement (no write operations)
- Repo allowlist / per-file blocklist

Reports affecting any of those are taken seriously. Reports about third-party dependencies should go to the upstream project; we'll patch forward once they ship.
