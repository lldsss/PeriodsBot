import asyncio
import calendar
import logging
import os
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, MenuButtonCommands, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from storage import MASHA, PARTNER, Store

LABELS = ['Месячные', 'Симптомы', 'Настроение', 'Самочувствие', 'Близость', 'Секс', 'Заметка', 'Боль']
OPTIONS = {
    'Месячные': ['Да', 'Нет'], 'Близость': ['Да', 'Нет'], 'Секс': ['Да', 'Нет'],
    'Симптомы': ['Нет симптомов', 'Спазмы', 'Головная боль', 'Вздутие', 'Боль в груди', 'Усталость'],
    'Настроение': ['Хорошее', 'Спокойное', 'Грустное', 'Тревожное', 'Раздражённое'],
    'Самочувствие': ['Отличное', 'Нормальное', 'Плохое'],
}
OPTIONS['Симптомы'] += ['Боль в пояснице', 'Боль внизу живота', 'Тошнота', 'Головокружение', 'Сонливость', 'Бессонница', 'Отёчность', 'Акне', 'Изменение аппетита', 'Тяга к сладкому', 'Запор', 'Диарея', 'Приливы', 'Озноб']
OPTIONS['Настроение'] += ['Радостное', 'Нежное', 'Влюблённое', 'Энергичное', 'Вдохновлённое', 'Чувствительное', 'Плаксивое', 'Апатичное', 'Одинокое', 'Перепады настроения', 'Злое', 'Уверенное']
OPTIONS['Боль'] = [str(n) for n in range(1, 11)] + ['Нет боли']
BINARY = {'Месячные'}
MONTHS = ('Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь')
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
    items = [('Календарик 🌸', 'menu:calendar'), ('Посмотреть записи 💌', 'menu:day'), ('Мой цикл 🌙', 'menu:forecast')]
    if user_id == MASHA:
        items.insert(0, ('Добавить запись 🎀', 'menu:record'))
        items.append(('Делиться с любимым 💕', 'menu:share'))
        items.append(('Удалить записи дня', 'menu:delete'))
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
            '/record — добавить запись: выбери день кнопками\n'
            '/day — выбрать день и посмотреть записи\n/calendar — календарь\n'
            '/forecast — приблизительный прогноз\n/share — управление обменом\n'
            '/delete — выбрать день для удаления\n/erase — удалить весь дневник\n/cancel — отмена\n\n'
            'Обмен изначально выключен. Если включить его, партнёр получит новые записи и доступ ко всему дневнику. '
            'Уже отправленные сообщения останутся в его чате после отключения обмена или удаления записей. '
            'Данные хранятся на сервере бота; Telegram-боты не используют сквозное шифрование. '
            'При сильной боли, необычном кровотечении или резком ухудшении самочувствия обратись к врачу.')
    else:
        await message.answer('Привет! Когда Маша включит обмен, здесь будут её новые записи.\n'
                             '/calendar — календарь\n/day — выбрать день дневника\n/forecast — оценка следующего цикла')
    await message.answer('🐷🌷 Хрю-дневничок · маленькая забота о себе', reply_markup=menu(message.from_user.id))


@dp.callback_query(F.data.startswith('menu:'))
async def menu_action(query: CallbackQuery, state: FSMContext):
    command = query.data.split(':')[1]
    await query.answer()
    await state.clear()
    message = query.message.model_copy(update={'from_user': query.from_user, 'text': '/' + command}).as_(query.bot)
    if command == 'home':
        await message.answer('🐷🌷 Хрю-дневничок', reply_markup=menu(query.from_user.id))
    elif command == 'record':
        await record(message, state)
    elif command == 'delete':
        await delete(message, state)
    elif command == 'share':
        await share(message)
    else:
        await read(message, state)


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


def date_label(day):
    return date.fromisoformat(day).strftime('%d.%m.%Y')


def day_shortcuts(action="record"):
    current = date.fromisoformat(today())
    prefix = "pick" if action == "record" else f"select:{action}"
    month_prefix = "pickmonth" if action == "record" else f"selectmonth:{action}"
    return keyboard([
        ('Сегодня 🌷', f'{prefix}:{current.isoformat()}'),
        ('Вчера 🌙', f'{prefix}:{(current - timedelta(days=1)).isoformat()}'),
        ('Другой день 📅', f'{month_prefix}:{current:%Y-%m}'),
        ('Главное меню', 'menu:home'),
    ])


def date_picker(month, action="record"):
    prefix = "pick" if action == "record" else f"select:{action}"
    month_prefix = "pickmonth" if action == "record" else f"selectmonth:{action}"
    first = date.fromisoformat(month + '-01')
    current = date.fromisoformat(today())
    rows = [[InlineKeyboardButton(text=f'{MONTHS[first.month - 1]} {first.year}', callback_data='picknoop')],
            [InlineKeyboardButton(text=d, callback_data='picknoop') for d in ('Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс')]]
    for week in calendar.monthcalendar(first.year, first.month):
        row = []
        for number in week:
            day = first.replace(day=number) if number else None
            enabled = day is not None and day <= current
            row.append(InlineKeyboardButton(text=str(number) if enabled else '·',
                       callback_data=f'{prefix}:{day.isoformat()}' if enabled else 'picknoop'))
        rows.append(row)
    offset = first.year * 12 + first.month - 1
    navigation = []
    for delta, label in ((-1, '‹'), (1, '›')):
        year, month_index = divmod(offset + delta, 12)
        if 1 <= year <= 9999 and (year, month_index + 1) <= (current.year, current.month):
            navigation.append(InlineKeyboardButton(text=label, callback_data=f'{month_prefix}:{year:04d}-{month_index + 1:02d}'))
    rows.append(navigation)
    rows.append([InlineKeyboardButton(text='🐷 Назад', callback_data=f'menu:{action}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def categories(message, day):
    await message.answer(f'🐽 Запись за {date_label(day)}. Что отметим? 🌸',
        reply_markup=keyboard([(label, f'kind:{day}:{i}') for i, label in enumerate(LABELS)] +
                              [('Выбрать другой день 📅', 'menu:record'), ('Главное меню', 'menu:home')]))


@dp.message(Command('record'))
async def record(message: Message, state: FSMContext):
    if message.from_user.id != MASHA:
        return
    await state.clear()
    parts = message.text.split()
    if len(parts) > 1:
        try:
            day = parse_day(parts[1])
        except ValueError:
            pass
        else:
            await categories(message, day)
            return
    await message.answer('🐷 За какой день добавим запись?', reply_markup=day_shortcuts())


@dp.callback_query(F.data.startswith('pickmonth:'))
async def pick_month(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != MASHA:
        await query.answer('Записи доступны только Маше.')
        return
    await state.clear()
    await query.answer()
    await query.message.edit_text('🐷 Выбери день в календарике 🌸', reply_markup=date_picker(query.data.split(':')[1]))


async def can_select(query, action):
    if action not in ('day', 'delete') or (action == 'delete' and query.from_user.id != MASHA):
        await query.answer('Доступ закрыт.')
        return False
    if query.from_user.id == PARTNER and not store.sharing():
        await query.answer('Маша пока не включила обмен дневником.')
        return False
    return True


@dp.callback_query(F.data.startswith('selectmonth:'))
async def select_month(query: CallbackQuery, state: FSMContext):
    _, action, month = query.data.split(':')
    if not await can_select(query, action):
        return
    await state.clear()
    await query.answer()
    await query.message.edit_text('🐷 Выбери день 🌸', reply_markup=date_picker(month, action))


@dp.callback_query(F.data.startswith('select:'))
async def select_day(query: CallbackQuery, state: FSMContext):
    _, action, value = query.data.split(':')
    if not await can_select(query, action):
        return
    try:
        day = parse_day(value)
    except ValueError:
        await query.answer('Выбери сегодняшний или прошедший день.')
        return
    await state.clear()
    await query.answer()
    message = query.message.model_copy(update={'from_user': query.from_user, 'text': f'/{action} {day}'}).as_(query.bot)
    if action == 'day':
        await read(message, state)
    else:
        await delete(message, state)


@dp.callback_query(F.data == 'picknoop')
async def pick_noop(query: CallbackQuery):
    await query.answer()


@dp.callback_query(F.data.startswith('pick:'))
async def pick_day(query: CallbackQuery, state: FSMContext):
    if query.from_user.id != MASHA:
        await query.answer('Записи доступны только Маше.')
        return
    try:
        day = parse_day(query.data.split(':')[1])
    except ValueError:
        await query.answer('Выбери сегодняшний или прошедший день.')
        return
    await state.clear()
    await query.answer()
    await categories(query.message, day)


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
    if label in BINARY:
        prompt = 'Нажми «Да» или «Нет» ниже 🐷'
    elif label in ('Секс', 'Близость'):
        prompt = 'Выбери «Да»/«Нет» или напиши своими словами (до 1500 символов) 💕\nТекстовая запись отмечается в календаре как событие. Если события не было, выбери «Нет».'
    elif label == 'Боль':
        prompt = 'Оцени боль от 1 до 10: 1 — едва заметная, 10 — самая сильная. Нажми число или отправь его текстом. Если ничего не болит, выбери «Нет боли».'
    elif label == 'Симптомы':
        prompt = 'Выбери симптом или напиши несколько через запятую (до 1500 символов).'
    elif label == 'Заметка':
        prompt = 'Напиши, что хочется сохранить в дневничке (до 1500 символов) 💌'
    else:
        prompt = 'Выбери вариант или опиши своими словами (до 1500 символов) 🌷'
    await query.message.answer(f'{label} · {date_label(day)}\n{prompt}\nПовторная запись заменит прежнюю.',
        reply_markup=keyboard([(v, f'value:{day}:{index}:{i}') for i, v in enumerate(choices)] +
                              [('Назад к категориям', f'pick:{day}')]))


async def save(message, state, value):
    data = await state.get_data()
    if data['kind'] in BINARY and value not in ('Да', 'Нет'):
        index = LABELS.index(data['kind'])
        await message.answer('Нажми одну из кнопок 🐷', reply_markup=keyboard([(v, f"value:{data['day']}:{index}:{i}") for i, v in enumerate(OPTIONS[data['kind']])]))
        return
    if data['kind'] == 'Боль' and value not in OPTIONS['Боль']:
        await message.answer('Выбери число от 1 до 10 или «Нет боли».', reply_markup=keyboard([(v, f"value:{data['day']}:{LABELS.index('Боль')}:{i}") for i, v in enumerate(OPTIONS['Боль'])]))
        return
    store.put(data['day'], data['kind'], value)
    await state.clear()
    delivered = await notify(message.bot, f"Маша · {data['day']}\n{data['kind']}: {value}")
    await message.answer('Хрю! Записала в твой дневничок 🐷💕' + (' Партнёру отправлено.' if delivered else ' Не удалось уведомить партнёра. Запись сохранена; проверьте, что он нажал /start, и соединение работает.' if delivered is False else ' Обмен выключен.'), reply_markup=keyboard([('Добавить ещё за этот день 🌸', f"pick:{data['day']}"), ('Выбрать другой день 📅', 'menu:record'), ('Главное меню', 'menu:home')]))
    if data['kind'] == 'Симптомы' and value != 'Нет симптомов':
        await message.answer('Есть боль? Можешь оценить её от 1 до 10 🐷', reply_markup=keyboard([('Оценить боль', f"kind:{data['day']}:{LABELS.index('Боль')}"), ('Не сейчас', 'menu:home')]))


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
async def read(message: Message, state: FSMContext = None):
    if state is not None:
        await state.clear()
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
            if len(parts) == 1:
                await message.answer('🐷 За какой день показать записи?', reply_markup=day_shortcuts('day'))
                return
            day = parse_day(parts[1])
            rows = store.rows(day)
            await message.answer(date_label(day))
            for label, value in rows:
                await message.answer(f'{label}: {value}')
            if not rows:
                await message.answer('Здесь пока пусто 🐷🌷')
    except ValueError:
        await message.answer('Выбери день кнопками 🐷', reply_markup=day_shortcuts('day'))


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
            await message.answer('🐷 За какой день удалить записи?', reply_markup=day_shortcuts('delete'))
            return
    await message.answer('Удалить ' + ('весь дневник' if target == 'all' else date_label(target)) + '? Отправленные сообщения в Telegram сохранятся.', reply_markup=keyboard([('Да, удалить', 'delete:' + target), ('Отмена', 'menu:home')]))


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


async def configure_commands(bot):
    common = [('start', 'Главное меню 🐷'), ('day', 'Посмотреть записи дня'),
              ('calendar', 'Календарик 🌸'), ('forecast', 'Прогноз цикла'), ('cancel', 'Отменить ввод')]
    owner = common + [('record', 'Добавить запись 🎀'), ('share', 'Обмен с любимым'),
                      ('delete', 'Удалить записи дня'), ('erase', 'Удалить весь дневник')]
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in owner])
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


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
        await configure_commands(bot)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        store.db.close()
        await bot.session.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
