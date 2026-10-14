import asyncio
from datetime import datetime, timedelta
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from storage import MASHA


def reminder_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🐷 Записать, как я себя чувствую', callback_data='menu:record')],
        [InlineKeyboardButton(text='Через час 🌙', callback_data='remind:snooze')],
        [InlineKeyboardButton(text='Настройки напоминаний', callback_data='menu:reminders')],
    ])


async def tick(bot, store, now, recipient=MASHA):
    if store.get_setting('reminder_enabled','no') != 'yes':
        return False
    snooze = store.get_setting('reminder_snooze','')
    due_snooze = bool(snooze and datetime.fromisoformat(snooze) <= now)
    daily = (not snooze and now.strftime('%H:%M') >= store.get_setting('reminder_time','21:00')
             and store.get_setting('reminder_last','') != now.date().isoformat())
    if not (due_snooze or daily):
        return False
    # Bounded retries on API failure, without logging private message content.
    retry = store.get_setting('reminder_retry','')
    if retry and datetime.fromisoformat(retry) > now:
        return False
    try:
        text='Маша, как ты сегодня? 🐷🌷\nЗаглянем в дневничок?' if recipient==MASHA else 'Тестовое напоминание 🐷🌷\nЗаглянем в твой дневничок?'
        await bot.send_message(recipient, text, reply_markup=reminder_keyboard())
    except TelegramAPIError:
        store.set_setting('reminder_retry',(now+timedelta(minutes=5)).isoformat())
        return False
    store.set_setting('reminder_last',now.date().isoformat())
    store.set_setting('reminder_snooze','')
    store.set_setting('reminder_retry','')
    return True


async def run(bot, stores, timezone):
    while True:
        for recipient, diary in stores.items():
            await tick(bot,diary,datetime.now(timezone),recipient)
        await asyncio.sleep(30)
