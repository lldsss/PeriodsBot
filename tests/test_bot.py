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
