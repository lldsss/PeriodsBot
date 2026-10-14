import unittest
from datetime import datetime
from unittest.mock import AsyncMock
from aiogram.types import Chat, Message, User
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
import bot
from storage import Store, MASHA, PARTNER

class DiaryTests(unittest.TestCase):
    def setUp(self):
        self.s = Store(':memory:')
    def tearDown(self):
        self.s.db.close()
    def test_consent(self):
        self.assertFalse(self.s.sharing())
        self.s.set_sharing(True)
        self.assertTrue(self.s.sharing())
        self.s.set_sharing(False)
        self.assertFalse(self.s.sharing())
    def test_calendar_edit_delete(self):
        for kind in ('Месячные', 'Близость', 'Секс'):
            self.s.put('2026-10-01', kind, 'Да')
        self.assertIn('1БМС', self.s.calendar('2026-10'))
        self.s.put('2026-10-01', 'Секс', 'Нет')
        self.assertIn('1БМ ', self.s.calendar('2026-10'))
        self.s.delete('2026-10-01')
        self.assertEqual(self.s.rows('2026-10-01'), [])
    def test_forecast(self):
        self.assertIn('минимум три', self.s.forecast())
        for day in ('2026-08-01', '2026-08-02', '2026-08-29', '2026-09-26'):
            self.s.put(day, 'Месячные', 'Да')
        self.assertIn('24.10.2026', self.s.forecast())
        self.s.clear()
        self.assertEqual(self.s.rows('2026-08-01'), [])

class AccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_access(self):
        for uid, typ, allowed in [(MASHA, 'private', True), (PARTNER, 'private', True), (99, 'private', False), (MASHA, 'group', False)]:
            event = Message(message_id=1, date=datetime.now(), chat=Chat(id=uid, type=typ), from_user=User(id=uid, is_bot=False, first_name='Test')).as_(AsyncMock())
            handler = AsyncMock()
            await bot.Access()(handler, event, {})
            self.assertEqual(handler.await_count, int(allowed))
    async def test_partner_read_denied(self):
        bot.store = Store(':memory:')
        m = AsyncMock()
        m.from_user.id = PARTNER
        m.text = '/day'
        await bot.read(m)
        self.assertIn('не включила', m.answer.call_args.args[0])
        bot.store.db.close()
    async def test_save_notifications(self):
        bot.store = Store(':memory:')
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=MASHA, user_id=MASHA))
        m = AsyncMock()
        for enabled in (False, True):
            bot.store.set_sharing(enabled)
            await state.set_state(bot.Entry.value)
            await state.update_data(day='2026-10-01', kind='Настроение')
            await bot.save(m, state, 'Хорошее')
            self.assertEqual(bot.store.rows('2026-10-01'), [('Настроение', 'Хорошее')])
            self.assertIsNone(await state.get_state())
        m.bot.send_message.assert_awaited_once_with(PARTNER, 'Маша · 2026-10-01\nНастроение: Хорошее')
        bot.store.db.close()

class RecordFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from unittest.mock import patch
        self.clock = patch('bot.today', return_value='2026-10-14')
        self.clock.start()
        self.state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=MASHA, user_id=MASHA))
        bot.store = Store(':memory:')

    async def asyncTearDown(self):
        self.clock.stop()
        bot.store.db.close()

    def query(self, data, user=MASHA):
        q = AsyncMock()
        q.from_user.id = user
        q.data = data
        return q

    async def test_record_offers_dates_without_typing(self):
        m = AsyncMock()
        m.from_user.id = MASHA
        m.text = '/record'
        await bot.record(m, self.state)
        markup = m.answer.call_args.kwargs['reply_markup']
        values = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn('pick:2026-10-14', values)
        self.assertIn('pick:2026-10-13', values)
        self.assertIn('pickmonth:2026-10', values)

    async def test_picker_leap_year_and_future_dates(self):
        from unittest.mock import patch
        with patch('bot.today', return_value='2024-03-01'):
            feb = bot.date_picker('2024-02')
            values = [b.callback_data for row in feb.inline_keyboard for b in row]
            self.assertIn('pick:2024-02-29', values)
            march = bot.date_picker('2024-03')
            values = [b.callback_data for row in march.inline_keyboard for b in row]
            self.assertIn('pick:2024-03-01', values)
            self.assertNotIn('pick:2024-03-02', values)
            self.assertNotIn('pickmonth:2024-04', values)

    async def test_binary_prompt_and_button_save(self):
        for label in bot.BINARY:
            index = bot.LABELS.index(label)
            q = self.query(f'kind:2026-10-13:{index}')
            await bot.kind(q, self.state)
            prompt = q.message.answer.call_args.args[0]
            self.assertIn('«Да» или «Нет»', prompt)
            self.assertNotIn('отправь текст', prompt)
            self.assertNotIn('симптомов', prompt)
            choice = self.query(f'value:2026-10-13:{index}:0')
            await bot.choice(choice, self.state)
            self.assertIn((label, 'Да'), bot.store.rows('2026-10-13'))
            markup = choice.message.answer.call_args.kwargs['reply_markup']
            self.assertEqual(markup.inline_keyboard[0][0].callback_data, 'pick:2026-10-13')

    async def test_invalid_binary_text_keeps_buttons(self):
        await self.state.set_state(bot.Entry.value)
        await self.state.update_data(day='2026-10-13', kind='Месячные')
        m = AsyncMock()
        await bot.save(m, self.state, 'круто')
        self.assertEqual(bot.store.rows('2026-10-13'), [])
        self.assertEqual(await self.state.get_state(), bot.Entry.value.state)
        self.assertIn('reply_markup', m.answer.call_args.kwargs)

    async def test_partner_cannot_select_record_date(self):
        q = self.query('pick:2026-10-13', PARTNER)
        await bot.pick_day(q, self.state)
        q.message.answer.assert_not_awaited()
        q = self.query('pickmonth:2026-10', PARTNER)
        await bot.pick_month(q, self.state)
        q.message.edit_text.assert_not_awaited()

    async def test_back_to_categories_cancels_previous_input(self):
        await self.state.set_state(bot.Entry.value)
        await self.state.update_data(day='2026-10-13', kind='Заметка')
        q = self.query('pick:2026-10-13')
        await bot.pick_day(q, self.state)
        self.assertIsNone(await self.state.get_state())
        self.assertIn('13.10.2026', q.message.answer.call_args.args[0])
        stale = self.query('value:2026-10-13:5:0')
        await bot.choice(stale, self.state)
        self.assertIn('устарела', stale.answer.call_args.args[0])
        self.assertEqual(bot.store.rows('2026-10-13'), [])

    async def test_free_text_intimacy_mood_and_wellbeing(self):
        for label in ('Секс', 'Близость', 'Настроение', 'Самочувствие'):
            index = bot.LABELS.index(label)
            q = self.query(f'kind:2026-10-13:{index}')
            await bot.kind(q, self.state)
            self.assertIn('своими словами', q.message.answer.call_args.args[0])
            m = AsyncMock()
            m.from_user.id = MASHA
            m.text = 'Было прекрасно'
            await bot.text_value(m, self.state)
            self.assertIn((label, 'Было прекрасно'), bot.store.rows('2026-10-13'))
        self.assertIn('13БС', bot.store.calendar('2026-10'))
        bot.store.put('2026-10-13', 'Секс', 'нет')
        self.assertIn('13Б ', bot.store.calendar('2026-10'))
