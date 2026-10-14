import asyncio
import logging
import os
from datetime import date, datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from storage import MASHA, PARTNER, Store

LABELS = ['Месячные', 'Симптомы', 'Настроение', 'Самочувствие', 'Близость', 'Секс', 'Заметка']
OPTIONS = {
    'Месячные': ['Да', 'Нет'], 'Близость': ['Да', 'Нет'], 'Секс': ['Да', 'Нет'],
    'Симптомы': ['Нет симптомов', 'Спазмы', 'Головная боль', 'Вздутие', 'Боль в груди', 'Усталость'],
    'Настроение': ['Хорошее', 'Спокойное', 'Грустное', 'Тревожное', 'Раздражённое'],
    'Самочувствие': ['Отличное', 'Нормальное', 'Плохое'],
}
dp = Dispatcher()
store = None
tz = None


class Access(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = event.from_user
        chat = event.chat if isinstance(event, Message) else getattr(event.message, 'chat', None)
        if not user or user.id not in (MASHA, PARTNER) or not chat or chat.type != 'private':
            if isinstance(event, CallbackQuery):
                await event.answer('Доступ закрыт', show_alert=True)
            elif chat and chat.type == 'private':
                await event.answer('Этот бот доступен только двум владельцам.')
            return
        return await handler(event, data)


dp.message.outer_middleware(Access())
dp.callback_query.outer_middleware(Access())


class Entry(StatesGroup):
    value = State()


def keyboard(items):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🐷 ' + label, callback_data=key)] for label, key in items])


def menu(user_id):
    items = [('Календарик 🌸', 'menu:calendar'), ('Записи сегодня 💌', 'menu:day'), ('Мой цикл 🌙', 'menu:forecast')]
    if user_id == MASHA:
        items.insert(0, ('Как я сегодня? 🎀', 'menu:record'))
        items.append(('Делиться с любимым 💕', 'menu:share'))
    return keyboard(items)


def today():
    return datetime.now(tz).date().isoformat()


def parse_day(value):
    day = date.fromisoformat(value)
    if day > datetime.now(tz).date():
        raise ValueError('Будущая дата')
    return day.isoformat()


async def notify(bot, message):
    if store.sharing():
        try:
            await bot.send_message(PARTNER, message)
            return True
        except TelegramAPIError:
            return False
    return None


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == MASHA:
        await message.answer('Маша, привет! Я твой хрю-дневничок 🐷🌷\n'
            '/record — запись за сегодня\n/record ГГГГ-ММ-ДД — за другой день\n'
            '/day [ГГГГ-ММ-ДД] — записи дня\n/calendar [ГГГГ-ММ] — календарь\n'
            '/forecast — приблизительный прогноз\n/share — управление обменом\n'
            '/delete ГГГГ-ММ-ДД — удалить день\n/erase — удалить весь дневник\n/cancel — отмена\n\n'
            'Обмен изначально выключен. Если включить его, партнёр получит новые записи и доступ ко всему дневнику. '
            'Уже отправленные сообщения останутся в его чате после отключения обмена или удаления записей. '
            'Данные хранятся на сервере бота; Telegram-боты не используют сквозное шифрование. '
            'При сильной боли, необычном кровотечении или резком ухудшении самочувствия обратись к врачу.')
    else:
        await message.answer('Привет! Когда Маша включит обмен, здесь будут её новые записи.\n'
                             '/calendar [ГГГГ-ММ] — календарь\n/day [ГГГГ-ММ-ДД] — дневник\n/forecast — оценка следующего цикла')
    await message.answer('🐷🌷 Хрю-дневничок · маленькая забота о себе', reply_markup=menu(message.from_user.id))


@dp.callback_query(F.data.startswith('menu:'))
async def menu_action(query: CallbackQuery, state: FSMContext):
    command = query.data.split(':')[1]
    await query.answer()
    await state.clear()
    message = query.message.model_copy(update={'from_user': query.from_user, 'text': '/' + command}).as_(query.bot)
    if command == 'record':
        await record(message, state)
    elif command == 'share':
        await share(message)
    else:
        await read(message)


@dp.message(Command('cancel'))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer('Хрю, ввод отменён 🐷🌸')


@dp.message(Command('share'))
async def share(message: Message):
    if message.from_user.id != MASHA:
        return
    await message.answer('Обмен ' + ('включён.' if store.sharing() else 'выключен.') +
                         '\nВключение открывает партнёру весь дневник, включая секс, близость и заметки, и отправляет новые записи. Можно отключить в любой момент.',
                         reply_markup=keyboard([('Включить обмен', 'share:yes'), ('Отключить обмен', 'share:no')]))


@dp.callback_query(F.data.startswith('share:'))
async def set_share(query: CallbackQuery):
    if query.from_user.id != MASHA:
        await query.answer('Только Маша может изменить обмен.')
        return
    store.set_sharing(query.data == 'share:yes')
    await query.answer()
    await query.message.answer('Обмен включён.' if store.sharing() else 'Обмен выключен. Доступ партнёра к дневнику закрыт.')


@dp.message(Command('record'))
async def record(message: Message, state: FSMContext):
    if message.from_user.id != MASHA:
        return
    await state.clear()
    try:
        parts = message.text.split()
        day = parse_day(parts[1]) if len(parts) > 1 else today()
    except ValueError:
        await message.answer('Укажи прошедшую или сегодняшнюю дату: /record ГГГГ-ММ-ДД')
        return
    await message.answer(f'Запись за {day}. 🐽 Выбери категорию 🌸:', reply_markup=keyboard([(label, f'kind:{day}:{i}') for i, label in enumerate(LABELS)]))


@dp.callback_query(F.data.startswith('kind:'))
async def kind(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != MASHA:
        await query.answer('Записи доступны только Маше.')
        return
    _, day, index = query.data.split(':')
    label = LABELS[int(index)]
    await state.set_state(Entry.value)
    await state.update_data(day=day, kind=label)
    choices = OPTIONS.get(label, [])
    await query.answer()
    await query.message.answer(f'{label}, {day}: отправь текст (до 1500 символов) или выбери значение. '
                               'Несколько симптомов можно перечислить через запятую. Повторная запись категории заменяет прежнюю.',
                               reply_markup=keyboard([(v, f'value:{day}:{index}:{i}') for i, v in enumerate(choices)]) if choices else None)


async def save(message, state, value):
    data = await state.get_data()
    if data['kind'] in ('Месячные', 'Секс', 'Близость') and value not in ('Да', 'Нет'):
        await message.answer('Для этой категории выбери «Да» или «Нет».')
        return
    store.put(data['day'], data['kind'], value)
    await state.clear()
    delivered = await notify(message.bot, f"Маша · {data['day']}\n{data['kind']}: {value}")
    await message.answer('Хрю! Записала в твой дневничок 🐷💕' + (' Партнёру отправлено.' if delivered else ' Не удалось уведомить партнёра. Запись сохранена; проверьте, что он нажал /start, и соединение работает.' if delivered is False else ' Обмен выключен.'), reply_markup=menu(MASHA))


@dp.callback_query(F.data.startswith('value:'))
async def choice(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != MASHA:
        await query.answer('Доступ закрыт.')
        return
    _, day, index, option = query.data.split(':')
    data = await state.get_data()
    if await state.get_state() != Entry.value.state or data.get('day') != day or data.get('kind') != LABELS[int(index)]:
        await query.answer('Эта кнопка устарела. Начни /record заново.')
        return
    await query.answer()
    await save(query.message, state, OPTIONS[LABELS[int(index)]][int(option)])


@dp.message(Command('day', 'calendar', 'forecast'))
async def read(message: Message):
    if message.from_user.id == PARTNER and not store.sharing():
        await message.answer('Маша пока не включила обмен дневником.')
        return
    parts = message.text.split()
    command = parts[0].split('@')[0]
    try:
        if command == '/calendar':
            month = parts[1] if len(parts) > 1 else today()[:7]
            text = store.calendar(month)
            first = date.fromisoformat(month + '-01')
            offset = first.year * 12 + first.month - 1
            neighbors = []
            for delta, label in [(-1, 'Предыдущий месяц'), (1, 'Следующий месяц')]:
                year, month_index = divmod(offset + delta, 12)
                if 1 <= year <= 9999:
                    neighbors.append((label, f'month:{year:04d}-{month_index + 1:02d}'))
            await message.answer('🐷🌸 Машин календарик\n<pre>' + escape(text) + '</pre>', parse_mode='HTML', reply_markup=keyboard(neighbors))
        elif command == '/forecast':
            await message.answer(store.forecast())
        else:
            day = parse_day(parts[1]) if len(parts) > 1 else today()
            rows = store.rows(day)
            await message.answer(day)
            for label, value in rows:
                await message.answer(f'{label}: {value}')
            if not rows:
                await message.answer('Здесь пока пусто 🐷🌷')
    except ValueError:
        await message.answer('Неверная дата. Формат: ГГГГ-ММ для календаря, ГГГГ-ММ-ДД для дня.')


@dp.callback_query(F.data.startswith('month:'))
async def month_page(query: CallbackQuery):
    await query.answer()
    message = query.message.model_copy(update={'from_user': query.from_user, 'text': '/calendar ' + query.data.split(':')[1]}).as_(query.bot)
    await read(message)


@dp.message(Command('delete', 'erase'))
async def delete(message: Message, state: FSMContext):
    if message.from_user.id != MASHA:
        return
    await state.clear()
    parts = message.text.split()
    if parts[0].split('@')[0] == '/erase':
        target = 'all'
    else:
        try:
            target = parse_day(parts[1])
        except (ValueError, IndexError):
            await message.answer('Формат: /delete ГГГГ-ММ-ДД')
            return
    await message.answer('Удалить ' + ('весь дневник' if target == 'all' else target) + '? Отправленные сообщения в Telegram сохранятся.', reply_markup=keyboard([('Да, удалить', 'delete:' + target)]))


@dp.callback_query(F.data.startswith('delete:'))
async def confirm_delete(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != MASHA:
        await query.answer('Доступ закрыт.')
        return
    await state.clear()
    target = query.data.split(':')[1]
    store.clear() if target == 'all' else store.delete(target)
    await query.answer('Удалено')
    await query.message.edit_text('Записи удалены из дневника.')
    await notify(query.bot, 'Маша удалила ' + ('весь дневник.' if target == 'all' else f'записи за {target}.'))


@dp.message(Entry.value)
async def text_value(message: Message, state: FSMContext):
    if message.from_user.id != MASHA:
        return
    if not message.text or message.text.startswith('/') or not 1 <= len(message.text.strip()) <= 1500:
        await message.answer('Пришли текст от 1 до 1500 символов или /cancel.')
        return
    await save(message, state, message.text.strip())


async def main():
    global store, tz
    token = os.environ.get('BOT_TOKEN')
    if not token:
        raise SystemExit('Задайте BOT_TOKEN через защищённые переменные среды.')
    tz = ZoneInfo(os.environ.get('BOT_TIMEZONE', 'Europe/Moscow'))
    path = Path(os.environ.get('BOT_DB', 'data/diary.sqlite3'))
    path.parent.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    store = Store(path)
    bot = Bot(token)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        store.db.close()
        await bot.session.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
