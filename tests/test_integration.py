from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import fixtures
from etf_limit_impact.cli import latest_run, main


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.run,_,_=fixtures.fixture(self.root/'source')
        self.backtest=self.root/'original_project'
        package=self.backtest/'etf_arb/cli'
        package.mkdir(parents=True)
        (self.backtest/'etf_arb/__init__.py').write_text('',encoding='utf-8')
        (package/'__init__.py').write_text('',encoding='utf-8')
        self.config=self.root/'backtest.json'
        raw=json.loads((self.run/'config.json').read_text(encoding='utf-8'))
        raw['synthetic_source_run']=str(self.run)
        self.config.write_text(json.dumps(raw),encoding='utf-8')
        self.child=package/'__main__.py'
        self.child.write_text('''import argparse,json,shutil
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('--config');p.add_argument('--premium-threshold');p.add_argument('--direction')
a=p.parse_args();c=json.loads(Path(a.config).read_text(encoding='utf-8'));source=Path(c['synthetic_source_run']);out=Path(c['output_dir']);out.mkdir()
for path in source.iterdir():
    if path.is_file() and path.name!='config.json': shutil.copyfile(path,out/path.name)
shutil.copytree(source/'report',out/'report')
if a.direction is not None:c['simulation']['trade_direction']=a.direction
if a.premium_threshold is not None:c['simulation']['premium_threshold']=a.premium_threshold
(out/'config.json').write_text(json.dumps(c),encoding='utf-8')
''',encoding='utf-8')

    def invoke(self, *extra):
        with redirect_stdout(StringIO()) as stdout, redirect_stderr(StringIO()) as stderr:
            code=main(['run','--backtest-root',str(self.backtest),'--config',str(self.config),
                       '--output-root',str(self.root/'reports'),*extra])
        return code,stdout.getvalue(),stderr.getvalue()

    def test_run_executes_child_then_generates_analysis_in_new_directories(self):
        before=self.config.read_bytes()
        code,_,error=self.invoke('--direction','redemption','--premium-threshold','.0014')
        self.assertEqual(code,0,error)
        bundle=next((self.root/'reports').iterdir())
        self.assertTrue((bundle/'limit_analysis/report.html').is_file())
        self.assertTrue((bundle/'backtest/report/report.html').is_file())
        self.assertTrue((bundle/'report.html').is_file())
        self.assertEqual(json.loads((bundle/'limit_analysis/analysis_status.json').read_text())['state'],'completed')
        saved=json.loads((bundle/'backtest/config.json').read_text())
        self.assertEqual(Decimal(saved['simulation']['premium_threshold']), Decimal('.0014'))
        self.assertEqual(saved['simulation']['trade_direction'],'redemption')
        self.assertEqual(self.config.read_bytes(),before)
        code,_,error=self.invoke()
        self.assertEqual(code,0,error)
        self.assertEqual(len(list((self.root/'reports').iterdir())),2)

    def test_child_failure_never_starts_analysis(self):
        self.child.write_text('raise SystemExit(7)',encoding='utf-8')
        code,_,error=self.invoke()
        self.assertEqual(code,2)
        self.assertIn('analysis was not started',error)
        self.assertFalse(list((self.root/'reports').glob('*/limit_analysis')))

    def test_latest_skips_incomplete_runs(self):
        reports=self.root/'pool'
        import shutil
        shutil.copytree(self.run,reports/'finished')
        (reports/'running').mkdir()
        (reports/'running/run_summary.json').write_text('{"state":"running"}',encoding='utf-8')
        self.assertEqual(latest_run(reports).name,'finished')
        with redirect_stdout(StringIO()):
            self.assertEqual(main(['latest','--reports-root',str(reports),'--output',str(self.root/'latest_analysis')]),0)
        self.assertTrue((self.root/'latest_analysis/report.html').is_file())


if __name__=='__main__':
    unittest.main()
