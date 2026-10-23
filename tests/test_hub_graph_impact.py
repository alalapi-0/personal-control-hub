"""Synthetic published graph records only; no new source adapter or real IO claims."""
import copy
import unittest
from unittest import mock
from hub.graph_service import project_graph
from hub.graph_records import validate_graph, stable_id
from test_hub_graph_service import dto
from test_hub_graph_relations import relation
from test_hub_management_store import overlay


def endpoint(value):
    return dict(id=value['id'], revision=value['revision'])


class GraphImpactTests(unittest.TestCase):
    def record(self, native, revision='v1', kind='Task', identity='fact'):
        # Use an existing validated source projection shape, never invented native IDs.
        template=next(o for o in project_graph(dto())['objects'] if o['type']=='Task')
        template.update(id=stable_id('business',kind,native),native_id=native,type=kind,
                        revision=revision,label='Same synthetic label',identity=identity,data={})
        if kind=='Artifact':
            template['data']['artifact']={k:dict(value=None,unknown_reason='Not published by this fixture.')
                for k in ('input_version','code_version','sha256','purpose','host','file_ref')}
        return template

    def edge(self, ident, kind, a, b, *, candidate=False):
        return dict(id=stable_id('business','Relation',ident),native_id=ident,project_id='business',revision='edge-v1',type=kind,
                    **{'from':endpoint(a),'to':endpoint(b)},identity='candidate' if candidate else 'fact',
                    source=copy.deepcopy(a['source']),evidence=['state:explicit-io-field'],run_id=None)

    def published(self, objects, edges):
        value=dto();before=copy.deepcopy(value);originals=copy.deepcopy((objects,edges))
        def publish(dto_value, root, projected, relations):
            projected.extend(copy.deepcopy(objects));relations.extend(copy.deepcopy(edges))
        # The current DTO has no fact IO publisher. This is an explicit synthetic
        # producer seam in the actual project_graph path, not a source collector.
        with mock.patch('hub.graph_service._management_relations',side_effect=publish):
            result=project_graph(value)
        validate_graph(result);self.assertEqual(value,before);self.assertEqual((objects,edges),originals)
        return result

    def impact(self, graph, obj):
        return next(o['data']['impact'] for o in graph['objects'] if endpoint(o)==endpoint(obj))

    def test_multihop_produces_consumes_derived_reverse_and_exact_revisions(self):
        producer=self.record('producer');old=self.record('output',kind='Artifact');new=self.record('output','v2','Artifact')
        same_name=self.record('other-output',kind='Artifact');consumer=self.record('consumer');derived=self.record('derived',kind='Artifact');tail=self.record('tail')
        edges=[self.edge('produce','produces',producer,old),self.edge('consume','consumes',consumer,old),
               self.edge('derive','derived_from',derived,old),self.edge('tail-use','consumes',tail,derived)]
        g=self.published([producer,old,new,same_name,consumer,derived,tail],edges)
        impact=self.impact(g,old);self.assertEqual({tuple(p['target'].values()) for p in impact['fact_paths']},
            {tuple(endpoint(o).values()) for o in (consumer,derived,tail)})
        self.assertEqual(next(p for p in impact['fact_paths'] if p['target']==endpoint(tail))['path'][0]['type'],'derived_from')
        self.assertEqual(self.impact(g,new)['fact_paths'],[]);self.assertEqual(self.impact(g,same_name)['fact_paths'],[])
        self.assertEqual(len(self.impact(g,producer)['fact_paths']),4)
        self.assertEqual(self.impact(g,consumer)['fact_paths'],[])

    def test_dependency_reference_management_and_layout_never_create_io(self):
        a=self.record('left');b=self.record('right')
        for kind in ('depends_on','blocks','references','related_to','part_of','managed_by'):
            with self.subTest(kind=kind):
                g=self.published([a,b],[self.edge('not-io',kind,a,b)])
                self.assertEqual(self.impact(g,a)['fact_paths'],[]);self.assertEqual(self.impact(g,b)['candidate_paths'],[])
                self.assertIn('NO_EXPLICIT_VERSIONED_IO',self.impact(g,a)['gaps'])

    def test_cycle_is_bounded_deduplicated_and_retains_exact_evidence_path(self):
        a=self.record('cycle-a',kind='Artifact');b=self.record('cycle-b',kind='Artifact');c=self.record('cycle-c',kind='Artifact')
        g=self.published([a,b,c],[self.edge('ab','derived_from',b,a),self.edge('bc','derived_from',c,b),self.edge('ca','derived_from',a,c)])
        impact=self.impact(g,a);self.assertEqual([p['target']for p in impact['fact_paths']],[endpoint(b),endpoint(c)])
        self.assertTrue(impact['traversal_complete']);self.assertEqual(len(impact['fact_paths'][-1]['path']),2)
        self.assertEqual(impact['fact_paths'][-1]['path'][0]['evidence'],['state:explicit-io-field'])
        self.assertFalse(impact['rerun']['available'])

    def test_any_candidate_on_path_stays_candidate_and_accepted_overlay_is_not_fact(self):
        a=self.record('fact-root');b=self.record('candidate-output',kind='Artifact',identity='candidate');c=self.record('consumer')
        g=self.published([a,b,c],[self.edge('first','produces',a,b,candidate=True),self.edge('second','consumes',c,b,candidate=True)])
        impact=self.impact(g,a);self.assertEqual(impact['fact_paths'],[]);self.assertEqual([p['target']for p in impact['candidate_paths']],[endpoint(b),endpoint(c)])
        value=dto();item=relation('accepted-io','produces');item['state']='accepted';item['origin']='explicit'
        value['management']=dict(available=True,overlay=overlay(relations=dict(belongs_to=None,depends_on=[],related_to=[],independent=None,
            typed=dict(schema_version='1.0',items=[item]))),project_options=[])
        graph=project_graph(value);source=next(o for o in graph['objects'] if o['native_id']=='a')
        self.assertEqual(source['data']['impact']['fact_paths'],[]);self.assertEqual(len(source['data']['impact']['candidate_paths']),1)
        self.assertEqual(next(e for e in graph['relations'] if 'management'in e)['identity'],'candidate')

    def test_new_artifact_does_not_inherit_old_consumer_or_decision(self):
        old=self.record('release','z-old','Artifact');new=self.record('release','a-new','Artifact')
        old_decision=self.record('decision','decision-9','Decision');new_decision=self.record('decision','decision-10','Decision')
        g=self.published([old,new,old_decision,new_decision],[self.edge('old-only','consumes',old_decision,old)])
        self.assertEqual([p['target']for p in self.impact(g,old)['fact_paths']],[endpoint(old_decision)])
        self.assertEqual(self.impact(g,new)['fact_paths'],[]);self.assertEqual(self.impact(g,new_decision)['fact_paths'],[])

    def test_no_io_or_unqualified_version_is_unknown_and_denied_projection_stays_private(self):
        a=self.record('unversioned');b=self.record('target');a['revision']=None
        g=self.published([a,b],[self.edge('missing-version','produces',a,b)])
        self.assertEqual(self.impact(g,a)['state'],'partial');self.assertIn('UNQUALIFIED_IO_OMITTED',self.impact(g,a)['gaps'])
        self.assertEqual(self.impact(g,a)['fact_paths'],[])
        value=dto();value['declared']['connection_read_allowed']=False;value['source']['source_record']='do not inspect'
        g=project_graph(value);self.assertEqual(len(g['objects']),1);self.assertEqual(g['objects'][0]['data']['impact']['state'],'unknown')
        self.assertEqual(g['objects'][0]['data']['impact']['fact_paths'],[])

    def test_bounded_partial_history_and_projection_hash_without_self_reference(self):
        nodes=[self.record('long-'+str(i),kind='Artifact') for i in range(36)]
        edges=[self.edge('long-'+str(i),'derived_from',nodes[i+1],nodes[i]) for i in range(35)]
        g=self.published(nodes,edges);impact=self.impact(g,nodes[0]);self.assertEqual(len(impact['fact_paths']),32)
        self.assertIn('TRAVERSAL_LIMIT',impact['gaps']);self.assertFalse(impact['traversal_complete']);validate_graph(g)
        value=dto();first=project_graph(value);value['freshness']['projected_at']='2026-10-24T00:00:00Z'
        other=project_graph(value);self.assertEqual(first['projection_version'],other['projection_version'])
        self.assertEqual(first['source_version'],other['source_version'])
