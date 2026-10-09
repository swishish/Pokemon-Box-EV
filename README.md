# Pokemon Box EV

日盒 EV 小算盤（朋友向）· **v1.20.0**

給「不太懂金融／AI」的買卡朋友看的單頁工具：選系列 → 看 **四個預期回報 %**（同一分母）→ 有膜／無膜切換 → 看主力 chase 卡與「買單卡 vs 開盒」粗略提示。

資料來自靜態 snapshot（`ev_live`），**無登入、無即時爬價**。

## 線上使用（GitHub Pages）

開啟：**https://swishish.github.io/Pokemon-Box-EV/**

日語熱度（SNKRDUNK 成交＋流動性）：**https://swishish.github.io/Pokemon-Box-EV/market-jp.html**

英文裸卡市況（S&P-style pulse）：**https://swishish.github.io/Pokemon-Box-EV/market-en.html**

公開 repo 的 GitHub Pages **免費**，無需付費。

## JP market heat（Phase 2）

獨立頁 `market-jp.html`：日版二次市場溫度（SNKRDUNK-primary），UI 跟 EN 市況同一套 chrome。綜合熱度 = 價格動能＋廣度＋流動性；指數是本站 chase 卡價格加權 Top 100（基準 1,000）。

**誠實範圍：** SNKRDUNK **沒有穩定公開 API**。本頁不爬 `snkrdunk.com`、不用 TCGCSV／TCGPlayer JP、也不打 PriceCharting 付費 API。

| 顯示 | 說明 |
| --- | --- |
| Index + % | 相對**上一份已提交快照**。第一次建檔只有指數 1,000，沒有每日 % |
| 綜合熱度 | 有歷史動能／廣度，或 inbox 帶流動性時才有 0–100 分數（熱／偏熱／平／偏冷／冷） |
| Breadth | 籃子裡上漲 vs 下跌張數（有歷史才有） |
| Top gainers / losers | 同一份快照對比 |
| 流動性備註 | 掛單／近週成交；沒匯入就顯示空狀態與更新方法 |
| Disclaimer | 日版二次市場、SNKRDUNK-based、非 EN TCGCSV、非正式投資建議 |

### 資料怎麼更新

1. **預設（已提交的第一份快照）**：從 Box-EV `data.json` 讀本站已整理的 **SNKRDUNK Grade A 掛價**（不是即時成交）。這是允許的靜態來源——數字已在 repo 裡。
2. **成交＋流動性（Gary 之後可補）**：把半結構化 JSON 放到 `data/market-jp/inbox.json`（格式見 `data/market-jp/inbox.example.json`），再跑腳本。inbox 會 overlay 到同一張卡上：有 `soldPriceYen`／`成交` 就改用成交價；`listings`／`掛單`、`sales7d`／`近週成交` 進入流動性。
3. GitHub Action **Update JP market heat**（`.github/workflows/update-market-jp.yml`）
   - **沒有每日 cron**（沒有公開 SNKRDUNK dump 可排程）
   - **手動**：Actions 頁按 **Run workflow**（`auto` 或 `force`）
   - **PR**：只跑 `python3 tools/refresh_market_jp.py --self-test`（不 commit）
4. 腳本 `python3 tools/refresh_market_jp.py` 寫入：
   - `data/market-jp/latest.json`
   - `data/market-jp/history.json`
   - `data/market-jp/latest.js`（`file://` / 快取備援，同 `data.js` 慣例）

本機重跑：

```bash
python3 tools/refresh_market_jp.py --self-test
python3 tools/refresh_market_jp.py -v --force          # Box-EV Grade A 籃子；有 inbox.json 就 overlay
python3 tools/refresh_market_jp.py --ingest-only --inbox data/market-jp/inbox.example.json -v --force
```

`inbox.example.json` 裡的成交／掛單是 **schema 範例**，不是即時盤；不要把它當成真行情。

未來若有官方／授權 connector，只要吐出同一份 inbox JSON 即可，不必改頁面。

## EN market pulse（Phase 1）

獨立頁 `market-en.html`：英文 **raw singles** 市場溫度（價格加權 Top 500，基準 1,000），資料是靜態 JSON，不經瀏覽器打 TCGPlayer。

| 顯示 | 說明 |
| --- | --- |
| Index + % | 相對**上一份已提交快照**。第一次建檔只有指數 1,000，沒有每日 % |
| Breadth | 籃子裡上漲 vs 下跌張數（有歷史才有） |
| Top gainers / losers | 同一份快照對比 |
| Disclaimer | TCGPlayer Market via TCGCSV；僅 EN raw；非 JP／SNKRDUNK；非正式投資建議 |

### 資料怎麼更新

來源是免費 [TCGCSV](https://tcgcsv.com) 每日 Pokemon category dump（category 3＝英文），**不需要 TCGPlayer API key**，也不爬 SNKRDUNK／PriceCharting HTML。

1. GitHub Action **Update EN market pulse**（`.github/workflows/update-market-en.yml`）
   - **排程**：每天 21:23 UTC（TCGCSV 約 20:05 UTC 刷新之後）
   - **手動**：Actions 頁按 **Run workflow**
   - **PR**：只跑 `python3 tools/refresh_market_en.py --self-test`（不抓 dump、不 commit）
2. 腳本 `python3 tools/refresh_market_en.py` 先讀 `https://tcgcsv.com/last-updated.txt`；stamp 沒變就結束（零流量）。
3. 有新 dump 才抓 `Groups.csv` + 各系列 `ProductsAndPrices.csv`，寫入：
   - `data/market-en/latest.json`（指數、廣度、漲跌榜、500 張成分——給下一次對比用）
   - `data/market-en/history.json`
   - `data/market-en/latest.js`（`file://` / 快取備援，同 `data.js` 慣例）
4. Action 把變更 commit 回預設分支；GitHub Pages 仍是純靜態。

本機重跑：

```bash
python3 tools/refresh_market_en.py --self-test
python3 tools/refresh_market_en.py -v --force
```

## 本機打開

1. 下載本 repo
2. 雙擊 **`index.html`**（或用瀏覽器 File → Open）
3. 用本機 HTTP 開（見下）：頁面 **`fetch('./data.json')`**，失敗則讀同資料夾 **`data.js`**。不要再把整份 JSON 內嵌進 `index.html` 的 `<script>`（`</script>`／相鄰 `<script>` 會令 `EV_DATA` 派唔到）

或：

```bash
python3 -m http.server 8765
```

瀏覽器開：`http://127.0.0.1:8765/`

## Changelog（摘要）

- **v1.20.0**：新增日語熱度頁（`market-jp.html`）＋首頁／EN 頁「日語熱度」連結；SNKRDUNK-primary 靜態 JSON（Box-EV Grade A 籃子＋可選 `inbox.json` overlay），不爬站、無付費 API
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
