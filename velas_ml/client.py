"""OAuth com PKCE e chamadas autenticadas; nenhum token aparece nos logs."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

API = 'https://api.mercadolibre.com'
AUTH = 'https://auth.mercadolivre.com.br/authorization'


class APIError(RuntimeError):
    def __init__(self, status, path):
        self.status = status
        detail = {
            401: 'Autorização inválida. Execute o fluxo de autorização novamente.',
            403: 'A API negou acesso. Verifique as permissões do aplicativo para esta conta.',
            429: 'Limite de requisições atingido. Tente novamente mais tarde.',
        }.get(status, 'Verifique o aplicativo e tente novamente.')
        super().__init__(f'Mercado Livre: HTTP {status} em {path}. {detail}')


def private_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.token-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Client:
    def __init__(self, config, session=None):
        self.config = config
        self.session = session or requests.Session()
        self.token_path = config.dados / 'tokens.json'
        self.pending_path = config.dados / 'autorizacao.json'

    def authorization_url(self):
        uri = urlsplit(self.config.redirect_uri)
        if not self.config.client_id or uri.scheme != 'https' or not uri.netloc or uri.query or uri.fragment:
            raise ValueError('Preencha client_id e uma redirect_uri HTTPS fixa em config.toml.')
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        private_json(self.pending_path, {
            'verifier': verifier, 'state': state, 'created_at': time.time(),
            'client_id': self.config.client_id, 'redirect_uri': self.config.redirect_uri,
        })
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        return AUTH + '?' + urlencode({
            'response_type': 'code', 'client_id': self.config.client_id,
            'redirect_uri': self.config.redirect_uri, 'state': state,
            'code_challenge': challenge, 'code_challenge_method': 'S256',
        })

    def _exchange(self, data):
        secret = os.environ.get('MELI_CLIENT_SECRET')
        if not secret:
            raise ValueError('Defina MELI_CLIENT_SECRET no ambiente local; não envie a chave pelo chat.')
        # Não repetir POST: refresh tokens e authorization codes são de uso único.
        try:
            response = self.session.post(API + '/oauth/token', data={
                **data, 'client_id': self.config.client_id, 'client_secret': secret,
            }, timeout=30, allow_redirects=False)
        except requests.RequestException:
            raise RuntimeError('Falha de rede na autorização. Se o token foi consumido, autorize novamente.') from None
        if response.status_code != 200:
            raise APIError(response.status_code, '/oauth/token')
        token = response.json()
        if not all(token.get(k) for k in ('access_token', 'refresh_token', 'user_id', 'expires_in')):
            raise ValueError('A resposta de autorização não contém todos os campos necessários.')
        token['expires_at'] = time.time() + int(token['expires_in'])
        token['client_id'] = self.config.client_id
        private_json(self.token_path, token)
        return token

    def complete_authorization(self, callback):
        pending = json.loads(self.pending_path.read_text())
        actual, expected = urlsplit(callback), urlsplit(self.config.redirect_uri)
        query = parse_qs(actual.query)
        if (actual.scheme, actual.netloc, actual.path) != (expected.scheme, expected.netloc, expected.path):
            raise ValueError('A URL de retorno não corresponde à redirect_uri configurada.')
        if pending['client_id'] != self.config.client_id or pending['redirect_uri'] != self.config.redirect_uri:
            raise ValueError('A configuração mudou. Inicie uma nova autorização.')
        if time.time() - pending['created_at'] > 1800:
            raise ValueError('A tentativa expirou. Inicie uma nova autorização.')
        if not secrets.compare_digest(query.get('state', [''])[0], pending['state']):
            raise ValueError('State inválido. Inicie uma nova autorização.')
        if query.get('error') or not query.get('code'):
            raise ValueError('A autorização não foi concedida ou não retornou um código.')
        token = self._exchange({
            'grant_type': 'authorization_code', 'code': query['code'][0],
            'redirect_uri': self.config.redirect_uri, 'code_verifier': pending['verifier'],
        })
        self.pending_path.unlink()
        return token

    def token(self, refresh=False):
        if not self.token_path.exists():
            raise ValueError('Conta ainda não autorizada. Execute autorizar e depois concluir-autorizacao.')
        token = json.loads(self.token_path.read_text())
        if token.get('client_id') != self.config.client_id:
            raise ValueError('Tokens de outro aplicativo. Autorize novamente.')
        if refresh or token['expires_at'] <= time.time() + 60:
            token = self._exchange({'grant_type': 'refresh_token', 'refresh_token': token['refresh_token']})
        return token

    def get(self, path, params=None, headers=None):
        if not path.startswith('/') or '://' in path or '?' in path:
            raise ValueError('Caminho de API inválido.')
        refreshed = False
        for attempt in range(4):
            token = self.token()
            try:
                response = self.session.get(API + path, params=params, headers={
                    **(headers or {}), 'Authorization': 'Bearer ' + token['access_token'],
                }, timeout=30, allow_redirects=False)
            except requests.RequestException:
                if attempt == 3:
                    raise RuntimeError('Falha de rede ao consultar o Mercado Livre; nenhuma importação foi confirmada.') from None
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 401 and not refreshed and attempt < 3:
                self.token(refresh=True)
                refreshed = True
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                try:
                    delay = min(60, max(0, float(response.headers.get('Retry-After', 2 ** attempt))))
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(delay)
                continue
            if response.status_code == 204:
                return None
            if response.status_code != 200:
                raise APIError(response.status_code, path)
            return response.json()
        raise RuntimeError('Não foi possível concluir a consulta.')

    def orders(self, buyer):
        offset, seen = 0, set()
        while True:
            payload = self.get('/orders/search', params={
                'buyer': buyer, 'sort': 'date_asc', 'limit': 50, 'offset': offset,
                'order.date_created.from': self.config.desde + 'T00:00:00.000-03:00',
                'order.date_created.to': self.config.ate + 'T23:59:59.999-03:00',
            })
            batch = payload['results']
            total = int(payload['paging']['total'])
            if not batch:
                if offset < total:
                    raise ValueError('Paginação incompleta; a importação foi interrompida.')
                break
            new = [o for o in batch if str(o['id']) not in seen]
            if not new:
                raise ValueError('A API repetiu uma página; a importação foi interrompida.')
            for order in new:
                seen.add(str(order['id']))
                yield order
            offset += len(batch)
            if offset >= total:
                if len(seen) != total:
                    raise ValueError('O histórico mudou durante a paginação; repita a sincronização.')
                break
