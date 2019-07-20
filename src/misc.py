import binascii
import logging
import re
from json import JSONDecodeError

import hashlib
import aiohttp

import ecdsa
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.serialization import load_der_public_key
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from cryptography.exceptions import InvalidSignature

logger = logging.getLogger(__name__)

ID_LIST = {'claim_id', 'parent_id', 'comment_id', 'channel_id'}

ERRORS = {
    'INVALID_PARAMS': {'code': -32602, 'message': 'Invalid Method Parameter(s).'},
    'INTERNAL': {'code': -32603, 'message': 'Internal Server Error.'},
    'METHOD_NOT_FOUND': {'code': -32601, 'message': 'The method does not exist / is not available.'},
    'INVALID_REQUEST': {'code': -32600, 'message': 'The JSON sent is not a valid Request object.'},
    'PARSE_ERROR': {
        'code': -32700,
        'message': 'Invalid JSON was received by the server.\n'
                   'An error occurred on the server while parsing the JSON text.'
    }
}


def make_error(error, exc: Exception = None) -> dict:
    body = ERRORS[error] if error in ERRORS else ERRORS['INTERNAL']
    try:
        if exc:
            body.update({
                type(exc).__name__: str(exc)
            })
    finally:
        return body


def channel_matches_pattern(channel_id: str, channel_name: str):
    assert channel_id and channel_name
    assert type(channel_id) is str and type(channel_name) is str
    assert re.fullmatch(
        '^@(?:(?![\x00-\x08\x0b\x0c\x0e-\x1f\x23-\x26'
        '\x2f\x3a\x3d\x3f-\x40\uFFFE-\U0000FFFF]).){1,255}$',
        channel_name
    )
    assert re.fullmatch('[a-z0-9]{40}', channel_id)


async def resolve_channel_claim(app: dict, channel_id: str, channel_name: str):
    lbry_url = f'lbry://{channel_name}#{channel_id}'
    resolve_body = {
        'method': 'resolve',
        'params': {
            'urls': [lbry_url, ]
        }
    }
    async with aiohttp.request('POST', app['config']['LBRYNET'], json=resolve_body) as req:
        try:
            resp = await req.json()
        except JSONDecodeError as jde:
            logger.exception(jde.msg)
            raise Exception('JSON Decode Error in Claim Resolution')
        finally:
            if 'result' in resp:
                return resp['result'].get(lbry_url)
            raise ValueError('claim resolution yields error', {'error': resp['error']})


def is_signature_valid(encoded_signature, signature_digest, public_key_bytes):
    try:
        public_key = load_der_public_key(public_key_bytes, default_backend())
        public_key.verify(encoded_signature, signature_digest, ec.ECDSA(Prehashed(hashes.SHA256())))
        return True
    except (ValueError, InvalidSignature) as err:
        logger.debug('Signature Valiadation Failed: %s', err)
    return False


def get_encoded_signature(signature):
    signature = signature.encode() if type(signature) is str else signature
    r = int(signature[:int(len(signature) / 2)], 16)
    s = int(signature[int(len(signature) / 2):], 16)
    return ecdsa.util.sigencode_der(r, s, len(signature) * 4)

def validate_base_comment(comment: str, claim_id: str, **kwargs):
    assert 0 < len(comment) <= 2000
    assert re.fullmatch('[a-z0-9]{40}', claim_id)


async def is_authentic_delete_signal(app, comment_id: str, channel_name: str, channel_id: str, signature: str):
    claim = await resolve_channel_claim(app, channel_id, channel_name)
    if claim:
        public_key = claim['value']['public_key']
        claim_hash = binascii.unhexlify(claim['claim_id'].encode())[::-1]
        pieces_injest = b''.join((comment_id.encode(), claim_hash))
        return is_signature_valid(
            encoded_signature=get_encoded_signature(signature),
            signature_digest=hashlib.sha256(pieces_injest).digest(),
            public_key_bytes=binascii.unhexlify(public_key.encode())
        )
    return False


def clean_input_params(kwargs: dict):
    for k, v in kwargs.items():
        if type(v) is str:
            kwargs[k] = v.strip()
            if k in ID_LIST:
                kwargs[k] = v.lower()

