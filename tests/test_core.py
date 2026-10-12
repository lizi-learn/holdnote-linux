import json
from pathlib import Path
import tempfile
import unittest
from cryptography.fernet import Fernet, InvalidToken
import requests
from holdnote.client import DeliveryError, Memos, Receipt
from holdnote.config import normalize_url
from holdnote.gesture import Gesture
from holdnote.outbox import Outbox, Worker


class GestureTests(unittest.TestCase):
    def test_short_press_opens_overview(self):
        g=Gesture();g.press(1);self.assertEqual(g.release(1.2).action,'overview')
    def test_long_press_capture_requires_intent(self):
        g=Gesture();g.press(1);self.assertEqual(g.tick(1.5).action,'arm');g.button(1)
        outcome=g.release(2);self.assertEqual(outcome.action,'capture');self.assertTrue(outcome.clicked)
    def test_no_double_arming(self):
        g=Gesture();g.press(1);g.tick(2);self.assertEqual(g.tick(3).action,'ignore')
    def test_keyboard_shortcuts_cancel_capture(self):
        g=Gesture();g.press(1);g.key();self.assertEqual(g.release(1.2).action,'cancel')
    def test_escape_after_arming_cancels(self):
        g=Gesture();g.press(1);g.tick(2);g.key();self.assertEqual(g.release(3).action,'cancel')
    def test_key_repeat_does_not_reset_timer(self):
        g=Gesture();g.press(1);g.press(1.4);self.assertEqual(g.tick(1.5).action,'arm')
    def test_release_without_press_is_ignored(self):self.assertEqual(Gesture().release(1).action,'ignore')
    def test_long_hold_without_selection_has_no_click(self):
        g=Gesture();g.press(1);g.tick(2);self.assertFalse(g.release(3).clicked)
    def test_secondary_mouse_button_is_not_text_selection(self):
        g=Gesture();g.press(1);g.button(3);self.assertFalse(g.release(2).clicked)


class Response:
    def __init__(self,status,data):self.status_code=status;self.data=data
    def json(self):return self.data


class Session:
    def __init__(self,*responses):self.headers={};self.responses=list(responses);self.calls=[]
    def request(self,*args,**kwargs):
        self.calls.append((args,kwargs));r=self.responses.pop(0)
        if isinstance(r,Exception):raise r
        return r


class APITests(unittest.TestCase):
    def test_only_https_or_localhost(self):
        self.assertEqual(normalize_url('https://memos.example.com/'),'https://memos.example.com')
        self.assertEqual(normalize_url('http://127.0.0.1:5230'),'http://127.0.0.1:5230')
        for url in ['http://example.com','https://user:password@example.com','https://example.com?secret=1','https://example.com#token']:
            with self.assertRaises(ValueError):normalize_url(url)
    def test_private_memo_and_no_redirect(self):
        s=Session(Response(200,{'name':'memos/abc','visibility':'PRIVATE'}));m=Memos('https://memos.example.com','test-token',s)
        receipt=m.send('你好','abc');self.assertEqual(receipt.name,'memos/abc')
        self.assertEqual(s.calls[0][1]['json'],{'content':'你好','visibility':'PRIVATE'})
        self.assertFalse(s.calls[0][1]['allow_redirects'])
        self.assertIn('memoId=abc',s.calls[0][0][1])
    def test_auth_error_does_not_retry(self):
        m=Memos('https://memos.example.com','test',Session(Response(401,{})))
        with self.assertRaises(DeliveryError) as c:m.send('private','id')
        self.assertEqual(c.exception.kind,'authentication');self.assertFalse(c.exception.retryable)
    def test_network_failure_can_retry(self):
        m=Memos('https://memos.example.com','test',Session(requests.ConnectionError('message must not be leaked')))
        with self.assertRaises(DeliveryError) as c:m.send('private','id')
        self.assertEqual(str(c.exception),'network');self.assertTrue(c.exception.retryable)
    def test_retry_conflict_verifies_same_private_text(self):
        s=Session(Response(409,{}),Response(200,{'name':'memos/id','visibility':'PRIVATE','content':'private'}))
        self.assertEqual(Memos('https://memos.example.com','test',s).send('private','id').name,'memos/id')
    def test_conflicting_id_never_silently_succeeds(self):
        s=Session(Response(409,{}),Response(200,{'name':'memos/id','visibility':'PRIVATE','content':'different'}))
        with self.assertRaises(DeliveryError):Memos('https://memos.example.com','test',s).send('private','id')
    def test_public_response_rejected(self):
        with self.assertRaises(DeliveryError):Memos('https://memos.example.com','test',Session(Response(200,{'name':'memos/id','visibility':'PUBLIC'}))).send('private','id')
    def test_redirect_rejected(self):
        with self.assertRaises(DeliveryError):Memos('https://memos.example.com','test',Session(Response(302,{}))).send('private','id')


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'outbox.db';self.key=Fernet.generate_key();self.q=Outbox(self.path,self.key)
    def tearDown(self):self.temp.cleanup()
    def test_text_encrypted_on_disk_and_recovers_after_restart(self):
        text='这是绝不能出现在数据库里的私密内容';capture=self.q.add(text)
        self.assertNotIn(text.encode(),self.path.read_bytes())
        q=Outbox(self.path,self.key);row=q.due();self.assertEqual(row['id'],capture);self.assertEqual(q.decode(row),text)
        with self.assertRaises(InvalidToken):Outbox(self.path,Fernet.generate_key()).decode(row)
    def test_one_sender_claims_each_record(self):
        self.q.add('private');self.assertIsNotNone(self.q.due());self.assertIsNone(self.q.due())
    def test_success_removes_local_plain_payload(self):
        capture=self.q.add('private');self.q.done(capture,'https://memos.example.com/memos/a')
        self.assertEqual(self.q.stats(),{'done':1});self.assertIsNone(self.q.due())
    def test_done_worker_notifies_only_after_ack(self):
        class Sink:
            def send(self,text,capture_id):return Receipt('memos/'+capture_id,'https://example.com/'+capture_id)
        seen=[];self.q.add('private');Worker(self.q,Sink(),lambda *a:seen.append(a)).once()
        self.assertEqual(self.q.stats(),{'done':1});self.assertEqual(seen,[('saved',7)])
    def test_transient_error_keeps_encrypted_payload_and_id(self):
        class Sink:
            def send(self,*_):raise DeliveryError('network',True)
        capture=self.q.add('private');Worker(self.q,Sink(),lambda *_:None).once()
        self.assertEqual(self.q.stats(),{'pending':1});self.assertIsNone(self.q.due());self.q.retry()
        self.assertEqual(self.q.due()['id'],capture)
    def test_bad_credentials_pause_but_do_not_delete_text(self):
        class Sink:
            def send(self,*_):raise DeliveryError('authentication')
        self.q.add('private');Worker(self.q,Sink(),lambda *_:None).once()
        self.assertEqual(self.q.stats(),{'failed':1});self.q.retry();self.assertEqual(self.q.decode(self.q.due()),'private')
    def test_state_permissions(self):self.assertEqual(self.path.stat().st_mode&0o777,0o600)


if __name__=='__main__':unittest.main()
