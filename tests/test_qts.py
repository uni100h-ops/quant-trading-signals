"""Local tests: ephemeral, unfunded keys — no order or payment ever touches the network."""
import base64,configparser,copy,io,json,logging,tempfile,time,unittest,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import agente as q
from algosdk import account,encoding,transaction
from algosdk.util import sign_bytes
from eth_account import Account as EthAccount
from x402.schemas import VerifyResponse,SettleResponse,PaymentPayload

ROOT=Path(__file__).resolve().parents[1]
HL_KEY='0x'+'2'*64
MAIN='0x'+'1'*40

class Vault:
    def __init__(self):self.items={}
    def get_password(self,service,name):return self.items.get((service,name))
    def set_password(self,service,name,key):self.items[(service,name)]=key

class Algo:
    def __init__(self,cfg):self.cfg=cfg;self.confirmed={}
    def suggested_params(self):
        return transaction.SuggestedParams(fee=1000,first=100,last=220,gh=self.cfg.chain.split(':',1)[1],gen='testnet-v1.0',flat_fee=True,min_fee=1000)
    def pending_transaction_info(self,tx):return self.confirmed.get(tx,{'confirmed-round':0})
    def account_info(self,address):return {'amount':1000000,'min-balance':200000,'assets':[{'asset-id':int(self.cfg.asset),'amount':1000000}]}

class Facilitator:
    def __init__(self,algo):self.algo=algo;self.settlements=0;self.lose_response=False
    def verify(self,payload,req):
        from nacl.signing import VerifyKey
        signed=encoding.msgpack_decode(payload.payload['paymentGroup'][1]);txn=signed.transaction
        VerifyKey(encoding.decode_address(txn.sender)).verify(b'TX'+base64.b64decode(encoding.msgpack_encode(txn)),base64.b64decode(signed.signature))
        return VerifyResponse(is_valid=True,payer=txn.sender)
    def settle(self,payload,req):
        self.settlements+=1;txn=q.payment_transaction(payload);txid=txn.get_txid()
        self.algo.confirmed[txid]={'confirmed-round':123}
        if self.lose_response:raise TimeoutError()
        return SettleResponse(success=True,transaction=txid,network=req.network,payer=txn.sender)

class Bridge:
    def __init__(self,client):self.client=client;self.lose_once=False
    def post(self,url,data,headers,timeout,allow_redirects):
        response=self.client.post('/v1/audit',content=data,headers=headers)
        if self.lose_once and response.status_code==200:
            self.lose_once=False
            import requests
            raise requests.Timeout()
        return response

class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.algo_key,self.payer=account.generate_account();_,pay_to=account.generate_account();_,sponsor=account.generate_account()
        self.policy_patch=patch.dict(q.PUBLISHER_POLICY,service_url='http://127.0.0.1:8000',builder_address='0x'+'3'*40,algorand_recipient=pay_to,fee_payer_testnet=sponsor)
        self.policy_patch.start();self.addCleanup(self.policy_patch.stop)
        ini=configparser.ConfigParser(interpolation=None);ini.read(ROOT/'config.txt')
        ini['account'].update(network='testnet',wallet_hyperliquid=MAIN,wallet_algorand_payments=self.payer)
        ini['billing'].update(service_url='http://127.0.0.1:8000',wallet_hyperliquid_commission='0x'+'3'*40,
            wallet_algorand_payout=pay_to,algod_url='https://testnet-api.algonode.cloud')
        with (self.root/'config.txt').open('w') as f:ini.write(f)
        self.cfg=q.Settings(self.root/'config.txt');self.algo=Algo(self.cfg);self.facilitator=Facilitator(self.algo)
        self.clock=lambda:1_800_000_000_000
        self.market=SimpleNamespace(quote=lambda:{'mid':60000.,'mark':60000.,'time':self.clock()})
        self.log=logging.Logger('test_qts');self.log.addHandler(logging.NullHandler())
        self.patch=patch.object(q,'algo_client',return_value=self.algo);self.patch.start()
        self.service=q.AuditService(self.cfg,self.facilitator,self.algo,self.market,self.clock)
        from fastapi.testclient import TestClient
        self.http=TestClient(q.make_service_app(self.cfg,self.service));self.bridge=Bridge(self.http)
        self.paid=q.PaidAudit(self.cfg,self.algo_key,self.log,self.bridge,self.clock)
    def tearDown(self):self.paid.close();self.http.close();self.patch.stop();self.tmp.cleanup()
    def event(self,kind='activation',data=None,n='a'):
        eid=n*64;self.paid.enqueue(eid,kind,data or {'strategy':'EMA34/89','timeframe':'5m'});return eid

class PaymentsTests(Fixture):
    def test_real_sdk_signature_and_http402_to_receipt(self):
        eid=self.event();r=self.paid.pay(eid)
        self.assertEqual(r['id'],eid);self.assertEqual(self.facilitator.settlements,1)
        self.assertEqual(self.paid.pay(eid),r);self.assertEqual(self.facilitator.settlements,1)
    def test_restart_does_not_charge_same_event_twice(self):
        eid=self.event();first=self.paid.pay(eid);self.paid.close()
        self.paid=q.PaidAudit(self.cfg,self.algo_key,self.log,self.bridge,self.clock)
        self.assertEqual(self.paid.pay(eid),first);self.assertEqual(self.facilitator.settlements,1)
    def test_lost_response_retries_exact_payment_once(self):
        eid=self.event();self.bridge.lose_once=True
        with self.assertRaises(q.TemporaryError):self.paid.pay(eid)
        header=self.paid.db.execute('SELECT header FROM pagos WHERE id=?',(eid,)).fetchone()[0]
        self.paid.pay(eid)
        self.assertEqual(self.facilitator.settlements,1)
        self.assertEqual(self.paid.db.execute('SELECT header FROM pagos WHERE id=?',(eid,)).fetchone()[0],header)
    def test_settlement_timeout_recovers_onchain(self):
        eid=self.event();self.facilitator.lose_response=True
        with self.assertRaises(q.TemporaryError):self.paid.pay(eid)
        self.paid.pay(eid);self.assertEqual(self.facilitator.settlements,1)
    def test_daily_budget_includes_uncertain_reservations(self):
        self.cfg.daily=self.cfg.price;self.paid.reserve(self.event())
        with self.assertRaises(q.Rejected):self.paid.reserve(self.event(n='b'))
        self.paid.reserve('a'*64)
    def test_total_budget_does_not_reset_next_day(self):
        self.cfg.total=self.cfg.price;self.paid.reserve(self.event())
        self.paid.now=lambda:self.clock()+86400000
        with self.assertRaises(q.Rejected):self.paid.reserve(self.event(n='c'))
    def test_price_recipient_asset_and_network_substitution_refused(self):
        original=self.service.quote().model_dump(by_alias=True,exclude_none=True)
        for field,value in [('amount','999999'),('payTo',self.payer),('asset','0'),('network','algorand:other'),('extra',{'feePayer':self.payer})]:
            with self.subTest(field=field):
                quote=copy.deepcopy(original);quote['accepts'][0][field]=value
                with self.assertRaises(q.SafetyError):q.build_payment_header(self.cfg,self.algo_key,quote)
    def test_signer_refuses_rekey_and_closeout(self):
        signer=q.GuardedAlgoSigner(self.algo_key,self.cfg)
        for kwargs in ({'rekey_to':self.cfg.pay_to},{'close_assets_to':self.cfg.pay_to}):
            txn=transaction.AssetTransferTxn(self.payer,self.algo.suggested_params(),self.cfg.pay_to,self.cfg.price,int(self.cfg.asset),**kwargs)
            with self.assertRaises(q.SafetyError):signer.sign_transactions([base64.b64decode(encoding.msgpack_encode(txn))],[0])
    def test_invalid_payer_signature_never_charges(self):
        eid=self.event();body=json.loads(self.paid.db.execute('SELECT body FROM pagos WHERE id=?',(eid,)).fetchone()[0])
        with self.assertRaises(q.ServiceError):self.service.handle(body,self.payer,'invalid')
        self.assertEqual(self.facilitator.settlements,0)
    def test_payment_replay_for_different_event_refused(self):
        eid=self.event();self.paid.pay(eid)
        body,header=self.paid.db.execute('SELECT body,header FROM pagos WHERE id=?',(eid,)).fetchone()
        body=json.loads(body);body['id']='b'*64
        with self.assertRaises(q.ServiceError) as caught:self.service.handle(body,self.payer,sign_bytes(q.canonical(body).encode(),self.algo_key),header)
        self.assertEqual(caught.exception.status,409);self.assertEqual(self.facilitator.settlements,1)
    def test_entry_audit_rejects_stale_signal_before_charge(self):
        data={'signal_ms':self.clock()-120000,'side':1,'qty':.1,'price':60000.,'stop':56000.,'rsi':62.,'funding':0.,'leverage':5,'is_add':False,'level':0}
        eid=self.event('entry_audit',data)
        with self.assertRaises(q.TemporaryError):self.paid.pay(eid)
        self.assertEqual(self.facilitator.settlements,0)
    def test_entry_audit_supplies_independent_risk_check(self):
        data={'signal_ms':self.clock(),'side':1,'qty':.1,'price':60000.,'stop':56000.,'rsi':62.,'funding':0.,'leverage':5,'is_add':False,'level':0}
        r=self.paid.pay(self.event('entry_audit',data));self.assertTrue(r['result']['allowed'])
        self.assertEqual(r['result']['notional_usdc'],6000.)
    def test_exit_receipt_not_claimed_as_verified_exchange_fill(self):
        data={'opened_ms':self.clock()-300000,'closed_ms':self.clock(),'side':1,'qty':.1,'price':60000.,'realized_before_costs':10.}
        r=self.paid.pay(self.event('exit_receipt',data));self.assertEqual(r['result']['status'],'client_reported')

class SetupTests(Fixture):
    def test_missing_production_setup_blocks(self):
        with self.assertRaises(q.SafetyError):self.cfg.live_ready()
    def test_empty_and_invalid_keys_never_advance(self):
        prompt=Mock(side_effect=['','wrong',HL_KEY])
        key=q.required_input('API: ',lambda x:q.validate_hl_key(x,MAIN),prompt)
        self.assertEqual(key,HL_KEY);self.assertEqual(prompt.call_count,3)
    def test_main_wallet_key_refused(self):
        with self.assertRaises(q.SafetyError):q.validate_hl_key(HL_KEY,EthAccount.from_key(HL_KEY).address)
    def test_expired_or_wrong_api_wallet_is_refused(self):
        api=Mock();api.post.return_value=[{'address':EthAccount.from_key(HL_KEY).address,'validUntil':1}]
        with self.assertRaises(q.SafetyError):q.verify_hl_authority(api,MAIN,HL_KEY)
        api.post.return_value=[{'address':'0x'+'a'*40,'validUntil':int(time.time()*1000)+1000000}]
        with self.assertRaises(q.SafetyError):q.verify_hl_authority(api,MAIN,HL_KEY)
    def test_authorized_api_wallet_requires_futures_balance(self):
        api=Mock();api.post.return_value=[{'address':EthAccount.from_key(HL_KEY).address,'validUntil':int(time.time()*1000)+1000000}]
        api.account.return_value={'marginSummary':{'accountValue':'0'},'withdrawable':'0','assetPositions':[]}
        with self.assertRaises(q.SafetyError):q.verify_hl_authority(api,MAIN,HL_KEY)
        api.account.return_value['marginSummary']['accountValue']='1000';api.account.return_value['withdrawable']='1000'
        self.assertEqual(q.verify_hl_authority(api,MAIN,HL_KEY).equity,1000)
    def test_plaintext_secret_configuration_refused(self):
        with self.cfg.path.open('a') as f:f.write('\nprivate_key = do-not-store\n')
        with self.assertRaises(q.SafetyError):q.Settings(self.cfg.path)
    def test_successful_wizard_stores_only_public_config(self):
        vault=Vault();api=Mock();api.post.side_effect=lambda payload: 100
        password=Mock(side_effect=[HL_KEY,self.algo_key])
        terms=Mock(status_code=200);terms.json.return_value=self.cfg.terms()
        with patch.object(q,'verify_hl_authority',return_value=q.Account(1000,1000)),patch.object(q.requests,'get',return_value=terms):
            result=q.configure_wizard(self.cfg,input_fn=lambda _:'ACCEPT',password_fn=password,vault=vault,api=api)
        result=q.Settings(result.path);result.live_ready()
        text=result.path.read_text();self.assertNotIn(HL_KEY,text);self.assertNotIn(self.algo_key,text)
        self.assertEqual(q.load_secrets(result,vault),(HL_KEY,self.algo_key))
    def test_rejected_fees_leave_setup_incomplete(self):
        vault=Vault();api=Mock();terms=Mock(status_code=200);terms.json.return_value=self.cfg.terms()
        with patch.object(q,'verify_hl_authority',return_value=q.Account(1000,1000)),patch.object(q.requests,'get',return_value=terms):
            with self.assertRaises(q.SafetyError):q.configure_wizard(self.cfg,input_fn=lambda _:'NO',password_fn=Mock(side_effect=[HL_KEY,self.algo_key]),vault=vault,api=api)
        self.assertEqual(q.Settings(self.cfg.path).get('setup','completed'),'no')
    def test_terms_changed_require_fresh_consent(self):
        self.cfg.ini['setup'].update(completed='si',acceptance_sha256=self.cfg.consent_hash());self.cfg.save()
        q.Settings(self.cfg.path).live_ready()
        self.cfg.ini['billing']['service_price_usdc']='0.02';self.cfg.save()
        with self.assertRaises(q.SafetyError):q.Settings(self.cfg.path).live_ready()
    def test_secure_store_failure_does_not_fall_back_to_file(self):
        vault=Mock();vault.set_password.side_effect=OSError()
        with self.assertRaises(q.SafetyError):q.store_verified(vault,'test','test',HL_KEY)
    def test_builder_authorization_matches_sdk_eip712(self):
        from eth_account.messages import encode_typed_data
        action,typed=q.builder_typed_action(self.cfg,1000)
        sig=EthAccount.sign_message(encode_typed_data(full_message=typed),HL_KEY)
        recovered=EthAccount.recover_message(encode_typed_data(full_message=typed),signature=sig.signature)
        self.assertEqual(recovered,EthAccount.from_key(HL_KEY).address)
        self.assertEqual(action['maxFeeRate'],'0.1%')

class ExecutionTests(Fixture):
    def test_builder_fee_only_on_close_not_entry(self):
        api=Mock();api.post.return_value=100;exchange=Mock();exchange.market_open.return_value={'status':'ok','response':{'data':{'statuses':[{'filled':{'oid':1,'totalSz':'.1','avgPx':'60000'}}]}}}
        broker=q.QTSBroker(api,MAIN,exchange,{'universe':[{'name':'BTC','szDecimals':5,'maxLeverage':40}]},self.clock,cfg=self.cfg)
        base={'cloid':'0x'+'a'*32,'expires':self.clock()+30000,'side':1,'qty':.1,'reference':60000.}
        broker.submit(dict(base,kind='entry'))
        self.assertNotIn('builder',exchange.market_open.call_args.kwargs)
        exchange.order.return_value={'status':'ok','response':{'data':{'statuses':[{'resting':{'oid':2}}]}}}
        broker.submit(dict(base,kind='stop',stop=56000.,old_oid=None))
        self.assertEqual(exchange.order.call_args.kwargs['builder'],{'b':self.cfg.builder,'f':100})
        broker.submit(dict(base,kind='stop',stop=55000.,old_oid=2))
        self.assertNotIn('builder',exchange.modify_order.call_args.kwargs)
        broker.submit(dict(base,kind='close',reason='protection'))
        self.assertTrue(exchange.order.call_args.kwargs['reduce_only']);self.assertEqual(exchange.order.call_args.kwargs['builder'],{'b':self.cfg.builder,'f':100})
    def test_close_and_fresh_stop_still_submit_without_builder_when_unauthorized(self):
        api=Mock();api.post.return_value=0;exchange=Mock()
        exchange.order.return_value={'status':'ok','response':{'data':{'statuses':[{'resting':{'oid':9}}]}}}
        broker=q.QTSBroker(api,MAIN,exchange,{'universe':[{'name':'BTC','szDecimals':5,'maxLeverage':40}]},self.clock,cfg=self.cfg)
        base={'cloid':'0x'+'a'*32,'expires':self.clock()+30000,'side':1,'qty':.1,'reference':60000.}
        r=broker.submit(dict(base,kind='stop',stop=56000.,old_oid=None))
        self.assertNotIn('builder',exchange.order.call_args.kwargs);self.assertEqual(r.status,'open')
        r=broker.submit(dict(base,kind='close',reason='protection'))
        self.assertNotIn('builder',exchange.order.call_args.kwargs);self.assertEqual(r.status,'open')
    def test_revoked_builder_fee_refuses_new_entry(self):
        api=Mock();api.post.return_value=0;exchange=Mock()
        broker=q.QTSBroker(api,MAIN,exchange,{'universe':[{'name':'BTC','szDecimals':5,'maxLeverage':40}]},self.clock,cfg=self.cfg)
        r=broker.submit({'kind':'entry','cloid':'0x'+'a'*32})
        self.assertEqual(r.status,'rejected');exchange.market_open.assert_not_called()
    def test_log_flushes_during_execution_and_resets_on_restart(self):
        path=self.root/'log.txt';log=q.configure_log(path,'paper');log.info('OPEN\nSTOP')
        self.assertEqual(len(path.read_text().splitlines()),1)
        self.assertRegex(path.read_text(),r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}')
        log=q.configure_log(path,'paper');log.info('NEW SESSION')
        self.assertNotIn('OPEN',path.read_text())
        for h in log.handlers:h.close()
        log.handlers.clear()
    def test_size_is_proportional_and_not_multiplied_by_leverage(self):
        self.assertEqual(q.size_for_equity(1000,100,60000,5,1000,5,.00055),.01666)
        self.assertEqual(q.size_for_equity(2000,100,60000,5,2000,5,.00055),.03333)
    def test_payment_outage_does_not_prevent_native_stop_updates(self):
        class LivePaper(q.PaperBroker):
            mode='live'
            def builder_authorized(self):pass
        store=q.Store(self.root/'execution.sqlite3');now=[self.clock()]
        api=SimpleNamespace(quote=lambda:{'mid':price[0],'mark':price[0],'time':now[0]})
        price=[60000.];broker=LivePaper(api,store,lambda:now[0],10000.)
        settings=dict(self.cfg.settings);settings['stop_indicators']=q.DEFAULT_STOP_INDICATORS
        payment=Mock();payment.signal_confirmation.return_value=True
        a=q.QTSAgent(broker,store,self.cfg.management,self.cfg.stop,settings,self.log,lambda:now[0],payments=payment)
        def row():return {'time':now[0]//q.BAR_MS*q.BAR_MS,'close':price[0],'atr':100.,'rsi':62.,'funding':0.,'raw_long':2,'raw_short':0}
        payment.entry_signal.return_value=(0,2000.)
        a.poll();a.process_rows([row()]);now[0]+=q.BAR_MS+2000
        payment.entry_signal.return_value=(1,2000.);a.poll();a.process_rows([row()])
        old=a.state['position']['protected_stop'];payment.entry_signal.side_effect=q.TemporaryError('provider down')
        price[0]=66000.;now[0]+=q.BAR_MS;a.poll();a.process_rows([row()])
        self.assertGreater(a.state['position']['protected_stop'],old)
        self.assertEqual(a.state['position']['entry_legs'],1)
        price[0]=a.state['position']['protected_stop']-100;now[0]+=15000;a.poll()
        self.assertIsNone(a.state['position']);self.assertTrue(store.get('outbox_exit'))
        store.close()
    def test_native_stop_during_payment_cannot_reopen_as_addition(self):
        class LivePaper(q.PaperBroker):
            mode='live'
            def builder_authorized(self):pass
        store=q.Store(self.root/'during_payment.sqlite3');now=[self.clock()];price=[60000.]
        api=SimpleNamespace(quote=lambda:{'mid':price[0],'mark':price[0],'time':now[0]})
        broker=LivePaper(api,store,lambda:now[0],10000.);payment=Mock()
        a=q.QTSAgent(broker,store,self.cfg.management,self.cfg.stop,self.cfg.settings,self.log,lambda:now[0],payments=payment)
        def row():return {'time':now[0]//q.BAR_MS*q.BAR_MS,'close':price[0],'atr':100.,'rsi':62.,'funding':0.,'raw_long':2,'raw_short':0}
        payment.entry_signal.return_value=(0,2000.)
        a.poll();a.process_rows([row()]);now[0]+=q.BAR_MS+2000
        payment.entry_signal.return_value=(1,2000.);a.poll();a.process_rows([row()])
        def stop_while_paying(*args):
            stop=a.state['position']['protected_stop']
            broker.observe({'mid':stop-1,'mark':stop-1,'time':now[0]});return (1,2000.)
        payment.entry_signal.side_effect=stop_while_paying
        price[0]=62000.;now[0]+=q.BAR_MS;a.poll();a.process_rows([row()])
        self.assertEqual(broker.account().qty,0.)
        self.assertEqual(sum(x['command']['kind']=='entry' for x in broker.data['orders'].values()),1)
        a.poll();self.assertIsNone(a.state['position']);store.close()

class SponsoredPaymentTests(Fixture):
    def payload(self):
        header,_=q.build_payment_header(self.cfg,self.algo_key,self.service.quote().model_dump(by_alias=True,exclude_none=True))
        return PaymentPayload.model_validate(q.qts_unb64(header))
    def test_customer_signs_only_zero_fee_usdc_transfer(self):
        payload=self.payload();txns=q.validate_payment_payload(payload,self.cfg,self.payer)
        self.assertEqual(payload.payload['paymentIndex'],1)
        self.assertEqual(txns[0].sender,self.cfg.fee_payer);self.assertEqual(txns[0].fee,2000)
        self.assertEqual(txns[1].sender,self.payer);self.assertEqual(txns[1].fee,0)
        self.assertEqual(txns[1].amount,10000)
        self.assertIsInstance(encoding.msgpack_decode(payload.payload['paymentGroup'][0]),transaction.PaymentTxn)
        self.assertIsInstance(encoding.msgpack_decode(payload.payload['paymentGroup'][1]),transaction.SignedTransaction)
    def test_customer_needs_no_spendable_algo_after_activation(self):
        self.algo.account_info=lambda _: {'amount':200000,'min-balance':200000,'assets':[{'asset-id':int(self.cfg.asset),'amount':1000000}]}
        q.verify_algo_funds(self.cfg,self.algo_key)
        self.paid.pay(self.event());self.assertEqual(self.facilitator.settlements,1)
    def test_unactivated_wallet_blocks_without_attempting_swap(self):
        self.algo.account_info=lambda _: {'amount':0,'min-balance':100000,'assets':[]}
        with self.assertRaisesRegex(q.SafetyError,'activated|enabled'):
            q.verify_algo_funds(self.cfg,self.algo_key)
        self.assertEqual(self.facilitator.settlements,0)
    def test_sponsor_change_or_missing_sponsorship_is_rejected(self):
        original=self.service.quote().model_dump(by_alias=True,exclude_none=True)
        _,other=account.generate_account()
        for extra in ({},{'feePayer':other},{'feePayer':self.payer},{'feePayer':self.cfg.fee_payer,'other':True}):
            data=copy.deepcopy(original);data['accepts'][0]['extra']=extra
            with self.assertRaises(q.SafetyError):q.build_payment_header(self.cfg,self.algo_key,data)
    def test_sponsored_group_rejects_hidden_debits_and_permissions(self):
        original=q.validate_payment_payload(self.payload(),self.cfg,self.payer)
        for index,attribute,value in [(0,'amt',1),(0,'receiver',self.payer),(0,'fee',100000),
                                     (0,'rekey_to',self.payer),(1,'fee',1000),(1,'close_assets_to',self.cfg.pay_to),
                                     (1,'rekey_to',self.cfg.pay_to),(1,'amount',10001)]:
            with self.subTest(attribute=attribute,index=index):
                txns=copy.deepcopy(original);setattr(txns[index],attribute,value)
                for t in txns:t.group=None
                transaction.assign_group_id(txns)
                with self.assertRaises(q.SafetyError):q.check_sponsored_group(txns,self.cfg,self.payer)
    def test_group_hash_mismatch_is_rejected(self):
        txns=q.validate_payment_payload(self.payload(),self.cfg,self.payer)
        txns[0].note=b'changed'
        with self.assertRaises(q.SafetyError):q.check_sponsored_group(txns,self.cfg,self.payer)
    def test_receipt_can_report_first_group_transaction(self):
        settle=self.facilitator.settle
        def first_transaction(payload,req):
            result=settle(payload,req)
            first=encoding.msgpack_decode(payload.payload['paymentGroup'][0]).get_txid()
            return SettleResponse(success=True,transaction=first,network=req.network,payer=result.payer)
        self.facilitator.settle=first_transaction
        result=self.paid.pay(self.event())
        self.assertEqual(result['amount'],'10000');self.assertEqual(self.facilitator.settlements,1)
    def test_customer_config_cannot_replace_developer_address(self):
        self.cfg.ini['billing']['wallet_hyperliquid_commission']='0x'+'9'*40;self.cfg.save()
        with self.assertRaisesRegex(q.SafetyError,'cannot override'):q.Settings(self.cfg.path)
    def test_api_requires_payment_before_delivering_result(self):
        eid=self.event();body=json.loads(self.paid.db.execute('SELECT body FROM pagos WHERE id=?',(eid,)).fetchone()[0])
        response=self.paid.request(body)
        self.assertEqual(response.status_code,402);self.assertNotIn('result',response.json())
        self.assertEqual(self.facilitator.settlements,0)

class SignalFeedTests(Fixture):
    def app_with_feed(self,table):
        from fastapi.testclient import TestClient
        feed=SimpleNamespace(decision=lambda ms:table.get(ms))
        return TestClient(q.make_service_app(self.cfg,self.service,feed))
    def signed(self,signal_ms):
        body={'payer':self.payer,'signal_ms':signal_ms};raw=q.canonical(body).encode()
        return raw,{'X-QTS-Payer':self.payer,'X-QTS-Signature':sign_bytes(raw,self.algo_key)}
    def test_signal_requires_activation_then_returns_the_decision(self):
        ms=self.clock()//q.BAR_MS*q.BAR_MS
        with self.app_with_feed({ms:(1,123.5)}) as http:
            raw,headers=self.signed(ms)
            self.assertEqual(http.post('/v1/signal/BTC',content=raw,headers=headers).status_code,402)
            self.paid.pay(self.event())
            r=http.post('/v1/signal/BTC',content=raw,headers=headers)
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()['signal'],1);self.assertEqual(r.json()['risk_atr'],123.5)
            self.assertEqual(http.post('/v1/signal/ETH',content=raw,headers=headers).status_code,404)
    def test_signal_rejects_forged_signature_stale_and_unknown_candle(self):
        ms=self.clock()//q.BAR_MS*q.BAR_MS
        with self.app_with_feed({ms:(1,123.5)}) as http:
            self.paid.pay(self.event())
            raw,_=self.signed(ms)
            self.assertEqual(http.post('/v1/signal/BTC',content=raw,
                headers={'X-QTS-Payer':self.payer,'X-QTS-Signature':'bad'}).status_code,401)
            sraw,sheaders=self.signed(self.clock()-120_000)
            self.assertEqual(http.post('/v1/signal/BTC',content=sraw,headers=sheaders).status_code,422)
    def test_unknown_candle_is_not_an_entry(self):
        ms=self.clock()//q.BAR_MS*q.BAR_MS
        with self.app_with_feed({}) as http:
            self.paid.pay(self.event())
            raw,headers=self.signed(ms)
            self.assertEqual(http.post('/v1/signal/BTC',content=raw,headers=headers).status_code,422)

class LocalAgentCannotDecideEntriesTests(Fixture):
    def test_client_module_has_no_entry_strategy(self):
        for gone in ('context_signals','features','build_features','EntryConfig','DEFAULT_ENTRY','FeatureCache'):
            self.assertFalse(hasattr(q,gone),f'{gone} must not ship to customers')
    def test_market_rows_carry_no_entry_signal(self):
        import inspect,ast
        tree=ast.parse(inspect.getsource(q.Market.sync).strip())
        keys={k.value for n in ast.walk(tree) if isinstance(n,ast.Dict)
              for k in n.keys if isinstance(k,ast.Constant) and isinstance(k.value,str)}
        self.assertNotIn('signal',keys);self.assertNotIn('risk_atr',keys)
        self.assertIn('raw_long',keys)
    def test_agent_does_not_enter_without_the_service(self):
        store=q.Store(self.root/'nofallback.sqlite3');now=[self.clock()]
        api=SimpleNamespace(quote=lambda:{'mid':60000.,'mark':60000.,'time':now[0]})
        broker=q.PaperBroker(api,store,lambda:now[0],10000.)
        a=q.Agent(broker,store,self.cfg.management,self.cfg.stop,self.cfg.settings,self.log,lambda:now[0])
        row={'time':now[0]//q.BAR_MS*q.BAR_MS,'close':60000.,'atr':100.,'rsi':62.,'funding':0.,'raw_long':2,'raw_short':0}
        a.poll();a.process_rows([row]);now[0]+=q.BAR_MS+2000;a.poll()
        a.process_rows([dict(row,time=now[0]//q.BAR_MS*q.BAR_MS)])
        self.assertIsNone(a.state['position'])
        store.close()

if __name__=='__main__':unittest.main()
