"""Single-job Discord pagination, privacy and expiry, with no Discord/network I/O."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import test_controller as fixtures
from store import Store
import queue_status

APP=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('cvpilot_status_listener',APP.parent/'discord/cvpilot_discord.py')
listener=importlib.util.module_from_spec(spec);spec.loader.exec_module(listener)
UID=int(fixtures.UID)


class StatusUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=Store(Path(temp.name)/'jobs.sqlite')
        for _ in range(3):
            jid=self.store.enqueue(str(UID),'tailor',{})
            self.store.finish(jid,'failed',error='Specific compiler error at source line 42')
        self.result={'ok':True,**queue_status.status(self.store,str(UID))}
        self.done=False;self.interaction=mock.Mock();self.interaction.user.id=UID
        self.interaction.response.is_done.side_effect=lambda:self.done
        async def defer(**kwargs):self.done=True
        self.interaction.response.defer=mock.AsyncMock(side_effect=defer)
        self.interaction.response.send_message=mock.AsyncMock()
        self.interaction.followup.send=mock.AsyncMock()
        self.interaction.edit_original_response=mock.AsyncMock()
        self.message=mock.Mock();self.message.edit=mock.AsyncMock()
        self.interaction.followup.send.return_value=self.message
        patch=mock.patch.object(listener,'public_authorized',new=mock.AsyncMock(return_value=True))
        self.auth=patch.start();self.addCleanup(patch.stop)
        async def request(uid,action,args):
            return {'ok':True,**queue_status.status(self.store,str(uid),args.get('job_id'),args.get('direction'))}
        patch=mock.patch.object(listener,'request_controller',new=mock.AsyncMock(side_effect=request))
        self.controller=patch.start();self.addCleanup(patch.stop)

    async def test_command_sends_one_private_embed_with_report_and_arrows(self):
        await listener.status.callback(self.interaction)
        self.controller.assert_awaited_once_with(UID,'status',{'job_id':None})
        kwargs=self.interaction.followup.send.call_args.kwargs
        self.assertTrue(kwargs['ephemeral']);self.assertTrue(kwargs['wait'])
        self.assertIn('#3',kwargs['embed'].title);self.assertNotIn('#2',kwargs['embed'].title)
        self.assertEqual(kwargs['file'].filename,'cvpilot-job-3-status.txt')
        view=kwargs['view'];self.assertIs(view.message,self.message)
        self.assertEqual({item.label for item in view.children},{'Older','Newer','Refresh'})
        self.assertTrue(view.newer.disabled);self.assertFalse(view.older.disabled)
        self.assertEqual(view.timeout,600);view.stop()

    async def test_optional_job_id_opens_requested_owned_job(self):
        await listener.status.callback(self.interaction,1)
        self.controller.assert_awaited_once_with(UID,'status',{'job_id':1})
        view=self.interaction.followup.send.call_args.kwargs['view']
        self.assertEqual(view.job_id,1);self.assertTrue(view.older.disabled);view.stop()

    async def test_arrows_edit_same_message_and_replace_report_in_both_directions(self):
        view=listener.StatusView(UID,self.result)
        await view.older.callback(self.interaction)
        self.controller.assert_awaited_with(UID,'status',{'job_id':3,'direction':'older'})
        kwargs=self.interaction.edit_original_response.call_args.kwargs
        self.assertIn('#2',kwargs['embed'].title)
        self.assertEqual([file.filename for file in kwargs['attachments']],['cvpilot-job-2-status.txt'])
        self.assertEqual(view.job_id,2);self.assertFalse(view.newer.disabled)
        await view.newer.callback(self.interaction)
        self.assertEqual(view.job_id,3);self.assertTrue(view.newer.disabled)
        self.interaction.followup.send.assert_not_awaited();view.stop()

    async def test_refresh_stays_on_current_job_and_discovers_newer_jobs(self):
        view=listener.StatusView(UID,self.result)
        self.store.enqueue(str(UID),'profile-sync',{})
        await view.refresh.callback(self.interaction)
        self.controller.assert_awaited_with(UID,'status',{'job_id':3})
        self.assertEqual(view.job_id,3);self.assertFalse(view.newer.disabled);view.stop()

    async def test_other_users_cannot_operate_any_button(self):
        view=listener.StatusView(UID,self.result);self.interaction.user.id=UID+1
        self.assertFalse(await view.interaction_check(self.interaction))
        for button in view.children:await button.callback(self.interaction)
        self.controller.assert_not_awaited();self.auth.assert_not_awaited()
        self.interaction.edit_original_response.assert_not_awaited();view.stop()

    async def test_each_click_rechecks_roles_after_prompt_acknowledgement(self):
        view=listener.StatusView(UID,self.result)
        async def authorize(interaction):
            self.assertTrue(self.done);return False
        self.auth.side_effect=authorize
        await view.older.callback(self.interaction)
        self.auth.assert_awaited_once();self.controller.assert_not_awaited()
        self.interaction.edit_original_response.assert_not_awaited();view.stop()

    async def test_permission_denial_on_command_does_not_read_status(self):
        self.auth.return_value=False
        await listener.status.callback(self.interaction)
        self.controller.assert_not_awaited();self.interaction.followup.send.assert_not_awaited()

    async def test_controller_failure_keeps_existing_page(self):
        self.controller.side_effect=None;self.controller.return_value={'ok':False,'text':'Status is unavailable'}
        view=listener.StatusView(UID,self.result)
        await view.older.callback(self.interaction)
        self.assertEqual(view.job_id,3);self.interaction.edit_original_response.assert_not_awaited()
        self.assertTrue(self.interaction.followup.send.call_args.kwargs['ephemeral']);view.stop()

    async def test_failed_discord_edit_does_not_advance_cursor(self):
        view=listener.StatusView(UID,self.result)
        self.interaction.edit_original_response.side_effect=OSError('Discord unavailable')
        await view.older.callback(self.interaction)
        self.assertEqual(view.job_id,3);self.assertTrue(view.newer.disabled)
        self.assertFalse(view.older.disabled);view.stop()

    async def test_timeout_disables_buttons_and_cannot_resurrect_view(self):
        view=listener.StatusView(UID,self.result);view.message=self.message
        await view.on_timeout()
        self.assertTrue(all(item.disabled for item in view.children))
        self.message.edit.assert_awaited_once_with(view=view)
        await view.refresh.callback(self.interaction)
        self.controller.assert_not_awaited();self.interaction.edit_original_response.assert_not_awaited()

    async def test_no_jobs_and_unknown_id_have_no_navigation_controls(self):
        self.controller.side_effect=None
        for response in ({'ok':True,'text':'No jobs yet.'},{'ok':False,'text':'No job found for your account.'}):
            self.controller.return_value=response
            await listener.status.callback(self.interaction)
            self.assertNotIn('view',self.interaction.followup.send.call_args.kwargs)

    async def test_embed_limits_and_full_report_attachment(self):
        self.store.finish(3,'failed',error='Error line.\n'*250)
        result={'ok':True,**queue_status.status(self.store,str(UID))}
        embed,file=listener.status_message(result)
        try:
            self.assertLessEqual(len(embed),6000)
            self.assertTrue(all(len(field.value)<=1024 for field in embed.fields))
            report=file.fp.read().decode('utf-8')
            self.assertEqual(report.count('Error line.'),250)
        finally:file.close()


if __name__=='__main__':unittest.main()
