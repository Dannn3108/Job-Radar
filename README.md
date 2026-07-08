# Job Radar 🛰️

每日自動掃描 104 目標職能職缺,偵測「公司招募加速(WoW)」與「值得追蹤的新公司」,結果同步到 Google Sheet。全程跑在 GitHub Actions,不需要本機環境。

## 架構

```
GitHub Actions(每日 09:00 台北時間)
  → 抓 104 search API(依 config.yaml 關鍵字組)
  → SQLite 入庫(data/jobs.db,每日 commit 回 repo = 免費雲端儲存+歷史版本)
  → 計算訊號:WoW 招募加速 / 新公司發現
  → 產出 markdown digest(data/digests/)
  → 推送 Google Sheet(Jobs / Signals 兩個分頁)
```

## Setup(一次性,約 15 分鐘,全程網頁操作)

### Step 1:建立 GitHub Repo
1. GitHub → New repository → 命名 `job-radar` → **Private** → Create
2. 進 repo → `Add file` → `Upload files` → 把這整包解壓縮後全部拖進去 → Commit
   - ⚠️ 注意 `.github/workflows/daily.yml` 這個隱藏資料夾也要上傳成功(上傳後在 repo 的 Actions 分頁應該會看到 "Daily Job Radar")

### Step 2:設定 Google Sheet
1. 開一個新的 Google Sheet(名稱隨意)
2. 上方選單 `擴充功能` → `Apps Script`
3. 把 `sheets/apps_script.gs` 的內容整份貼上,**把第一行的 TOKEN 改成你自己的密碼**
4. 右上 `部署` → `新增部署` → 齒輪選 `網頁應用程式`
   - 執行身分:**我**
   - 誰可以存取:**任何人**(資料寫入受 TOKEN 保護)
5. 複製部署網址(`https://script.google.com/macros/s/.../exec`)

### Step 3:設定 GitHub Secrets
Repo → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`,新增兩個:

| Name | Value |
|---|---|
| `SHEET_WEBHOOK_URL` | Step 2 複製的部署網址 |
| `SHEET_TOKEN` | 你在 apps_script.gs 裡設的密碼 |

### Step 4:手動觸發第一次執行(驗證)
Repo → `Actions` → `Daily Job Radar` → `Run workflow`

- 綠色勾勾 → 打開你的 Google Sheet,應該會看到 `Jobs` 分頁有資料
- 紅色叉叉 → 點進去看 log,把錯誤訊息貼給 Claude 修

之後每天台北時間 09:00 左右會自動執行,不用管它。

## 日常使用

- **Google Sheet `Jobs` 分頁**:所有在架職缺,每天整頁刷新 → **不要手動編輯這頁**,想追蹤的職缺複製到自己的分頁
- **`Signals` 分頁**:累積的訊號歷史(招募加速 ⚡ / 新公司建議)
- **改搜尋條件**:直接在 GitHub 網頁上編輯 `config.yaml`(關鍵字、地區、排除字、觸發門檻),commit 後隔天生效

## 已知事項

- **WoW 需要至少兩週資料才有意義** — 系統以自己記錄的 `first_seen` 建時間序列,前期訊號少是正常的,越早開始跑越好
- 104 API 是半公開的,欄位若改版導致解析失敗,`data/debug/sample_response.json` 會留存原始回應,丟給 Claude 即可修
- 若 GitHub 機房 IP 被 104 擋(log 出現 403),備案:降低頻率 / 換執行時間 / 改跑本機

## Roadmap

- [ ] Phase 2:CakeResume / Yourator fetcher、LinkedIn Job Alert email 解析
- [ ] Phase 2:目標公司官網 ATS polling(watchlist 深度追蹤)
- [ ] Phase 3:Claude API 自動評分(JD vs 履歷 fit score)+ 公司研究報告
- [ ] Phase 3:pipeline 進度追蹤整合(backlog → 投遞 → 面試 → offer)
