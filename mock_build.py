"""見た目の確認用。外部には一切アクセスせず、作り物の予報で public/ を書き出す。
使い方: python mock_build.py && python preview.py
本物の予報ではないので、表示される天気・数値はすべてでたらめ。公開には使わない(公開は GitHub Actions の build.py)"""
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import build

rng = random.Random(1)


def blocked(*args, **kwargs):          # 外部へのアクセスはすべて止める(祝日一覧など)
    raise urllib.error.URLError("mock_build.py は外部にアクセスしません")


urllib.request.urlopen = blocked
build.time.sleep = lambda s: None      # 外部にアクセスしないので待つ必要もない


def value(name, t, i):
    if name in ("sunrise", "sunset"):
        return f"{t[:10]}T{'06:10' if name == 'sunrise' else '16:50'}"
    if "weather_code" in name:
        return rng.choice([0, 1, 2, 3, 3, 45, 51, 61, 63, 71, 73, 80, 95])
    if "direction" in name:
        return rng.randrange(0, 360)
    if "temperature" in name or "dew_point" in name:
        return round(10 + 8 * rng.random() - 0.3 * (i % 24), 1)
    if name in ("precipitation", "rain", "showers") or name.startswith("precipitation_sum"):
        return rng.choice([0, 0, 0, 0.2, 1.5, 4.0])
    if "snowfall" in name:
        return rng.choice([0, 0, 0.5, 2.0])
    if "snow_depth" in name:
        return round(rng.uniform(0, 1.2), 2)
    if "probability" in name or "cloud" in name or "humidity" in name:
        return rng.randrange(0, 101)
    if "cape" in name:
        return rng.choice([0, 200, 1200])
    if "wave_height" in name:
        return round(rng.uniform(0.3, 2.0), 2)
    if "wave_period" in name:
        return round(rng.uniform(5, 12), 1)
    if "sea_level" in name:
        return round(0.8 * __import__("math").sin(i / 8), 2)
    if "visibility" in name:
        return rng.randrange(1000, 30000)
    if "wind" in name or "gust" in name:
        return round(rng.uniform(1, 12), 1)
    if "freezing_level" in name:
        return rng.randrange(1500, 3500)
    return round(rng.uniform(0, 10), 1)


def series(kind, names, days, past):
    start = datetime.now(build.JST).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=past)
    step = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "minutely_15": timedelta(minutes=15)}[kind]
    n = int(timedelta(days=days + past) / step)
    fmt = "%Y-%m-%d" if kind == "daily" else "%Y-%m-%dT%H:%M"
    times = [(start + step * i).strftime(fmt) for i in range(n)]
    out = {"time": times}
    for name in names:
        out[name] = [value(name, t, i) for i, t in enumerate(times)]
        if "ensemble" in kind:
            pass
    return out


def fake_fetch(url, params, retries=3):
    build.count_use(url, params)
    if "open-meteo.com" not in url:
        raise urllib.error.URLError("mock")          # 気象庁・前回公開分の JSON などは「取得失敗」として扱う
    lats = str(params["latitude"]).split(",")
    lons = str(params["longitude"]).split(",")
    days, past = int(params.get("forecast_days", 7)), int(params.get("past_days", 0))
    res = []
    for lat, lon in zip(lats, lons):
        r = {"latitude": float(lat), "longitude": float(lon), "elevation": 0}
        for kind in ("hourly", "daily", "minutely_15"):
            if params.get(kind):
                names = params[kind].split(",")
                if "ensemble" in url:                 # アンサンブルはメンバーごとの列も作る
                    names = names + [f"{n}_member{m:02d}" for n in names for m in range(1, 31)]
                r[kind] = series(kind, names, days, past)
        res.append(r)
    return res


build.fetch = fake_fetch
build.main()
print("※ 作り物の予報で書き出しました(見た目の確認用)", file=sys.stderr)
