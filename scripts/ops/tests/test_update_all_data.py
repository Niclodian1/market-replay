"""数据更新回归：时间戳兼容、覆盖顺序、无效行情不落盘、分时跨月不丢片。"""
import asyncio
import importlib
import json
import struct
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
update=importlib.import_module('update_all_data')
intraday=importlib.import_module('build_intraday_chunks')


def frame(times, closes=None):
    return pd.DataFrame({'time':times,'open':[10.]*len(times),'high':[12.]*len(times),'low':[9.]*len(times),
                         'close':closes or [11.]*len(times),'volume':[1.]*len(times)})


class UpdateTests(unittest.TestCase):
    def test_alias_backfill_does_not_duplicate_legacy_daily_timestamps(self):
        day=86400
        old=frame([day+57600,2*day+57600,8*day,9*day])
        fresh=frame([day,2*day,8*day,9*day,10*day],[10.5]*5)
        merged=update.merge_chart_history(old,fresh,day)
        self.assertEqual(merged.time.tolist(),[day+57600,2*day+57600,8*day,9*day,10*day])
        self.assertEqual(merged.iloc[0]['close'],11.)
        self.assertEqual(merged.iloc[-2]['close'],10.5)

    def test_fresh_values_win_for_same_timestamp(self):
        merged=update.merge_frame(frame([1,2]),frame([2,3],[10.5,11.5]))
        self.assertEqual(merged.time.tolist(),[1,2,3])
        self.assertEqual(merged.iloc[1]['close'],10.5)

    def test_invalid_candle_does_not_replace_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bars.parquet'
            original=frame([1,2]);update.atomic_frame(original,path)
            invalid=frame([1,2],[20.,11.])
            with self.assertRaises(ValueError):update.atomic_frame(invalid,path)
            pd.testing.assert_frame_equal(pd.read_parquet(path),original)

    def test_intraday_tail_is_persisted_to_each_month_and_preserves_existing_rows(self):
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,11,1,1,0,tzinfo=timezone.utc)
        oct_time=int(datetime(2026,10,31,23,30,tzinfo=timezone.utc).timestamp())
        nov_time=int(datetime(2026,11,1,tzinfo=timezone.utc).timestamp())
        async def archive(*_):return None
        async def tail(*_):return [(oct_time+900,10.,12.,9.,11.,1.),(nov_time,10.,12.,9.,11.,1.)]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);out=root/'BTC'/'15m';out.mkdir(parents=True)
            intraday.write_bin(out/'2026-10.bin',[(oct_time,10.,12.,9.,11.,1.)])
            with patch.object(intraday,'OUT_ROOT',root),patch.object(intraday,'REPO',root),patch.object(intraday,'LISTING_MONTH',{'BTC':'2026-10'}),patch.object(intraday,'datetime',Clock),patch.object(intraday,'fetch_archive',archive),patch.object(intraday,'fetch_api_range',tail):
                asyncio.run(intraday.build_one(None,'BTC','15m',False,False))
            manifest=json.loads((out/'manifest.json').read_text())
            self.assertEqual([c['n'] for c in manifest['chunks']],[2,1])
            self.assertEqual(manifest['total'],3)
            self.assertEqual(int(struct.unpack('<6d',(out/'2026-11.bin').read_bytes())[0]),nov_time)
            self.assertTrue((out/'2026-11.js').exists())


if __name__=='__main__':unittest.main()
