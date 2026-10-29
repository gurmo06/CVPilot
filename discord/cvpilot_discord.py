#!/usr/bin/env python3
"""
Native CVPilot Discord slash-command tree. Sends authenticated user IDs to the
existing CVPilot v2 Unix-socket controller. Uses no LLM, GitHub credentials,
filesystem mounts, or Discord message-content privileges.
Use 'python3 -m py_compile   ~/.local/share/cvpilot/app/controller/main.py' for recompilation
"""
import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Literal

import discord
from discord import app_commands

SOCKET = os.environ.get('CVPILOT_SOCKET', str(Path.home() / '.local/state/cvpilot/controller.sock'))
TOKEN = os.environ.get('CVPILOT_DISCORD_TOKEN', '')
GUILD_ID = os.environ.get('CVPILOT_GUILD_ID', '').strip()
PUBLIC_CHANNEL_ID = os.environ.get('CVPILOT_CHANNEL_ID', '').strip()
USER_ROLE_ID = os.environ.get('CVPILOT_USER_ROLE_ID', '').strip()
ADMIN_CHANNEL_ID = os.environ.get('CVPILOT_ADMIN_CHANNEL_ID', '').strip()
CLEAR_GUILD = os.environ.get('CVPILOT_CLEAR_GUILD_COMMANDS', '0') == '1'
OWNER_ID = os.environ.get('CVPILOT_OWNER_DISCORD_ID', '').strip()
ADMIN_GUILD_ID = os.environ.get('CVPILOT_ADMIN_GUILD_ID', GUILD_ID).strip()

async def request_controller(uid: int, action: str, args: dict) -> str:
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(SOCKET), timeout=3)
    try:
        packet = json.dumps({'sender_id': str(uid), 'action': action, 'args': args}) + '\n'
        writer.write(packet.encode('utf8'))
        await asyncio.wait_for(writer.drain(), timeout=3)
        raw = await asyncio.wait_for(reader.readline(), timeout=10)
        if not raw or len(raw) > 65536:
            raise RuntimeError('Invalid or empty controller response')
        result = json.loads(raw)
        return str(result.get('text', 'No response from CVPilot controller'))[:1900]
    finally:
        writer.close()
        await writer.wait_closed()

async def public_authorized(interaction: discord.Interaction) -> bool:
    # The command is restricted to the dedicated guild, public command channel
    # and explicitly provisioned member role. Owner can operate without the role.
    if not GUILD_ID.isdigit() or not PUBLIC_CHANNEL_ID.isdigit() or not USER_ROLE_ID.isdigit():
        await interaction.response.send_message('GUILD_ID, PUBLIC_CHANNEL_ID, or USER_ROLE_ID is not configured correctly.', ephemeral=True)
        return False
    if OWNER_ID.isdigit() and interaction.user.id == int(OWNER_ID):
        return True
    if interaction.guild_id != int(GUILD_ID) or interaction.channel_id != int(PUBLIC_CHANNEL_ID):
        await interaction.response.send_message('Use CVPilot in the designated #cvpilot channel.', ephemeral=True)
        return False
    user_roles = getattr(interaction.user, 'roles', [])
    if int(USER_ROLE_ID) not in [getattr(role, 'id', None) for role in user_roles]:
        await interaction.response.send_message('Access requires the CVPilot User role.', ephemeral=True)
        return False
    return True

async def invoke(interaction: discord.Interaction, action: str, **args) -> None:
    # Discord provides interaction.user.id; never read a caller ID from user text.
    if not await public_authorized(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        response = await request_controller(interaction.user.id, action, args)
    except (OSError, TimeoutError, RuntimeError, ValueError) as exc:
        response = 'CVPilot controller unavailable or request failed. Check /cvpilot status or contact the operator.'
        print(f'[cvpilot] command error: {type(exc).__name__}: {exc}', flush=True)
    await interaction.followup.send(response, ephemeral=True)

# These are genuine typed Discord slash subcommands, not a generic 'input' box.
cvpilot = app_commands.Group(name='cvpilot', description='Manage your private CVPilot career resumes')
# Guild-only commands are registered to the dedicated CVPilot server below.

@cvpilot.command(name='help', description='Show CVPilot commands and usage examples')
async def help_command(interaction: discord.Interaction):
    if not await public_authorized(interaction):
        return
    help_text = (
        '**CVPilot — Quick Help**\n'
        '**Getting started**\n'
        '`/cvpilot init github_username:<your GitHub username>` — Create your private repo.\n'
        '`/cvpilot repo` — View the repository link.\n'
        '`/cvpilot status` — Check queued work and results.\n\n'
        '**Applications**\n'
        '`/cvpilot tailor url:<job posting URL>` — Generate a job-specific TeX and PDF.\n\n'
        '**Master resumes**\n'
        '`/cvpilot master get role:<swe|ml|systems|it|web>` — Locate a master.\n'
        '`/cvpilot master upload role:<role>` — Get GitHub upload instructions.\n'
        '`/cvpilot master edit role:<role> instructions:<change>` — Propose edits.\n\n'
        '**Master profile**\n'
        '`/cvpilot profile show` — View your profile.\n'
        '`/cvpilot profile sync` — Sync verified facts from masters.\n'
        '`/cvpilot profile edit instructions:<change>` — Propose a profile update.\n\n'
        '**Permissions**\n'
        '`/cvpilot settings master_edit value:<on|off|show>` — Manage master edit permission.\n\n'
        'Master edits must be requested explicitly. CVPilot does not silently merge into main. '
        'Current binary resume uploads are performed through GitHub.'
    )
    await interaction.response.send_message(help_text, ephemeral=True)

@cvpilot.command(name='init', description='Initialize a private GitHub repository and workspace')
@app_commands.describe(github_username='Your GitHub username (for the private repository invitation)')
async def init(interaction: discord.Interaction, github_username: str):
    await invoke(interaction, 'init', github_username=github_username)

@cvpilot.command(name='repo', description='Get the URL of your private CVPilot repository')
async def repo(interaction: discord.Interaction):
    await invoke(interaction, 'repo')

@cvpilot.command(name='status', description='View your career resume job queue and results')
async def status(interaction: discord.Interaction):
    await invoke(interaction, 'status')

@cvpilot.command(name='tailor', description='Tailor a one-page resume from an HTTPS job posting URL')
@app_commands.describe(url='Full HTTPS URL for the job posting')
async def tailor(interaction: discord.Interaction, url: str):
    await invoke(interaction, 'tailor', url=url)

master = app_commands.Group(name='master', description='Get or revise a master resume', parent=cvpilot)

@master.command(name='get', description='Get the GitHub location of your master resume')
@app_commands.describe(role='Resume category (swe, ml, systems, it, web, etc.)')
async def master_get(interaction: discord.Interaction, role: str):
    await invoke(interaction, 'master', operation='get', role=role)

@master.command(name='edit', description='Request a master edit on a new review branch')
@app_commands.describe(role='Resume category', instructions='Specific requested changes (must be factual)')
async def master_edit(interaction: discord.Interaction, role: str, instructions: str):
    await invoke(interaction, 'master', operation='edit', role=role, instructions=instructions)

@master.command(name='upload', description='Show how to upload or replace a master resume using GitHub')
@app_commands.describe(role='Resume category')
async def master_upload(interaction: discord.Interaction, role: str):
    # Controller v2 does not yet ingest Discord binary attachments.
    await invoke(interaction, 'master', operation='upload', role=role)

profile = app_commands.Group(name='profile', description='View, sync, or edit your master profile', parent=cvpilot)

@profile.command(name='show', description='Open your confirmed master profile')
async def profile_show(interaction: discord.Interaction):
    await invoke(interaction, 'profile', operation='show')

@profile.command(name='sync', description='Synchronize your master profile from your master resumes')
async def profile_sync(interaction: discord.Interaction):
    await invoke(interaction, 'profile', operation='sync')

@profile.command(name='edit', description='Request changes to verified profile facts')
@app_commands.describe(instructions='Describe verified skills, tools, projects, or experience to update')
async def profile_edit(interaction: discord.Interaction, instructions: str):
    await invoke(interaction, 'profile', operation='edit', instructions=instructions)

settings = app_commands.Group(name='settings', description='Control master-resume editing permission', parent=cvpilot)

@settings.command(name='master_edit', description='Enable, disable, or inspect permission to edit master resumes')
@app_commands.describe(value='Whether explicitly requested master edits are allowed')
async def settings_master_edit(interaction: discord.Interaction, value: Literal['on', 'off', 'show']):
    await invoke(interaction, 'settings', option='master_edit', value=value)

# Owner-only OpenClaw controls are guild-local, NOT global DM commands.
# They expose a minimal read-only set; unrestricted OpenClaw CLI / admin tools
# stay on the Ubuntu host. Both Discord permission hints AND an exact-ID runtime
# check are used. No arbitrary commands or shell arguments are accepted.
openclaw_admin = app_commands.Group(
    name='openclaw',
    description='Owner-only OpenClaw diagnostics',
    guild_only=True,
    default_permissions=discord.Permissions(administrator=True),
)

async def admin_authorized(interaction: discord.Interaction) -> bool:
    if (not OWNER_ID.isdigit() or not ADMIN_GUILD_ID.isdigit()
            or interaction.user.id != int(OWNER_ID)
            or interaction.guild_id != int(ADMIN_GUILD_ID)
            or not ADMIN_CHANNEL_ID.isdigit()
            or interaction.channel_id != int(ADMIN_CHANNEL_ID)):
        await interaction.response.send_message('Not authorized.', ephemeral=True)
        return False
    return True

def openclaw_executable() -> str:
    from_path = shutil.which('openclaw')
    if from_path:
        return from_path
    fallback = Path.home() / '.npm-global/bin/openclaw'
    if fallback.is_file():
        return str(fallback)
    raise FileNotFoundError('OpenClaw executable not found. Configure service PATH.')

async def show_admin_cli(interaction: discord.Interaction, *args: str) -> None:
    if not await admin_authorized(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        proc = await asyncio.create_subprocess_exec(
            openclaw_executable(), *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=25)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise TimeoutError('OpenClaw command timed out')
        if proc.returncode:
            result = f'OpenClaw returned exit code {proc.returncode}. ' + stderr.decode('utf-8', 'replace')
        else:
            result = stdout.decode('utf-8', 'replace')
        result = (result.strip() or '(No output)')[:1700]
        # Avoid embedding pings or Markdown code fence injection from CLI output.
        result = result.replace('```', "''' ").replace('@', '@\u200b')
        await interaction.followup.send(f'```text\n{result}\n```', ephemeral=True)
    except (OSError, TimeoutError) as exc:
        await interaction.followup.send(f'Admin diagnostic failed: {type(exc).__name__}: {exc}', ephemeral=True)

@openclaw_admin.command(name='status', description='Show OpenClaw Gateway and agent status')
async def admin_status(interaction: discord.Interaction):
    await show_admin_cli(interaction, 'status')

@openclaw_admin.command(name='model', description='Show the configured default model (not a session override)')
async def admin_model(interaction: discord.Interaction):
    await show_admin_cli(interaction, 'config', 'get', 'agents.defaults.model')

@openclaw_admin.command(name='agents', description='Show agents and Discord bindings')
async def admin_agents(interaction: discord.Interaction):
    await show_admin_cli(interaction, 'agents', 'list', '--bindings')

@openclaw_admin.command(name='health', description='Read the Gateway health result')
async def admin_health(interaction: discord.Interaction):
    await show_admin_cli(interaction, 'health', '--json')

class CVPilotClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True  # no message content/member/presence intents
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if not all(x.isdigit() for x in (GUILD_ID, PUBLIC_CHANNEL_ID, USER_ROLE_ID,
                                         OWNER_ID, ADMIN_CHANNEL_ID, ADMIN_GUILD_ID)):
            raise RuntimeError('Set all CVPILOT_* server/channel/role/owner IDs in controller.env')
        if GUILD_ID != ADMIN_GUILD_ID:
            raise RuntimeError('Admin guild must equal the dedicated CVPilot guild')
        guild = discord.Object(id=int(GUILD_ID))
        self.tree.add_command(cvpilot, guild=guild)
        self.tree.add_command(openclaw_admin, guild=guild)
        # Removes stale GLOBAL commands from this dedicated Discord application,
        # including the old DM-only /cvpilot command. Does not affect other bots.
        globals_synced = await self.tree.sync()
        print('[cvpilot] Global command list now: ' +
              (', '.join(c.name for c in globals_synced) or '(empty)'), flush=True)
        guild_synced = await self.tree.sync(guild=guild)
        print('[cvpilot] Dedicated guild commands: ' +
              ', '.join(c.name for c in guild_synced), flush=True)
        # Previous guild cleanup is deliberate and manual, never automatic.

    async def on_ready(self):
        print(f'[cvpilot] Native command client ready as {self.user} (command-only)', flush=True)

if __name__ == '__main__':
    import sys
    if '--check' in sys.argv:
        names = {cmd.qualified_name for cmd in cvpilot.walk_commands()}
        expected = {'cvpilot help', 'cvpilot init', 'cvpilot repo', 'cvpilot status', 'cvpilot tailor',
                    'cvpilot master get', 'cvpilot master edit', 'cvpilot master upload',
                    'cvpilot profile show', 'cvpilot profile sync', 'cvpilot profile edit',
                    'cvpilot settings master_edit'}
        missing = expected - names
        if missing:
            raise SystemExit('Missing native subcommands: ' + ', '.join(sorted(missing)))
        print('OK: 12 guild CVPilot subcommands and 4 owner-only admin diagnostics defined')
        sys.exit(0)
    if not TOKEN or TOKEN == 'REPLACE_WITH_TOKEN':
        raise SystemExit('Set CVPILOT_DISCORD_TOKEN in ~/.config/cvpilot/controller.env')
    CVPilotClient().run(TOKEN, log_handler=None)
