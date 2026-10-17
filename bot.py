import asyncio
import calendar
import contextlib
import logging
import os
import re
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import (BotCommand, BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, MenuButtonCommands, Message)

from storage import MASHA, PARTNER, TEST_USER, WRITERS, DiaryStores, current_user
from visuals import MONTHS, calendar_image, pain_chart
import reminders

LABELS = ['Месячные', 'Симптомы', 'Настроение', 'Самочувствие', 'Близость', 'Секс', 'Заметка', 'Боль', 'Выделения']
OPTIONS = {
    'Симптомы': ['Нет симптомов', 'Спазмы', 'Головная боль', 'Вздутие', 'Боль в груди', 'Усталость',
                'Боль в пояснице', 'Боль внизу живота', 'Тошнота', 'Головокружение', 'Сонливость',
                'Бессонница', 'Отёчность', 'Акне', 'Изменение аппетита', 'Тяга к сладкому', 'Запор', 'Диарея', 'Приливы', 'Озноб'],
    'Настроение': ['Хорошее', 'Спокойное', 'Грустное', 'Тревожное', 'Раздражённое', 'Радостное', 'Нежное',
                  'Влюблённое', 'Энергичное', 'Вдохновлённое', 'Чувствительное', 'Плаксивое', 'Апатичное',
                  'Одинокое', 'Перепады настроения', 'Злое', 'Уверенное'],
    'Самочувствие': ['Отличное', 'Нормальное', 'Плохое'],
    'Секс': ['Да', 'Нет'], 'Близость': ['Да', 'Нет'],
    'Боль': [str(n) for n in range(1, 11)] + ['Нет боли'],
    'Выделения': ['Мажущие', 'Слабые', 'Умеренные', 'Обильные'],
    'Месячные': ['Да', 'Нет'],  # editing imported daily marks only
}
COMMANDS = [('start','Главное меню 🐷'), ('record','Добавить запись'), ('period','Начало и конец месячных'),
            ('day','Посмотреть записи дня'), ('calendar','Календарик 🌸'), ('stats','Статистика и график боли'),
            ('forecast','Прогноз цикла'), ('edit','Исправить запись'), ('undo','Отменить последнее действие'),
            ('reminders','Настроить напоминания'), ('pills','Напоминания о таблетках 💊'), ('share','Информация об обмене'),
            ('delete','Удалить записи дня'), ('erase','Очистить дневник'), ('cancel','Отменить ввод')]
SHARING_TEXT = 'Обмен всегда включён 💕 Все записи, изменения и оценки доступны партнёру и отправляются ему. Уже доставленные сообщения останутся в его чате после удаления или отмены записей.'
dp = Dispatcher(events_isolation=SimpleEventIsolation())
store = None
tz = ZoneInfo('Europe/Moscow')


class Access(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = event.from_user
        chat = event.chat if isinstance(event, Message) else getattr(event.message, 'chat', None)
        if not user or user.id not in (MASHA, PARTNER, TEST_USER) or not chat or chat.type != 'private':
            if isinstance(event, CallbackQuery):
                await event.answer('Доступ закрыт', show_alert=True)
            elif chat and chat.type == 'private':
                await event.answer('Этот бот доступен только разрешённым аккаунтам.')
            return
        token=current_user.set(user.id)
        try:
            return await handler(event, data)
        finally:
            current_user.reset(token)


dp.message.outer_middleware(Access())
dp.callback_query.outer_middleware(Access())


class Entry(StatesGroup):
    value = State()
    clock = State()
    reminder_time = State()


def now():
    return datetime.now(tz)


def today():
    return now().date().isoformat()


def pretty(day):
    return date.fromisoformat(day).strftime('%d.%m.%Y')


def parse_day(value):
    parsed = date.fromisoformat(value)
    if parsed > date.fromisoformat(today()):
        raise ValueError('Выбери сегодняшний или прошедший день.')
    return parsed.isoformat()


def valid_time(value):
    return bool(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value))


def keyboard(items, columns=1):
    buttons = [InlineKeyboardButton(text=label, callback_data=key) for label,key in items]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i:i+columns] for i in range(0,len(buttons),columns)])


def menu(user):
    items = [('📅 Календарик','menu:calendar'), ('💌 Записи','menu:day'), ('📊 Статистика','menu:stats'), ('🌙 Прогноз','menu:forecast')]
    if user in WRITERS:
        items = [('🐷 Добавить запись','menu:record'), ('🌸 Месячные','menu:period')] + items + [
            ('✏️ Исправить запись','menu:edit'), ('↩️ Отменить последнее','menu:undo'),
            ('⏰ Напоминания','menu:reminders'), ('💊 Таблетки','menu:pills'), ('💕 Обмен','menu:share')]
    return keyboard(items,2)


def is_write_action(action):
    return action not in ('day','calendar','stats','forecast','share','start','cancel')


def allowed(user, action):
    return user in WRITERS or not is_write_action(action)


def day_shortcuts(action):
    current = date.fromisoformat(today())
    return keyboard([('Сегодня 🌷',f'd:{action}:{current}'), ('Вчера 🌙',f'd:{action}:{current-timedelta(days=1)}'),
                     ('Другой день 📅',f'm:{action}:{current:%Y-%m}'), ('🐷 Главное меню','menu:start')])


def date_picker(month, action):
    first = date.fromisoformat(month+'-01')
    current = date.fromisoformat(today())
    rows = [[InlineKeyboardButton(text=f'{MONTHS[first.month-1]} {first.year}',callback_data='noop')],
            [InlineKeyboardButton(text=t,callback_data='noop') for t in ('Пн','Вт','Ср','Чт','Пт','Сб','Вс')]]
    for week in calendar.monthcalendar(first.year,first.month):
        rows.append([InlineKeyboardButton(text=str(n) if n and first.replace(day=n)<=current else '·',
            callback_data=f'd:{action}:{first.replace(day=n)}' if n and first.replace(day=n)<=current else 'noop') for n in week])
    offset=first.year*12+first.month-1
    nav=[]
    for delta,label in ((-1,'‹'),(1,'›')):
        year,idx=divmod(offset+delta,12)
        if 1<=year<=9999 and (year,idx+1)<=(current.year,current.month):
            nav.append(InlineKeyboardButton(text=label,callback_data=f'm:{action}:{year:04d}-{idx+1:02d}'))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text='🐷 Главное меню',callback_data='menu:start')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sharing_text(user):
    if user==TEST_USER:
        return 'Тестовый режим 🧪 Все функции доступны в отдельном дневнике. Партнёр не получает уведомлений и не видит эти записи. Напоминания приходят только тебе.'
    return SHARING_TEXT


async def notify(api, text):
    if current_user.get()==TEST_USER:
        return None
    try:
        await api.send_message(PARTNER,text)
        return True
    except TelegramAPIError:
        return False


async def changed(message, text):
    delivered = await notify(message.bot, text)
    if delivered is False:
        await message.answer('Запись сохранена, но уведомление не доставлено. Партнёру нужно открыть бота и нажать /start; также проверьте соединение.')


async def categories(message,day):
    await message.answer(f'🐽 {pretty(day)} · что отметим?',reply_markup=keyboard(
        [(label,f'kind:{day}:{i}') for i,label in enumerate(LABELS)] + [('Другой день','menu:record')],2))


def form_keyboard(data):
    nonce=data['nonce']
    options=OPTIONS.get(data['kind'],[])
    selected=data.get('selected',[])
    if data['kind']=='Симптомы':
        items=[(('✅ ' if v in selected else '▫️ ')+v,f'sym:{nonce}:{i}') for i,v in enumerate(options)]
        items += [('💾 Сохранить выбранное',f'done:{nonce}')]
    else:
        items=[(v,f'val:{nonce}:{i}') for i,v in enumerate(options)]
    if data.get('edit_id'):
        items.append(('Сохранить прежний текст',f'keep:{nonce}'))
    items += [(f'🕒 Время: {data["time"] or "не указано"}',f'clock:{nonce}'), ('Назад к категориям',f'd:record:{data["day"]}')]
    return keyboard(items,2)


async def show_form(message,state,day,kind,edit=None):
    await state.clear()
    selected=[]
    if edit and kind=='Симптомы':
        selected=[v.strip() for v in edit['value'].split(',') if v.strip()]
    data={'day':day,'kind':kind,'time':edit['time'] if edit else now().strftime('%H:%M') if day==today() else '',
          'nonce':secrets.token_hex(4),'edit_id':edit['id'] if edit else None,'original':edit['value'] if edit else None,'selected':selected}
    await state.set_state(Entry.value)
    await state.update_data(**data)
    if kind=='Симптомы':
        prompt='Выбери несколько симптомов и нажми «Сохранить выбранное». Свои симптомы можно написать через запятую — они добавятся к выбору.'
    elif kind=='Боль':
        prompt='Оцени боль от 1 до 10: 1 — едва заметная, 10 — самая сильная. Или выбери «Нет боли».'
    elif kind=='Месячные':
        prompt='Это старая дневная отметка. Выбери «Да» или «Нет».'
    else:
        prompt='Выбери вариант или напиши своими словами (до 1500 символов) 💕'
        if kind in ('Секс','Близость'):
            prompt+=' Текст отмечается в календаре как событие; если события не было, выбери «Нет».'
    current=f'\nСейчас: {edit["value"]}' if edit else ''
    await message.answer(f'{kind} · {pretty(day)}{current}\n{prompt}',reply_markup=form_keyboard(data))


async def save(message,state,value):
    data=await state.get_data()
    if not data.get('kind'):
        await message.answer('Открой запись заново.',reply_markup=menu(current_user.get()))
        return
    if not 1<=len(value)<=1500 or (data['kind'] in ('Боль','Месячные') and value not in OPTIONS[data['kind']]):
        await message.answer('Выбери допустимый вариант или введи текст до 1500 символов.',reply_markup=form_keyboard(data))
        return
    if data.get('edit_id'):
        store.edit(data['edit_id'],value,data['time'])
        verb='исправила'
    else:
        store.add(data['day'],data['kind'],value,data['time'])
        verb='добавила'
    await state.clear()
    await changed(message,f'Маша {verb} запись · {pretty(data["day"])} {data["time"]}\n{data["kind"]}: {value}')
    items=[('Ещё за этот день 🌸',f'd:record:{data["day"]}'), ('↩️ Отменить последнее','menu:undo'), ('🐷 Главное меню','menu:start')]
    if data['kind']=='Симптомы' and value!='Нет симптомов':
        items.insert(0,('Оценить боль от 1 до 10',f'kind:{data["day"]}:{LABELS.index("Боль")}'))
    await message.answer('Хрю! Сохранено 🐷💕',reply_markup=keyboard(items))


def telegram_length(text):
    return len(text.encode('utf-16-le')) // 2


async def show_day(message,day,user,action='day',offset=0,edit=False):
    entries=store.events(day)
    periods=[p for p in store.periods() if p['start']<=day<=(p['end'] or today())]
    header=f'🐷 {pretty(day)} · записей: {len(entries)}'
    for p in periods:
        header+='\n🌸 Месячные с '+pretty(p['start'])+(f' по {pretty(p["end"])}' if p['end'] else ' · ещё идут')
    # Telegram limits text to 4096 UTF-16 units. Keep complete events together;
    # an emoji can occupy two units even though Python counts one character.
    pages=[]
    blocks=[]
    size=telegram_length(header)+100
    for number,e in enumerate(entries,1):
        value=f'{e["value"]}/10' if e['kind']=='Боль' and e['value'].isdigit() else e['value']
        block=f'{number}. {e["time"] or "Время не указано"} · {e["kind"]}\n{value}'
        cost=telegram_length(block)+2
        if blocks and (size+cost>4000 or len(blocks)>=10):
            pages.append(blocks)
            blocks=[]
            size=telegram_length(header)+100
        blocks.append((number,e,block))
        size+=cost
    if blocks or not pages:
        pages.append(blocks)
    page=max(0,min(offset,len(pages)-1))
    text=header+'\n\n'+'\n\n'.join(block for _,_,block in pages[page])
    if not entries:
        text+='Записей за этот день пока нет 🐷🌷'
    if len(pages)>1:
        text+=f'\n\nСтраница {page+1} из {len(pages)}'
    rows=[]
    if user in WRITERS:
        for number,e,_ in pages[page]:
            rows.append([InlineKeyboardButton(text=f'✏️ Изменить №{number}',callback_data=f'event:{e["id"]}'),
                         InlineKeyboardButton(text=f'🗑 Удалить №{number}',callback_data=f'ask:event:{e["id"]}')])
        for p in periods:
            rows.append([InlineKeyboardButton(text='🌸 Исправить период',callback_data=f'period:{p["id"]}')])
    nav=[]
    if page:
        nav.append(InlineKeyboardButton(text='‹ Назад',callback_data=f'page:{action}:{day}:{page-1}'))
    if page+1<len(pages):
        nav.append(InlineKeyboardButton(text='Дальше ›',callback_data=f'page:{action}:{day}:{page+1}'))
    if nav:
        rows.append(nav)
    if action=='delete' and user in WRITERS and entries:
        rows.append([InlineKeyboardButton(text='Удалить все записи дня',callback_data=f'ask:day:{day}')])
    if user in WRITERS:
        rows.append([InlineKeyboardButton(text='🐷 Добавить запись',callback_data=f'd:record:{day}')])
    rows.append([InlineKeyboardButton(text='📅 Другой день',callback_data=f'menu:{action}')])
    markup=InlineKeyboardMarkup(inline_keyboard=rows)
    if edit:
        try:
            await message.edit_text(text,reply_markup=markup)
        except TelegramBadRequest as exc:
            if 'message is not modified' not in str(exc).lower():
                raise
    else:
        await message.answer(text,reply_markup=markup)


async def period_panel(message,period_id=None):
    period=store.period(period_id) if period_id else store.active_period()
    if period:
        text=f'🌸 Начало: {pretty(period["start"])}\nКонец: '+(pretty(period['end']) if period['end'] else 'ещё идут')
        items=[('Исправить начало',f'choose:ps{period["id"]}'),
               ('Исправить конец' if period['end'] else 'Закончились',f'choose:pe{period["id"]}'),
               ('Удалить этот период',f'ask:period:{period["id"]}')]
    else:
        text='🌸 Отметим начало месячных? Дни между началом и концом заполнятся автоматически. Конец — последний день кровотечения.'
        items=[('Начались 🌸','choose:pstart')]
    items += [('Добавить прошлый период','choose:paststart'),('История периодов','periods'),('🐷 Главное меню','menu:start')]
    await message.answer(text,reply_markup=keyboard(items))


async def select_date(message,state,action,day,user):
    if not allowed(user,action):
        raise ValueError('Изменять дневник может только Маша.')
    day=parse_day(day)
    pending=await state.get_data()
    await state.clear()
    if action=='paststart':
        await state.update_data(past_start=day)
        await message.answer('Теперь выбери последний день месячных 🌸',reply_markup=day_shortcuts('pastend'))
    elif action=='pastend':
        if not pending.get('past_start'):
            raise ValueError('Начни добавление прошлого периода заново.')
        try:
            pid=store.start_period(pending['past_start'],day)
        except ValueError:
            await state.update_data(past_start=pending['past_start'])
            raise
        await changed(message,f'🌸 Маша добавила месячные: {pretty(pending["past_start"])} — {pretty(day)}')
        await period_panel(message,pid)
    elif action=='record':
        await categories(message,day)
    elif action in ('day','edit','delete'):
        await show_day(message,day,user,action)
    elif action=='pstart':
        store.start_period(day)
        await changed(message,f'🌸 Маша отметила начало месячных: {pretty(day)}')
        await period_panel(message)
    elif re.fullmatch(r'p[se]\d+',action):
        period_id=int(action[2:])
        store.update_period(period_id, **({'start':day} if action[1]=='s' else {'end':day}))
        await changed(message,f'🌸 Маша обновила {"начало" if action[1]=="s" else "конец"} месячных: {pretty(day)}')
        await period_panel(message,period_id)
    else:
        raise ValueError('Открой главное меню заново.')


async def show_calendar(message,month):
    first=date.fromisoformat(month+'-01')
    image=await asyncio.to_thread(calendar_image_data,month,today(),store.marks(month,today()),store.cycle_data(today())['prediction'])
    offset=first.year*12+first.month-1
    items=[]
    for delta,label in ((-1,'‹ Предыдущий'),(1,'Следующий ›')):
        year,idx=divmod(offset+delta,12)
        if 1<=year<=9999:
            items.append((label,f'cal:{year:04d}-{idx+1:02d}'))
    items.append(('🐷 Главное меню','menu:start'))
    await message.answer_photo(BufferedInputFile(image,filename=f'calendar-{month}.png'),caption='🐷🌸 Машин календарик. Лиловым отмечено приблизительное окно начала.',reply_markup=keyboard(items,2))


def calendar_image_data(month,day,marks,prediction):
    # Rendering happens in a thread; SQLite stays in the event-loop thread.
    class Data:
        def marks(self,*args): return marks
        def cycle_data(self,*args): return {'prediction':prediction}
    return calendar_image(Data(),month,day)


async def show_stats(message):
    stats=store.stats(today())
    cycles=stats['cycles']
    from statistics import mean
    text=f'📊 Последние 90 дней · записей: {stats["events"]}\n'
    if cycles['intervals']:
        text+='Последние интервалы между циклами: '+', '.join(map(str,cycles['intervals']))+' дн.\n'
    if cycles['durations']:
        text+=f'Средняя длительность завершённых месячных: {mean(cycles["durations"]):.1f} дн.\n'
    for title,key in [('Частые симптомы','symptoms'),('Настроение','moods')]:
        text+='\n'+title+':\n'+('\n'.join(f'• {v[:100]} — {n} записей' for v,n in stats[key]) or 'Пока нет записей')+'\n'
    if stats['mood_history']:
        text+='\nПоследние отметки настроения:\n'+'\n'.join(f'{pretty(day)} {time} — {value[:100]}' for day,time,value in stats['mood_history'])+'\n'
    text+='\n'+store.forecast(today())
    await message.answer(text)
    week=store.stats(today(),days=7)
    image=await asyncio.to_thread(pain_chart,week)
    await message.answer_photo(BufferedInputFile(image,filename='pain.png'),caption=f'Боль за последние 7 дней: {pretty(week["start"])} — {pretty(week["end"])}. Средняя оценка за день; пропуски не заменяются нулями.',reply_markup=menu(message.chat.id))


async def reminder_panel(message,kind='reminder'):
    prefix='pill' if kind=='pill' else 'remind'
    enabled=store.get_setting(f'{kind}_enabled','no')=='yes'
    title='💊 Таблетки' if kind=='pill' else '⏰ Дневничок'
    extra='\nВыбери своё время и нажми «Включить». Напоминание ежедневное.' if kind=='pill' else ''
    items=[('Выключить' if enabled else 'Включить',f'{prefix}:off' if enabled else f'{prefix}:on'),
           ('09:00',f'{prefix}:09:00'),('18:00',f'{prefix}:18:00'),('21:00',f'{prefix}:21:00'),
           ('Своё время',f'{prefix}:custom')]
    if kind=='reminder':
        items.append(('💊 Напоминание о таблетках','menu:pills'))
    items.append(('🐷 Главное меню','menu:start'))
    await message.answer(f'{title}: напоминания {"включены" if enabled else "выключены"}.\nВремя: {store.get_setting(f"{kind}_time","21:00")} ({tz.key}). Только для твоего аккаунта.'+extra,reply_markup=keyboard(items,2))


async def perform_command(message,state,command,user,arg=None):
    await state.clear()
    if not allowed(user,command):
        await message.answer('Изменять дневник может только Маша.')
        return
    if command=='start':
        await message.answer(('🐷 Тестовый хрю-дневничок\n' if user==TEST_USER else '🐷🌷 Машин хрю-дневничок\n')+sharing_text(user),reply_markup=menu(user))
    elif command in ('record','day','edit','delete'):
        if arg:
            await select_date(message,state,command,arg,user)
        else:
            await message.answer('🐷 Выбери день',reply_markup=day_shortcuts(command))
    elif command=='period':
        await period_panel(message)
    elif command=='calendar':
        await show_calendar(message,arg or today()[:7])
    elif command=='stats':
        await show_stats(message)
    elif command=='forecast':
        await message.answer(store.forecast(today()),reply_markup=menu(user))
    elif command=='share':
        await message.answer(sharing_text(user))
    elif command in ('reminders','pills'):
        await reminder_panel(message,'pill' if command=='pills' else 'reminder')
    elif command=='undo':
        last=store.last_action()
        if last:
            await message.answer(f'Отменить: {last["label"]}?',reply_markup=keyboard([('Да, отменить',f'undo:{last["id"]}'),('Назад','menu:start')]))
        else:
            await message.answer('Пока нечего отменять 🐷')
    elif command=='erase':
        await ask_delete(message,'all','all')
    elif command=='cancel':
        await message.answer('Ввод отменён 🐷',reply_markup=menu(user))
    else:
        await message.answer('Открой меню 🐷',reply_markup=menu(user))


async def ask_delete(message,kind,target):
    label='весь дневник' if kind=='all' else 'записи дня (периоды месячных останутся)' if kind=='day' else 'период месячных' if kind=='period' else 'эту запись'
    await message.answer(f'Удалить {label}? Можно отменить последним действием. Сообщения в чате партнёра сохранятся.',reply_markup=keyboard([('Да, удалить',f'del:{kind}:{target}'),('Отмена','menu:start')]))


@dp.message(Command(*[c for c,_ in COMMANDS]))
async def commands(message:Message,state:FSMContext):
    parts=message.text.split(maxsplit=1)
    try:
        await perform_command(message,state,parts[0].split('@')[0][1:],message.from_user.id,parts[1] if len(parts)>1 else None)
    except ValueError as exc:
        await message.answer(str(exc),reply_markup=menu(message.from_user.id))


@dp.callback_query()
async def callbacks(query:CallbackQuery,state:FSMContext):
    await query.answer()
    parts=(query.data or '').split(':')
    head=parts[0]
    user=query.from_user.id
    message=query.message
    read_only=head in ('noop','cal','menu') or (head in ('d','m','page') and len(parts)>1 and parts[1]=='day')
    if user not in WRITERS and not read_only:
        await message.answer('Изменять дневник может только Маша.')
        return
    try:
        if head=='noop':
            return
        if head=='menu':
            await perform_command(message,state,parts[1],user)
        elif head in ('d','m','choose'):
            action=parts[1]
            if not allowed(user,action):
                raise ValueError('Изменять дневник может только Маша.')
            if head!='d' and action!='pastend':
                await state.clear()
            if head=='d':
                await select_date(message,state,action,parts[2],user)
            elif head=='m':
                await message.edit_text('🐷 Выбери день',reply_markup=date_picker(parts[2],action))
            else:
                await message.answer('🐷 Выбери день',reply_markup=day_shortcuts(action))
        elif head=='kind':
            day=parse_day(parts[1])
            kind=LABELS[int(parts[2])]
            if kind=='Месячные':
                await state.clear()
                await period_panel(message)
            else:
                await show_form(message,state,day,kind)
        elif head in ('val','sym','done','clock','at','keep'):
            data=await state.get_data()
            if data.get('nonce')!=parts[1] or await state.get_state() not in (Entry.value.state,Entry.clock.state):
                raise ValueError('Эти кнопки устарели. Открой запись заново.')
            if head=='clock':
                await state.set_state(Entry.clock)
                await message.answer('Время события: выбери или введи ЧЧ:ММ.',reply_markup=keyboard([
                    ('Утро · 09:00',f'at:{parts[1]}:09:00'),('День · 14:00',f'at:{parts[1]}:14:00'),
                    ('Вечер · 21:00',f'at:{parts[1]}:21:00'),('Не указывать',f'at:{parts[1]}:none')],2))
            elif head=='at':
                await set_event_time(message,state,':'.join(parts[2:]))
            elif head=='sym':
                item=OPTIONS['Симптомы'][int(parts[2])]
                selected=data.get('selected',[])
                if item in selected:
                    selected.remove(item)
                elif item=='Нет симптомов':
                    selected=[item]
                else:
                    selected=[s for s in selected if s!='Нет симптомов']+[item]
                await state.update_data(selected=selected)
                data['selected']=selected
                await message.edit_reply_markup(reply_markup=form_keyboard(data))
            elif head=='keep':
                if not data.get('edit_id'):
                    raise ValueError('Открой запись заново.')
                await save(message,state,data['original'])
            elif head=='done':
                selected=data.get('selected',[])
                if not selected:
                    raise ValueError('Выбери хотя бы один симптом или «Нет симптомов».')
                await save(message,state,', '.join(selected))
            else:
                await save(message,state,OPTIONS[data['kind']][int(parts[2])])
        elif head=='page':
            await state.clear()
            await show_day(message,parse_day(parts[2]),user,parts[1],max(0,int(parts[3])),edit=True)
        elif head=='event':
            event=store.event(int(parts[1]))
            if not event:
                raise ValueError('Запись уже удалена.')
            await show_form(message,state,event['day'],event['kind'],event)
        elif head=='period':
            await state.clear()
            await period_panel(message,int(parts[1]))
        elif head=='periods':
            offset=int(parts[1]) if len(parts)>1 else 0
            periods=list(reversed(store.periods()))
            items=[(f'{pretty(p["start"])} — {pretty(p["end"]) if p["end"] else "идут"}',f'period:{p["id"]}') for p in periods[offset:offset+10]]
            if offset+10<len(periods):
                items.append(('Дальше',f'periods:{offset+10}'))
            items += [('Начались 🌸','choose:pstart'),('🐷 Главное меню','menu:start')]
            await message.answer('🌸 История месячных',reply_markup=keyboard(items))
        elif head=='ask':
            await state.clear()
            await ask_delete(message,parts[1],parts[2])
        elif head=='del':
            await state.clear()
            kind,target=parts[1:]
            if kind=='all': store.clear()
            elif kind=='day': store.delete_day(parse_day(target))
            elif kind=='event': store.delete_event(int(target))
            elif kind=='period': store.delete_period(int(target))
            else: raise ValueError('Неизвестное действие.')
            await message.edit_text('Удалено из дневника 🐷')
            await changed(message,'Маша удалила '+('весь дневник.' if kind=='all' else 'записи за '+pretty(target) if kind=='day' else 'запись из дневника.'))
            await message.answer('Можно отменить последнее действие.',reply_markup=keyboard([('↩️ Отменить','menu:undo'),('Главное меню','menu:start')]))
        elif head=='undo':
            await state.clear()
            label=store.undo(int(parts[1]))
            await changed(message,f'Маша отменила действие: {label}. Актуальные записи доступны в дневнике.')
            await message.edit_text('Действие отменено 🐷',reply_markup=menu(user))
        elif head=='cal':
            await state.clear()
            await show_calendar(message,parts[1])
        elif head in ('remind','pill'):
            await reminder_action(message,state,parts[1:],'pill' if head=='pill' else 'reminder')
        else:
            await state.clear()
            await message.answer('Меню обновилось 🐷 Открой нужный раздел заново.',reply_markup=menu(user))
    except (ValueError,IndexError,KeyError) as exc:
        text=str(exc) if isinstance(exc,ValueError) else 'Открой нужный раздел заново.'
        await message.answer(text,reply_markup=menu(user))


async def set_event_time(message,state,value):
    if value!='none' and not valid_time(value):
        raise ValueError('Введи время в формате ЧЧ:ММ, например 09:30.')
    await state.update_data(time='' if value=='none' else value)
    await state.set_state(Entry.value)
    data=await state.get_data()
    await message.answer('Время выбрано. Теперь укажи значение записи 🐷',reply_markup=form_keyboard(data))


async def reminder_action(message,state,parts,kind='reminder'):
    await state.clear()
    action=parts[0]
    if action=='custom':
        await state.set_state(Entry.reminder_time)
        await state.update_data(reminder_kind=kind)
        await message.answer(f'Во сколько напоминать? Введи ЧЧ:ММ, например 20:30. Часовой пояс: {tz.key}.')
        return
    if action in ('on','off'):
        store.set_setting(f'{kind}_enabled','yes' if action=='on' else 'no')
        store.set_setting(f'{kind}_snooze','')
    elif action=='snooze':
        if store.get_setting(f'{kind}_enabled','no')!='yes':
            raise ValueError('Напоминания выключены.')
        store.set_setting(f'{kind}_snooze',(now()+timedelta(hours=1)).isoformat())
        store.set_setting(f'{kind}_last',today())
        await message.answer('Хрю, напомню через час 🐷🌙')
        return
    else:
        value=':'.join(parts)
        if not valid_time(value):
            raise ValueError('Некорректное время.')
        store.set_setting(f'{kind}_time',value)
        store.set_setting(f'{kind}_snooze','')
    await reminder_panel(message,kind)


@dp.message(Entry.clock)
async def clock_text(message:Message,state:FSMContext):
    if message.from_user.id not in WRITERS: return
    try:
        await set_event_time(message,state,(message.text or '').strip())
    except ValueError as exc:
        await message.answer(str(exc))


@dp.message(Entry.reminder_time)
async def reminder_text(message:Message,state:FSMContext):
    if message.from_user.id not in WRITERS: return
    value=(message.text or '').strip()
    if not valid_time(value):
        await message.answer('Введи время ЧЧ:ММ, например 20:30, или /cancel.')
        return
    kind=(await state.get_data()).get('reminder_kind','reminder')
    store.set_setting(f'{kind}_time',value)
    store.set_setting(f'{kind}_snooze','')
    await state.clear()
    await reminder_panel(message,kind)


@dp.message(Entry.value)
async def text_value(message:Message,state:FSMContext):
    if message.from_user.id not in WRITERS: return
    value=(message.text or '').strip()
    if not value or value.startswith('/') or len(value)>1500:
        await message.answer('Пришли текст от 1 до 1500 символов или /cancel.')
        return
    data=await state.get_data()
    if data.get('kind')=='Симптомы':
        selected=data.get('selected',[])
        added=[v.strip() for v in value.split(',') if v.strip()]
        combined=list(dict.fromkeys(selected+added))
        if len(combined)>1: combined=[v for v in combined if v!='Нет симптомов']
        if len(', '.join(combined))>1500:
            await message.answer('Список слишком длинный: максимум 1500 символов.')
            return
        await state.update_data(selected=combined)
        data['selected']=combined
        await message.answer('Выбрано: '+', '.join(combined)+'\nНажми «Сохранить выбранное» 🐷',reply_markup=form_keyboard(data))
    else:
        try:
            await save(message,state,value)
        except ValueError as exc:
            await state.clear()
            await message.answer(str(exc),reply_markup=menu(current_user.get()))


async def configure_commands(api):
    await api.set_my_commands([BotCommand(command=c,description=d) for c,d in COMMANDS])
    await api.set_chat_menu_button(menu_button=MenuButtonCommands())


async def main():
    global store,tz
    token=os.environ.get('BOT_TOKEN')
    if not token:
        raise SystemExit('Задайте BOT_TOKEN в защищённых переменных среды.')
    tz=ZoneInfo(os.environ.get('BOT_TIMEZONE','Europe/Moscow'))
    os.umask(0o077)
    path=Path(os.environ.get('BOT_DB','data/diary.sqlite3'))
    path.parent.mkdir(parents=True,exist_ok=True)
    store=DiaryStores(path,os.environ.get('BOT_TEST_DB'))
    api=Bot(token)
    task=None
    try:
        await configure_commands(api)
        task=asyncio.create_task(reminders.run(api,{owner:store.for_user(owner) for owner in WRITERS},tz))
        await dp.start_polling(api,allowed_updates=dp.resolve_used_update_types())
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError): await task
        store.close()
        await api.session.close()


if __name__=='__main__':
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
