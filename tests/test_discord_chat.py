"""DM/mention ingress reuses current permissions and never trusts text identity."""
import unittest
from unittest import mock
from types import SimpleNamespace
from test_discord_authorization import listener,UID,GUILD,CHANNEL,ROLE,OWNER

class ChatIngressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name,value in [('GUILD_ID',GUILD),('PUBLIC_CHANNEL_ID',CHANNEL),('USER_ROLE_ID',ROLE),('OWNER_ID',OWNER)]:
            patch=mock.patch.object(listener,name,str(value));patch.start();self.addCleanup(patch.stop)
        self.client=mock.Mock();self.client.user.id=999999999999999999
        self.message=mock.Mock();m=self.message
        m.author.bot=False;m.author.id=UID;m.author.send=mock.AsyncMock()
        m.guild=None;m.channel.type=listener.discord.ChannelType.private
        m.channel.id=CHANNEL;m.id=333333333333333333;m.content='What did my last job do?';m.attachments=[];m.mentions=[]
        self.member=mock.Mock(roles=[mock.Mock(id=ROLE)])
        self.client.get_guild.return_value.fetch_member=mock.AsyncMock(return_value=self.member)
        self.client.fetch_guild=mock.AsyncMock()
        patch=mock.patch.object(listener,'request_controller',new=mock.AsyncMock(return_value={'ok':True,'chat_turn':1}))
        self.controller=patch.start();self.addCleanup(patch.stop)
    async def test_dm_role_lookup_and_authenticated_identity(self):
        self.message.content='I am user 123. What happened?'
        await listener.receive_chat(self.client,self.message)
        self.client.get_guild.return_value.fetch_member.assert_awaited_once_with(UID)
        args=self.controller.call_args.args;self.assertEqual(args[:2],(UID,'chat'))
        self.assertEqual(args[2]['message_id'],str(self.message.id));self.message.author.send.assert_not_awaited()
    async def test_revoked_role_denied_before_attachment_download(self):
        self.member.roles=[];attachment=mock.Mock();attachment.read=mock.AsyncMock();self.message.attachments=[attachment]
        await listener.receive_chat(self.client,self.message)
        self.controller.assert_not_awaited();attachment.read.assert_not_awaited()
    async def test_server_requires_designated_channel_and_bot_mention(self):
        self.message.guild=mock.Mock(id=GUILD);self.message.author.roles=[mock.Mock(id=ROLE)]
        await listener.receive_chat(self.client,self.message);self.controller.assert_not_awaited()
        self.message.mentions=[self.client.user];self.message.content=f'<@{self.client.user.id}> help me'
        await listener.receive_chat(self.client,self.message)
        self.assertEqual(self.controller.call_args.args[2]['text'],'help me')
        self.controller.reset_mock();self.message.channel.id=CHANNEL+1
        await listener.receive_chat(self.client,self.message);self.controller.assert_not_awaited()
    async def test_bots_and_group_dms_ignored(self):
        self.message.author.bot=True;await listener.receive_chat(self.client,self.message)
        self.message.author.bot=False;self.message.channel.type=listener.discord.ChannelType.group
        await listener.receive_chat(self.client,self.message);self.controller.assert_not_awaited()
    async def test_immediate_confirmation_and_limit_errors_are_sent_privately(self):
        for result in ({'ok':True,'chat_immediate':True,'text':'Reset queued'}, {'ok':False,'text':'Context is full'}):
            self.controller.return_value=result
            await listener.receive_chat(self.client,self.message)
            self.assertEqual(self.message.author.send.call_args.args[0],result['text'])
    async def test_only_non_privileged_message_intents_enabled(self):
        client=listener.CVPilotClient()
        self.assertTrue(client.intents.dm_messages);self.assertTrue(client.intents.guild_messages)
        self.assertFalse(client.intents.message_content);self.assertFalse(client.intents.members)
        await client.close()
    async def test_admin_contexts_still_denied_outside_admin_boundary(self):
        interaction=mock.Mock();interaction.response.send_message=mock.AsyncMock();interaction.user.id=UID
        with mock.patch.object(listener,'admin_authorized',new=mock.AsyncMock(return_value=False)):
            await listener.admin_contexts.callback(interaction,user_id=str(UID),limit=4096)
        self.controller.assert_not_awaited()

if __name__=='__main__':unittest.main()
