import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from aiogram import Bot
from aiogram.types import User, Chat, Message, Update, CallbackQuery
import bot
from storage import MASHA, PARTNER, TEST_USER, DiaryStores, current_user
from reminders import tick
from test_dispatcher import LocalTelegram


class AccountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bot.store=DiaryStores(':memory:')
        self.transport=LocalTelegram()
        self.api=Bot('123456:TEST_TOKEN_NOT_A_REAL_CREDENTIAL',session=self.transport)
        self.counter=1000

    async def asyncTearDown(self):
        bot.store.close()
        await self.api.session.close()

    async def send(self,text,user):
        self.counter+=1
        m=Message(message_id=self.counter,date=datetime.now(),chat=Chat(id=user,type='private'),from_user=User(id=user,is_bot=False,first_name='Test'),text=text)
        await bot.dp.feed_update(self.api,Update(update_id=self.counter,message=m))

    async def click(self,data,user):
        self.counter+=1
        m=Message(message_id=self.counter,date=datetime.now(),chat=Chat(id=user,type='private'),from_user=User(id=123456,is_bot=True,first_name='Bot'))
        q=CallbackQuery(id=str(self.counter),from_user=User(id=user,is_bot=False,first_name='Test'),chat_instance=str(user),data=data,message=m)
        await bot.dp.feed_update(self.api,Update(update_id=self.counter,callback_query=q))

    def calls_to(self,user):
        return [c for c in self.transport.calls if getattr(c,'chat_id',None)==user]

    async def add_note(self,user,value):
        await self.send('/record 2026-01-01',user)
        await self.click('kind:2026-01-01:6',user)
        await self.send(value,user)

    async def test_test_account_silent_main_account_shares(self):
        await self.send('/start',TEST_USER)
        self.assertIn('Тестовый режим',self.calls_to(TEST_USER)[-1].text)
        await self.add_note(TEST_USER,'TEST PRIVATE')
        self.assertEqual(self.calls_to(PARTNER),[])
        await self.add_note(MASHA,'MASHA RECORD')
        self.assertEqual(len(self.calls_to(PARTNER)),1)
        self.assertIn('MASHA RECORD',self.calls_to(PARTNER)[0].text)
        self.assertEqual(len(bot.store.for_user(TEST_USER).events('2026-01-01')),1)
        self.assertEqual(len(bot.store.for_user(MASHA).events('2026-01-01')),1)
        self.assertEqual(current_user.get(),MASHA)

    async def test_partner_reads_only_masha_and_tester_reads_only_test(self):
        await self.add_note(TEST_USER,'TEST PRIVATE')
        await self.add_note(MASHA,'MASHA RECORD')
        self.transport.calls.clear()
        await self.send('/day 2026-01-01',PARTNER)
        text=' '.join(getattr(c,'text','') or '' for c in self.calls_to(PARTNER))
        self.assertIn('MASHA RECORD',text)
        self.assertNotIn('TEST PRIVATE',text)
        await self.send('/day 2026-01-01',TEST_USER)
        text=' '.join(getattr(c,'text','') or '' for c in self.calls_to(TEST_USER))
        self.assertIn('TEST PRIVATE',text)
        self.assertNotIn('MASHA RECORD',text)

    async def test_colliding_event_ids_undo_periods_and_clear_are_isolated(self):
        main=bot.store.for_user(MASHA)
        test=bot.store.for_user(TEST_USER)
        main_id=main.add('2026-01-01','Заметка','MAIN')
        test_id=test.add('2026-01-01','Заметка','TEST')
        self.assertEqual(main_id,test_id)
        await self.click(f'event:{test_id}',TEST_USER)
        await self.send('TEST EDIT',TEST_USER)
        self.assertEqual(main.event(main_id)['value'],'MAIN')
        self.assertEqual(test.event(test_id)['value'],'TEST EDIT')
        await self.click('d:pstart:2026-01-01',TEST_USER)
        self.assertIsNone(main.active_period())
        self.assertIsNotNone(test.active_period())
        await self.click(f'undo:{test.last_action()["id"]}',TEST_USER)
        self.assertIsNone(test.active_period())
        await self.click('del:all:all',TEST_USER)
        self.assertEqual(test.events('2026-01-01'),[])
        self.assertIsNotNone(main.event(main_id))
        self.assertEqual(self.calls_to(PARTNER),[])

    async def test_concurrent_requests_keep_account_context(self):
        await asyncio.gather(self.add_note(TEST_USER,'TEST CONCURRENT'),self.add_note(MASHA,'MAIN CONCURRENT'))
        self.assertEqual(bot.store.for_user(TEST_USER).events('2026-01-01')[0]['value'],'TEST CONCURRENT')
        self.assertEqual(bot.store.for_user(MASHA).events('2026-01-01')[0]['value'],'MAIN CONCURRENT')
        self.assertEqual(len(self.calls_to(PARTNER)),1)
        self.assertIn('MAIN CONCURRENT',self.calls_to(PARTNER)[0].text)

    async def test_reminder_settings_and_recipients_are_separate(self):
        await self.click('remind:20:30',TEST_USER)
        await self.click('remind:on',TEST_USER)
        main=bot.store.for_user(MASHA)
        test=bot.store.for_user(TEST_USER)
        self.assertEqual(main.get_setting('reminder_enabled','no'),'no')
        self.assertEqual(test.get_setting('reminder_time'),'20:30')
        self.transport.calls.clear()
        stamp=datetime(2026,10,14,21,0,tzinfo=bot.tz)
        self.assertTrue(await tick(self.api,test,stamp,TEST_USER))
        self.assertEqual(len(self.calls_to(TEST_USER)),1)
        self.assertEqual(self.calls_to(MASHA),[])
        self.assertEqual(self.calls_to(PARTNER),[])
        main.set_setting('reminder_enabled','yes')
        self.assertTrue(await tick(self.api,main,stamp,MASHA))
        self.assertEqual(len(self.calls_to(MASHA)),1)

    async def test_unknown_account_and_partner_cannot_edit(self):
        await self.send('/record',999)
        self.assertIn('только разрешённым',self.calls_to(999)[0].text)
        await self.click('d:pstart:2026-01-01',PARTNER)
        self.assertEqual(bot.store.for_user(MASHA).periods(),[])


class AccountStorageTests(unittest.TestCase):
    def test_files_persist_separately_and_main_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'diary.sqlite3'
            stores=DiaryStores(path)
            stores.for_user(MASHA).add('2026-01-01','Заметка','main')
            stores.for_user(TEST_USER).add('2026-01-01','Заметка','test')
            stores.close()
            stores=DiaryStores(path)
            self.assertEqual(stores.for_user(PARTNER).events('2026-01-01')[0]['value'],'main')
            self.assertEqual(stores.for_user(TEST_USER).events('2026-01-01')[0]['value'],'test')
            self.assertTrue(path.with_name(f'diary.test-{TEST_USER}.sqlite3').exists())
            stores.close()
            with self.assertRaises(ValueError): DiaryStores(path,path)
            link=Path(folder)/'alias.sqlite3'
            link.symlink_to(path)
            with self.assertRaises(ValueError): DiaryStores(path,link)
