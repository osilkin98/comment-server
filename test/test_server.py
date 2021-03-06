import os
import random

import aiohttp
from itertools import *

import faker
from faker.providers import internet
from faker.providers import lorem
from faker.providers import misc

from src.main import get_config, CONFIG_FILE
from src.server import app
from src.server.validation import is_valid_base_comment

from test.testcase import AsyncioTestCase


config = get_config(CONFIG_FILE)
config['mode'] = 'testing'
config['testing']['file'] = ':memory:'


if 'slack_webhook' in config:
    config.pop('slack_webhook')


fake = faker.Faker()
fake.add_provider(internet)
fake.add_provider(lorem)
fake.add_provider(misc)


def fake_lbryusername() -> str:
    return '@' + fake.user_name()


def nothing():
    pass


def fake_signature() -> str:
    return fake.sha256() + fake.sha256()


def fake_signing_ts() -> str:
    return str(random.randint(1, 2**32 - 1))


async def jsonrpc_post(url, method, **params):
    json_body = {
        'jsonrpc': '2.0',
        'id': None,
        'method': method,
        'params': params
    }
    async with aiohttp.request('POST', url, json=json_body) as request:
        return await request.json()


replace = {
    'claim_id': fake.sha1,
    'comment': fake.text,
    'channel_id': fake.sha1,
    'channel_name': fake_lbryusername,
    'signature': fake_signature,
    'signing_ts': fake_signing_ts,
    'parent_id': fake.sha256,
}


def create_test_comments(values: iter, **default):
    vars_combo = chain.from_iterable(combinations(values, r) for r in range(1, len(values) + 1))
    return [{k: replace[k]() if k in comb else v for k, v in default.items()}
            for comb in vars_combo]


class ServerTest(AsyncioTestCase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config['mode'] = 'testing'
        config['testing']['file'] = ':memory:'
        self.host = 'localhost'
        self.port = 5931

    @property
    def url(self):
        return f'http://{self.host}:{self.port}/api'

    @classmethod
    def tearDownClass(cls) -> None:
        print('exit reached')

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.server = app.CommentDaemon(config)
        await self.server.start(host=self.host, port=self.port)
        self.addCleanup(self.server.stop)

    async def post_comment(self, **params):
        return await jsonrpc_post(self.url, 'create_comment', **params)

    async def test01CreateCommentNoReply(self):
        anonymous_test = create_test_comments(
            ('claim_id', 'channel_id', 'channel_name', 'comment'),
            comment=None,
            channel_name=None,
            channel_id=None,
            claim_id=None
        )
        for test in anonymous_test:
            with self.subTest(test='null fields: ' + ', '.join(k for k, v in test.items() if not v)):
                message = await self.post_comment(**test)
                self.assertTrue('result' in message or 'error' in message)
                if 'error' in message:
                    self.assertFalse(is_valid_base_comment(**test))
                else:
                    self.assertTrue(is_valid_base_comment(**test))

    async def test02CreateNamedCommentsNoReply(self):
        named_test = create_test_comments(
            ('channel_name', 'channel_id', 'signature'),
            claim_id='1234567890123456789012345678901234567890',
            channel_name='@JamieFoxx',
            channel_id='1234567890'*4,
            comment='blah blah blahbity blah',
            signature=None
        )
        for test in named_test:
            with self.subTest(test=test):
                message = await self.post_comment(**test)
                self.assertTrue('result' in message or 'error' in message)
                if 'error' in message:
                    self.assertFalse(is_valid_base_comment(**test))
                else:
                    self.assertTrue(is_valid_base_comment(**test))

    async def test03CreateAllTestComments(self):
        test_all = create_test_comments(replace.keys(), **{
            k: None for k in replace.keys()
        })
        test_all.reverse()
        for test in test_all:
            nulls = 'null fields: ' + ', '.join(k for k, v in test.items() if not v)
            with self.subTest(test=nulls):
                message = await self.post_comment(**test)
                self.assertTrue('result' in message or 'error' in message)
                if 'error' in message:
                    self.assertFalse(is_valid_base_comment(**test, strict=True))
                else:
                    self.assertTrue(is_valid_base_comment(**test, strict=True))

    async def test04CreateAllReplies(self):
        claim_id = '1d8a5cc39ca02e55782d619e67131c0a20843be8'
        parent_comment = await self.post_comment(
            channel_name='@KevinWalterRabie',
            channel_id=fake.sha1(),
            comment='Hello everybody and welcome back to my chan nel',
            claim_id=claim_id,
            signing_ts='1234',
            signature='_'*128
        )
        parent_id = parent_comment['result']['comment_id']
        test_all = create_test_comments(
            ('comment', 'channel_name', 'channel_id', 'signature', 'parent_id'),
            parent_id=parent_id,
            comment=None,
            channel_name=None,
            channel_id=None,
            signature=None,
            claim_id=claim_id
        )
        for test in test_all:
            with self.subTest(test=test):
                if test['parent_id'] != parent_id:
                    continue
                else:
                    message = await self.post_comment(**test)
                    self.assertTrue('result' in message or 'error' in message)
                    if 'error' in message:
                        self.assertFalse(is_valid_base_comment(**test))
                    else:
                        self.assertTrue(is_valid_base_comment(**test))

    async def testSlackWebhook(self):
        claim_id = '1d8a5cc39ca02e55782d619e67131c0a20843be8'
        channel_name = '@name'
        channel_id = fake.sha1()
        signature = '{}'*64
        signing_ts = '1234'

        base = await self.post_comment(
            channel_name=channel_name,
            channel_id=channel_id,
            comment='duplicate',
            claim_id=claim_id,
            signing_ts=signing_ts,
            signature=signature
        )

        comment_id = base['result']['comment_id']

        with self.subTest(test=comment_id):
            await self.post_comment(
                channel_name=channel_name,
                channel_id=channel_id,
                comment='duplicate',
                claim_id=claim_id,
                signing_ts=signing_ts,
                signature=signature
            )


class ListCommentsTest(AsyncioTestCase):
    replace = {
        'claim_id': fake.sha1,
        'comment': fake.text,
        'channel_id': fake.sha1,
        'channel_name': fake_lbryusername,
        'signature': fake_signature,
        'signing_ts': fake_signing_ts,
        'parent_id': nothing
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.host = 'localhost'
        self.port = 5931
        config['mode'] = 'testing'
        config['testing']['file'] = ':memory:'
        self.claim_id = '1d8a5cc39ca02e55782d619e67131c0a20843be8'
        self.comment_ids = None

    @property
    def url(self):
        return f'http://{self.host}:{self.port}/api'

    async def post_comment(self, **params):
        return await jsonrpc_post(self.url, 'create_comment', **params)

    async def create_lots_of_comments(self, n=23):
        self.comment_list = [{key: self.replace[key]() for key in self.replace.keys()} for _ in range(23)]
        for comment in self.comment_list:
            comment['claim_id'] = self.claim_id
        self.comment_ids = [(await self.post_comment(**comm))['result']['comment_id']
                            for comm in self.comment_list]

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.server = app.CommentDaemon(config)
        await self.server.start(self.host, self.port)
        self.addCleanup(self.server.stop)

    async def testListComments(self):
        await self.create_lots_of_comments()
        response_one = await jsonrpc_post(
            self.url, 'get_claim_comments', page_size=20, page=1, top_level=1, claim_id=self.claim_id
        )
        self.assertIsNotNone(response_one)
        self.assertIn('result', response_one)
        response_one: dict = response_one['result']
        self.assertEqual(response_one['page_size'], len(response_one['items']))
        self.assertIn('items', response_one)

        comments = response_one['items']
        hidden = list(filter(lambda c: c['is_hidden'], comments))
        self.assertEqual(hidden, [])

        self.assertGreaterEqual(response_one['total_pages'], response_one['page'])
        last_page = response_one['total_pages']
        response = await jsonrpc_post(self.url, 'get_claim_comments', page_size=20,
                                page=last_page, top_level=1, claim_id=self.claim_id)
        self.assertIsNotNone(response)
        self.assertIn('result', response)
        response: dict = response['result']
        self.assertIs(type(response['items']), list)
        self.assertEqual(response['total_items'], response_one['total_items'])
        self.assertEqual(response['total_pages'], response_one['total_pages'])
