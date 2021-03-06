# coding: utf-8

from __future__ import absolute_import, unicode_literals

from contextlib import contextmanager
from datetime import datetime, timedelta
from itertools import product
import json
import random
import string

from cryptography.hazmat.backends import default_backend
from mock import Mock, mock_open, patch, sentinel
import pytest
from six import string_types, text_type

from boxsdk.auth.jwt_auth import JWTAuth
from boxsdk.config import API
from boxsdk.object.user import User
from boxsdk.util.compat import total_seconds


@pytest.fixture(params=[16, 32, 128])
def jti_length(request):
    return request.param


@pytest.fixture(params=('RS256', 'RS512'))
def jwt_algorithm(request):
    return request.param


@pytest.fixture(scope='module')
def jwt_key_id():
    return 'jwt_key_id_1'


@pytest.fixture(params=(None, b'strong_password'))
def rsa_passphrase(request):
    return request.param


@pytest.fixture(scope='function')
def successful_token_response(successful_token_mock, successful_token_json_response):
    # pylint:disable=redefined-outer-name
    response = successful_token_json_response.copy()
    del response['refresh_token']
    successful_token_mock.json = Mock(return_value=response)
    successful_token_mock.ok = True
    successful_token_mock.content = json.dumps(response)
    successful_token_mock.status_code = 200
    return successful_token_mock


@pytest.fixture
def jwt_auth_init_mocks(mock_network_layer, successful_token_response, jwt_algorithm, jwt_key_id, rsa_passphrase):
    # pylint:disable=redefined-outer-name

    @contextmanager
    def _jwt_auth_init_mocks(**kwargs):
        assert_authed = kwargs.pop('assert_authed', True)
        fake_client_id = 'fake_client_id'
        fake_client_secret = 'fake_client_secret'
        assertion = Mock()
        data = {
            'grant_type': JWTAuth._GRANT_TYPE,  # pylint:disable=protected-access
            'client_id': fake_client_id,
            'client_secret': fake_client_secret,
            'assertion': assertion,
            'box_device_id': '0',
            'box_device_name': 'my_awesome_device',
        }

        mock_network_layer.request.return_value = successful_token_response
        key_file_read_data = b'key_file_read_data'
        with patch('boxsdk.auth.jwt_auth.open', mock_open(read_data=key_file_read_data), create=True) as jwt_auth_open:
            with patch('cryptography.hazmat.primitives.serialization.load_pem_private_key') as load_pem_private_key:
                oauth = JWTAuth(
                    client_id=fake_client_id,
                    client_secret=fake_client_secret,
                    rsa_private_key_file_sys_path=sentinel.rsa_path,
                    rsa_private_key_passphrase=rsa_passphrase,
                    network_layer=mock_network_layer,
                    box_device_name='my_awesome_device',
                    jwt_algorithm=jwt_algorithm,
                    jwt_key_id=jwt_key_id,
                    enterprise_id=kwargs.pop('enterprise_id', None),
                    **kwargs
                )

                jwt_auth_open.assert_called_once_with(sentinel.rsa_path, 'rb')
                jwt_auth_open.return_value.read.assert_called_once_with()  # pylint:disable=no-member
                load_pem_private_key.assert_called_once_with(
                    key_file_read_data,
                    password=rsa_passphrase,
                    backend=default_backend(),
                )

                yield oauth, assertion, fake_client_id, load_pem_private_key.return_value

        if assert_authed:
            mock_network_layer.request.assert_called_once_with(
                'POST',
                '{0}/token'.format(API.OAUTH2_API_URL),
                data=data,
                headers={'content-type': 'application/x-www-form-urlencoded'},
                access_token=None,
            )
            assert oauth.access_token == successful_token_response.json()['access_token']

    return _jwt_auth_init_mocks


def test_refresh_authenticates_with_user_if_enterprise_id_and_user_both_passed_to_constructor(jwt_auth_init_and_auth_mocks):
    user = 'fake_user_id'
    with jwt_auth_init_and_auth_mocks(sub=user, sub_type='user', enterprise_id='fake_enterprise_id', user=user) as oauth:
        oauth.refresh(None)


@pytest.mark.parametrize('jwt_auth_method_name', ['authenticate_user', 'authenticate_instance'])
def test_authenticate_raises_value_error_if_sub_was_never_given(jwt_auth_init_mocks, jwt_auth_method_name):
    with jwt_auth_init_mocks(assert_authed=False) as params:
        auth = params[0]
        authenticate_method = getattr(auth, jwt_auth_method_name)
        with pytest.raises(ValueError):
            authenticate_method()


def test_jwt_auth_constructor_raises_type_error_if_user_is_unsupported_type(jwt_auth_init_mocks):
    with pytest.raises(TypeError):
        with jwt_auth_init_mocks(user=object()):
            assert False


def test_authenticate_user_raises_type_error_if_user_is_unsupported_type(jwt_auth_init_mocks):
    with jwt_auth_init_mocks(assert_authed=False) as params:
        auth = params[0]
        with pytest.raises(TypeError):
            auth.authenticate_user(object())


@pytest.mark.parametrize('user_id_for_init', [None, 'fake_user_id_1'])
def test_authenticate_user_saves_user_id_for_future_calls(jwt_auth_init_and_auth_mocks, user_id_for_init, jwt_encode):

    def assert_jwt_encode_call_args(user_id):
        assert jwt_encode.call_args[0][0]['sub'] == user_id
        assert jwt_encode.call_args[0][0]['box_sub_type'] == 'user'
        jwt_encode.call_args = None

    with jwt_auth_init_and_auth_mocks(sub=None, sub_type=None, assert_authed=False, user=user_id_for_init) as auth:
        for new_user_id in ['fake_user_id_2', 'fake_user_id_3']:
            auth.authenticate_user(new_user_id)
            assert_jwt_encode_call_args(new_user_id)
            auth.authenticate_user()
            assert_jwt_encode_call_args(new_user_id)


def test_authenticate_instance_raises_value_error_if_different_enterprise_id_is_given(jwt_auth_init_mocks):
    with jwt_auth_init_mocks(enterprise_id='fake_enterprise_id_1', assert_authed=False) as params:
        auth = params[0]
        with pytest.raises(ValueError):
            auth.authenticate_instance('fake_enterprise_id_2')


def test_authenticate_instance_saves_enterprise_id_for_future_calls(jwt_auth_init_and_auth_mocks):
    enterprise_id = 'fake_enterprise_id'
    with jwt_auth_init_and_auth_mocks(sub=enterprise_id, sub_type='enterprise', assert_authed=False) as auth:
        auth.authenticate_instance(enterprise_id)
        auth.authenticate_instance()
        auth.authenticate_instance(enterprise_id)
        with pytest.raises(ValueError):
            auth.authenticate_instance('fake_enterprise_id_2')


@pytest.yield_fixture
def jwt_encode():
    with patch('jwt.encode') as patched_jwt_encode:
        yield patched_jwt_encode


@pytest.fixture
def jwt_auth_auth_mocks(jti_length, jwt_algorithm, jwt_key_id, jwt_encode):

    @contextmanager
    def _jwt_auth_auth_mocks(sub, sub_type, oauth, assertion, client_id, secret, assert_authed=True):
        # pylint:disable=redefined-outer-name
        with patch('boxsdk.auth.jwt_auth.datetime') as mock_datetime:
            with patch('boxsdk.auth.jwt_auth.random.SystemRandom') as mock_system_random:
                jwt_encode.return_value = assertion
                mock_datetime.utcnow.return_value = datetime(2015, 7, 6, 12, 1, 2)
                mock_datetime.return_value = datetime(1970, 1, 1)
                now_plus_30 = mock_datetime.utcnow.return_value + timedelta(seconds=30)
                exp = int(total_seconds(now_plus_30 - datetime(1970, 1, 1)))
                system_random = mock_system_random.return_value
                system_random.randint.return_value = jti_length
                random_choices = [random.random() for _ in range(jti_length)]
                system_random.random.side_effect = random_choices
                ascii_alphabet = string.ascii_letters + string.digits
                ascii_len = len(ascii_alphabet)
                jti = ''.join(ascii_alphabet[int(r * ascii_len)] for r in random_choices)

                yield oauth

                if assert_authed:
                    system_random.randint.assert_called_once_with(16, 128)
                    assert len(system_random.random.mock_calls) == jti_length
                    jwt_encode.assert_called_once_with({
                        'iss': client_id,
                        'sub': sub,
                        'box_sub_type': sub_type,
                        'aud': 'https://api.box.com/oauth2/token',
                        'jti': jti,
                        'exp': exp,
                    }, secret, algorithm=jwt_algorithm, headers={'kid': jwt_key_id})

    return _jwt_auth_auth_mocks


@pytest.fixture
def jwt_auth_init_and_auth_mocks(jwt_auth_init_mocks, jwt_auth_auth_mocks):

    @contextmanager
    def _jwt_auth_init_and_auth_mocks(sub, sub_type, *jwt_auth_init_mocks_args, **jwt_auth_init_mocks_kwargs):
        assert_authed = jwt_auth_init_mocks_kwargs.pop('assert_authed', True)
        with jwt_auth_init_mocks(*jwt_auth_init_mocks_args, assert_authed=assert_authed, **jwt_auth_init_mocks_kwargs) as params:
            with jwt_auth_auth_mocks(sub, sub_type, *params, assert_authed=assert_authed) as oauth:
                yield oauth

    return _jwt_auth_init_and_auth_mocks


@pytest.mark.parametrize(
    ('user', 'pass_in_init'),
    list(product([str('fake_user_id'), text_type('fake_user_id'), User(None, 'fake_user_id')], [False, True])),
)
def test_authenticate_user_sends_post_request_with_correct_params(jwt_auth_init_and_auth_mocks, user, pass_in_init):
    # pylint:disable=redefined-outer-name
    if isinstance(user, User):
        user_id = user.object_id
    elif isinstance(user, string_types):
        user_id = user
    else:
        raise NotImplementedError
    init_kwargs = {}
    authenticate_params = []
    if pass_in_init:
        init_kwargs['user'] = user
    else:
        authenticate_params.append(user)
    with jwt_auth_init_and_auth_mocks(user_id, 'user', **init_kwargs) as oauth:
        oauth.authenticate_user(*authenticate_params)


@pytest.mark.parametrize(('pass_in_init', 'pass_in_auth'), [(True, False), (False, True), (True, True)])
def test_authenticate_instance_sends_post_request_with_correct_params(jwt_auth_init_and_auth_mocks, pass_in_init, pass_in_auth):
    # pylint:disable=redefined-outer-name
    enterprise_id = 'fake_enterprise_id'
    init_kwargs = {}
    auth_params = []
    if pass_in_init:
        init_kwargs['enterprise_id'] = enterprise_id
    if pass_in_auth:
        auth_params.append(enterprise_id)
    with jwt_auth_init_and_auth_mocks(enterprise_id, 'enterprise', **init_kwargs) as oauth:
        oauth.authenticate_instance(*auth_params)


def test_refresh_app_user_sends_post_request_with_correct_params(jwt_auth_init_and_auth_mocks):
    # pylint:disable=redefined-outer-name
    fake_user_id = 'fake_user_id'
    with jwt_auth_init_and_auth_mocks(fake_user_id, 'user', user=fake_user_id) as oauth:
        oauth.refresh(None)


def test_refresh_instance_sends_post_request_with_correct_params(jwt_auth_init_and_auth_mocks):
    # pylint:disable=redefined-outer-name
    enterprise_id = 'fake_enterprise_id'
    with jwt_auth_init_and_auth_mocks(enterprise_id, 'enterprise', enterprise_id=enterprise_id) as oauth:
        oauth.refresh(None)
