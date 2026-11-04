# CVPilot deployment files

This directory contains **safe, reviewable deployment examples**, not exported
live service configuration, credentials, or user data.

## Included files

- `systemd/cvpilot-controller.service.example`: minimal controller service template.
- `systemd/cvpilot-discord-commands.service.example`: Discord listener template.
- `systemd/cvpilot-controller.service.d/30-openclaw-compat.conf`: validated copy
  of the active controller drop-in, synchronized from your Ubuntu host.
- `openclaw.json.example`: redacted reference snapshot of the OpenClaw
  configuration, including CVPilot's sandbox and Discord interaction settings.

The `.service.example` files are starter templates, **not exact copies** of your
installed systemd services. Compare their directives against your deployment.
OpenClaw's own installer manages `openclaw-gateway.service`; no Gateway unit
or unredacted OpenClaw configuration is copied here.

## Redacted OpenClaw configuration

[`openclaw.json.example`](openclaw.json.example) is a reference backup, not a
ready-to-run replacement for `~/.openclaw/openclaw.json`. It preserves the
configuration structure and operational settings while replacing identifying
values. Setup/consent timestamps, migration metadata, and personal heartbeat
destinations and prompts are omitted.

| Placeholder | Supply privately on your host |
| --- | --- |
| `${OPENCLAW_GATEWAY_TOKEN}` | A strong Gateway authentication token |
| `${CVPILOT_DISCORD_TOKEN}` | Your Discord bot token |
| `${OLLAMA_API_KEY}` | Your model provider credential, or a nonsecret dummy value for an unauthenticated local Ollama server |
| `/home/USER` | Your actual home directory |
| `YOUR_DISCORD_GUILD_ID` | Your Discord server ID |
| `YOUR_DISCORD_OWNER_ID` | Your operator Discord user ID |
| `YOUR_DISCORD_USER_ID` | The user associated with the demonstration agent binding |

The `${...}` values are OpenClaw environment-variable references. Set them in
the **Gateway service's environment** before using a customized copy; the
controller's environment file does not automatically configure the Gateway.
The model endpoint uses localhost in this example. Adjust it privately if
your model server runs elsewhere.

Install the CVPilot and DuckDuckGo OpenClaw plugins referenced in the example
before validating your customized private copy, or remove their entries if
they are not part of your installation.

`career-example-user` demonstrates a provisioned user agent without including
a real user's ID, display name, or filesystem paths. For a fresh installation,
omit that demonstration agent and its direct-message binding, and let CVPilot
provision actual `career-<Discord user ID>` agents during initialization.
When restoring an existing installation, use its matching private state and
bindings. The sandbox bind source must stay inside that agent's workspace;
the `/career` container path is an intentional compatibility identifier.

Keep `channels.discord.agentComponents.enabled` set to `false` when OpenClaw
and the CVPilot command listener share a Discord bot. OpenClaw's generic
component/form handlers otherwise compete with CVPilot's confirmation forms,
causing intermittent invalid-form errors. CVPilot's confirmation buttons still
work with this setting. OpenClaw's separate execution approvals and native
question/model controls are independent of this switch. Native OpenClaw slash
command registration is also disabled in the example because CVPilot's native
listener owns its commands.

This example is maintained separately from the allowlisted source sync script;
the script does not export the live configuration. Review and redact any future
updates before committing them. The real `openclaw.json` filename is ignored
by Git; the `.example` file is intentionally eligible for version control.

## Install the OpenClaw compatibility override

From this repository's root on the target Ubuntu host:

```bash
mkdir -p ~/.config/systemd/user/cvpilot-controller.service.d
cp deployment/systemd/cvpilot-controller.service.d/30-openclaw-compat.conf \
  ~/.config/systemd/user/cvpilot-controller.service.d/
systemctl --user daemon-reload
# Restart only when the controller has no active CVPilot jobs.
systemctl --user restart cvpilot-controller.service
systemctl --user show cvpilot-controller.service -p PrivateTmp
```

This override sets `PrivateTmp=no` so the OpenClaw CLI can check the running
Gateway's lock ownership on affected Ubuntu/systemd installations. It reduces
one filesystem-isolation setting on the controller; the separate Docker agent
sandbox is still configured independently.

## Security

Keep real secrets in `~/.config/cvpilot/controller.env` on the host, with
restricted filesystem permissions. Never copy that file, private SSH keys,
`~/.openclaw/openclaw.json`, or SQLite user/job state into this repository.
The sync script **only** accepts the `PrivateTmp=no` controller override;
other systemd drop-ins require manual security review.
