/**
 * Job Radar → Google Sheet Webhook
 *
 * 安裝步驟(README 有完整說明):
 * 1. 開一個新的 Google Sheet
 * 2. 擴充功能 → Apps Script,貼上這整份程式碼
 * 3. 把下面的 TOKEN 改成你自己隨便打的一串密碼
 * 4. 部署 → 新增部署 → 類型「網頁應用程式」
 *    - 執行身分:我
 *    - 誰可以存取:任何人
 * 5. 複製部署後的網址(https://script.google.com/macros/s/.../exec)
 *    → 存進 GitHub repo 的 Secrets(SHEET_WEBHOOK_URL)
 *    → TOKEN 存進 Secrets(SHEET_TOKEN)
 */

const TOKEN = 'CHANGE_ME_到一串你自己的密碼';

function doPost(e) {
  const p = JSON.parse(e.postData.contents);
  if (p.token !== TOKEN) {
    return ContentService.createTextOutput('unauthorized');
  }

  const ss = SpreadsheetApp.getActiveSpreadsheet();

  // Jobs 分頁:每天整頁覆寫成最新的在架職缺
  overwriteTab(ss, 'Jobs', p.jobs_header, p.jobs);

  // Signals 分頁:累加(保留歷史訊號)
  appendTab(ss, 'Signals', p.signals_header, p.signals);

  return ContentService.createTextOutput('ok: ' + p.jobs.length + ' jobs');
}

function overwriteTab(ss, name, header, rows) {
  let sheet = ss.getSheetByName(name) || ss.insertSheet(name);
  sheet.clearContents();
  sheet.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold');
  if (rows.length > 0) {
    sheet.getRange(2, 1, rows.length, header.length).setValues(rows);
  }
  sheet.setFrozenRows(1);
}

function appendTab(ss, name, header, rows) {
  let sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
    sheet.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold');
    sheet.setFrozenRows(1);
  }
  if (rows.length > 0) {
    sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, header.length).setValues(rows);
  }
}
