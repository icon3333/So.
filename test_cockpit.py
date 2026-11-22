import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cockpit


class CockpitTests(unittest.TestCase):
    def test_desktop_preserves_categories_and_project_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp)
            for folder in ('To/Reading to do', 'To/Finance to do', 'Projects/Home/Research'):
                (desktop / folder).mkdir(parents=True)
            (desktop / 'To/Reading to do/Read.pdf').touch()
            (desktop / 'To/Finance to do/Invoice.pdf').touch()
            (desktop / 'To/Reading to do/.hidden').touch()
            (desktop / 'To/Reading to do/Outside').symlink_to(desktop / 'Projects')
            projects = cockpit.scan_desktop(desktop, 'Projects')
            tasks = cockpit.scan_desktop(desktop, 'To')
            self.assertEqual([(i['title'], i['kind'], i['group']) for i in tasks['items']],
                             [('Invoice.pdf', 'task', 'Finance to do'), ('Read.pdf', 'reading', 'Reading to do')])
            self.assertEqual([i['title'] for i in projects['items']], ['Home'])
            self.assertEqual(tasks, cockpit.scan_desktop(desktop, 'To'))
            self.assertEqual(cockpit.scan_desktop(desktop, 'Missing')['state'], 'missing')

    def test_keep_one_item_per_note_including_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'checklist.json').write_text(json.dumps({'title':'Einkauf', 'isArchived':True,
                'listContent':[{'text':'Obst', 'isChecked':False}, {'text':'Tee', 'isChecked':True}]}))
            (root / 'note.json').write_text(json.dumps({'textContent':'Hallo', 'attachments':[{'filePath':'local.png'}]}))
            (root / 'trashed.json').write_text(json.dumps({'title':'Papierkorb', 'isTrashed':True}))
            (root / 'broken.json').write_text('{')
            (root / 'junk.json').symlink_to(root / 'note.json')
            result = cockpit.scan_keep(root)
            self.assertEqual(result['state'], 'partial')
            self.assertEqual(result['skipped'], 1)
            self.assertEqual([i['title'] for i in result['items']], ['Einkauf', 'Hallo'])
            self.assertEqual(result['items'][0]['detail'], '☐ Obst\n☑ Tee')
            self.assertTrue(result['items'][0]['archived'])
            self.assertEqual(result['items'][1]['attachments'], 1)

    def test_cockpit_maps_saved_imports_with_topics_and_x_content(self):
        articles = [
            {'id':'in-1','origin':'inoreader','title':'Paper','url':'https://example.com/paper','excerpt':'Text','format':'web'},
            {'id':'x-1','origin':'x','title':'Prompt','url':'https://x.com/user/status/123','excerpt':'Tweet'},
            {'id':'not-saved','origin':'inoreader','title':'Hidden'}]
        mapped = cockpit.global_sources(articles, ['in-1','x-1'], {}, {
            'x-1': {'topic':'KI & Agenten','summary':'Summary','text':'Post body','takeaway':'Try it'}
        })['reading']['items']
        self.assertEqual(len(mapped), 2)
        self.assertEqual(mapped[0]['sourceLabel'], 'Inoreader')
        x = next(i for i in mapped if i['group'] == 'X')
        self.assertEqual((x['group'], x['topic'], x['detail']), ('X','KI & Agenten','Post body'))
        self.assertEqual(x['xContent']['summary'], 'Summary')
        self.assertEqual(mapped[0]['url'], 'https://example.com/paper')
        self.assertTrue(mapped[0]['format'])

    def test_posteo_absent_and_normalized(self):
        self.assertEqual(cockpit.scan_posteo('/nonexistent')['state'], 'unconfigured')
        output = json.dumps({'tasks':[{'resource':'private.ics', 'summary':'Test', 'due':'20261125',
                    'status':'NEEDS-ACTION','categories':['Home'], 'rrule':'FREQ=MONTHLY'}]})
        with patch('cockpit.Path.is_file', return_value=True), patch('cockpit.subprocess.run') as run:
            run.return_value.stdout = output
            result = cockpit.scan_posteo('/path/adapter.py')
            self.assertEqual(result['state'], 'ready')
            self.assertEqual(result['items'][0]['due'], '2026-11-25')
            self.assertTrue(result['items'][0]['recurring'])
            self.assertNotIn('private.ics', json.dumps(result))
            self.assertEqual(run.call_args.args[0][-2:], ['inventory', '--json'])
            run.return_value.stdout = '{}'
            self.assertEqual(cockpit.scan_posteo('/path/adapter.py')['state'], 'error')
