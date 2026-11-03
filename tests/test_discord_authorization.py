"""Current-role DM authorization; all Discord and controller calls are mocked."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock

APP=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('cvpilot_dm_listener',APP.parent/'discord/cvpilot_discord.py')
listener=importlib.util.module_from_spec(spec);spec.loader.exec_module(listener)
discord=listener.discord
UID=123456789012345678
GUILD=223456789012345678
CHANNEL=323456789012345678
ROLE=423456789012345678
OWNER=523456789012345678


class AuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name,value in [('GUILD_ID',GUILD),('PUBLIC_CHANNEL_ID',CHANNEL),('USER_ROLE_ID',ROLE),
                           ('OWNER_ID',OWNER),('ADMIN_GUILD_ID',GUILD),('ADMIN_CHANNEL_ID',CHANNEL+1)]:
            patch=mock.patch.object(listener,name,str(value));patch.start();self.addCleanup(patch.stop)
        self.done=False
        self.interaction=mock.Mock()
        self.interaction.user.id=UID
        self.interaction.user.roles=[]
        self.interaction.guild_id=None
        self.interaction.channel.type=discord.ChannelType.private
        self.interaction.response.is_done.side_effect=lambda:self.done
        async def acknowledge(**kwargs):self.done=True
        async def respond(*args,**kwargs):self.done=True
        self.interaction.response.defer=mock.AsyncMock(side_effect=acknowledge)
        self.interaction.response.send_message=mock.AsyncMock(side_effect=respond)
        self.interaction.followup.send=mock.AsyncMock()
        self.interaction.client.fetch_guild=mock.AsyncMock()
        self.member=mock.Mock(roles=[mock.Mock(id=ROLE)])
        self.guild=mock.Mock()
        self.guild.fetch_member=mock.AsyncMock(return_value=self.member)
        self.interaction.client.get_guild.return_value=self.guild
        self.interaction.client.fetch_guild.return_value=self.guild

    def server(self,roles=None):
        self.interaction.guild_id=GUILD;self.interaction.channel_id=CHANNEL
        self.interaction.user.roles=[mock.Mock(id=value) for value in (roles if roles is not None else [ROLE])]

    async def test_current_role_holder_can_use_dms_without_database_registration(self):
        self.assertTrue(await listener.public_authorized(self.interaction))
        self.interaction.client.get_guild.assert_called_once_with(GUILD)
        self.guild.fetch_member.assert_awaited_once_with(UID)
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True,thinking=True)

    async def test_dm_member_lookup_occurs_after_acknowledgement(self):
        async def fetch(uid):
            self.assertTrue(self.done)
            return self.member
        self.guild.fetch_member.side_effect=fetch
        self.assertTrue(await listener.public_authorized(self.interaction))

    async def test_dm_fetches_uncached_server_and_current_membership(self):
        self.interaction.client.get_guild.return_value=None
        self.assertTrue(await listener.public_authorized(self.interaction))
        self.interaction.client.fetch_guild.assert_awaited_once_with(GUILD)
        self.guild.fetch_member.assert_awaited_once_with(UID)

    async def test_dm_role_revocation_takes_effect_on_next_command(self):
        self.assertTrue(await listener.public_authorized(self.interaction))
        self.member.roles=[]
        self.assertFalse(await listener.public_authorized(self.interaction))
        self.assertEqual(self.guild.fetch_member.await_count,2)
        self.assertIn('requires the CVPilot User role',self.interaction.followup.send.call_args.args[0])

    async def test_cached_role_or_user_supplied_roles_cannot_grant_dm_access(self):
        self.interaction.user.roles=[mock.Mock(id=ROLE)]
        self.guild.get_member.return_value=self.member
        self.guild.fetch_member.return_value=mock.Mock(roles=[])
        self.assertFalse(await listener.public_authorized(self.interaction))
        self.guild.get_member.assert_not_called()

    async def test_departed_or_unknown_member_cannot_use_dms(self):
        response=mock.Mock(status=404,reason='Not Found')
        self.guild.fetch_member.side_effect=discord.NotFound(response,{'message':'Unknown Member','code':10007})
        self.assertFalse(await listener.public_authorized(self.interaction))
        self.assertIn('Join the CVPilot server',self.interaction.followup.send.call_args.args[0])
        self.assertTrue(self.interaction.followup.send.call_args.kwargs['ephemeral'])

    async def test_member_lookup_forbidden_http_failure_and_timeout_deny_access(self):
        response=mock.Mock(status=403,reason='Forbidden')
        errors=[discord.Forbidden(response,'Missing Access'),discord.HTTPException(response,'API unavailable'),TimeoutError('lookup timed out')]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.guild.fetch_member.side_effect=error
                with mock.patch('builtins.print'):
                    self.assertFalse(await listener.public_authorized(self.interaction))
                self.assertIn('Unable to verify',self.interaction.followup.send.call_args.args[0])

    async def test_group_dms_and_unknown_private_contexts_are_rejected(self):
        for kind in (discord.ChannelType.group,None):
            self.interaction.channel.type=kind
            self.assertFalse(await listener.public_authorized(self.interaction))
        self.guild.fetch_member.assert_not_awaited()

    async def test_server_role_holder_still_uses_designated_channel(self):
        self.server()
        self.assertTrue(await listener.public_authorized(self.interaction))
        self.interaction.response.defer.assert_not_awaited()
        self.guild.fetch_member.assert_not_awaited()

    async def test_server_missing_role_wrong_channel_and_wrong_server_are_rejected(self):
        self.server(roles=[])
        self.assertFalse(await listener.public_authorized(self.interaction))
        self.server();self.interaction.channel_id=CHANNEL+2
        self.assertFalse(await listener.public_authorized(self.interaction))
        self.server();self.interaction.guild_id=GUILD+2
        self.assertFalse(await listener.public_authorized(self.interaction))
        self.guild.fetch_member.assert_not_awaited()

    async def test_owner_retains_role_override_but_dm_membership_is_verified(self):
        self.interaction.user.id=OWNER
        self.member.roles=[]
        self.assertTrue(await listener.public_authorized(self.interaction))
        self.guild.fetch_member.assert_awaited_once_with(OWNER)

    async def test_invalid_configuration_cannot_grant_dm_access(self):
        with mock.patch.object(listener,'USER_ROLE_ID',''):
            self.assertFalse(await listener.public_authorized(self.interaction))
        self.guild.fetch_member.assert_not_awaited()

    async def test_authorized_dm_command_is_deferred_only_once(self):
        with mock.patch.object(listener,'request_controller',new=mock.AsyncMock(return_value={'ok':True,'text':'No jobs yet.'})) as controller:
            await listener.status.callback(self.interaction)
        self.interaction.response.defer.assert_awaited_once()
        controller.assert_awaited_once_with(UID,'status',{})
        self.interaction.followup.send.assert_awaited_once()
        self.assertTrue(self.interaction.followup.send.call_args.kwargs['ephemeral'])

    async def test_dm_help_uses_followup_after_membership_check(self):
        await listener.help_command.callback(self.interaction)
        self.interaction.response.send_message.assert_not_awaited()
        self.assertIn('DM CVPilot',self.interaction.followup.send.call_args.args[0])
        self.assertLessEqual(len(self.interaction.followup.send.call_args.args[0]),2000)

    async def test_dm_upload_is_gated_before_reading_attachment(self):
        self.member.roles=[]
        file=mock.Mock(filename='cv.pdf',size=12);file.read=mock.AsyncMock(return_value=b'%PDF-1.4\n')
        with mock.patch.object(listener,'request_controller',new=mock.AsyncMock()) as controller:
            await listener.master_upload.callback(self.interaction,file)
        file.read.assert_not_awaited();controller.assert_not_awaited()

    async def test_authorized_dm_upload_acknowledges_once_and_uses_actual_sender(self):
        file=mock.Mock(filename='cv.pdf',size=12);file.read=mock.AsyncMock(return_value=b'%PDF-1.4\n')
        with mock.patch.object(listener,'request_controller',new=mock.AsyncMock(return_value={'ok':True,'text':'Queued'})) as controller:
            await listener.master_upload.callback(self.interaction,file)
        self.interaction.response.defer.assert_awaited_once()
        self.assertEqual(controller.call_args.args[0],UID)
        self.assertTrue(self.interaction.followup.send.call_args.kwargs['ephemeral'])

    async def test_owner_admin_commands_are_never_authorized_in_dms(self):
        self.interaction.user.id=OWNER
        self.assertFalse(await listener.admin_authorized(self.interaction))
        self.interaction.response.send_message.assert_awaited_once()


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_dms_and_server_commands_registered_without_global_admin(self):
        values={'GUILD_ID':str(GUILD),'PUBLIC_CHANNEL_ID':str(CHANNEL),'USER_ROLE_ID':str(ROLE),
                'OWNER_ID':str(OWNER),'ADMIN_GUILD_ID':str(GUILD),'ADMIN_CHANNEL_ID':str(CHANNEL+1)}
        with mock.patch.multiple(listener,**values):
            client=listener.CVPilotClient()
            try:
                async def sync(*,guild=None):
                    return client.tree.get_commands(guild=guild)
                with mock.patch.object(client.tree,'sync',side_effect=sync) as sync_mock,mock.patch('builtins.print'):
                    await client.setup_hook()
                self.assertEqual({cmd.name for cmd in client.tree.get_commands()},{'cvpilot'})
                self.assertEqual({cmd.name for cmd in client.tree.get_commands(guild=discord.Object(id=GUILD))},{'cvpilot','openclaw'})
                payload=listener.cvpilot.to_dict(client.tree)
                leaves={command.qualified_name for command in listener.cvpilot.walk_commands() if not hasattr(command,'commands')}
                self.assertEqual(len(leaves),16)
                self.assertIn('cvpilot reset',leaves)
                self.assertTrue({'cvpilot history','cvpilot revert'}<=leaves)
                self.assertEqual(payload['contexts'],[0,1])
                self.assertEqual(payload['integration_types'],[0])
                self.assertTrue(payload['dm_permission'])
                self.assertTrue(listener.openclaw_admin.guild_only)
                self.assertEqual(sync_mock.await_count,2)
                self.assertFalse(client.intents.members)
                self.assertFalse(client.intents.message_content)
            finally:await client.close()


if __name__=='__main__':unittest.main()
