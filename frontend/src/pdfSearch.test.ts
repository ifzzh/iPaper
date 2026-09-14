import {afterEach,expect,it,vi} from 'vitest';
import {findMatches} from './readingSearch';
import {PdfSearch} from './pdfSearch';
class SearchWorker {
  onmessage: ((value:any)=>void)|null=null;
  onerror:null=null;
  dead=false;
  postMessage(value:any) {queueMicrotask(()=>{if(!this.dead)this.onmessage?.({data:{id:value.id,...findMatches(value.items,value.query,value.sensitive,value.skip,value.limit)}});});}
  terminate(){this.dead=true;}
}
afterEach(()=>vi.unstubAllGlobals());
it('searches unrendered pages, bounds cached page text and reports extraction failure separately',async()=>{
  vi.stubGlobal('Worker',SearchWorker);
  const extracted:number[]=[];
  const pdf={numPages:30,getPage:async(page:number)=>({streamTextContent:()=>new ReadableStream({start(c){extracted.push(page);if(page===13){c.error(new Error('failed page'));return;}c.enqueue({items:[{str:page===29?'rear evidence':'ordinary text',hasEOL:true}]});c.close();}})})};
  const search=new PdfSearch(pdf as any,'rear evidence',false);let result:any[]=[];
  await search.scan(p=>{result=p;});expect(result).toHaveLength(30);expect(result[28].count).toBe(1);expect(result[12].error).toBe('failed page');expect(result[0].error).toBeUndefined();
  await search.matches(1);expect(extracted.filter(n=>n===1)).toHaveLength(2); // discarded text must be re-read, not all pages retained
  const match=await search.matches(29);expect(match.matches[0].text).toBe('rear evidence');search.destroy();
});
it('stopping search cancels extraction and prevents late page results',async()=>{
  vi.stubGlobal('Worker',SearchWorker);let cancelled=false;let started=false;const updates:any[]=[];
  const pdf={numPages:300,getPage:async()=>({streamTextContent:()=>new ReadableStream({start(){started=true;},cancel(){cancelled=true;}})})};
  const search=new PdfSearch(pdf as any,'unused',false);const work=search.scan(p=>updates.push(p));await vi.waitFor(()=>expect(started).toBe(true));search.destroy();await work;expect(cancelled).toBe(true);expect(updates).toEqual([]);await expect(search.matches(1)).rejects.toMatchObject({name:'AbortError'});
});
it('oversized page is incomplete rather than a false no-match',async()=>{
  vi.stubGlobal('Worker',SearchWorker);const pdf={numPages:1,getPage:async()=>({streamTextContent:()=>new ReadableStream({start(c){c.enqueue({items:[{str:'a'.repeat(400000)}]});c.close();}})})};
  const search=new PdfSearch(pdf as any,'a',false);let result:any;await search.scan(p=>result=p);expect(result[0].error).toBe('page_text_limit');search.destroy();
});
