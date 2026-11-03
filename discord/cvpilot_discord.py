#!/usr/bin/env python3
"""
Native CVPilot Discord slash-command tree. Sends authenticated user IDs to the
existing CVPilot v2 Unix-socket controller. Uses no LLM, GitHub credentials,
shared resume filesystem, or Discord message-content privileges. Attachments are
transferred through the authenticated local controller socket.
Use 'python3 -m py_compile   ~/.local/share/cvpilot/app/controller/main.py' for recompilation
"""
import asyncio
import base64
import io
import sys
import json
import os
import shutil
from pathlib import Path
from typing import Literal

import discord
from discord import app_commands

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app/controller'))
from resumes import MAX_FILE_BYTES, MAX_PACKET_BYTES, validate_files, label as validate_label

SOCKET = os.environ.get('CVPILOT_SOCKET', str(Path.home() / '.local/state/cvpilot/controller.sock'))
TOKEN = os.environ.get('CVPILOT_DISCORD_TOKEN', '')
GUILD_ID = os.environ.get('CVPILOT_GUILD_ID', '').strip()
PUBLIC_CHANNEL_ID = os.environ.get('CVPILOT_CHANNEL_ID', '').strip()
USER_ROLE_ID = os.environ.get('CVPILOT_USER_ROLE_ID', '').strip()
ADMIN_CHANNEL_ID = os.environ.get('CVPILOT_ADMIN_CHANNEL_ID', '').strip()
CLEAR_GUILD = os.environ.get('CVPILOT_CLEAR_GUILD_COMMANDS', '0') == '1'
OWNER_ID = os.environ.get('CVPILOT_OWNER_DISCORD_ID', '').strip()
ADMIN_GUILD_ID = os.environ.get('CVPILOT_ADMIN_GUILD_ID', GUILD_ID).strip()

async def request_controller(uid: int, action: str, args: dict) -> dict:
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(SOCKET,limit=MAX_PACKET_BYTES+1), timeout=3)
    try:
        packet = json.dumps({'sender_id': str(uid), 'action': action, 'args': args}) + '\n'
        encoded=packet.encode('utf8')
        if len(encoded)>MAX_PACKET_BYTES:raise ValueError('Resume transfer is too large')
        writer.write(encoded)
        await asyncio.wait_for(writer.drain(), timeout=60)
        raw = await asyncio.wait_for(reader.readline(), timeout=75)
        if not raw or len(raw) > MAX_PACKET_BYTES:
            raise RuntimeError('Invalid or empty controller response')
        result = json.loads(raw)
        return result
    finally:
        writer.close()
        await writer.wait_closed()

async def defer_private(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True,thinking=True)


async def private_reply(interaction: discord.Interaction, text: str) -> None:
    kwargs={'ephemeral':True,'allowed_mentions':discord.AllowedMentions.none()}
    if interaction.response.is_done():
        await interaction.followup.send(text,**kwargs)
    else:
        await interaction.response.send_message(text,**kwargs)


async def public_authorized(interaction: discord.Interaction) -> bool:
    # Membership and role grants originate in the dedicated server. DM checks
    # fetch current membership rather than trusting a permanent approval/cache.
    if not all(value.isdigit() for value in (GUILD_ID,PUBLIC_CHANNEL_ID,USER_ROLE_ID)):
        await private_reply(interaction,'CVPilot server access is not configured correctly. Contact the operator.')
        return False
    owner=OWNER_ID.isdigit() and interaction.user.id==int(OWNER_ID)
    if interaction.guild_id is not None:
        if interaction.guild_id!=int(GUILD_ID) or interaction.channel_id!=int(PUBLIC_CHANNEL_ID):
            await private_reply(interaction,'Use the designated #cvpilot server channel or a direct message to CVPilot.')
            return False
        member=interaction.user
    else:
        if getattr(interaction.channel,'type',None)!=discord.ChannelType.private:
            await private_reply(interaction,'Use a direct message to CVPilot or the designated server channel.')
            return False
        # A REST role check can take longer than Discord's initial-response limit.
        await defer_private(interaction)
        async def fetch_member():
            guild=interaction.client.get_guild(int(GUILD_ID))
            if guild is None:guild=await interaction.client.fetch_guild(int(GUILD_ID))
            return await guild.fetch_member(interaction.user.id)
        try:
            member=await asyncio.wait_for(fetch_member(),timeout=10)
        except discord.NotFound:
            await private_reply(interaction,'Join the CVPilot server and get the CVPilot User role before using commands in DMs.')
            return False
        except (discord.HTTPException,OSError,TimeoutError) as exc:
            print(f'[cvpilot] DM authorization failed: {type(exc).__name__}: {exc}',flush=True)
            await private_reply(interaction,'Unable to verify your CVPilot server access right now. Try again shortly or use the server channel.')
            return False
    if not owner and int(USER_ROLE_ID) not in [getattr(role,'id',None) for role in getattr(member,'roles',[])]:
        await private_reply(interaction,'Access requires the CVPilot User role in the CVPilot server. Ask a server administrator for access.')
        return False
    return True


async def send_controller_response(interaction: discord.Interaction, result: dict) -> None:
    text=str(result.get('text','No response from CVPilot controller'))[:1900]
    files=[]
    attachments=result.get('attachments',[]) if result.get('ok',True) else []
    if not isinstance(attachments,list) or len(attachments)>2:
        raise ValueError('Invalid resume download response')
    if attachments:
        decoded=validate_files(attachments)
        limit=getattr(interaction,'filesize_limit',MAX_FILE_BYTES)
        if not isinstance(limit,int):limit=MAX_FILE_BYTES
        if any(len(data)>min(limit,MAX_FILE_BYTES) for data in decoded.values()):
            text+='\nDiscord cannot attach a file of this size here. Use the private GitHub link.'
        else:
            files=[discord.File(io.BytesIO(data),filename=name) for name,data in decoded.items()]
    try:
        await interaction.followup.send(text,ephemeral=True,files=files,
                                         allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        if not files:raise
        await interaction.followup.send(text+'\nDiscord could not attach the files. Try /cvpilot download or use the private GitHub link.',
                                         ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
    finally:
        for file in files:file.close()


async def invoke(interaction: discord.Interaction, action: str, **args) -> None:
    # Discord provides interaction.user.id; never read a caller ID from user text.
    if not await public_authorized(interaction):
        return
    await defer_private(interaction)
    try:
        response = await request_controller(interaction.user.id, action, args)
        await send_controller_response(interaction,response)
    except (OSError, TimeoutError, RuntimeError, ValueError, discord.HTTPException) as exc:
        print(f'[cvpilot] command error: {type(exc).__name__}: {exc}', flush=True)
        await interaction.followup.send('CVPilot request failed. Check /cvpilot status or contact the operator.',
                                        ephemeral=True,allowed_mentions=discord.AllowedMentions.none())

# These are genuine typed Discord slash subcommands, not a generic 'input' box.
cvpilot = app_commands.Group(
    name='cvpilot',description='Manage your private CVPilot resumes',
    allowed_contexts=app_commands.AppCommandContext(guild=True,dm_channel=True,private_channel=False),
    allowed_installs=app_commands.AppInstallationType(guild=True,user=False),
)
# Register both globally (for bot DMs) and in the dedicated server. Runtime
# checks still require membership and the server-issued role for every command.

@cvpilot.command(name='help', description='Show CVPilot commands and usage examples')
async def help_command(interaction: discord.Interaction):
    if not await public_authorized(interaction):
        return
    help_text = (
        '**CVPilot — Quick Help**\n'
        'Use commands in #cvpilot or DM CVPilot after receiving the CVPilot User role. Server membership and permissions are checked in DMs.\n\n'
        '**Getting started**\n'
        '`/cvpilot init github_username:<your GitHub username>` — Create your private repo.\n'
        '`/cvpilot repo` — View the repository link.\n'
        '`/cvpilot status` — Check queued work and results.\n\n'
        '**Applications**\n'
        '`/cvpilot tailor url:<job posting URL>` — Get a tailored one-page PDF and TeX by private DM.\n'
        '`/cvpilot download job_id:<id>` — Download completed resume files here privately; omit the ID for the latest.\n\n'
        '**Master resumes**\n'
        '`/cvpilot master get label:<label>` — Download your saved source resume here privately.\n'
        '`/cvpilot master upload file:<PDF or TeX> label:<label> companion:<optional matching file>` — Upload a source resume.\n'
        '`/cvpilot master edit label:<label> instructions:<change>` — Propose edits.\n'
        'Labels default to `general`. Choose any label: teaching, healthcare, sales, arts, or your own. Reusing a label explicitly replaces its active uploaded version.\n\n'
        '**Master profile**\n'
        '`/cvpilot profile show` — View your profile.\n'
        '`/cvpilot profile sync` — Sync verified facts from masters.\n'
        '`/cvpilot profile edit instructions:<change>` — Propose a profile update.\n\n'
        '**Permissions**\n'
        '`/cvpilot settings master_edit value:<on|off|show>` — Manage master edit permission.\n\n'
        'Master edits must be requested explicitly. CVPilot does not silently merge into main. '
        'PDF and UTF-8 TeX attachments are supported, up to 8 MiB each. Use companion to upload a matching PDF/TeX pair together. '
        'New uploads are available after their job completes. GitHub keeps review history; no manual merge is needed to start tailoring. '
        'If DMs are blocked, use /cvpilot download. DOCX uploads are not supported.'
    )
    await private_reply(interaction,help_text)

@cvpilot.command(name='init', description='Initialize a private GitHub repository and workspace')
@app_commands.describe(github_username='Your GitHub username (for the private repository invitation)')
async def init(interaction: discord.Interaction, github_username: str):
    await invoke(interaction, 'init', github_username=github_username)

@cvpilot.command(name='repo', description='Get the URL of your private CVPilot repository')
async def repo(interaction: discord.Interaction):
    await invoke(interaction, 'repo')

@cvpilot.command(name='status', description='View your CVPilot job queue and results')
async def status(interaction: discord.Interaction):
    await invoke(interaction, 'status')

@cvpilot.command(name='tailor', description='Tailor a one-page resume from an HTTPS job posting URL')
@app_commands.describe(url='Full HTTPS URL for the job posting')
async def tailor(interaction: discord.Interaction, url: str):
    await invoke(interaction, 'tailor', url=url)

@cvpilot.command(name='download', description='Download your completed resume files privately as attachments')
@app_commands.describe(job_id='Your completed job ID; omit to download your latest resume')
async def download(interaction: discord.Interaction, job_id: int | None = None):
    await invoke(interaction,'download',job_id=job_id)

master = app_commands.Group(name='master', description='Upload, download, or revise source resumes for any field', parent=cvpilot)

@master.command(name='get', description='Download your saved source resume as private attachments')
@app_commands.describe(label='Any resume label, such as general, teaching, healthcare, or sales')
async def master_get(interaction: discord.Interaction, label: str = 'general'):
    await invoke(interaction, 'master', operation='get', role=label)

@master.command(name='edit', description='Request a source-resume edit on a new review branch')
@app_commands.describe(label='Your resume label; default general', instructions='Specific requested changes (must be factual)')
async def master_edit(interaction: discord.Interaction, instructions: str, label: str = 'general'):
    await invoke(interaction, 'master', operation='edit', role=label, instructions=instructions)

@master.command(name='upload', description='Upload a PDF or TeX source resume for any profession')
@app_commands.describe(file='PDF or UTF-8 TeX resume, up to 8 MiB', label='Any lowercase resume label; default general',
                       companion='Optional matching PDF or TeX file, up to 8 MiB')
async def master_upload(interaction: discord.Interaction, file: discord.Attachment,
                        label: str = 'general', companion: discord.Attachment | None = None):
    if not await public_authorized(interaction):return
    await defer_private(interaction)
    try:
        validate_label(label)
        attachments=[file]+([companion] if companion is not None else [])
        for attachment in attachments:
            if not 0 < attachment.size <= MAX_FILE_BYTES:
                raise ValueError('Each resume attachment must be nonempty and at most 8 MiB.')
            if Path(attachment.filename).suffix.lower() not in ('.pdf','.tex'):
                raise ValueError('Resume attachments must be PDF or TeX files.')
        contents=await asyncio.gather(*(asyncio.wait_for(item.read(),timeout=60) for item in attachments))
        files=[{'filename':item.filename,'content_base64':base64.b64encode(data).decode('ascii')}
               for item,data in zip(attachments,contents)]
        validate_files(files)
        response=await request_controller(interaction.user.id,'master',
                                           {'operation':'upload','role':label,'files':files})
    except (OSError,TimeoutError,RuntimeError,ValueError,discord.HTTPException) as exc:
        print(f'[cvpilot] upload error: {type(exc).__name__}: {exc}',flush=True)
        response={'text':str(exc) if isinstance(exc,ValueError) else 'Resume upload failed. Please try again or contact the operator.'}
    await send_controller_response(interaction,response)

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
        self.tree.add_command(cvpilot)
        self.tree.add_command(cvpilot, guild=guild)
        self.tree.add_command(openclaw_admin, guild=guild)
        # Global CVPilot commands enable bot DMs. Admin diagnostics are only
        # included in the dedicated guild sync and remain server/channel scoped.
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
        expected = {'cvpilot help', 'cvpilot init', 'cvpilot repo', 'cvpilot status', 'cvpilot tailor', 'cvpilot download',
                    'cvpilot master get', 'cvpilot master edit', 'cvpilot master upload',
                    'cvpilot profile show', 'cvpilot profile sync', 'cvpilot profile edit',
                    'cvpilot settings master_edit'}
        missing = expected - names
        if missing:
            raise SystemExit('Missing native subcommands: ' + ', '.join(sorted(missing)))
        print('OK: 13 server/DM CVPilot subcommands and 4 server-only admin diagnostics defined')
        sys.exit(0)
    if not TOKEN or TOKEN == 'REPLACE_WITH_TOKEN':
        raise SystemExit('Set CVPILOT_DISCORD_TOKEN in ~/.config/cvpilot/controller.env')
    CVPilotClient().run(TOKEN, log_handler=None)
