# Security Policy

## Supported release line

`0.2.0rc1` is the current local release-candidate line under security review. Earlier development
snapshots are not maintained as supported security releases.

## Reporting a vulnerability

Use GitHub Private Vulnerability Reporting:

https://github.com/sangtikui642-sketch/battery-health-benchmark/security/advisories/new

Do not report vulnerabilities in public issues, discussions, pull requests, or commit messages.
Include the affected version, reproduction conditions, impact, and the smallest safe evidence that
allows the maintainer to validate the report. Do not include real battery fleet data, credentials,
personal data, company material, or unpublished patent information.

No security email address is published for this release. If the private reporting link is not
available, the repository owner must enable Private Vulnerability Reporting in GitHub Security
settings before the public release proceeds.

## Response and disclosure

The maintainer will acknowledge reports on a best-effort basis, assess whether the issue is in the
supported release line, and coordinate remediation and disclosure through the private advisory.
There is no guaranteed response-time or remediation-time service-level agreement.

Security reports do not authorize testing against systems, datasets, accounts, or infrastructure
that the reporter does not own or have explicit permission to assess.

## Scope

In scope are repository code, packaging, CI configuration, evidence integrity, path handling,
credential detection, and documented trust boundaries. Accuracy limitations of synthetic models,
unsupported real-data claims, third-party service outages, and vulnerabilities in unbundled
quarantined plugins should be reported to their respective upstream projects unless AutoBench
introduces the exposure.
