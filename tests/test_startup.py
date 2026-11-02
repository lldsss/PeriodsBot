import asyncio
import unittest
from unittest.mock import AsyncMock,patch
from aiogram.exceptions import TelegramNetworkError,TelegramServerError,TelegramRetryAfter,TelegramUnauthorizedError
from aiogram.methods import GetMe, SetMyCommands
import bot


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_dns_and_gateway_recover_then_return_result(self):
        method=GetMe()
        operation=AsyncMock(side_effect=[TelegramNetworkError(method=method,message='DNS'),TelegramServerError(method=method,message='Bad Gateway'),'connected'])
        with patch('bot.asyncio.sleep',new_callable=AsyncMock) as sleep:
            self.assertEqual(await bot.retry_telegram(operation,'test'),'connected')
        self.assertEqual([c.args[0] for c in sleep.call_args_list],[1,2])

    async def test_flood_control_waits_as_requested_and_backoff_is_bounded(self):
        method=GetMe()
        operation=AsyncMock(side_effect=[TelegramRetryAfter(method=method,message='flood',retry_after=17)]+[TelegramNetworkError(method=method,message='DNS') for _ in range(8)]+['ok'])
        with patch('bot.asyncio.sleep',new_callable=AsyncMock) as sleep:
            await bot.retry_telegram(operation,'test')
        self.assertEqual(sleep.call_args_list[0].args[0],17)
        self.assertEqual(max(c.args[0] for c in sleep.call_args_list),30)

    async def test_bad_token_is_not_retried_and_cancellation_propagates(self):
        operation=AsyncMock(side_effect=TelegramUnauthorizedError(method=GetMe(),message='bad token'))
        with patch('bot.asyncio.sleep',new_callable=AsyncMock) as sleep:
            with self.assertRaises(TelegramUnauthorizedError):
                await bot.retry_telegram(operation,'test')
            sleep.assert_not_awaited()
        with self.assertRaises(asyncio.CancelledError):
            await bot.retry_telegram(AsyncMock(side_effect=asyncio.CancelledError()),'test')

    async def test_menu_retry_does_not_repeat_successful_command_registration(self):
        api=AsyncMock()
        api.set_my_commands.side_effect=[TelegramNetworkError(method=SetMyCommands(commands=[]),message='DNS'),True]
        api.set_chat_menu_button.side_effect=[TelegramServerError(method=GetMe(),message='gateway'),True]
        with patch('bot.asyncio.sleep',new_callable=AsyncMock):
            await bot.configure_commands(api)
        self.assertEqual(api.set_my_commands.await_count,2)
        self.assertEqual(api.set_chat_menu_button.await_count,2)
