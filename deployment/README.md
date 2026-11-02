# CVPilot deployment files

This directory contains **safe, reviewable deployment examples**, not exported
live service configuration, credentials, or user data.

## Included files

- `systemd/cvpilot-controller.service.example`: minimal controller service template.
- `systemd/cvpilot-discord-commands.service.example`: Discord listener template.
- `systemd/cvpilot-controller.service.d/30-openclaw-compat.conf`: validated copy
  of the active controller drop-in, synchronized from your Ubuntu host.

The `.service.example` files are starter templates, **not exact copies** of your
installed systemd services. Compare their directives against your deployment.
OpenClaw's own installer manages `openclaw-gateway.service`; no Gateway unit
or real OpenClaw configuration is copied here.

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
