"""History/revert UI is private, current-permission checked, and requires typed confirmation."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

APP=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('cvpilot_history_listener',APP.parent/'discord/cvpilot_discord.py')
listener=importlib.util.module_from_spec(spec);spec.loader.exec_module(listener)
UID=123456789012345678


class HistoryUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.done=False;self.interaction=mock.Mock();self.interaction.user.id=UID
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
            'ok':True,'text':'Review the change and type REVERT.','confirmation_token':'token'}))
        self.controller=request.start();self.addCleanup(request.stop)

    async def test_history_uses_authenticated_sender_and_private_response(self):
        await listener.history.callback(self.interaction,10)
        self.auth.assert_awaited_once_with(self.interaction)
        self.controller.assert_awaited_once_with(UID,'history',{'limit':10})
        self.assertTrue(self.interaction.followup.send.call_args.kwargs['ephemeral'])

    async def test_revert_only_prepares_a_private_preview(self):
        await listener.revert.callback(self.interaction,'abcdef123456')
        self.controller.assert_awaited_once_with(UID,'revert',{'operation':'prepare','commit':'abcdef123456'})
        reply=self.interaction.followup.send.call_args
        self.assertTrue(reply.kwargs['ephemeral']);self.assertTrue(reply.kwargs['wait'])
        view=reply.kwargs['view'];self.assertIsInstance(view,listener.RevertView)
        self.assertEqual(view.timeout,120);self.assertEqual(view.confirm.label,'Revert commit')
        self.assertEqual({button.label for button in view.children},{'Revert commit','Cancel'})
        view.stop()

    async def test_click_opens_revert_modal_without_queuing_anything(self):
        view=listener.RevertView(UID,'token');await view.confirm.callback(self.interaction)
        modal=self.interaction.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal,listener.RevertModal)
        self.assertEqual(modal.verification.min_length,6);self.assertEqual(modal.verification.max_length,6)
        self.assertEqual(len(modal.children),1)
        self.assertLessEqual(len(modal.title),45)
        self.controller.assert_not_awaited();view.stop();modal.stop()

    async def test_typed_revert_rechecks_permissions_and_sends_exact_phrase(self):
        view=listener.RevertView(UID,'token');view.message=self.message
        modal=listener.RevertModal(view);modal.verification._value='REVERT'
        self.controller.return_value={'ok':True,'text':'Revert queued.'}
        await modal.on_submit(self.interaction)
        self.auth.assert_awaited_once_with(self.interaction)
        self.controller.assert_awaited_once_with(UID,'revert',{'operation':'confirm','token':'token','confirmation':'REVERT'})
        self.assertTrue(all(button.disabled for button in view.children));modal.stop()

    async def test_wrong_user_or_revoked_role_cannot_confirm(self):
        view=listener.RevertView(UID,'token');modal=listener.RevertModal(view);modal.verification._value='REVERT'
        self.interaction.user.id=UID+1
        self.assertFalse(await view.interaction_check(self.interaction))
        await modal.on_submit(self.interaction);self.controller.assert_not_awaited()
        self.interaction.user.id=UID;self.auth.return_value=False
        await modal.on_submit(self.interaction);self.controller.assert_not_awaited()
        modal.stop();view.stop()

    async def test_backend_refusal_keeps_prompt_unconfirmed(self):
        view=listener.RevertView(UID,'token');view.message=self.message
        modal=listener.RevertModal(view);modal.verification._value='revert'
        self.controller.return_value={'ok':False,'text':'Type REVERT exactly.'}
        await modal.on_submit(self.interaction)
        self.assertFalse(any(button.disabled for button in view.children))
        self.message.edit.assert_not_awaited();modal.stop();view.stop()

    async def test_cancel_consumes_only_revert_token_and_timeout_queues_nothing(self):
        view=listener.RevertView(UID,'token');view.message=self.message
        self.controller.return_value={'ok':True,'text':'Revert cancelled.'}
        await view.cancel.callback(self.interaction)
        self.controller.assert_awaited_once_with(UID,'revert',{'operation':'cancel','token':'token'})
        self.controller.reset_mock()
        other=listener.RevertView(UID,'token');await other.on_timeout()
        self.controller.assert_not_awaited()

    async def test_permission_denial_prevents_history_and_revert_preparation(self):
        self.auth.return_value=False
        await listener.history.callback(self.interaction)
        await listener.revert.callback(self.interaction,'abcdef123456')
        self.controller.assert_not_awaited()


if __name__=='__main__':unittest.main()
