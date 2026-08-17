# hall-aircon-cli

Unofficial command-line client for the **Hall Aircon** service. It talks to the
same public HTTPS API that the official mobile app uses, with **your own**
account. No third-party dependencies — Python 3.8+ standard library only.

> Not affiliated with Daikin or NTU. Use only with your own account and your
> own aircon unit.

## Install

```bash
git clone https://github.com/<you>/hall-aircon-cli.git
cd hall-aircon-cli
# no dependencies to install
```

## Quick start

```bash
# Log in once (stores the session token in ~/.config/hall-aircon/config.json, mode 0600)
./hall_aircon.py login --email YOU0001@e.ntu.edu.sg

# For non-student (email + password) accounts:
./hall_aircon.py login --email you@example.com --password ...

# Then:
./hall_aircon.py status          # balance + aircon state
./hall_aircon.py on              # turn on
./hall_aircon.py off             # turn off
./hall_aircon.py temp 23         # set setpoint (16-30 C)
./hall_aircon.py fan M           # fan speed: A, L, LM, M, MH, H
./hall_aircon.py swing on        # swing/flap: on | off
./hall_aircon.py usage           # usage history (billed sessions)
./hall_aircon.py topups          # top-up history
./hall_aircon.py inbox           # notifications
./hall_aircon.py logout          # log out and delete the stored token
```

### Token alternatives

You can skip `login` and supply a session token directly:

```bash
export HALL_AIRCON_TOKEN=...
./hall_aircon.py status
# or per-command:
./hall_aircon.py --token ... status
```

## How login works

1. `login` sends your email to the account-check endpoint.
2. **Student accounts** (`@e.ntu.edu.sg`): it prints the NTU sign-in URL. Open it
   in your browser, complete NTU SSO, then paste the final redirect URL (it
   starts with `https://cmsntu-prod.daikinpayu.com/adfs/saml/redirect/`) back
   into the tool. The tool exchanges the redirect for a session token.
3. **Other accounts**: it prompts for your password and logs in directly.

The session token is valid ~90 days and is stored locally with restrictive
permissions. It is never sent anywhere except to the official API.

## Notes

- Turning the aircon **off settles the current usage session** — the accrued
  charge is deducted from your wallet balance. That's normal billing behaviour.
- Commands are accepted by the cloud immediately, but the physical unit may
  take ~10 seconds to reflect a change (`status` shows the last reported state).
- The official API enforces rate limits; please don't script rapid toggling.

## License

MIT — see [LICENSE](LICENSE).
