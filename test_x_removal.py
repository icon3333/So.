import tempfile, threading, unittest
from pathlib import Path
from unittest.mock import Mock
from server import App
from triage_state import Store

class RemovalTests(unittest.TestCase):
    def test_karakeep_stages_only_native_origin_with_same_url(self):
        with tempfile.TemporaryDirectory() as d:
            app=App.__new__(App)
            url='https://x.com/u/status/123'
            app.articles=[{'id':'ir-id','url':url,'origin':'inoreader'}, {'id':'x:acct:123','url':url,'origin':'x','postId':'123','originAccountId':'acct'}]
            app.store=Store(Path(d)/'state.json',app.articles,x_account_id='acct')
            app.operation_lock=threading.RLock(); app.karakeep=Mock()
            app.karakeep.save.return_value={'bookmarkId':'k','url':url}
            app.payload=lambda: {'state':app.store.view()}
            app.save_to_karakeep(0,'x:acct:123')
            state=app.store.view()
            self.assertIn('x:acct:123',state['decisions'])
            self.assertNotIn('ir-id',state['decisions'])
            self.assertIn('x:acct:123',state['karakeepSaved'])

class RemovalSafetyTests(unittest.TestCase):
    def setUp(self):
        import json, time
        from x_connection import SCOPES
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        (self.root/'articles.json').write_text(json.dumps({'fetchedAt':'fixture', 'sameUrlGroups':[],
            'articles':[{'id':'ir-id','ref':'ir','url':'https://x.com/i/status/101','title':'IR'}]}))
        self.app = App(self.root,'http://127.0.0.1:8769')
        self.app.x_bookmarks.merge_page('12345',[{'postId':'101','url':'https://x.com/i/status/101','title':'Native'}],None,complete=True)
        self.app.refresh_catalog()
        self.app.x_connection.tokens={'access_token':'synthetic','expires_at':time.time()+60,'scopes':list(SCOPES)}
        self.app.x_connection.account={'id':'12345'}
        self.app.connection=Mock()
        self.app.connection.status.return_value={'connected':True}
        self.ir_saved=True
        def ir_call(operation, ids=None):
            if operation=='remove': self.ir_saved=False
            return {'starredIds':['ir-id'] if self.ir_saved else [],'checkedAt':'fixture'}
        self.app.connection.call.side_effect=ir_call
        self.app.store.decide(0,['ir-id','x:12345:101'],'remove')
        self.approval={'mutations':1,'verification_posts':1,'pages':1,'usd':.015}
        self.members={'101'}
        self.events=[]

    def transport(self, url, *args, **kwargs):
        method=kwargs.get('method','GET')
        self.events.append((method,url))
        if method=='DELETE':
            self.members.discard(url.rsplit('/',1)[-1])
            return {'data':{}}
        return {'data':[{'id':p} for p in sorted(self.members)],'meta':{'result_count':len(self.members)}}

    def confirm(self, preview, approval=None):
        return self.app.confirm_removals(preview['version'],[p['id'] for p in preview['items']],
            preview['confirmationToken'],preview['catalogRevision'],self.approval if approval is None else approval)

    def test_mixed_confirmation_routes_same_url_and_does_not_double_reserve(self):
        from unittest.mock import patch
        preview=self.app.preview_removals()
        with patch('x_connection._safe_json_request',side_effect=self.transport):
            result=self.confirm(preview)
        self.assertEqual(set(result['result']['confirmed']),{'ir-id','x:12345:101'})
        self.assertEqual(result['result']['unconfirmed'],[])
        self.assertEqual([c.args for c in self.app.connection.call.call_args_list if c.args[0]=='remove'],[('remove',['ir-id'])])
        self.assertEqual([m for m,u in self.events],['DELETE','GET'])
        self.assertTrue(result['result']['verificationComplete'])

    def test_underfunded_verification_fails_before_any_provider_write(self):
        from unittest.mock import patch
        from triage_state import StateError
        with patch('x_connection._safe_json_request') as request:
            with self.assertRaises(StateError):
                self.confirm(self.app.preview_removals(),{'mutations':1,'verification_posts':0,'pages':0,'usd':.01})
        request.assert_not_called()
        self.app.connection.call.assert_not_called()

    def test_stale_catalog_and_single_use_replay_never_repeat_writes(self):
        from unittest.mock import patch
        from triage_state import StateError
        old=self.app.preview_removals()
        self.app.x_bookmarks.merge_page('12345',[{'postId':'102'}],None,complete=True)
        with patch('x_connection._safe_json_request',side_effect=self.transport):
            with self.assertRaises(StateError): self.confirm(old)
            self.app.refresh_catalog()
            preview=self.app.preview_removals()
            self.confirm(preview)
            count=len(self.events)
            with self.assertRaises(StateError): self.confirm(preview)
            self.assertEqual(len(self.events),count)

    def test_lost_delete_response_is_read_back_without_delete_retry(self):
        from unittest.mock import patch
        from triage_state import StateError
        def lost(url,*args,**kwargs):
            result=self.transport(url,*args,**kwargs)
            if kwargs.get('method')=='DELETE': raise StateError('fixture timeout',502)
            return result
        with patch('x_connection._safe_json_request',side_effect=lost):
            result=self.confirm(self.app.preview_removals())
        self.assertEqual([m for m,u in self.events],['DELETE','GET'])
        self.assertIn('x:12345:101',result['result']['confirmed'])
        self.assertNotIn('x:12345:101',result['result']['unconfirmed'])

    def test_partial_readback_keeps_native_pending_and_never_reuses_approval(self):
        from unittest.mock import patch
        def partial(url,*args,**kwargs):
            result=self.transport(url,*args,**kwargs)
            if kwargs.get('method')!='DELETE':
                return {'data':[{'id':'999'}],'meta':{'next_token':'more','result_count':1}}
            return result
        with patch('x_connection._safe_json_request',side_effect=partial):
            result=self.confirm(self.app.preview_removals())
        self.assertEqual(result['result']['confirmed'],['ir-id'])
        self.assertEqual(result['result']['unconfirmed'],['x:12345:101'])
        self.assertTrue(self.app.store.is_saved('x:12345:101'))
        self.assertEqual([m for m,u in self.events],['DELETE','GET'])

    def test_readonly_check_never_deletes_and_partial_errors_never_prove_absence(self):
        from unittest.mock import patch
        with patch('x_connection._safe_json_request',return_value={'data':[], 'errors':[{'title':'partial'}],'meta':{'result_count':0}}) as request:
            result=self.app.check_x_membership({'verification_posts':1,'pages':1,'usd':.005})
        self.assertFalse(result['xMembership']['complete'])
        self.assertTrue(self.app.store.is_saved('x:12345:101'))
        self.assertTrue(all(c.kwargs.get('method')!='DELETE' for c in request.call_args_list))

    def test_missing_readback_result_count_never_proves_absence(self):
        from unittest.mock import patch
        with patch('x_connection._safe_json_request', return_value={'data':[{'id':'999'}], 'meta':{}}):
            result = self.app.check_x_membership({'verification_posts':1,'pages':1,'usd':.005})
        self.assertFalse(result['xMembership']['complete'])
        self.assertTrue(self.app.store.is_saved('x:12345:101'))
        self.assertNotIn('starredIds', result['xMembership'])

    def test_disconnected_x_subgroup_stays_pending_without_cross_origin_fallback(self):
        from unittest.mock import patch
        self.app.x_connection.disconnect()
        with patch('x_connection._safe_json_request') as request:
            result=self.confirm(self.app.preview_removals(),{})
        request.assert_not_called()
        self.assertEqual(result['result']['confirmed'],['ir-id'])
        self.assertEqual(result['result']['unconfirmed'],['x:12345:101'])

    def test_changed_session_and_malformed_confirmations_have_no_side_effects(self):
        from unittest.mock import patch
        from triage_state import StateError
        preview=self.app.preview_removals()
        with patch('x_connection._safe_json_request') as request:
            for token,ids,revision in [
                ('wrong',['ir-id','x:12345:101'],preview['catalogRevision']),
                (preview['confirmationToken'],['ir-id'],preview['catalogRevision']),
                (preview['confirmationToken'],['ir-id','ir-id'],preview['catalogRevision']),
                (preview['confirmationToken'],['ir-id','x:12345:101'],True)]:
                with self.assertRaises(StateError):
                    self.app.confirm_removals(preview['version'],ids,token,revision,self.approval)
            self.app.x_connection.tokens={**self.app.x_connection.tokens,'access_token':'other-fixture-session'}
            with self.assertRaises(StateError): self.confirm(preview)
        request.assert_not_called()
        self.app.connection.call.assert_not_called()

    def test_consumed_nonce_blocks_replay_even_without_state_version_change(self):
        from unittest.mock import patch
        from triage_state import StateError
        self.app.connection.status.return_value={'connected':False}
        preview=self.app.preview_removals()
        def partial(url,*args,**kwargs):
            result=self.transport(url,*args,**kwargs)
            return result if kwargs.get('method')=='DELETE' else {'data':[],'errors':[{'title':'partial'}],'meta':{}}
        with patch('x_connection._safe_json_request',side_effect=partial):
            self.confirm(preview)
            self.assertEqual(self.app.store.view()['version'],preview['version'])
            count=len(self.events)
            with self.assertRaises(StateError): self.confirm(preview)
            self.assertEqual(len(self.events),count)

    def test_http_preview_and_confirm_bind_nonce_and_reject_legacy_write_body(self):
        import json,threading
        from unittest.mock import patch
        from http.client import HTTPConnection
        from http.server import ThreadingHTTPServer
        from server import Handler
        httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.addCleanup(httpd.server_close)
        self.app.origin='http://127.0.0.1:%s'%httpd.server_port
        httpd.app=self.app
        thread=threading.Thread(target=httpd.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(thread.join,2)
        self.addCleanup(httpd.shutdown)
        def request(method,path,body=None):
            connection=HTTPConnection('127.0.0.1',httpd.server_port,timeout=2)
            headers={'Origin':self.app.origin,'Content-Type':'application/json','X-Triage-Request':'1'}
            connection.request(method,path,None if body is None else json.dumps(body),headers)
            response=connection.getresponse()
            data=json.loads(response.read()); status=response.status
            connection.close()
            return status,data
        with patch('x_connection._safe_json_request',side_effect=self.transport) as transport:
            _,preview=request('GET','/api/sync/preview')
            ids=[p['id'] for p in preview['items']]
            self.assertEqual(request('POST','/api/sync',{'version':preview['version'],'ids':ids})[0],409)
            transport.assert_not_called()
            status,result=request('POST','/api/sync',{**self.approval,'version':preview['version'],'ids':ids,
                'confirmationToken':preview['confirmationToken'],'catalogRevision':preview['catalogRevision']})
        self.assertEqual(status,200)
        self.assertEqual(set(result['result']['confirmed']),set(ids))

if __name__=='__main__': unittest.main()
