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
        'Use #cvpilot or DM CVPilot with your current server role.\n\n'
        '`/cvpilot init github_username:<name>` — Create a private repo.\n'
        '`/cvpilot repo` — Repository link.\n'
        '`/cvpilot status [job_id]` — One detailed job; arrows browse history.\n\n'
        '**Applications**\n'
        '`/cvpilot tailor url:<posting>` — One-page PDF/TeX by private DM.\n'
        '`/cvpilot download job_id:<id>` — Private files; omit ID for latest.\n'
        '`/cvpilot application delete link:<URL>` or `commit:<ID>` — Delete one application.\n\n'
        '**Master resumes**\n'
        '`/cvpilot master upload file:<PDF or TeX> label:<label> companion:<optional file>`\n'
        '`/cvpilot master get label:<label>` — Private source attachments.\n'
        '`/cvpilot master edit label:<label> instructions:<change>`\n'
        '`/cvpilot master delete label:<label>` or `commit:<ID>` — Delete one source.\n'
        'Labels default to general; use healthcare, sales or any label. Reusing a label replaces it.\n'
        'PDF/UTF-8 TeX: 8 MiB each. TeX-only uploads compile to PDF; companion supplies a matching pair. Wait for completion before tailoring.\n\n'
        '**Profile and history**\n'
        '`/cvpilot profile show` | `/cvpilot profile sync`\n'
        '`/cvpilot profile edit instructions:<facts>`\n'
        '`/cvpilot answer job_id:<id> question:<n> details:<experience>` — Review and confirm a profile fact.\n'
        '`/cvpilot continue job_id:<id>` — Resume after reviewing qualification questions.\n'
        '`/cvpilot history limit:<1–10>` — Main commits.\n'
        '`/cvpilot revert commit:<ID>` — Undo one commit; type REVERT.\n'
        '`/cvpilot reset` — Restore template and delete other branches; type RESET.\n'
        '`/cvpilot settings master_edit value:<on|off|show>` — Opt in to requested master edits.\n\n'
        'Deletions require a preview and typing DELETE. Files leave main and CVPilot downloads; Git history and sent attachments remain. Profile facts are kept. Changes commit to main; revert can restore deletions.'
    )
    await private_reply(interaction,help_text)

class ResetModal(discord.ui.Modal, title='Confirm repository reset'):
    verification=discord.ui.TextInput(label='Type RESET to confirm repository reset',
                                      placeholder='RESET',min_length=5,max_length=5,required=True)

    def __init__(self, view):
        super().__init__(timeout=120)
        self.reset_view=view

    async def on_submit(self, interaction: discord.Interaction):
        view=self.reset_view
        if interaction.user.id!=view.sender_id:
            await private_reply(interaction,f'Only the user who requested this {view.action} can confirm it.')
            return
        if not await public_authorized(interaction):return
        await defer_private(interaction)
        try:
            response=await request_controller(interaction.user.id,view.action,
                {'operation':'confirm','token':view.token,'confirmation':self.verification.value})
            await private_reply(interaction,str(response.get('text','No response from CVPilot')))
            if response.get('ok'):await view.close_prompt()
        except (OSError,TimeoutError,ValueError,RuntimeError) as exc:
            print(f'[cvpilot] {view.action} confirmation error: {type(exc).__name__}: {exc}',flush=True)
            await private_reply(interaction,f'Could not confirm the {view.action}. Check /cvpilot status before trying again.')


class RevertModal(ResetModal, title='Confirm commit revert'):
    verification=discord.ui.TextInput(label='Type REVERT to undo this commit',placeholder='REVERT',
                                      min_length=6,max_length=6,required=True)


class DeleteModal(ResetModal, title='Confirm resume deletion'):
    verification=discord.ui.TextInput(label='Type DELETE to delete this resume',placeholder='DELETE',
                                      min_length=6,max_length=6,required=True)


class AnswerModal(ResetModal, title='Confirm profile experience'):
    verification=discord.ui.TextInput(label='Type CONFIRM to save your statement',placeholder='CONFIRM',
                                      min_length=7,max_length=7,required=True)


class ResetView(discord.ui.View):
    action='reset'
    modal_type=ResetModal
    def __init__(self, sender_id: int, token: str):
        super().__init__(timeout=120)
        self.sender_id=sender_id;self.token=token;self.message=None

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id!=self.sender_id:
            await private_reply(interaction,f'Only the user who requested this {self.action} can use this confirmation.')
            return False
        return True

    async def close_prompt(self):
        for item in self.children:item.disabled=True
        self.stop()
        if self.message is not None:
            try:await self.message.edit(view=self)
            except discord.HTTPException:pass

    async def on_timeout(self):
        await self.close_prompt()

    @discord.ui.button(label='Reset repository',style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.interaction_check(interaction):return
        # Opening a modal must be the initial response. Permissions are checked
        # again when the user submits the typed confirmation.
        await interaction.response.send_modal(self.modal_type(self))

    @discord.ui.button(label='Cancel',style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.interaction_check(interaction):return
        await defer_private(interaction)
        try:
            response=await request_controller(interaction.user.id,self.action,{'operation':'cancel','token':self.token})
            await private_reply(interaction,str(response.get('text','No response from CVPilot')))
            await self.close_prompt()
        except (OSError,TimeoutError,ValueError,RuntimeError) as exc:
            print(f'[cvpilot] {self.action} cancellation error: {type(exc).__name__}: {exc}',flush=True)
            await private_reply(interaction,'Could not cancel this confirmation. Check /cvpilot status; confirmations expire after 2 minutes.')


@cvpilot.command(name='reset', description='Restore the initial repository template and delete other branches after confirmation')
async def reset(interaction: discord.Interaction):
    await prepare_repository_change(interaction,'reset',ResetView)


class RevertView(ResetView):
    action='revert'
    modal_type=RevertModal

    @discord.ui.button(label='Revert commit',style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.interaction_check(interaction):return
        await interaction.response.send_modal(self.modal_type(self))


class MasterDeleteView(ResetView):
    action='master-delete'
    modal_type=DeleteModal

    @discord.ui.button(label='Delete resume',style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.interaction_check(interaction):return
        await interaction.response.send_modal(self.modal_type(self))


class ApplicationDeleteView(MasterDeleteView):
    action='application-delete'


class AnswerView(ResetView):
    action='answer'
    modal_type=AnswerModal

    @discord.ui.button(label='Save to profile',style=discord.ButtonStyle.primary)
    async def confirm(self,interaction:discord.Interaction,button:discord.ui.Button):
        if not await self.interaction_check(interaction):return
        await interaction.response.send_modal(self.modal_type(self))


async def prepare_repository_change(interaction, action, view_type, **args):
    if not await public_authorized(interaction):return
    await defer_private(interaction)
    try:
        response=await request_controller(interaction.user.id,action,{'operation':'prepare',**args})
        if not response.get('ok'):
            await private_reply(interaction,str(response.get('text','Operation is unavailable')))
            return
        view=view_type(interaction.user.id,response['confirmation_token'])
        view.message=await interaction.followup.send(str(response['text']),ephemeral=True,view=view,wait=True,
                                                    allowed_mentions=discord.AllowedMentions.none())
    except (OSError,TimeoutError,ValueError,RuntimeError) as exc:
        print(f'[cvpilot] {action} preparation error: {type(exc).__name__}: {exc}',flush=True)
        await private_reply(interaction,f'Could not prepare the {action}. Check /cvpilot status or contact the operator.')


@cvpilot.command(name='history', description='List recent commits on your repository main branch')
@app_commands.describe(limit='Number of recent commits to show (1–10)')
async def history(interaction: discord.Interaction, limit: app_commands.Range[int,1,10] = 8):
    await invoke(interaction,'history',limit=limit)


@cvpilot.command(name='revert', description='Undo one main commit with a new commit after confirmation')
@app_commands.describe(commit='Commit ID from /cvpilot history (at least 7 characters)')
async def revert(interaction: discord.Interaction, commit: str):
    await prepare_repository_change(interaction,'revert',RevertView,commit=commit)


@cvpilot.command(name='init', description='Initialize a private GitHub repository and workspace')
@app_commands.describe(github_username='Your GitHub username (for the private repository invitation)')
async def init(interaction: discord.Interaction, github_username: str):
    await invoke(interaction, 'init', github_username=github_username)

@cvpilot.command(name='repo', description='Get the URL of your private CVPilot repository')
async def repo(interaction: discord.Interaction):
    await invoke(interaction, 'repo')

def status_message(result):
    """Keep the full diagnostic text in an attachment, outside embed limits."""
    detail=result['status_detail']
    colors={'failed':0xe74c3c,'done':0x2ecc71,'running':0x3498db,'queued':0xf1c40f}
    embed=discord.Embed(title=detail['title'],description=detail['description'],
                        color=colors.get(detail['state'],0x5865f2))
    for field in detail['fields']:
        embed.add_field(name=field['name'],value=field['value'],inline=False)
    embed.set_footer(text='Arrows browse your jobs. Refresh updates this job. Full details are attached. Buttons expire after 10 minutes; run /cvpilot status again.')
    file=discord.File(io.BytesIO(detail['report'].encode('utf-8')),
                      filename=f'cvpilot-job-{result["status_view"]["job_id"]}-status.txt')
    return embed,file


class StatusView(discord.ui.View):
    def __init__(self,sender_id,result):
        super().__init__(timeout=600)
        self.sender_id=sender_id;self.message=None;self.lock=asyncio.Lock()
        self.update_cursor(result)

    def update_cursor(self,result):
        page=result['status_view'];self.job_id=page['job_id']
        self.older.disabled=page['older'] is None
        self.newer.disabled=page['newer'] is None

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.sender_id:
            await private_reply(interaction,'Only the user who opened this status view can browse it.')
            return False
        return True

    async def show(self,interaction,direction=None):
        if not await self.interaction_check(interaction):return
        # Acknowledge first: current membership checks in DMs may take seconds.
        if not interaction.response.is_done():await interaction.response.defer()
        if not await public_authorized(interaction):return
        async with self.lock:
            if self.is_finished():
                await private_reply(interaction,'This status view expired. Run /cvpilot status again.')
                return
            args={'job_id':self.job_id}
            if direction:args['direction']=direction
            try:
                result=await request_controller(interaction.user.id,'status',args)
                if not result.get('ok') or not result.get('status_detail'):
                    await private_reply(interaction,str(result.get('text','Status is unavailable. Run /cvpilot status again.')))
                    return
                embed,file=status_message(result)
                previous_id=self.job_id
                previous_disabled=[item.disabled for item in self.children]
                self.update_cursor(result)
                try:
                    await interaction.edit_original_response(content=None,embed=embed,attachments=[file],view=self,
                        allowed_mentions=discord.AllowedMentions.none())
                except Exception:
                    self.job_id=previous_id
                    for item,disabled in zip(self.children,previous_disabled):item.disabled=disabled
                    raise
                finally:file.close()
            except (OSError,TimeoutError,RuntimeError,ValueError,discord.HTTPException) as exc:
                print(f'[cvpilot] status navigation error: {type(exc).__name__}: {exc}',flush=True)
                await private_reply(interaction,'Could not refresh this status. The displayed job has not changed. Try Refresh or run /cvpilot status again.')

    @discord.ui.button(label='Older',emoji='⬅️',style=discord.ButtonStyle.secondary)
    async def older(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self.show(interaction,'older')

    @discord.ui.button(label='Refresh',emoji='🔄',style=discord.ButtonStyle.primary)
    async def refresh(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self.show(interaction)

    @discord.ui.button(label='Newer',emoji='➡️',style=discord.ButtonStyle.secondary)
    async def newer(self,interaction:discord.Interaction,button:discord.ui.Button):
        await self.show(interaction,'newer')

    async def on_timeout(self):
        async with self.lock:
            for item in self.children:item.disabled=True
            self.stop()
            if self.message is not None:
                try:await self.message.edit(view=self)
                except discord.HTTPException:pass


@cvpilot.command(name='status', description='View one detailed job with errors, timing, and history arrows')
@app_commands.describe(job_id='Your job ID; omit to show your latest job')
async def status(interaction: discord.Interaction, job_id: int | None = None):
    if not await public_authorized(interaction):return
    await defer_private(interaction)
    try:
        result=await request_controller(interaction.user.id,'status',{'job_id':job_id})
        if not result.get('ok') or not result.get('status_detail'):
            await send_controller_response(interaction,result)
            return
        view=StatusView(interaction.user.id,result)
        embed,file=status_message(result)
        try:
            view.message=await interaction.followup.send(embed=embed,file=file,view=view,ephemeral=True,wait=True,
                allowed_mentions=discord.AllowedMentions.none())
        finally:file.close()
    except (OSError,TimeoutError,RuntimeError,ValueError,discord.HTTPException) as exc:
        print(f'[cvpilot] status error: {type(exc).__name__}: {exc}',flush=True)
        await private_reply(interaction,'Could not load job status. Try /cvpilot status again shortly.')

@cvpilot.command(name='tailor', description='Tailor a one-page resume from an HTTPS job posting URL')
@app_commands.describe(url='Full HTTPS URL for the job posting')
async def tailor(interaction: discord.Interaction, url: str):
    await invoke(interaction, 'tailor', url=url)


@cvpilot.command(name='answer',description='Answer a tailoring question and review a fact before saving it to your profile')
@app_commands.describe(job_id='Tailoring job that asked the question',question='Question number shown in the job status',
                       details='Describe your actual experience in your own words; no skill is assumed from yes/no')
async def answer(interaction:discord.Interaction,job_id:int,question:app_commands.Range[int,1,3],details:str):
    await prepare_repository_change(interaction,'answer',AnswerView,job_id=job_id,question=question,details=details)


@cvpilot.command(name='continue',description='Continue a tailoring job after reviewing its qualification questions')
@app_commands.describe(job_id='Your tailoring job awaiting answers; unanswered qualifications stay unconfirmed')
async def continue_tailoring(interaction:discord.Interaction,job_id:int):
    await invoke(interaction,'continue',job_id=job_id)

@cvpilot.command(name='download', description='Download your completed resume files privately as attachments')
@app_commands.describe(job_id='Your completed job ID; omit to download your latest resume')
async def download(interaction: discord.Interaction, job_id: int | None = None):
    await invoke(interaction,'download',job_id=job_id)

master = app_commands.Group(name='master', description='Upload, download, or revise source resumes for any field', parent=cvpilot)

@master.command(name='delete', description='Delete one master source resume after reviewing and confirming')
@app_commands.describe(label='Resume label to delete; supply either label or commit',
                       commit='Commit ID identifying one master resume; at least 7 characters')
async def master_delete(interaction: discord.Interaction, label: str | None = None, commit: str | None = None):
    await prepare_repository_change(interaction,'master-delete',MasterDeleteView,label=label,commit=commit)


application = app_commands.Group(name='application', description='Manage saved tailored application resumes', parent=cvpilot)

@application.command(name='delete', description='Delete one tailored application resume after reviewing and confirming')
@app_commands.describe(link='CVPilot GitHub result/file/folder link or original posting URL; supply link or commit',
                       commit='Commit ID identifying one tailored application; at least 7 characters')
async def application_delete(interaction: discord.Interaction, link: str | None = None, commit: str | None = None):
    await prepare_repository_change(interaction,'application-delete',ApplicationDeleteView,link=link,commit=commit)

@master.command(name='get', description='Download your saved source resume as private attachments')
@app_commands.describe(label='Any resume label, such as general, teaching, healthcare, or sales')
async def master_get(interaction: discord.Interaction, label: str = 'general'):
    await invoke(interaction, 'master', operation='get', role=label)

@master.command(name='edit', description='Request a source-resume edit committed to main after validation')
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
        expected = {'cvpilot help', 'cvpilot init', 'cvpilot repo', 'cvpilot status', 'cvpilot tailor', 'cvpilot answer', 'cvpilot continue', 'cvpilot download', 'cvpilot reset', 'cvpilot history', 'cvpilot revert',
                    'cvpilot master get', 'cvpilot master edit', 'cvpilot master upload', 'cvpilot master delete', 'cvpilot application delete',
                    'cvpilot profile show', 'cvpilot profile sync', 'cvpilot profile edit',
                    'cvpilot settings master_edit'}
        missing = expected - names
        if missing:
            raise SystemExit('Missing native subcommands: ' + ', '.join(sorted(missing)))
        print('OK: 20 server/DM CVPilot subcommands and 4 server-only admin diagnostics defined')
        sys.exit(0)
    if not TOKEN or TOKEN == 'REPLACE_WITH_TOKEN':
        raise SystemExit('Set CVPILOT_DISCORD_TOKEN in ~/.config/cvpilot/controller.env')
    CVPilotClient().run(TOKEN, log_handler=None)
