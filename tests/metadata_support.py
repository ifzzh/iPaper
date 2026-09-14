"""Explicit fake bibliographic provider for browser acceptance, never installed in production."""
from .test_metadata_jobs import good_response


def install(application):
    service=application.extensions['metadata']
    calls=[]
    class HTTP:
        def __init__(self,store,before,check):self.before=before;self.check=check
        def get(self,provider,path,params=None,**kwargs):
            self.check();self.before();calls.append({'provider':provider,'path':path})
            title=(params or {}).get('q') or (params or {}).get('query.bibliographic','Synthetic reader validation').split(' Chen Wang')[0]
            if provider=='dblp':return good_response(title)
            return {'message':{'items':[{'DOI':'10.9999/synthetic','title':[title],
                'author':[{'given':'Chen','family':'Wang'},{'given':'Maya','family':'Lee'},{'given':'Alex','family':'Kim'}],
                'container-title':['合成会议 · 隔离验收'],'type':'proceedings-article','published':{'date-parts':[[2026,1,1]]}}]}}
    service.http_factory=HTTP
    service.start()
    return calls
