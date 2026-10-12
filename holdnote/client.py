"""Pinned Memos 0.31 API. Redirects never receive your bearer credential."""
from dataclasses import dataclass
import requests
from .config import normalize_url


class DeliveryError(Exception):
    def __init__(self,kind,retryable=False):
        super().__init__(kind);self.kind=kind;self.retryable=retryable


@dataclass
class Receipt:
    name:str
    url:str


class Memos:
    def __init__(self,url,token,session=None):
        self.url=normalize_url(url);self.session=session or requests.Session()
        self.session.trust_env=False
        self.session.headers.update({'Authorization':'Bearer '+token,'Content-Type':'application/json','User-Agent':'HoldNote/0.1'})
    def request(self,method,path,body=None):
        try:r=self.session.request(method,self.url+path,json=body,timeout=(5,20),allow_redirects=False)
        except requests.RequestException:raise DeliveryError('network',True) from None
        if r.status_code in [401,403]:raise DeliveryError('authentication')
        if r.status_code==404:raise DeliveryError('not-found')
        if r.status_code==409:raise DeliveryError('already-exists')
        if r.status_code==429 or r.status_code>=500:raise DeliveryError('server',True)
        if not 200<=r.status_code<300:raise DeliveryError('api-contract')
        try:return r.json()
        except ValueError:raise DeliveryError('invalid-json') from None
    def check(self):
        r=self.request('GET','/api/v1/auth/me')
        return {'username':r['user']['username'],'role':r['user']['role']}
    def send(self,text,capture_id):
        body={'content':text,'visibility':'PRIVATE'}
        try:memo=self.request('POST','/api/v1/memos?memoId='+capture_id,body)
        except DeliveryError as e:
            if e.kind!='already-exists':raise
            memo=self.request('GET','/api/v1/memos/'+capture_id)
            if memo.get('content')!=text or memo.get('visibility')!='PRIVATE':raise DeliveryError('id-conflict')
        name=memo['name']
        if memo.get('visibility')!='PRIVATE':raise DeliveryError('privacy')
        return Receipt(name,self.url+'/'+name)
