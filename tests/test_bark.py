import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from server import Store
from bark_transport import BarkDelivery


class BarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'db','client','worker')
        self.delivery=BarkDelivery(self.store,{'registration_secret':'a'*40})

    def tearDown(self):
        self.store.db.close();self.tmp.cleanup()

    def add(self,kind='final',text='answer'):
        with self.store.lock,self.store.db:
            self.store.add_message({'id':str(time.time())},kind,text,text)

    def test_binding_does_not_replay_history_and_only_sends_test_before_enable(self):
        self.add()
        self.delivery.bind('valid_device_key')
        self.add()
        with patch('bark_transport.urlopen',return_value=io.BytesIO(b'{"code":200}')) as send:
            self.delivery.tick()
            payload=json.loads(send.call_args.args[0].data)
            self.assertEqual(payload['title'],'助手连接测试')
        with patch('bark_transport.urlopen') as send:
            self.delivery.tick();send.assert_not_called()
        self.delivery.enable()
        self.add(text='new answer')
        with patch('bark_transport.urlopen',return_value=io.BytesIO(b'{"code":200}')) as send:
            self.delivery.tick()
            self.assertEqual(json.loads(send.call_args.args[0].data)['body'],'new answer')
        self.assertEqual(self.store.get_meta('delivery_transport'),'bark')

    def test_retry_and_payload_bound(self):
        self.delivery.bind('valid_device_key')
        with patch('bark_transport.urlopen',side_effect=TimeoutError): self.delivery.tick()
        row=self.store.db.execute('select * from messages').fetchone()
        self.assertEqual(row['wake_attempts'],1)
        self.assertEqual(row['wake_status'],'pending')
        self.delivery.enable();self.add(text='中'*5000)
        with patch('bark_transport.urlopen',return_value=io.BytesIO(b'{"code":200}')) as send:
            self.delivery.tick()
            payload=send.call_args.args[0].data
            self.assertLess(len(payload),3500)
        self.assertEqual(self.store.db.execute("select count(*) from messages where wake_status='sent'").fetchone()[0],1)

    def test_registration_proxy_cannot_be_arbitrary_relay(self):
        class Handler:
            command='GET'
            headers={}
            def respond(self,status,value): self.status,self.value=status,value
        h=Handler()
        self.assertFalse(self.delivery.proxy(h,'/bark/wrong/register',''))
        self.assertTrue(self.delivery.proxy(h,self.delivery.prefix+'/info',''))
        self.assertEqual(h.status,404)
        response={'code':200,'data':{'key':'valid_device_key'}}
        with patch('bark_transport.urlopen',return_value=io.BytesIO(json.dumps(response).encode())):
            self.assertTrue(self.delivery.proxy(h,self.delivery.prefix+'/register','devicetoken=private'))
        self.assertEqual(h.status,200)
        self.assertEqual(self.store.get_meta('bark_device_key'),'valid_device_key')
        self.assertEqual(self.store.db.execute('select count(*) from messages').fetchone()[0],1)
        self.delivery.bind('valid_device_key')
        self.assertEqual(self.store.db.execute('select count(*) from messages').fetchone()[0],1)


if __name__=='__main__': unittest.main()
