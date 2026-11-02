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

    async def test_pill_preferences_and_delivery_stay_in_test_account(self):
        await self.click('pill:09:00',TEST_USER)
        await self.click('pill:on',TEST_USER)
        main=bot.store.for_user(MASHA)
        test=bot.store.for_user(TEST_USER)
        self.assertEqual(main.get_setting('pill_enabled','no'),'no')
        self.transport.calls.clear()
        stamp=datetime(2026,10,14,9,0,tzinfo=bot.tz)
        self.assertTrue(await tick(self.api,test,stamp,TEST_USER,kind='pill'))
        self.assertEqual(len(self.calls_to(TEST_USER)),1)
        self.assertEqual(self.calls_to(MASHA),[])
        self.assertEqual(self.calls_to(PARTNER),[])
        await self.click('pill:on',PARTNER)
        self.assertEqual(main.get_setting('pill_enabled','no'),'no')

    async def test_partner_records_intimacy_and_sex_and_notifies_masha(self):
        await self.send('/start',PARTNER)
        start=self.calls_to(PARTNER)[-1]
        self.assertTrue(any(b.callback_data=='menu:record' for row in start.reply_markup.inline_keyboard for b in row))
        await self.send('/record 2026-01-01',PARTNER)
        category_buttons=[b for row in self.calls_to(PARTNER)[-1].reply_markup.inline_keyboard for b in row if b.callback_data.startswith('kind:')]
        self.assertEqual({b.text for b in category_buttons},{'Близость','Секс'})
        await self.click('kind:2026-01-01:4',PARTNER)
        state=await bot.dp.fsm.get_context(bot=self.api,chat_id=PARTNER,user_id=PARTNER).get_data()
        await self.click(f'clock:{state["nonce"]}',PARTNER)
        await self.send('09:30',PARTNER)
        await self.send('Обнимались утром',PARTNER)
        await self.click('kind:2026-01-01:5',PARTNER)
        state=await bot.dp.fsm.get_context(bot=self.api,chat_id=PARTNER,user_id=PARTNER).get_data()
        await self.click(f'val:{state["nonce"]}:0',PARTNER)
        main=bot.store.for_user(MASHA)
        entries=main.events('2026-01-01')
        self.assertEqual(len(entries),2)
        self.assertTrue(all(e['author_id']==PARTNER for e in entries))
        self.assertTrue(any(e['time']=='09:30' and e['value']=='Обнимались утром' for e in entries))
        self.assertEqual(main.marks('2026-01','2026-01-01')[1],{'Б','С'})
        notices=[getattr(c,'text','') or '' for c in self.calls_to(MASHA)]
        self.assertEqual(len(notices),2)
        self.assertTrue(all('Партнёр добавил' in text for text in notices))
        self.assertEqual(bot.store.for_user(TEST_USER).events('2026-01-01'),[])
        await self.send('/day 2026-01-01',MASHA)
        self.assertIn('от партнёра',self.calls_to(MASHA)[-1].text)

    async def test_partner_cannot_use_other_categories_or_edit_delete_undo(self):
        main=bot.store.for_user(MASHA)
        eid=main.add('2026-01-01','Боль','7')
        for payload in ['kind:2026-01-01:1','kind:2026-01-01:7',f'event:{eid}',f'del:event:{eid}',f'undo:{main.last_action()["id"]}','d:pstart:2026-01-01','pill:on']:
            await self.click(payload,PARTNER)
        self.assertEqual(main.event(eid)['value'],'7')
        self.assertEqual(main.periods(),[])
        self.assertEqual(main.get_setting('pill_enabled','no'),'no')
        # A crafted state must not bypass the save-layer check.
        ctx=bot.dp.fsm.get_context(bot=self.api,chat_id=PARTNER,user_id=PARTNER)
        await ctx.set_state(bot.Entry.value)
        await ctx.update_data(kind='Боль',day='2026-01-01',time='09:00',nonce='forged',edit_id=None)
        await self.click('val:forged:0',PARTNER)
        await self.send('2',PARTNER)
        self.assertEqual(len(main.events('2026-01-01')),1)

    async def test_partner_button_flow_stale_click_and_failed_notification(self):
        from unittest.mock import patch
        from aiogram.exceptions import TelegramForbiddenError
        from aiogram.methods import SendMessage
        await self.click('menu:record',PARTNER)
        buttons=[b for row in self.calls_to(PARTNER)[-1].reply_markup.inline_keyboard for b in row]
        self.assertTrue(any(b.callback_data.startswith('m:record:') for b in buttons))
        await self.click('m:record:2026-01',PARTNER)
        await self.click('d:record:2026-01-01',PARTNER)
        await self.click('kind:2026-01-01:5',PARTNER)
        ctx=bot.dp.fsm.get_context(bot=self.api,chat_id=PARTNER,user_id=PARTNER)
        state=await ctx.get_data()
        await self.click(f'clock:{state["nonce"]}',PARTNER)
        await self.click(f'at:{state["nonce"]}:21:00',PARTNER)
        # A blocked recipient must not lose the entry or cause a duplicate save.
        original=self.transport.make_request
        async def failing(api,method,timeout=None):
            if method.__api_method__=='sendMessage' and method.chat_id==MASHA:
                raise TelegramForbiddenError(method=SendMessage(chat_id=MASHA,text='test'),message='blocked')
            return await original(api,method,timeout)
        with patch.object(self.transport,'make_request',side_effect=failing):
            await self.click(f'val:{state["nonce"]}:0',PARTNER)
        entries=bot.store.for_user(MASHA).events('2026-01-01')
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['time'],'21:00')
        self.assertTrue(any('не доставлено' in (getattr(c,'text','') or '') for c in self.calls_to(PARTNER)))
        await self.click(f'val:{state["nonce"]}:0',PARTNER)
        self.assertEqual(len(bot.store.for_user(MASHA).events('2026-01-01')),1)
        self.assertIsNone(await ctx.get_state())

    async def test_partner_denied_management_commands_and_forged_edit_state(self):
        main=bot.store.for_user(MASHA)
        eid=main.add('2026-01-01','Боль','7')
        for command in ('/period','/edit','/delete','/erase','/undo','/reminders','/pills'):
            await self.send(command,PARTNER)
        for data in ('menu:erase','choose:pstart','m:pstart:2026-01','ask:all:all','del:all:all','periods','period:1','remind:on'):
            await self.click(data,PARTNER)
        ctx=bot.dp.fsm.get_context(bot=self.api,chat_id=PARTNER,user_id=PARTNER)
        await ctx.set_state(bot.Entry.value)
        await ctx.update_data(kind='Секс',day='2026-01-01',time='21:00',nonce='forged-edit',edit_id=eid)
        await self.click('val:forged-edit:0',PARTNER)
        await self.send('Исправить чужую запись',PARTNER)
        self.assertEqual(main.events('2026-01-01'),[main.event(eid)])
        self.assertEqual(main.event(eid)['value'],'7')
        self.assertIsNone(main.get_setting('reminder_enabled'))
        self.assertEqual(main.periods(),[])
        self.assertEqual(self.calls_to(MASHA),[])


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

    def test_upgrade_existing_event_database_preserves_author_and_undo(self):
        import sqlite3
        import json
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'diary.sqlite3'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, time TEXT, kind TEXT, value TEXT)')
            db.execute("INSERT INTO events(day,time,kind,value) VALUES ('2026-01-01','10:00','Секс','Да')")
            db.execute('CREATE TABLE history(id INTEGER PRIMARY KEY AUTOINCREMENT,label TEXT,snapshot TEXT)')
            snapshot={'events':[{'id':1,'day':'2026-01-01','time':'10:00','kind':'Секс','value':'Да'}],'periods':[]}
            db.execute('INSERT INTO history(label,snapshot) VALUES (?,?)',('старое действие',json.dumps(snapshot)))
            db.commit()
            db.close()
            stores=DiaryStores(path)
            main=stores.for_user(MASHA)
            self.assertEqual(main.event(1)['value'],'Да')
            main.undo(main.last_action()['id'])
            self.assertIsNone(main.event(1)['author_id'])
            eid=main.add('2026-01-01','Близость','Да',author_id=PARTNER)
            stores.close()
            stores=DiaryStores(path)
            self.assertEqual(stores.for_user(MASHA).event(eid)['author_id'],PARTNER)
            self.assertEqual(stores.for_user(TEST_USER).events('2026-01-01'),[])
            stores.close()
