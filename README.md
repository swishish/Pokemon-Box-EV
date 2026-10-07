# Pokemon Box EV

日盒 EV 小算盤（朋友向）· **v1.18.2**

給「不太懂金融／AI」的買卡朋友看的單頁工具：選系列 → 看 **四個預期回報 %**（同一分母）→ 有膜／無膜切換 → 看主力 chase 卡與「買單卡 vs 開盒」粗略提示。

資料來自靜態 snapshot（`ev_live`），**無登入、無即時爬價**。

## 線上使用（GitHub Pages）

開啟：**https://swishish.github.io/Pokemon-Box-EV/**

公開 repo 的 GitHub Pages **免費**，無需付費。

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
