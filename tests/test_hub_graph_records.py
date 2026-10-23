"""Pure graph identity, revision, relation and layout contracts."""
import sys,copy,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.graph_records import graph,stable_id,validate_object,validate_graph,EXPLANATIONS,TYPES
from hub.service_contract import ServiceError


def obj(kind='Task',native='task-a',revision='v1',identity='fact'):
 return {'id':stable_id('business',kind,native),'project_id':'business','type':kind,'native_id':native,'revision':revision,
         'label':'Same name','identity':identity,'source':{'ref':'source:state','version':revision,'sha256':'a'*64,'fact_time':None,
         'collected_at':'2026-10-23T00:00:00Z','origin':'source_fact','applicability':'current','unknown_reason':'No source timestamp.'},
         'data':({'artifact':{k:{'value':None,'unknown_reason':'Not declared.'}for k in ('input_version','code_version','sha256','purpose','host','file_ref')}} if kind=='Artifact' else {'domain_coordinates':{'x':3,'y':4,'z':5,'heading':90}}),
         'explanations':{k:{'state':'unknown','value':None,'source':None,'unknown_reason':'No declared explanation.'}for k in EXPLANATIONS}}


def edge(a,b,kind='related_to',native='edge-a',identity='fact'):
 return {'id':stable_id('business','Relation',native),'project_id':'business','native_id':native,'revision':'1','type':kind,
         'from':{'id':a['id'],'revision':a['revision']},'to':{'id':b['id'],'revision':b['revision']},
         'source':copy.deepcopy(a['source']),'evidence':['source:typed-relation'],'identity':identity,'run_id':None}


class GraphRecordTests(unittest.TestCase):
 def test_types_ids_same_names_revisions_and_detached(self):
  nodes=[obj(t,'source-'+t)for t in sorted(TYPES)];nodes += [obj('Artifact','output','v1'),obj('Artifact','output','v2')]
  before=copy.deepcopy(nodes);g=graph('business','a'*64,nodes);self.assertEqual(len(g['rows']),len(nodes));self.assertEqual(nodes,before)
  self.assertEqual(g['objects'][-1]['id'],g['objects'][-2]['id']);self.assertNotEqual(g['rows'][-1]['revision'],g['rows'][-2]['revision'])
  nodes[0]['label']='renamed';self.assertEqual(g['objects'][0]['label'],'Same name')
 def test_duplicate_wrong_project_unknown_schema_and_identity_fail(self):
  a=obj()
  for nodes in ([a,a],[{**a,'project_id':'foreign'}],[{**a,'id':'title'}],[{**a,'type':'Unknown'}]):
   with self.subTest(nodes=nodes),self.assertRaises(ServiceError):graph('business','a'*64,nodes)
  with self.assertRaises(ServiceError):validate_object({**a,'extra':True})
 def test_general_cycles_ok_dependency_cycles_reject_and_candidate_retained(self):
  a=obj();b=obj(native='task-b');g=graph('business','a'*64,[a,b],[edge(a,b),edge(b,a,native='edge-b')]);self.assertEqual(len(g['relations']),2)
  with self.assertRaises(ServiceError):graph('business','a'*64,[a,b],[edge(a,b,'depends_on'),edge(b,a,'depends_on','edge-b')])
  b['identity']='candidate'
  with self.assertRaises(ServiceError):graph('business','a'*64,[a,b],[edge(a,b)])
  g=graph('business','a'*64,[a,b],[edge(a,b,identity='candidate')]);self.assertEqual(g['relations'][0]['identity'],'candidate')
 def test_layout_separate_domain_coordinates_and_version(self):
  a=obj();base=graph('business','a'*64,[a]);layout={'view_id':'canvas','object_id':a['id'],'object_revision':a['revision'],'x':999,'y':888,'width':100,'height':50,'collapsed':False,'color':'blue','revision':1}
  g=graph('business','a'*64,[a],layouts=[layout]);self.assertEqual(g['objects'],base['objects']);self.assertEqual(g['projection_version'],base['projection_version']);self.assertEqual(a['data']['domain_coordinates']['x'],3)
  with self.assertRaises(ServiceError):graph('business','a'*64,[a],layouts=[{**layout,'domain_coordinates':{}}])
 def test_rows_version_unknown_time_and_run_identity_cannot_be_forged(self):
  a=obj();g=graph('business','a'*64,[a]);g['rows'][0]['label']='different'
  with self.assertRaises(ServiceError):validate_graph(g)
  a['source']['unknown_reason']=None
  with self.assertRaises(ServiceError):validate_object(a)
  a=obj();b=obj(native='task-b');e=edge(a,b);e['run_id']='invented'
  with self.assertRaises(ServiceError):graph('business','a'*64,[a,b],[e])

 def test_blocks_direction_matches_dependency_and_empty_evidence_refuses(self):
  a=obj();b=obj(native='task-b')
  # A depends on B and B blocks A describe the same dependency, not a cycle.
  graph('business','a'*64,[a,b],[edge(a,b,'depends_on'),edge(b,a,'blocks','edge-b')])
  with self.assertRaises(ServiceError):graph('business','a'*64,[a,b],[edge(a,b,'depends_on'),edge(a,b,'blocks','edge-b')])
  e=edge(a,b);e['evidence']=['']
  with self.assertRaises(ServiceError):graph('business','a'*64,[a,b],[e])

 def test_composed_relation_identity_has_separate_bounded_capacity(self):
  native='a'*128+':depends:'+'b'*128
  self.assertEqual(stable_id('business','Relation',native),'business/Relation/'+native)
  self.assertNotEqual(stable_id('business','Relation',native),stable_id('business','Relation','b'*128+':depends:'+'a'*128))
  with self.assertRaises(ServiceError):stable_id('business','Task','a'*257)
  with self.assertRaises(ServiceError):stable_id('business','Relation','a'*1025)
