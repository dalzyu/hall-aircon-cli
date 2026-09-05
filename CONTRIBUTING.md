# Contributing

Keep the CLI dependency-free and share network behavior in `hall_aircon_api.py`.
Use Python 3.10-compatible syntax. See the README for setup and test commands.

For fixes, add an offline regression test that reproduces the behavior.
Do not use real accounts or control physical devices in automated tests.
Use synthetic tokens and `example.test` addresses in fixtures.

Open a pull request describing the user-visible change and validation.
For UI changes, include a screenshot without account details.
Thermostat changes should explain timing, failure behavior, and hardware limits.
