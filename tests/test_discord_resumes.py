"""Discord slash-command attachment tests with mocked interactions and network."""
import asyncio
import base64
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest import mock

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP/'controller'))
import resumes
spec=importlib.util.spec_from_file_location('cvpilot_attachment_listener',APP.parent/'discord/cvpilot_discord.py')
listener=importlib.util.module_from_spec(spec);spec.loader.exec_module(listener)
REAL_REQUEST_CONTROLLER=listener.request_controller
PDF=b'%PDF-1.4\nVerified resume\n%%EOF\n'
TEX=b'\\documentclass{article}\\begin{document}Verified facts\\end{document}'


class DiscordResumeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.interaction=mock.Mock()
        self.interaction.user.id=123456789012345678
        self.interaction.filesize_limit=resumes.MAX_FILE_BYTES
        self.interaction.response.is_done.return_value=False
        self.interaction.response.defer=mock.AsyncMock()
        self.interaction.response.send_message=mock.AsyncMock()
        self.interaction.followup.send=mock.AsyncMock()
        self.authorized=mock.patch.object(listener,'public_authorized',new=mock.AsyncMock(return_value=True))
        self.authorized.start();self.addCleanup(self.authorized.stop)
        self.request=mock.patch.object(listener,'request_controller',new=mock.AsyncMock(return_value={'ok':True,'text':'Queued'}))
        self.controller=self.request.start();self.addCleanup(self.request.stop)

    def file(self,name,data):
        attachment=mock.Mock(filename=name,size=len(data))
        attachment.read=mock.AsyncMock(return_value=data)
        return attachment

    async def test_upload_is_deferred_private_and_binds_authenticated_sender(self):
        file=self.file('my-resume.pdf',PDF)
        await listener.master_upload.callback(self.interaction,file)
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True,thinking=True)
        args=self.controller.call_args.args
        self.assertEqual(args[0],self.interaction.user.id)
        self.assertEqual(args[1],'master')
        self.assertEqual(args[2]['role'],'general')
        self.assertEqual(resumes.validate_files(args[2]['files']),{'resume.pdf':PDF})
        self.assertTrue(self.interaction.followup.send.call_args.kwargs['ephemeral'])

    async def test_upload_pair_and_custom_nontechnical_label(self):
        await listener.master_upload.callback(self.interaction,self.file('cv.pdf',PDF),
                                               'social-work',self.file('cv.tex',TEX))
        payload=self.controller.call_args.args[2]
        self.assertEqual(payload['role'],'social-work')
        self.assertEqual(resumes.validate_files(payload['files']),{'resume.pdf':PDF,'resume.tex':TEX})

    async def test_invalid_large_or_unsupported_upload_does_not_reach_controller(self):
        for name,data,size in [('cv.docx',b'docx',4),('cv.pdf',b'bad',3),
                               ('cv.pdf',PDF,resumes.MAX_FILE_BYTES+1)]:
            self.controller.reset_mock()
            file=self.file(name,data);file.size=size
            await listener.master_upload.callback(self.interaction,file)
            self.controller.assert_not_awaited()
            self.assertTrue(self.interaction.followup.send.call_args.kwargs['ephemeral'])

    async def test_authorization_precedes_attachment_read(self):
        file=self.file('cv.pdf',PDF)
        with mock.patch.object(listener,'public_authorized',new=mock.AsyncMock(return_value=False)):
            await listener.master_upload.callback(self.interaction,file)
        file.read.assert_not_awaited();self.controller.assert_not_awaited()
        self.interaction.response.defer.assert_not_awaited()

    async def test_download_sends_real_private_attachment_bytes(self):
        seen=[]
        async def capture(text,**kwargs):
            seen.extend((file.filename,file.fp.read()) for file in kwargs['files'])
            self.assertTrue(kwargs['ephemeral'])
            self.assertFalse(kwargs['allowed_mentions'].everyone)
        self.interaction.followup.send.side_effect=capture
        self.controller.return_value={'ok':True,'text':'Your files',
            'attachments':resumes.encode_attachments([('resume.pdf',PDF),('resume.tex',TEX)])}
        await listener.download.callback(self.interaction,42)
        self.assertEqual(dict(seen),{'resume.pdf':PDF,'resume.tex':TEX})
        self.controller.assert_awaited_once_with(self.interaction.user.id,'download',{'job_id':42})

    async def test_discord_attachment_rejection_falls_back_to_private_download_guidance(self):
        import discord
        response=mock.Mock(status=413,reason='Payload too large')
        self.interaction.followup.send.side_effect=[discord.HTTPException(response,'File rejected'),None]
        result={'ok':True,'text':'Private link: https://github.com/CV-Pilot/test',
                'attachments':resumes.encode_attachments([('resume.pdf',PDF)])}
        await listener.send_controller_response(self.interaction,result)
        self.assertEqual(self.interaction.followup.send.await_count,2)
        self.assertIn('/cvpilot download',self.interaction.followup.send.call_args.args[0])
        self.assertTrue(self.interaction.followup.send.call_args.kwargs['ephemeral'])

    async def test_interaction_file_limit_uses_private_link_without_oversize_attachment(self):
        self.interaction.filesize_limit=8
        result={'ok':True,'text':'Private link',
                'attachments':resumes.encode_attachments([('resume.pdf',PDF)])}
        await listener.send_controller_response(self.interaction,result)
        self.assertEqual(self.interaction.followup.send.call_args.kwargs['files'],[])
        self.assertIn('private GitHub link',self.interaction.followup.send.call_args.args[0])

    async def test_failed_controller_response_cannot_smuggle_attachments(self):
        await listener.send_controller_response(self.interaction,{'ok':False,'text':'Not yours',
              'attachments':resumes.encode_attachments([('resume.pdf',PDF)])})
        self.assertEqual(self.interaction.followup.send.call_args.kwargs['files'],[])

    async def test_help_fits_discord_and_explains_general_labels_and_downloads(self):
        await listener.help_command.callback(self.interaction)
        text=self.interaction.response.send_message.call_args.args[0]
        self.assertLessEqual(len(text),2000)
        self.assertIn('general',text);self.assertIn('healthcare',text)
        self.assertIn('/cvpilot download',text)
        self.assertNotIn('swe|ml',text)

    async def test_socket_response_accepts_large_packets_and_closes_writer(self):
        reader=mock.Mock();writer=mock.Mock()
        import json
        result={'ok':True,'text':'Your files','attachments':resumes.encode_attachments([('resume.pdf',PDF+b'x'*150000)])}
        reader.readline=mock.AsyncMock(return_value=(json.dumps(result)+'\n').encode())
        writer.drain=mock.AsyncMock();writer.wait_closed=mock.AsyncMock()
        with mock.patch.object(listener.asyncio,'open_unix_connection',new=mock.AsyncMock(return_value=(reader,writer))) as connect:
            # Use the real request function, bypassing this test class's mock.
            real=REAL_REQUEST_CONTROLLER
            received=await real(self.interaction.user.id,'download',{'job_id':42})
        self.assertEqual(received,result)
        self.assertEqual(connect.call_args.kwargs['limit'],resumes.MAX_PACKET_BYTES+1)
        writer.close.assert_called_once();writer.wait_closed.assert_awaited_once()


if __name__=='__main__':unittest.main()
