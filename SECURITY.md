# Security

Do not post credentials or private account data in public issues.
Use this repository's Security tab to privately report a vulnerability:
https://github.com/dalzyu/hall-aircon-cli/security/advisories/new

Include the affected version, reproduction steps using synthetic data, and
the expected impact. The latest release is the supported version.

Tokens and SSO redirect URLs are credentials. The client stores the session
token locally, not your password. Configuration and API overrides are trusted
local inputs. Only point `HALL_AIRCON_API` at a destination you trust with your
credentials. No automated process should upload your configuration file.
