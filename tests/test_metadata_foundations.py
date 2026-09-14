import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ipaper.database import connection
from ipaper.database.models import SCHEMA_SCRIPT
from ipaper.database.dao.paper_dao import PaperDAO
from ipaper.metadata.store import MetadataStore
from ipaper.metadata.model import MetadataError, bibtex, validate_patch
from ipaper.metadata.providers import crossref_records, arxiv_records, dblp_records, openreview_records, clues, match
from ipaper.security.identity import Identity, run_as_identity

OWNER='00000000-0000-0000-0000-000000000011'
OTHER='00000000-0000-0000-0000-000000000022'
PAPER='00000000-0000-0000-0000-000000000033'


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Path(self.temp.name)/'db.sqlite'
        sqlite3.connect(self.db).executescript(SCHEMA_SCRIPT).close()
        connection.close_db();self.patch=patch.object(connection,'DB_PATH',str(self.db));self.patch.start()
        self.identity=Identity(OWNER,'user','user');self.store=MetadataStore(self.db,OWNER)
        self.paper={'id':PAPER,'title':'Original title','authors':'A. Example','file_path':'/data/papers/.users/'+OWNER+'/_ReadingListTemp/stable.pdf','filename':'stable.pdf','year':'2020','doi':'','notes':'keep','category_id':'reading_list_temp'}
        self.act(PaperDAO.save_paper,self.paper)
    def tearDown(self):connection.close_db();self.patch.stop();self.temp.cleanup()
    def act(self,fn,*args,**kwargs):return run_as_identity(self.identity,fn,*args,**kwargs)
    def test_manual_clear_survives_stale_save_and_background(self):
        old=self.act(PaperDAO.get_paper,PAPER)
        view=self.store.get(PAPER)
        view=self.store.edit(PAPER,{'title':'Correct title','abstract':'','doi':''},view['revision'])
        old.update(authors='Wrong person',abstract='Wrong abstract',read_time=12)
        self.act(PaperDAO.save_paper,old)
        saved=self.act(PaperDAO.get_paper,PAPER)
        self.assertEqual(saved['title'],'Correct title');self.assertEqual(saved['authors'],'A. Example');self.assertEqual(saved['abstract'],'')
        record={'provider':'crossref','url':'https://doi.org/10.1000/test','fields':{'abstract':'Unexpected','doi':'10.1000/test','affiliation':'Confirmed institution'}}
        self.assertEqual(self.store.apply(PAPER,record,view['revision']),['affiliation'])
        self.assertEqual(self.store.get(PAPER)['fields']['doi'],'')
        self.assertEqual(saved['file_path'],self.paper['file_path']);self.assertEqual(saved['filename'],'stable.pdf')
    def test_late_revision_rejected_and_failed_transaction_keeps_all_fields(self):
        before=self.store.get(PAPER);self.store.edit(PAPER,{'authors':'New author'},before['revision'])
        with self.assertRaises(MetadataError):self.store.edit(PAPER,{'title':'Late'},before['revision'])
        with patch('ipaper.metadata.store._save_head',side_effect=sqlite3.OperationalError('simulated')):
            with self.assertRaises(sqlite3.OperationalError):self.store.edit(PAPER,{'title':'Must roll back'})
        self.assertEqual(self.act(PaperDAO.get_paper,PAPER)['title'],'Original title')
    def test_missing_old_extra_fields_restored_after_rollback_but_manual_old_edits_kept(self):
        v=self.store.edit(PAPER,{'doi':'10.1000/test','author_list':[{'name':'Research Group','kind':'organization'}]})
        with sqlite3.connect(self.db) as db:
            db.execute('UPDATE papers SET title=?,authors=?,metadata=? WHERE id=?',('Old version edit','Research Group',json.dumps({'filename':'stable.pdf','year':'2020','notes':'new old-version note'}),PAPER))
        self.store.reconcile();now=self.store.get(PAPER)
        self.assertEqual(now['fields']['title'],'Old version edit');self.assertTrue(now['provenance']['title']['manual'])
        self.assertEqual(now['fields']['doi'],'10.1000/test');self.assertEqual(len(now['fields']['author_list']),1)
        self.assertEqual(self.act(PaperDAO.get_paper,PAPER)['notes'],'new old-version note')
    def test_owner_batch_idempotency_and_daily_exclusion(self):
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM metadata_items').fetchone()[0],1)
        a=self.store.create([PAPER]);b=self.store.create([PAPER]);self.assertEqual(a,b)
        with self.assertRaises(MetadataError):MetadataStore(self.db,OTHER).get(PAPER)
        daily={**self.paper,'id':'00000000-0000-0000-0000-000000000044','is_daily':True,'file_path':'/data/papers/.users/'+OWNER+'/.daily_arxiv_temp/test.pdf'}
        self.act(PaperDAO.save_paper,daily)
        self.assertEqual(self.store.selection({}),[PAPER])
        with self.assertRaises(MetadataError):self.store.create([daily['id']])
        self.store.cancel(a);self.assertEqual(self.store.batch(a)['status'],'cancelled')
    def test_bibtex_unicode_escaping_and_current_fields(self):
        fields=validate_patch({'title':'A & B {实验}','author_list':[{'name':'ACME Research','kind':'organization'},{'name':'王一','kind':'person'}],'year':'2026','doi':'https://doi.org/10.1000/ABC','venue_type':'conference','journal':'TestConf'})
        result=bibtex(PAPER,fields)
        self.assertIn('A \\& B \\{实验\\}',result);self.assertIn('{ACME Research} and 王一',result);self.assertIn('booktitle = {TestConf}',result);self.assertIn('10.1000/abc',result)
        for payload in ({'github':'javascript:alert(1)'},{'title':3},{'file_path':'/tmp/a'},{'arxiv_id':'nope'}):
            with self.assertRaises(MetadataError):validate_patch(payload)
    def test_reference_doi_and_same_title_first_hit_are_not_identity(self):
        data={'message':{'items':[{'DOI':'10.1000/wrong','title':['Other paper'],'author':[{'given':'Wrong','family':'Author'}]},{'DOI':'10.1000/right','title':['Original title'],'author':[{'given':'A.','family':'Example'}]}]}}
        candidates=crossref_records(data)
        clue=clues({'title':'Original title','authors':'A. Example'},{'first_page_text':'Original title\nA. Example\nAbstract\nResearch\nReferences\n10.1000/wrong'})
        self.assertEqual(clue['doi'],'');self.assertNotEqual(match(candidates[0],clue),'verified');self.assertEqual(match(candidates[1],clue),'verified')
        self.assertNotEqual(match(candidates[1],{'title':'Original title','authors':'Someone else'}),'verified')
    def test_parsers_keep_arxiv_version_and_do_not_promote_submission(self):
        records=arxiv_records({'entries':[{'id':'http://arxiv.org/abs/1706.03762v1','title':'Attention Is All You Need','published':'2017-06-12T00:00:00Z','updated':'2017-06-12T00:00:00Z','summary':'Author abstract','authors':[{'name':'Author'}]}]})
        self.assertEqual(records[0]['fields']['arxiv_version'],'1');self.assertEqual(records[0]['fields']['venue_type'],'preprint')
        item={'id':'abc','content':{'title':{'value':'A paper'},'abstract':{'value':'Abstract'},'venue':{'value':'ICLR 2026 Submission'},'venueid':{'value':'ICLR.cc/2026/Conference/Submission'}}}
        records=openreview_records({'notes':[item,{**item,'replyto':'abc'}]})
        self.assertEqual(len(records),1);self.assertEqual(records[0]['fields']['journal'],'');self.assertEqual(records[0]['fields']['venue_type'],'submission')


def test_arxiv_crossref_publication_fields_do_not_replace_preprint_text():
    from ipaper.metadata.providers import combine_publication
    preprint={'provider':'arxiv','url':'https://arxiv.org/abs/2401.00001v1','externalId':'2401.00001','fields':{'doi':'10.9999/a','title':'Reliable systems','author_list':[{'name':'Ada Example'},{'name':'Bo Example'}],'abstract':'Version one abstract','year':'2024','arxiv_version':'1'},'relations':{}}
    publication={'provider':'crossref','url':'https://doi.org/10.9999/a','externalId':'10.9999/a','fields':{'doi':'10.9999/a','title':'Reliable systems','author_list':[{'name':'Ada Example'},{'name':'Bo Example'}],'abstract':'Publication abstract','year':'2025','journal':'Journal of Examples','published_date':'2025-02-01','venue_type':'journal'}}
    merged=combine_publication([preprint,publication])
    assert len(merged)==1 and merged[0]['fields']['abstract']=='Version one abstract'
    assert merged[0]['fields']['year']=='2025' and merged[0]['fields']['arxiv_version']=='1'
    assert merged[0]['fieldSources']['journal']['provider']=='crossref'
    publication['fields']['title']='An extended different method'
    assert len(combine_publication([preprint,publication]))==2
