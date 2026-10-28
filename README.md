# 山と雪と波の天気

日本百名山の登山天気、スキー場(滋賀・岐阜・長野・新潟)の降雪、サーフポイントの波と風をまとめた静的サイト。

- `build.py` … Open-Meteo から予報を取得して `public/` にHTMLを書き出す(Python標準ライブラリのみ)
- `data/` … 地点の一覧(山・スキー場・サーフポイント)。地点の追加・修正はここを編集する
- `.github/workflows/build.yml` … GitHub Actions で3時間ごとにビルドし、GitHub Pages に公開する
- `preview.py` … 開発用。`python3 build.py && python3 preview.py` でサーバー内だけで確認する

気象データ: [Open-Meteo.com](https://open-meteo.com/)(CC BY 4.0、無料枠は非商用のみ)
