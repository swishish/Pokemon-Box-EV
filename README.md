# Pokemon Box EV

日盒 EV 小算盤（朋友向）· **v1.15**

給「不太懂金融／AI」的買卡朋友看的單頁工具：選系列 → 看 **四個預期回報 %**（同一分母）→ 有膜／無膜切換 → 看主力 chase 卡與「買單卡 vs 開盒」粗略提示。

資料來自靜態 snapshot（`ev_live`），**無登入、無即時爬價**。

## 線上使用（GitHub Pages）

開啟：**https://swishish.github.io/Pokemon-Box-EV/**

公開 repo 的 GitHub Pages **免費**，無需付費。

## 本機打開

1. 下載本 repo
2. 雙擊 **`index.html`**（或用瀏覽器 File → Open）
3. `index.html` 已內嵌 snapshot，單獨一個檔也能開；同資料夾的 `data.js` / `data.json` 方便之後更新資料

或：

```bash
python3 -m http.server 8765
```

瀏覽器開：`http://127.0.0.1:8765/`

## Changelog（摘要）

- **v1.15**：UI 精簡；「預期回報 %」；有膜／無膜盒價覆寫；「恢復預設」
- **v1.14**：系列名旁 PriceCharting + SNKRDUNK（有膜）連結
- **v1.13**：修復盒圖／chase lightbox＋補齊圖檔

## 注意

數字為模型估計，非投資建議；市價會變。
