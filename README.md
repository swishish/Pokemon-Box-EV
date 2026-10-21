# Pokemon Box EV

日盒 EV 小算盤（朋友向）· **v1.30.6**

給「不太懂金融／AI」的買卡朋友看的工具：首頁介紹點樣用 → 開盒算盤選系列 → 看 **四個預期回報 %**（同一分母）→ 有膜／無膜切換 → 看主力 chase 卡與「買單卡 vs 開盒」粗略提示。

資料來自靜態 snapshot（`ev_live`），**無登入、無即時行情**。盒價可用公開頁手動／Actions 刷新；單卡仍然要人手。

## 線上使用（GitHub Pages）

開啟：**https://swishish.github.io/Pokemon-Box-EV/**（首頁）

開盒 EV 小算盤：**https://swishish.github.io/Pokemon-Box-EV/calc.html**

英文裸卡市況（S&P-style pulse）：**https://swishish.github.io/Pokemon-Box-EV/market-en.html**

Base Set 復古指數（1st / Shadowless / Unlimited）：**https://swishish.github.io/Pokemon-Box-EV/market-vintage.html**

中世代指數（DP → SM chase Top 50）：**https://swishish.github.io/Pokemon-Box-EV/market-midera.html**

Chase 公允值（PSA10 回歸散點）：**https://swishish.github.io/Pokemon-Box-EV/chase-fit.html**

日版 PSA10 指數（SV chase 60 · 等權／流動性／價格加權）：**https://swishish.github.io/Pokemon-Box-EV/market-jp-psa10.html**

日版 PSA10 廣度基準（復古／中世代／現行，N 跟篩選浮動）：**https://swishish.github.io/Pokemon-Box-EV/market-jp-psa10-bench.html**

姊妹站 Base Unlimited（Base Set Unlimited 速查）：**https://swishish.github.io/Base-Set-Unlimited-Cheatsheet/**（頂欄外連，唔喺本 repo）

公開 repo 的 GitHub Pages **免費**，無需付費。首頁**唔會**自動跳去算盤。`calc.html?set=M6` 仍然可以深連結指定系列。

## EN market pulse（Phase 1）

獨立頁 `market-en.html`：英文 **raw singles** 市場溫度（價格加權 Top 500，基準 1,000），資料是靜態 JSON，不經瀏覽器打 TCGPlayer。

| 顯示 | 說明 |
| --- | --- |
| Index + % | 預設 **1D**＝相對上一份已提交快照。第一次建檔只有指數 1,000，沒有每日 % |
| 5D / 30D / 1Y / YTD / 自訂 | 用 `history.json` 真實快照算區間報酬；缺日跳過、**不補價**。沒有 cutoff 當天或更早的點就 disable。自訂日期只放大圖，**不**把指數重設成 1000 |
| Breadth | 籃子裡上漲 vs 下跌張數（有歷史才有） |
| Top gainers / losers | 期間可選 1D／5D／30D／YTD（`price-history.json`）；歷史快照不足則 N/A，不虛構報酬 |
| Disclaimer | 每日 TCGCSV；歷史 TickerMint（免費、需 linkback）；僅 EN raw；非正式投資建議 |

### 資料怎麼更新

來源是免費 [TCGCSV](https://tcgcsv.com) 每日 Pokemon category dump（category 3＝英文），**不需要 TCGPlayer API key**，也不爬 SNKRDUNK／PriceCharting HTML。

1. GitHub Action **Update EN market pulse**（`.github/workflows/update-market-en.yml`）
   - **排程**：每天 21:23 UTC（TCGCSV 約 20:05 UTC 刷新之後）
   - **手動**：Actions 頁按 **Run workflow**
   - **PR**：只跑 self-test（`refresh_market_en` + `fetch_tcgcsv_archive` + `backfill_tickermint` + `backfill_tickermint_vintage` + `refresh_market_vintage` + `refresh_market_midera` + `backfill_tickermint_midera` + `refresh_sealed_boxes` + `refresh_market_jp_psa10` + `refresh_market_jp_psa10_bench`），不抓 dump、不 commit
2. 腳本 `python3 tools/refresh_market_en.py` 先讀 `https://tcgcsv.com/last-updated.txt`；stamp 沒變就結束（零流量）。
3. 有新 dump 才抓 `Groups.csv` + 各系列 `ProductsAndPrices.csv`，寫入：
   - `data/market-en/latest.json`（指數、廣度、漲跌榜、500 張成分——給下一次對比用）
   - `data/market-en/history.json`（**append／upsert 當天**，保留 backfill 舊點與 `baseDate`／`returns`）
   - `data/market-en/price-history.json`（成分市價，用來算漲跌榜 5D／30D／YTD）
   - `data/market-en/latest.js`、`history.js`（`file://` / 快取備援）
4. Action 把變更 commit 回預設分支；GitHub Pages 仍是純靜態。

本機重跑每日管線（**只要 Python 標準庫**）：

```bash
python3 tools/refresh_market_en.py --self-test
python3 tools/refresh_market_en.py -v --force
```

### 三條資料路（請照這個用）

| 用途 | 來源 | 現況 |
| --- | --- | --- |
| **每天的「今天」市況** | TCGCSV **現況** dump（`Groups.csv` + 各套 `ProductsAndPrices.csv`） | 正常。GitHub Action 每晚跑 `python3 tools/refresh_market_en.py` |
| **往回的價格歷史（5D／30D／YTD）** | 免費 [TickerMint API](https://tickermint.cards/developers/pokemon) | **請用這條。** 無需 API key。product id = TCGPlayer / TCGCSV 同一個號碼 |
| TCGCSV **dated archive** `.ppmd.7z` | `https://tcgcsv.com/archive/tcgplayer/prices-YYYY-MM-DD.ppmd.7z` | 仍 **HTTP 403**。腳本 `tools/fetch_tcgcsv_archive.py --probe` 留著，但**不要靠它回填** |

起始日預設 **2024-02-08**（TickerMint 與 TCGCSV FAQ 都是這天開始有歷史）。Gary 的 **2024-02-28** 是可選較晚切點：指令加 `--start 2024-02-28`。

#### 用 TickerMint 補歷史（複製貼上）

免費、非商業、每分鐘最多 120 次。本站頁尾與卡名旁有 TickerMint 連結（ToS 要求 linkback）。**沒有**實作 $99 的 CSV dump。

```bash
python3 tools/backfill_tickermint.py --self-test
python3 tools/backfill_tickermint.py --sample
python3 tools/backfill_tickermint.py --backfill --confirm BACKFILL
python3 tools/backfill_tickermint.py --rebuild-movers
```

1. `--self-test`：不連網，用小樣本確認「缺價不補 0、1st Edition 不當代表價、5D／30D／YTD 在有足夠快照時會亮」。
2. `--sample`：只抓籃子裡最貴的幾張卡，**不改**網站上的 `history.json`。約幾秒。用來確認 API 通。這幾張常常是 2026 新卡，示範的 30D 可能用到更早的真實點；全 500 回填後區間會密很多。
3. `--backfill --confirm BACKFILL`：抓現況 Top 500 全部（約 500 次、約 4–5 分鐘），重寫 `history.json`、`latest.json` 的 divisor，以及 `price-history.json`（成分週線市價），所以 Top 10／Bottom 10／廣度在 rebase 當天就能用，不必等下一份每日 dump。指數會 **rebase**（第一個有資料的日子＝1,000）。預設 **每週一點**（`--step-days 7`，約 140 點，GitHub Pages 很輕）。每週取樣時，5D 會用 cutoff 當天或更早「最近的一個真實點」（可能是 6–7 天前），**不會**補中間的日子。若要每日一點（約 980 點）：

```bash
python3 tools/backfill_tickermint.py --backfill --confirm BACKFILL --step-days 1
```

4. `--rebuild-movers`：只從 TickerMint 週線重算漲跌榜，**不改**指數水平。舊回填若把 `prevPrice` 清成空，用這條。

之後每日 Action 仍用 TCGCSV **現況**往後加一天，接在這條歷史後面。

空的 market／沒有那天的資料：**跳過，不當 $0**。新卡（例如 2026 才上市的 Mew）從 TickerMint 第一個有市價的日子才進籃子。

#### TCGCSV archive（備用，現在 403）

```bash
python3 tools/fetch_tcgcsv_archive.py --self-test
python3 tools/fetch_tcgcsv_archive.py --probe
```

`--probe` 若仍是 403 會結束、不改 JSON。Archive 若有一天恢復，用法見 `data/tcgcsv-archive/README.md`。

#### 容量

| | 估計 |
| --- | --- |
| TickerMint `--sample`（5 張） | ~5 次 API，幾秒 |
| TickerMint `--backfill` 每週 | ~500 次 API（約 4–5 分鐘）＋ `history.json` 約 140 點（幾十 KB） |
| TickerMint `--step-days 1` | 同樣 500 次 API；`history.json` 約 980 點（仍很小，約 100 KB） |
| TCGCSV dated 7z 全日回填 | 不要 git（數 GB）；而且現在下載不到 |

Phase 1 **不做**日版熱度頁、eBay、PriceCharting 付費 API、TickerMint $99 dump。

## Base Set vintage indexes（EN raw）

獨立頁 `market-vintage.html`：三條 **固定** chase 籃子（Holo Rare + Rare），各自基準 1,000。同一頁可切 **指數 Pulse**／**卡冊 Binder**（格狀縮圖＋USD；`#1st/binder` 深連結）。

| 指數 | 印刷 | TCGCSV |
| --- | --- | --- |
| Base 1st Edition | 第一版 stamp | group 1663，subtype `1st Edition` / `1st Edition Holofoil` |
| Base Shadowless | 無限版、無右緣陰影 | group 1663，subtype `Unlimited` / `Unlimited Holofoil` |
| Base Unlimited | 後來有陰影的無限版 | group 604，Holofoil / Normal |

TCGPlayer 沒有獨立的「1st Edition set」：1st 與 Shadowless 共用 Shadowless group 的同一個 product ID，用 subtype 分開。詳細 ID／缺口見 `data/market-vintage/baskets.json`（Machamp 008/102 暫無單卡 ID；1st Edition Charizard／Blastoise／Mewtwo 常常沒有 Market Price，當天不進指數）。

### 每日 log

同一個 GitHub Action **Update EN market pulse** 在 EN Top 500 之後跑 `python3 tools/refresh_market_vintage.py`：stamp 沒變就結束；有新 dump 才抓 group 604 + 1663，append／upsert 當天（`latest.json` / `history.json` / `price-history.json` 與 `.js` 備援）。缺日不補價。

本機：

```bash
python3 tools/refresh_market_vintage.py --self-test
python3 tools/refresh_market_vintage.py -v --force
```

重建凍結名單（不寫歷史）：`python3 tools/refresh_market_vintage.py --rebuild-baskets -v`

### TickerMint 回填歷史（複製貼上）

免費、非商業、每分鐘最多 120 次。**不要**用 `tools/backfill_tickermint.py`（EN Top 500）——那支會跳過 1st Edition，1st 會誤用 Shadowless 價。Vintage 必須按印刷篩：1st＝`1st Edition`／`1st Edition Holofoil`；Shadowless＝同一 BSS id 的 `Unlimited`／`Unlimited Holofoil`；Unlimited＝group 604 自己的 Holofoil／Normal。1st 與 Shadowless 共用 31 個 product id，加上 Unlimited 31 個，**約 62 次 API（約 1 分鐘）**，不是 93。

```bash
python3 tools/backfill_tickermint_vintage.py --self-test
python3 tools/backfill_tickermint_vintage.py --sample
python3 tools/backfill_tickermint_vintage.py --backfill --confirm BACKFILL
```

1. `--self-test`：不連網。確認印刷篩選、unique id＝62、缺價不補 0、沒有 `--confirm BACKFILL` 就拒寫。
2. `--sample`：只抓幾張 BSS + Unlimited，**不改**網站上的 JSON。
3. `--backfill --confirm BACKFILL`：回填 2024-02-08 起、預設**每週一點**（`--step-days 7`，每指數約 140 點），每條指數第一個有資料的日子 rebase 成 **1,000**（固定歷史基準，不是每個期間重設）。會重寫 `history.json`、`latest.json` 的 divisor，以及 `price-history.json`（成分週線市價），所以 Top 10／Bottom 10／廣度在 rebase 當天就能用，不必等下一份每日 dump。**今天的指數水平會離開暫時的 Oct-2026＝1,000 序列**。必須打 `BACKFILL`，不要把舊鏈混進去。
4. `--rebuild-movers`：只從 TickerMint 週線重算漲跌榜，**不改**指數水平。舊回填若把 `prevPrice` 清成空，用這條。

可選較晚切點：`--start 2024-02-28`。每日一點：`--step-days 1`。頁尾／卡名有 TickerMint linkback（ToS）。快取在 `data/tickermint-cache/`（gitignore）。History 與 EN 一樣由 Gary 本機或手動 Action 跑完再 commit——本 PR **不**塞進已回填的 140 點檔。

風險：1st Edition 在 Charizard／Mewtwo／Blastoise 上常常沒有 Market Price（籃子較薄）；rebase 後今日指數不再是 1,000。

## Mid-era Top 50（EN raw, DP → SM）

獨立頁 `market-midera.html`：一條**固定** chase 籃子（約 50 張），價格加權，基準 1,000。時代＝Diamond & Pearl → Sun & Moon（含 BW／XY／SM）。**不含** Base／Wizards 復古，也不含 Sword & Shield／Scarlet & Violet。不是每週重排的 Double Holo Mid-Era 100。

怎麼選的：從 2026-10-12 EN Top 500（已有 TCGPlayer Market Price＝流動性）篩中世代，按當日市價排名，凍結前 50 個 product ID 於 `data/market-midera/baskets.json`。每日 job **不**重建名單。

指數規則與 vintage／EN 相同：缺價不當 $0；卡從第一個有市價日才進籃；rebase＝**1,000 @ 2024-02-08**（TickerMint 週線已回填）。期間視窗只放大圖，不把每段重設成 1000。Top 10／Bottom 10 用最後兩份有市價快照的 pairwise（同 EN Pulse）。

```bash
python3 tools/refresh_market_midera.py --self-test
python3 tools/refresh_market_midera.py -v --force
python3 tools/backfill_tickermint_midera.py --self-test
python3 tools/backfill_tickermint_midera.py --sample
python3 tools/backfill_tickermint_midera.py --backfill --confirm BACKFILL
python3 tools/backfill_tickermint_midera.py --rebuild-movers
```

同一個 GitHub Action **Update EN market pulse** 在 vintage 之後跑 mid-era：只抓凍結 ID 所在的 ~28 個 group，append／upsert 當天。Base 1st／Shadowless／Unlimited 腳本與 JSON **不動**。

## 本機打開

1. 下載本 repo
2. 雙擊 **`index.html`**（首頁）或 **`calc.html`**（算盤）；或用瀏覽器 File → Open
3. 用本機 HTTP 開（見下）：算盤 **`fetch('./data.json')`**，失敗則讀同資料夾 **`data.js`**。不要再把整份 JSON 內嵌進 `calc.html` 的 `<script>`（`</script>`／相鄰 `<script>` 會令 `EV_DATA` 派唔到）

或：

```bash
python3 -m http.server 8765
```

瀏覽器開：`http://127.0.0.1:8765/`（首頁）、`http://127.0.0.1:8765/calc.html`（算盤）、`http://127.0.0.1:8765/market-en.html`（EN 市況）、`http://127.0.0.1:8765/market-vintage.html`（Base 復古）、`http://127.0.0.1:8765/market-midera.html`（中世代）、`http://127.0.0.1:8765/market-jp-psa10.html`（日版 PSA10 chase 60）、`http://127.0.0.1:8765/market-jp-psa10-bench.html`（日版 PSA10 廣度基準）或 `http://127.0.0.1:8765/chase-fit.html`（Chase 公允值）

## Chase 公允值（PSA10 回歸）

獨立頁 `chase-fit.html`：Gary 的 **Chase_Fit** 模型。散點（預測 vs 實際）在篩選下方、**便宜過模型** 清單上方（預設最負殘差、淚眼蜥可釘選；四關 ✓/✗ 對 workbook Strong Buy）。繁中名見 `data/names_zh.json`（official 名才翻譯）；SNKRDUNK 產品頁見 `data/snkrdunk_ids.json`（未知則搜尋）。清單縮圖見 `data/chase-fit/thumbs.json`（JP＝算盤本機 `img/cards/*.webp`，EN＝市況已存 TCGPlayer CDN；對唔到就 cream placeholder，唔發明 SNKR／PC 圖）。散點＝預測 PSA10 vs 實際 PSA10，下方另有 POP × PSA10/raw 倍數圖。公式／係數收入摺疊「方法」。資料是靜態 JSON（`data/chase-fit/latest.json`）。圖例標籤可點選篩選。篩選寫入網址；清單排序／淚眼蜥另存 localStorage。頂列有 **模型日期** chip 同 **本週變動**。

**沒有** GitHub Action 會自動匯出 Chase——每週要 Gary 的 dated workbook，然後本機／agent 跑：

```bash
python3 tools/export_chase_fit.py --self-test
python3 tools/build_chase_thumbs.py --self-test
python3 tools/export_chase_fit.py /path/to/pokemon_psa10_regression_YYYY-MM-DD.xlsx
```

只發佈 sheet **Chase_Fit**（唔用 `Chase_Fit_Large`）。會先把舊 `latest.json` 存去 `data/chase-fit/archive/YYYY-MM-DD.json`。缺欄就拒絕寫入。數字為模型快照，**非正式投資建議**。資料日期超過 7 日會顯示灰色「數字過期」。

每週報告裡 Vintage_Fit 的 Strong Buy（例如 臭泥 Grimer #88）**唔會**出現在此頁，除非之後另接 Vintage_Fit。

## 刷新日盒盒價（給 Gary · 唔使識寫 code）

算盤頂欄「盒價」嘅預設分母（有膜／無膜落地）而家可以用公開頁自動更新，**唔使逐個盒抄價**。只讀 SNKRDUNK／PriceCharting 公開資料，**唔會登入、下單、出價或傳訊息**。

**一週刷新一次就夠**（盒價會變，但唔使日日睇）。

1. 打開 GitHub → **Actions** → 左邊揀 **Refresh sealed box prices** → **Run workflow** → 再按一次綠色 **Run workflow**
2. 等佢跑完。成功就會自動把新盒價寫入網站資料。
3. 若 Actions 失敗（有時 SNKRDUNK 會擋伺服器 IP），用本機（只要有 Python，唔使安裝套件）：

```bash
python3 tools/refresh_sealed_boxes.py --self-test
python3 tools/refresh_sealed_boxes.py --root .
```

然後照常 commit／push `data.json` 同 `data.js`。

**而家自動得到：** 各系列有膜盒價（通常取 2–4 盒一組嘅公開掛牌，再加原本嗰條落地運費）、搵到就一齊更新無膜、以及 PriceCharting 盒均價（只係參考連結，唔會改成盒價分母）。

**仍然要人手：** 單卡 Grade A／抽出率。呢啲仍要先手存每個系列嘅 PriceCharting 卡表（`/tmp/pc-sets/M6.html` 噉），再跑下面嘅 `refresh_box_ev.py`。橙色「數字已過期」橫幅跟單卡資料日期，唔單止盒價。

唔會發明未見過嘅 SNKRDUNK 產品編號或價格；某系列抓唔到就留舊數。

## Box EV 週更（單卡 · 仍然手動）

開盒算盤 `calc.html` 用 `data.json`／`data.js`。`tools/refresh_box_ev.py` 要先有手存的 PriceCharting **單卡** console HTML（`/tmp/pc-sets/{SET}.html`，例如 `M6.html`）。**沒有這些檔就唔好跑**——腳本會拒絕寫入，避免虛構價格或把 `as_of` 推到今天。SNKRDUNK Grade A **單卡**亦唔會自動爬。市場頁先由 `update-market-en.yml` 每日更新；Chase 同單卡 Box EV 仍然係人手。`calc.html` 資料超過 **8 日**會用橙色過期橫幅。

```bash
python3 tools/refresh_box_ev.py --root . --pc-dir /tmp/pc-sets
```

## 日版 PSA10 指數

獨立頁 `market-jp-psa10.html`：同一個凍結籃子畫 **三條** 鏈結報酬指數（基準 1,000）＋下方成交量柱。

| 線 | 權重 |
| --- | --- |
| 等權（實線） | 每張 1/n（廣度） |
| 流動性（虛線） | 近 30 日 SNK PSA10 成交日圓（先用最近 20 筆；冇就用刊價日×價），**每張最多 3%** |
| 價格（點虛線） | 滯後市價／籃子總價（Gary 的 market weight），**同一個 3% 上限** |

價＝SNKRDUNK 公開 `trading-history` PSA10 圖價（JP 站、只讀）。缺日用上一次刊價延續，**唔補 ¥0**。量柱＝當日有刊價的成分張數（SNK 唔公開完整成交筆數；不是 pokeca 乘數）。刊價缺口 **> 14 日**當重入（當日報酬 0）；其餘單卡日報酬 **±25%** 封頂。基準 1,000＝第一日有 ≥40 張有價。

籃子來自本站 `data.json` 已有嘅 apparel ID，再篩最低價 ¥3,000 ＋ 90 日 ≥12 個刊價日，凍結約 60 張。每日 job **唔重排**。

```bash
python3 tools/refresh_market_jp_psa10.py --self-test
python3 tools/refresh_market_jp_psa10.py --rebuild-baskets --confirm REBUILD -v
python3 tools/refresh_market_jp_psa10.py --from-cache
python3 tools/refresh_market_jp_psa10.py -v
```

GitHub Action **Update JP PSA10 index** 每晚跑刷新；PR 只跑 self-test。詳見 `data/market-jp-psa10/README.md`。

## 日版 PSA10 廣度基準

獨立頁 `market-jp-psa10-bench.html`：**唔取代**上面嗰條 60 張 SV chase 袖。呢頁係 representable 日版 PSA10 市場（Pokeca 意圖：時代扇區、√(sales×price) 排名、等權頭條＋3% cap 流動／價格、季度重組名單）。**唔再**每區鎖 80／補到 80／截到 80——N 跟流動性／刊價篩選浮動；綜合＝三區聯集。

| 籃子 | 說明 |
| --- | --- |
| 綜合 Composite | 復古＋中世代＋現行嘅篩選聯集（N 浮動；唔補／截到 500） |
| 復古 Vintage | SM 之前（約 1996–2016：XY／BW／DP…） |
| 中世代 Modern | SM＋劍盾 S（Tag Team 亦算入呢區） |
| 現行 Current | SV＋MEGA；宇宙來自 SNK 搜尋，唔限 `data.json` 657 個 chase ID |
| 特典 Promo | 平行姊妹：Grey Felt Hat、Munch／Picasso／長場、Mario／Luigi／poncho 皮卡丘、Extra Battle Day 等 |
| Tag Team / MID | 平行姊妹溫度計（SM8b–SM12a／SMM），避免中世代隱形 |

四條每日重算權重（Gary 順序）：等權 → 流動性 3% cap → 價格 3% cap → 價格無 cap（只疊加）。成分**季度**重組（1／4／7／10 月 1 日 JST），每日 job 只刷新刊價同權重。

```bash
python3 tools/refresh_market_jp_psa10_bench.py --self-test
python3 tools/refresh_market_jp_psa10_bench.py --rebuild-baskets --confirm REBUILD -v
python3 tools/refresh_market_jp_psa10_bench.py --reselect -v
python3 tools/refresh_market_jp_psa10_bench.py --from-cache
python3 tools/refresh_market_jp_psa10_bench.py -v
```

GitHub Action **Update JP PSA10 broad benchmark** 每晚重算權重、季度重組名單；PR 只跑 self-test。詳見 `data/market-jp-psa10-bench/README.md`。

## Changelog（摘要）

- **v1.30.6**：JP PSA10 基準分享圖改走新檔名 `og-psa10-bench-v2.png`（WhatsApp 會按路徑快取舊 `og.png`／`og-psa10-bench.png`，`?v=` 甩不掉）。`og:site_name`／title 仍是「日版 PSA10 基準」。首頁／Chase 公允值繼續用 EV `og.png`；chase 指數繼續用 `og-psa10.png`。指數數學／refresh 工具不變
- **v1.30.5**：JP PSA10 基準／chase 指數用獨立 OG 圖與 `og:site_name`（不再共用首頁「日盒開盒值不值？」＋`og.png`）。WhatsApp／Facebook 要新 image URL 才會甩掉舊卡。基準／市況「這是什麼 · 方法」改為書面繁中（不使用粵語口語）。指數數學／refresh 工具不變
- **v1.30.4**：JP PSA10 廣度基準扇區**唔再鎖 80 張**——N 跟刊價／90 日刊價日篩選浮動；綜合＝三區聯集（唔補／截到 500）。舊 60 張 chase 袖不變
- **v1.30.3**：JP PSA10 chase／基準圖預設期間改 **1Y**（1D／5D／30D／YTD／All／自訂仍可撳）。基準頁六格指數 tile 顯示等權期間 %（跟圖表 tenor）；手機 2×3 格收密。指數數學／refresh 工具不變
- **v1.30.2**：JP PSA10 籃子把 SNK `_’s Pikachu` 寫入空位顯示成 PSA `___'s Pikachu`（去掉 `: Old Back/PROMO` 噪音）。v1.30.1 的等權＋流動預設／廣度跟期間不變
- **v1.30.1**：JP PSA10 chase／基準圖預設只開等權＋流動性；廣度跟圖表期間（各成分窗口起迄 LOCF）；籃子顯示 SNK EN 卡名。指數數學／籃子／refresh 工具不變
- **v1.30.0**：新增日版 PSA10 **廣度基準**（`market-jp-psa10-bench.html`）——復古／中世代／現行每區地板 80、特典＋Tag Team 姊妹籃子、四條權重（等權／流動 3% cap／價格 3% cap／價格無 cap 疊加）、每日權重／季度名單。舊 60 張 `market-jp-psa10.html` SV chase 袖不變
- **v1.29.0**：新增日版 PSA10 指數頁（`market-jp-psa10.html`）——同一籃子等權／流動性／價格加權三條線 + SNK 刊價量柱；3% 單卡上限；公開 SNKRDUNK trading-history。EV／Chase_Fit／EN／復古／中世代算法不變
- **v1.28.11**：日盒**盒價**可用公開 SNKRDUNK 掛牌＋PriceCharting 盒頁刷新（`tools/refresh_sealed_boxes.py`，Actions 手動掣）。只讀、唔登入／下單。單卡 Grade A／抽出率同 EV 算法不變
- **v1.28.10**：Chase「落後籃子」——模型殘差低估（最低估先）；與 EN 指數重疊的英文卡另給 30D 落後（卡 % − 指數 %，唔發明 ID）。中世代「落後籃子」同 Unlimited：30D／YTD vs 同一籃子指數。EV／Chase_Fit 算法不變
- **v1.28.9**：Base 復古 Unlimited「落後籃子」——成分 30D／YTD 相對同一籃子指數落後（最落後先；真實快照，不補價）。中世代圖表加 **All**（全部已有日期、唔重設 1,000）、懸停／點選睇指數與日期、卡冊／籃子 DP·BW·XY·SM 晶片。EV／Chase_Fit 算法不變
- **v1.28.8**：Chase「便宜過模型」清單加卡縮圖（JP＝本機 `img/cards/*.webp`，EN＝市況已有 TCGPlayer `_200w.jpg`；無對就 placeholder）。`loading=lazy`＋`decoding=async`。EV／Chase 算法不變
- **v1.28.7**：分享 OG／Twitter 圖；算盤 chase 縮圖 `loading=lazy`＋`decoding=async`（JP 仍用本機 webp，唔發明 SNKR／PC 圖 CDN）；Base 復古輸出 `data/market-vintage/cheatsheet.json` 給姊妹 cheatsheet 取價。EV／Chase 算法不變
- **v1.28.6**：Chase 公允值加「便宜過模型」買入清單（圖表上方）——最負殘差排序、淚眼蜥釘選、四關 ✓/✗、official 繁中名、已知 SNKRDUNK 產品頁；係數收入摺疊「方法」。EV／市況算法不變
- **v1.28.5**：Chase 公允值接每週 `Chase_Fit` workbook（`tools/export_chase_fit.py`）——模型日期 chip、本週 Strong Buy 進出、封存 10-09、上 10-16 快照；首頁 PriceCharting／SNKRDUNK 死連結修好；隱藏已放棄的「日語熱度」佔位。Box EV 仍要手存 `/tmp/pc-sets`，過期 ≥8 日橙色橫幅。EV／市況算法不變
- **v1.28.4**：Base 復古——圖表期間 **All**＝該條全部已有日期（唔重設 1,000）；三條重疊改叫 **三條 Overlay**（唔再搶 All）；廣度跟漲跌榜期間掣即時更新。EV／Chase_Fit／EN／中世代算法不變
- **v1.28.3**：Base 復古手機 Top 10／完整籃子表可橫滑，USD（含 carried）唔再被裁成「$3」。v1.28.2 的廣度期間／All 圖／期間記憶不變
- **v1.28.2**：Base 復古頁——廣度跟漲跌榜同一個期間（5D／30D／1Y／YTD／自訂）；圖表加 **全部 All** 三條重疊（只放大、唔重設 1,000）；指數期間同漲跌榜期間各自記住，換 1st／Shadowless／Unlimited／All 唔會打回 1D。EV／Chase_Fit／EN／中世代算法不變
- **v1.28.1**：市況漲跌榜期間列補 **1Y** 同由–至自訂日期（EN／復古／中世代），同圖表期間列對齊；自訂只篩該段漲跌、唔重設指數基準 1,000。EV／Chase_Fit 算法不變
- **v1.28.0**：頂欄改成左 Pokéball＋標題＋版本、右黃 3D 導覽鈕（手機自動換行）；Pokéball／標題回首頁同一分頁；Base Unlimited 外連改同一分頁。市況圖加 **1Y** 同由–至自訂日期，只放大視窗、不重設基準 1,000。EV／Chase_Fit 算法不變
- **v1.27.0**：中世代 TickerMint 週線回填（2024-02-08 起 rebase 1,000）＋最後兩份有市價快照填 Top 10／Bottom 10；圖表懸停提示對齊游標（不再用 viewBox 對 CSS 寬度錯位）；市況頁改奶油紙面／黃 3D 圓鈕／黑框，頂欄 nav 泡泡改成內容寬度內一條帶。EV／Chase_Fit 算法不變
- **v1.26.0**：新增中世代 EN raw 指數（`market-midera.html`）——DP→SM 固定 chase Top 50，價格加權，意圖 rebase 1,000 @ 2024-02-08。每日 TCGCSV append；TickerMint 週線回填腳本已備、跟 vintage #28 一樣先不上完整歷史。Base 1st／Shadowless／Unlimited 與 EN Top 500 算法不變
- **v1.25.2**：EN Top 500 rebase 後立刻用週線最後兩份有市價快照填 Top 10／Bottom 10／廣度（1D／5D／30D／YTD）。缺市價仍空白，不虛構、不重設指數。EV／Chase_Fit／復古算法不變
- **v1.25.1**：復古指數 rebase 後立刻用週線最後兩份有市價快照填 Top 10／Bottom 10／廣度（1st／Shadowless／Unlimited 同一條路）。缺印刷市價仍空白，不虛構。EV／Chase_Fit／EN Top 500 算法不變
- **v1.25.0**：Base Set 復古指數加印刷感知的 TickerMint 週線回填（`tools/backfill_tickermint_vintage.py`，2024-02-08 起、rebase 需 `--confirm BACKFILL`）。**不要**重用 EN 的 `backfill_tickermint.py`（會跳過 1st Edition）。5D／30D／YTD 在 Gary 跑完回填後才會亮。EV／Chase_Fit／EN Top 500 算法不變
- **v1.24.0**：新增 Base Set EN 復古指數頁（`market-vintage.html`）——1st Edition／Shadowless／Unlimited 三條固定 chase 籃子，每日 TCGCSV 只 append 一點，**不回填**歷史；同一頁有卡冊 Binder 格狀速覽。EV／Chase_Fit／EN Top 500 算法不變
- **v1.23.0**：EN 市況歷史改走免費 TickerMint API 回填（TCGCSV archive 仍 403）；頁尾／卡名 TickerMint linkback；指數 5D／30D／YTD 有真實快照才亮。每日現況仍是 TCGCSV dump。EV／Chase_Fit 算法不變
- **v1.22.0**：EN 市況指數 5D／30D／YTD period picker（真實 `history.json` 快照，缺日不補價）＋ `tools/fetch_tcgcsv_archive.py`（7z 解 TCGCSV archive，預設起點 2024-02-08；2024-02-28 為可選較晚切點）。Archive 仍 403 時不發明價格。EV／Chase_Fit 算法不變
- **v1.21.8**：EN 市況漲跌榜加 **1D／5D／30D／YTD** 期間選擇。多日報酬只用 repo 裡已提交的每日快照；缺歷史（含 TCGCSV archive 目前 403）就顯示 N/A，不虛構數字。EV／Chase_Fit 算法不變
- **v1.21.7**：加書籤／分頁 favicon（`favicon.svg`，原創簡幾何 Pokéball）。各公開頁加 `<link rel="icon">`。EV／Chase_Fit 算法不變
- **v1.21.6**：算盤切系列／TOC／pack dropdown 時保留上次選的有膜／無膜；新系列無無膜落地則沿用有膜、不虛構價格。EV／Chase_Fit 算法不變
- **v1.21.5**：頂欄加姊妹站 **Base Unlimited** 外連（首頁／算盤／Chase／EN 市況）；手機頂欄 nav 改全寬換行，避免溢出。EV／Chase_Fit 算法不變
- **v1.21.4**：Chase 公允值圖例標籤改為可點選篩選（可多選；全關＝沒有點，按「顯示全部」還原）。EV／Chase_Fit 算法不變
- **v1.21.3**：算盤「回到默認」——一鍵還原 haircut／Gem／Ask／費用／盒價（有膜落地）；進階假設內同頂欄都有。EV 算法不變
- **v1.21.2**：手機算盤取消四個 %／盒價／進階假設整疊 sticky，跟頁面一齊滾，Chase／方法論可佔過半畫面；桌面仍 sticky。EV 算法不變
- **v1.21.1**：Chase 公允值詳情加 PriceCharting「睇卡相／價」產品頁連結（117 張皆有）；JP 另有 SNKRDUNK 搜尋、EN 另有 TCGPlayer 搜尋。唔熱鏈第三方卡圖。EV 算法不變
- **v1.21.0**：新增 Chase 公允值頁（`chase-fit.html`）——117 張 PSA10 回歸散點、點擊睇殘差與公式組成；首頁導覽／有用連結。EV 算法不變
- **v1.20.4**：首頁有用連結加 GemRate、Grade10 PTCG100、皮卡千、TCGPlayer JP（純文字、一行說明）；Rarebox 說明改「日版ポケカ相場參考」；保留 SNKRDUNK／PriceCharting／GitHub／EN 市況。EV 算法不變
- **v1.20.3**：手機算盤「設定」sheet 常開（盒價／四個 %／恢復預設可見）；只有「進階假設」預設收起。唔再把成塊設定藏喺「設定」掣後面。EV 算法不變
- **v1.20.2**：首頁改圖文 one-pager——有用連結放 hero 正下方（大 tile＋chips，含 Rarebox 純文字）；點樣用五步示意（揀系列→sticky %／燈→haircut／盒價→Chase→買單卡 vs 開盒）；首頁唔再 auto-redirect。方法論改為預設收起。EV 算法不變
- **v1.20.1**：首頁 Useful Links 加 Rarebox（純文字）；首頁／算盤頂欄加 EN 市況＋日語熱度導覽（日語熱度頁未上線則顯示即將推出，無死連結）
- **v1.20.0**：首頁／算盤拆開——`index.html` 為介紹＋有用連結；算盤改去 `calc.html`；點擊頂欄 Pokéball＋標題返回首頁。EV 算法不變
- **v1.19.1**：視覺 polish——奶油紙面／墨線／硬陰影、chase 卡格、寶可夢剪影點綴（御三家／伊布／百變怪／Sobble）；EV 算法不變
- **v1.19.0**：新增英文裸卡市況頁（`market-en.html`）＋首頁「EN 市況」連結；TCGCSV 靜態 JSON 由 GitHub Action 每日更新
- **v1.18.5**：修手機橫向溢出；系列改為可收起下拉（官方發行序）；桌面側欄 TOC 不變
- **v1.18.4**：手機頂欄收成一列（系列名＋有膜／無膜＋HC%）；盒價與進階假設收入「設定」；稀有度折扣直向排列；系列 TOC 維持橫向滑動
- **v1.18.3**：SSR 與復刻 S 預設折扣 20%（×0.80）；AR／UR／ACE 仍 50%
- **v1.18.2**：資料網址加 `?v=`，載入失敗會顯示抓取狀態同強制重新整理提示（避開瀏覽器留住壞掉的舊頁）
- **v1.18.1**：GitHub Pages 載入修復——`data.json`／`data.js` 外掛，唔再內嵌超大 `EV_DATA`（缺 `</script>` 會令整段 script 語法錯誤 →「找不到資料」）
- **v1.18**：日版盒保底／超配按系列重算（Pokegto 等）；SR／AR 用 min(PriceCharting 未評成交, SNKRDUNK Grade A)；落地盒分母與 haircut 折扣語意不變
- **v1.17**：`?set=M6` 深連結（選系列會 `replaceState`）；一鍵「複製給朋友」短訊；SV2a chase 名改官方繁中（台灣 SV2aF／訓練家網站）
- **v1.16**：稀有度折扣精簡——刪標籤旁重複「折扣 X%」，MUR／SAR 等與滑桿同行，右側只留 %；短註 10%＝×0.9
- **v1.15**：UI 精簡；「預期回報 %」；有膜／無膜盒價覆寫；「恢復預設」
- **v1.14**：系列名旁 PriceCharting + SNKRDUNK（有膜）連結
- **v1.13**：修復盒圖／chase lightbox＋補齊圖檔

## 注意

數字為模型估計，非投資建議；市價會變。
