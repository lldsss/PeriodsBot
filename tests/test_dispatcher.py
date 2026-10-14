"""Exercise actual aiogram routing with a local Telegram transport."""
import unittest
from datetime import datetime
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import User, Chat, Message, Update, CallbackQuery
import bot
from storage import Store, MASHA, PARTNER


class LocalTelegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls=[]
    async def close(self): pass
    async def make_request(self, api, method, timeout=None):
        self.calls.append(method)
        if method.__api_method__=='getMe':
            return User(id=123456,is_bot=True,first_name='Test',username='diary_test_bot')
        if method.__api_method__ in ('sendMessage','sendPhoto','editMessageText','editMessageReplyMarkup'):
            return Message(message_id=len(self.calls),date=datetime.now(),chat=Chat(id=int(method.chat_id),type='private'),text=getattr(method,'text',None),reply_markup=getattr(method,'reply_markup',None))
        return True
    async def stream_content(self,*args,**kwargs):
        yield b''


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_record_text_and_partner_day(self):
        bot.store=Store(':memory:')
        transport=LocalTelegram()
        api=Bot('123456:TEST_TOKEN_NOT_A_REAL_CREDENTIAL',session=transport)
        counter=0
        async def send(text,uid=MASHA):
            nonlocal counter
            counter+=1
            m=Message(message_id=counter,date=datetime.now(),chat=Chat(id=uid,type='private'),from_user=User(id=uid,is_bot=False,first_name='Test'),text=text)
            await bot.dp.feed_update(api,Update(update_id=counter,message=m))
        async def click(data):
            nonlocal counter
            counter+=1
            m=Message(message_id=counter,date=datetime.now(),chat=Chat(id=MASHA,type='private'),from_user=User(id=123456,is_bot=True,first_name='Bot'))
            q=CallbackQuery(id=str(counter),from_user=User(id=MASHA,is_bot=False,first_name='Test'),chat_instance='local',data=data,message=m)
            await bot.dp.feed_update(api,Update(update_id=counter,callback_query=q))
        try:
            await send('/start')
            self.assertTrue(any('всегда включён' in (getattr(m,'text','') or '') for m in transport.calls))
            await send('/record')
            await click('d:record:2026-01-01')
            await click('kind:2026-01-01:5')
            await send('Хорошо провели время')
            self.assertEqual(bot.store.events('2026-01-01')[0]['value'],'Хорошо провели время')
            self.assertTrue(any(getattr(m,'chat_id',None)==PARTNER and 'Хорошо провели время' in (getattr(m,'text','') or '') for m in transport.calls))
            await send('/day 2026-01-01',PARTNER)
            await send('/stats',PARTNER)
            self.assertTrue(any(m.__api_method__=='sendPhoto' for m in transport.calls))
            await send('/calendar')
        finally:
            bot.store.db.close()
            await api.session.close()
