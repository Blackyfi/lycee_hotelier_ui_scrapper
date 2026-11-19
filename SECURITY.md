# Security policy

## Supported versions

Only the latest commit on `main` is supported. Please update before reporting.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report them privately through GitHub instead:
[Report a vulnerability](https://github.com/Blackyfi/lycee_hotelier_ui_scrapper/security/advisories/new)
(Security tab > "Report a vulnerability").

Include:

- what the issue is and where it lives (file, endpoint, setting);
- steps to reproduce or a proof of concept;
- the impact you expect (data exposure, auth bypass, ...).

You should get a first answer within a week. Once a fix is ready it will be
pushed to `main` and the advisory published, crediting you unless you prefer
otherwise.

## Scope

In scope: this repository's code, Docker setup and default configuration
(admin token auth, encrypted database, notification channels, inbox commands).

Out of scope: the upstream restaurant website and third-party services
(Gmail, Google Chat), and deployments that ignore the README's guidance
(for example exposing the dashboard publicly without the admin token).
