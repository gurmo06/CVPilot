"""Delete previews and typed confirmations use authenticated private interactions."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

APP = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cvpilot_delete_listener', APP.parent / 'discord/cvpilot_discord.py')
listener = importlib.util.module_from_spec(spec); spec.loader.exec_module(listener)
UID = 123456789012345678


class DeleteUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.interaction = mock.Mock(); self.interaction.user.id = UID
        self.interaction.response.is_done.return_value = True
        self.interaction.response.send_modal = mock.AsyncMock()
        self.interaction.followup.send = mock.AsyncMock()
        self.message = mock.Mock(); self.message.edit = mock.AsyncMock()
        self.interaction.followup.send.return_value = self.message
        auth = mock.patch.object(listener, 'public_authorized', new=mock.AsyncMock(return_value=True))
        self.auth = auth.start(); self.addCleanup(auth.stop)
        controller = mock.patch.object(listener, 'request_controller', new=mock.AsyncMock(return_value={
            'ok': True, 'text': 'Review and type DELETE.', 'confirmation_token': 'token'}))
        self.controller = controller.start(); self.addCleanup(controller.stop)

    async def test_both_commands_prepare_private_previews_with_exact_selectors(self):
        for command, kwargs, action, payload, view_type in [
            (listener.master_delete, {'label': 'teaching'}, 'master-delete', {'label': 'teaching', 'commit': None}, listener.MasterDeleteView),
            (listener.master_delete, {'commit': 'abcdef1'}, 'master-delete', {'label': None, 'commit': 'abcdef1'}, listener.MasterDeleteView),
            (listener.application_delete, {'link': 'https://github.com/example/repo/commit/abcdef1'}, 'application-delete',
             {'link': 'https://github.com/example/repo/commit/abcdef1', 'commit': None}, listener.ApplicationDeleteView),
            (listener.application_delete, {'commit': 'abcdef1'}, 'application-delete', {'link': None, 'commit': 'abcdef1'}, listener.ApplicationDeleteView)]:
            with self.subTest(action=action, kwargs=kwargs):
                self.controller.reset_mock()
                await command.callback(self.interaction, **kwargs)
                self.controller.assert_awaited_once_with(UID, action, {'operation': 'prepare', **payload})
                reply = self.interaction.followup.send.call_args
                self.assertTrue(reply.kwargs['ephemeral']); self.assertTrue(reply.kwargs['wait'])
                view = reply.kwargs['view']; self.assertIsInstance(view, view_type)
                self.assertEqual({button.label for button in view.children}, {'Delete resume', 'Cancel'})
                self.assertEqual(view.timeout, 120); view.stop()

    async def test_button_opens_delete_modal_without_queuing(self):
        for view_type in (listener.MasterDeleteView, listener.ApplicationDeleteView):
            view = view_type(UID, 'token')
            await view.confirm.callback(self.interaction)
            modal = self.interaction.response.send_modal.call_args.args[0]
            self.assertIsInstance(modal, listener.DeleteModal)
            self.assertEqual(modal.verification.min_length, 6); self.assertEqual(len(modal.children), 1)
            self.controller.assert_not_awaited(); view.stop(); modal.stop()

    async def test_confirmation_rechecks_role_and_uses_correct_action(self):
        for view_type, action in ((listener.MasterDeleteView, 'master-delete'), (listener.ApplicationDeleteView, 'application-delete')):
            self.controller.reset_mock(); self.auth.reset_mock()
            view = view_type(UID, 'token'); view.message = self.message
            modal = listener.DeleteModal(view); modal.verification._value = 'DELETE'
            await modal.on_submit(self.interaction)
            self.auth.assert_awaited_once_with(self.interaction)
            self.controller.assert_awaited_once_with(UID, action, {'operation': 'confirm', 'token': 'token', 'confirmation': 'DELETE'})
            self.assertTrue(all(button.disabled for button in view.children)); modal.stop()

    async def test_other_user_revoked_role_and_denied_preparation_cannot_delete(self):
        view = listener.MasterDeleteView(UID, 'token'); modal = listener.DeleteModal(view)
        modal.verification._value = 'DELETE'; self.interaction.user.id = UID + 1
        self.assertFalse(await view.interaction_check(self.interaction))
        await modal.on_submit(self.interaction); self.controller.assert_not_awaited()
        self.interaction.user.id = UID; self.auth.return_value = False
        await modal.on_submit(self.interaction)
        await listener.master_delete.callback(self.interaction, label='teaching')
        await listener.application_delete.callback(self.interaction, commit='abcdef1')
        self.controller.assert_not_awaited(); modal.stop(); view.stop()

    async def test_refused_phrase_keeps_prompt_and_cancel_sends_correct_action(self):
        view = listener.ApplicationDeleteView(UID, 'token'); view.message = self.message
        modal = listener.DeleteModal(view); modal.verification._value = 'delete'
        self.controller.return_value = {'ok': False, 'text': 'Type DELETE exactly.'}
        await modal.on_submit(self.interaction)
        self.assertFalse(any(button.disabled for button in view.children)); modal.stop()
        self.controller.reset_mock(); self.controller.return_value = {'ok': True, 'text': 'Cancelled.'}
        await view.cancel.callback(self.interaction)
        self.controller.assert_awaited_once_with(UID, 'application-delete', {'operation': 'cancel', 'token': 'token'})
        self.controller.reset_mock()
        view = listener.MasterDeleteView(UID, 'token'); await view.on_timeout()
        self.controller.assert_not_awaited()


if __name__ == '__main__': unittest.main()
