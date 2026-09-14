# Security policy

## Reporting a vulnerability

Do not open a public issue. Use GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) on this repository.

Include reproduction steps and impact. You will get an acknowledgement within a week.

## Deploying this safely

This project talks to a paid LLM API and, if you expose it, will spend your money on anyone who finds it. Before putting it on a public address:

- **Set `ADMIN_TOKEN`,** or leave it empty so the `/admin/*` endpoints stay disabled. These trigger data ingestion and cache rebuilds — unauthenticated, they are a free denial-of-wallet.
- **Set `CORS_ORIGINS` to explicit origins.** Not `*`.
- **Keep `DEV_RELOAD=false`.** Autoreload plus debug logging in production leaks internals and burns CPU.
- **Put it behind a reverse proxy** with TLS and rate limiting. The application does no per-client rate limiting of its own — the reliability layer throttles *outbound* calls to NSE, not inbound requests from users.
- **Do not bind to `0.0.0.0`** unless something in front of it is doing authentication.

## Secrets

`.env`, `*service-account*.json`, and `.private/` are gitignored. Verify with `git diff --cached` before committing.

If you leak a credential: **revoke it first, then clean the history.** Removing a file from a commit does not un-publish a key that was already pushed — assume anything committed to a public repo was scraped within minutes.
