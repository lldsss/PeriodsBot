import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Chat, Message, User
import bot
from storage import Store, MASHA, PARTNER


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bot.store=Store(':memory:')
        self.state=FSMContext(MemoryStorage(),StorageKey(bot_id=1,chat_id=MASHA,user_id=MASHA))
        self.clock=patch('bot.today',return_value='2026-10-14')
        self.clock.start()
        self.message=AsyncMock()
        self.message.from_user.id=MASHA
        self.message.chat.id=MASHA

    async def asyncTearDown(self):
        self.clock.stop()
        bot.store.db.close()

    def query(self,data,user=MASHA):
        q=AsyncMock()
        q.data=data
        q.from_user.id=user
        q.message=self.message
        return q

    async def click(self,data,user=MASHA):
        await bot.callbacks(self.query(data,user),self.state)

    async def test_access_middleware(self):
        for uid,typ,allowed in [(MASHA,'private',True),(PARTNER,'private',True),(99,'private',False),(MASHA,'group',False)]:
            event=Message(message_id=1,date=datetime.now(),chat=Chat(id=uid,type=typ),from_user=User(id=uid,is_bot=False,first_name='Test')).as_(AsyncMock())
            handler=AsyncMock()
            await bot.Access()(handler,event,{})
            self.assertEqual(handler.await_count,int(allowed))

    async def test_all_date_actions_have_buttons_and_no_future_days(self):
        for action in ('record','day','edit','delete','pstart'):
            shortcuts=bot.day_shortcuts(action)
            self.assertEqual(shortcuts.inline_keyboard[0][0].callback_data,f'd:{action}:2026-10-14')
            values=[b.callback_data for row in bot.date_picker('2026-10',action).inline_keyboard for b in row]
            self.assertIn(f'd:{action}:2026-10-13',values)
            self.assertNotIn(f'd:{action}:2026-10-15',values)
        with patch('bot.today',return_value='2024-03-01'):
            values=[b.callback_data for row in bot.date_picker('2024-02','record').inline_keyboard for b in row]
            self.assertIn('d:record:2024-02-29',values)

    async def test_partner_can_read_always_but_cannot_mutate(self):
        event=bot.store.add('2026-10-01','Заметка','Личная запись')
        await self.click('d:day:2026-10-01',PARTNER)
        self.assertTrue(any('Личная запись' in call.args[0] for call in self.message.answer.call_args_list))
        for data in [f'event:{event}',f'del:event:{event}','d:record:2026-10-01','menu:erase','remind:on','undo:1']:
            await self.click(data,PARTNER)
        self.assertIsNotNone(bot.store.event(event))
        self.assertEqual(bot.store.get_setting('reminder_enabled','no'),'no')
        self.assertIsNone(await self.state.get_state())

    async def test_multiselect_custom_symptoms_and_save(self):
        await bot.show_form(self.message,self.state,'2026-10-01','Симптомы')
        nonce=(await self.state.get_data())['nonce']
        await self.click(f'sym:{nonce}:1')
        await self.click(f'sym:{nonce}:5')
        self.message.text='Мой симптом'
        await bot.text_value(self.message,self.state)
        self.assertEqual(bot.store.events('2026-10-01'),[])
        await self.click(f'done:{nonce}')
        self.assertEqual(bot.store.events('2026-10-01')[0]['value'],'Спазмы, Усталость, Мой симптом')
        self.message.bot.send_message.assert_awaited_once()
        self.assertIsNone(await self.state.get_state())
        # Repeated old button cannot duplicate the event.
        await self.click(f'done:{nonce}')
        self.assertEqual(len(bot.store.events('2026-10-01')),1)

    async def test_no_symptoms_exclusive_and_toggle(self):
        await bot.show_form(self.message,self.state,'2026-10-01','Симптомы')
        nonce=(await self.state.get_data())['nonce']
        await self.click(f'sym:{nonce}:1')
        await self.click(f'sym:{nonce}:0')
        self.assertEqual((await self.state.get_data())['selected'],['Нет симптомов'])
        await self.click(f'sym:{nonce}:5')
        self.assertEqual((await self.state.get_data())['selected'],['Усталость'])
        await self.click(f'sym:{nonce}:5')
        await self.click(f'done:{nonce}')
        self.assertEqual(bot.store.events('2026-10-01'),[])

    async def test_multiple_free_text_events_and_edit_time(self):
        for value in ('Утренние объятия','Вечерние объятия'):
            await bot.show_form(self.message,self.state,'2026-10-01','Близость')
            await bot.save(self.message,self.state,value)
        self.assertEqual(len(bot.store.events('2026-10-01')),2)
        first=bot.store.events('2026-10-01')[0]
        await bot.show_form(self.message,self.state,first['day'],first['kind'],first)
        nonce=(await self.state.get_data())['nonce']
        await self.click(f'at:{nonce}:09:30')
        await self.click(f'keep:{nonce}')
        self.assertEqual(bot.store.event(first['id'])['time'],'09:30')
        self.assertEqual(len(bot.store.events('2026-10-01')),2)

    async def test_pain_validation_and_symptom_prompt(self):
        await bot.show_form(self.message,self.state,'2026-10-01','Боль')
        await bot.save(self.message,self.state,'11')
        self.assertEqual(bot.store.events('2026-10-01'),[])
        await bot.save(self.message,self.state,'7')
        self.assertEqual(bot.store.events('2026-10-01')[0]['value'],'7')
        await bot.show_form(self.message,self.state,'2026-10-01','Симптомы')
        await bot.save(self.message,self.state,'Спазмы')
        markup=self.message.answer.call_args.kwargs['reply_markup']
        self.assertIn('Оценить боль',markup.inline_keyboard[0][0].text)

    async def test_period_start_end_and_edit(self):
        await self.click('d:pstart:2026-10-01')
        pid=bot.store.active_period()['id']
        await self.click(f'd:pe{pid}:2026-10-05')
        self.assertEqual(len(bot.store.period_days('2026-10-14')),5)
        await self.click(f'd:ps{pid}:2026-10-02')
        self.assertEqual(len(bot.store.period_days('2026-10-14')),4)
        self.assertEqual(self.message.bot.send_message.await_count,3)

    async def test_delete_requires_confirmation_and_undo_restores(self):
        event=bot.store.add('2026-10-01','Заметка','test')
        await self.click(f'ask:event:{event}')
        self.assertIsNotNone(bot.store.event(event))
        await self.click(f'del:event:{event}')
        self.assertIsNone(bot.store.event(event))
        await self.click(f'undo:{bot.store.last_action()["id"]}')
        self.assertEqual(bot.store.event(event)['value'],'test')

    async def test_commands_and_reminder_controls(self):
        api=AsyncMock()
        await bot.configure_commands(api)
        names={c.command for c in api.set_my_commands.call_args.args[0]}
        self.assertTrue({'reminders','stats','period','edit','undo'}<=names)
        api.set_chat_menu_button.assert_awaited_once()
        await self.click('remind:20:30')
        await self.click('remind:on')
        self.assertEqual(bot.store.get_setting('reminder_time'),'20:30')
        await self.click('remind:off')
        self.assertEqual(bot.store.get_setting('reminder_enabled'),'no')

    async def test_calendar_and_stats_send_photos(self):
        await bot.show_calendar(self.message,'2026-10')
        self.assertTrue(self.message.answer_photo.call_args.args[0].data.startswith(b'\x89PNG'))
        await bot.show_stats(self.message)
        self.assertEqual(self.message.answer_photo.await_count,2)

    async def test_historical_period_before_existing_one(self):
        pid=bot.store.start_period('2026-10-01')
        bot.store.update_period(pid,end='2026-10-05')
        await self.click('d:paststart:2026-09-01')
        await self.click('m:pastend:2026-09')
        await self.click('d:pastend:2026-09-05')
        self.assertEqual([(p['start'],p['end']) for p in bot.store.periods()],[('2026-09-01','2026-09-05'),('2026-10-01','2026-10-05')])

    async def test_day_summary_is_one_message_with_numbered_controls(self):
        symptom=bot.store.add('2026-10-01','Симптомы','Вздутие, Усталость','16:26')
        pain=bot.store.add('2026-10-01','Боль','6','16:27')
        await bot.show_day(self.message,'2026-10-01',MASHA)
        self.message.answer.assert_awaited_once()
        text=self.message.answer.call_args.args[0]
        self.assertIn('1. 16:26 · Симптомы\nВздутие, Усталость',text)
        self.assertIn('2. 16:27 · Боль\n6/10',text)
        markup=self.message.answer.call_args.kwargs['reply_markup']
        self.assertEqual(markup.inline_keyboard[0][0].callback_data,f'event:{symptom}')
        self.assertEqual(markup.inline_keyboard[1][1].callback_data,f'ask:event:{pain}')

    async def test_long_day_pages_edit_same_message_without_truncating(self):
        values=['🐷'*1500, 'а'*1500, 'б'*1500]
        for value in values:
            bot.store.add('2026-10-01','Заметка',value)
        texts=[]
        page=0
        while True:
            if page==0:
                await bot.show_day(self.message,'2026-10-01',MASHA)
                call=self.message.answer.call_args
            else:
                await self.click(f'page:day:2026-10-01:{page}')
                call=self.message.edit_text.call_args
            texts.append(call.args[0])
            self.assertLessEqual(bot.telegram_length(call.args[0]),4096)
            buttons=[b for row in call.kwargs['reply_markup'].inline_keyboard for b in row]
            if not any(b.text=='Дальше ›' for b in buttons):
                break
            page+=1
            self.assertLess(page,10)
        self.message.answer.assert_awaited_once()
        self.assertGreater(self.message.edit_text.await_count,0)
        for value in values:
            self.assertIn(value,'\n'.join(texts))

    async def test_empty_day_and_partner_summary_have_single_message(self):
        await bot.show_day(self.message,'2026-10-01',PARTNER)
        self.message.answer.assert_awaited_once()
        self.assertIn('пока нет',self.message.answer.call_args.args[0])
        bot.store.start_period('2026-10-01',end='2026-10-05')
        bot.store.add('2026-10-01','Заметка','test')
        self.message.answer.reset_mock()
        await bot.show_day(self.message,'2026-10-01',PARTNER)
        self.message.answer.assert_awaited_once()
        self.assertIn('Месячные с',self.message.answer.call_args.args[0])
        buttons=[b.callback_data for row in self.message.answer.call_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertFalse(any(b.startswith(('event:','ask:','period:')) for b in buttons))
