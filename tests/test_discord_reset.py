"""Destructive reset UI verification. No Discord messages or real reset jobs."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

APP=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('cvpilot_reset_listener',APP.parent/'discord/cvpilot_discord.py')
listener=importlib.util.module_from_spec(spec);spec.loader.exec_module(listener)
UID=123456789012345678


class ResetUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.done=False
        self.interaction=mock.Mock();self.interaction.user.id=UID
        self.interaction.response.is_done.side_effect=lambda:self.done
        async def defer(**kwargs):self.done=True
        async def respond(*args,**kwargs):self.done=True
        self.interaction.response.defer=mock.AsyncMock(side_effect=defer)
        self.interaction.response.send_message=mock.AsyncMock(side_effect=respond)
        self.interaction.response.send_modal=mock.AsyncMock()
        self.interaction.followup.send=mock.AsyncMock()
        self.message=mock.Mock();self.message.edit=mock.AsyncMock()
        self.interaction.followup.send.return_value=self.message
        auth=mock.patch.object(listener,'public_authorized',new=mock.AsyncMock(return_value=True))
        self.auth=auth.start();self.addCleanup(auth.stop)
        request=mock.patch.object(listener,'request_controller',new=mock.AsyncMock(return_value={
            'ok':True,'text':'Read the reset warning and type RESET.','confirmation_token':'private-token'}))
        self.controller=request.start();self.addCleanup(request.stop)

    async def test_command_only_prepares_private_confirmation(self):
        await listener.reset.callback(self.interaction)
        self.controller.assert_awaited_once_with(UID,'reset',{'operation':'prepare'})
        reply=self.interaction.followup.send.call_args
        self.assertTrue(reply.kwargs['ephemeral'])
        self.assertTrue(reply.kwargs['wait'])
        view=reply.kwargs['view']
        self.assertEqual(view.sender_id,UID);self.assertEqual(view.token,'private-token')
        self.assertEqual(view.timeout,120)
        self.assertEqual(view.confirm.style,listener.discord.ButtonStyle.danger)
        self.assertIs(view.message,self.message)
        view.stop()

    async def test_permission_denial_does_not_prepare_any_reset(self):
        self.auth.return_value=False
        await listener.reset.callback(self.interaction)
        self.controller.assert_not_awaited()

    async def test_busy_response_does_not_show_confirmation_controls(self):
        self.controller.return_value={'ok':False,'text':'Wait for running jobs.'}
        await listener.reset.callback(self.interaction)
        self.assertNotIn('view',self.interaction.response.send_message.call_args.kwargs if self.interaction.response.send_message.called else self.interaction.followup.send.call_args.kwargs)

    async def test_red_button_opens_typed_modal_without_queuing_reset(self):
        view=listener.ResetView(UID,'token')
        await view.confirm.callback(self.interaction)
        modal=self.interaction.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal,listener.ResetModal)
        self.assertLessEqual(len(modal.title),45)
        self.assertLessEqual(len(modal.to_dict()['components'][0]['components'][0]['label']),45)
        self.assertEqual(modal.verification.max_length,5)
        self.assertTrue(modal.verification.required)
        self.controller.assert_not_awaited()
        modal.stop();view.stop()

    async def test_other_user_cannot_use_confirmation_or_submit_modal(self):
        view=listener.ResetView(UID,'token');self.interaction.user.id=UID+1
        self.assertFalse(await view.interaction_check(self.interaction))
        await view.confirm.callback(self.interaction)
        self.interaction.response.send_modal.assert_not_awaited()
        modal=listener.ResetModal(view);modal.verification._value='RESET'
        await modal.on_submit(self.interaction)
        self.controller.assert_not_awaited();self.auth.assert_not_awaited()
        modal.stop();view.stop()

    async def test_submit_rechecks_permissions_and_sends_exact_confirmation(self):
        view=listener.ResetView(UID,'token');view.message=self.message
        modal=listener.ResetModal(view);modal.verification._value='RESET'
        self.controller.return_value={'ok':True,'text':'Reset queued as job #2.'}
        await modal.on_submit(self.interaction)
        self.auth.assert_awaited_once_with(self.interaction)
        self.controller.assert_awaited_once_with(UID,'reset',{'operation':'confirm','token':'token','confirmation':'RESET'})
        self.assertTrue(all(item.disabled for item in view.children))
        self.message.edit.assert_awaited_once()
        self.assertTrue(self.interaction.followup.send.call_args.kwargs['ephemeral'])
        modal.stop()

    async def test_role_revoked_between_prompt_and_submit_cannot_queue_reset(self):
        view=listener.ResetView(UID,'token');modal=listener.ResetModal(view)
        modal.verification._value='RESET';self.auth.return_value=False
        await modal.on_submit(self.interaction)
        self.controller.assert_not_awaited()
        modal.stop();view.stop()

    async def test_wrong_phrase_backend_rejection_keeps_prompt_unconfirmed(self):
        view=listener.ResetView(UID,'token');view.message=self.message
        modal=listener.ResetModal(view);modal.verification._value='reset'
        self.controller.return_value={'ok':False,'text':'Type RESET exactly.'}
        await modal.on_submit(self.interaction)
        self.assertEqual(self.controller.call_args.args[2]['confirmation'],'reset')
        self.assertFalse(any(item.disabled for item in view.children))
        self.message.edit.assert_not_awaited()
        modal.stop();view.stop()

    async def test_cancel_consumes_confirmation_without_requesting_reset(self):
        view=listener.ResetView(UID,'token');view.message=self.message
        self.controller.return_value={'ok':True,'text':'Reset cancelled.'}
        await view.cancel.callback(self.interaction)
        self.controller.assert_awaited_once_with(UID,'reset',{'operation':'cancel','token':'token'})
        self.assertTrue(all(item.disabled for item in view.children))

    async def test_timeout_disables_controls_without_queueing_reset(self):
        view=listener.ResetView(UID,'token');view.message=self.message
        await view.on_timeout()
        self.controller.assert_not_awaited()
        self.assertTrue(all(item.disabled for item in view.children))


if __name__=='__main__':unittest.main()
