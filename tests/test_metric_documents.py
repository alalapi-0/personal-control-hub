import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hub.metric_documents import collect_study, collect_story, collect_zarathustra, collect_cognitive

NOW='2026-09-09T00:00:00Z'


class DocumentMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def put(self,path,obj):
        target=self.root/path; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(obj))

    def run_adapter(self,fn):
        result=fn(self.root,'fixture',NOW,{})
        return result,{m['metric_id']:m['value'] for m in result['metrics']}

    def study(self):
        self.progress={'version':2,'active_course_id':'linux-foundations','tasks':{'task-1':{'done':False,'done_at':None}}}
        self.put('progress.json',self.progress)
        (self.root/'rounds_data.js').write_text('// generated\nwindow.ROUNDS_DATA = '+json.dumps([{'id':'round_00','weeks':[{'id':'w1','tasks':[{'id':'task-1','title':'PRIVATE BODY'}]}]}])+';')

    def test_study_round_identity_status_and_semantic_version(self):
        self.study(); first,rows=self.run_adapter(collect_study)
        self.assertEqual(rows['study.tasks_assigned'],1);self.assertEqual(rows['study.tasks_done'],0)
        self.progress['tasks']['task-1']['done_at']='bad time';self.progress['unrelated_prose']='SECRET PROSE'
        self.put('progress.json',self.progress);second,_=self.run_adapter(collect_study)
        self.assertEqual(first['source_version'],second['source_version'])
        self.assertTrue(all(m['business_at'] is None for m in second['metrics']))
        self.assertNotIn('SECRET',json.dumps(second))
        self.progress['tasks']['task-1']['done']=True;self.put('progress.json',self.progress)
        third,rows=self.run_adapter(collect_study);self.assertNotEqual(second['source_version'],third['source_version']);self.assertEqual(rows['study.tasks_done'],1)
        self.progress['tasks']['task-1']['done']='yes';self.put('progress.json',self.progress)
        _,rows=self.run_adapter(collect_study);self.assertEqual(rows['study.tasks_assigned'],1);self.assertIsNone(rows['study.tasks_done'])
        self.progress['tasks']['wrong']=self.progress['tasks'].pop('task-1');self.put('progress.json',self.progress)
        _,rows=self.run_adapter(collect_study);self.assertIsNone(rows['study.tasks_assigned'])

    def test_study_never_executes_js(self):
        self.study();(self.root/'rounds_data.js').write_text('window.ROUNDS_DATA = []; evil();')
        _,rows=self.run_adapter(collect_study);self.assertIsNone(rows['study.tasks_assigned'])

    def story(self):
        self.base='productions/scripts/season-0-daily-v005/'
        self.review='workbench/editorial_review/season-0-daily-v003/verdicts/season-0-daily-v005.json'
        production='story_faceless_utopia__production__season_0_daily_v005'
        self.put(self.base+'season.json',{'schema_version':'season0_daily_season_v0_5','production_id':production,'episode_count':1,'episode_refs':[{'episode_id':'EP01'}],'arc_phases':[{'phase_id':'phase1','episode_start':1,'episode_end':1}]})
        self.put(self.base+'manifest.json',{'schema_version':'production_manifest_v0_1','production_id':production,'production_version':'v005','artifact_count':2,'artifacts':[{'artifact_id':'a'},{'artifact_id':'b'}]})
        self.verdict={'schema_version':'season0_daily_human_editorial_review_v0_3','production_id':production,'source_id':'season-0-daily-v005','episodes':{'EP01':{'verdict':'pending'}}}
        self.put(self.review,self.verdict)

    def test_story_counts_episodes_not_artifacts_and_binds_review(self):
        self.story();_,rows=self.run_adapter(collect_story)
        self.assertEqual(rows['story.episodes_declared'],1);self.assertEqual(rows['story.artifacts_declared'],2);self.assertEqual(rows['story.editorial_pending'],1)
        self.verdict['source_id']='season-0-daily-v004';self.put(self.review,self.verdict)
        _,rows=self.run_adapter(collect_story);self.assertIsNone(rows['story.editorial_pending']);self.assertEqual(rows['story.episodes_declared'],1)

    def test_zarathustra_claim_failure_independent_of_coverage(self):
        self.put('source/de/index.json',{'unit_count':1,'units':[{'id':'u1'}]})
        self.put('analysis/modern_reader/coverage.json',{'schema_version':'zarathustra_modern_reader_coverage_v0_1','expected_unit_count':1,'accepted_count':1,'units':{'u1':{'status':'accepted','version':1,'content_sha256':'ignored'}}})
        self.put('analysis/claims.json',{'claim_count':1,'claims':[{'id':'c1','claim':'SECRET','source_unit_ids':['wrong']}]})
        result,rows=self.run_adapter(collect_zarathustra)
        self.assertEqual(rows['zarathustra.modern_reader_accepted'],1);self.assertIsNone(rows['zarathustra.claims']);self.assertNotIn('SECRET',json.dumps(result));self.assertIsNone(rows['zarathustra.current_reviews_passed'])

    def cognitive(self):
        self.put('data/topic_registry.json',{'version':'0.1.0','topics':[{'id':'KB-001','output_dir':'outputs/topic'}]})
        self.put('data/source_registry.json',{'version':'0.1.0','sources':[]})
        self.put('outputs/topic/topic_card.json',{'id':'KB-001','status':'planned','created_at':'2026-06-11'})
        self.put('outputs/topic/references.json',{'article_id':'KB-001','sources':[],'min_total':5,'min_academic_or_official':2})
        self.put('outputs/topic/image_manifest.json',{'article_id':'KB-001','images':[{'id':'cover','status':'planned'}]})

    def test_cognitive_identity_failure_and_unknown_time(self):
        self.cognitive();result,rows=self.run_adapter(collect_cognitive)
        self.assertEqual(rows['cognitive.topics_published'],0);self.assertEqual(rows['cognitive.topic_images_planned'],1)
        self.assertTrue(all(m['business_at'] is None for m in result['metrics']))
        self.put('outputs/topic/references.json',{'article_id':'wrong','sources':[]})
        self.put('outputs/topic/topic_card.json',{'id':'KB-001','status':'nonsense'})
        _,rows=self.run_adapter(collect_cognitive);self.assertIsNone(rows['cognitive.topic_sources']);self.assertIsNone(rows['cognitive.topics_published']);self.assertEqual(rows['cognitive.topic_images_planned'],1)

    def test_cognitive_path_escape_is_not_read(self):
        self.cognitive();self.put('data/topic_registry.json',{'version':'0.1.0','topics':[{'id':'KB-001','output_dir':'../outside'}]})
        _,rows=self.run_adapter(collect_cognitive);self.assertIsNone(rows['cognitive.topic_sources']);self.assertEqual(rows['cognitive.sources_registered'],0)

    def test_missing_sources_unknown_all_adapters(self):
        for fn in (collect_study,collect_story,collect_zarathustra,collect_cognitive):
            result,rows=self.run_adapter(fn);self.assertTrue(all(v is None for v in rows.values()));self.assertEqual(result['disposition'],'partial')

    def test_symlink_escape(self):
        self.study();(self.root/'progress.json').unlink();(self.root/'progress.json').symlink_to('/etc/passwd')
        _,rows=self.run_adapter(collect_study);self.assertIsNone(rows['study.tasks_assigned'])

    def test_zarathustra_current_bundle_identity_and_external_symlink(self):
        self.put('source/de/index.json',{'unit_count':1,'units':[{'id':'u1'}]})
        current=self.root/'workbench/editorial_review/current.yaml';current.parent.mkdir(parents=True)
        current.write_text('schema_version: zarathustra_editorial_review_current_v0_1\nsource_id: phase1d-modern-reader-v032\n')
        path='workbench/editorial_review/sources/phase1d-modern-reader-v032/manifest.json'
        manifest={'schema_version':'zarathustra_editorial_review_bundle_v0_6','source_id':'phase1d-modern-reader-v032','item_count':1,'accepted_count':1}
        self.put(path,manifest);_,rows=self.run_adapter(collect_zarathustra)
        self.assertEqual(rows['zarathustra.review_bundle_items'],1)
        manifest['source_id']='phase1d-modern-reader-v031';self.put(path,manifest)
        _,rows=self.run_adapter(collect_zarathustra);self.assertIsNone(rows['zarathustra.review_bundle_items'])
        target=self.root/path;target.unlink();target.symlink_to('/etc/passwd')
        _,rows=self.run_adapter(collect_zarathustra);self.assertIsNone(rows['zarathustra.review_bundle_items']);self.assertEqual(rows['zarathustra.source_units'],1)

    def test_explicit_external_editorial_metadata_root(self):
        self.put('source/de/index.json',{'unit_count':1,'units':[{'id':'u1'}]})
        current=self.root/'workbench/editorial_review/current.yaml';current.parent.mkdir(parents=True)
        current.write_text('schema_version: zarathustra_editorial_review_current_v0_1\nsource_id: phase1d-modern-reader-v032\n')
        with tempfile.TemporaryDirectory() as external:
            manifest=Path(external)/'phase1d-modern-reader-v032/manifest.json';manifest.parent.mkdir()
            data={'schema_version':'zarathustra_editorial_review_bundle_v0_6','source_id':'phase1d-modern-reader-v032','item_count':1,'accepted_count':1}
            manifest.write_text(json.dumps(data))
            result=collect_zarathustra(self.root,'fixture',NOW,{'editorial_root':external})
            rows={r['metric_id']:r['value'] for r in result['metrics']}
            self.assertEqual(rows['zarathustra.review_bundle_items'],1)
            self.assertIsNone(rows['zarathustra.current_editorial_pending'])
            data['source_id']='phase1d-modern-reader-v031';manifest.write_text(json.dumps(data))
            result=collect_zarathustra(self.root,'fixture',NOW,{'editorial_root':external})
            rows={r['metric_id']:r['value'] for r in result['metrics']}
            self.assertIsNone(rows['zarathustra.review_bundle_items'])

    def test_story_all_schema_verdicts_dimensions_and_business_time(self):
        self.story()
        for state in ('pending','advance','revise','rewrite'):
            self.verdict['episodes']['EP01']['verdict']=state
            self.verdict['updated_at']='2026-09-09T05:23:00Z'
            self.put(self.review,self.verdict)
            result,rows=self.run_adapter(collect_story)
            for other in ('pending','advance','revise','rewrite'):
                self.assertEqual(rows['story.editorial_'+other],int(other==state))
            for row in result['metrics']:
                self.assertEqual(row['dimensions']['production_id'],self.verdict['production_id'])
                if row['metric_id'].startswith('story.editorial_'):
                    self.assertEqual(row['business_at'],'2026-09-09T05:23:00Z')
        for bad in ('2026-09-09','2026-09-09T05:23:00','broken',7):
            self.verdict['updated_at']=bad;self.put(self.review,self.verdict)
            result,rows=self.run_adapter(collect_story)
            self.assertEqual(rows['story.editorial_rewrite'],1)
            self.assertTrue(all(r['business_at'] is None for r in result['metrics']))
        self.verdict['episodes']['EP01']['verdict']='approved';self.put(self.review,self.verdict)
        _,rows=self.run_adapter(collect_story);self.assertIsNone(rows['story.editorial_pending'])

    def test_study_course_identity_is_dimension_and_version_boundary(self):
        self.study();first,_=self.run_adapter(collect_study)
        self.assertTrue(all(r['dimensions']['active_course_id']=='linux-foundations' for r in first['metrics']))
        self.progress['active_course_id']='other-course';self.put('progress.json',self.progress)
        second,_=self.run_adapter(collect_study);self.assertNotEqual(first['source_version'],second['source_version'])
        self.progress['active_course_id']=[];self.put('progress.json',self.progress)
        _,rows=self.run_adapter(collect_study);self.assertIsNone(rows['study.tasks_assigned'])

    def test_zarathustra_legal_nonaccepted_coverage_statuses(self):
        self.put('source/de/index.json',{'unit_count':1,'units':[{'id':'u1'}]})
        for state in ('not_started','drafted','blind_reviewed','fidelity_reviewed','accepted'):
            self.put('analysis/modern_reader/coverage.json',{'schema_version':'zarathustra_modern_reader_coverage_v0_1','expected_unit_count':1,'accepted_count':int(state=='accepted'),'units':{'u1':{'status':state,'version':0 if state=='not_started' else 1}}})
            _,rows=self.run_adapter(collect_zarathustra)
            self.assertEqual(rows['zarathustra.modern_reader_accepted'],int(state=='accepted'))

    def test_cognitive_legal_topic_statuses(self):
        self.cognitive()
        for state in ('planned','drafting','review','published','deprecated'):
            self.put('outputs/topic/topic_card.json',{'id':'KB-001','status':state})
            _,rows=self.run_adapter(collect_cognitive)
            self.assertEqual(rows['cognitive.topics_published'],int(state=='published'))


class StudyBindingTests(unittest.TestCase):
    def test_registry_watches_declaration_and_export(self):
        registry = yaml.safe_load((ROOT / 'data/registry/external_projects.yaml').read_text())
        registered = next(item for item in registry['projects'] if item['id'] == 'computer-study-plan')
        self.assertIn('hub.connection.yaml', registered['watch_paths'])
        self.assertIn('scripts/export_hub_metric_snapshot.py', registered['watch_paths'])
        sources = yaml.safe_load((ROOT / 'data/connections/metric_sources.yaml').read_text())
        spec = sources['projects']['computer-study-plan']
        self.assertEqual(spec['adapter'], 'study')
        self.assertEqual(spec.get('github_repository'), 'alalapi-0/computer_study_plan')
