import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage
from storage import Store, MASHA
from reminders import tick


class ReminderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.s=Store(':memory:')
        self.api=AsyncMock()
        self.now=datetime(2026,10,14,21,0,tzinfo=ZoneInfo('Europe/Moscow'))
    async def asyncTearDown(self): self.s.db.close()

    async def test_disabled_then_enabled_once_daily(self):
        self.assertFalse(await tick(self.api,self.s,self.now))
        self.s.set_setting('reminder_enabled','yes')
        self.assertFalse(await tick(self.api,self.s,self.now-timedelta(minutes=1)))
        self.assertTrue(await tick(self.api,self.s,self.now))
        self.assertEqual(self.api.send_message.call_args.args[0],MASHA)
        self.assertFalse(await tick(self.api,self.s,self.now+timedelta(minutes=1)))
        self.assertTrue(await tick(self.api,self.s,self.now+timedelta(days=1)))

    async def test_snooze_and_off(self):
        self.s.set_setting('reminder_enabled','yes')
        await tick(self.api,self.s,self.now)
        self.s.set_setting('reminder_snooze',(self.now+timedelta(hours=1)).isoformat())
        self.assertFalse(await tick(self.api,self.s,self.now+timedelta(minutes=59)))
        self.assertTrue(await tick(self.api,self.s,self.now+timedelta(hours=1)))
        self.s.set_setting('reminder_enabled','no')
        self.assertFalse(await tick(self.api,self.s,self.now+timedelta(days=1)))

    async def test_failed_delivery_retry_not_marked_sent(self):
        self.s.set_setting('reminder_enabled','yes')
        self.api.send_message.side_effect=TelegramNetworkError(method=SendMessage(chat_id=MASHA,text='test'),message='offline')
        self.assertFalse(await tick(self.api,self.s,self.now))
        self.assertIsNone(self.s.get_setting('reminder_last'))
        self.assertFalse(await tick(self.api,self.s,self.now+timedelta(minutes=1)))
        self.assertEqual(self.api.send_message.await_count,1)
        self.api.send_message.side_effect=None
        self.assertTrue(await tick(self.api,self.s,self.now+timedelta(minutes=5)))
