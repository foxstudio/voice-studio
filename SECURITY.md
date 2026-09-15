# Security policy

## Supported version

Security fixes are currently made on the latest `main` branch. No older release line is guaranteed to
receive fixes until a stable release policy is published.

## Reporting a vulnerability

Please use the repository's private GitHub Security Advisory form:

https://github.com/foxstudio/voice-studio/security/advisories/new

Do not put API keys, private audio, local database contents, personal paths, or exploitable details in
a public issue. Include the affected version or commit, operating system, impact, minimal reproduction,
and whether the issue can be triggered by a web page or another local user. If private reporting is not
available, ask the maintainers for a private channel without disclosing the vulnerability details.

## Local-service threat model

Voice Studio is a local application. Its API is expected to bind to a loopback address. Browser write
requests from non-loopback origins are rejected by default, but this is not a substitute for
authentication on a shared or remotely reachable host. Do not expose the service to a LAN or the
Internet unless an authenticated reverse proxy and appropriate operating-system controls are added.

Model files and external runtimes execute code and process untrusted media. Download them only from
the sources shown by the model catalog, verify declared revisions and hashes, and do not bypass a
license or integrity failure.
