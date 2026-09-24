#!/usr/bin/env python3
"""QUANT TRADING SIGNALS — local BTC/USDC agent. Python 3.12.
Contains no keys. Configuration: config.txt. --help shows the modes.
"""
from __future__ import annotations


# ========================================================================
# CORE: indicators.py
"""Shared indicator math and the LOCAL stop-protection layer.

The entry-signal engine is deliberately NOT here: it lives in the publisher's
private `estrategia.py`, which is never distributed. A customer running this
file cannot decide when to enter without calling the paid service.

Everything needed to PROTECT an already-open position (stop distance, trailing
mode, emergency close) is local on purpose, so a service outage can never leave
a funded position unmanaged.
"""


from dataclasses import dataclass, asdict


import math


import numpy as np


import pandas as pd


@dataclass(frozen=True)
class Config:
    """Execution and sizing only. Entry-strategy parameters are NOT here:
    they live in the publisher's private estrategia.py, server-side."""
    stop_atr: float = 1.5
    add_step_pct: float = 2.
    max_entries: int = 3
    equity_pct: float = 100.
    leverage: float = 5.
    fee_rate: float = .00045
    slippage_bps: float = 2.
    capital: float = 10000.

    def validate(self):
        if self.stop_atr <= 0:
            raise ValueError('Invalid initial stop multiple')
        if not 0 < self.equity_pct <= 100 or not 1 <= self.leverage <= 5:
            raise ValueError('Notional percentage (0,100]; margin leverage [1,5]')
        if not isinstance(self.max_entries,int) or self.max_entries < 0 or self.add_step_pct <= 0:
            raise ValueError('Invalid entry count or step')
        if self.fee_rate < 0 or self.slippage_bps < 0 or self.capital <= 0:
            raise ValueError('Invalid costs or capital')

    def to_dict(self):
        return asdict(self)

    def entry_capacity(self,opened,requested=1):
        """0 = unlimited entries; margin controls still apply."""
        return requested if self.max_entries==0 else max(0,min(requested,self.max_entries-opened))


def frame(raw):
    d = pd.DataFrame(raw)
    for key in ('o','h','l','c','v'):
        d[key] = d[key].astype(float)
    return d.sort_values('t').reset_index(drop=True)


def rma(a, n):
    """Wilder's method seeded with an SMA of n valid points, like Pine's ta.rma."""
    a = np.asarray(a, dtype=float)
    result = np.full(len(a), np.nan)
    seed, previous = [], np.nan
    for i, value in enumerate(a):
        if not np.isfinite(value):
            continue
        if np.isnan(previous):
            seed.append(value)
            if len(seed) == n:
                previous = float(np.mean(seed))
        else:
            previous = (previous * (n-1) + value) / n
        result[i] = previous
    return result


def wma(a, n):
    n = max(1, int(n))
    return pd.Series(a).rolling(n).apply(lambda x: np.dot(x,np.arange(1,n+1))/(n*(n+1)/2),raw=True).to_numpy()


def hma(a, n):
    # Pine-style rounding of the square root: .5 rounds up; the lengths used are never ambiguous.
    return wma(2*wma(a,n//2)-wma(a,n),int(math.floor(math.sqrt(n)+.5)))


# ========================================================================
# CORE: backtest.py
import argparse


from dataclasses import dataclass


import hashlib


import json


import math


from pathlib import Path


import numpy as np


# ========================================================================
# CORE: ema_strategy.py
"""EMA cross on 5m. ATR/RSI filters and the latest already-published funding."""


from dataclasses import dataclass,asdict


import numpy as np


import pandas as pd


@dataclass(frozen=True)
class StopIndicators:
    """Indicator PERIODS the local stop layer needs. Thresholds, the entry rule
    and the sweep/context layer are not here — they are server-side only."""
    fast:int=34
    slow:int=89
    atr_period:int=7
    rsi_period:int=14

    def validate(self):
        if not 1<self.fast<self.slow or self.atr_period<2 or self.rsi_period<2:
            raise ValueError('Invalid periods')

    def to_dict(self):return asdict(self)


def ema(values,n):
    # Seeded at the first stored candle, constant when replaying the file.
    return pd.Series(values).ewm(span=n,adjust=False,min_periods=n).mean().to_numpy()


def last_known_funding(rows,times):
    ordered=sorted(rows,key=lambda x:int(x['time']))
    ft=np.array([int(x['time']) for x in ordered],dtype=np.int64)
    raw=np.array([float(x['fundingRate']) for x in ordered])
    if np.any(np.diff(ft)<=0):raise ValueError('Unsorted or duplicate funding')
    # Known duration of the finished event, in whole hours. Lets us compare
    # Hyperliquid's hourly rate against Binance's typically 8h rate.
    hours=np.maximum(1.,np.round(np.r_[8*3600000,np.diff(ft)]/3600000))
    ix=np.searchsorted(ft,times,side='left')-1
    rate=np.full(len(times),np.nan);known=np.full(len(times),-1,dtype=np.int64)
    valid=ix>=0;selected=ix[valid]
    rate[valid]=raw[selected]*8/hours[selected];known[valid]=ft[selected]
    # Data more stale than its period + 1h does not authorize an entry.
    stale=np.zeros(len(times),dtype=bool)
    stale[valid]=times[valid]-ft[selected]>(hours[selected]+1)*3600000
    rate[stale]=np.nan
    return rate,known


def stop_inputs(data,cfg=None):
    """Local indicator inputs the stop layer needs. Computes NO entry signal.

    Identical math to the indicators the shipped v2.2.0 used, so trailing-stop
    behaviour is unchanged; only the entry rule and its thresholds were removed.
    """
    cfg=cfg or StopIndicators();cfg.validate()
    bars=frame(data['BTC_5m.json']);c=bars.c.to_numpy()
    h,l=bars.h.to_numpy(),bars.l.to_numpy();prev=np.r_[np.nan,c[:-1]]
    tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)));tr[0]=h[0]-l[0]
    change=np.r_[np.nan,np.diff(c)]
    atr=rma(tr,cfg.atr_period)
    # Prior average, excludes the current ATR from the denominator.
    ref=pd.Series(atr).shift(1).rolling(96).mean().to_numpy()
    gain=rma(np.maximum(change,0),cfg.rsi_period);loss=rma(np.maximum(-change,0),cfg.rsi_period)
    with np.errstate(divide='ignore',invalid='ignore'):rsi=100-100/(1+gain/loss)
    rsi[(loss==0)&(gain>0)]=100;rsi[(loss==0)&(gain==0)]=50
    funding,funding_time=last_known_funding(data['BTC_funding.json'],bars['T'].to_numpy(dtype=np.int64)+1)
    return {'bars':bars,'ema_fast':ema(c,cfg.fast),'ema_slow':ema(c,cfg.slow),
            'rsi':rsi,'atr_expansion':atr/ref,'funding_known_8h':funding,
            'funding_known_time':funding_time}


# ========================================================================
# CORE: adaptive_stop.py
from dataclasses import dataclass,asdict


import math


import numpy as np


@dataclass(frozen=True)
class StopConfig:
    atr_period:int=14
    wide_atr:float=8.
    wide_min_pct:float=1.
    weak_ratio:float=.5
    adverse_ratio:float=.25
    activate_profit_pct:float=2.
    confirm_bars:int=2
    rsi_favorable:float=55.
    rsi_drop_points:float=3.
    momentum_bars:int=3
    atr_expansion_min:float=.9
    tighten_before_activation:bool=False

    def validate(self):
        if self.atr_period<2 or self.wide_atr<=0 or self.wide_min_pct<=0:
            raise ValueError('Invalid stop distance')
        if not 0<self.adverse_ratio<self.weak_ratio<1:
            raise ValueError('Distances must decrease as indicators weaken')
        if self.activate_profit_pct<0 or self.confirm_bars<1 or self.momentum_bars<1:
            raise ValueError('Invalid activation or confirmation')
        if not 50<self.rsi_favorable<100 or self.rsi_drop_points<0:
            raise ValueError('Invalid strength thresholds')

    def to_dict(self):return asdict(self)


def prepare_stop_features(features,stop_cfg):
    """Only appends data: does NOT change signal, the initial 12h ATR, or the entry EMAs."""
    stop_cfg.validate();f=dict(features);b=f['bars'];c=b.c.to_numpy()
    h,l=b.h.to_numpy(),b.l.to_numpy();prev=np.r_[np.nan,c[:-1]]
    tr=np.maximum(h-l,np.maximum(abs(h-prev),abs(l-prev)));tr[0]=h[0]-l[0]
    f['stop_atr_5m']=rma(tr,stop_cfg.atr_period)
    for side,label in ((1,'long'),(-1,'short')):
        fast,slow=f['ema_fast'],f['ema_slow'];rsi=f['rsi'];n=stop_cfg.momentum_bars
        slope=fast-np.r_[np.full(n,np.nan),fast[:-n]]
        strength=rsi if side==1 else 100-rsi
        old_strength=np.r_[np.full(n,np.nan),strength[:-n]]
        ema_wrong=side*(fast-slow)<=0
        price_wrong=side*(c-fast)<0
        against=ema_wrong | ((strength<50)&price_wrong)
        favorable=(~ema_wrong)&(~price_wrong)&(side*slope>=0)&(strength>=stop_cfg.rsi_favorable)
        favorable &= (strength-old_strength>=-stop_cfg.rsi_drop_points)
        favorable &= np.isfinite(f['funding_known_8h'])&(side*f['funding_known_8h']<=0)
        favorable &= np.isfinite(f['atr_expansion'])&(f['atr_expansion']>=stop_cfg.atr_expansion_min)
        f['stop_mode_'+label]=np.where(against,0,np.where(favorable,2,1)).astype(np.int8)
    return f


# ========================================================================
# CORE: agente/core.py
"""Persistent state and agent rules; contains no exchange calls."""


from dataclasses import dataclass, asdict, replace


from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING


import hashlib


import json


import logging


import math


import os


from pathlib import Path


import re


import sqlite3


import sys


import uuid


BAR_MS=300_000


class SafetyError(RuntimeError):pass


class TemporaryError(RuntimeError):pass


class Rejected(RuntimeError):pass


def canonical(obj):return json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False)


def identity(obj):return hashlib.sha256(canonical(obj).encode()).hexdigest()


def cloid(instance,kind,sequence):
    return '0x'+hashlib.blake2b(f'{instance}:{kind}:{sequence}'.encode(),digest_size=16).hexdigest()


def floor_size(value,decimals):
    return float(Decimal(str(value)).quantize(Decimal(1).scaleb(-decimals),rounding=ROUND_FLOOR))


def price_tick(price):
    if not math.isfinite(price) or price<=0:raise ValueError('Invalid price')
    # Same as the BTC backtest: integers below 100,000, tens above.
    return 10**max(0,math.floor(math.log10(price))-4)


def stop_price(price,side):
    tick=price_tick(price)
    return (math.floor(price/tick) if side==1 else math.ceil(price/tick))*tick


def order_price(price,is_buy,sz_decimals=5):
    tick=max(10**(math.floor(math.log10(price))-4),10**(-(6-sz_decimals)))
    return float((Decimal(str(price))/Decimal(str(tick))).to_integral_value(
        rounding=ROUND_CEILING if is_buy else ROUND_FLOOR)*Decimal(str(tick)))


@dataclass
class Trailing:
    side:int
    first:float
    stop:float
    best_close:float
    active:bool=False
    mode:int=2
    candidate:int=2
    count:int=0

    def advance(self,close,atr,raw,cfg):
        """Same transition as AdaptiveEngine.update_stop, applied to a new close."""
        s=replace(self)
        s.best_close=max(s.best_close,close) if s.side==1 else min(s.best_close,close)
        favorable=s.side*(s.best_close/s.first-1)*100
        if favorable>=cfg.activate_profit_pct:s.active=True
        if raw==s.candidate:s.count+=1
        else:s.candidate=raw;s.count=1
        if s.count>=cfg.confirm_bars:s.mode=raw
        if not s.active and (s.mode==2 or not cfg.tighten_before_activation):return s
        if not math.isfinite(atr):return s
        ratio=1. if s.mode==2 else cfg.weak_ratio if s.mode==1 else cfg.adverse_ratio
        distance=max(cfg.wide_atr*ratio*atr,close*cfg.wide_min_pct/100*ratio)
        desired=s.best_close-s.side*distance
        tick=price_tick(close)
        desired=min(desired,close-tick) if s.side==1 else max(desired,close+tick)
        candidate=stop_price(max(tick,desired),s.side)
        s.stop=max(s.stop,candidate) if s.side==1 else min(s.stop,candidate)
        return s


def size_for_equity(equity,pct,price,decimals,free_margin,leverage,fee_rate):
    if not all(math.isfinite(x) for x in (equity,pct,price,free_margin,leverage,fee_rate)):
        raise Rejected('Invalid balance or price')
    if equity<=0 or not 0<pct<=100 or price<=0 or leverage<=0:
        raise Rejected('Balance, percentage or leverage out of range')
    qty=floor_size(equity*pct/100/price,decimals)
    notional=qty*price
    if qty<=0 or notional<10:raise Rejected('Order below the 10 USDC minimum')
    required=notional/leverage+notional*fee_rate
    if required>free_margin+1e-8:
        raise Rejected(f'Insufficient free margin: required {required:.2f}, available {free_margin:.2f} USDC')
    return qty


@dataclass
class Account:
    equity:float
    free_margin:float
    qty:float=0.
    entry:float=0.
    unrealized:float=0.
    leverage_type:str='isolated'
    leverage:int=5
    time:int=0
    liquidation:float|None=None


@dataclass
class Outcome:
    status:str
    oid:int|None=None
    qty:float=0.
    price:float=0.
    message:str=''
    details:dict|None=None


class Store:
    def __init__(self,path,same_thread=True):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        # same_thread=False is used by the service, whose requests run on a thread
        # pool: sqlite3 forbids cross-thread use unless this is disabled. The only
        # cross-thread user is SignalFeed, which serialises every access with a lock.
        self.db=sqlite3.connect(self.path,check_same_thread=same_thread)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS candles (interval TEXT, t INTEGER, payload TEXT, PRIMARY KEY(interval,t))')
        self.db.execute('CREATE TABLE IF NOT EXISTS funding (t INTEGER PRIMARY KEY, payload TEXT)')
        self.db.commit()
    def get(self,key,default=None):
        row=self.db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default
    def put(self,key,value):
        with self.db:self.db.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',(key,canonical(value)))
    def candles(self,interval):
        return [json.loads(x[0]) for x in self.db.execute('SELECT payload FROM candles WHERE interval=? ORDER BY t',(interval,))]
    def last_candle(self,interval):
        r=self.db.execute('SELECT MAX(t) FROM candles WHERE interval=?',(interval,)).fetchone()
        return r[0]
    def add_candles(self,interval,rows):
        with self.db:
            for row in rows:
                # Closed candles already stored are never rewritten on restart.
                self.db.execute('INSERT OR IGNORE INTO candles VALUES (?,?,?)',(interval,int(row['t']),canonical(row)))
    def add_funding(self,rows):
        with self.db:
            for row in rows:self.db.execute('INSERT OR IGNORE INTO funding VALUES (?,?)',(int(row['time']),canonical(row)))
    def funding(self):return [json.loads(x[0]) for x in self.db.execute('SELECT payload FROM funding ORDER BY t')]
    def close(self):self.db.close()


def new_state():
    return {'schema':1,'instance':uuid.uuid4().hex,'sequence':0,'last_bar':0,
            'position':None,'pending':{},'closed_at':0,'blocked':None,'config_hash':None}


class OneInstance:
    """OS-level lock; also released after an unexpected termination."""
    def __init__(self,key,directory=None):
        folder=Path(directory) if directory else Path.home()/'.antonio_btc_agent'/'locks'
        folder.mkdir(parents=True,exist_ok=True);self.path=folder/(identity(key)+'.lock');self.file=None
    def __enter__(self):
        self.file=self.path.open('a+b');self.file.seek(0)
        if not self.file.read(1):self.file.write(b'0');self.file.flush()
        self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();raise SafetyError('Another agent is already running for this wallet, network and mode on this computer')
        return self
    def __exit__(self,*args):self.file.close()


class SecretFilter(logging.Filter):
    def filter(self,record):
        text=record.getMessage()
        text=re.sub(r'0x[0-9a-fA-F]{64}\b','[KEY/HEX REDACTED]',text)
        text=text.replace('\r',' ').replace('\n',' | ')
        record.msg=text;record.args=()
        return True


def configure_log(path,mode):
    log=logging.getLogger('quant_trading_signals')
    for old in log.handlers:old.flush();old.close()
    log.handlers.clear();log.propagate=False;log.setLevel(logging.INFO)
    fmt=logging.Formatter(f'%(asctime)s | {mode.upper()} | %(levelname)s | %(message)s',datefmt='%Y-%m-%d %H:%M:%S%z')
    # FileHandler emits and flushes on EVERY message, not when the process closes.
    # Each startup creates a session log; operational state lives in SQLite.
    for handler in (logging.StreamHandler(sys.stdout),logging.FileHandler(path,mode='w',encoding='utf-8')):
        handler.setFormatter(fmt);handler.addFilter(SecretFilter());log.addHandler(handler)
    return log


# ========================================================================
# CORE: agente/market.py
"""Public Hyperliquid data with a fixed seed and persisted closed candles."""


import math


import time


import requests


import numpy as np


BASE_URLS={'mainnet':'https://api.hyperliquid.xyz','testnet':'https://api.hyperliquid-testnet.xyz'}


class PublicAPI:
    def __init__(self,network='mainnet',timeout=12):
        self.base_url=BASE_URLS[network];self.timeout=timeout;self.session=requests.Session()
        self.session.headers.update({'User-Agent':'Quant-Trading-Signals/2.0'})
    def post(self,payload):
        try:
            r=self.session.post(self.base_url+'/info',json=payload,timeout=self.timeout)
            if r.status_code==429:raise TemporaryError('API rate limit 429; waiting before retrying')
            r.raise_for_status();return r.json()
        except (requests.RequestException,ValueError) as e:
            raise TemporaryError(f'Public query failed ({type(e).__name__})') from None
    def meta(self):return self.post({'type':'meta'})
    def candles(self,interval,start,end):
        return self.post({'type':'candleSnapshot','req':{'coin':'BTC','interval':interval,'startTime':start,'endTime':end}})
    def funding(self,start,end):
        rows=[];cursor=start
        while cursor<=end:
            batch=self.post({'type':'fundingHistory','coin':'BTC','startTime':cursor,'endTime':end})
            if not isinstance(batch,list):raise TemporaryError('Invalid funding response')
            rows.extend(batch)
            if len(batch)<500:break
            next_cursor=max(int(r['time']) for r in batch)+1
            if next_cursor<=cursor:raise SafetyError('Funding timestamps are not advancing')
            cursor=next_cursor
        return rows
    def account(self,address):return self.post({'type':'clearinghouseState','user':address})
    def orders(self,address):return self.post({'type':'frontendOpenOrders','user':address})
    def order(self,address,oid):return self.post({'type':'orderStatus','user':address,'oid':oid})
    def fills(self,address,start,end):
        rows=[];cursor=start
        for _ in range(10):
            batch=self.post({'type':'userFillsByTime','user':address,'startTime':cursor,'endTime':end,'aggregateByTime':True})
            rows.extend(batch)
            if len(batch)<2000:break
            cursor=max(int(x['time']) for x in batch)+1
        else:raise TemporaryError('Too many fills to reconcile; review the account')
        return [r for r in rows if r.get('coin')=='BTC']
    def quote(self):
        m,ctx=self.post({'type':'metaAndAssetCtxs'})
        i=next(i for i,x in enumerate(m['universe']) if x['name']=='BTC')
        return {'mid':float(ctx[i].get('midPx') or ctx[i]['markPx']),
                'mark':float(ctx[i]['markPx']),'time':int(time.time()*1000)}


# A fixed source/seed contract is shared by the private service and local stops.
# Changing source never reuses another venue's candles or position journal.
MARKET_SOURCE='binance-usdm:BTCUSDC:5m:v1'
BENCHMARK_START_MS=1758475500000
BINANCE_SEED_MS=1758412500000

class BinanceDataAPI:
    """Read-only public USD-M BTCUSDC data. Never submits Binance orders.

    No spot/USDT fallback: a different instrument would change the strategy.
    The broker and its account, quote, fills and native stops stay Hyperliquid.
    """
    base_url='https://fapi.binance.com'
    source=MARKET_SOURCE
    max_candle_window=None
    def __init__(self,log=None,progress=None):
        self.session=requests.Session();self.log=log;self.progress=progress
    def get(self,path,params):
        try:
            r=self.session.get(self.base_url+path,params=params,timeout=(20,20))
            if r.status_code in (403,451):
                raise TemporaryError('Binance data access is restricted here; no source substitution; new entries paused')
            if r.status_code in (418,429):raise TemporaryError('Binance data rate limit; retry later')
            r.raise_for_status();payload=r.json()
            if not isinstance(payload,list):raise ValueError('Expected a list')
            return payload
        except (requests.RequestException,ValueError) as e:
            raise TemporaryError('Binance public data unavailable ('+type(e).__name__+')') from None
    def candles(self,interval,start,end):
        step={'5m':BAR_MS,'12h':43_200_000}[interval]
        rows=[];cursor=start
        while cursor<=end:
            batch=self.get('/fapi/v1/klines',{'symbol':'BTCUSDC','interval':interval,
                      'startTime':cursor,'endTime':end,'limit':1000})
            if not batch:break
            parsed=[dict(t=int(x[0]),T=int(x[6]),o=x[1],h=x[2],l=x[3],c=x[4],v=x[5]) for x in batch]
            if parsed[0]['t']<cursor or any(b['t']<=a['t'] for a,b in zip(parsed,parsed[1:])):
                raise SafetyError('Binance candles did not advance')
            rows.extend(parsed);cursor=parsed[-1]['t']+step
            if self.progress:self.progress()
            if self.log and end-start>1000*step:
                self.log.info('BINANCE HISTORY | %s | %s closed candles downloaded | fixed benchmark seed',interval,len(rows))
        return rows
    def funding(self,start,end):
        rows=[];cursor=start
        while cursor<=end:
            batch=self.get('/fapi/v1/fundingRate',{'symbol':'BTCUSDC','startTime':cursor,'endTime':end,'limit':1000})
            if not batch:break
            parsed=[{'time':int(x['fundingTime']),'fundingRate':x['fundingRate'],
                     'markPrice':x.get('markPrice')} for x in batch]
            if any(x['time']<cursor or x['time']>end for x in parsed):raise SafetyError('Invalid Binance funding time')
            rows.extend(parsed);cursor=max(x['time'] for x in parsed)+1
            if self.progress:self.progress()
            if len(batch)<1000:break
        return rows


def normalize_candle(row,step):
    r={k:float(row[k]) for k in ('o','h','l','c','v')};r.update(t=int(row['t']),T=int(row['T']))
    if (not all(math.isfinite(r[k]) for k in ('o','h','l','c','v')) or r['v']<0 or
        r['t']%step or r['T']-r['t']!=step-1 or not 0<r['l']<=min(r['o'],r['c'])<=max(r['o'],r['c'])<=r['h']):
        raise SafetyError('Invalid candle received from provider')
    return r


class Market:
    def __init__(self,api,store,management,indicators,stop,log,delay_seconds=2):
        self.api=api;self.store=store;self.management=management;self.indicators=indicators;self.stop=stop
        self.log=log;self.delay=int(delay_seconds*1000)
        seed=Path(__file__).with_name('binance_seed.zip')
        if getattr(self.api,'source',None)==MARKET_SOURCE and self.store.last_candle('5m') is None and seed.is_file():
            import zipfile
            with zipfile.ZipFile(seed) as z:
                m=json.loads(z.read('manifest.json'))
                for name in ('BTC_5m.json','BTC_12h.json','BTC_funding.json'):
                    payload=z.read(name)
                    if hashlib.sha256(payload).hexdigest()!=m['files'][name]:raise SafetyError('Binance seed checksum mismatch')
                    rows=json.loads(payload)
                    if name=='BTC_funding.json':self.store.add_funding(rows)
                    else:self.store.add_candles('5m' if name=='BTC_5m.json' else '12h',rows)
    def sync(self,now):
        boundary=(now-self.delay)//BAR_MS*BAR_MS
        manifest=self.store.get('market_manifest')
        if not manifest:
            manifest=({'test_start_ms':BENCHMARK_START_MS,'network':self.api.base_url,
                       'source':MARKET_SOURCE,'seed_5m':BINANCE_SEED_MS}
                      if getattr(self.api,'source',None)==MARKET_SOURCE else
                      {'test_start_ms':boundary,'network':self.api.base_url,'seed_5m':boundary-1200*BAR_MS})
            self.store.put('market_manifest',manifest)
        if manifest['network']!=self.api.base_url:raise SafetyError('History belongs to another network')
        anchor=(manifest['test_start_ms']//43_200_000-400)*43_200_000
        for interval,step,seed in [('5m',BAR_MS,manifest['seed_5m']),('12h',43_200_000,anchor)]:
            last=self.store.last_candle(interval)
            begin=seed if last is None else last+step
            expected_end=boundary//step*step
            while begin<expected_end:
                if getattr(self.api,'max_candle_window',4998) is not None and expected_end-begin>4998*step:
                    raise SafetyError('Disconnection exceeds the Hyperliquid candle window; incomplete history, refusing to recalculate with a different indicator seed')
                # Commit each verified page. Bootstrap resumes here after an outage.
                chunk_end=min(expected_end,begin+1000*step) if getattr(self.api,'source',None)==MARKET_SOURCE else expected_end
                batch=self.api.candles(interval,begin,chunk_end-1)
                rows=[normalize_candle(x,step) for x in batch if int(x['t'])>=begin and int(x['T'])+1<=chunk_end]
                rows=sorted({r['t']:r for r in rows}.values(),key=lambda r:r['t'])
                if not rows or rows[0]['t']!=begin or rows[-1]['T']+1!=chunk_end or any(b['t']-a['t']!=step for a,b in zip(rows,rows[1:])):
                    raise TemporaryError(f'Missing closed {interval} candles; incomplete signals are not evaluated')
                self.store.add_candles(interval,rows);begin=chunk_end
                if getattr(self.api,'source',None)==MARKET_SOURCE and chunk_end<expected_end:
                    self.log.info('BINANCE HISTORY | %s | cached through %s UTC | fixed seed; bootstrap can resume',
                                  interval,datetime.fromtimestamp(chunk_end/1000,timezone.utc).isoformat())
        funding=self.store.funding()
        # A small overlap lets the latest hourly publication be received.
        begin=max(manifest['seed_5m']-9*3600_000,int(funding[-1]['time'])-3600_000) if funding else manifest['seed_5m']-9*3600_000
        if not funding or boundary>int(funding[-1]['time'])+3600_000:
            self.store.add_funding(self.api.funding(begin,now))
        data={'BTC_5m.json':self.store.candles('5m'),'BTC_12h.json':self.store.candles('12h'),
              'BTC_funding.json':self.store.funding()}
        if not data['BTC_funding.json']:raise TemporaryError('Funding unavailable')
        # No entry signal is produced here on purpose: 'signal' and 'risk_atr'
        # come from the paid service, which is the only place that knows them.
        feature=prepare_stop_features(stop_inputs(data,self.indicators),self.stop)
        rows=[]
        for i,b in feature['bars'].iterrows():
            rows.append({'time':int(b['T'])+1,'open_time':int(b['t']),'close':float(b['c']),
                'ema_fast':float(feature['ema_fast'][i]),'ema_slow':float(feature['ema_slow'][i]),
                'atr':float(feature['stop_atr_5m'][i]),'rsi':float(feature['rsi'][i]),
                'funding':float(feature['funding_known_8h'][i]),
                'raw_long':int(feature['stop_mode_long'][i]),'raw_short':int(feature['stop_mode_short'][i]),
                'source':getattr(self.api,'source','hyperliquid')})
        return rows


def entry_scan_text(row,signal=None,reason=None):
    """English, one-line explanation. Private filter results come from the server."""
    details=row.get('diagnostics')
    server=isinstance(details,dict) and details.get('signal_ms')==row['time']
    values=details if server else row
    fast,slow=values.get('ema_fast'),values.get('ema_slow')
    fp,sp=values.get('ema_fast_period',34),values.get('ema_slow_period',89)
    if fast is not None and slow is not None and math.isfinite(fast) and math.isfinite(slow):
        alignment='BULLISH' if fast>slow else 'BEARISH' if fast<slow else 'FLAT'
        ema_text=f'EMA {fp}/{sp} {alignment} ({fast:.2f}/{slow:.2f})'
    else:ema_text=f'EMA {fp}/{sp} warm-up incomplete'
    close=values.get('close',row['close']);rsi=values.get('rsi',row['rsi']);funding=values.get('funding',row['funding'])
    rsi=float('nan') if rsi is None else rsi;funding=float('nan') if funding is None else funding
    parts=['data '+str(values.get('source',row.get('source','unknown'))),('SERVER ' if server else 'LOCAL ')+ema_text,
           f'close {close:.2f}',f'RSI {rsi:.2f}',f'funding/8h {funding*100:+.6f}%']
    if reason:parts.append('BUY/SELL BLOCKED: '+str(reason))
    elif server:
        for side,label,key in ((1,'BUY','buy_blocks'),(-1,'SELL','sell_blocks')):
            blocks=details.get(key)
            if not isinstance(blocks,list) or any(not isinstance(x,str) for x in blocks):
                parts.append(label+': filter details unavailable')
            elif blocks:parts.append(label+' NOT OPENED: '+'; '.join(blocks))
            elif signal==side:parts.append(label+' SIGNAL PASSED; checking execution conditions')
            else:parts.append(label+': server rejected entry; diagnostic mismatch')
    elif signal:
        parts.append(('BUY' if signal==1 else 'SELL')+' SIGNAL PASSED; checking execution conditions')
    else:
        parts.append('BUY/SELL NOT OPENED: server returned no signal; this server version does not provide filter reasons')
    return ' | '.join(parts)


# ========================================================================
# CORE: agente/broker.py
"""Real/paper adapters. Ambiguous orders are looked up, never resubmitted."""


from dataclasses import asdict


import math


def parse_account(raw):
    summary=raw['marginSummary']
    btc=next((r['position'] for r in raw.get('assetPositions',[]) if r['position']['coin']=='BTC'),{})
    leverage=btc.get('leverage',{})
    return Account(equity=float(summary['accountValue']),free_margin=max(0.,float(raw['withdrawable'])),
        qty=float(btc.get('szi',0)),entry=float(btc.get('entryPx') or 0),
        unrealized=float(btc.get('unrealizedPnl',0)),leverage_type=leverage.get('type','isolated'),
        leverage=int(leverage.get('value',5)),time=int(raw.get('time',0)),
        liquidation=float(btc['liquidationPx']) if btc.get('liquidationPx') else None)


def parse_response(raw):
    if not isinstance(raw,dict) or raw.get('status')!='ok':
        return Outcome('rejected',message=str(raw.get('response','Response rejected')) if isinstance(raw,dict) else 'Invalid response')
    statuses=raw.get('response',{}).get('data',{}).get('statuses',[])
    if len(statuses)!=1:return Outcome('unknown',message='Response without order confirmation')
    st=statuses[0]
    if isinstance(st,dict) and 'filled' in st:
        f=st['filled'];return Outcome('filled',oid=int(f['oid']),qty=float(f['totalSz']),price=float(f['avgPx']))
    if isinstance(st,dict) and 'resting' in st:return Outcome('open',oid=int(st['resting']['oid']))
    if isinstance(st,dict) and 'error' in st:return Outcome('rejected',message=str(st['error']))
    return Outcome('unknown',message='Unrecognized order status')


class RealBroker:
    mode='live'
    def __init__(self,api,address,exchange,meta,now,entry_slippage_bps=2.,emergency_slippage_bps=100.):
        self.api=api;self.address=address;self.exchange=exchange;self.now=now
        asset=next(r for r in meta['universe'] if r['name']=='BTC')
        self.decimals=int(asset['szDecimals']);self.max_leverage=int(asset['maxLeverage'])
        self.entry_slippage=entry_slippage_bps/10000
        self.emergency_slippage=emergency_slippage_bps/10000
    def account(self):return parse_account(self.api.account(self.address))
    def quote(self):return self.api.quote()
    def orders(self):return [r for r in self.api.orders(self.address) if r['coin']=='BTC']
    def fills(self,start):return self.api.fills(self.address,start,self.now())
    def configure_isolated(self,leverage):
        if not 1<=leverage<=self.max_leverage:raise Rejected('Leverage not allowed for BTC')
        if abs(self.account().qty)>0:raise Rejected('Cannot change the margin mode of an existing position')
        try:
            self.exchange.set_expires_after(self.now()+30_000)
            r=self.exchange.update_leverage(leverage,'BTC',is_cross=False)
        except Exception as e:raise TemporaryError(f'Isolated margin not confirmed ({type(e).__name__})') from None
        finally:self.exchange.set_expires_after(None)
        if not isinstance(r,dict) or r.get('status')!='ok':raise Rejected('Hyperliquid rejected isolated margin')
    def submit(self,command):
        from hyperliquid.utils.types import Cloid
        c=command;kind=c['kind'];token=Cloid.from_str(c['cloid'])
        try:
            self.exchange.set_expires_after(c['expires'])
            if kind=='entry':
                r=self.exchange.market_open('BTC',c['side']==1,c['qty'],px=c['reference'],
                    slippage=self.entry_slippage,cloid=token)
            elif kind=='stop':
                buy=c['side']==-1
                # Native stop-market. The trigger is mark; the limit price is
                # 10% aggressive, matching the tolerance of market TP/SL orders.
                limit=order_price(c['stop']*(1.1 if buy else .9),buy,self.decimals)
                args=dict(name='BTC',is_buy=buy,sz=c['qty'],limit_px=limit,
                    order_type={'trigger':{'isMarket':True,'triggerPx':c['stop'],'tpsl':'sl'}},
                    reduce_only=True,cloid=token)
                if c.get('old_oid') is None:r=self.exchange.order(**args)
                else:r=self.exchange.modify_order(c['old_oid'],**args)
            elif kind=='close':
                buy=c['side']==-1
                limit=order_price(c['reference']*(1+self.emergency_slippage if buy else 1-self.emergency_slippage),buy,self.decimals)
                r=self.exchange.order('BTC',buy,c['qty'],limit,{'limit':{'tif':'Ioc'}},reduce_only=True,cloid=token)
            else:raise ValueError('Order type not allowed')
            return parse_response(r)
        except Exception as e:
            # Never resubmitted: a transport exception can be hiding a fill.
            return Outcome('unknown',message=f'Confirmation pending ({type(e).__name__})')
        finally:self.exchange.set_expires_after(None)
    def lookup(self,command):
        raw=self.api.order(self.address,command['cloid'])
        if raw.get('status')=='unknownOid':return Outcome('unknown',message='unknownOid')
        if raw.get('status')!='order':return Outcome('unknown',message='Query returned no status')
        obj=raw['order'];r=obj['order'];oid=int(r['oid']);status=obj['status']
        if status=='open':return Outcome('open',oid=oid,details=r)
        fills=[x for x in self.fills(command['created']-1000) if int(x['oid'])==oid]
        qty=sum(float(x['sz']) for x in fills)
        price=sum(float(x['sz'])*float(x['px']) for x in fills)/qty if qty else 0.
        if qty>0:return Outcome('filled',oid=oid,qty=qty,price=price,details=r)
        if status in ('filled','triggered'):
            return Outcome('unknown',oid=oid,message='Fill not yet visible')
        return Outcome('rejected',oid=oid,message=status,details=r)
    def cancel(self,oid):
        try:
            self.exchange.set_expires_after(self.now()+30_000)
            result=self.exchange.cancel('BTC',oid)
            status=result.get('response',{}).get('data',{}).get('statuses',[])
            return result.get('status')=='ok' and (status==['success'] or
                (status and isinstance(status[0],dict) and 'already' in str(status[0].get('error',''))))
        except Exception:return False
        finally:self.exchange.set_expires_after(None)


class PaperBroker:
    mode='paper'
    def __init__(self,api,store,now,capital,decimals=5,fee=.00045,slippage_bps=2.):
        self.api=api;self.store=store;self.now=now;self.decimals=decimals;self.fee=fee;self.slip=slippage_bps/10000
        self.data=store.get('paper',{'cash':capital,'qty':0.,'entry':0.,'margin':0.,'leverage':5,
            'orders':{},'fills':[],'next_oid':1,'last_funding':0})
        self.current={'mid':1.,'mark':1.,'time':0};self._save()
    def _save(self):self.store.put('paper',self.data)
    def configure_isolated(self,leverage):self.data['leverage']=leverage;self._save()
    def quote(self):
        q=self.api.quote();self.observe(q);return q
    def observe(self,quote):
        self.current=quote
        for token,row in list(self.data['orders'].items()):
            c=row['command']
            if row['status']=='open' and c['kind']=='stop' and self.data['qty'] and c['side']*(quote['mark']-c['stop'])<=0:
                self._close_fill(c,abs(self.data['qty']),quote['mid'])
        self._save()
    def account(self):
        d=self.data;upnl=d['qty']*(self.current['mid']-d['entry'])
        return Account(d['cash']+upnl,max(0.,d['cash']-d['margin']),d['qty'],d['entry'],upnl,'isolated',d['leverage'],self.now())
    def orders(self):
        return [{'coin':'BTC','oid':x['oid'],'sz':str(x['command']['qty']),
                 'side':'A' if x['command']['side']==1 else 'B','reduceOnly':True,'isTrigger':True,
                 'triggerPx':str(x['command']['stop']),'cloid':token}
                for token,x in self.data['orders'].items() if x['status']=='open' and x['command']['kind']=='stop']
    def fills(self,start):return [x for x in self.data['fills'] if x['time']>=start]
    def _price(self,p,buy):return order_price(p*(1+self.slip if buy else 1-self.slip),buy,self.decimals)
    def _fill_record(self,row,qty,price,closed,side):
        fee=qty*price*self.fee
        fill={'time':self.now(),'coin':'BTC','oid':row['oid'],'sz':str(qty),'px':str(price),
              'closedPnl':str(closed),'fee':str(fee),'side':'B' if side==1 else 'A','tid':len(self.data['fills'])+1}
        self.data['fills'].append(fill);row.update(status='filled',qty=qty,price=price)
        return fee
    def _close_fill(self,c,qty,reference):
        d=self.data;row=d['orders'][c['cloid']]
        side=1 if d['qty']>0 else -1
        qty=min(qty,abs(d['qty']))
        price=self._price(reference,side==-1);pnl=side*qty*(price-d['entry'])
        before=abs(d['qty']);fee=self._fill_record(row,qty,price,pnl,-side)
        d['cash']+=pnl-fee;d['qty']-=side*qty;d['margin']*=max(0.,1-qty/before) if before else 0.
        if abs(d['qty'])<1e-10:d['qty']=0.;d['entry']=0.;d['margin']=0.
        for other in d['orders'].values():
            if other['status']=='open' and not d['qty']:other['status']='canceled'
    def submit(self,c):
        d=self.data
        if c['cloid'] in d['orders']:return self.lookup(c)
        row={'command':c,'oid':d['next_oid'],'status':'unknown','qty':0.,'price':0.};d['next_oid']+=1
        d['orders'][c['cloid']]=row
        if c['kind']=='entry':
            price=self._price(c['reference'],c['side']==1);qty=c['qty']
            margin=qty*price/d['leverage'];fee=qty*price*self.fee
            if margin+fee>d['cash']-d['margin'] or (d['qty'] and d['qty']*c['side']<0):row['status']='rejected'
            else:
                before=abs(d['qty']);d['entry']=(before*d['entry']+qty*price)/(before+qty)
                d['qty']+=c['side']*qty;d['margin']+=margin
                d['cash']-=self._fill_record(row,qty,price,0.,c['side'])
        elif c['kind']=='stop':
            if not d['qty']:row['status']='rejected'
            else:
                for old in d['orders'].values():
                    if old['oid']==c.get('old_oid') and old['status']=='open':old['status']='canceled'
                row['status']='open'
        elif c['kind']=='close':
            if not d['qty'] or c['side']*d['qty']<=0:row['status']='rejected'
            else:self._close_fill(c,c['qty'],c['reference'])
        self._save();return self.lookup(c)
    def lookup(self,c):
        row=self.data['orders'].get(c['cloid'])
        if not row:return Outcome('unknown',message='unknownOid')
        status=row['status'];status='rejected' if status=='canceled' else status
        return Outcome(status,row['oid'],row['qty'],row['price'])
    def cancel(self,oid):
        for row in self.data['orders'].values():
            if row['oid']==oid and row['status']=='open':row['status']='canceled'
        self._save();return True


# ========================================================================
# CORE: agente/engine.py
"""Agent cycle: one BTC position, a journal entry before every order, and a native stop."""


from dataclasses import asdict


from datetime import datetime, timezone


import math


NAMES={0:'adverse',1:'weakening',2:'favorable'}


class Agent:
    def __init__(self,broker,store,management,stop,settings,log,now):
        self.broker=broker;self.store=store;self.mgmt=management;self.stop_cfg=stop
        self.settings=settings;self.log=log;self.now=now;self.state=store.get('agent',new_state())
        self.quote=None;self.account=None
        if self.state['schema']!=1:raise SafetyError('Unsupported state version')
        key=identity({'network':settings['network'],'account':settings['account_address'].lower(),
                      'mode':broker.mode,'coin':'BTC'})
        previous=store.get('account_identity')
        if previous and previous!=key:raise SafetyError('This state belongs to another account/network/mode')
        store.put('account_identity',key)
        fingerprint=identity({'management':management.to_dict(),'stop':stop.to_dict(),
                              'pct':settings['capital_per_entry_pct'],'entry':settings['stop_indicators']})
        if self.state['config_hash'] not in (None,fingerprint) and (self.state['position'] or self.state['pending']):
            raise SafetyError('Configuration changed with a pending position/order. Restore the configuration used to open it.')
        self.state['config_hash']=fingerprint;self.state.setdefault('cleanup',[]);self.save()

    def save(self):self.store.put('agent',self.state)
    def event(self,message,*args):self.log.info(message,*args)
    def active(self):return bool(self.state['position'] or self.state['pending'] or self.state['cleanup'])
    def block(self,reason):
        if self.state['blocked']!=reason:self.log.error(reason)
        self.state['blocked']=reason;self.save()

    def make_command(self,kind,**values):
        self.state['sequence']+=1
        c={'kind':kind,'cloid':cloid(self.state['instance'],kind,self.state['sequence']),
           'created':self.now(),'expires':self.now()+30_000,**values}
        self.state['pending'][kind]=c
        self.save()  # fsync SQLite ANTES de cualquier peticion que modifique el exchange.
        return c

    def send(self,c):
        result=self.broker.submit(c)
        self.apply(c,result)
        return result

    def apply(self,c,result):
        kind=c['kind'];p=self.state['position']
        if result.status=='unknown':
            self.log.warning('ORDER PENDING | %s | checking by identifier without resubmitting: %s',kind,result.message)
            return
        if kind=='entry':
            if result.status=='open':
                # An IOC should never rest open. Cancel only our own order.
                self.broker.cancel(result.oid)
                self.log.warning('IOC unexpectedly open; cancelling and reconciling before continuing')
                return
            if result.status=='filled' and result.qty>0 and result.price>0:
                if result.qty>c['qty']+10**(-self.broker.decimals):
                    self.block('Filled quantity exceeds requested quantity; review the account');return
                if p is None:
                    if c['is_add']:
                        self.block('Addition confirmed without a local position; reconciliation required');return
                    price=result.price
                    stop=stop_price(price-c['side']*c['distance'],c['side'])
                    trail=Trailing(c['side'],price,stop,price)
                    p={'side':c['side'],'qty':result.qty,'first':price,'opened':c['created'],
                       'entry_legs':1,'next_add':1,'trail':asdict(trail),'stop_oid':None,
                       'protected_stop':None,'protected_qty':0.,'additions_disabled':False,
                       'emergency':False,'last_fill':self.now(),'entry_oids':[result.oid]}
                    self.state['position']=p
                    self.event('OPEN | %s BTC/USDC | %.5f BTC | price %.2f | equity %.2f USDC | %.2f%% notional | initial stop %.2f',
                        'BUY' if p['side']==1 else 'SELL',result.qty,price,c['equity'],self.settings['capital_per_entry_pct'],stop)
                else:
                    if p['side']!=c['side']:self.block('Fill direction mismatch');return
                    p['qty']+=result.qty;p['entry_legs']+=1;p['last_fill']=self.now();p['entry_oids'].append(result.oid)
                    self.event('ADD +%g%% | %.5f BTC | price %.2f | entries %s/%s | shared stop %.2f',
                        c['level']*self.mgmt.add_step_pct,result.qty,result.price,p['entry_legs'],self.mgmt.max_entries or 'unlimited',p['trail']['stop'])
                if result.qty+10**(-self.broker.decimals)<c['qty']:
                    self.log.warning('PARTIAL FILL | requested %.5f, filled %.5f BTC; remainder will not be resubmitted',c['qty'],result.qty)
            else:self.log.warning('ENTRY REJECTED | %s',result.message)
        elif kind=='stop':
            if result.status=='open':
                if p:
                    old=p['protected_stop'];p['stop_oid']=result.oid;p['protected_stop']=c['stop'];p['protected_qty']=c['qty']
                    if old is None:self.event('STOP CONFIRMED %s | %.2f | quantity %.5f BTC',
                        'ON HYPERLIQUID' if self.broker.mode=='live' else 'SIMULATED (PAPER)',c['stop'],c['qty'])
                    elif old!=c['stop']:
                        self.event('STOP %s | %.2f -> %.2f | indicators %s | quantity %.5f BTC',
                            'RAISED' if p['side']==1 else 'LOWERED',old,c['stop'],NAMES[p['trail']['mode']],c['qty'])
                    else:self.event('STOP UPDATED | same price %.2f | covers %.5f BTC',c['stop'],c['qty'])
                else:
                    self.state['cleanup'].append(result.oid)
            elif result.status=='filled':
                if p:p['emergency']=True
                self.event('STOP FILLED | checking remaining position')
            else:
                self.log.error('STOP REJECTED | %s',result.message)
                if p and (p['stop_oid'] is None or p['protected_qty']+1e-10<p['qty']):p['emergency']=True
        elif kind=='close':
            if result.status=='filled':
                self.event('CLOSE FILLED | %.5f BTC | price %.2f | reason %s; checking remaining balance',
                           result.qty,result.price,c['reason'])
            else:self.log.error('CLOSE UNCONFIRMED | %s; checking the position before another attempt',result.message)
        self.state['pending'].pop(kind,None);self.save()

    def recover_pending(self):
        for kind,c in list(self.state['pending'].items()):
            result=self.broker.lookup(c)
            if result.status=='unknown':
                # expiresAfter prevents a request that was never registered from
                # appearing later. The balance is also confirmed before discarding it.
                if result.message=='unknownOid' and self.now()>c['expires']+15_000:
                    a=self.broker.account();p=self.state['position'];expected=p['side']*p['qty'] if p else 0.
                    if abs(a.qty-expected)<=10**(-self.broker.decimals)/2:
                        self.apply(c,Outcome('rejected',message='Not registered after expiry; will not be resubmitted'))
                continue
            self.apply(c,result)

    def reconcile(self):
        a=self.broker.account();self.account=a;p=self.state['position']
        tol=10**(-self.broker.decimals)/2
        if 'entry' in self.state['pending']:return
        if p is None:
            if abs(a.qty)>tol:
                self.block('External BTC position without agent state: no modifications or new trades')
            elif self.state['blocked'] and self.state['blocked'].startswith(('External BTC position','Posicion BTC externa')):
                self.state['blocked']=None;self.save();self.event('BTC account clear; resuming scanning')
            return
        if abs(a.qty)<=tol:
            # An Info read can briefly lag behind reflecting a confirmed fill.
            if self.now()-p['last_fill']<5000:return
            fills=self.broker.fills(p['opened']-1000)
            exit_fills=[f for f in fills if f.get('side')==('A' if p['side']==1 else 'B')]
            closed_time=max([int(f['time']) for f in exit_fills],default=self.now())
            price=float(exit_fills[-1]['px']) if exit_fills else self.quote['mid']
            when=datetime.fromtimestamp(closed_time/1000,timezone.utc).isoformat()
            realized=sum(float(f.get('closedPnl',0)) for f in exit_fills)
            self.event('CLOSE CONFIRMED | exchange time UTC %s | last price %.2f | realized PnL %.2f USDC before funding/fees | resuming scanning',when,price,realized)
            hook=getattr(self,'after_close',None)
            if hook:hook(p,closed_time,price,realized)
            if p['stop_oid'] is not None:self.state['cleanup'].append(p['stop_oid'])
            self.state['position']=None;self.state['closed_at']=closed_time;self.state['blocked']=None;self.save();return
        if p['side']*a.qty<=0:
            self.block('BTC direction changed externally; entries paused, new position not adopted');return
        if abs(a.qty)>p['qty']+tol:
            self.block('BTC size increased externally; existing stop retained and new orders paused');return
        if abs(a.qty)<p['qty']-tol:
            self.event('PARTIAL REDUCTION detected | %.5f -> %.5f BTC; stop coverage will be adjusted',p['qty'],abs(a.qty))
            p['qty']=abs(a.qty);p['additions_disabled']=True;self.save()
        if a.leverage_type!='isolated':
            p['additions_disabled']=True;self.log.error('BTC margin changed to cross; additions blocked');self.save()

    def cleanup(self):
        if not self.state['cleanup']:return
        opened={int(x['oid']) for x in self.broker.orders()}
        remaining=[]
        for oid in set(self.state['cleanup']):
            if oid in opened and not self.broker.cancel(oid):remaining.append(oid)
        self.state['cleanup']=remaining;self.save()

    def emergency_close(self,reason):
        p=self.state['position']
        if not p or 'close' in self.state['pending']:return
        p['emergency']=True;self.save()
        c=self.make_command('close',side=p['side'],qty=p['qty'],reference=self.quote['mid'],reason=reason)
        self.log.error('PROTECTIVE CLOSE | %s',reason);self.send(c)

    def ensure_stop(self,verify=False):
        p=self.state['position']
        if not p or self.state['blocked'] or 'entry' in self.state['pending']:return
        if 'close' in self.state['pending']:return
        if p['emergency']:
            self.emergency_close('Full protection could not be confirmed');return
        if self.account and self.account.liquidation:
            if p['side']*(p['trail']['stop']-self.account.liquidation)<=0:
                self.emergency_close('Stop would be beyond the liquidation price');return
        if p['side']*(self.quote['mark']-p['trail']['stop'])<=0:
            self.emergency_close('Mark price has already reached the calculated stop');return
        if 'stop' in self.state['pending']:
            if p['protected_qty']+1e-10<p['qty']:
                self.emergency_close('Ambiguous stop response without full coverage')
            return
        if verify and p['stop_oid'] is not None:
            orders={int(x['oid']):x for x in self.broker.orders()}
            actual=orders.get(p['stop_oid'])
            if actual is None:
                # It may have triggered partially: a reduce-only exit is kept
                # if contracts remain; another position is never opened.
                p['stop_oid']=None;p['protected_qty']=0.;p['protected_stop']=None;self.save()
            elif not actual.get('reduceOnly') or not actual.get('isTrigger'):
                self.block('Stop order was modified externally');return
            elif (float(actual.get('triggerPx',0))!=p['protected_stop'] or
                  actual.get('side')!=('A' if p['side']==1 else 'B')):
                self.block('Stop price or direction was modified externally');return
            else:
                # Coverage is verified against the exchange, not just against
                # the quantity we stored when the order was sent.
                actual_qty=float(actual.get('sz',0))
                if abs(actual_qty-p['protected_qty'])>10**(-self.broker.decimals)/2:
                    self.log.warning('Stop coverage changed; adjusting to actual position')
                    p['protected_qty']=actual_qty;self.save()
        if (p['protected_stop']==p['trail']['stop'] and
            abs(p['protected_qty']-p['qty'])<10**(-self.broker.decimals)/2):return
        c=self.make_command('stop',side=p['side'],qty=p['qty'],stop=p['trail']['stop'],old_oid=p['stop_oid'])
        self.send(c)
        if p.get('emergency') or ('stop' in self.state['pending'] and p['protected_qty']+1e-10<p['qty']):
            self.emergency_close('Stop coverage for the entire position is not confirmed')

    def open_entry(self,row,is_add=False,level=0,fixed_qty=None,fixed_equity=None):
        if self.state['blocked'] or self.state['pending'] or self.state['cleanup']:return False
        p=self.state['position'];a=self.broker.account()
        if not is_add:
            if a.qty or self.broker.orders():
                self.log.warning('Entry skipped: BTC has a position or orders outside this agent');return False
            self.broker.configure_isolated(int(self.mgmt.leverage));side=int(row['signal'])
        else:
            if not p or p['additions_disabled'] or p['emergency'] or not self.mgmt.entry_capacity(p['entry_legs']):return False
            if abs(a.qty-p['side']*p['qty'])>10**(-self.broker.decimals)/2:
                self.log.warning('Addition skipped: actual size must be reconciled');return False
            side=p['side']
        equity=a.equity if fixed_equity is None else fixed_equity
        try:
            qty=size_for_equity(equity,self.settings['capital_per_entry_pct'],row['close'],
                 self.broker.decimals,a.free_margin,self.mgmt.leverage,self.mgmt.fee_rate)
            if fixed_qty is not None:qty=fixed_qty
            # Re-check margin using the execution limit, not just the close.
            worst=self.quote['mid']*(1+self.settings['entry_slippage_bps']/10000)
            if qty*worst*(1/self.mgmt.leverage+self.mgmt.fee_rate)>a.free_margin:
                raise Rejected('Insufficient margin at execution price')
            distance=max(1.,math.floor(self.mgmt.stop_atr*row['risk_atr']+.5)) if not is_add else 0.
            if not is_add and distance>=self.quote['mid']*.9/self.mgmt.leverage:
                raise Rejected('Initial stop is too distant for the configured isolated margin')
            if abs(self.quote['mid']/row['close']-1)*100>self.settings['max_desviacion_entrada_pct']:
                raise Rejected('Price has moved too far from the signal close')
        except (Rejected,ValueError) as e:
            self.log.warning('ENTRY SKIPPED | %s',e);return False
        hook=getattr(self,'before_entry',None)
        if hook and not hook(row,side,qty,distance,is_add,level):return False
        c=self.make_command('entry',side=side,qty=qty,reference=self.quote['mid'],equity=equity,
            distance=distance,is_add=is_add,level=level,signal_time=row['time'])
        self.send(c)
        if self.state['position']:self.ensure_stop()
        return self.state['position'] is not None and not self.state['pending']

    def poll(self):
        self.quote=self.broker.quote()
        self.recover_pending();self.reconcile();self.cleanup()
        self.ensure_stop(verify=True)
        p=self.state['position']
        if p:
            profit=p['side']*(self.quote['mid']/p['first']-1)*100
            self.event('POSITION %s | %.5f BTC | price %.2f | move %+.2f%% | confirmed stop %s | trailing %s | indicators %s',
                'BUY' if p['side']==1 else 'SELL',p['qty'],self.quote['mid'],profit,
                f'{p["protected_stop"]:.2f}' if p['protected_stop'] is not None else 'PENDING',
                'active' if p['trail']['active'] else f'waiting for +{self.stop_cfg.activate_profit_pct:g}%',NAMES[p['trail']['mode']])

    def process_rows(self,rows):
        if not rows:return
        if not self.state['last_bar']:
            self.state['last_bar']=rows[-1]['time'];self.save()
            self.event('%s | history initialized; waiting for the next 5-minute close',
                       'SCANNING PAUSED' if self.state['blocked'] else
                       'POSITION RECOVERED' if self.state['position'] else 'NO OPEN POSITIONS')
            if not self.state['position']:
                self.event('NO OPEN POSITIONS | %s',entry_scan_text(rows[-1],reason='startup baseline recorded; waiting for the next closed 5m candle'))
            return
        new=[r for r in rows if r['time']>self.state['last_bar']]
        if not new:return
        if len(new)>1:self.event('RECOVERY | %s closed candles; only the latest may generate an entry',len(new))
        for row in new:
            p=self.state['position']
            if p and row['time']>p['opened']:
                trail=Trailing(**p['trail']).advance(row['close'],row['atr'],row['raw_long'] if p['side']==1 else row['raw_short'],self.stop_cfg)
                p['trail']=asdict(trail)
            self.state['last_bar']=row['time'];self.save()
        last=new[-1]
        self.ensure_stop()
        if self.state['blocked'] or self.state['pending']:
            if not self.state['position']:
                reason=self.state['blocked'] or ('order reconciliation pending: '+', '.join(self.state['pending']))
                self.event('NO OPEN POSITIONS | %s',entry_scan_text(last,reason=reason))
            return
        fresh=0<=self.now()-last['time']<=self.settings['max_edad_senal_segundos']*1000
        if not fresh:
            reason=f'closed candle age {(self.now()-last["time"])/1000:.1f}s is outside 0-{self.settings["max_edad_senal_segundos"]}s; historical entries are not replayed'
            self.event('STALE CANDLE | %s | protection reconciled',entry_scan_text(last,reason=reason));return
        p=self.state['position']
        if p:
            if p['emergency'] or p['additions_disabled']:return
            favorable=p['side']*(last['close']/p['first']-1)*100
            reached=math.floor(favorable/self.mgmt.add_step_pct+1e-8)
            if reached>=p['next_add']:
                # Additions are entries too: they need an authorized service answer.
                # Without one the position simply stops growing; its stop keeps working.
                if self.entry_decision(last) is None:return
                level=p['next_add'];count=self.mgmt.entry_capacity(p['entry_legs'],reached-level+1)
                p['next_add']=reached+1;self.save()
                eq=self.broker.account().equity;fixed=None
                for n in range(count):
                    before=self.state['position']['qty']
                    if not self.open_entry(last,True,level+n,fixed,eq):break
                    fixed=self.state['position']['qty']-before
        else:
            if last['time']<=self.state['closed_at']:return
            decision=self.entry_decision(last)
            if decision is None:
                self.event('NO OPEN POSITIONS | %s',entry_scan_text(last,reason=last.get('entry_block','signal service unavailable')))
                return
            signal,risk_atr=decision
            reason='initial-stop ATR unavailable' if signal and not math.isfinite(risk_atr) else None
            self.event('NO OPEN POSITIONS | BTC/USDC 5m | %s',entry_scan_text(last,signal,reason))
            if signal and math.isfinite(risk_atr):
                self.open_entry(dict(last,signal=signal,risk_atr=risk_atr))

    def entry_decision(self,row):
        """Ask the paid service what to do with this candle. The agent has no
        local fallback by design: without a paid, authorized answer it does not
        open positions. Any position already open keeps its local protection."""
        hook=getattr(self,'ask_service',None)
        if hook is None:
            row['entry_block']='no signal service configured'
            self.log.warning('ENTRY SKIPPED | no signal service configured');return None
        try:
            return hook(row)
        except Rejected as e:
            row['entry_block']='service rejected request: '+str(e)
            self.event('ENTRY PAUSED BY SERVICE | %s | existing protection remains active',e);return None
        except TemporaryError as e:
            row['entry_block']='signal service unavailable: '+str(e)
            self.event('SIGNAL SERVICE UNAVAILABLE | %s | no entry this candle; protection unaffected',e);return None


# ========================================================================
# CONFIGURATION.PY
import configparser
import base64
import secrets
import time
import argparse
import getpass
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parent
VERSION='2.2.0'
BUILD_ID='2.2.0-binance-restored.1'
# Public publisher policy is embedded in the release, never taken from customer config.
# The publisher must insert their OWN public addresses before distribution.
# This is a configuration safeguard, not protection against modifying source code.
# BEGIN QTS PUBLISHER POLICY
PUBLISHER_POLICY = {"service_url":"https://qts-server-938o.onrender.com","builder_address":"0xf4efa481cf5cbb2f93e67f1d97e50508ef99af65","algorand_recipient":"SGLTUPAC7TKGKNNXKNPQ2QZCC7NJSLAKYZ7O7NOGGAPXWBFZTOLTPMSPPI","builder_fee_bps":"10","service_price_usdc":"0.01","fee_payer_mainnet":"ZMFK2OI7ZBD2U27ISERZC4S6LKM6WMFJPZQ4MYNJDZ2VNBNMBA67RA22AA","fee_payer_testnet":"ZMFK2OI7ZBD2U27ISERZC4S6LKM6WMFJPZQ4MYNJDZ2VNBNMBA67RA22AA"}
# END QTS PUBLISHER POLICY
# Execution and sizing only. Every entry-strategy parameter (context/sweep
# settings, EMA/ATR/RSI thresholds, funding cap) lives in the publisher's
# private estrategia.py and is never shipped to a customer.
DEFAULT_MANAGEMENT={"stop_atr":2,"add_step_pct":2,"max_entries":3,"equity_pct":100,"leverage":5,"fee_rate":0.00045,"slippage_bps":2,"capital":10000}
# Indicator PERIODS the local stop layer needs. Thresholds are not here.
DEFAULT_STOP_INDICATORS={"fast":34,"slow":89,"atr_period":7,"rsi_period":14}
DEFAULT_STOP={"atr_period":14,"wide_atr":32,"wide_min_pct":0.25,"weak_ratio":0.5,"adverse_ratio":0.25,"activate_profit_pct":5,"confirm_bars":12,"rsi_favorable":55,"rsi_drop_points":3,"momentum_bars":3,"atr_expansion_min":0.9,"tighten_before_activation":False}

def atomic_text(path,text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp-'+secrets.token_hex(4))
    with temp.open('w',encoding='utf-8',newline='\n') as out:
        os.chmod(temp,0o600);out.write(text);out.flush();os.fsync(out.fileno())
    temp.replace(path)

def trusted_url(value,local=False):
    parsed=urlparse(value)
    if parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise SafetyError('URL must not contain credentials, query parameters or a fragment')
    if not parsed.hostname or (parsed.scheme!='https' and not(local and parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1'))):
        raise SafetyError('Service URL requires HTTPS (HTTP only on localhost for testing)')
    return value.rstrip('/')

class Settings:
    def __init__(self,path=ROOT/'config.txt'):
        self.path=Path(path).resolve();self.root=self.path.parent
        self.ini=configparser.ConfigParser(interpolation=None)
        if not self.ini.read(self.path,encoding='utf-8-sig'):raise SafetyError('Missing config.txt next to the agent')
        for section in ('account','operations','billing','setup'):
            if not self.ini.has_section(section):self.ini.add_section(section)
        for section in self.ini.sections():
            for key in self.ini[section]:
                if any(x in key.lower() for x in ('semilla','private','secret','password','mnemonic')):
                    raise SafetyError('config.txt only accepts public settings. Keys are stored in the system credential vault.')
        self.network=self.get('account','network')
        self.mode=self.get('account','mode')
        if self.network not in ('mainnet','testnet') or self.mode not in ('paper','live'):raise SafetyError('Invalid network or mode')
        self.address=self.get('account','wallet_hyperliquid')
        self.payer=self.get('account','wallet_algorand_payments')
        self.endpoint=PUBLISHER_POLICY['service_url'].rstrip('/')
        self.builder=PUBLISHER_POLICY['builder_address'].lower()
        self.pay_to=PUBLISHER_POLICY['algorand_recipient']
        self.fee_payer=PUBLISHER_POLICY['fee_payer_'+self.network]
        self.fee_bps=Decimal(PUBLISHER_POLICY['builder_fee_bps'])
        for key,expected in {'service_url':self.endpoint,'wallet_hyperliquid_commission':self.builder,
                            'wallet_algorand_payout':self.pay_to,'hyperliquid_commission_bps':str(self.fee_bps),
                            'service_price_usdc':PUBLISHER_POLICY['service_price_usdc']}.items():
            old=self.ini.get('billing',key,fallback='').strip()
            if old and old!=expected:raise SafetyError('Publisher settings are embedded in this release. config.txt cannot override '+key)
        if not self.fee_bps.is_finite() or not Decimal('.1')<=self.fee_bps<=10 or self.fee_bps*10!=(self.fee_bps*10).to_integral_value():raise SafetyError('Fee: 0.1 to 10 bps, in 0.1 bps increments')
        self.builder_units=int(self.fee_bps*10)
        amount=Decimal(PUBLISHER_POLICY['service_price_usdc'])*1_000_000
        if not amount.is_finite() or amount!=amount.to_integral_value():raise SafetyError('Invalid embedded service price')
        self.price=int(amount);self.daily=self.micro('daily_limit_usdc');self.total=self.micro('total_limit_usdc')
        if not 1<=self.price<=10_000_000 or not self.price<=self.daily<=self.total:raise SafetyError('Invalid price or payment limits')
        self.algod_url=trusted_url(self.get('billing','algod_url'))
        from x402.mechanisms.avm.constants import ALGORAND_MAINNET_CAIP2,ALGORAND_TESTNET_CAIP2,USDC_MAINNET_ASA_ID,USDC_TESTNET_ASA_ID
        self.chain=ALGORAND_MAINNET_CAIP2 if self.network=='mainnet' else ALGORAND_TESTNET_CAIP2
        self.asset=str(USDC_MAINNET_ASA_ID if self.network=='mainnet' else USDC_TESTNET_ASA_ID)
        self.settings={'mode':self.mode,'network':self.network,'account_address':self.address,
            'capital_per_entry_pct':float(self.get('operations','capital_per_entry_pct')),
            'revision_posicion_segundos':15,'retardo_cierre_segundos':2,'max_edad_senal_segundos':60,
            'max_desviacion_entrada_pct':0.3,'entry_slippage_bps':2.,'emergency_slippage_bps':100.,'paper_capital_inicial':10000.,
            'stop_indicators':dict(DEFAULT_STOP_INDICATORS)}
        pct=self.settings['capital_per_entry_pct']
        if not math.isfinite(pct) or not 0<pct<=100:raise SafetyError('Capital per entry: greater than zero and up to 100% notional')
        management=dict(DEFAULT_MANAGEMENT)
        management['max_entries']=int(self.get('operations','max_entries'))
        if management['max_entries']!=3:raise SafetyError('This distribution retains the validated maximum of 3 entries')
        management['equity_pct']=pct
        # Reserves margin for the eventual closing commission.
        management['fee_rate']+=float(self.fee_bps)/10000
        self.management=Config(**management);self.indicators=StopIndicators(**DEFAULT_STOP_INDICATORS);self.stop=StopConfig(**DEFAULT_STOP)
        self.management.validate();self.indicators.validate();self.stop.validate()

    def get(self,section,key):
        defaults={('account','network'):'mainnet',('account','mode'):'paper',
            ('account','wallet_hyperliquid'):'',('account','wallet_algorand_payments'):'',
            ('operations','capital_per_entry_pct'):'100',('operations','max_entries'):'3',
            ('billing','daily_limit_usdc'):'0.50',('billing','total_limit_usdc'):'10.00',
            ('billing','algod_url'):'https://'+('testnet' if self.ini.get('account','network',fallback='mainnet').strip()=='testnet' else 'mainnet')+'-api.algonode.cloud',
            ('setup','completed'):'no',('setup','acceptance_sha256'):'',
            ('server','facilitator_url'):'https://facilitator.goplausible.xyz',
            ('server','host'):'127.0.0.1',('server','port'):'8000'}
        value=self.ini.get(section,key,fallback=defaults.get((section,key)))
        if value is None:raise SafetyError(f'Missing [{section}] {key} in config.txt')
        return value.strip()

    def micro(self,key):
        value=Decimal(self.get('billing',key))*1_000_000
        if not value.is_finite() or value!=value.to_integral_value():raise SafetyError(f'{key}: maximum 6 decimal places')
        return int(value)

    def save(self):
        import io
        out=io.StringIO();self.ini.write(out);atomic_text(self.path,out.getvalue())

    def terms(self):
        return {'project':'QUANT TRADING SIGNALS','version':VERSION,'network':self.chain,'asset':self.asset,
            'pay_to':self.pay_to,'amount':str(self.price),'builder':self.builder,'builder_units':self.builder_units,
            'builder_scope':'exits_only','resources':['activation','exit_receipt'],
            'network_fees':'sponsored','fee_payer':self.fee_payer}

    def consent_hash(self):
        return identity({'terms':self.terms(),'url':self.endpoint,'account':self.address.lower(),'payer':self.payer,
                         'daily':self.daily,'total':self.total,'algod':self.algod_url})

    def merchant_ready(self):
        from algosdk.encoding import is_valid_address
        trusted_url(self.endpoint,local=self.network=='testnet')
        if not re.fullmatch(r'0x[0-9a-fA-F]{40}',self.builder) or int(self.builder,16)==0:raise SafetyError('Publisher must configure wallet_hyperliquid_commission before distribution')
        if not is_valid_address(self.pay_to):raise SafetyError('Publisher must configure wallet_algorand_payout before distribution')
        if not is_valid_address(self.fee_payer):raise SafetyError('Publisher must embed the verified facilitator fee payer for this network')

    def live_ready(self):
        from algosdk.encoding import is_valid_address
        self.merchant_ready()
        if not re.fullmatch(r'0x[0-9a-fA-F]{40}',self.address) or int(self.address,16)==0:raise SafetyError('Missing Hyperliquid public address')
        if not is_valid_address(self.payer):raise SafetyError('Missing separate Algorand payment wallet')
        if self.payer==self.fee_payer:raise SafetyError('Payment wallet must be separate from the network fee sponsor')
        if self.get('setup','completed')!='si' or self.get('setup','acceptance_sha256')!=self.consent_hash():
            raise SafetyError('Setup incomplete or terms changed: run INSTALAR again')

def protected_vault():
    if sys.platform=='win32':
        from keyring.backends.Windows import WinVaultKeyring
        vault=WinVaultKeyring()
    elif sys.platform=='darwin':
        from keyring.backends.macOS import Keyring
        vault=Keyring()
    else:
        from keyring.backends.SecretService import Keyring
        vault=Keyring()
    if vault.priority<=0:raise SafetyError('Unlock the system credential vault; plaintext storage will not be used')
    return vault

def secret_service(kind,network):return 'QUANT_TRADING_SIGNALS/'+kind+'/'+network

def validate_hl_key(key,address):
    from eth_account import Account as EthAccount
    if not re.fullmatch(r'(0x)?[0-9a-fA-F]{64}',key or ''):raise SafetyError('Empty or invalid Hyperliquid API key. Do not enter a seed phrase.')
    try:signer=EthAccount.from_key(key)
    except Exception:raise SafetyError('Invalid API key') from None
    if signer.address.lower()==address.lower():raise SafetyError('This is the main wallet key. Only a separate API wallet is accepted.')
    return key

def validate_algo_key(key):
    from algosdk import account
    try:
        raw=base64.b64decode(key,validate=True)
        if len(raw)!=64:raise ValueError()
        from nacl.signing import SigningKey
        if bytes(SigningKey(raw[:32]).verify_key)!=raw[32:]:raise ValueError()
        address=account.address_from_private_key(key)
    except Exception:raise SafetyError('Invalid Algorand key: use a 64-byte Base64 key from a secondary payment wallet') from None
    return address

def load_secrets(cfg,vault=None):
    vault=vault or protected_vault()
    try:
        hl=vault.get_password(secret_service('Hyperliquid',cfg.network),cfg.address.lower())
        algo=vault.get_password(secret_service('Algorand',cfg.network),cfg.payer)
    except Exception:raise SafetyError('Cannot open the protected vault: unlock it and retry') from None
    validate_hl_key(hl,cfg.address)
    if validate_algo_key(algo)!=cfg.payer:raise SafetyError('Payment key does not match the configured wallet')
    return hl,algo


# ========================================================================
# BILLING.PY
def qts_b64(obj):
    if hasattr(obj,'model_dump'):obj=obj.model_dump(by_alias=True,exclude_none=True)
    return base64.b64encode(canonical(obj).encode()).decode()

def qts_unb64(value):
    if not value or len(value)>64000:raise SafetyError('Invalid payment header')
    return json.loads(base64.b64decode(value,validate=True))

def algo_client(cfg):
    from algosdk.v2client.algod import AlgodClient
    class BoundedAlgod(AlgodClient):
        def algod_request(self,*args,**kwargs):
            kwargs.setdefault('timeout',8)
            return super().algod_request(*args,**kwargs)
    return BoundedAlgod('',cfg.algod_url)

def payment_transaction(payload):
    from algosdk import encoding
    obj=payload.payload if hasattr(payload,'payload') else payload['payload']
    group=obj['paymentGroup'];index=int(obj['paymentIndex'])
    if not 1<=len(group)<=2 or not 0<=index<len(group):raise SafetyError('Invalid payment group')
    decoded=encoding.msgpack_decode(group[index])
    txn=getattr(decoded,'transaction',decoded)
    return txn

def check_payment_tx(txn,cfg,payer):
    expected=cfg.chain.split(':',1)[1]
    if (txn.type!='axfer' or txn.sender!=payer or txn.receiver!=cfg.pay_to or
        txn.amount!=cfg.price or str(txn.index)!=cfg.asset or
        txn.rekey_to or txn.close_assets_to or txn.revocation_target or
        txn.genesis_hash!=expected or txn.fee!=0 or
        not 0<txn.last_valid_round-txn.first_valid_round<=1000):
        raise SafetyError('Payment does not match the authorized recipient, amount, network and permissions')

def check_sponsored_group(txns,cfg,payer):
    from algosdk import transaction
    if len(txns)!=2 or not cfg.fee_payer or payer==cfg.fee_payer:
        raise SafetyError('A separate, pinned network fee sponsor is required')
    sponsor,payment=txns
    check_payment_tx(payment,cfg,payer)
    if (sponsor.type!='pay' or sponsor.sender!=cfg.fee_payer or sponsor.receiver!=cfg.fee_payer or
        sponsor.amt!=0 or sponsor.close_remainder_to or sponsor.rekey_to or
        not 2000<=sponsor.fee<=4000 or sponsor.genesis_hash!=payment.genesis_hash or
        sponsor.first_valid_round!=payment.first_valid_round or sponsor.last_valid_round!=payment.last_valid_round or
        sponsor.lease or payment.lease):
        raise SafetyError('Invalid sponsored payment group')
    if not payment.group or sponsor.group!=payment.group:raise SafetyError('Missing or mismatched atomic group')
    group_id=payment.group
    # Calculate on independent copies so validating never changes a signed transaction.
    from copy import deepcopy
    unsigned=deepcopy(txns)
    for txn in unsigned:txn.group=None
    if transaction.calculate_group_id(unsigned)!=group_id:raise SafetyError('Atomic group hash mismatch')

def validate_payment_payload(payload,cfg,payer):
    from algosdk import encoding,transaction
    obj=payload.payload if hasattr(payload,'payload') else payload['payload']
    if obj.get('paymentIndex')!=1 or len(obj.get('paymentGroup',[]))!=2:
        raise SafetyError('Expected one sponsor transaction and one USDC payment')
    decoded=[encoding.msgpack_decode(x) for x in obj['paymentGroup']]
    if not isinstance(decoded[0],transaction.PaymentTxn) or not isinstance(decoded[1],transaction.SignedTransaction):
        raise SafetyError('Only the USDC payment may be signed by the customer')
    if decoded[1].authorizing_address:raise SafetyError('Rekeyed payment accounts are not supported')
    txns=[decoded[0],decoded[1].transaction]
    check_sponsored_group(txns,cfg,payer)
    return txns

class GuardedAlgoSigner:
    def __init__(self,key,cfg):self.key=key;self.cfg=cfg;self.address=validate_algo_key(key)
    def sign_transactions(self,unsigned_txns,indexes_to_sign):
        from algosdk import encoding
        if len(unsigned_txns)!=2 or indexes_to_sign!=[1]:
            raise SafetyError('Only the USDC payment in the sponsored group may be signed')
        txns=[encoding.msgpack_decode(base64.b64encode(raw).decode()) for raw in unsigned_txns]
        check_sponsored_group(txns,self.cfg,self.address)
        return [None,base64.b64decode(encoding.msgpack_encode(txns[1].sign(self.key)))]

def build_payment_header(cfg,key,requirements_json):
    from x402 import x402ClientSync
    from x402.schemas import PaymentRequired
    from x402.mechanisms.avm.exact import ExactAvmScheme
    from x402.http import x402HTTPClientSync
    required=PaymentRequired.model_validate(requirements_json)
    if required.x402_version!=2 or len(required.accepts)!=1:raise SafetyError('Only x402 V2 with a known fee is accepted')
    req=required.accepts[0]
    if (req.scheme!='exact' or req.network!=cfg.chain or req.asset!=cfg.asset or
        req.pay_to!=cfg.pay_to or req.amount!=str(cfg.price) or req.extra!={'feePayer':cfg.fee_payer} or
        req.max_timeout_seconds!=120):raise SafetyError('x402 fee changed or requests unsupported permissions')
    if not required.resource or required.resource.url!=cfg.endpoint+'/v1/audit':raise SafetyError('x402 resource differs from the authorized resource')
    mechanism=ExactAvmScheme(GuardedAlgoSigner(key,cfg),algod_url=cfg.algod_url)
    mechanism._clients[cfg.chain]=algo_client(cfg)
    client=x402ClientSync();client.register(cfg.chain,mechanism)
    payload=client.create_payment_payload(required)
    validate_payment_payload(payload,cfg,validate_algo_key(key))
    return x402HTTPClientSync(client).encode_payment_signature_header(payload)['PAYMENT-SIGNATURE'],payment_transaction(payload).get_txid()

class PaidAudit:
    """Durable queue and budgets. An ambiguous response never generates another payment."""
    def __init__(self,cfg,key,log,session=None,now=None):
        self.cfg=cfg;self.key=key;self.log=log;self.session=session or requests.Session()
        self.now=now or (lambda:int(time.time()*1000))
        folder=cfg.root/'estado';folder.mkdir(exist_ok=True)
        name=identity({'payer':cfg.payer,'chain':cfg.chain})[:20]
        self.db=sqlite3.connect(folder/('pagos_'+name+'.sqlite3'))
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS pagos (id TEXT PRIMARY KEY, body TEXT NOT NULL, stage TEXT NOT NULL, created INTEGER NOT NULL, reserved INTEGER DEFAULT 0, day TEXT, header TEXT, tx TEXT, receipt TEXT)')
        self.db.commit()

    def enqueue(self,event_id,kind,data):
        body={'id':event_id,'kind':kind,'payer':self.cfg.payer,'account':self.cfg.address.lower(),
              'created_ms':self.now(),'data':data}
        with self.db:
            row=self.db.execute('SELECT body FROM pagos WHERE id=?',(event_id,)).fetchone()
            if row:
                old=json.loads(row[0]);body['created_ms']=old['created_ms']
                if canonical(old)!=canonical(body):raise SafetyError('The same audit identifier contains different data')
            else:self.db.execute('INSERT INTO pagos(id,body,stage,created) VALUES (?,?,?,?)',(event_id,canonical(body),'queued',self.now()))
        return event_id

    def reserve(self,event_id):
        day=datetime.fromtimestamp(self.now()/1000,timezone.utc).strftime('%Y-%m-%d')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT reserved FROM pagos WHERE id=?',(event_id,)).fetchone()
            if not row:raise SafetyError('Payment has no prior event')
            if not row[0]:
                total=self.db.execute('SELECT COALESCE(SUM(reserved),0) FROM pagos').fetchone()[0]
                daily=self.db.execute('SELECT COALESCE(SUM(reserved),0) FROM pagos WHERE day=?',(day,)).fetchone()[0]
                if daily+self.cfg.price>self.cfg.daily or total+self.cfg.price>self.cfg.total:raise Rejected('Micropayment limit reached; stops and exits remain active, new entries paused')
                self.db.execute('UPDATE pagos SET reserved=?,day=?,stage=? WHERE id=?',(self.cfg.price,day,'reserved',event_id))
            self.db.commit()
        except Exception:self.db.rollback();raise

    def request(self,body,header=None):
        from algosdk.util import sign_bytes
        raw=canonical(body).encode()
        headers={'Content-Type':'application/json','X-QTS-Payer':self.cfg.payer,
                 'X-QTS-Signature':sign_bytes(raw,self.key)}
        if header:headers['PAYMENT-SIGNATURE']=header
        return self.session.post(self.cfg.endpoint+'/v1/audit',data=raw,headers=headers,timeout=(5,12),allow_redirects=False)

    def receipt(self,response,event_id,txid):
        data=response.json();settle=qts_unb64(response.headers.get('PAYMENT-RESPONSE',''))
        saved=self.db.execute('SELECT header FROM pagos WHERE id=?',(event_id,)).fetchone()
        txns=validate_payment_payload(qts_unb64(saved[0]),self.cfg,self.cfg.payer)
        group_ids={txn.get_txid() for txn in txns}
        if (data.get('id')!=event_id or data.get('payer')!=self.cfg.payer or
            settle.get('success') is not True or settle.get('network')!=self.cfg.chain or
            data.get('transaction')!=txid or settle.get('transaction') not in group_ids or
            settle.get('payer')!=self.cfg.payer):raise SafetyError('Invalid payment receipt')
        confirmed=algo_client(self.cfg).pending_transaction_info(txid)
        if int(confirmed.get('confirmed-round',0))<=0:raise TemporaryError('Payment not yet confirmed by Algorand; will not be repeated')
        with self.db:self.db.execute('UPDATE pagos SET stage=?,receipt=? WHERE id=?',('paid',canonical(data),event_id))
        self.log.info('X402 PAYMENT CONFIRMED | %.6f USDC | event %s | transaction %s',self.cfg.price/1e6,event_id,txid)
        return data

    def pay(self,event_id):
        row=self.db.execute('SELECT body,stage,header,tx,receipt FROM pagos WHERE id=?',(event_id,)).fetchone()
        if not row:raise SafetyError('Audit not found')
        body,stage,header,txid,receipt=row
        if stage=='paid':return json.loads(receipt)
        body=json.loads(body)
        try:
            if not header:
                response=self.request(body)
                if response.status_code!=402:raise TemporaryError('Service did not return the expected x402 fee')
                required=qts_unb64(response.headers.get('PAYMENT-REQUIRED',''))
                # Durable reservation before signing. The budget includes ambiguous payments.
                self.reserve(event_id)
                header,txid=build_payment_header(self.cfg,self.key,required)
                with self.db:self.db.execute('UPDATE pagos SET stage=?,header=?,tx=? WHERE id=?',('signed',header,txid,event_id))
            response=self.request(body,header)
            if response.status_code==200:return self.receipt(response,event_id,txid)
            if response.status_code in (202,503):raise TemporaryError('Settlement/receipt pending; retaining the exact same payment')
            raise Rejected('Audit service rejected the request ('+str(response.status_code)+'); no additional payment will be generated')
        except requests.RequestException as e:raise TemporaryError('Payment service unavailable ('+type(e).__name__+'); charge will not be repeated') from None

    def activation(self):
        event_id=identity({'activation':self.cfg.consent_hash()})
        self.enqueue(event_id,'activation',{'strategy':'EMA34/89-ATR-RSI-funding','timeframe':'5m','version':VERSION})
        return self.pay(event_id)

    def entry_signal(self,row):
        """Fetch the entry decision from the paid service. No x402 charge per
        call: gated on a completed (already-paid) activation. This is the ONLY
        place the agent can learn whether to enter and how wide the initial stop
        must be; nothing here is computable locally."""
        self.activation()
        from algosdk.util import sign_bytes
        if row.get('source')!=MARKET_SOURCE:
            raise Rejected('Legacy Hyperliquid position: managing its existing stop; additions paused until flat')
        body={'payer':self.cfg.payer,'signal_ms':int(row['time']),'source':MARKET_SOURCE}
        raw=canonical(body).encode()
        headers={'Content-Type':'application/json','X-QTS-Payer':self.cfg.payer,'X-QTS-Signature':sign_bytes(raw,self.key)}
        try:
            response=self.session.post(self.cfg.endpoint+'/v1/signal/BTC',data=raw,headers=headers,timeout=(5,12),allow_redirects=False)
        except requests.RequestException as e:raise TemporaryError('Signal service unavailable ('+type(e).__name__+')') from None
        if response.status_code in (502,503,504):raise TemporaryError('Signal service busy; no entry this candle')
        if response.status_code==409:raise Rejected('Signal source/version mismatch; update the client from the Windows release')
        if response.status_code!=200:raise Rejected('Signal service did not authorize an entry ('+str(response.status_code)+')')
        data=response.json()
        if data.get('source')!=MARKET_SOURCE:raise Rejected('Service and client use different market data; update both')
        if data.get('signal') not in (-1,0,1):raise Rejected('Malformed signal from service')
        if data.get('signal_ms',row['time'])!=row['time']:raise Rejected('Service response belongs to a different candle')
        row['diagnostics']=data.get('diagnostics')
        return int(data['signal']),float(data.get('risk_atr',float('nan')))

    def queue_exit(self,position,closed_time,price,realized):
        event_id=identity({'account':self.cfg.address.lower(),'chain':self.cfg.chain,'opened':position['opened'],'event':'exit'})
        self.enqueue(event_id,'exit_receipt',{'opened_ms':position['opened'],'closed_ms':closed_time,
            'side':position['side'],'qty':position['qty'],'price':price,'realized_before_costs':realized})
        self.log.info('EXIT RECEIPT QUEUED | exit already executed; payment does not block protection')

    def drain_one(self):
        row=self.db.execute("SELECT id FROM pagos WHERE stage!='paid' ORDER BY created LIMIT 1").fetchone()
        if row:self.pay(row[0])

    def close(self):self.db.close()

class QTSBroker(RealBroker):
    def __init__(self,*args,cfg,**kwargs):super().__init__(*args,**kwargs);self.cfg=cfg
    def builder_authorized(self):
        value=self.api.post({'type':'maxBuilderFee','user':self.address,'builder':self.cfg.builder})
        if isinstance(value,bool) or not isinstance(value,(int,float)) or value<self.cfg.builder_units:
            raise Rejected('Fee not authorized or revoked on Hyperliquid; no new entry')
    def submit(self,command):
        c=command;kind=c['kind']
        if kind=='entry':
            # No charge on entry (it's charged on close), but valid authorization
            # is still required before opening a NEW position: no existing
            # protection is at stake here, so rejecting is safe.
            try:self.builder_authorized()
            except Rejected as error:return Outcome('rejected',message=str(error))
            except TemporaryError:return Outcome('rejected',message='Could not verify fee authorization')
            return super().submit(command)
        if kind not in ('stop','close') or (kind=='stop' and c.get('old_oid') is not None):
            return super().submit(command)
        # Charged on CLOSE only. A freshly placed stop and an emergency close try
        # to carry the builder tag, but PROTECTION is never blocked by this: if
        # authorization cannot be confirmed, the order is still sent WITHOUT builder
        # (that commission isn't charged, but the stop/close is still placed).
        # hyperliquid-python-sdk 0.24.0 also doesn't expose 'builder' on modify_order,
        # so a MODIFIED stop always falls back to the base path; it is unverified
        # whether Hyperliquid keeps the original stop's builder tag through a modify —
        # confirm on testnet before treating that path's commission as collected.
        builder=None
        try:
            self.builder_authorized();builder={'b':self.cfg.builder,'f':self.cfg.builder_units}
        except (Rejected,TemporaryError):pass
        if builder is None:return super().submit(command)
        from hyperliquid.utils.types import Cloid
        token=Cloid.from_str(c['cloid'])
        try:
            self.exchange.set_expires_after(c['expires'])
            buy=c['side']==-1
            if kind=='stop':
                # Native stop-market, first placement. The trigger is mark; the limit
                # price is 10% aggressive, matching the tolerance of market TP/SL orders.
                limit=order_price(c['stop']*(1.1 if buy else .9),buy,self.decimals)
                result=self.exchange.order('BTC',buy,c['qty'],limit,
                    order_type={'trigger':{'isMarket':True,'triggerPx':c['stop'],'tpsl':'sl'}},
                    reduce_only=True,cloid=token,builder=builder)
            else:
                limit=order_price(c['reference']*(1+self.emergency_slippage if buy else 1-self.emergency_slippage),buy,self.decimals)
                result=self.exchange.order('BTC',buy,c['qty'],limit,{'limit':{'tif':'Ioc'}},
                    reduce_only=True,cloid=token,builder=builder)
            return parse_response(result)
        except Exception as error:return Outcome('unknown',message='Confirmation pending ('+type(error).__name__+')')
        finally:self.exchange.set_expires_after(None)

class QTSAgent(Agent):
    def __init__(self,*args,payments=None,**kwargs):
        self.payments=payments;self.payment_error=None
        super().__init__(*args,**kwargs)
    def ask_service(self,row):
        """Entry decisions come from the paid service. Paper mode runs the
        agent's plumbing without it, so it simply never enters."""
        if self.broker.mode=='paper':
            row['entry_block']='paper monitor mode; private entry signals are not requested and no orders are opened'
            return None
        if not self.payments:
            row['entry_block']='payment credential unavailable; private signal cannot be requested'
            self.log.warning('ENTRY SKIPPED | payment credential unavailable; stops and exits remain active');return None
        return self.payments.entry_signal(row)
    def before_entry(self,row,side,qty,distance,is_add,level):
        if self.broker.mode=='paper':return True
        if not self.payments:
            self.log.warning('ENTRY PAUSED | payment credential unavailable; stops and exits remain active');return False
        try:
            self.broker.builder_authorized()
            if not 0<=self.now()-row['time']<=self.settings['max_edad_senal_segundos']*1000:
                raise Rejected('Signal expired while preparing the order; late entry skipped')
            self.quote=self.broker.quote()
            if abs(self.quote['mid']/row['close']-1)*100>self.settings['max_desviacion_entrada_pct']:
                raise Rejected('Price moved away during audit; entry will not be forced')
            # The native stop can trigger while payment is in flight. Never turn
            # an addition to a position that already closed into a new entry.
            current=self.broker.account();position=self.state['position']
            if is_add:
                if not position or abs(current.qty-position['side']*position['qty'])>10**(-self.broker.decimals)/2:
                    raise Rejected('Position changed during audit; reconciling before trading')
            elif current.qty or self.broker.orders():
                raise Rejected('BTC account is no longer clear after audit')
            worst=self.quote['mid']*(1+self.settings['entry_slippage_bps']/10000)
            if qty*worst*(1/self.mgmt.leverage+self.mgmt.fee_rate)>current.free_margin:
                raise Rejected('Available margin changed during audit')
            return True
        except Exception as error:
            safe=str(error) if isinstance(error,(SafetyError,TemporaryError,Rejected)) else type(error).__name__
            self.log.warning('ENTRY PAUSED | %s | existing protection remains active',safe)
            return False
    def after_close(self,position,closed_time,price,realized):
        # Queued in the SAME operational journal before clearing the position: recoverable after a crash.
        queue=self.store.get('outbox_exit',{})
        key=str(position['opened'])
        queue.setdefault(key,{'position':position,'closed_time':closed_time,'price':price,'realized':realized})
        self.store.put('outbox_exit',queue)
    def flush_exit_outbox(self):
        if not self.payments:return
        queue=self.store.get('outbox_exit',{})
        for key,item in list(queue.items()):
            self.payments.queue_exit(**item)
            queue.pop(key);self.store.put('outbox_exit',queue)


# ========================================================================
# SETUP.PY
def required_input(label,validate,input_fn=input):
    while True:
        value=input_fn(label).strip()
        try:
            if not value:raise SafetyError('This field is required. Ctrl+C cancels setup.')
            return validate(value)
        except (SafetyError,ValueError) as error:print('CANNOT CONTINUE:',error)

def store_verified(vault,service,name,key):
    try:
        vault.set_password(service,name,key)
        if vault.get_password(service,name)!=key:raise RuntimeError()
    except Exception:raise SafetyError('Could not save the protected credential. Setup cannot complete.') from None

def normalize_algo_key(text):
    # Only the SEPARATE micropayments wallet; the trading seed is never requested.
    if len(text.split())==25:
        from algosdk import mnemonic
        try:text=mnemonic.to_private_key(text)
        except Exception:raise SafetyError('Invalid 25-word payment wallet mnemonic') from None
    validate_algo_key(text)
    return text

def verify_hl_authority(api,address,key):
    from eth_account import Account as EthAccount
    signer=EthAccount.from_key(validate_hl_key(key,address)).address.lower()
    agents=api.post({'type':'extraAgents','user':address})
    now=int(time.time()*1000)
    if not isinstance(agents,list) or not any(str(x.get('address','')).lower()==signer and int(x.get('validUntil',0))>now for x in agents):
        raise SafetyError('API wallet is not authorized or has expired. Authorize this key at app.hyperliquid.xyz/API and retry.')
    account=parse_account(api.account(address))
    if account.equity<=0:raise SafetyError('Main account has no available USDC futures equity')
    return account

def builder_typed_action(cfg,nonce):
    from hyperliquid.utils.signing import user_signed_payload
    action={'type':'approveBuilderFee','maxFeeRate':str(cfg.fee_bps/100)+'%',
            'builder':cfg.builder,'nonce':nonce,'signatureChainId':'0x66eee',
            'hyperliquidChain':'Mainnet' if cfg.network=='mainnet' else 'Testnet'}
    fields=[{'name':'hyperliquidChain','type':'string'},{'name':'maxFeeRate','type':'string'},
            {'name':'builder','type':'address'},{'name':'nonce','type':'uint64'}]
    return action,user_signed_payload('HyperliquidTransaction:ApproveBuilderFee',fields,action)

def approve_builder_locally(cfg,api,timeout=300):
    """Signs in the wallet extension, never in Python with the main key."""
    from http.server import BaseHTTPRequestHandler,HTTPServer
    from eth_account import Account as EthAccount
    from eth_account.messages import encode_typed_data
    import threading,webbrowser
    nonce=int(time.time()*1000);action,typed=builder_typed_action(cfg,nonce)
    token=secrets.token_urlsafe(32);done=threading.Event();outcome={}
    public={'account':cfg.address,'typed':typed,'fee':str(cfg.fee_bps/100)+'%', 'builder':cfg.builder}
    data=canonical(public).replace('<','\\u003c')
    html='''<!doctype html><html lang="en"><meta charset="utf-8"><title>QUANT TRADING SIGNALS · Local authorization</title>
<style>body{font:18px system-ui;max-width:820px;margin:50px auto;background:#101722;color:#f0f4fa;padding:24px}button{padding:14px;font-size:18px;background:#74e0b8}code{overflow-wrap:anywhere}p{line-height:1.6}</style>
<h1>QUANT TRADING SIGNALS</h1><h2>Authorize entry fee</h2>
<p>This page runs on your computer. Signing happens in your wallet extension. Do not enter keys or seed phrases here.</p>
<p id="details"></p><button id="approve">Connect wallet and review authorization</button><p id="status"></p>
<script>const cfg=__DATA__;document.getElementById('details').textContent='Maximum fee: '+cfg.fee+' of filled notional. Recipient: '+cfg.builder+'. Does not authorize withdrawals.';
document.getElementById('approve').onclick=async()=>{const s=document.getElementById('status');try{if(!window.ethereum)throw Error('Open this URL in Chrome with your MetaMask/Rabby extension installed.');
const accounts=await window.ethereum.request({method:'eth_requestAccounts'});if(accounts[0].toLowerCase()!==cfg.account.toLowerCase())throw Error('Select the main wallet '+cfg.account);
s.textContent='Review the amount and recipient in your wallet.';
const chainId=await window.ethereum.request({method:'eth_chainId'});cfg.typed.domain.chainId=parseInt(chainId,16);cfg.typed.message.signatureChainId=chainId;
const sig=await window.ethereum.request({method:'eth_signTypedData_v4',params:[accounts[0],JSON.stringify(cfg.typed)]});
const r=await fetch(location.pathname+'/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({signature:sig,chainId})});
const b=await r.json();if(!r.ok)throw Error(b.error||'Not confirmed');s.textContent='Authorization confirmed. Return to the console.';document.getElementById('approve').disabled=true;
}catch(e){s.textContent=e.message;}};</script></html>'''.replace('__DATA__',data)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def response(self,status,body,ctype='application/json'):
            payload=body.encode();self.send_response(status);self.send_header('Content-Type',ctype)
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def do_GET(self):
            if self.headers.get('Host')!=expected_host or self.path!='/'+token:return self.response(404,'{}')
            self.response(200,html,'text/html; charset=utf-8')
        def do_POST(self):
            if (self.headers.get('Host')!=expected_host or self.headers.get('Origin')!='http://'+expected_host or
                self.path!='/'+token+'/submit' or done.is_set()):return self.response(403,'{}')
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=1024:raise ValueError()
                posted=json.loads(self.rfile.read(length));sig=posted['signature'];chain_id=posted['chainId']
                if not re.fullmatch(r'0x[0-9a-fA-F]{1,12}',chain_id):raise ValueError()
                from hyperliquid.utils.signing import user_signed_payload
                approved_action=dict(action,signatureChainId=chain_id)
                local_typed=user_signed_payload(typed['primaryType'],typed['types'][typed['primaryType']],approved_action)
                if not re.fullmatch(r'0x[0-9a-fA-F]{130}',sig):raise ValueError()
                signer=EthAccount.recover_message(encode_typed_data(full_message=local_typed),signature=sig)
                if signer.lower()!=cfg.address.lower():raise ValueError()
                signature={'r':'0x'+sig[2:66],'s':'0x'+sig[66:130],'v':int(sig[130:132],16)}
                if signature['v']<27:signature['v']+=27
                response=requests.post(api.base_url+'/exchange',json={'action':approved_action,'nonce':nonce,'signature':signature},timeout=12)
                response.raise_for_status()
                if response.json().get('status')!='ok':raise ValueError()
                outcome['success']=True;done.set();self.response(200,'{"ok":true}')
            except Exception:self.response(400,'{"error":"Authorization not confirmed. Check your wallet and retry."}')
    server=HTTPServer(('127.0.0.1',0),Handler);expected_host='127.0.0.1:'+str(server.server_port)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url='http://'+expected_host+'/'+token
    print('Open this LOCAL authorization page in the browser with your wallet:',url)
    webbrowser.open(url)
    try:
        if not done.wait(timeout):raise SafetyError('Authorization not completed. Setup remains pending; you can retry.')
    finally:server.shutdown();server.server_close()

def verify_algo_funds(cfg,key,input_fn=input,allow_optin=False):
    address=validate_algo_key(key);client=algo_client(cfg)
    if address==cfg.fee_payer:raise SafetyError('Payment wallet must not be the network fee sponsor')
    try:account=client.account_info(address)
    except Exception:raise SafetyError('Payment wallet is not ready. Ask the provider to activate it before depositing Algorand USDC.') from None
    if account.get('auth-addr') not in (None,'',address):raise SafetyError('Rekeyed payment accounts are not supported')
    assets={str(x['asset-id']):int(x['amount']) for x in account.get('assets',[])}
    if cfg.asset not in assets:
        raise SafetyError('USDC is not enabled. Ask the provider to activate this wallet and its USDC opt-in first. Do not send USDC yet.')
    if int(account.get('amount',0))<int(account.get('min-balance',0)):
        raise SafetyError('Wallet minimum balance is missing; ask the provider to restore its activation reserve')
    if assets[cfg.asset]<cfg.price*2:raise SafetyError('Deposit at least two service fees in USDC into the payment wallet and retry')
    # The account reserve remains necessary, but the customer pays no ALGO transaction fee.
    return account

def configure_wizard(cfg,input_fn=input,password_fn=getpass.getpass,vault=None,api=None):
    print('\nQUANT TRADING SIGNALS — guided LIVE setup')
    print('No trades are opened. Ctrl+C cancels without completing setup. Never enter your main trading seed phrase.')
    cfg.merchant_ready()
    vault=vault or protected_vault();api=api or PublicAPI(cfg.network)
    cfg.ini['setup']['completed']='no';cfg.ini['setup']['acceptance_sha256']='';cfg.save()
    def public_address(value):
        if not re.fullmatch(r'0x[0-9a-fA-F]{40}',value) or int(value,16)==0:raise SafetyError('Must be a public 0x address')
        return value
    print('\nSTEP 1/6 · Hyperliquid account')
    address=cfg.address or required_input('Main account public 0x address: ',public_address,input_fn)
    cfg.ini['account']['wallet_hyperliquid']=address;cfg.save();cfg=Settings(cfg.path)
    print('\nSTEP 2/6 · Trading API wallet (separate and revocable)')
    print('Create/authorize an API wallet at https://app.hyperliquid.xyz/API. Its key is not your main wallet key.')
    hl=vault.get_password(secret_service('Hyperliquid',cfg.network),address.lower())
    if not hl:hl=required_input('Hyperliquid API PRIVATE key (hidden input): ',lambda x:validate_hl_key(x,address),password_fn)
    validate_hl_key(hl,address);verify_hl_authority(api,address,hl)
    store_verified(vault,secret_service('Hyperliquid',cfg.network),address.lower(),hl)
    print('API key authorized and saved in your operating system password manager, not in a text file.')
    print('\nSTEP 3/6 · Separate wallet for Algorand micropayments')
    print('Use a separate, activated Algorand USDC wallet. Network transaction fees are sponsored; no ALGO top-ups for payments.')
    print('Enter its Base64 key or the 25 words of ONLY this secondary wallet, never your main trading seed.')
    algo=vault.get_password(secret_service('Algorand',cfg.network),cfg.payer) if cfg.payer else None
    if not algo:algo=required_input('Secondary Algorand wallet key (hidden input): ',normalize_algo_key,password_fn)
    payer=validate_algo_key(algo)
    cfg.ini['account']['wallet_algorand_payments']=payer;cfg.save();cfg=Settings(cfg.path)
    store_verified(vault,secret_service('Algorand',cfg.network),payer,algo)
    print('Payment wallet:',payer,'| Network:',cfg.network,'| USDC ASA:',cfg.asset)
    verify_algo_funds(cfg,algo,input_fn,allow_optin=True)
    print('\nSTEP 4/6 · Review both fees')
    print(f'Developer fee: {cfg.fee_bps/100}% of the closing fill only (entries are free), paid to {cfg.builder} through Hyperliquid Builder codes.')
    print('Hyperliquid exchange fee: base taker rate 0.045% per fill, separate; account tier/discounts may change it. Funding is variable.')
    print(f'x402: {cfg.price/1e6:.6f} USDC per one-time activation and per full-position exit receipt. Recipient: {cfg.pay_to}')
    print(f'x402 limits: {cfg.daily/1e6:.2f} USDC/day UTC, {cfg.total/1e6:.2f} USDC total for this installation. Algorand transaction fees are covered by the sponsor.')
    print('A one-time activation payment is required before the first private signal. Developer commission and exit receipts apply on close; protective exits never wait for payment.')
    print('The service receives public addresses and trade data for auditing; it never receives your keys.')
    terms=requests.get(cfg.endpoint+'/v1/terms',timeout=12,allow_redirects=False)
    if terms.status_code!=200 or terms.json()!=cfg.terms():raise SafetyError('Service terms do not match config.txt')
    if input_fn('To accept these fees and limits, type ACCEPT: ').strip()!='ACCEPT':raise SafetyError('Fees not accepted. Live mode will not be activated.')
    print('\nSTEP 5/6 · Authorize the fee with your main wallet')
    max_fee=api.post({'type':'maxBuilderFee','user':address,'builder':cfg.builder})
    if not isinstance(max_fee,(int,float)) or max_fee<cfg.builder_units:approve_builder_locally(cfg,api)
    max_fee=api.post({'type':'maxBuilderFee','user':address,'builder':cfg.builder})
    if not isinstance(max_fee,(int,float)) or max_fee<cfg.builder_units:raise SafetyError('Hyperliquid has not confirmed fee authorization; repeat setup')
    print('\nSTEP 6/6 · Save configuration')
    verify_hl_authority(api,address,hl)
    cfg.ini['account']['mode']='live';cfg.ini['setup']['acceptance_sha256']=cfg.consent_hash();cfg.ini['setup']['completed']='si';cfg.save()
    print('SETUP COMPLETE. No trades were opened or service fees charged during setup.')
    print('Run INICIAR.bat (Windows) or ./iniciar.sh. config.txt contains only public data; keys remain protected.')
    return cfg

def publisher_wizard(cfg,input_fn=input):
    from algosdk.encoding import is_valid_address
    from x402.http import HTTPFacilitatorClientSync,FacilitatorConfig
    print('PUBLISHER ONLY: embed public recipients and service URL in agente.py before distribution.')
    print('The developer fee is charged on the closing fill only; x402 is 0.01 USDC per activation/exit service, embedded rate from PUBLISHER_POLICY.')
    def evm(value):
        if not re.fullmatch(r'0x[0-9a-fA-F]{40}',value) or int(value,16)==0:raise SafetyError('Invalid public EVM address')
        return value.lower()
    def algo(value):
        if not is_valid_address(value):raise SafetyError('Invalid public Algorand address')
        return value
    policy=dict(PUBLISHER_POLICY)
    policy['service_url']=required_input('Deployed HTTPS service URL: ',trusted_url,input_fn)
    policy['builder_address']=required_input('YOUR public Hyperliquid developer address: ',evm,input_fn)
    policy['algorand_recipient']=required_input('YOUR public Algorand USDC recipient: ',algo,input_fn)
    facilitator=HTTPFacilitatorClientSync(FacilitatorConfig(url=trusted_url(cfg.get('server','facilitator_url')),timeout=12))
    supported=facilitator.get_supported()
    from x402.mechanisms.avm.constants import ALGORAND_MAINNET_CAIP2,ALGORAND_TESTNET_CAIP2
    for network,chain in [('mainnet',ALGORAND_MAINNET_CAIP2),('testnet',ALGORAND_TESTNET_CAIP2)]:
        sponsors=[(k.extra or {}).get('feePayer') for k in supported.kinds if k.network==chain and k.scheme=='exact' and k.x402_version==2]
        sponsors=[x for x in sponsors if x and is_valid_address(x)]
        if not sponsors:raise SafetyError('No advertised fee sponsor for '+network+'; publisher setup is incomplete')
        policy['fee_payer_'+network]=sponsors[0]
    print('Public release policy:',json.dumps(policy,indent=2))
    if input_fn('Type EMBED to write these public addresses into the release: ').strip()!='EMBED':
        raise SafetyError('Publisher configuration was not changed')
    source=Path(__file__).resolve();text=source.read_text(encoding='utf-8')
    start='# BEGIN QTS PUBLISHER POLICY';end='# END QTS PUBLISHER POLICY'
    a=text.index(start);b=text.index(end,a)+len(end)
    atomic_text(source,text[:a]+start+'\nPUBLISHER_POLICY = '+json.dumps(policy,separators=(',',':'))+'\n'+end+text[b:])
    for key in ('service_url','wallet_hyperliquid_commission','wallet_algorand_payout','hyperliquid_commission_bps','service_price_usdc'):
        cfg.ini['billing'].pop(key,None)
    cfg.ini['account']['wallet_hyperliquid']='';cfg.ini['account']['wallet_algorand_payments']='';cfg.ini['account']['mode']='paper'
    cfg.ini['setup']['completed']='no';cfg.ini['setup']['acceptance_sha256']='';cfg.save()
    print('Public policy embedded. Restart the program. Deploy the same release on the paid API and test on Testnet before distribution.')


# ========================================================================
# SERVICE.PY
from contextlib import contextmanager

class ServiceError(Exception):
    def __init__(self,status,message):self.status=status;self.message=message

class AuditService:
    """Publisher's service; receives no keys and has no ability to execute trades."""
    def __init__(self,cfg,facilitator=None,algod=None,market=None,now=None):
        from x402.http import HTTPFacilitatorClientSync,FacilitatorConfig
        self.cfg=cfg;cfg.merchant_ready();self.now=now or (lambda:int(time.time()*1000))
        self.facilitator=facilitator or HTTPFacilitatorClientSync(FacilitatorConfig(
            url=trusted_url(cfg.get('server','facilitator_url')),timeout=12))
        self.algod=algod or algo_client(cfg);self.market=market or PublicAPI(cfg.network)
        folder=service_state_dir(cfg)
        self.path=folder/'cobros.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, payer TEXT NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL, tx TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, stage TEXT NOT NULL, receipt TEXT)')
        os.chmod(self.path,0o600)

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
        try:
            with db:yield db
        finally:db.close()

    def requirement(self):
        from x402.schemas import PaymentRequirements
        return PaymentRequirements(scheme='exact',network=self.cfg.chain,asset=self.cfg.asset,
            amount=str(self.cfg.price),pay_to=self.cfg.pay_to,max_timeout_seconds=120,extra={'feePayer':self.cfg.fee_payer})

    def quote(self):
        from x402.schemas import PaymentRequired,ResourceInfo
        return PaymentRequired(accepts=[self.requirement()],resource=ResourceInfo(url=self.cfg.endpoint+'/v1/audit',
            description='QUANT TRADING SIGNALS: pre-trade risk check or execution audit receipt',mime_type='application/json'))

    def validate_body(self,body,payer,signature):
        from algosdk.encoding import is_valid_address
        from algosdk.util import verify_bytes
        try:
            if not isinstance(body,dict) or set(body)!={'id','kind','payer','account','created_ms','data'}:raise ValueError()
            raw=canonical(body).encode()
            if len(raw)>16000 or not is_valid_address(payer) or body['payer']!=payer or not verify_bytes(raw,signature,payer):raise ValueError()
            if not re.fullmatch('[0-9a-f]{64}',body['id']):raise ValueError()
            if body['kind'] not in ('activation','entry_audit','exit_receipt'):raise ValueError()
            if not re.fullmatch('0x[0-9a-f]{40}',body['account']):raise ValueError()
            if not isinstance(body['data'],dict) or not isinstance(body['created_ms'],int):raise ValueError()
            if body['created_ms']>self.now()+30_000:raise ValueError()
        except Exception:raise ServiceError(401,'Invalid payer request or signature') from None
        return identity(body)

    def evaluate(self,body):
        data=body['data'];kind=body['kind']
        if kind=='activation':return {'allowed':True,'service':'QTS risk audit and receipts','terms_sha256':identity(self.cfg.terms()),'strategy':data.get('strategy'),'timeframe':'5m'}
        if kind=='exit_receipt':
            if not {'opened_ms','closed_ms','side','qty','price','realized_before_costs'}<=set(data):raise ServiceError(422,'Incomplete exit receipt')
            return {'allowed':True,'report_sha256':identity(data),'status':'client_reported','note':'Audit of client report; not independent proof of exchange fills or profit'}
        try:
            for key in ('price','stop','qty','rsi','funding','signal_ms','leverage'):
                if isinstance(data[key],bool) or not math.isfinite(float(data[key])):raise ValueError()
            if data['side'] not in (-1,1) or data['qty']<=0 or data['price']<=0 or data['stop']<=0 or not 1<=data['leverage']<=5:raise ValueError()
            if not 0<=self.now()-int(data['signal_ms'])<=60_000:raise ServiceError(422,'Signal is no longer recent')
        except (KeyError,ValueError,TypeError):raise ServiceError(422,'Invalid risk data') from None
        quote=self.market.quote();price=float(quote['mid']);mark=float(quote['mark'])
        deviation=abs(price/data['price']-1)*100
        distance=data['side']*(mark-data['stop'])/mark
        allowed=deviation<=.3 and 0<distance<.9/data['leverage'] and data['qty']*price>=10
        return {'allowed':allowed,'market_mid':price,'market_mark':mark,'price_deviation_pct':deviation,
                'stop_distance_pct':distance*100,'notional_usdc':data['qty']*price,
                'checked_at_ms':self.now(),'report_sha256':identity(data),
                'scope':'freshness, price deviation, stop direction/distance and minimum size; not a return forecast'}

    def cached(self,body,digest):
        with self.connect() as db:row=db.execute('SELECT payer,digest,tx,payload,stage,receipt FROM events WHERE id=?',(body['id'],)).fetchone()
        if row and (row[0]!=body['payer'] or row[1]!=digest):raise ServiceError(409,'Identifier already associated with different data')
        return row

    def finish(self,body,txid,result,settle=None):
        settle=settle or {'success':True,'transaction':txid,'network':self.cfg.chain,'payer':body['payer']}
        receipt={'id':body['id'],'payer':body['payer'],'kind':body['kind'],'result':result,
                 'audit_sha256':identity(body),'transaction':txid,'amount':str(self.cfg.price),'network':self.cfg.chain}
        packed={'receipt':receipt,'settle':settle}
        with self.connect() as db:db.execute('UPDATE events SET stage=?,receipt=? WHERE id=?',('paid',canonical(packed),body['id']))
        return 200,receipt,{'PAYMENT-RESPONSE':qts_b64(settle)}

    def activated(self,payer):
        """True once this payer has a PAID activation on file. Not x402-gated itself:
        it just proves the customer completed the one-time paid onboarding."""
        with self.connect() as db:
            rows=db.execute("SELECT body FROM events WHERE payer=? AND stage='paid'",(payer,)).fetchall()
        return any(json.loads(row[0]).get('kind')=='activation' for row in rows)

    def evaluate_signal(self,payer,feed,signal_ms):
        """Free (no x402 charge) once activated: THE entry decision. The customer's
        agent cannot compute this locally, so removing the call does not save the
        commission, it stops the agent from opening anything."""
        if not self.activated(payer):raise ServiceError(402,'Complete activation before requesting signals')
        decision=feed.decision(signal_ms)
        if decision is None:raise ServiceError(422,'Unknown or not-yet-closed signal timestamp')
        signal,risk_atr=decision
        result={'signal':signal,'risk_atr':risk_atr,'signal_ms':int(signal_ms),'decided_at_ms':self.now(),'source':MARKET_SOURCE}
        if hasattr(feed,'explain'):result['diagnostics']=feed.explain(signal_ms)
        return result

    def handle(self,body,payer,signature,payment_header=None):
        from x402.schemas import PaymentPayload
        digest=self.validate_body(body,payer,signature)
        old=self.cached(body,digest)
        if old:
            if old[4]=='paid':
                packed=json.loads(old[5]);return 200,packed['receipt'],{'PAYMENT-RESPONSE':qts_b64(packed['settle'])}
            # A failure after charging is reconciled on-chain; it never creates another charge.
            try:confirmed=self.algod.pending_transaction_info(old[2])
            except Exception:return 202,{'status':'settlement_pending','id':body['id']},{}
            if int(confirmed.get('confirmed-round',0))>0:
                packed=json.loads(old[5]);return self.finish(body,old[2],packed['result'])
            return 202,{'status':'settlement_pending','id':body['id']},{}
        result=self.evaluate(body)
        if not payment_header:return 402,{'error':'payment_required'}, {'PAYMENT-REQUIRED':qts_b64(self.quote())}
        try:
            payload=PaymentPayload.model_validate(qts_unb64(payment_header))
            if payload.x402_version!=2 or payload.accepted!=self.requirement():raise ValueError()
            if not payload.resource or payload.resource.url!=self.cfg.endpoint+'/v1/audit':raise ValueError()
            txns=validate_payment_payload(payload,self.cfg,payer)
            txn=txns[1];txid=txn.get_txid()
        except Exception:raise ServiceError(400,'Payment differs from the request or is not allowed') from None
        try:verification=self.facilitator.verify(payload,self.requirement())
        except Exception:raise ServiceError(503,'Cannot verify payment; retain the same identifier and signature') from None
        if not verification.is_valid or verification.payer!=payer:raise ServiceError(402,'Invalid payment for this payer')
        # A single reservation per event AND per transaction before calling settle.
        try:
            with self.connect() as db:
                db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?,?)',
                    (body['id'],payer,digest,canonical(body),txid,canonical(payload.model_dump(by_alias=True,exclude_none=True)),
                     'settling',canonical({'result':result})))
        except sqlite3.IntegrityError:raise ServiceError(409,'This event or transaction is already being processed; it will not be charged again') from None
        try:settle=self.facilitator.settle(payload,self.requirement())
        except Exception:return 202,{'status':'settlement_pending','id':body['id']},{}
        if not settle.success:return 202,{'status':'settlement_pending','id':body['id']},{}
        if settle.network!=self.cfg.chain or settle.transaction not in {t.get_txid() for t in txns} or settle.payer!=payer:return 202,{'status':'settlement_pending','id':body['id']},{}
        return self.finish(body,txid,result,settle.model_dump(by_alias=True,exclude_none=True))

def validate_signal_request(body,payer,signature,now):
    from algosdk.encoding import is_valid_address
    from algosdk.util import verify_bytes
    try:
        if not isinstance(body,dict) or set(body) not in ({'payer','signal_ms'},{'payer','signal_ms','source'}):raise ValueError()
        raw=canonical(body).encode()
        if len(raw)>2000 or not is_valid_address(payer) or body['payer']!=payer or not verify_bytes(raw,signature,payer):raise ValueError()
        if body.get('source')!=MARKET_SOURCE:raise ServiceError(409,'Update client: Binance BTCUSDC market data required')
        if isinstance(body['signal_ms'],bool) or not isinstance(body['signal_ms'],int):raise ValueError()
        if not 0<=now-int(body['signal_ms'])<=60_000:raise ServiceError(422,'Signal is no longer recent')
    except ServiceError:raise
    except Exception:raise ServiceError(401,'Invalid payer request or signature') from None


class SignalFeed:
    """Server-side entry engine, shared by every activated customer. Imports the
    publisher's PRIVATE estrategia.py, which is never distributed: this is the
    only place the entry signal and its risk ATR exist.

    Recomputed at most every min_gap_ms so concurrent polls share one Binance
    fetch. The customer needs an activated account to request private signals."""
    def __init__(self,cfg,api=None,now=None,min_gap_ms=15_000,strategy=None):
        import threading
        if strategy is None:
            try:
                import estrategia as strategy
            except ImportError:
                raise SafetyError('estrategia.py is missing: the private entry engine must be deployed with the service') from None
        self.strategy=strategy
        self.now=now or (lambda:int(time.time()*1000));self.min_gap=min_gap_ms
        self.api=api or BinanceDataAPI()
        folder=service_state_dir(cfg)
        self.store=Store(folder/'senal_binance_btcusdc_v1.sqlite3',same_thread=False)
        self.management=Config(**DEFAULT_MANAGEMENT);self.stop=StopConfig(**DEFAULT_STOP)
        self.management.validate();self.stop.validate()
        self.market=Market(self.api,self.store,self.management,StopIndicators(),self.stop,
                           logging.getLogger('quant_trading_signals.senal'),delay_seconds=2)
        self.lock=threading.Lock();self.table={};self.reports={};self.fetched=0
        self.warm_thread=None

    def start_warming(self):
        import threading
        def work():
            while True:
                try:
                    now=self.now()
                    self.decision((now-2000)//BAR_MS*BAR_MS)
                except Exception as error:
                    logging.getLogger('quant_trading_signals.senal').warning('BINANCE SIGNAL WARMUP | %s',type(error).__name__)
                time.sleep(15)
        if self.warm_thread is None:
            self.warm_thread=threading.Thread(target=work,name='binance-signal-cache',daemon=True)
            self.warm_thread.start()

    def decision(self,signal_ms):
        """(signal, risk_atr) for that closed candle, or None if unknown."""
        with self.lock:
            if self.now()-self.fetched>=self.min_gap and ((self.now()-2000)//BAR_MS*BAR_MS not in self.table):
                self.market.sync(self.now())
                manifest=self.store.get('market_manifest')
                data={'BTC_5m.json':self.store.candles('5m'),
                      f'BTC_{self.strategy.DEFAULT_STRATEGY["context_hours"]}h.json':self.store.candles('12h'),
                      'BTC_funding.json':self.store.funding()}
                if hasattr(self.strategy,'entry_report'):
                    recent={int(x['T'])+1 for x in data['BTC_5m.json'][-2:]}
                    self.table,self.reports=self.strategy.entry_report(data,manifest,report_times=recent)
                else:
                    self.table=self.strategy.entry_rows(data,manifest);self.reports={}
                self.fetched=self.now()
            return self.table.get(int(signal_ms))

    def explain(self,signal_ms):
        with self.lock:return self.reports.get(int(signal_ms))


def service_state_dir(cfg):
    """Where the SERVICE keeps its payment journal and candle cache.

    On Render (and any container) the working directory is wiped on every
    redeploy, which would erase paid activations. Set QTS_STATE_DIR to a mounted
    persistent disk to keep them. Falls back to the local folder for local runs.
    This is server-only: the customer agent's own state folder is untouched.
    """
    folder=Path(os.environ.get('QTS_STATE_DIR') or (cfg.root/'estado_servidor'))
    folder.mkdir(parents=True,exist_ok=True)
    return folder


def make_service_app(cfg,service=None,feed=None):
    from fastapi import FastAPI,Request
    from fastapi.responses import JSONResponse
    app=FastAPI(title='QUANT TRADING SIGNALS paid audit API',docs_url=None,redoc_url=None)
    service=service or AuditService(cfg)
    # The signal engine is built on first use: the payment/audit endpoints must
    # not depend on the private estrategia.py being importable.
    lazy={'feed':feed}
    def signal_feed():
        if lazy['feed'] is None:lazy['feed']=SignalFeed(cfg)
        return lazy['feed']
    @app.get('/health')
    def health():
        private_path=Path(__file__).with_name('estrategia.py')
        return {'status':'ok','project':'QUANT TRADING SIGNALS','version':VERSION,'build':BUILD_ID,
                'agent_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'strategy_sha256':hashlib.sha256(private_path.read_bytes()).hexdigest() if private_path.is_file() else None,
                'signal_source':MARKET_SOURCE,
                'signal_last_closed_ms':max(lazy['feed'].table,default=None) if lazy['feed'] else None}
    @app.get('/v1/terms')
    def terms():return cfg.terms()
    # Request must be in globals to resolve FastAPI's postponed annotations.
    globals()['QTSRequest']=Request
    @app.post('/v1/audit')
    async def audit(request:QTSRequest):
        if int(request.headers.get('content-length','0'))>16000:return JSONResponse({'error':'too_large'},status_code=413)
        raw=await request.body()
        if len(raw)>16000:return JSONResponse({'error':'too_large'},status_code=413)
        from starlette.concurrency import run_in_threadpool
        try:
            body=json.loads(raw)
            status,data,headers=await run_in_threadpool(service.handle,body,request.headers.get('X-QTS-Payer',''),
                request.headers.get('X-QTS-Signature',''),request.headers.get('PAYMENT-SIGNATURE'))
            return JSONResponse(data,status_code=status,headers=headers)
        except ServiceError as error:return JSONResponse({'error':error.message},status_code=error.status)
        except Exception:return JSONResponse({'error':'service_unavailable'},status_code=503)
    @app.post('/v1/signal/{symbol}')
    async def signal(symbol:str,request:QTSRequest):
        if symbol!='BTC':return JSONResponse({'error':'unsupported_symbol'},status_code=404)
        raw=await request.body()
        if len(raw)>2000:return JSONResponse({'error':'too_large'},status_code=413)
        from starlette.concurrency import run_in_threadpool
        try:
            body=json.loads(raw)
            payer=request.headers.get('X-QTS-Payer','');signature=request.headers.get('X-QTS-Signature','')
            def run():
                validate_signal_request(body,payer,signature,service.now())
                return service.evaluate_signal(payer,signal_feed(),body['signal_ms'])
            return JSONResponse(await run_in_threadpool(run),status_code=200)
        except ServiceError as error:return JSONResponse({'error':error.message},status_code=error.status)
        except Exception as error:
            logging.getLogger('quant_trading_signals.senal').exception('SIGNAL REQUEST FAILED')
            return JSONResponse({'error':'service_unavailable','detail':type(error).__name__},status_code=503)
    return app

def serve_api(cfg):
    import uvicorn
    cfg.merchant_ready()
    service=AuditService(cfg);feed=SignalFeed(cfg)  # fail fast: no engine, no service
    feed.start_warming()
    supported=service.facilitator.get_supported()
    if not any(k.network==cfg.chain and k.scheme=='exact' and k.x402_version==2 and
               (k.extra or {}).get('feePayer')==cfg.fee_payer for k in supported.kinds):
        raise SafetyError('Facilitator does not advertise the embedded fee sponsor on this network')
    print('QUANT TRADING SIGNALS — paid service. Publish behind an HTTPS proxy; the client only needs its URL.')
    # Render (and most PaaS) assign the port via PORT and require binding to
    # 0.0.0.0; binding to config.txt's 127.0.0.1 would make the service
    # unreachable and fail the platform health check. Local runs keep config.txt.
    assigned=os.environ.get('PORT')
    host='0.0.0.0' if assigned else cfg.get('server','host')
    port=int(assigned) if assigned else int(cfg.get('server','port'))
    uvicorn.run(make_service_app(cfg,service,feed),host=host,port=port,access_log=False)


# ========================================================================
# EXECUTION.PY
def next_close(now,delay):return ((now-delay)//BAR_MS+1)*BAR_MS+delay

def run_agent(cfg,paper=False,once=False):
    from eth_account import Account as EthAccount
    from hyperliquid.exchange import Exchange
    mode='paper' if paper else cfg.mode
    settings=dict(cfg.settings);settings['mode']=mode
    if mode=='live':cfg.live_ready()
    now=lambda:int(time.time()*1000)
    account_key={'account':cfg.address.lower(),'network':cfg.network,'mode':mode,'coin':'BTC'}
    path=cfg.root/'estado'/(mode+'_'+cfg.network+'_'+identity(account_key)[:16]+'.sqlite3')
    with OneInstance(account_key):
        log=configure_log(cfg.root/'log.txt',mode)
        store=Store(path);payments=None;agent=None;market_store=None
        try:
            log.info('START QUANT TRADING SIGNALS | BTC/USDC perpetual 5m | %s | %.2f%% notional per entry | isolated 5x | maximum 3 entries',cfg.network,settings['capital_per_entry_pct'])
            api=PublicAPI(cfg.network);meta=api.meta()
            if mode=='live':
                vault=protected_vault()
                hl=vault.get_password(secret_service('Hyperliquid',cfg.network),cfg.address.lower())
                validate_hl_key(hl,cfg.address);verify_hl_authority(api,cfg.address,hl)
                exchange=Exchange(EthAccount.from_key(hl),api.base_url,meta=meta,account_address=cfg.address,
                    spot_meta={'universe':[],'tokens':[]},timeout=12)
                broker=QTSBroker(api,cfg.address,exchange,meta,now,settings['entry_slippage_bps'],settings['emergency_slippage_bps'],cfg=cfg)
                try:
                    algo=vault.get_password(secret_service('Algorand',cfg.network),cfg.payer)
                    if validate_algo_key(algo)!=cfg.payer:raise SafetyError('Payment key does not match the account')
                    payments=PaidAudit(cfg,algo,log)
                except Exception:
                    log.error('PAYMENT KEY UNAVAILABLE | all entries paused; recovering protection for existing positions')
                log.info('LIVE | developer fee %.2f bps via Hyperliquid Builder codes, charged on close only | x402 %.6f USDC per service; Algorand network fees sponsored',float(cfg.fee_bps),cfg.price/1e6)
            else:
                decimals=int(next(x for x in meta['universe'] if x['name']=='BTC')['szDecimals'])
                broker=PaperBroker(api,store,now,10000.,decimals,DEFAULT_MANAGEMENT['fee_rate'],settings['entry_slippage_bps'])
                log.info('PAPER MONITOR | public Binance indicators / Hyperliquid quotes | private entry signals disabled | no orders or payments')
            agent=QTSAgent(broker,store,cfg.management,cfg.stop,settings,log,now,payments=payments)
            legacy=agent.active() and agent.state.get('market_source')!=MARKET_SOURCE
            market_store=Store(cfg.root/'estado'/'binance_btcusdc_v1.sqlite3')
            binance=BinanceDataAPI(log,progress=lambda:agent.poll() if agent.active() else None)
            def select_market(legacy):
                return Market(api if legacy else binance,store if legacy else market_store,
                              cfg.management,cfg.indicators,cfg.stop,log,settings['retardo_cierre_segundos'])
            market=select_market(legacy)
            if legacy:
                log.warning('LEGACY POSITION | retaining Hyperliquid stop inputs until flat; no additions; Binance starts afterwards')
            else:
                if agent.state.get('market_source')!=MARKET_SOURCE:
                    agent.state['last_bar']=0
                agent.state['market_source']=MARKET_SOURCE;agent.save()
            log.info('DATA | indicators and published funding: Binance USD-M BTCUSDC | execution and native stops: Hyperliquid | basis limit 0.30%%')
            delay=int(settings['retardo_cierre_segundos']*1000)
            scan_due=0;poll_due=0;pay_due=0;failures=0
            while True:
                current=now()
                if current>=scan_due or (agent.active() and current>=poll_due):
                    try:
                        # Protection first; a payment provider being down never stops these calls.
                        agent.poll();poll_due=now()+settings['revision_posicion_segundos']*1000
                        if legacy and not agent.active():
                            legacy=False;market=select_market(False)
                            agent.state['last_bar']=0;agent.state['market_source']=MARKET_SOURCE;agent.save()
                            scan_due=0
                            log.info('BINANCE DATA ACTIVATED | legacy position closed; initializing a fresh scan baseline')
                        if now()>=scan_due:
                            agent.process_rows(market.sync(now()))
                            scan_due=next_close(now(),delay)
                        agent.flush_exit_outbox()
                        if payments and now()>=pay_due and not agent.state['pending']:
                            try:
                                payments.activation();payments.drain_one()
                            except Exception as error:
                                safe=str(error) if isinstance(error,(SafetyError,TemporaryError,Rejected)) else type(error).__name__
                                log.warning('PAYMENT PENDING | %s | stops and exits remain active',safe)
                            pay_due=now()+300_000
                        failures=0
                        if once:break
                    except TemporaryError as error:
                        failures+=1;wait=min(60,5*2**min(failures,4))
                        log.warning('CONNECTION/DATA | %s | retry in %ss',error,wait)
                        if once:raise
                        time.sleep(wait);continue
                    except Rejected as error:
                        log.warning('ACTION SKIPPED | %s',error);scan_due=next_close(now(),delay)
                        if once:break
                target=min(scan_due,poll_due) if agent.active() else scan_due
                time.sleep(max(.1,min(1.,(target-now())/1000)))
        finally:
            if agent and agent.active():log.warning('STOPPED | state saved; confirmed live stop remains but will not advance until restart')
            else:log.info('STOPPED | state saved')
            if payments:payments.close()
            if market_store:market_store.close()
            store.close()
    return 0

def main(argv=None):
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser(description='QUANT TRADING SIGNALS · BTC/USDC 5m agent')
    parser.add_argument('--config',type=Path,default=ROOT/'config.txt',help='Single public settings file')
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--configurar',action='store_true',help='Required guided setup before live trading')
    group.add_argument('--preparar-publicacion',action='store_true',help='Publisher: set public recipients and service URL')
    group.add_argument('--servir',action='store_true',help='Publisher: run the x402 API; no trading or customer keys required')
    group.add_argument('--paper',action='store_true',help='Public-data monitor; no private entry signals, orders or payments')
    parser.add_argument('--once',action='store_true',help='Run one cycle and exit; live mode may submit orders')
    args=parser.parse_args(argv)
    try:
        cfg=Settings(args.config)
        if args.configurar:configure_wizard(cfg);return 0
        if args.preparar_publicacion:publisher_wizard(cfg);return 0
        if args.servir:serve_api(cfg);return 0
        return run_agent(cfg,args.paper,args.once)
    except KeyboardInterrupt:print('\nCancelled. Incomplete setup is not marked as complete.');return 130
    except (SafetyError,TemporaryError,Rejected,ValueError,KeyError,configparser.Error) as error:
        print('CANNOT CONTINUE |',str(error));return 1
    except Exception as error:
        print('CANNOT CONTINUE |',type(error).__name__,'| credentials are not displayed. Check connectivity and README.md.');return 1

if __name__=='__main__':raise SystemExit(main())
