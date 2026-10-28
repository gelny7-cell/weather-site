"""山と雪と波の天気まとめ。Open-Meteo の予報を取得して public/ に静的なHTMLを書き出す。
使い方: python build.py  (GitHub Actions で3時間ごとに実行。Python標準ライブラリのみ)"""
import html
import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
OUT = ROOT / "public"
JST = ZoneInfo("Asia/Tokyo")
DAYS = 7
WEEK = "月火水木金土日"
e = html.escape


# ---------------------------------------------------------------- 取得
def fetch(url, params, retries=3):
    q = urllib.parse.urlencode(params)
    for i in range(retries):
        try:
            with urllib.request.urlopen(f"{url}?{q}", timeout=60) as r:
                d = json.load(r)
            return d if isinstance(d, list) else [d]
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(10 * (i + 1))


def forecast(points, **params):
    """points: [(緯度, 経度, 標高)]。50地点ずつまとめて取得する"""
    out = []
    for i in range(0, len(points), 50):
        chunk = points[i:i + 50]
        out += fetch("https://api.open-meteo.com/v1/forecast", {
            "latitude": ",".join(str(p[0]) for p in chunk),
            "longitude": ",".join(str(p[1]) for p in chunk),
            "elevation": ",".join(str(p[2]) for p in chunk),
            "timezone": "Asia/Tokyo", "wind_speed_unit": "ms", "forecast_days": DAYS, **params})
    return out


def marine(points, **params):
    out = []
    for lat, lon in points:
        out += fetch("https://marine-api.open-meteo.com/v1/marine", {
            "latitude": lat, "longitude": lon, "timezone": "Asia/Tokyo",
            "forecast_days": DAYS, **params})
    return out


def hours_of(h, key, day, hours):
    """hourly データから、指定日・指定時間帯の値だけを取り出す"""
    return [v for t, v in zip(h["time"], h[key])
            if t[:10] == day and int(t[11:13]) in hours and v is not None]


# ---------------------------------------------------------------- 表示の部品
def wx(code):
    if code is None:
        return "", "—"
    if code == 0:
        return "☀️", "快晴"
    if code == 1:
        return "🌤️", "晴れ"
    if code == 2:
        return "⛅", "晴れ時々曇り"
    if code == 3:
        return "☁️", "曇り"
    if code in (45, 48):
        return "🌫️", "霧"
    if 51 <= code <= 57:
        return "🌦️", "霧雨"
    if 61 <= code <= 67 or 80 <= code <= 82:
        return "🌧️", "雨"
    if 71 <= code <= 77 or code in (85, 86):
        return "❄️", "雪"
    if code >= 95:
        return "⛈️", "雷雨"
    return "", "—"


DIRS = ["北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東",
        "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西"]


def dir_name(deg):
    return DIRS[round(deg / 22.5) % 16] if deg is not None else "—"


def ang_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def mean_dir(degs, weights=None):
    import math
    weights = weights or [1] * len(degs)
    x = sum(w * math.cos(math.radians(d)) for d, w in zip(degs, weights))
    y = sum(w * math.sin(math.radians(d)) for d, w in zip(degs, weights))
    return (math.degrees(math.atan2(y, x)) + 360) % 360 if degs else None


RATE = {3: ("◎", "r3"), 2: ("○", "r2"), 1: ("△", "r1"), 0: ("×", "r0")}


def maplink(query, label):
    """Google マップの検索リンク(場所名で検索する。新しいタブで開く)"""
    url = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(query)
    return (f"<a class='mapbtn' href='{e(url)}' target='_blank' rel='noopener' "
            f"title='{e(query)}'>📍 {e(label)}</a>")


def day_label(d):
    dt = date.fromisoformat(d)
    cls = "sat" if dt.weekday() == 5 else ("sun" if dt.weekday() == 6 else "")
    return f"<span class='{cls}'>{dt.month}/{dt.day}<small>({WEEK[dt.weekday()]})</small></span>"


def weekend(days):
    """次の土日(今日が土日なら今日から)の日付"""
    return [d for d in days if date.fromisoformat(d).weekday() >= 5][:2]


# ---------------------------------------------------------------- 登山
def build_mountains():
    mts = json.loads((ROOT / "data/mountains.json").read_text(encoding="utf-8"))
    res = forecast([(m["lat"], m["lon"], m["elev"]) for m in mts], hourly=",".join([
        "temperature_2m", "precipitation", "weather_code",
        "wind_speed_850hPa", "wind_speed_700hPa", "freezing_level_height"]))
    day_hours = range(6, 16)          # 行動する時間帯(6〜15時)
    for m, r in zip(mts, res):
        h = r["hourly"]
        level = "wind_speed_700hPa" if m["elev"] >= 2200 else "wind_speed_850hPa"
        m["days"] = []
        for d in sorted({t[:10] for t in h["time"]}):
            rain = sum(hours_of(h, "precipitation", d, day_hours))
            wind = max(hours_of(h, level, d, day_hours) or [0])
            temps = hours_of(h, "temperature_2m", d, day_hours)
            codes = hours_of(h, "weather_code", d, day_hours)
            fl = min(hours_of(h, "freezing_level_height", d, day_hours) or [None]) if \
                hours_of(h, "freezing_level_height", d, day_hours) else None
            thunder = any(c >= 95 for c in codes)
            if thunder or rain >= 10 or wind >= 20:
                score = 0
            elif rain >= 3 or wind >= 15:
                score = 1
            elif rain >= 0.5 or wind >= 10:
                score = 2
            else:
                score = 3
            m["days"].append({"date": d, "score": score, "rain": rain, "wind": wind,
                              "tmin": min(temps) if temps else None,
                              "tmax": max(temps) if temps else None,
                              "code": max(codes) if codes else None, "fl": fl,
                              "thunder": thunder})
    return mts


def mountains_page(mts):
    days = [d["date"] for d in mts[0]["days"]]
    regions = ["北海道", "東北", "関東", "中部", "近畿", "中国・四国", "九州"]
    head = "".join(f"<th>{day_label(d)}</th>" for d in days)
    rows = ""
    for m in mts:
        cells = ""
        for d in m["days"]:
            mark, cls = RATE[d["score"]]
            icon, _ = wx(d["code"])
            cells += (f"<td class='{cls}' title='{e(wx(d['code'])[1])} 風{d['wind']:.0f}m/s "
                      f"雨{d['rain']:.0f}mm'><b>{mark}</b><small>{icon}</small></td>")
        detail = "".join(
            f"<tr><td>{day_label(d['date'])}</td><td>{wx(d['code'])[0]} {wx(d['code'])[1]}</td>"
            f"<td>{round(d['tmin'])}〜{round(d['tmax'])}℃</td><td>{d['wind']:.0f}m/s</td>"
            f"<td>{d['rain']:.1f}mm</td>"
            f"<td>{'—' if d['fl'] is None else f'{d['fl']:,.0f}m'}</td>"
            f"<td class='{RATE[d['score']][1]}'><b>{RATE[d['score']][0]}</b></td></tr>"
            for d in m["days"])
        rows += (
            f"<tbody class='mt' data-region='{e(m['region'])}' data-name='{e(m['name'] + m['yomi'])}'>"
            f"<tr class='main'><th class='nm'><button type='button' aria-expanded='false'>"
            f"<span>{e(m['name'])}</span>"
            + (" <span class='tag'>関西の名山</span>" if m.get("list") == "関西" else "")
            + f"<small>{m['elev']:,}m · {e('・'.join(m['prefs']))}</small>"
            f"</button></th>{cells}</tr>"
            f"<tr class='detail' hidden><td colspan='{len(days) + 1}'>"
            f"{maplink(m['trailhead'], '登山口の地図: ' + m['trailhead'])}<div class='scroll'><table class='sub'>"
            "<tr><th>日付</th><th>天気(6〜15時)</th><th>山頂付近の気温</th><th>最大風速</th>"
            f"<th>降水量</th><th>0℃の高さ</th><th>判定</th></tr>{detail}</table></div></td></tr></tbody>")
    chips = "<button class='chip on' data-r=''>すべて</button>" + "".join(
        f"<button class='chip' data-r='{r}'>{r}</button>" for r in regions)
    return f"""
<h1>日本百名山＋関西の名山 登山天気</h1>
<p class='lead'>日本百名山と、近畿の人気の山20座(<span class='tag'>関西の名山</span>)の予報です。
山頂の標高での予報(6〜15時)から、登山に向いた天気かを判定しています。山名を押すと7日間の詳細が開きます。</p>
<div class='filters'><input id='q' type='search' placeholder='山名で絞り込み(例: 槍、ふじ)' aria-label='山名で絞り込み'>
<div class='chips'>{chips}</div></div>
<div class='scroll'><table class='grid' id='mt'><thead><tr><th class='nm'>山名</th>{head}</tr></thead>{rows}</table></div>
<div class='note'><h2>判定の基準</h2><ul>
<li><b class='r3'>◎</b> 降水0.5mm未満、風速10m/s未満</li>
<li><b class='r2'>○</b> 降水3mm未満、風速15m/s未満</li>
<li><b class='r1'>△</b> 降水10mm未満、風速20m/s未満</li>
<li><b class='r0'>×</b> それ以上、または雷雨の予報</li></ul>
<p>風速は山頂付近の上空(標高2,200m以上は約3,000m、それ未満は約1,500m)の予報です。稜線の実際の風とは異なることがあり、突風はさらに強くなります。</p></div>
"""


# ---------------------------------------------------------------- スノーボード
def dem_elevation(points):
    """地形の標高(Open-Meteo の90m標高データ)。100地点ずつ"""
    out = []
    for i in range(0, len(points), 100):
        chunk = points[i:i + 100]
        out += fetch("https://api.open-meteo.com/v1/elevation", {
            "latitude": ",".join(str(p[0]) for p in chunk),
            "longitude": ",".join(str(p[1]) for p in chunk)})[0]["elevation"]
    return out


def build_ski():
    """予報はスキー場の座標の地形の標高(elev があればその標高=ゲレンデ中腹)で取得する"""
    ski = json.loads((ROOT / "data/ski.json").read_text(encoding="utf-8"))
    dem = dem_elevation([(s["lat"], s["lon"]) for s in ski])
    for s, z in zip(ski, dem):
        s["elev"] = s.get("elev") or round(z)
    res = forecast([(s["lat"], s["lon"], s["elev"]) for s in ski],
                   daily=",".join(["weather_code", "temperature_2m_max", "temperature_2m_min",
                                   "snowfall_sum", "rain_sum", "wind_gusts_10m_max"]),
                   hourly="snow_depth,freezing_level_height")
    for s, r in zip(ski, res):
        s["days"] = []
        dd, h = r["daily"], r["hourly"]
        for i, d in enumerate(dd["time"]):
            depth = hours_of(h, "snow_depth", d, [9])
            fl = hours_of(h, "freezing_level_height", d, range(6, 18))
            s["days"].append({
                "date": d, "code": dd["weather_code"][i],
                "snow": dd["snowfall_sum"][i] or 0,
                "depth": depth[0] * 100 if depth else None,
                "tmin": dd["temperature_2m_min"][i], "tmax": dd["temperature_2m_max"][i],
                "rain": dd["rain_sum"][i] or 0,
                "fl": min(fl) if fl else None,
                "gust": dd["wind_gusts_10m_max"][i] or 0,
            })
    return ski


SKI_REGIONS = {
    "北海道": ["北海道"],
    "東北": ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"],
    "関東": ["茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県"],
    "甲信越": ["新潟県", "長野県", "山梨県"],
    "北陸": ["富山県", "石川県", "福井県"],
    "東海": ["岐阜県", "静岡県", "愛知県"],
    "近畿": ["三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"],
    "中国・四国": ["鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県"],
    "九州": ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県"],
}
PREF_SKI_REGION = {p: r for r, ps in SKI_REGIONS.items() for p in ps}


def ski_page(ski):
    days = [d["date"] for d in ski[0]["days"]]
    head = "".join(f"<th>{day_label(d)}</th>" for d in days)
    used = []
    rows = ""
    for r in SKI_REGIONS:                       # 地方の順に並べる
        for s in [x for x in ski if PREF_SKI_REGION[x["pref"]] == r]:
            if r not in used:
                used.append(r)
            cells, detail = "", ""
            for d in s["days"]:
                icon, label = wx(d["code"])
                snow_cls = "big" if d["snow"] >= 20 else ("mid" if d["snow"] >= 5 else "muted")
                cells += (f"<td title='{e(label)} {round(d['tmin'])}〜{round(d['tmax'])}℃'>"
                          f"<b class='{snow_cls}'>{d['snow']:.0f}</b><small>{icon}</small></td>")
                powder = " <span class='tag'>パウダー期待</span>" if d["snow"] >= 10 and d["tmax"] <= -2 else ""
                rain = f" <span class='tag warn'>雨{d['rain']:.0f}mm</span>" if d["rain"] >= 1 else ""
                detail += (f"<tr><td>{day_label(d['date'])}</td><td>{icon} {label}{rain}</td>"
                           f"<td>{d['snow']:.0f}cm{powder}</td>"
                           f"<td>{'—' if d['depth'] is None else f'{d['depth']:.0f}cm'}</td>"
                           f"<td>{round(d['tmin'])}〜{round(d['tmax'])}℃</td>"
                           f"<td>{'—' if d['fl'] is None else f'{d['fl']:,.0f}m'}</td>"
                           f"<td>{d['gust']:.0f}m/s</td></tr>")
            total = sum(d["snow"] for d in s["days"])
            rows += (
                f"<tbody class='mt' data-region='{r}' data-name='{e(s['name'] + s.get('yomi', ''))}'>"
                f"<tr class='main'><th class='nm'><button type='button' aria-expanded='false'>"
                f"<span>{e(s['name'])}</span><small>{e(s['pref'])} · 7日間 {total:.0f}cm</small>"
                f"</button></th>{cells}</tr>"
                f"<tr class='detail' hidden><td colspan='{len(days) + 1}'>"
                f"{maplink(s['name'] + ' 第1駐車場', '駐車場の地図')} "
                f"<p class='muted'>予報の標高 {s['elev']:,}m · 標高差 {s['drop']:,}m</p><div class='scroll'><table class='sub'>"
                "<tr><th>日付</th><th>天気</th><th>新雪</th><th>積雪(朝)</th><th>気温</th>"
                f"<th>0℃の高さ(日中)</th><th>最大瞬間風速</th></tr>{detail}</table></div></td></tr></tbody>")
    chips = "<button class='chip on' data-r=''>すべて</button>" + "".join(
        f"<button class='chip' data-r='{r}'>{r}</button>" for r in used)
    cards = f"""
<div class='filters'><input id='q' type='search' placeholder='スキー場名で絞り込み' aria-label='スキー場名で絞り込み'>
<div class='chips'>{chips}</div></div>
<div class='scroll'><table class='grid'><thead><tr><th class='nm'>スキー場</th>{head}</tr></thead>{rows}</table></div>"""
    return f"""
<h1>スノーボード 雪と天気</h1>
<p class='lead'>標高差の大きいスキー場100か所です。表の数字は1日の降雪量(cm)の予報で、スキー場名を押すと積雪・気温・0℃の高さ・風の詳細が開きます。</p>
{cards}
<div class='note'><ul><li><span class='tag'>パウダー期待</span> 新雪10cm以上で、最高気温が-2℃以下</li>
<li><span class='tag warn'>雨</span> 予報の標高で雨の予報(雪質が重くなりやすい)</li>
<li>予報の標高は、Wikipediaにあるスキー場の座標の地形の標高です(一部はゲレンデ中腹の標高)。
ゲレンデ上部の天気は「0℃の高さ」を目安にしてください(それより高い所は雪、低い所は雨になりやすい)。</li>
<li>積雪の予報はモデルの推定で、実際の積雪と大きく異なることがあります。各スキー場の発表を確認してください。</li>
<li>スキー場の一覧・標高差: Wikipedia、Wikidata</li></ul></div>
"""


# ---------------------------------------------------------------- サーフィン
def build_surf():
    spots = json.loads((ROOT / "data/surf.json").read_text(encoding="utf-8"))
    waves = marine([(s["sea_lat"], s["sea_lon"]) for s in spots], hourly=",".join([
        "wave_height", "wave_period", "wave_direction", "swell_wave_height", "swell_wave_period"]))
    winds = forecast([(s["lat"], s["lon"], 0) for s in spots],
                     hourly="wind_speed_10m,wind_direction_10m,weather_code")
    for s, w, f in zip(spots, waves, winds):
        s["grid"] = (w["latitude"], w["longitude"])
        s["days"] = []
        for d in sorted({t[:10] for t in w["hourly"]["time"]}):
            slots = []
            for name, hrs in (("朝", range(6, 12)), ("昼", range(12, 18))):
                hgt = hours_of(w["hourly"], "wave_height", d, hrs)
                per = hours_of(w["hourly"], "wave_period", d, hrs)
                wdir = hours_of(w["hourly"], "wave_direction", d, hrs)
                ws = hours_of(f["hourly"], "wind_speed_10m", d, hrs)
                wd = hours_of(f["hourly"], "wind_direction_10m", d, hrs)
                if not hgt or not ws:
                    continue
                h_, p_, w_ = sum(hgt) / len(hgt), sum(per) / len(per), sum(ws) / len(ws)
                wdeg = mean_dir(wd, ws)
                diff = ang_diff(wdeg, s["facing"])     # 0に近いほど海から吹く
                kind = "オンショア" if diff <= 45 else ("オフショア" if diff >= 135 else "クロス")
                if h_ < 0.3:
                    score = 0
                elif h_ > 3.0:
                    score = 0
                else:
                    score = 3 if 0.6 <= h_ <= 2.0 and p_ >= 7 else 2
                    if p_ < 5:
                        score -= 1
                    if kind == "オンショア" and w_ >= 5:
                        score -= 1
                    if kind == "オフショア" and w_ <= 6 and score < 3:
                        score += 1
                    if h_ > 2.0:
                        score = min(score, 1)
                    score = max(score, 0)
                slots.append({"slot": name, "height": h_, "period": p_,
                              "wave_dir": mean_dir(wdir), "wind": w_, "wind_dir": wdeg,
                              "kind": kind, "score": score,
                              "code": max(hours_of(f["hourly"], "weather_code", d, hrs) or [None])})
            s["days"].append({"date": d, "slots": slots})
    return spots


def surf_page(spots):
    cards = ""
    for s in spots:
        rows = ""
        for d in s["days"]:
            for i, sl in enumerate(d["slots"]):
                mark, cls = RATE[sl["score"]]
                size = ("フラット" if sl["height"] < 0.3 else "ひざ〜もも" if sl["height"] < 0.6 else
                        "こし〜むね" if sl["height"] < 1.2 else "かた〜あたま" if sl["height"] < 2.0 else
                        "頭オーバー")
                date_cell = (f"<td rowspan='{len(d['slots'])}'>{day_label(d['date'])}</td>" if i == 0 else "")
                rows += (f"<tr>{date_cell}<td>{sl['slot']}</td><td class='{cls}'><b>{mark}</b></td>"
                         f"<td>{sl['height']:.1f}m <small>{size}</small></td><td>{sl['period']:.0f}秒</td>"
                         f"<td>{dir_name(sl['wave_dir'])}</td>"
                         f"<td>{dir_name(sl['wind_dir'])} {sl['wind']:.0f}m/s <small>{sl['kind']}</small></td>"
                         f"<td>{wx(sl['code'])[0]}</td></tr>")
        cards += (f"<section class='card'><h3>{e(s['name'])} <small>{e(s['pref'])} · "
                  f"浜の向き {dir_name(s['facing'])}</small> {maplink(s['parking'], '駐車場の地図')}</h3>"
                  "<div class='scroll'><table class='sub'>"
                  "<tr><th>日付</th><th></th><th>判定</th><th>波の高さ</th><th>周期</th><th>波の向き</th>"
                  f"<th>風</th><th>天気</th></tr>{rows}</table></div>"
                  f"<p class='muted'>波は沖合({s['grid'][0]:.2f}, {s['grid'][1]:.2f})の予報</p></section>")
    return f"""
<h1>サーフィン 波と風</h1>
<p class='lead'>朝(6〜11時)と昼(12〜17時)の平均です。波の高さは沖合の予報で、浜では地形や向きにより小さくなることが多いです。</p>
{cards}
<div class='note'><h2>判定の基準</h2><ul>
<li><b class='r3'>◎</b> 波0.6〜2.0mで周期7秒以上(オフショアの弱い風なら一段上げる)</li>
<li><b class='r1'>△</b> 周期5秒未満の風波、5m/s以上のオンショア、2mを超える大きな波(上級者向け)で一段下げる</li>
<li><b class='r0'>×</b> 0.3m未満(フラット)、または3mを超える荒れた海</li>
<li>オフショア=陸から海へ吹く風(波の面が整いやすい)、オンショア=海から陸へ吹く風</li></ul></div>
"""


def _size(h):
    return ("フラット" if h < 0.3 else "ひざ〜もも" if h < 0.6 else "こし〜むね" if h < 1.2
            else "かた〜あたま" if h < 2.0 else "頭オーバー")


def _swell(p):
    return ("長い周期の良いうねり" if p >= 10 else "まとまりのあるうねり" if p >= 7
            else "短めの周期の風波まじり" if p >= 5 else "まとまりのない風波")


def surf_comments(spots, we):
    """週末の予報から、海側ごとに2〜3文のコメントを作る(数値からの自動生成)"""
    out = []
    for coast in ("太平洋側", "日本海側"):
        group = [s for s in spots if s.get("coast") == coast]
        slots = [(s, d["date"], sl) for s in group for d in s["days"] if d["date"] in we for sl in d["slots"]]
        if not slots:
            continue
        s, dt, sl = max(slots, key=lambda x: (x[2]["score"], -abs(x[2]["height"] - 1.2)))
        dd = date.fromisoformat(dt)
        when = f"{WEEK[dd.weekday()]}曜の{sl['slot']}"
        if sl["score"] == 0:
            text = (f"週末は{'波がほとんどなく' if sl['height'] < 0.3 else '海が荒れて'}、"
                    "サーフィンには厳しそうです。")
        else:
            text = (f"{when}の{s['name']}が{RATE[sl['score']][0]}。{_size(sl['height'])}({sl['height']:.1f}m)の"
                    f"{dir_name(sl['wave_dir'])}からの{_swell(sl['period'])}(周期{sl['period']:.0f}秒)に、"
                    f"{dir_name(sl['wind_dir'])}の風{sl['wind']:.0f}m/s({sl['kind']})の予報です。")
        # 土日の波の高さの変化
        if len(we) == 2:
            avg = [sum(x[2]["height"] for x in slots if x[1] == d) / max(1, sum(1 for x in slots if x[1] == d))
                   for d in we]
            diff = avg[1] - avg[0]
            text += ("日曜にかけて波は上がり調子。" if diff >= 0.3 else
                     "日曜にかけて波は下がり気味。" if diff <= -0.3 else "土日とも波の大きさはほぼ同じです。")
        if any(x[2]["kind"] == "オンショア" and x[2]["wind"] >= 5 for x in slots):
            text += "オンショアが強まる時間帯は面が乱れそうです。"
        if any(x[2]["height"] > 2.0 for x in slots):
            text += "大きめの時間帯もあるので、無理は禁物です。"
        out.append((coast, text))
    return out


# ---------------------------------------------------------------- トップ
def index_page(mts, ski, spots):
    days = [d["date"] for d in mts[0]["days"]]
    we = weekend(days) or days[:2]
    label = "・".join(f"{date.fromisoformat(d).month}/{date.fromisoformat(d).day}"
                      f"({WEEK[date.fromisoformat(d).weekday()]})" for d in we)
    # 登山: 週末の判定の合計が高い山
    best = sorted(mts, key=lambda m: (-sum(d["score"] for d in m["days"] if d["date"] in we),
                                      max(d["wind"] for d in m["days"] if d["date"] in we)))
    good = [m for m in best if all(d["score"] >= 2 for d in m["days"] if d["date"] in we)]
    mt_rows = ""
    for m in good[:15]:
        cells = ""
        for d in m["days"]:
            if d["date"] in we:
                icon, _ = wx(d["code"])
                cells += (f"<td class='{RATE[d['score']][1]}'><b>{RATE[d['score']][0]}</b> {icon}"
                          f"<small class='muted'> {round(d['tmin'])}〜{round(d['tmax'])}℃ · "
                          f"風{d['wind']:.0f}m/s</small></td>")
        tag = " <span class='tag'>関西の名山</span>" if m.get("list") == "関西" else ""
        mt_rows += (f"<tr><td class='l'><b>{e(m['name'])}</b>{tag}<br><small class='muted'>"
                    f"{m['elev']:,}m · {e('・'.join(m['prefs']))}</small></td>{cells}"
                    f"<td>{maplink(m['trailhead'], '登山口')}</td></tr>")
    we_head = "".join(f"<th>{day_label(d)}</th>" for d in we)
    mt_list = (f"<div class='scroll'><table class='sub'><tr><th>山</th>{we_head}<th>地図</th></tr>"
               f"{mt_rows}</table></div>" if mt_rows else "<p>週末に条件の良い山は見当たりません</p>")
    # スノー: 週末までの降雪が多い順
    upto = we[-1]
    snow = sorted(ski, key=lambda s: -sum(d["snow"] for d in s["days"] if d["date"] <= upto))
    ski_list = "".join(
        f"<li><b>{e(s['name'])}</b> <small>{e(s['pref'])}</small> "
        f"週末までの降雪 {sum(d['snow'] for d in s['days'] if d['date'] <= upto):.0f}cm "
        f"{maplink(s['name'] + ' 第1駐車場', '駐車場')}</li>"
        for s in snow[:5])
    # よく行くスキー場(data/favorites.json)は常に表示する
    favs = json.loads((ROOT / "data/favorites.json").read_text(encoding="utf-8")).get("ski", [])
    by_name = {s["name"]: s for s in ski}
    fav_rows = ""
    for n in favs:
        s = by_name.get(n)
        if not s:
            continue
        cells = "".join(f"<td>{wx(d['code'])[0]} {d['snow']:.0f}cm</td>"
                        for d in s["days"] if d["date"] in we)
        until = sum(d["snow"] for d in s["days"] if d["date"] <= upto)
        fav_rows += (f"<tr><td class='l'><b>{e(n)}</b><br><small class='muted'>週末まで {until:.0f}cm</small></td>"
                     f"{cells}<td>{maplink(n + ' 第1駐車場', '駐車場')}</td></tr>")
    fav = (f"<h3>よく行くスキー場</h3><div class='scroll'><table class='sub'><tr><th></th>"
           + "".join(f"<th>{day_label(d)}</th>" for d in we)
           + f"<th></th></tr>{fav_rows}</table></div><h3 style='margin-top:14px'>週末までの降雪が多い順</h3>"
           if fav_rows else "<p class='muted'>週末までの降雪が多い順</p>")
    # サーフ: 予報コメント + 週末4枠(土朝・土昼・日朝・日昼)の判定表
    surf_comment = "".join(f"<p class='comment'><b>{c}</b> {e(t)}</p>"
                           for c, t in surf_comments(spots, we))
    heads = "".join(f"<th>{day_label(d)}<br><small>{sl}</small></th>" for d in we for sl in ("朝", "昼"))
    surf_rows = ""
    for s in spots:
        cells = ""
        for d in s["days"]:
            if d["date"] not in we:
                continue
            for sl in d["slots"]:
                mark, cls = RATE[sl["score"]]
                cells += (f"<td class='{cls}' title='{sl['height']:.1f}m {sl['period']:.0f}秒 {sl['kind']}'>"
                          f"<b>{mark}</b><small class='muted'>{sl['height']:.1f}m</small></td>")
        surf_rows += (f"<tr><td class='l'><b>{e(s['name'])}</b></td>{cells}"
                      f"<td>{maplink(s['parking'], '駐車場')}</td></tr>")
    surf_list = (f"{surf_comment}<div class='scroll'><table class='sub mini'><tr><th></th>{heads}<th></th></tr>"
                 f"{surf_rows}</table></div>")
    return f"""
<h1>今週末の山と雪と波</h1>
<p class='lead'>{label} の予報から、よさそうな場所をまとめました。</p>
<section class='card big'><h2><a href='mountains.html'>⛰️ 登山</a></h2>
<p class='muted'>週末の両日が○以上の山(上位15座、判定が良く風の弱い順)。気温・風は山頂付近の6〜15時</p>{mt_list}
<p><a href='mountains.html'>百名山＋関西の名山をすべて見る →</a></p></section>
<div class='cards2'>
<section class='card'><h2><a href='snow.html'>🏂 スノーボード</a></h2>{fav}<ul>{ski_list}</ul>
<p><a href='snow.html'>スキー場の詳細 →</a></p></section>
<section class='card'><h2><a href='surf.html'>🏄 サーフィン</a></h2>{surf_list}
<p><a href='surf.html'>波の詳細 →</a></p></section>
</div>
"""


# ---------------------------------------------------------------- 共通レイアウト
CSS = """
:root{color-scheme:light;--bg:#f6f7f5;--card:#fff;--line:#e2e4df;--text:#14171a;--sub:#55595e;--muted:#868a8f;
--accent:#1f6fb2;--r3:#0b7a3b;--r2:#2a78d6;--r1:#b27300;--r0:#c4312f;--sat:#2a6fd6;--sun:#d03b3b;--hl:#e8f1fb}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#151617;--card:#1e2022;--line:#34373a;--text:#eef0f2;
--sub:#b9bdc2;--muted:#8c9196;--accent:#6aaee8;--r3:#47c47c;--r2:#5fa3ef;--r1:#e0a43a;--r0:#ef6b69;--sat:#6aa6f0;
--sun:#f07a7a;--hl:#1f2a36}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.65 system-ui,-apple-system,"Hiragino Sans","Noto Sans JP","Yu Gothic UI",sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
nav{max-width:1000px;margin:0 auto;padding:0 16px;display:flex;gap:4px;overflow-x:auto}
nav a{padding:12px 12px;color:var(--sub);text-decoration:none;white-space:nowrap;border-bottom:3px solid transparent}
nav a.on{color:var(--text);border-color:var(--accent);font-weight:600}
main{max-width:1000px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:4px 0 6px}h2{font-size:17px;margin:24px 0 8px}h3{font-size:16px;margin:0 0 8px}
h3 small,li small{color:var(--muted);font-weight:400}
.lead{color:var(--sub);margin:0 0 16px}.muted{color:var(--muted);font-size:13px}
a{color:var(--accent)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
.cards2{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.cards2 .card{margin:0}.card h2{margin-top:0}.card h2 a{text-decoration:none;color:var(--text)}
.card ul{padding-left:18px;margin:6px 0}.card.big{margin:0 0 12px}
.sub td.l{white-space:normal;min-width:120px}
.mapbtn{display:inline-block;font-size:12px;padding:3px 10px;border-radius:99px;border:1px solid var(--line);
background:var(--card);color:var(--accent);text-decoration:none;white-space:nowrap}
.mapbtn:hover{border-color:var(--accent)}
tr.detail .mapbtn{margin:0 0 8px}
.comment{background:var(--hl);border-radius:8px;padding:8px 12px;margin:0 0 8px;font-size:14px}
.sub.mini td,.sub.mini th{text-align:center;padding:4px 6px}.sub.mini td b{display:block}
.sub.mini td.l{text-align:left}
.scroll{overflow-x:auto}
table{border-collapse:collapse;font-variant-numeric:tabular-nums}
.sub{width:100%}.sub th,.sub td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
.sub th{color:var(--sub);font-weight:500;font-size:13px}
.grid{width:100%;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.grid th,.grid td{padding:4px 2px;text-align:center;border-bottom:1px solid var(--line)}
.grid thead th{font-size:12px;color:var(--sub);font-weight:500;padding:8px 2px}
.grid td b{display:block;font-size:16px;line-height:1.2}.grid td small{font-size:12px}
.grid .nm{text-align:left;min-width:120px;padding-left:10px}
.nm button{all:unset;cursor:pointer;display:block}.nm button span{font-weight:600}
.nm button small{display:block;color:var(--muted);font-size:11px;font-weight:400}
.nm button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
tr.detail td{background:var(--hl);text-align:left;padding:8px}
small{font-size:12px}
.sat{color:var(--sat)}.sun{color:var(--sun)}
.r3{color:var(--r3)}.r2{color:var(--r2)}.r1{color:var(--r1)}.r0{color:var(--r0)}
.big{font-weight:700;color:var(--r2)}.mid{font-weight:600}
.tag{display:inline-block;font-size:11px;padding:0 6px;border-radius:99px;background:var(--hl);color:var(--accent)}
.tag.warn{color:var(--r1)}
.filters{display:flex;flex-direction:column;gap:8px;margin:8px 0 12px}
#q{font:inherit;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--text);max-width:320px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font:inherit;font-size:13px;padding:4px 12px;border-radius:99px;border:1px solid var(--line);background:var(--card);color:var(--sub);cursor:pointer}
.chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
.note{color:var(--sub);font-size:13px;margin-top:24px}.note ul{padding-left:18px}
footer{max-width:1000px;margin:0 auto;padding:16px;color:var(--muted);font-size:12px;border-top:1px solid var(--line)}
"""

JS = """
const q=document.getElementById('q');
if(q){
  let region='';
  const apply=()=>{const t=q.value.trim();
    document.querySelectorAll('tbody.mt').forEach(b=>{
      b.hidden=!((!region||b.dataset.region===region)&&(!t||b.dataset.name.includes(t)));});};
  q.addEventListener('input',apply);
  document.querySelectorAll('.chip').forEach(c=>c.addEventListener('click',()=>{
    document.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x===c));
    region=c.dataset.r;apply();}));
  document.querySelectorAll('.nm button').forEach(btn=>btn.addEventListener('click',()=>{
    const d=btn.closest('tbody').querySelector('tr.detail');d.hidden=!d.hidden;
    btn.setAttribute('aria-expanded',String(!d.hidden));}));
}
"""

PAGES = [("index.html", "週末"), ("mountains.html", "登山"), ("snow.html", "スノーボード"),
         ("surf.html", "サーフィン")]


def layout(file, title, body, updated):
    nav = "".join(f"<a href='{f}' class='{'on' if f == file else ''}'>{n}</a>" for f, n in PAGES)
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} | 山と雪と波の天気</title>
<meta name="description" content="日本百名山と関西の名山の登山天気、スキー場の降雪、サーフポイントの波と風の予報をまとめたサイト">
<style>{CSS}</style></head><body>
<header><nav>{nav}</nav></header>
<main>{body}<p class='muted'>更新: {updated:%Y年%-m月%-d日 %H:%M}(3時間ごとに自動更新)</p></main>
<footer>予報は目安です。登山・海に出る前に、気象庁の警報・注意報と現地の情報を必ず確認してください。<br>
気象データ: <a href="https://open-meteo.com/">Open-Meteo.com</a>(CC BY 4.0) ·
山の一覧・座標: Wikipedia「日本百名山」、Wikidata</footer>
<script>{JS}</script></body></html>"""


def main():
    updated = datetime.now(JST)
    mts, ski, spots = build_mountains(), build_ski(), build_surf()
    OUT.mkdir(exist_ok=True)
    bodies = {"index.html": ("今週末", index_page(mts, ski, spots)),
              "mountains.html": ("登山天気", mountains_page(mts)),
              "snow.html": ("スノーボード", ski_page(ski)),
              "surf.html": ("サーフィン", surf_page(spots))}
    for f, (title, body) in bodies.items():
        (OUT / f).write_text(layout(f, title, body, updated), encoding="utf-8")
    print("書き出し:", ", ".join(bodies), "→", OUT)


if __name__ == "__main__":
    main()
