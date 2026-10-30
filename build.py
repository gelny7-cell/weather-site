"""山と雪と波の天気まとめ。Open-Meteo の予報を取得して public/ に静的なHTMLを書き出す。
使い方: python build.py  (GitHub Actions で3時間ごとに実行。Python標準ライブラリのみ)"""
import html
import json
import math
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
OUT = ROOT / "public"
JST = ZoneInfo("Asia/Tokyo")
DAYS = 16          # 取得する日数(APIの上限)。値のない日は表示しない
TOP_DAYS = 7       # トップページに出す日数
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
        try:
            out += fetch("https://api.open-meteo.com/v1/forecast", {
                "latitude": ",".join(str(p[0]) for p in chunk),
                "longitude": ",".join(str(p[1]) for p in chunk),
                "elevation": ",".join(str(p[2]) for p in chunk),
                "timezone": "Asia/Tokyo", "wind_speed_unit": "ms", "forecast_days": DAYS, **params})
        except Exception as ex:        # 取得できなかった地点は表示しない
            print(f"天気の取得に失敗({len(chunk)}地点): {type(ex).__name__}")
            out += [None] * len(chunk)
    return out


def marine(points, **params):
    out = []
    for lat, lon in points:
        try:
            out += fetch("https://marine-api.open-meteo.com/v1/marine", {
                "latitude": lat, "longitude": lon, "timezone": "Asia/Tokyo",
                "forecast_days": DAYS, **params})
        except Exception as ex:        # 取得できなかった地点は表示しない
            print(f"波の取得に失敗({lat}, {lon}): {type(ex).__name__}")
            out.append(None)
    return out


def align(items, what):
    """取得できなかった地点を除き、全地点で値のそろった日だけを残す(表に空欄を作らない)。
    1地点も取得できなければ例外にして、前回公開したサイトをそのまま残す"""
    items = [x for x in items if x.get("days")]
    if not items:
        raise RuntimeError(f"{what}の予報を1地点も取得できませんでした")
    common = set.intersection(*({d["date"] for d in x["days"]} for x in items))
    for x in items:
        x["days"] = [d for d in x["days"] if d["date"] in common]
    return items


def hours_of(h, key, day, hours):
    """hourly データから、指定日・指定時間帯の値だけを取り出す"""
    return [v for t, v in zip(h["time"], h[key])
            if t[:10] == day and int(t[11:13]) in hours and v is not None]


# ---------------------------------------------------------------- 警報・注意報、火山(気象庁)
# 警報・注意報の種類コード: (名前, レベル)。レベル20=注意報、30以上=警報(40=危険警報、50=特別警報)
WARN_KINDS = {
    "02": ("暴風雪警報", 30), "03": ("レベル3大雨警報", 30), "05": ("暴風警報", 30), "06": ("大雪警報", 30),
    "07": ("波浪警報", 30), "08": ("レベル3高潮警報", 30), "09": ("レベル3土砂災害警報", 30),
    "10": ("レベル2大雨注意報", 20), "12": ("大雪注意報", 20), "13": ("風雪注意報", 20), "14": ("雷注意報", 20),
    "15": ("強風注意報", 20), "16": ("波浪注意報", 20), "17": ("融雪注意報", 20), "19": ("レベル2高潮注意報", 20),
    "20": ("濃霧注意報", 20), "21": ("乾燥注意報", 20), "22": ("なだれ注意報", 20), "23": ("低温注意報", 20),
    "24": ("霜注意報", 20), "25": ("着氷注意報", 20), "26": ("着雪注意報", 20), "29": ("レベル2土砂災害注意報", 20),
    "30": ("レベル3氾濫警報", 30), "31": ("レベル3氾濫警報", 30), "32": ("暴風雪特別警報", 50),
    "33": ("レベル5大雨特別警報", 50), "35": ("暴風特別警報", 50), "36": ("大雪特別警報", 50), "37": ("波浪特別警報", 50),
    "38": ("レベル5高潮特別警報", 50), "39": ("レベル5土砂災害特別警報", 50), "40": ("レベル4氾濫危険警報", 40),
    "41": ("レベル4氾濫危険警報", 40), "43": ("レベル4大雨危険警報", 40), "48": ("レベル4高潮危険警報", 40),
    "49": ("レベル4土砂災害危険警報", 40), "51": ("レベル5氾濫特別警報", 50), "53": ("レベル5氾濫特別警報", 50),
}


def fetch_alerts():
    """いま出ている警報・注意報。{市町村(class20)のコード: [(名前, レベル)]}。取れなければ空"""
    try:
        reports = fetch("https://www.jma.go.jp/bosai/warning/data/r8/map.json", {})
    except Exception as ex:
        print(f"警報・注意報の取得に失敗: {type(ex).__name__}")
        return {}
    out = {}
    for r in sorted(reports, key=lambda r: r["reportDatetime"]):     # 同じ地域は新しい発表で上書き
        for a in r.get("warning", {}).get("class20Items", []):
            out[a["areaCode"]] = sorted({WARN_KINDS[k["code"]] for k in a["kinds"]
                                         if k.get("status") in ("発表", "継続") and k.get("code") in WARN_KINDS},
                                        key=lambda x: -x[1])
    return out


def fetch_volcanoes():
    """噴火警報・予報が出ている火山。[(緯度, 経度, 火山名, 警報の名前, 発表日)]。取れなければ空。
    一覧に載っていない火山は噴火警戒レベル1(または警報なし)"""
    try:
        places = {v["code"]: (float(v["latlon"][0]), float(v["latlon"][1]), v["name_jp"])
                  for v in fetch("https://www.jma.go.jp/bosai/volcano/const/volcano_list.json", {})
                  if v.get("latlon") and v.get("code")}     # 位置のない項目(海域など)は除く
        reports = fetch("https://www.jma.go.jp/bosai/volcano/data/warning.json", {})
    except Exception as ex:
        print(f"火山の情報の取得に失敗: {type(ex).__name__}")
        return []
    now = {}
    for r in sorted(reports, key=lambda r: r["reportDatetime"]):
        for vi in r["volcanoInfos"]:
            if vi["type"] != "噴火警報・予報（対象火山）":
                continue
            for it in vi["items"]:
                for a in it["areas"]:
                    now[a["code"]] = (it["name"], r["reportDatetime"][:10])
    now = {c: v for c, v in now.items() if "留意" not in v[0]}    # 「活火山であることに留意」(レベル1相当)は除く
    return [(*places[c][:2], places[c][2], name, day) for c, (name, day) in now.items() if c in places]


def km_between(lat1, lon1, lat2, lon2):
    return 6371 * math.hypot(math.radians(lon2 - lon1) * math.cos(math.radians(lat1)), math.radians(lat2 - lat1))


# ページごとに関係のない警報・注意報(名前に含まれる言葉)。山で波浪注意報などを出さないため
ALERT_SKIP = {
    "mountain": ("波浪", "高潮", "氾濫", "乾燥", "霜"),
    "ski": ("波浪", "高潮", "氾濫", "乾燥", "霜"),
    "surf": ("大雪", "なだれ", "低温", "霜", "乾燥", "着氷", "着雪", "融雪", "土砂災害", "氾濫"),
}


def attach_alerts(items, alerts, volcanoes, near_volcano, page):
    """各場所に、その市町村の警報・注意報(そのページに関係のあるもの)と、10km以内の火山の噴火警報を付ける"""
    skip = ALERT_SKIP[page]
    for x in items:
        muni = x.get("muni")
        found = {k for code, ks in alerts.items() if muni and code.startswith(muni) for k in ks
                 if not any(w in k[0] for w in skip)}
        x["alerts"] = sorted(found, key=lambda k: (-k[1], k[0]))
        x["volcano"] = None
        if near_volcano:
            close = [v for v in volcanoes if km_between(x["lat"], x["lon"], v[0], v[1]) <= 10]
            x["volcano"] = close[0] if close else None


def fid(kind, x):
    """お気に入りの目印。場所の追加で並び順が変わっても崩れないよう名前で作る(同名の雲取山は県で区別)"""
    return {"m": f"m:{x['name']}:{x['prefs'][0]}" if "prefs" in x else "", "s": f"s:{x['name']}", "w": f"w:{x['name']}"}[kind]


def star(kind, x):
    """詳細の一番上に出す「☆ お気に入り登録」ボタン(押すと登録済みに。登録は見ている人のブラウザに保存する)"""
    return (f"<button type='button' class='star' data-fid='{e(fid(kind, x))}' aria-pressed='false' "
            f"aria-label='{e(x['name'])}をお気に入りに登録'>☆ お気に入り登録</button>")


def hutlink(m):
    """山小屋の予約先の一覧ボタン(data の booking)。予約サイトの小屋を山頂に近い順に、その後に公式HPの小屋を並べる。
    選ぶとその予約ページ・公式HPが開く"""
    if not m.get("booking") and not m.get("tent_booking"):
        return ""
    def item(b, icon=""):
        return (f"<a href='{e(b['url'])}' class='mapbtn' target='_blank' rel='noopener'>"
                f"{icon}{e(b['name'])}<small class='muted'>({e(b['site'])})</small></a>")
    # テント場は、予約先(Yamatan・小屋の予約先)が分かるものだけ ⛺ を付けて後ろに並べる
    items = "".join(item(b) for b in m.get("booking", [])) + "".join(item(b, "⛺ ") for b in m.get("tent_booking", []))
    return (f"<details class='menu'><summary class='mapbtn'>🏠 山小屋</summary>"
            f"<div class='menu-list'>{items}</div></details> ")


def intro(x):
    """詳細の一番上に出す一言紹介(data の intro)。山は最後に山小屋・テント場の数を印と数字だけで付ける
    (範囲と出典はページ下部の説明に)"""
    fac = "".join(f" <span class='fac' title='{label}'>{icon}{len(x[k])}</span>"
                  for k, icon, label in (("huts", "🏠", "山小屋"), ("tents", "⛺", "テント場")) if x.get(k))
    text = e(x.get("intro", ""))
    return f"<p class='intro'>{text}{fac}</p>" if text or fac else ""


def alert_tags(x):
    """一覧の名前の横に付ける印(火山・警報・注意報)"""
    out = ""
    if x.get("volcano"):
        out += f" <span class='tag danger'>🌋 {e(x['volcano'][3].split('（')[0])}</span>"
    lv = max((k[1] for k in x.get("alerts", [])), default=0)
    if lv >= 30:
        out += " <span class='tag danger'>警報</span>"
    elif lv:
        out += " <span class='tag warn'>注意報</span>"
    return out


def alert_box(x):
    """詳細の上に出す、火山・警報・注意報の内容"""
    out = ""
    if x.get("volcano"):
        lat, lon, name, warn, day = x["volcano"]
        out += (f"<p class='alert'>🌋 {e(name)}: {e(warn)}"
                f"<small class='muted'>(気象庁 {int(day[5:7])}/{int(day[8:10])}発表)</small></p>")
    if x.get("alerts"):
        out += f"<p class='alert'>⚠️ 気象庁: {e('・'.join(k[0] for k in x['alerts']))}</p>"
    return out


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


def tags(m):
    """入っている一覧の印(バッジ)。日本百名山と関西百名山の両方に入る山は両方付ける"""
    shown = ["日本百名山" if t == "百名山" else t for t in m.get("lists", [])]
    return "".join(f" <span class='mk{' hyaku' if t == '日本百名山' else ''}' title='{t}' role='img' aria-label='{t}'>"
                   "</span>" for t in shown)


def trailhead(m):
    """登山口の検索語。登録がなければ「山名 登山口 都道府県」で検索する(同名の山と区別するため)"""
    return m.get("trailhead") or f"{m['name']} 登山口 {m['prefs'][0]}"


def trailhead_label(m):
    """登山口ボタンの文字。検索語の頭の山名(「利尻山 鴛泊登山口」の「利尻山」)は外す。登録がなければ「登山口」"""
    th = m.get("trailhead")
    if not th:
        return "登山口"
    head, _, rest = th.partition(" ")
    if rest in ("登山口", "駐車場"):     # 「雪彦山 登山口」のように山名を外すと場所が分からなくなるもの
        return th
    return rest if rest and (head in (m["name"], m.get("yomi")) or head.endswith(("山", "岳", "峰", "嶽"))) else th


def sitelink(url, label, icon=""):
    """外部サイトへのボタン(新しいタブで開く)。icon は public/ に置いた画像のファイル名"""
    img = f"<img src='{icon}' alt='' width='14' height='14'>" if icon else ""
    return f"<a class='mapbtn' href='{e(url)}' target='_blank' rel='noopener'>{img}{e(label)}</a>"


def routelinks(m):
    """ヤマレコ・YAMAP の山のページへのボタン。番号が分からない山は出さない"""
    out = ""
    if m.get("yamareco"):
        out += sitelink(f"https://www.yamareco.com/modules/yamainfo/ptinfo.php?ptid={m['yamareco']}", "ヤマレコ", "yamareco.png") + " "
    if m.get("yamap"):
        out += sitelink(f"https://yamap.com/mountains/{m['yamap']}", "YAMAP", "yamap.ico") + " "
    return out


# ご当地の名物(Google マップで検索する言葉)。地域(中心・半径km)に入る場所はその名物、入らなければ県の名物。上から順に見る
FOOD_AREAS = [
    (45.18, 141.24, 25, "ウニ丼"),            # 利尻
    (44.05, 145.0, 45, "海鮮丼"),             # 知床(羅臼・斜里)
    (43.33, 142.38, 30, "オムカレー"),         # 富良野・美瑛
    (43.08, 142.85, 60, "豚丼"),              # 十勝(サホロ・トムラウシ・トマム)
    (43.70, 142.65, 35, "旭川ラーメン"),       # 旭川・大雪山・ぴっぷ
    (43.15, 140.95, 25, "寿司"),              # 小樽(キロロ・朝里)
    (43.00, 141.25, 25, "スープカレー"),       # 札幌(テイネ・国際・藻岩・ばんけい)
    (39.53, 141.49, 25, "ジンギスカン"),       # 遠野(早池峰)
    (38.17, 140.40, 8, "ジンギスカン"),        # 蔵王温泉
    (37.65, 140.02, 22, "喜多方ラーメン"),     # 裏磐梯・猪苗代
    (37.85, 140.10, 20, "米沢牛"),            # 米沢(吾妻・天元台)
    (36.78, 139.45, 15, "ゆば"),              # 日光
    (37.20, 139.45, 30, "ソースカツ丼"),       # 南会津(燧・会津駒・たかつえ)
    (37.10, 139.97, 20, "そば"),              # 那須
    (35.36, 138.73, 20, "吉田うどん"),         # 富士山
    (34.86, 139.05, 20, "わさび丼"),           # 天城山
    (36.29, 137.57, 6, "飛騨牛"),             # 新穂高(笠ヶ岳・焼岳)
    (36.25, 137.70, 25, "山賊焼"),            # 松本・上高地・乗鞍
    (36.15, 137.25, 25, "飛騨牛"),            # 飛騨高山・ほおのき平
    (35.73, 137.90, 18, "ソースかつ丼"),       # 駒ヶ根(木曽駒・空木)
    (35.95, 136.55, 15, "おろしそば"),         # 大野・勝山(荒島岳)
    (35.90, 136.88, 20, "鶏ちゃん"),           # 郡上(ダイナランド・高鷲・白鳥・めいほう)
    (36.57, 137.45, 18, "ます寿司"),           # 立山・剱・有峰
    (36.15, 136.75, 22, "治部煮"),             # 白山
    (35.50, 135.65, 30, "焼き鯖"),            # 若狭(小浜・高浜・難波江)
    (35.30, 135.90, 15, "鯖寿司"),            # 湖西・朽木(鯖街道)
    (35.55, 136.25, 22, "焼鯖そうめん"),       # 湖北(長浜・余呉・伊吹)
    (35.00, 136.45, 22, "とんてき"),           # 鈴鹿(御在所・鎌ヶ岳・藤原岳)
    (34.32, 136.85, 25, "海鮮丼"),             # 伊勢志摩(国府の浜)
    (34.70, 136.12, 10, "伊賀牛"),             # 伊賀
    (34.48, 136.35, 17, "松阪牛"),             # 松阪・奥伊勢(局ヶ岳)
    (35.46, 134.87, 12, "出石そば"),           # 出石(東床尾山)
    (35.65, 135.10, 25, "海鮮丼"),             # 丹後(八丁浜・依遅ヶ尾山・由良ヶ岳)
    (35.38, 134.55, 30, "但馬牛"),             # 但馬(ハチ北・ハチ高原・氷ノ山)
    (35.08, 135.22, 18, "ぼたん鍋"),           # 丹波篠山
    (34.72, 135.20, 15, "そばめし"),           # 神戸(六甲・摩耶)
    (35.00, 134.65, 35, "そうめん"),           # 播磨(揖保乃糸の産地。雪彦山・千ヶ峰・段ヶ峰)
    (34.25, 134.85, 20, "海鮮丼"),             # 淡路(諭鶴羽山)
    (34.53, 135.85, 8, "三輪そうめん"),        # 三輪山
    (34.22, 135.58, 15, "ごま豆腐"),           # 高野山(楊柳山・護摩壇山・伯母子岳)
    (33.75, 135.60, 45, "めはり寿司"),         # 熊野(法師山・大塔山・玉置山)
    (34.26, 135.09, 10, "しらす丼"),           # 加太・磯ノ浦
    (33.85, 134.10, 30, "祖谷そば"),           # 剣山
    (31.88, 130.88, 15, "黒豚"),              # 霧島
    (30.35, 130.52, 40, "トビウオ"),           # 屋久島
]
PREF_FOOD = {
    "北海道": "ジンギスカン", "青森県": "煮干しラーメン", "岩手県": "冷麺", "宮城県": "牛タン", "秋田県": "稲庭うどん",
    "山形県": "肉そば", "福島県": "ソースカツ丼", "茨城県": "そば", "栃木県": "ゆば", "群馬県": "うどん", "埼玉県": "わらじかつ丼",
    "神奈川県": "豆腐料理", "新潟県": "へぎそば", "富山県": "富山ブラック", "石川県": "治部煮", "福井県": "おろしそば",
    "山梨県": "ほうとう", "長野県": "そば", "岐阜県": "鶏ちゃん", "静岡県": "富士宮やきそば", "三重県": "伊勢うどん",
    "滋賀県": "近江ちゃんぽん", "京都府": "鯖寿司", "大阪府": "お好み焼き", "兵庫県": "そばめし", "奈良県": "柿の葉寿司",
    "和歌山県": "和歌山ラーメン", "鳥取県": "牛骨ラーメン", "広島県": "お好み焼き", "徳島県": "徳島ラーメン", "愛媛県": "鯛めし",
    "大分県": "とり天", "熊本県": "あか牛丼", "宮崎県": "チキン南蛮", "鹿児島県": "黒豚",
}


def local_food(lat, lon, pref):
    """その場所のご当地の名物。地域に入らず、県の名物も決めていなければ None"""
    for alat, alon, r, food in FOOD_AREAS:
        if 6371 * math.hypot(math.radians(lon - alon) * math.cos(math.radians(lat)), math.radians(lat - alat)) <= r:
            return food
    return PREF_FOOD.get(pref)


# 「飲食店」のジャンル(表示名, 検索する言葉)。ページごとに、よく使いそうなものを上に置く
# 飲食店のジャンル(どのページも同じ並び。ご当地はこの前に入る)
SHOP_GENRES = [("🍱 定食・食堂", "定食"), ("🍜 ラーメン", "ラーメン"), ("🍲 うどん・そば", "うどん そば"), ("🍣 海鮮", "海鮮"),
               ("🥩 焼肉", "焼肉"), ("🍽️ ファミレス", "ファミレス"), ("☕ カフェ", "カフェ"), ("🍰 スイーツ", "スイーツ")]


def foodlink(lat, lon, pref, page):
    """「飲食店」ボタン(押すとジャンルの一覧が開き、選ぶと Google マップでその地点の周りを探す)。
    その場所の名物(ご当地)があれば一覧の一番上に入れる"""
    food = local_food(lat, lon, pref)
    genres = ([(f"🏷️ {food}", food)] if food else []) + SHOP_GENRES
    items = "".join(
        f"<a href='{e(f'https://www.google.com/maps/search/{urllib.parse.quote(q)}/@{lat},{lon},13z')}' "
        f"class='mapbtn' target='_blank' rel='noopener'>{e(label)}</a>" for label, q in genres)
    return (f" <details class='menu'><summary class='mapbtn'>🍴 飲食店</summary>"
            f"<div class='menu-list'>{items}</div></details>")


def michilink(lat, lon):
    """その地点の周りの道の駅を Google マップで検索するボタン(道の駅は数が少ないので広めに探す)"""
    url = f"https://www.google.com/maps/search/{urllib.parse.quote('道の駅')}/@{lat},{lon},11z"
    return f" <a class='mapbtn' href='{e(url)}' target='_blank' rel='noopener'>🚗 道の駅</a>"


def conbilink(lat, lon):
    """その地点の周りのコンビニを Google マップで検索するボタン"""
    url = f"https://www.google.com/maps/search/{urllib.parse.quote('コンビニ')}/@{lat},{lon},13z"
    return f" <a class='mapbtn' href='{e(url)}' target='_blank' rel='noopener'>🏪 コンビニ</a>"


def onsenlink(lat, lon):
    """その地点の周りの日帰り温泉を Google マップで検索するボタン"""
    url = f"https://www.google.com/maps/search/{urllib.parse.quote('日帰り温泉')}/@{lat},{lon},12z"
    return f"<a class='mapbtn' href='{e(url)}' target='_blank' rel='noopener'>♨️ 温泉</a>"


def maplink(query, label):
    """Google マップの検索リンク(場所名で検索する。新しいタブで開く)"""
    url = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(query)
    return (f"<a class='mapbtn' href='{e(url)}' target='_blank' rel='noopener' "
            f"title='{e(query)}'>📍 {e(label)}</a>")


_holidays = None


def holidays():
    """祝日(振替休日を含む)の日付の集合。内閣府の祝日一覧を1回だけ読む。取れなければ空(祝日の色を付けない)"""
    global _holidays
    if _holidays is None:
        _holidays = set()
        try:
            with urllib.request.urlopen("https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv", timeout=30) as r:
                for line in r.read().decode("cp932").splitlines()[1:]:
                    y, m, d = line.split(",")[0].split("/")
                    _holidays.add(date(int(y), int(m), int(d)))
        except Exception as ex:
            print(f"祝日一覧の取得に失敗: {type(ex).__name__}")
    return _holidays


def day_label(d):
    dt = date.fromisoformat(d)
    cls = "sat" if dt.weekday() == 5 else ("sun" if dt.weekday() == 6 or dt in holidays() else "")
    return f"<span class='{cls}'>{dt.month}/{dt.day}<small>({WEEK[dt.weekday()]})</small></span>"


def day_label2(d, first=False):
    """一覧の表の見出し用の短い日付(日と曜日の2段)。月は最初の列と月が変わる日だけ書く"""
    dt = date.fromisoformat(d)
    cls = "sat" if dt.weekday() == 5 else ("sun" if dt.weekday() == 6 or dt in holidays() else "")
    day = f"{dt.month}/{dt.day}" if first or dt.day == 1 else f"{dt.day}"
    return f"<span class='{cls}'>{day}<br>{WEEK[dt.weekday()]}</span>"


def weekend(days):
    """次の土日(今日が土日なら今日から)の日付"""
    return [d for d in days if date.fromisoformat(d).weekday() >= 5][:2]


# ---------------------------------------------------------------- 登山
# 風を見る気圧面とそのおよその高度(m)。山頂に最も近い面を使う
LEVELS = [(925, 760), (900, 1000), (850, 1500), (800, 1950), (700, 3000)]


def grid(m):
    """アンサンブル予報(GFS 0.5°)の格子。同じ格子の山は1回の取得にまとめる"""
    return round(m["lat"] * 2) / 2, round(m["lon"] * 2) / 2


def thunder_prob(mts):
    """雷の確率(%) = GFSアンサンブル31通りのうち、6〜15時に「大気が不安定(CAPE 1,000J/kg以上)で
    1時間0.5mm以上の雨」の時間がある割合。アンサンブルの天気コードは雷雨(95)を出さないため、この組み合わせで見る。
    {格子: {日付: %}}。取得できなかった格子は含めない"""
    cells = sorted({grid(m) for m in mts})
    out = {}
    for i in range(0, len(cells), 50):
        chunk = cells[i:i + 50]
        try:
            res = fetch("https://ensemble-api.open-meteo.com/v1/ensemble", {
                "latitude": ",".join(str(c[0]) for c in chunk),
                "longitude": ",".join(str(c[1]) for c in chunk),
                "models": "gfs05", "hourly": "cape,precipitation",   # 0.25°は10日分しかないため16日分ある0.5°
                "timezone": "Asia/Tokyo", "forecast_days": DAYS})
        except Exception as ex:
            print(f"雷の確率の取得に失敗({len(chunk)}地点): {type(ex).__name__}")
            continue
        for c, r in zip(chunk, res):
            h = r["hourly"]
            members = [k[4:] for k in h if k.startswith("cape")]   # "", "_member01", ...
            out[c] = {}
            for d in sorted({t[:10] for t in h["time"]}):
                idx = [i for i, t in enumerate(h["time"]) if t[:10] == d and 6 <= int(t[11:13]) <= 15]
                hits = n = 0
                for s in members:
                    pairs = [(h["cape" + s][i], h["precipitation" + s][i]) for i in idx]
                    if len(pairs) < 10 or any(a is None or b is None for a, b in pairs):
                        continue            # 値の欠けたメンバーは数えない
                    n += 1
                    hits += any(a >= 1000 and b >= 0.5 for a, b in pairs)
                if n:
                    out[c][d] = round(100 * hits / n)
    return out


# 日ごとに使う予報モデル: (モデル, 取得日数, 使う日(今日=0))。上から順に、6〜15時の値がそろったものを使う。
# てんきとくらすとの比較(2026-10-29)で近かった組み合わせ。どれも使えない日は Open-Meteo のおまかせ(best_match)
MT_MODELS = [("jma_msm", 3, range(0, 3)),
             ("ukmo_global_deterministic_10km", 6, range(3, 6)),
             ("jma_gsm", 11, range(6, 11))]


def wind_chill(t, v):
    """体感温度(℃)。気温 t(℃)と風速 v(m/s)から、カナダ気象局などが使う式で求める。
    気温が10℃を超えるときや風が弱いときは、気温のまま"""
    kmh = v * 3.6
    if t > 10 or kmh < 4.8:
        return t
    return 13.12 + 0.6215 * t - 11.37 * kmh ** 0.16 + 0.3965 * t * kmh ** 0.16


def by_hour(h, key, d, hours):
    """hourly データから、指定日・指定時間帯の {時: 値}(値のない時刻は除く)"""
    return {int(t[11:13]): v for t, v in zip(h["time"], h[key])
            if t[:10] == d and int(t[11:13]) in hours and v is not None}


VIEW = {3: "◎", 2: "○", 1: "△", 0: "×"}


def view_rate(h, d):
    """展望(見晴らし)の目安。6〜12時の雲の量の平均で ◎20%未満・○50%未満・△80%未満・×それ以上。
    霧の時間があれば ×。雲の量がそろわなければ None(列を出さない)"""
    if "cloud_cover" not in h:
        return None
    clouds = hours_of(h, "cloud_cover", d, range(6, 13))
    if len(clouds) < 7:
        return None
    if any(c in (45, 48) for c in hours_of(h, "weather_code", d, range(6, 13))):
        return 0
    avg = sum(clouds) / len(clouds)
    return 3 if avg < 20 else 2 if avg < 50 else 1 if avg < 80 else 0


def mt_day(h, d, elev, strict):
    """1日分(6〜15時)の山頂の天気。値が足りなければ None。strict なら10時間すべてそろった日だけ"""
    hours = range(6, 16)
    need = 10 if strict else 1
    rains = hours_of(h, "precipitation", d, hours)
    temps = hours_of(h, "temperature_2m", d, hours)
    codes = hours_of(h, "weather_code", d, hours)
    # 風は山頂に近い気圧面から順に、値のある面を使う(モデルによっては一部の面がない)
    winds = next((w for lv, _ in sorted(LEVELS, key=lambda x: abs(x[1] - elev))
                  if len(w := hours_of(h, f"wind_speed_{lv}hPa", d, hours)) >= need), None)
    if not winds or min(len(rains), len(temps), len(codes)) < need:
        return None
    # 体感温度: 同じ時刻の気温と風速の組から求めた、6〜15時で最も低い値
    lv = next(lv for lv, _ in sorted(LEVELS, key=lambda x: abs(x[1] - elev))
              if len(hours_of(h, f"wind_speed_{lv}hPa", d, hours)) >= need)
    th, wh = by_hour(h, "temperature_2m", d, hours), by_hour(h, f"wind_speed_{lv}hPa", d, hours)
    feels = [wind_chill(th[k], wh[k]) for k in th if k in wh]
    return {"rain": sum(rains), "wind": max(winds), "wind_avg": sum(winds) / len(winds),
            "tmin": min(temps), "tmax": max(temps), "tavg": sum(temps) / len(temps), "code": max(codes), "view": view_rate(h, d),
            "feel": min(feels) if feels else min(temps)}


def mt_hours(h, d, elev, step):
    """その日の時間ごとの山頂の天気(0〜23時)。step 時間ごとにまとめ、雨はその間の合計、風は最大、天気は最も悪いもの。
    値の欠けた時間は出さない"""
    near = [lv for lv, _ in sorted(LEVELS, key=lambda x: abs(x[1] - elev))]
    rows = []
    for start in range(0, 24, step):
        hrs = range(start, start + step)
        rains = hours_of(h, "precipitation", d, hrs)
        temps = hours_of(h, "temperature_2m", d, [start])
        codes = hours_of(h, "weather_code", d, hrs)
        winds = next((w for lv in near if len(w := hours_of(h, f"wind_speed_{lv}hPa", d, hrs)) == step), None)
        if len(rains) == step and len(codes) == step and temps and winds:
            cloud = hours_of(h, "cloud_cover", d, [start]) if "cloud_cover" in h else []
            rows.append({"hour": start, "code": max(codes), "temp": temps[0], "wind": max(winds), "rain": sum(rains),
                         "cloud": cloud[0] if cloud else None,
                         "feel": wind_chill(temps[0], max(winds))})
    return rows


# 紅葉の予想: 7日平均の最低気温がこの値(℃)を下回った日から 色づき始め・見ごろ・見ごろ終わり。
# 2024・2025年の気温で、涸沢・大台ヶ原・金剛山・六甲山・高尾山など10か所の例年の見ごろと合うように決めた
KOYO_START, KOYO_PEAK, KOYO_END = 8, 6, 3
KOYO_DAYS = 18      # 見ごろは長くてもこの日数まで(高い山は3℃を下回るのが遅く、見ごろが長くなりすぎるため)
KOYO_ELEV = 2400    # これより高い山は、森林限界付近のこの標高で見る(山頂付近には木がないため)


def koyo(mts):
    """山ごとの紅葉の予想 {日付: 1(色づき始め) / 2(見ごろ)}。9〜12月だけ計算し、取得できなければ空"""
    if datetime.now(JST).month not in (9, 10, 11, 12):
        return [{} for _ in mts]
    res = forecast([(m["lat"], m["lon"], min(m["elev"], KOYO_ELEV)) for m in mts],
                   daily="temperature_2m_min", past_days=92)
    out = []
    for r in res:
        st = {}
        if r:
            t, v = r["daily"]["time"], r["daily"]["temperature_2m_min"]
            cross, first = {}, True
            for i in range(6, len(t)):
                w = v[i - 6:i + 1]
                if None in w or t[i][5:] < "09-01":
                    continue
                avg, d = sum(w) / 7, date.fromisoformat(t[i])
                for th in (KOYO_START, KOYO_PEAK, KOYO_END):
                    # 取得した期間の最初から下回っていれば、ずっと前に下回ったとみなす(9月初めは除く)
                    if avg <= th and th not in cross:
                        cross[th] = date.min if first and t[i][5:] > "09-07" else d
                first = False
            for x in t:
                d = date.fromisoformat(x)
                peak = cross.get(KOYO_PEAK)
                if peak and d >= peak and d < min(cross.get(KOYO_END, date.max), peak + timedelta(days=KOYO_DAYS)):
                    st[x] = 2
                elif KOYO_START in cross and d >= cross[KOYO_START] and not (peak and d >= peak):
                    st[x] = 1
        out.append(st)
    return out


# 桜の予想: 2月1日からの最高気温(0℃未満は0)の合計が600℃に達した日を開花とし(600℃の法則)、
# 開花から3日目までを咲き始め、4〜11日目を見ごろとする(満開は開花の5〜7日後が目安)。
# 吉野山・高尾山・六甲山・金剛山・弘前公園の2024・2025年の満開の時期と合うことを確かめた
SAKURA_SUM, SAKURA_ELEV = 600, 1500     # 1,500mより高い山は、この標高で見る(桜が咲くのはおおむねこの高さまで)


def sakura(mts):
    """山ごとの桜の予想 {日付: 1(咲き始め) / 2(見ごろ)}。3〜5月だけ計算し、取得できなければ空"""
    if datetime.now(JST).month not in (3, 4, 5):
        return [{} for _ in mts]
    res = forecast([(m["lat"], m["lon"], min(m["elev"], SAKURA_ELEV)) for m in mts],
                   daily="temperature_2m_max", past_days=92)
    out = []
    for r in res:
        st = {}
        if r:
            total, bloom = 0, None
            for x, v in zip(r["daily"]["time"], r["daily"]["temperature_2m_max"]):
                if x[5:] < "02-01" or v is None:
                    continue
                total += max(v, 0)
                if total >= SAKURA_SUM:
                    bloom = date.fromisoformat(x)
                    break
            if bloom:
                for x in r["daily"]["time"]:
                    k = (date.fromisoformat(x) - bloom).days
                    if 0 <= k <= 2:
                        st[x] = 1
                    elif 3 <= k <= 10:
                        st[x] = 2
        out.append(st)
    return out


# 季節の見ごろ: data の日ごとのキー → (印, 名前, 見る標高の上限, 見ごろ前の言い方)
SEASONS = {"sakura": ("🌸", "桜", SAKURA_ELEV, "咲き始め"), "koyo": ("🍁", "紅葉", KOYO_ELEV, "色づき始め")}


def season_mark(m, dates):
    """指定した日のどれかで桜・紅葉が見ごろ(予想)なら 🌸・🍁"""
    return "".join(f" <span class='fac' title='{name}の見ごろ(予想)' role='img' aria-label='{name}の見ごろ(予想)'>{icon}</span>"
                   for key, (icon, name, _, _) in SEASONS.items()
                   if any(d.get(key) == 2 for d in m["days"] if d["date"] in dates))


def season_line(m):
    """詳細に出す桜・紅葉の予想の一文(予報の期間に咲き始め・色づき始め・見ごろの日があるときだけ)"""
    md = lambda x: f"{date.fromisoformat(x).month}/{date.fromisoformat(x).day}"
    lines = ""
    for key, (icon, name, cap, early) in SEASONS.items():
        st = {d["date"]: d.get(key) for d in m["days"]}
        peak = [x for x, v in st.items() if v == 2]
        if peak:
            days = list(st)
            text = ("見ごろ" if peak[0] == days[0] else f"{md(peak[0])}頃から見ごろ") + \
                   ("" if peak[-1] == days[-1] else f"、{md(peak[-1])}頃まで")
        elif 1 in st.values():
            text = early
        else:
            continue
        where = "山頂付近" if m["elev"] <= cap else f"標高{cap:,}m付近"
        lines += f"<p class='muted'>{icon} {name}({where}・予想): {text}</p>"
    return lines


def build_mountains():
    mts = json.loads((ROOT / "data/mountains.json").read_text(encoding="utf-8"))
    points = [(m["lat"], m["lon"], m["elev"]) for m in mts]
    hourly = ",".join(["temperature_2m", "precipitation", "weather_code", "cloud_cover"]
                      + [f"wind_speed_{p}hPa" for p, _ in LEVELS])
    res = forecast(points, hourly=hourly + ",snow_depth", daily="sunrise,sunset")   # 積雪はおまかせのモデルだけで見る
    extra = [forecast(points, hourly=hourly, models=model, forecast_days=n) for model, n, _ in MT_MODELS]
    probs = thunder_prob(mts)
    leaves, blossoms = koyo(mts), sakura(mts)
    today = datetime.now(JST).date()
    for i, (m, r) in enumerate(zip(mts, res)):
        m["days"] = []
        if r is None:
            continue
        prob = probs.get(grid(m), {})
        dl = r.get("daily", {})
        if str(today) in dl.get("time", []):    # 本日の日の出・日の入り(山頂ではなく平地の地平線での時刻)
            k = dl["time"].index(str(today))
            if dl["sunrise"][k] and dl["sunset"][k]:
                m["sun"] = (dl["sunrise"][k][11:16], dl["sunset"][k][11:16])
        for d in sorted({t[:10] for t in r["hourly"]["time"]}):
            lead = (date.fromisoformat(d) - today).days
            day = None
            for (_, _, use), ex in zip(MT_MODELS, extra):
                if lead in use and ex[i] is not None and (day := mt_day(ex[i]["hourly"], d, m["elev"], True)):
                    src = ex[i]["hourly"]
                    break
            if not day:
                day = mt_day(r["hourly"], d, m["elev"], False)
                if not day:
                    continue            # 値のない日は表示しない
                src = r["hourly"]
            # 日付を押すと開く時間ごとの予報: 今日は1時間ごと、明日〜5日後は3時間ごと
            day["step"] = 1 if lead <= 0 else 3
            day["hours"] = mt_hours(src, d, m["elev"], day["step"]) if lead <= 5 else []
            rain, wind = day["rain"], day["wind"]
            thunder = prob.get(d)       # 雷の確率(%)。取得できなかった日は None
            if rain >= 10 or wind >= 17 or (thunder or 0) >= 50:
                score = 0
            elif rain >= 3 or wind >= 12:
                score = 1
            elif rain >= 0.5 or wind >= 10:
                score = 2
            else:
                score = 3
            if (thunder or 0) >= 20:
                score = min(score, 1)
            # 登山道の凍結注意: 最低気温0℃以下で、前日昼〜当日15時に雨・雪が0.5mm以上(濡れた道や雪が凍る)、
            # または最低気温-3℃以下(雨がなくても霜や前の雪が凍りやすい)
            prev = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
            wet = (sum(hours_of(r["hourly"], "precipitation", prev, range(12, 24)))
                   + sum(hours_of(r["hourly"], "precipitation", d, range(0, 16))))
            day["ice"] = day["tmin"] <= 0 and (wet >= 0.5 or day["tmin"] <= -3)
            # 山頂付近の積雪: 6〜15時の積雪深の予報が5cm以上なら ❄️(格子が粗く山では実際とずれやすいので目安)
            depth = hours_of(r["hourly"], "snow_depth", d, range(6, 16)) if "snow_depth" in r["hourly"] else []
            day["snowy"] = bool(depth) and max(depth) >= 0.05
            m["days"].append({"date": d, "score": score, "thunder": thunder, "koyo": leaves[i].get(d), "sakura": blossoms[i].get(d), **day})
    return align(mts, "登山")


def mt_detail(m, with_map=True, compact=False):
    """山の日ごとの詳細(登山口の地図ボタン付き)。判定は日付のすぐ右。
    compact=True はトップページ用で、最大風速と雷の確率を出さない"""
    has_t = not compact and all(d["thunder"] is not None for d in m["days"])   # 雷の確率がない日があれば列ごと出さない
    has_v = all(d.get("view") is not None for d in m["days"])                  # 展望も同じ
    rows = ""
    for d in m["days"]:
        hours = d.get("hours")
        label = (f"<button type='button' class='hbtn' aria-expanded='false'>{day_label(d['date'])}</button>"
                 if hours else day_label(d["date"]))
        label += season_mark(m, [d["date"]])
        rows += (f"<tr><td class='dt'>{label}</td>"
                 f"<td class='{RATE[d['score']][1]}'><b>{RATE[d['score']][0]}</b></td>"
                 f"<td>{wx(d['code'])[0]} {wx(d['code'])[1]}</td>"
                 + (f"<td class='{RATE[d['view']][1]}'>{VIEW[d['view']]}</td>" if has_v else "")
                 + f"<td>{round(d['tmin'])}〜{round(d['tmax'])}℃"
                 + (" <span class='fac' title='凍結注意' role='img' aria-label='凍結注意'>🧊</span>" if d.get("ice") and not compact else "")
                 + (" <span class='fac' title='積雪あり(山頂付近の予報で5cm以上)' role='img' aria-label='積雪あり'>❄️</span>" if d.get("snowy") and not compact else "") + "</td>"
                 + ("" if compact else f"<td>{round(d['feel'])}℃</td>")
                 + f"<td>{d['wind_avg']:.0f}m/s</td>"
                 + ("" if compact else f"<td>{d['wind']:.0f}m/s</td>")
                 + f"<td>{d['rain']:.1f}mm</td>" + (f"<td>{d['thunder']}%</td>" if has_t else "") + "</tr>")
        for x in hours or []:
            when = f"{x['hour']}時" if d["step"] == 1 else f"{x['hour']}〜{x['hour'] + 3}時"
            # 時間ごとの行: 判定・雷は空ける。風速は平均・最大の2列にまたがって出す
            rows += (f"<tr class='hr' hidden><td class='dt'>{when}</td><td></td>"
                     f"<td>{wx(x['code'])[0]} {wx(x['code'])[1]}</td>"
                     + (f"<td>雲{x['cloud']:.0f}%</td>" if has_v and x.get("cloud") is not None else "<td></td>" if has_v else "")
                     + f"<td>{round(x['temp'])}℃</td>"
                     + ("" if compact else f"<td>{round(x['feel'])}℃</td>")
                     + f"<td colspan='{1 if compact else 2}'>{x['wind']:.0f}m/s</td>"
                     f"<td>{x['rain']:.1f}mm</td>" + ("<td></td>" if has_t else "") + "</tr>")
    if not m.get("access"):
        place = maplink(trailhead(m), trailhead_label(m))
    else:                       # YAMAP の駐車場(なければ登山口・駅)の位置を示す。文字は登録した登山口の名前を優先
        a = m["access"]
        label = (trailhead_label(m) if m.get("trailhead") else
                 a["name"] if a["name"] not in ("駐車場", "有料駐車場", "登山者駐車場") else f"{m['name']} {a['name']}")
        place = maplink(f"{a['lat']},{a['lon']}", label)
    if m.get("accesses"):       # YAMAP・ヤマレコに登山口が複数あれば、飲食店と同じく押すと一覧が開く(各登山口の近くの駐車場の位置)
        items = "".join(maplink(x.get("q") or f"{x['lat']},{x['lon']}", x["name"]) for x in m["accesses"])
        place = (f"<details class='menu'><summary class='mapbtn'>📍 登山口</summary>"
                 f"<div class='menu-list'>{items}</div></details>")
    near = m.get("access") or m            # 温泉は駐車場・登山口の位置(分からなければ山頂)の周りで探す
    link = (routelinks(m) + place + " " + onsenlink(near["lat"], near["lon"])
            + foodlink(near["lat"], near["lon"], m["prefs"][0], "mountain") + michilink(near["lat"], near["lon"])
            + conbilink(near["lat"], near["lon"]) + " " + hutlink(m)) if with_map else ""    # 山小屋は右端
    sun = (f"<p class='muted'>本日の日の出 {m['sun'][0].lstrip('0')} ／ 日の入り {m['sun'][1]}</p>"
           if m.get("sun") else "")
    # スキー場と同じく、文字をまとめて上に、ボタン(お気に入り登録が先頭)をその下に
    buttons = f"<div class='btnrow'>{star('m', m)} {link}</div>" if with_map else ""
    return (f"{alert_box(m)}{intro(m)}{sun}{season_line(m)}{buttons}<div class='scroll'><table class='sub'>"
            "<tr><th class='dt'>日付</th><th>判定</th><th>天気(6〜15時)</th>"
            + ("<th>展望</th>" if has_v else "") + "<th>山頂付近の気温</th>"
            + ("" if compact else "<th>体感温度</th>") + "<th>平均風速</th>"
            + ("" if compact else "<th>最大風速</th>")
            + f"<th>降水量</th>{'<th>雷の確率</th>' if has_t else ''}</tr>{rows}</table></div>")


def mountains_page(mts):
    days = [d["date"] for d in mts[0]["days"][:TOP_DAYS]]   # 一覧は7日、詳細は全日数
    regions = ["北海道", "東北", "関東", "上信越", "北アルプス", "南アルプス", "中部"]
    kinki = ["滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県", "三重県", "福井県"]   # 近畿は県ごと
    after = ["中国・四国", "九州"]
    head = "".join(f"<th>{day_label2(d, i == 0)}</th>" for i, d in enumerate(days))
    rows = ""
    for m in mts:
        cells = ""
        for d in m["days"][:TOP_DAYS]:
            mark, cls = RATE[d["score"]]
            icon, _ = wx(d["code"])
            cells += (f"<td class='{cls}' title='{e(wx(d['code'])[1])} 風{d['wind']:.0f}m/s "
                      f"雨{d['rain']:.0f}mm" + (f" 雷{d['thunder']}%" if d["thunder"] is not None else "") + "'>"
                      f"<b>{mark}</b><small>{icon}</small></td>")
        rows += (
            f"<tbody class='mt' id='{m['key']}' data-fid='{e(fid('m', m))}' data-region='{e(m['region'])}' data-name='{e(m['name'] + m['yomi'])}'"
            + (f" data-prefs='{e(','.join(m['prefs']))}'" if m["region"] == "近畿" else "") + ">"
            f"<tr class='main'><th class='nm'><button type='button' aria-expanded='false'>"
            f"<span>{e(m['name'])}</span>"
            + tags(m) + season_mark(m, days) + alert_tags(m)
            + f"<small>{m['elev']:,}m · {e('・'.join(m['prefs']))}</small>"
            f"</button></th>{cells}</tr>"
            f"<tr class='detail' hidden><td colspan='{len(days) + 1}'><template>{mt_detail(m)}</template></td></tr></tbody>")
    chips = ("<button class='chip on' data-r=''>すべて</button><button class='chip' data-r='fav'>★お気に入り</button>"
             + "".join(f"<button class='chip' data-r='{r}'>{r}</button>" for r in regions)
             + "".join(f"<button class='chip' data-r='pref:{p}'>{p.rstrip('県府')}</button>" for p in kinki)
             + "".join(f"<button class='chip' data-r='{r}'>{r}</button>" for r in after))
    return f"""
<h1>登山天気</h1>
<div class='filters'><input id='q' type='search' placeholder='山名で絞り込み(例: 槍、ふじ)' aria-label='山名で絞り込み'>
<div class='chips'>{chips}</div></div>
<div class='scroll'><table class='grid' id='mt'><thead><tr><th class='nm'>山名</th>{head}</tr></thead>{rows}</table></div>
<div class='note'><h2>判定の基準</h2><ul>
<li><b class='r3'>◎</b> 降水0.5mm未満、風速10m/s未満</li>
<li><b class='r2'>○</b> 降水3mm未満、風速12m/s未満</li>
<li><b class='r1'>△</b> 降水10mm未満、風速17m/s未満</li>
<li><b class='r0'>×</b> それ以上、または雷の確率50%以上</li></ul>
<p>雷の確率: 米国の予報モデル(GFS)を条件を少しずつ変えて31通り計算したうち、6〜15時に「大気が不安定で雨が降る」時間がある割合です。20%以上で判定は最高でも△、50%以上で×。山では早めの下山を。</p>
<p>🏠 山小屋・⛺ テント場: 山頂から4km以内にあるもの(ヤマレコの登録。避難小屋は除く)。「山小屋」のボタンは、山頂か登山口の近くの小屋・宿の予約先です。予約サイト(Yamatan・モンベル・RESERVA・南アルプス市の予約)で予約できる小屋を先に、それ以外は公式ホームページ(長野県山小屋情報ポータル・OpenStreetMap などの登録)を並べています。その後ろの ⛺ は、山頂から4km以内のテント場のうち、予約先(Yamatan、またはテント場を管理する小屋の予約先・公式HP)が分かるものです。</p>
<p>展望: 6〜12時の雲の量の平均で、◎20%未満・○50%未満・△80%未満・×それ以上(霧の時間があれば×)。眺めの目安です。</p>
<p>🌸 桜(予想): 山頂付近(1,500mより高い山は標高1,500m付近)の気温から、2月1日からの最高気温の合計が600℃に達した日を開花とし、開花から4〜11日目を見ごろとしました(600℃の法則。吉野山・高尾山・六甲山・金剛山・弘前公園の2024・2025年の満開の時期と合わせた目安です)。麓は山頂付近より早く咲きます。</p>
<p>🍁 紅葉(予想): 山頂付近(2,400mより高い山は標高2,400m付近)の最低気温の7日平均から予想しています。8℃を下回ると色づき始め、6℃を下回ってから3℃を下回るまで(長くても18日間)を見ごろとしました(2024・2025年の気温で、涸沢・大台ヶ原・金剛山・六甲山・高尾山など10か所の例年の見ごろと合わせた目安です)。中腹や麓の見ごろは山頂付近より遅くなります。</p>
<p>❄️ 積雪あり: 山頂付近の積雪深の予報(6〜15時)が5cm以上の日。予報の格子が粗いため、稜線の吹き飛ばしや吹きだまりは表せません。雪がありそうかの目安です。</p>
<p>🧊 凍結注意: 山頂付近の最低気温(6〜15時)が0℃以下で前日昼〜当日に雨・雪が0.5mm以上の日、または最低気温が-3℃以下の日。登山道が凍りやすいので、チェーンスパイクやアイゼンの目安に。</p>
<p>体感温度: 山頂付近の気温と風速から求めた、6〜15時で最も低い値です(風が強いほど低くなります)。</p>
<p>⚠️ 警報・注意報は、駐車場・登山口(分からない山は山頂)のある市町村に気象庁がいま出しているもののうち、登山に関係するものです(波浪・高潮・氾濫・乾燥・霜は除く)。🌋 は、山頂から10km以内の火山に噴火警戒レベル2以上などの噴火警報・予報が出ているときに付けています。</p>
<p>風速は山頂の高さに最も近い上空(約760m・1,000m・1,500m・1,950m・3,000mのいずれか)の予報です。稜線の実際の風とは異なることがあり、突風はさらに強くなります。</p>
<p>予報モデル: 今日〜2日後は気象庁MSM、3〜5日後は英国気象局UKMO、6〜10日後は気象庁GSM、それ以降は Open-Meteo のおまかせ(各国のモデルの組み合わせ)を使っています。</p>
<h2>このページについて</h2>
<p>日本百名山(<span class='mk hyaku'></span>)と関西百名山(<span class='mk'></span>、山と溪谷社『関西百名山』)の予報です。両方に入る伊吹山・大台ヶ原山・大峰山は1つにまとめ、両方のバッジを付けています。
山頂の標高での予報(6〜15時)から、登山に向いた天気かを判定しています。表は7日間で、山名を押すと最大16日間の詳細が開きます。
詳細で今日から5日後までの日付を押すと、時間ごとの予報(今日は1時間ごと、明日〜5日後は3時間ごと。3時間ごとの行の風速・降水量はその3時間の最大・合計)が開きます。
日の出・日の入りは本日の時刻で、平地の地平線を基準にしています(山頂ではもう少し早く昇り、遅く沈みます)。</p></div>
"""


# ---------------------------------------------------------------- スノーボード
SITE_URL = "https://gelny7-cell.github.io/weather-site/"
REPORT_HOURS = (9, 12)      # SURF&SNOW の積雪情報を取り直す更新(日本時間の時)。スキー場の報告は1日1〜2回のため


def parse_report(h, today):
    """SURF&SNOW の積雪情報のページから、いちばん新しい「積雪深」の報告の日の値を取る。なければ None"""
    t = re.sub(r"\s+", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", " ", h, flags=re.S)).replace("&nbsp;", " ")
    m = re.search(r"積雪情報 日付 (.*?) 積雪深 平均\(cm\) (.*?) 積雪差\(cm\) (.*?) 雪質 (.*?) ゲレンデ状況 (.*?) 週間天気予報", t)
    if not m:
        return None
    dates = re.findall(r"(\d{1,2})/(\d{1,2})\(", m.group(1))
    cols = [x.split(" ") for x in m.group(2, 3, 4, 5)]
    if not dates or any(len(c) != len(dates) for c in cols):
        return None
    for (mo, dy), depth, diff, quality, state in zip(dates, *cols):     # 左が新しい日
        if re.fullmatch(r"\d+", depth) and int(depth) > 0:     # 0cm(オープン前など)は出さない
            year = today.year - (1 if int(mo) > today.month + 1 else 0)
            return {"date": f"{year}-{int(mo):02d}-{int(dy):02d}", "depth": int(depth),
                    "diff": int(diff) if re.fullmatch(r"-?\d+", diff) else None,
                    "quality": None if quality == "-" else quality, "state": None if state == "-" else state}
    return None


def attach_report(ski):
    """スキー場が報告したゲレンデの積雪深・雪質・ゲレンデ状況(SURF&SNOW の積雪情報)を s["report"] に付ける。
    取り直すのは朝・昼の更新のときだけで、ほかの回は公開中のサイトの snow_report.json(前回の値)を使う"""
    rids = {s["name"]: s.get("spec", {}).get("src", "")[9:] for s in ski}
    now = datetime.now(JST)
    data = None
    if now.hour not in REPORT_HOURS:
        try:
            data = fetch(SITE_URL + "snow_report.json", {})[0]
        except Exception as ex:
            print(f"前回の積雪情報の取得に失敗: {type(ex).__name__}")
    if data is None:
        data = {}
        for rid in sorted({r for r in rids.values() if r}):
            try:
                req = urllib.request.Request(f"https://surfsnow.jp/guide/htm/{rid}we.htm", headers={"User-Agent": "Mozilla/5.0"})
                data[rid] = parse_report(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore"), now.date())
            except Exception as ex:
                print(f"積雪情報の取得に失敗({rid}): {type(ex).__name__}")
            time.sleep(1)
    for s in ski:
        s["report"] = data.get(rids[s["name"]])
    OUT.mkdir(exist_ok=True)
    (OUT / "snow_report.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def overnight_snow(h, day):
    """前日17時〜当日8時(ゲレンデが閉まってから開くまで)の降雪量(cm)。
    1時間ごとの値はその前の1時間の量なので、前日18時〜当日8時の値を合計する。データが欠けていれば None"""
    prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    vals = hours_of(h, "snowfall", prev, range(18, 24)) + hours_of(h, "snowfall", day, range(0, 9))
    return sum(vals) if len(vals) == 15 else None


def ski_hours(th, bh, d, today):
    """日付を押すと開く時間ごとの予報。今日は8〜21時(ナイターまで)の1時間ごと、ほかの日は9・12・15時。
    気温・風・積雪・0℃の高さはその時刻の値、雪と雨はその前の1時間(3時間ごとの日は3時間)の合計
    (1時間ごとの値はその前の1時間の量)。値の欠けた時刻は出さない"""
    step = 1 if d == today else 3
    rows = []
    for hr in (range(8, 22) if step == 1 else (9, 12, 15)):
        win = range(hr - step + 1, hr + 1)
        at = {k: hours_of(th, k, d, [hr]) for k in
              ("weather_code", "temperature_2m", "wind_speed_10m", "wind_gusts_10m", "snow_depth", "freezing_level_height",
               "wind_direction_10m")}
        code_b = hours_of(bh, "weather_code", d, [hr])
        sums = {(src, k): hours_of(h, k, d, win) for src, h in (("t", th), ("b", bh)) for k in ("snowfall", "rain")}
        if not all(at.values()) or not code_b or any(len(v) != step for v in sums.values()):
            continue
        rows.append({"hour": hr, "code": at["weather_code"][0], "code_base": code_b[0],
                     "temp": at["temperature_2m"][0], "wind": at["wind_speed_10m"][0], "gust": at["wind_gusts_10m"][0],
                     "feel": wind_chill(at["temperature_2m"][0], at["wind_speed_10m"][0]), "wind_dir": at["wind_direction_10m"][0],
                     "depth": at["snow_depth"][0] * 100, "fl": at["freezing_level_height"][0],
                     "snow": sum(sums[("t", "snowfall")]), "snow_base": sum(sums[("b", "snowfall")]),
                     "rain": sum(sums[("t", "rain")]), "rain_base": sum(sums[("b", "rain")])})
    return rows


def build_ski():
    """ゲレンデ上部(top)と下部(base)の標高で、それぞれ予報を取得する"""
    ski = json.loads((ROOT / "data/ski.json").read_text(encoding="utf-8"))
    # 前日の夜の降雪も数えるため、前日分(past_days=1)も取得する。前日そのものは表示しない
    top = forecast([(s["lat"], s["lon"], s["top"]) for s in ski], past_days=1,
                   hourly="snow_depth,freezing_level_height,wind_speed_10m,snowfall,"
                          "weather_code,temperature_2m,wind_gusts_10m,rain,wind_direction_10m")
    base = forecast([(s["lat"], s["lon"], s["base"]) for s in ski], past_days=1,
                    hourly="snowfall,weather_code,rain,temperature_2m")
    today = datetime.now(JST).date().isoformat()
    open_hrs = range(8, 17)             # 営業時間(8〜16時)の値で1日分をまとめる
    rain_hrs = range(9, 17)             # 雨は1時間ごとの値がその前の1時間の量なので、9〜16時の値で8〜16時の合計
    for s, t, b in zip(ski, top, base):
        s["days"] = []
        if t is None or b is None:
            continue
        h, bh = t["hourly"], b["hourly"]
        for d in sorted({x[:10] for x in h["time"]}):
            if d < today:
                continue
            night = overnight_snow(h, d)                # 上部の新雪(前日17時〜当日8時)
            # アイスバーン注意: 前日に上部で雨が降ったか0℃を超え(雪が湿る・解ける)、夜〜朝の上部の最低気温が-2℃以下
            # (湿った雪が凍る)で、新雪が3cm未満(新しい雪で覆われない)の日
            prev = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
            thawed = (sum(hours_of(h, "rain", prev, range(0, 24))) > 0
                      or any(t > 0 for t in hours_of(h, "temperature_2m", prev, range(6, 19))))
            frozen = hours_of(h, "temperature_2m", prev, range(18, 24)) + hours_of(h, "temperature_2m", d, range(0, 9))
            night_base = overnight_snow(bh, d)
            depth = hours_of(h, "snow_depth", d, [9])
            v = {k: hours_of(h, k, d, open_hrs) for k in
                 ("weather_code", "temperature_2m", "wind_speed_10m", "wind_gusts_10m", "freezing_level_height")}
            code_b = hours_of(bh, "weather_code", d, open_hrs)
            morning = hours_of(bh, "temperature_2m", d, range(3, 9))    # 朝の路面: ふもと(下部)の3〜8時の最低気温
            rain, rain_b = hours_of(h, "rain", d, rain_hrs), hours_of(bh, "rain", d, rain_hrs)
            if (night is None or night_base is None or not depth or any(len(x) != 9 for x in v.values())
                    or len(code_b) != 9 or len(rain) != 8 or len(rain_b) != 8):
                continue                # 値のない日は表示しない
            ws = v["wind_speed_10m"]
            th, wh = by_hour(h, "temperature_2m", d, open_hrs), by_hour(h, "wind_speed_10m", d, open_hrs)
            wd = by_hour(h, "wind_direction_10m", d, open_hrs)
            s["days"].append({
                "date": d,
                "code": max(v["weather_code"]), "code_base": max(code_b),
                "snow": night, "snow_base": night_base,
                "depth": depth[0] * 100,
                "tmin": min(v["temperature_2m"]), "tmax": max(v["temperature_2m"]),
                "rain": sum(rain), "rain_base": sum(rain_b),
                "fl": min(v["freezing_level_height"]), "gust": max(v["wind_gusts_10m"]), "wind_avg": sum(ws) / len(ws),
                "feel": min(wind_chill(th[k], wh[k]) for k in th if k in wh),
                "wind_dir": mean_dir([wd[k] for k in wd if k in wh], [wh[k] for k in wd if k in wh]) if wd else None,
                "hours": ski_hours(h, bh, d, today),
                "road": min(morning) if len(morning) == 6 else None,
                "ice": bool(thawed and frozen and min(frozen) <= -2 and night < 3),
            })
    ski = align(ski, "スノーボード")
    attach_report(ski)
    return ski


SKI_REGIONS = {
    "北海道": ["北海道"],
    "東北": ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"],
    "関東": ["茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県", "山梨県"],
    "新潟": ["新潟県"],
    "長野": ["長野県"],
    "北陸": ["富山県", "石川県", "福井県"],
    "東海": ["岐阜県", "静岡県", "愛知県"],
    "近畿": ["三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"],
    "中国・四国": ["鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県"],
    "九州": ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県"],
}
PREF_SKI_REGION = {p: r for r, ps in SKI_REGIONS.items() for p in ps}
# パウダー期待の基準(上部の新雪cm, 上部の最高気温℃)。地域ごとに変え、雪の少ない地域でも「その地域にしては良い雪」の日が分かるようにする。
# 2023-24・2024-25年の冬の気象データで、どの地域でも1冬に数日ほどになる見当で決めた(過去データは山の雪を少なめに出すので目安)
POWDER = {"北海道": (15, -5), "東北": (15, -3), "関東": (15, -3), "新潟": (15, -3), "長野": (15, -3),
          "北陸": (10, -2), "東海": (10, -2), "近畿": (5, -1), "中国・四国": (5, -1), "九州": (5, -1)}


def is_powder(s, d):
    snow, tmax = POWDER[PREF_SKI_REGION[s["pref"]]]
    return d["snow"] >= snow and d["tmax"] <= tmax


def hour_marks(s):
    """一覧のスキー場名の横の印: 🌅 早朝営業(平日か休日の営業開始が7時台以前)、🌙 ナイター"""
    h = s.get("hours") or {}
    starts = [int(m.group(1)) for x in (h.get("day"), h.get("holiday")) if x for m in [re.search(r"(\d{1,2}):\d{2}", x)] if m]
    out = ""
    if starts and min(starts) <= 7:
        out += " <span class='fac' title='早朝営業あり' role='img' aria-label='早朝営業あり'>🌅</span>"
    if h.get("night"):
        out += " <span class='fac' title='ナイターあり' role='img' aria-label='ナイターあり'>🌙</span>"
    return out


def ski_hours_line(s):
    """営業時間の文字(ウェザーニュースの掲載情報。営業時間は年ごとにあまり変わらないので一度取って保存している)"""
    h = s.get("hours")
    if not h:
        return ""
    time_ = h["day"] if h["day"] == h["holiday"] else f"平日 {h['day']} ／ 休日 {h['holiday']}"
    night = f" · ナイター {h['night']}" if h.get("night") else ""
    return f"{time_}{night}"


def plan_text(s):
    """営業予定(SURF&SNOW のオープン予定日・クローズ予定日)。今季が未発表なら昨季の期間に注釈を付ける"""
    pl = s.get("plan")
    if not pl:
        return ""
    md = lambda x: f"{int(x[1])}/{int(x[2])}" if x[2].isdigit() else f"{int(x[1])}月{x[2]}"
    text = f"{md(pl['open'])}〜{md(pl['close'])}"
    if pl["season"] == "2026-2027":
        return f"{text} <small class='muted'>26-27シーズン・積雪による</small>"
    return f"{text} <small class='muted'>昨シーズンの期間・今季は未発表</small>"


def ski_info(s):
    """「スキー場詳細」ボタン。押すと営業時間・リフト券・コースのデータが開く(分かる項目だけ)。
    データは公式サイトに書いてあればその値、なければ SURF&SNOW の値(data の spec)"""
    sp, rows = s.get("spec", {}), []
    def row(label, value):
        rows.append(f"<div><span class='muted'>{label}</span> {value}</div>")
    if s.get("about"):         # 詳しい紹介(公式サイト・Wikipedia・SURF&SNOW の情報をもとに書いたもの)
        rows.append(f"<p class='about'>{e(s['about'])}</p>")
    if s.get("plan"):
        row("📅 営業期間", plan_text(s))
    if s.get("hours"):
        row("🕘 営業時間", ski_hours_line(s))
    t = s.get("ticket")
    if t:
        row("🎫 大人1日リフト券", f"{t['adult']:,}円 <small class='muted'>{t['season'][2:4]}-{t['season'][7:9]}シーズン"
                                     + ("・今季は未発表" if t["season"] != "2026-2027" else "") + "</small>")
    # キッカー・ハーフパイプ・ジブのパークがあれば 🏂(ウェザーニュースの「スノーパーク ○」。キッズパークは含まない)
    park = " <span class='fac' title='パーク(キッカー・ジブなど)あり' role='img' aria-label='パークあり'>🏂</span>" if s.get("park") else ""
    # ゲレンデで使うゴンドラ・ロープウェイがあれば 🚠(スキー場へ上がるためだけのものは除く)
    gondola = " <span class='fac' title='ゴンドラ・ロープウェイあり' role='img' aria-label='ゴンドラ・ロープウェイあり'>🚠</span>" if s.get("gondola") else ""
    if sp.get("courses") or sp.get("lifts") or gondola or park:
        row("🎿 コース・リフト", " ／ ".join(x for x in (f"{sp['courses']}コース{park}" if sp.get("courses") else park.strip(),
                                                        f"リフト{sp['lifts']}基" if sp.get("lifts") else "") if x) + gondola)
    if sp.get("longest") or sp.get("max_slope"):
        row("📏 最長滑走・最大斜度", " ／ ".join(x for x in (f"{sp['longest']:,}m" if sp.get("longest") else "",
                                                          f"{sp['max_slope']}度" if sp.get("max_slope") else "") if x))
    if sp.get("vertical"):
        row("⛰️ 標高差", f"{sp['vertical']:,}m" + (f" <small class='muted'>({sp['base']:,}〜{sp['peak']:,}m)</small>" if sp.get("peak") and sp.get("base") else ""))
    else:                       # SURF&SNOW にないときは一覧の上部・下部の標高で
        row("⛰️ 標高差", f"{s['top'] - s['base']:,}m <small class='muted'>({s['base']:,}〜{s['top']:,}m)</small>")
    if s.get("treerun") or s.get("sidecountry"):     # 公式サイト・SURF&SNOW・Wikipedia に記載があったものだけ
        row("🌲 非圧雪の滑走", " ／ ".join(x for x, k in (("ツリーランあり", "treerun"), ("サイドカントリーあり", "sidecountry")) if s.get(k)))
    if sp.get("level"):
        row("📊 難易度", " ".join(f"{k}{v}%" for k, v in sp["level"].items()))
    if sp.get("groom"):
        row("❄️ 圧雪", " ".join(f"{k}{v}%" for k, v in sp["groom"].items()))
    if not rows:
        return ""
    return (f"<details class='menu info'><summary class='mapbtn'>📋 スキー場詳細</summary>"
            f"<div class='info-body'>{''.join(rows)}</div></details> ")


def road(t):
    """朝の路面: ふもとの最低気温と凍結の印(1℃以下=凍結注意)"""
    t = round(t)                        # 表示する値で判定する(1.2℃が「1℃」なのに印がない、を防ぐ)
    tag = " <span class='frz' title='路面凍結注意' role='img' aria-label='路面凍結注意'>凍</span>" if t <= 1 else ""
    return f"{t}℃{tag}"


def ski_detail(s, with_map=True, simple=False):
    """スキー場の日ごとの詳細。上部と下部の天気・気温を並べる(駐車場の地図ボタン付き)。
    simple=True はトップページ用で、これまでどおり1列ずつ(値はゲレンデ上部)"""
    has_road = all(d.get("road") is not None for d in s["days"])     # 値のない日があれば列ごと出さない
    if simple:
        rows = ""
        for d in s["days"]:
            icon, label = wx(d["code"])
            powder = " <span class='tag'>パウダー期待</span>" if is_powder(s, d) else ""
            rain = f" <span class='tag warn'>雨{d['rain']:.0f}mm</span>" if d["rain"] >= 1 else ""
            rows += (f"<tr><td class='dt'>{day_label(d['date'])}</td><td>{icon} {label}{rain}</td>"
                     f"<td>{d['snow']:.0f}cm{powder}</td><td>{d['depth']:.0f}cm</td>"
                     f"<td>{round(d['tmin'])}〜{round(d['tmax'])}℃</td>"
                     f"<td>{d['gust']:.0f}m/s" + (" <span class='tag warn'>運休注意</span>" if d["gust"] >= 20 else "")
                     + "</td>" + (f"<td>{road(d['road'])}</td>" if has_road else "") + "</tr>")
        return (alert_box(s) + "<div class='scroll'><table class='sub'><tr><th class='dt'>日付</th><th>天気</th><th>新雪</th>"
                "<th>積雪</th><th>気温</th><th>最大瞬間風速</th>" + ("<th>朝の路面</th>" if has_road else "") + "</tr>"
                f"{rows}</table></div>")
    def gust(g):                # 突風が強い日はゴンドラ・リフトが止まりやすい
        return f"{g:.0f}m/s" + (" <span class='tag warn'>運休注意</span>" if g >= 20 else "")

    def wind(direction, speed):
        return (f"{dir_name(direction)} " if direction is not None else "") + f"{speed:.0f}m/s"

    def weather(code, rain):
        icon, label = wx(code)
        tag = f" <span class='tag warn'>雨{rain:.0f}mm</span>" if rain >= 1 else ""
        return f"{icon} {label}{tag}"
    rows = ""
    for d in s["days"]:
        powder = " <span class='tag'>パウダー期待</span>" if is_powder(s, d) else ""
        label = (f"<button type='button' class='hbtn' aria-expanded='false'>{day_label(d['date'])}</button>"
                 if d.get("hours") else day_label(d["date"]))
        rows += (f"<tr><td class='dt'>{label}</td>"
                 f"<td>{weather(d['code'], d['rain'])}</td><td>{weather(d['code_base'], d['rain_base'])}</td>"
                 f"<td>{d['snow']:.0f}cm{powder}" + (" <span class='fac' title='アイスバーン注意' role='img' aria-label='アイスバーン注意'>🧊</span>" if d.get("ice") else "")
                 + f"</td><td>{d['snow_base']:.0f}cm</td>"
                 f"<td>{d['depth']:.0f}cm</td>"
                 f"<td>{round(d['tmin'])}〜{round(d['tmax'])}℃</td><td>{round(d['feel'])}℃</td>"
                 f"<td>{d['fl']:,.0f}m</td><td>{wind(d.get('wind_dir'), d['wind_avg'])}</td><td>{gust(d['gust'])}</td>"
                 + (f"<td>{road(d['road'])}</td>" if has_road else "") + "</tr>")
        for x in d.get("hours", []):    # 時間ごと(雪・雨は前の3時間の合計、ほかはその時刻の値)
            rows += (f"<tr class='hr' hidden><td class='dt'>{x['hour']}時</td>"
                     f"<td>{weather(x['code'], x['rain'])}</td><td>{weather(x['code_base'], x['rain_base'])}</td>"
                     f"<td>{x['snow']:.0f}cm</td><td>{x['snow_base']:.0f}cm</td><td>{x['depth']:.0f}cm</td>"
                     f"<td>{round(x['temp'])}℃</td><td>{round(x['feel'])}℃</td><td>{x['fl']:,.0f}m</td>"
                     f"<td>{wind(x.get('wind_dir'), x['wind'])}</td><td>{gust(x['gust'])}</td>"
                     + ("<td></td>" if has_road else "") + "</tr>")
    link = ""
    if with_map:                # 公式ページ・ゲレンデマップ(分かるスキー場だけ)・駐車場の地図
        link = ((sitelink(s["site"], "🏠 公式ページ") + " " if s.get("site") else "")
                + (sitelink(s["map"], "🗺️ ゲレンデマップ") + " " if s.get("map") else "")
                + camlink(s) + maplink(s['name'] + ' 第1駐車場', '駐車場') + " " + onsenlink(s["lat"], s["lon"]) + foodlink(s["lat"], s["lon"], s["pref"], "ski") + michilink(s["lat"], s["lon"]) + conbilink(s["lat"], s["lon"]) + " ")
    rp = s.get("report")
    obs = ""
    if rp:                      # スキー場が報告したゲレンデの積雪(SURF&SNOW)。報告のない日は出さない
        when = date.fromisoformat(rp["date"])
        obs = (f"<p class='obs'>ゲレンデ積雪:<b>{rp['depth']}cm</b>"
               + (f"(前日比{rp['diff']:+d}cm)" if rp["diff"] is not None else "")
               + "".join(f"・{e(x)}" for x in (rp["quality"], rp["state"]) if x)
               + f" <small class='muted'>{when.month}/{when.day}時点</small></p>")
    # 文字(注意報・紹介・営業時間・標高・積雪の観測)をまとめて上に、ボタン(お気に入り登録が先頭)をその下の1か所に
    buttons = f"<div class='btnrow'>{star('s', s)} {ski_info(s)}{link}</div>" if with_map else ""
    return (f"{alert_box(s)}{obs}"
            f"{buttons}<div class='scroll'><table class='sub'>"
            "<tr><th rowspan='2' class='dt'>日付</th><th colspan='2'>天気</th><th colspan='2'>新雪</th><th rowspan='2'>積雪<br>(上部)</th>"
            "<th rowspan='2'>気温<br>(上部)</th><th rowspan='2'>体感温度<br>(上部)</th><th rowspan='2'>0℃の高さ<br>(8〜16時)</th><th rowspan='2'>平均風速<br>(上部)</th>"
            "<th rowspan='2'>最大瞬間風速<br>(上部)</th>"
            + ("<th rowspan='2'>朝の路面<br>(下部3〜8時)</th>" if has_road else "") + "</tr>"
            "<tr><th>上部</th><th>下部</th><th>上部</th><th>下部</th></tr>"
            f"{rows}</table></div>")


def ski_page(ski):
    days = [d["date"] for d in ski[0]["days"][:TOP_DAYS]]   # 一覧は7日、詳細は全日数
    head = "".join(f"<th>{day_label2(d, i == 0)}</th>" for i, d in enumerate(days))
    used = []
    rows = ""
    for r in SKI_REGIONS:                       # 地方の順に並べる
        for s in [x for x in ski if PREF_SKI_REGION[x["pref"]] == r]:
            if r not in used:
                used.append(r)
            cells = ""
            for d in s["days"][:TOP_DAYS]:
                icon, label = wx(d["code"])
                snow_cls = "big" if d["snow"] >= 20 else ("mid" if d["snow"] >= 5 else "muted")
                cells += (f"<td title='{e(label)} {round(d['tmin'])}〜{round(d['tmax'])}℃'>"
                          f"<b class='{snow_cls}'>{d['snow']:.0f}</b><small>{icon}</small></td>")
            total7 = sum(d["snow"] for d in s["days"][:TOP_DAYS])
            powder7 = " data-powder='1'" if any(is_powder(s, d) for d in s["days"][:TOP_DAYS]) else ""
            rows += (
                f"<tbody class='mt' id='{s['key']}' data-fid='{e(fid('s', s))}' data-region='{r}' data-name='{e(s['name'] + s.get('yomi', ''))}'{powder7}>"
                f"<tr class='main'><th class='nm'><button type='button' aria-expanded='false'>"
                f"<span>{e(s['name'])}</span>{hour_marks(s)}{alert_tags(s)}<small>{e(s['pref'])} · 7日間 {total7:.0f}cm</small>"
                f"</button></th>{cells}</tr>"
                f"<tr class='detail' hidden><td colspan='{len(days) + 1}'><template>{ski_detail(s)}</template></td></tr></tbody>")
    chips = ("<button class='chip on' data-r=''>すべて</button>"
             + "<button class='chip' data-r='fav'>★お気に入り</button>"
             + "<button class='chip' data-r='powder'>❄️パウダー期待</button>"
             + "".join(f"<button class='chip' data-r='{r}'>{r}</button>" for r in used))
    cards = f"""
<div class='filters'><input id='q' type='search' placeholder='スキー場名で絞り込み' aria-label='スキー場名で絞り込み'>
<div class='chips'>{chips}</div></div>
<div class='scroll'><table class='grid'><thead><tr><th class='nm'>スキー場</th>{head}</tr></thead>{rows}</table></div>"""
    return f"""
<h1>スノーボード 雪と天気</h1>
{cards}
<div class='note'><ul>
<li>新雪: 前日17時〜当日8時(ゲレンデが閉まってから開くまで)に降る雪の量の予報</li>
<li>積雪: 朝9時に積もっている雪の深さの予報(気象モデルの推定。実際の積雪と大きく違うことがあります)</li>
<li>🌅 早朝営業: 平日か休日の営業開始が7時台以前のスキー場。🌙 ナイター: ナイター営業のあるスキー場(どちらもウェザーニュースの営業時間から。特定日だけのものも含みます)</li>
<li>📷 ライブカメラ: スキー場の公式サイトが載せているカメラです(公式にないスキー場はウェザーニュースのカメラ)</li>
<li>ゲレンデ積雪: スキー場が報告した積雪深(平均)・前日比・雪質・ゲレンデ状況で、<a href='https://surfsnow.jp/'>SURF&amp;SNOW</a> の積雪情報から朝・昼に取得しています。報告がないスキー場・日は表示しません</li>
<li><span class='tag'>パウダー期待</span> 上部の新雪と最高気温が、地域ごとの基準を満たす日です(北海道 15cm以上・-5℃以下、東北・関東・新潟・長野 15cm以上・-3℃以下、北陸・東海 10cm以上・-2℃以下、近畿・中国・四国・九州 5cm以上・-1℃以下)。雪の少ない地域でも「その地域にしては良い雪」の日が分かるようにしています。「❄️パウダー期待」のボタンで、7日間のうちにパウダー期待の日があるスキー場だけに絞り込めます</li>
<li>🧊 アイスバーン注意: 前日に上部で雨が降ったか0℃を超え、夜〜朝の上部の最低気温が-2℃以下で、新雪が3cm未満の日(湿った雪が凍って硬くなりやすい)</li>
<li>体感温度: 上部の気温と風速から求めた、8〜16時で最も低い値。平均風速には風向(風が吹いてくる方角)も表示しています</li>
<li>朝の路面: ふもと(ゲレンデ下部)の3〜8時の最低気温。<span class='frz' title='路面凍結注意' role='img' aria-label='路面凍結注意'>凍</span> 路面凍結注意(1℃以下)。冬用タイヤ・チェーンの準備の目安です(実際の路面は除雪や日当たりで変わります)</li>
<li><span class='tag warn'>運休注意</span> 上部の最大瞬間風速が20m/s以上(ゴンドラ・リフトが止まりやすい強さ)</li>
<li>⚠️ 警報・注意報は、スキー場のある市町村に気象庁がいま出しているもの。🌋 は10km以内の火山に噴火警報・予報が出ているとき</li>
<li>天気・雨・気温・0℃の高さ・平均風速・最大瞬間風速は、営業時間(8〜16時)の予報からまとめています(天気は最も悪いもの、0℃の高さは最も低いもの)</li>
<li>日付を押すと時間ごとの予報が開きます(今日は8〜21時の1時間ごと、ほかの日は9時・12時・15時)。雪・雨はその時刻までの1時間(3時間ごとの日は3時間)の合計、気温・積雪・0℃の高さ・風はその時刻の値です(風速の列は平均ではなくその時刻の風速、最大瞬間風速の列はその時刻の突風)</li>
<li><span class='tag warn'>雨</span> 雨の予報(雪質が重くなりやすい)</li>
<li>上部・下部の標高: OpenStreetMap のリフトの位置と地形の標高から求めた値(一部は手作業で補正)。概算です</li>
<li>スキー場の一覧・標高差: Wikipedia、Wikidata。リフトの位置: © OpenStreetMap contributors</li>
<li>公式ページ・ゲレンデマップのボタンは、リンク先が確認できたスキー場だけに出しています</li>
<li>スキー場詳細: コース数・リフト数・最長滑走距離・最大斜度・標高差は、公式サイトに書いてあればその値、なければ <a href='https://surfsnow.jp/'>SURF&amp;SNOW</a>、それもなければウェザーニュースの掲載値です。難易度・圧雪の割合は SURF&amp;SNOW、大人1日リフト券は公式サイトで確かめられたものはその料金(休日・通常料金)、ほかは SURF&amp;SNOW、ウェザーニュースの掲載料金です(2026-27シーズン。未発表のスキー場は2025-26シーズン)。営業期間は SURF&amp;SNOW のオープン予定日・クローズ予定日です(26-27シーズンが未発表のスキー場は昨シーズンの期間)。🏂 は、キッカー・ハーフパイプ・ジブなどのパークがあるスキー場です(ウェザーニュースの掲載情報。キッズパークは含みません)。🚠 は、ゲレンデで使うゴンドラ・ロープウェイがあるスキー場です(びわ湖バレイのように、スキー場へ上がるためだけのものは除く)。ツリーラン・サイドカントリー(ゲートなどから管理区域外へ出られる仕組み)は、公式サイト・SURF&amp;SNOW・Wikipedia・<a href='https://steep.jp/'>STEEP</a> に記載があったスキー場だけ表示しています。サイドカントリーは自己責任の山岳エリアなので、装備と各スキー場のルールを必ず確認してください。料金や施設は変わることがあるので、公式ページで確認してください</li>
<li>営業時間: ウェザーニュースの掲載情報(2025-26シーズン)。今シーズンの営業日・時間は公式ページで確認してください</li></ul>
<h2>このページについて</h2>
<p>標高差の大きいスキー場100か所(営業を終えた芸北国際スキー場を除く)に、志賀高原 焼額山・キューピットバレイ・だいせんホワイトリゾート・スノーウェーブパーク白鳥高原と、SURF&amp;SNOW の中京・北陸・関西のスキー場を加えた一覧です。表の数字はゲレンデ上部の新雪(前日17時〜当日8時、閉場から開場までに積もる雪)の予報(cm)です。表は7日間で、スキー場名を押すと最大16日間の積雪・気温・0℃の高さ・風の詳細が開きます。</p></div>
"""


# ---------------------------------------------------------------- サーフィン
def wind_kind(wdeg, facing):
    diff = ang_diff(wdeg, facing)     # 0に近いほど海から吹く
    return "オンショア" if diff <= 45 else ("オフショア" if diff >= 135 else "クロス")


def surf_hours(s, wh, fh, d, today):
    """日付を押すと開く時間ごとの予報(6〜16時): 今日・明日は1時間ごと、明後日は3時間ごと。値の欠けた時刻は出さない"""
    lead = (date.fromisoformat(d) - today).days
    if not 0 <= lead <= 2:
        return []
    rows = []
    for hr in range(6, 17, 1 if lead <= 1 else 3):
        v = {k: hours_of(src, k, d, [hr]) for src, keys in (
            (wh, ("wave_height", "swell_wave_height", "swell_wave_period", "wave_direction")),
            (fh, ("wind_speed_10m", "wind_direction_10m", "weather_code"))) for k in keys}
        if all(v.values()):
            v = {k: x[0] for k, x in v.items()}
            air, sea = hours_of(fh, "temperature_2m", d, [hr]), hours_of(wh, "sea_surface_temperature", d, [hr])
            rows.append({"hour": hr, "height": v["wave_height"], "swell": v["swell_wave_height"],
                         "air": air[0] if air else None, "sea": sea[0] if sea else None,
                         "period": v["swell_wave_period"], "wave_dir": v["wave_direction"],
                         "wind": v["wind_speed_10m"], "wind_dir": v["wind_direction_10m"],
                         "kind": wind_kind(v["wind_direction_10m"], s["facing"]), "code": v["weather_code"]})
    return rows


def tide_events(m15):
    """15分ごとの潮位から満潮・干潮を探す。[(時刻 "YYYY-MM-DDTHH:MM", "満潮" or "干潮")]。
    潮位は1cm単位で同じ値が続くので、同じ値の区間を1つ(真ん中の時刻)にまとめてから山と谷を探す。
    潮の差が小さい日本海側で細かい揺れを拾わないよう、隣の山・谷との差が5cm未満のものは除く"""
    runs = []                           # [(時刻, 潮位)]
    seg = []
    for t, v in zip(m15["time"], m15["sea_level_height_msl"]):
        if v is None:
            continue
        if seg and v != seg[0][1]:
            runs.append(seg[len(seg) // 2])
            seg = []
        seg.append((t, v))
    if seg:
        runs.append(seg[len(seg) // 2])
    ev = []
    for i in range(1, len(runs) - 1):
        a, b, c = runs[i - 1][1], runs[i][1], runs[i + 1][1]
        if b > a and b > c:
            ev.append([runs[i][0], "満潮", b])
        elif b < a and b < c:
            ev.append([runs[i][0], "干潮", b])
    changed = True
    while changed:                      # 差の小さい山と谷の組を取り除く
        changed = False
        for i in range(len(ev) - 1):
            if abs(ev[i][2] - ev[i + 1][2]) < 0.05:
                del ev[i:i + 2]
                changed = True
                break
    return [(x[0], x[1]) for x in ev]


def tide_text(m15, events, d, hrs):
    """時間帯の潮: その間の満潮・干潮の時刻。なければ上げ潮・下げ潮。値がなければ None"""
    inside = [f"{k} {int(x[11:13])}:{x[14:16]}" for x, k in events if x[:10] == d and int(x[11:13]) in hrs]
    if inside:
        return "・".join(inside)
    vals = [v for x, v in zip(m15["time"], m15["sea_level_height_msl"])
            if x[:10] == d and int(x[11:13]) in hrs and v is not None]
    if len(vals) < 2:
        return None
    return "上げ潮" if vals[-1] > vals[0] else "下げ潮"


def build_surf():
    spots = json.loads((ROOT / "data/surf.json").read_text(encoding="utf-8"))
    today = datetime.now(JST).date()
    waves = marine([(s["sea_lat"], s["sea_lon"]) for s in spots], hourly=",".join([
        "wave_height", "wave_period", "wave_direction", "swell_wave_height", "swell_wave_period",
        "sea_surface_temperature"]))
    tides = marine([(s["sea_lat"], s["sea_lon"]) for s in spots], minutely_15="sea_level_height_msl")
    winds = forecast([(s["lat"], s["lon"], 0) for s in spots],
                     hourly="wind_speed_10m,wind_direction_10m,weather_code,temperature_2m", daily="sunrise,sunset")
    for s, w, f, tide in zip(spots, waves, winds, tides):
        s["days"] = []
        if w is None or f is None:
            continue
        dl = f.get("daily", {})               # 本日の日の出・日の入り(登山と同じく平地の地平線での時刻)
        if str(today) in dl.get("time", []):
            k = dl["time"].index(str(today))
            if dl["sunrise"][k] and dl["sunset"][k]:
                s["sun"] = (dl["sunrise"][k][11:16], dl["sunset"][k][11:16])
        m15 = tide["minutely_15"] if tide else None
        events = tide_events(m15) if m15 else []
        s["grid"] = (w["latitude"], w["longitude"])
        for d in sorted({t[:10] for t in w["hourly"]["time"]}):
            slots = []
            for name, hrs in (("朝", range(6, 12)), ("昼", range(12, 18))):
                hgt = hours_of(w["hourly"], "wave_height", d, hrs)
                per = hours_of(w["hourly"], "swell_wave_period", d, hrs)     # 周期はうねりの周期(風波を含めない)
                swh = hours_of(w["hourly"], "swell_wave_height", d, hrs)
                wdir = hours_of(w["hourly"], "wave_direction", d, hrs)
                ws = hours_of(f["hourly"], "wind_speed_10m", d, hrs)
                wd = hours_of(f["hourly"], "wind_direction_10m", d, hrs)
                codes = hours_of(f["hourly"], "weather_code", d, hrs)
                # 時間帯の半分以上の時刻に値がなければ、その時間帯は表示しない
                if min(len(hgt), len(per), len(swh), len(wdir), len(ws), len(wd), len(codes)) < len(hrs) / 2:
                    continue
                h_, p_, w_ = sum(hgt) / len(hgt), sum(per) / len(per), sum(ws) / len(ws)
                wdeg = mean_dir(wd, ws)
                kind = wind_kind(wdeg, s["facing"])
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
                # 気温・水温は判定に使わないので、値がなくても時間帯は残す(表で列ごと出さない)
                air = hours_of(f["hourly"], "temperature_2m", d, hrs)
                sea = hours_of(w["hourly"], "sea_surface_temperature", d, hrs)
                slots.append({"slot": name, "height": h_, "period": p_, "swell": sum(swh) / len(swh),
                              "air": sum(air) / len(air) if air else None, "sea": sum(sea) / len(sea) if sea else None,
                              "tide": tide_text(m15, events, d, hrs) if m15 else None,
                              "wave_dir": mean_dir(wdir), "wind": w_, "wind_dir": wdeg,
                              "kind": kind, "score": score,
                              "code": max(codes)})
            if len(slots) == 2:         # 朝・昼の両方がそろった日だけ表示する
                hours = surf_hours(s, w["hourly"], f["hourly"], d, today)
                for x in hours:             # 時間ごとの潮: その時刻の前後1時間に満潮・干潮があればその時刻、なければ上げ・下げ
                    x["tide"] = tide_text(m15, events, d, range(x["hour"], x["hour"] + 1)) if m15 else None
                s["days"].append({"date": d, "slots": slots, "hours": hours})
    return align(spots, "サーフィン")


def surf_table(s):
    """サーフポイントの朝・昼の表"""
    rows = ""
    every = [x for d in s["days"] for x in d["slots"] + d.get("hours", [])]
    has_air = all(x["air"] is not None for x in every)    # 値の欠けたスポットは列ごと出さない
    has_sea = all(x["sea"] is not None for x in every)
    has_tide = all(x.get("tide") for x in every)

    def front(deg):             # うねりが浜の正面(向きの差30°以内)から入るなら 🎯
        return (" <span class='fac' title='うねりが浜の正面から入る' role='img' aria-label='うねりが浜の正面から'>🎯</span>"
                if deg is not None and ang_diff(deg, s["facing"]) <= 30 else "")

    def temps(x):
        return ((f"<td>{x['air']:.0f}℃</td>" if has_air else "")
                + (f"<td>{x['sea']:.0f}℃</td>" if has_sea else ""))
    for d in s["days"]:
        for i, sl in enumerate(d["slots"]):
            mark, cls = RATE[sl["score"]]
            label = (f"<button type='button' class='hbtn' aria-expanded='false'>{day_label(d['date'])}</button>"
                     if d.get("hours") else day_label(d["date"]))
            date_cell = (f"<td rowspan='{len(d['slots'])}' class='dt'>{label}</td>" if i == 0 else "")
            rows += (f"<tr>{date_cell}<td>{sl['slot']}</td><td class='{cls}'><b>{mark}</b></td>"
                     f"<td>{sl['height']:.1f}m</td>"
                     f"<td>{sl['swell']:.1f}m</td><td>{sl['period']:.1f}秒</td>"
                     f"<td>{dir_name(sl['wave_dir'])}{front(sl['wave_dir'])}</td>" + (f"<td>{sl['tide']}</td>" if has_tide else "")
                     + f"<td>{dir_name(sl['wind_dir'])} {sl['wind']:.1f}m/s <small>{sl['kind']}</small></td>"
                     f"<td>{wx(sl['code'])[0]}</td>{temps(sl)}</tr>")
        for x in d.get("hours", []):
            rows += (f"<tr class='hr' hidden><td class='dt'>{x['hour']}時</td><td colspan='2'></td>"
                     f"<td>{x['height']:.1f}m</td><td>{x['swell']:.1f}m</td><td>{x['period']:.1f}秒</td>"
                     f"<td>{dir_name(x['wave_dir'])}{front(x['wave_dir'])}</td>" + (f"<td>{x['tide']}</td>" if has_tide else "")
                     + f"<td>{dir_name(x['wind_dir'])} {x['wind']:.1f}m/s <small>{x['kind']}</small></td>"
                     f"<td>{wx(x['code'])[0]}</td>{temps(x)}</tr>")
    return ("<div class='scroll'><table class='sub'>"
            "<tr><th class='dt'>日付</th><th></th><th>判定</th><th>波の高さ</th><th>うねり</th><th>周期</th><th>波の向き</th>" + ("<th>潮</th>" if has_tide else "")
            + f"<th>風</th><th>天気</th>{'<th>気温</th>' if has_air else ''}{'<th>水温</th>' if has_sea else ''}</tr>{rows}</table></div>")


def facmarks(s):
    """トイレ・シャワーがあれば印だけ(surfride.net の設備欄)。一覧のポイント名の横に出す"""
    return "".join(f" <span class='fac' title='{label}' role='img' aria-label='{label}'>{icon}</span>"
                   for k, icon, label in (("toilet", "🚻", "トイレあり"), ("shower", "🚿", "シャワーあり"),
                                            ("shop", "🏪", "近くにサーフショップあり")) if s.get(k) is True)


def camlink(s):
    """ライブカメラのボタン(data の cams。サーフィンはサーファーズオーシャン・surfride.net、スキー場は公式サイト
    (なければウェザーニュース)に載っているカメラ)。
    1つならそのまま開き、複数あれば押すと一覧が開く"""
    cams = s.get("cams") or []
    if len(cams) == 1:
        return sitelink(cams[0]["url"], "📷 ライブカメラ") + " "
    if not cams:
        return ""
    items = "".join(sitelink(c["url"], c["name"]) for c in cams)
    return (f"<details class='menu'><summary class='mapbtn'>📷 ライブカメラ</summary>"
            f"<div class='menu-list'>{items}</div></details> ")


def wetsuit(t):
    """水温(℃)からウェットスーツの目安(一般的な目安。風が強い日・朝夕は一段厚めに)"""
    for lo, text in ((25, "ボードショーツ・ラッシュガード"), (23, "スプリング(半袖・半ズボン)"), (20, "シーガル(半袖・長ズボン)"),
                     (17, "3/2mm フルスーツ"), (14, "5/3mm フルスーツ"), (11, "5/3mm セミドライ+ブーツ")):
        if t >= lo:
            return text
    return "5mm セミドライ+ブーツ・グローブ・キャップ"


def surf_detail(s):
    """サーフポイントの詳細(名前を押すと開く): 注意報・紹介・ボタン(お気に入り登録が先頭)・朝昼の表"""
    pref = s["pref"][:s["pref"].index("県") + 1] if "県" in s["pref"] else s["pref"][:3]
    buttons = (f"<div class='btnrow'>{star('w', s)} {camlink(s)}{maplink(s['parking'], '駐車場')} {onsenlink(s['lat'], s['lon'])}"
               f"{foodlink(s['lat'], s['lon'], pref, 'surf')}{michilink(s['lat'], s['lon'])}{conbilink(s['lat'], s['lon'])}</div>")
    seas = [sl["sea"] for d in s["days"][:1] for sl in d["slots"] if sl.get("sea") is not None]
    suit = (f"<p class='muted'>🩱 ウェットスーツの目安: <b>{wetsuit(sum(seas) / len(seas))}</b>"
            f"(今日の水温 {sum(seas) / len(seas):.0f}℃。風が強い日や朝夕は一段厚めに)</p>") if seas else ""
    sun = (f"<p class='muted'>本日の日の出 {s['sun'][0].lstrip('0')} ／ 日の入り {s['sun'][1]}</p>" if s.get("sun") else "")
    return (f"{alert_box(s)}{intro(s)}{sun}{suit}{buttons}{surf_table(s)}"
            + (f"<p class='muted'>波は沖合({s['grid'][0]:.2f}, {s['grid'][1]:.2f})の予報</p>" if s.get("grid") else ""))


def surf_page(spots):
    days = [d["date"] for d in spots[0]["days"][:TOP_DAYS]]   # 一覧は7日、詳細は全日数
    head = "".join(f"<th>{day_label2(d, i == 0)}</th>" for i, d in enumerate(days))
    coasts = ["太平洋側", "日本海側"]
    rows = ""
    for c in coasts:                            # 海側ごとに並べる
        for s in sorted([x for x in spots if x.get("coast") == c], key=lambda x: -x["lon"]):   # 東から西へ
            cells = ""
            for d in s["days"][:TOP_DAYS]:      # 一覧はその日の朝・昼のよいほうの判定とうねりの高さだけ
                sl = max(d["slots"], key=lambda x: x["score"])
                mark, cls = RATE[sl["score"]]
                cells += (f"<td class='{cls}' title='{sl['slot']} 波{sl['height']:.1f}m うねり{sl['swell']:.1f}m "
                          f"周期{sl['period']:.1f}秒 {sl['kind']}'><b>{mark}</b><small>{sl['swell']:.1f}m</small></td>")
            rows += (
                f"<tbody class='mt' id='{s['key']}' data-fid='{e(fid('w', s))}' data-region='{c}' data-name='{e(s['name'] + s['pref'])}'>"
                f"<tr class='main'><th class='nm'><button type='button' aria-expanded='false'>"
                f"<span>{e(s['name'])}</span>{facmarks(s)}<small>{e(s['pref'])} · 浜の向き {dir_name(s['facing'])}</small>"
                f"</button></th>{cells}</tr>"
                f"<tr class='detail' hidden><td colspan='{len(days) + 1}'><template>{surf_detail(s)}</template></td></tr></tbody>")
    chips = ("<button class='chip on' data-r=''>すべて</button><button class='chip' data-r='fav'>★お気に入り</button>"
             + "".join(f"<button class='chip' data-r='{c}'>{c}</button>" for c in coasts if any(x.get("coast") == c for x in spots)))
    cards = f"""
<div class='filters'><input id='q' type='search' placeholder='ポイント名・地名で絞り込み' aria-label='ポイント名・地名で絞り込み'>
<div class='chips'>{chips}</div></div>
<div class='scroll'><table class='grid'><thead><tr><th class='nm'>ポイント</th>{head}</tr></thead>{rows}</table></div>"""
    return f"""
<h1>サーフィン 波と風</h1>
{cards}
<div class='note'><h2>判定の基準</h2><ul>
<li><b class='r3'>◎</b> 波0.6〜2.0mでうねりの周期7秒以上(オフショアの弱い風なら一段上げる)</li>
<li><b class='r1'>△</b> うねりの周期5秒未満、5m/s以上のオンショア、2mを超える大きな波(上級者向け)で一段下げる</li>
<li><b class='r0'>×</b> 0.3m未満(フラット)、または3mを超える荒れた海</li>
<li>オフショア=陸から海へ吹く風(波の面が整いやすい)、オンショア=海から陸へ吹く風</li>
<li>波の高さ=風波とうねりを合わせた高さ。うねり・周期=遠くから届く波(うねり)だけの高さと周期(周期が長いほど力のある波)</li>
<li>気温は浜付近、水温は沖合の海面水温の予報</li>
<li>潮: その時間帯にある満潮・干潮の時刻。なければ上げ潮・下げ潮(沖合の潮位の予報から求めた目安で、港の潮汐表とは数十分ずれることがあります)</li>
<li>⚠️ 警報・注意報は、浜のある市町村に気象庁がいま出しているもの</li>
<li>🚻 トイレ・🚿 シャワー: <a href='https://japan.surfride.net/'>surfride.net</a> の設備の情報で、あるポイントの名前の横に付けています(分からないポイントは付けていません)</li>
<li>🏪 サーフショップ: surfride.net・サーファーズオーシャンに、近くにサーフショップ(スクール・レンタル)があると書かれているか、確認できたポイントに付けています</li>
<li>🎯 波の向きが浜の向き(正面)から30°以内の時間帯。うねりがまっすぐ入り、波が立ちやすい目安です</li>
<li>🩱 ウェットスーツの目安: 今日の水温の予報から、25℃以上 ボードショーツ・ラッシュガード、23℃以上 スプリング、20℃以上 シーガル、17℃以上 3/2mm フル、14℃以上 5/3mm フル、11℃以上 5/3mm セミドライ+ブーツ、それ未満 5mm セミドライ+ブーツ・グローブ・キャップ(一般的な目安です)</li>
<li>📷 ライブカメラ: <a href='https://www.surfers-ocean.com/'>サーファーズオーシャン</a>・<a href='https://japan.surfride.net/'>surfride.net</a> に載っているカメラのうち、ポイントかそのすぐ近くを映すものです(停止中のものは除く)。提供元のサイトが開きます</li>
<li>一覧は7日間で、その日の朝・昼のよいほうの判定とうねりの高さです。ポイント名を押すと、朝・昼の詳しい予報(最大16日間)が開きます</li>
<li>今日〜明後日の日付を押すと、6〜16時の時間ごとの予報が開きます(今日・明日は1時間ごと、明後日は3時間ごと。その時刻の値)</li></ul>
<h2>このページについて</h2>
<p>朝(6〜11時)と昼(12〜17時)の平均です。波の高さは沖合の予報で、浜では地形や向きにより小さくなることが多いです。</p></div>
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
                    f"{dir_name(sl['wave_dir'])}からの{_swell(sl['period'])}(うねり{sl['swell']:.1f}m・周期{sl['period']:.1f}秒)に、"
                    f"{dir_name(sl['wind_dir'])}の風{sl['wind']:.1f}m/s({sl['kind']})の予報です。")
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


def link_row(name_html, cells_html, page, attrs=""):
    """トップページの1行(週末だけ)。名前を押すと、各ページでその予報を開いた状態で表示する"""
    return (f"<tbody{attrs}><tr><td class='l'><a class='xlink' href='{page}'>{name_html}</a></td>"
            f"{cells_html}</tr></tbody>")


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
    def mt_row(m, attrs=""):
        cells = ""
        for d in m["days"]:
            if d["date"] in we:
                icon, _ = wx(d["code"])
                cells += (f"<td class='{RATE[d['score']][1]}'><b>{RATE[d['score']][0]}</b> {icon}"
                          f"<br><small class='muted'>{round(d['tavg'])}℃ · "
                          f"{d['wind']:.0f}m/s</small></td>")
        return link_row(f"<b>{e(m['name'])}</b>{tags(m)}{season_mark(m, we)}<br><small class='muted'>"
                        f"{m['elev']:,}m · {e(m['prefs'][0])}</small>",
                        cells, f"mountains.html#{m['key']}", attrs)
    mt_rows = "".join(mt_row(m) for m in good[:10]) or (
        f"<tbody><tr><td colspan='3'>週末に条件の良い山は見当たりません</td></tr></tbody>")
    we_head = "".join(f"<th>{day_label(d)}</th>" for d in we)
    # お気に入り(☆で登録)の山: 全部の山を隠して入れておき、登録されたものだけ表示する
    fav_mt = "".join(mt_row(m, f" class='favonly' data-fid='{e(fid('m', m))}' hidden") for m in mts)
    # お気に入りと天気が良さそうな山は、列の幅がそろうよう1つの表にして、見出しの行を間に挟む
    # 登山とスノーの表は列の幅をそろえる(広い画面で予報の列がずれないように)
    cols = "<colgroup><col class='nmcol'>" + "<col>" * len(we) + "</colgroup>"
    mt_list = (f"<div class='scroll'><table class='sub top'>{cols}"
               f"<tbody class='favhead' hidden><tr><th><a href='mountains.html#fav'><span class='favmark'>★</span>お気に入りの山</a></th>"
               f"{we_head}</tr></tbody>{fav_mt}"
               f"<tbody><tr><th>週末おすすめの山</th>{we_head}</tr></tbody>{mt_rows}</table></div>")
    # スノー: よく行くスキー場(data/favorites.json)を常に表示する
    upto = we[-1]
    fav_rows = ""
    for s in ski:
        n = s["name"]
        cells = "".join(f"<td>{wx(d['code'])[0]}<br><small class='muted'>新雪</small>{d['snow']:.0f}cm"
                        f"<br><small class='muted'>積雪</small>{d['depth']:.0f}cm</td>"
                        for d in s["days"] if d["date"] in we)
        until = sum(d["snow"] for d in s["days"] if d["date"] <= upto)
        more = f"<br><small class='muted'>週末まで {until:.0f}cm</small>" if round(until) else ""
        fav_rows += link_row(f"<b>{e(n)}</b>{more}",
                             cells, f"snow.html#{s['key']}", f" class='favonly' data-fid='{e(fid('s', s))}' hidden")
    # 週末おすすめのスキー場: 週末の新雪(パウダー期待の日は加点)が多い順に、地方が重ならないよう5か所
    def ski_score(s):
        days = [d for d in s["days"] if d["date"] in we]
        return sum(d["snow"] for d in days) + sum(10 for d in days if is_powder(s, d))
    picks, used = [], set()
    for s in sorted(ski, key=ski_score, reverse=True):
        r = PREF_SKI_REGION[s["pref"]]
        if ski_score(s) >= 1 and r not in used:
            picks.append(s); used.add(r)
        if len(picks) == 5:
            break
    rec_rows = ""
    for s in picks:
        cells = "".join(f"<td>{wx(d['code'])[0]}<br><small class='muted'>新雪</small>{d['snow']:.0f}cm"
                        + (" <span class='tag'>パウダー</span>" if is_powder(s, d) else "")
                        + f"<br><small class='muted'>積雪</small>{d['depth']:.0f}cm</td>" for d in s["days"] if d["date"] in we)
        rec_rows += link_row(f"<b>{e(s['name'])}</b><br><small class='muted'>{PREF_SKI_REGION[s['pref']]} · {e(s['pref'])}</small>",
                             cells, f"snow.html#{s['key']}")
    rec = (f"<div class='scroll'><table class='sub top'>{cols}<tr><th>週末おすすめのスキー場</th>"
           + "".join(f"<th>{day_label(d)}</th>" for d in we) + f"</tr>{rec_rows}</table></div>") if picks else (
        "<p class='muted'>週末に新雪が見込めるスキー場は、いまのところ見当たりません</p>")
    fav = (f"<div class='favbox' hidden><div class='scroll'><table class='sub top'>{cols}<tr><th></th>"
           + "".join(f"<th>{day_label(d)}</th>" for d in we)
           + f"</tr>{fav_rows}</table></div></div>"
           + "<p class='favhint muted'>スノーボードのページでスキー場の詳細を開き「☆ お気に入り登録」を押すと、ここに表示されます</p>")
    # サーフ: 予報コメント + 週末4枠(土朝・土昼・日朝・日昼)の判定表
    surf_comment = "".join(f"<p class='comment'><b>{c}</b> {e(t)}</p>"
                           for c, t in surf_comments(spots, we))
    # 日付は2枠(朝・昼)にまたがる1つの見出しにする
    heads = ("<tr><th rowspan='2'></th>" + "".join(f"<th colspan='2'>{day_label(d)}</th>" for d in we) + "</tr><tr>"
             + "".join("<th><small>朝</small></th><th><small>昼</small></th>" for _ in we) + "</tr>")
    surf_rows = ""
    for s in spots:
        cells = ""
        for d in s["days"]:
            if d["date"] not in we:
                continue
            for sl in d["slots"]:
                mark, cls = RATE[sl["score"]]
                cells += (f"<td class='{cls}' title='波{sl['height']:.1f}m うねり{sl['swell']:.1f}m 周期{sl['period']:.1f}秒 {sl['kind']}'>"
                          f"<b>{mark}</b><small class='muted'>{sl['swell']:.1f}m</small></td>")
        surf_rows += link_row(f"<b>{e(s['name'])}</b>", cells, f"surf.html#{s['key']}",
                              f" class='favonly' data-fid='{e(fid('w', s))}' hidden")
    surf_list = (f"{surf_comment}<div class='favbox' hidden><div class='scroll'><table class='sub mini surf'>{heads}"
                 f"{surf_rows}</table></div></div>"
                 "<p class='favhint muted'>サーフィンのページでポイントの詳細を開き「☆ お気に入り登録」を押すと、ここに表示されます</p>")
    return f"""
<h1>今週末の山と雪と波</h1>
<section class='card big'><h2><a href='mountains.html'>⛰️ 登山</a></h2>
{mt_list}
</section>
<section class='card'><h2><a href='snow.html'>🏂 スノーボード</a></h2>{rec}{fav}
</section>
<section class='card'><h2><a href='surf.html'>🏄 サーフィン</a></h2>{surf_list}
</section>
<div class='note'><p>{label} の予報から、よさそうな場所をまとめました。</p>
<p>警報・注意報・火山の印は、今出ているものなので各ページの詳細で表示しています。</p>
<p>週末おすすめのスキー場: 週末の新雪の合計(パウダー期待の日は10cm分を加点)が多い順に、北海道・東北・関東・新潟・長野・北陸・東海・近畿などの地方が重ならないよう最大5か所。</p>
<p>週末おすすめの山: 週末の両日が○以上の山(上位10座、判定が良く風の弱い順)。気温は山頂付近の6〜15時の平均、風は同じ時間帯の最大。名前を押すと、各ページでその場所の予報が開きます。</p></div>
"""


# ---------------------------------------------------------------- 共通レイアウト
CSS = """
:root{color-scheme:light;--bg:#f6f7f5;--card:#fff;--line:#e2e4df;--text:#14171a;--sub:#55595e;--muted:#868a8f;
--accent:#1f6fb2;--r3:#0b7a3b;--r2:#2a78d6;--r1:#b27300;--r0:#c4312f;--sat:#2a6fd6;--sun:#d03b3b;--hl:#e8f1fb;--hyaku:#c4312f;--hyaku-bg:#fdecea;--fav:#f2b600}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#151617;--card:#1e2022;--line:#34373a;--text:#eef0f2;
--sub:#b9bdc2;--muted:#8c9196;--accent:#6aaee8;--r3:#47c47c;--r2:#5fa3ef;--r1:#e0a43a;--r0:#ef6b69;--sat:#6aa6f0;
--sun:#f07a7a;--hl:#1f2a36;--hyaku:#ff8a87;--hyaku-bg:#3a2222;--fav:#ffd23f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.65 system-ui,-apple-system,"Hiragino Sans","Noto Sans JP","Yu Gothic UI",sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
nav{max-width:1000px;margin:0 auto;padding:0 16px;display:flex;gap:4px;overflow-x:auto}
nav a{padding:12px 12px;color:var(--sub);text-decoration:none;white-space:nowrap;border-bottom:3px solid transparent}
nav a.on{color:var(--text);border-color:var(--accent);font-weight:600}
main{max-width:1000px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:22px;margin:4px 0 6px}h2{font-size:17px;margin:24px 0 8px}h3{font-size:16px;margin:0 0 8px}
h3 small,li small{color:var(--muted);font-weight:400}
.muted{color:var(--muted);font-size:13px}
a{color:var(--accent)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
.card h2{margin-top:0}.card h2 a{text-decoration:none;color:var(--text)}
.card ul{padding-left:18px;margin:6px 0}.card.big{margin:0 0 12px}
.sub td.l{white-space:normal;min-width:120px}
.mapbtn{display:inline-block;font-size:12px;padding:3px 10px;border-radius:99px;border:1px solid var(--line);
background:var(--card);color:var(--text);text-decoration:none;white-space:nowrap;box-shadow:0 1px 2px rgba(0,0,0,.12)}
.mapbtn:hover{border-color:var(--accent)}
/* 「飲食店」: 押すとジャンルのボタンがその場に並ぶ(浮かせると表の固定列に隠れるため) */
.menu{display:inline-block;vertical-align:top}
.menu>summary{list-style:none;cursor:pointer}.menu>summary::-webkit-details-marker{display:none}
.menu>summary::after{content:' ▼';font-size:10px}.menu[open]>summary::after{content:' ▲'}
.menu[open]>summary{background:var(--accent);border-color:var(--accent);color:#fff}
.menu.info[open]{display:block}.info-body{margin:2px 0 10px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;font-size:13px;line-height:1.9;white-space:normal}.info-body .muted{display:inline-block;min-width:9.5em}.info-body .about{margin:0 0 6px;line-height:1.7}
.menu-list{display:inline;margin-left:4px}.menu-list .mapbtn{margin:0 4px 4px 0}.mapbtn img{vertical-align:-2px;margin-right:4px;border-radius:3px}
tr.detail .mapbtn{margin:0 0 8px}
.xlink{display:block;color:inherit;text-decoration:none}.xlink b{color:var(--accent);text-decoration:underline;
text-decoration-thickness:1px;text-underline-offset:3px}.xlink b::after{content:' ›';text-decoration:none;display:inline-block;margin-left:2px}
.sub .dt,.sub td.l,.grid .nm{position:sticky;left:0;z-index:1;background:var(--card)}
tr.detail .sub .dt{background:var(--hl)}
tr.detail td>*{position:sticky;left:8px;max-width:calc(100vw - 72px)}.sub .dt{box-shadow:1px 0 0 var(--line)}
.hbtn{all:unset;cursor:pointer;color:inherit;text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:3px}
.hbtn[aria-expanded='false']::after{content:' ▼';font-size:10px}.hbtn[aria-expanded='true']::after{content:' ▲';font-size:10px}
.hbtn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
tr.hr td{font-size:12px;color:var(--sub);padding-top:3px;padding-bottom:3px}
tbody[id],section[id]{scroll-margin-top:56px}   /* 上に固定したメニューに隠れないように */
.alert{margin:6px 0;color:var(--r0);font-size:13px;font-weight:600}.alert small{font-weight:400;margin-left:4px}
.tag.danger{background:var(--hyaku-bg);color:var(--r0);font-weight:600}
.star{all:unset;cursor:pointer;display:inline-block;font-size:13px;padding:3px 12px;margin:0 0 8px;border-radius:99px;
border:1px solid var(--line);background:var(--card);color:var(--text);box-shadow:0 1px 2px rgba(0,0,0,.12)}
.star[aria-pressed='true']{border-color:var(--fav);font-weight:600}
.favmark{color:var(--fav)}
.star:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
tr.detail .star{display:table}tr.detail .btnrow .star{display:inline-block;vertical-align:top}h3+.star{margin-top:-2px}
.favbox h3{font-size:14px;margin:4px 0}
.intro{margin:0 0 8px;font-size:14px;color:var(--text)}
.fac{font-size:12px}
.frz{display:inline-block;width:17px;height:17px;border-radius:50%;background:var(--r2);color:#fff;font-size:10.5px;line-height:17px;text-align:center;font-weight:700;vertical-align:1px}
.obs{margin:6px 0}.obs b{display:inline!important}  /* 表の中の b{display:block} に負けないように */
.comment{background:var(--hl);border-radius:8px;padding:8px 12px;margin:0 0 8px;font-size:14px}
.sub.mini td,.sub.mini th{text-align:center;padding:4px 6px}.sub.mini td b{display:block}
.sub.mini td.l{text-align:left}
@media (min-width:600px){.sub.top{table-layout:fixed}.sub.top col.nmcol{width:40%}}.sub.mini.surf td:not(.l),.sub.mini.surf th{padding:4px 2px}
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
.mk{display:inline-block;width:24px;height:24px;vertical-align:-5px;font-size:0;background:url(kansai.svg) center/contain no-repeat}.mk.hyaku{background-image:url(hyaku.svg)}
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
// お気に入り: 見ている人のブラウザに保存する(保存できない環境でも表示は崩れない)
const FKEY='weather-site-favs';
let favs=null;
try{favs=JSON.parse(localStorage.getItem(FKEY));}catch(e){}
if(!Array.isArray(favs))favs=DEFAULT_FAVS.slice();
try{if(!localStorage.getItem(FKEY+'-surf')){if(!favs.some(f=>f.startsWith('w:')))favs=favs.concat(DEFAULT_FAVS.filter(f=>f.startsWith('w:')));
  localStorage.setItem(FKEY+'-surf','1');localStorage.setItem(FKEY,JSON.stringify(favs));}}catch(e){}
const isFav=f=>favs.includes(f);
let apply=()=>{},region='';
// 詳細は開くまで <template> に入れておき(ページを軽くするため)、初めて開いたときに組み立てる
const paintStars=root=>root.querySelectorAll('.star').forEach(b=>{const on=isFav(b.dataset.fid);
  b.innerHTML=on?"<span class='favmark'>★</span> お気に入り登録済み":'☆ お気に入り登録';b.setAttribute('aria-pressed',String(on));});
const paintFavs=()=>{
  paintStars(document);
  document.querySelectorAll('tbody.mt[data-fid]').forEach(b=>{b.dataset.fav=isFav(b.dataset.fid)?'1':'';});
  document.querySelectorAll('.favonly').forEach(b=>{b.hidden=!isFav(b.dataset.fid);});
  document.querySelectorAll('.favhead').forEach(h=>{h.hidden=!h.closest('table').querySelector('.favonly:not([hidden])');});
  document.querySelectorAll('.favbox').forEach(box=>{box.hidden=!box.querySelector('.favonly:not([hidden])');
    const hint=box.nextElementSibling;if(hint&&hint.classList.contains('favhint'))hint.hidden=!box.hidden;});
};
const openDetail=(tb,open)=>{const d=tb.querySelector('tr.detail'),b=tb.querySelector('.nm button');if(!d)return;
  const t=d.querySelector('template');if(t&&open){t.replaceWith(t.content.cloneNode(true));paintStars(d);}
  d.hidden=!open;if(b)b.setAttribute('aria-expanded',String(open));};
paintFavs();
const q=document.getElementById('q');
if(q){
  apply=()=>{const t=q.value.trim();
    document.querySelectorAll('tbody.mt').forEach(b=>{
      const inRegion=!region||b.dataset.region===region||(region==='fav'&&b.dataset.fav)||(region==='powder'&&b.dataset.powder)
        ||(region.startsWith('pref:')&&(b.dataset.prefs||'').split(',').includes(region.slice(5)));
      b.hidden=!(inRegion&&(!t||b.dataset.name.includes(t)));});};
  q.addEventListener('input',apply);
  document.querySelectorAll('.chip').forEach(c=>c.addEventListener('click',()=>{
    document.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x===c));
    region=c.dataset.r;apply();}));
}
// ボタンの反応はページ全体で1か所で受ける(あとから組み立てた詳細の中のボタンにも効く)
document.addEventListener('click',ev=>{
  const star=ev.target.closest('.star');
  if(star){const f=star.dataset.fid;
    favs=isFav(f)?favs.filter(x=>x!==f):favs.concat([f]);
    try{localStorage.setItem(FKEY,JSON.stringify(favs));}catch(e){}
    paintStars(document);
    document.querySelectorAll(`[data-fid="${CSS.escape(f)}"]`).forEach(b=>{if(b.matches('tbody.mt'))b.dataset.fav=isFav(f)?'1':'';});
    if(region==='fav')apply();
    return;}
  const hb=ev.target.closest('.hbtn');
  if(hb){const open=hb.getAttribute('aria-expanded')!=='true';
    for(let r=hb.closest('tr').nextElementSibling;r&&!r.querySelector('.hbtn');r=r.nextElementSibling)if(r.classList.contains('hr'))r.hidden=!open;
    hb.setAttribute('aria-expanded',String(open));return;}
  const nb=ev.target.closest('.nm button');
  if(nb){const tb=nb.closest('tbody');openDetail(tb,tb.querySelector('tr.detail').hidden);return;}
  // 「飲食店」などの一覧は、外を押したら閉じる
  document.querySelectorAll('details.menu[open]').forEach(m=>{if(!m.contains(ev.target))m.open=false;});
});
// #fav で来たときは「★お気に入り」の絞り込みを選んだ状態にする(トップページの「★お気に入りの山」から)
const favChip=location.hash==='#fav'&&document.querySelector(".chip[data-r='fav']");
if(favChip)favChip.click();
const target=location.hash&&document.getElementById(location.hash.slice(1));
if(target){openDetail(target,true);target.scrollIntoView({block:'start'});}
"""

PAGES = [("index.html", "週末"), ("mountains.html", "登山"), ("snow.html", "スノーボード"),
         ("surf.html", "サーフィン")]


def default_favs():
    """☆をまだ押していない人に最初から入れておくお気に入り(data/favorites.json の山・スキー場)"""
    favs = json.loads((ROOT / "data/favorites.json").read_text(encoding="utf-8"))
    mts = json.loads((ROOT / "data/mountains.json").read_text(encoding="utf-8"))
    return ([fid("m", m) for n in favs.get("mountain", []) for m in mts if m["name"] == n]
            + [f"s:{n}" for n in favs.get("ski", [])] + [f"w:{n}" for n in favs.get("surf", [])])


def layout(file, title, body, updated):
    nav = "".join(f"<a href='{f}' class='{'on' if f == file else ''}'>{n}</a>" for f, n in PAGES)
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} | 山と雪と波の天気</title>
<meta name="description" content="日本百名山と関西百名山の登山天気、スキー場の降雪、サーフポイントの波と風の予報をまとめたサイト">
<style>{CSS}</style></head><body>
<header><nav>{nav}</nav></header>
<main>{body}<p class='muted'>更新: {updated:%Y年%-m月%-d日 %H:%M}(3時間ごとに自動更新)</p></main>
<footer>予報は目安です。登山・海に出る前に、気象庁の警報・注意報と現地の情報を必ず確認してください。<br>
気象データ: <a href="https://open-meteo.com/">Open-Meteo.com</a>(CC BY 4.0) · ゲレンデ積雪: SURF&amp;SNOW · リフトの位置: © OpenStreetMap contributors ·
山の一覧・座標: Wikipedia「日本百名山」、Wikidata、ヤマレコ「関西百名山」</footer>
<script>const DEFAULT_FAVS={json.dumps(default_favs(), ensure_ascii=False)};{JS}</script></body></html>"""


def main():
    updated = datetime.now(JST)
    mts, ski, spots = build_mountains(), build_ski(), build_surf()
    alerts, volcanoes = fetch_alerts(), fetch_volcanoes()
    attach_alerts(mts, alerts, volcanoes, True, "mountain")
    attach_alerts(ski, alerts, volcanoes, True, "ski")
    attach_alerts(spots, alerts, volcanoes, False, "surf")
    for prefix, items in (("m", mts), ("s", ski), ("w", spots)):   # 行の id(トップページの「詳細」のリンク先)
        for i, x in enumerate(items):
            x["key"] = f"{prefix}{i}"
    OUT.mkdir(exist_ok=True)
    def first(items):                  # トップページは最初の TOP_DAYS 日だけ
        return [{**x, "days": x["days"][:TOP_DAYS]} for x in items]
    bodies = {"index.html": ("今週末", index_page(first(mts), first(ski), first(spots))),
              "mountains.html": ("登山天気", mountains_page(mts)),
              "snow.html": ("スノーボード", ski_page(ski)),
              "surf.html": ("サーフィン", surf_page(spots))}
    for f, (title, body) in bodies.items():
        (OUT / f).write_text(layout(f, title, body, updated), encoding="utf-8")
    for icon in (ROOT / "data/icons").iterdir():     # ボタンのアイコン
        (OUT / icon.name).write_bytes(icon.read_bytes())
    print("書き出し:", ", ".join(bodies), "→", OUT)


if __name__ == "__main__":
    main()
