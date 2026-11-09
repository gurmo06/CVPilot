"""Question answers use private previews, typed consent and current permissions."""
from unittest import mock
import unittest
from test_discord_delete import listener,UID


class AnswerUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.interaction=mock.Mock();self.interaction.user.id=UID
        self.interaction.response.is_done.return_value=True
        self.interaction.response.send_modal=mock.AsyncMock()
        self.interaction.followup.send=mock.AsyncMock()
        self.message=mock.Mock();self.message.edit=mock.AsyncMock()
        self.interaction.followup.send.return_value=self.message
        auth=mock.patch.object(listener,'public_authorized',new=mock.AsyncMock(return_value=True))
        self.auth=auth.start();self.addCleanup(auth.stop)
        controller=mock.patch.object(listener,'request_controller',new=mock.AsyncMock(return_value={
            'ok':True,'text':'Review your statement and type CONFIRM.','confirmation_token':'token'}))
        self.controller=controller.start();self.addCleanup(controller.stop)

    async def test_answer_prepares_a_private_fact_preview(self):
        await listener.answer.callback(self.interaction,job_id=7,question=1,details='I have supervised care experience.')
        self.controller.assert_awaited_once_with(UID,'answer',{'operation':'prepare','job_id':7,'question':1,'details':'I have supervised care experience.'})
        reply=self.interaction.followup.send.call_args
        self.assertTrue(reply.kwargs['ephemeral'])
        view=reply.kwargs['view'];self.assertEqual({b.label for b in view.children},{'Save to profile','Cancel'});view.stop()

    async def test_click_opens_modal_and_submission_rechecks_permissions(self):
        view=listener.AnswerView(UID,'token');view.message=self.message
        await view.confirm.callback(self.interaction)
        self.controller.assert_not_awaited()
        modal=self.interaction.response.send_modal.call_args.args[0]
        self.assertIsInstance(modal,listener.AnswerModal);modal.verification._value='CONFIRM'
        await modal.on_submit(self.interaction)
        self.auth.assert_awaited_once_with(self.interaction)
        self.controller.assert_awaited_once_with(UID,'answer',{'operation':'confirm','token':'token','confirmation':'CONFIRM'})
        self.assertTrue(all(b.disabled for b in view.children));modal.stop()

    async def test_other_user_and_revoked_role_cannot_confirm(self):
        view=listener.AnswerView(UID,'token');modal=listener.AnswerModal(view);modal.verification._value='CONFIRM'
        self.interaction.user.id=UID+1
        await modal.on_submit(self.interaction);self.controller.assert_not_awaited()
        self.interaction.user.id=UID;self.auth.return_value=False
        await modal.on_submit(self.interaction);self.controller.assert_not_awaited();modal.stop();view.stop()

    async def test_cancel_and_timeout_never_save_a_fact(self):
        view=listener.AnswerView(UID,'token');await view.cancel.callback(self.interaction)
        self.controller.assert_awaited_once_with(UID,'answer',{'operation':'cancel','token':'token'})
        self.controller.reset_mock();view=listener.AnswerView(UID,'token');await view.on_timeout()
        self.controller.assert_not_awaited()


if __name__=='__main__':unittest.main()
