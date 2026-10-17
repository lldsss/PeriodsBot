import asyncio
from datetime import datetime, timedelta
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from storage import MASHA


def reminder_keyboard(kind="reminder"):
    if kind=="pill":
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Через час 🌙",callback_data="pill:snooze")],
            [InlineKeyboardButton(text="Настройки таблеток 💊",callback_data="menu:pills")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🐷 Записать, как я себя чувствую', callback_data='menu:record')],
        [InlineKeyboardButton(text='Через час 🌙', callback_data='remind:snooze')],
        [InlineKeyboardButton(text='Настройки напоминаний', callback_data='menu:reminders')],
    ])


async def tick(bot, store, now, recipient=MASHA, kind="reminder"):
    if kind not in ("reminder", "pill"):
        raise ValueError("Unknown reminder kind")
    if store.get_setting(f'{kind}_enabled','no') != 'yes':
        return False
    snooze = store.get_setting(f'{kind}_snooze','')
    due_snooze = bool(snooze and datetime.fromisoformat(snooze) <= now)
    daily = (not snooze and now.strftime('%H:%M') >= store.get_setting(f'{kind}_time','21:00')
             and store.get_setting(f'{kind}_last','') != now.date().isoformat())
    if not (due_snooze or daily):
        return False
    # Bounded retries on API failure, without logging private message content.
    retry = store.get_setting(f'{kind}_retry','')
    if retry and datetime.fromisoformat(retry) > now:
        return False
    try:
        text='Маша, как ты сегодня? 🐷🌷\nЗаглянем в дневничок?' if recipient==MASHA else 'Тестовое напоминание 🐷🌷\nЗаглянем в твой дневничок?'
        if kind=='pill':
            text='Пора выпить таблетки по твоему расписанию 💊🐷'
        await bot.send_message(recipient, text, reply_markup=reminder_keyboard(kind))
    except TelegramAPIError:
        store.set_setting(f'{kind}_retry',(now+timedelta(minutes=5)).isoformat())
        return False
    store.set_setting(f'{kind}_last',now.date().isoformat())
    store.set_setting(f'{kind}_snooze','')
    store.set_setting(f'{kind}_retry','')
    return True


async def run(bot, stores, timezone):
    while True:
        for recipient, diary in stores.items():
            await tick(bot,diary,datetime.now(timezone),recipient)
            await tick(bot,diary,datetime.now(timezone),recipient,kind="pill")
        await asyncio.sleep(30)
