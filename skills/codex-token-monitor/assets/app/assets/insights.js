'use strict';
window.FishInsights = (() => {
  const byId = id => document.getElementById(id);
  const moneyFormat = new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const rateKey = 'token-monitor-usd-cny';
  const validRate = value => Number.isFinite(value) && value >= .01 && value <= 1000;
  let exchangeRate = 7;
  try {
    const saved = Number(localStorage.getItem(rateKey));
    if (validRate(saved)) exchangeRate = saved;
  } catch {}
  byId('usd-cny').value = exchangeRate === 7 ? '7.00' : String(exchangeRate);
  byId('usd-cny').addEventListener('input',event => {
    const next = Number(event.target.value), valid = validRate(next);
    event.target.setAttribute('aria-invalid',String(!valid));
    byId('exchange-error').hidden = valid;
    if (!valid) return;
    exchangeRate = next;
    try { localStorage.setItem(rateKey,String(next)); } catch {}
    updateBasis();
    window.dispatchEvent(new Event('fish-cost-rate-change'));
  });
  function updateBasis() {
    byId('cost-basis').textContent = '按你的 20x 方案：每周 20 亿 Token × 4 周 = 80 亿 Token，费用 200 美元。'
      + '每百万 Token 约 0.025 美元，每亿 Token 约 ¥' + moneyFormat.format(2.5 * exchangeRate)
      + '。这是按设定总量平均分摊的成本；20 亿/周是自定估算口径，不代表官方固定配额。';
  }
  updateBasis();
  const selectedCost = (task, includeChildren) => includeChildren ? task.total_cost : task.self_cost;
  const validCost = cost => cost && Number.isFinite(cost.usd) && cost.usd >= 0;
  const incomplete = cost => Boolean(cost?.incomplete) || cost?.unpriced_responses > 0;
  const knownCost = cost => validCost(cost) && (cost.priced_responses > 0 || !incomplete(cost));
  const moneyAmount = usd => usd * exchangeRate > 0 && usd * exchangeRate < .01
    ? '¥ <0.01' : '¥ ' + moneyFormat.format(usd * exchangeRate);
  const moneyText = cost => !knownCost(cost) ? '—' : (incomplete(cost) ? '已确认部分约 ' : '约 ') + moneyAmount(cost.usd);
  function clearCost() {
    byId('cost-value').textContent = '—';
    byId('cost-value').removeAttribute('title');
    byId('cost-qualifier').textContent = '订阅摊算';
  }
  function renderCost(snapshot, tasks, includeChildren) {
    const total = {usd:0,priced_responses:0,incomplete:Boolean(snapshot.partial)};
    for (const task of tasks) {
      const cost = selectedCost(task,includeChildren);
      if (!validCost(cost)) { total.incomplete = true; continue; }
      total.usd += cost.usd;
      total.priced_responses += cost.priced_responses || 0;
      total.incomplete ||= incomplete(cost);
    }
    const known = tasks.length > 0 && knownCost(total);
    byId('cost-qualifier').textContent = total.incomplete ? '已确认部分摊算' : '订阅摊算 · 约';
    byId('cost-value').textContent = known ? moneyAmount(total.usd) : '—';
    if (known) byId('cost-value').title = '20x 方案：Token × 200 美元 ÷ 80 亿 × 汇率 ' + exchangeRate
      + (includeChildren ? ' · 包含子任务' : ' · 仅任务本体')
      + (total.incomplete ? ' · 仅按已确认记录计算' : '');
    else byId('cost-value').removeAttribute('title');
  }

  let account = null, accountBusy = false, accountTimer = null, pollStarted = 0;
  const timestamp = value => {
    if (value === null || value === undefined || value === '') return null;
    const date = new Date(typeof value === 'number' ? value * 1000 : value);
    return Number.isFinite(date.getTime()) ? date : null;
  };
  function countdown(reset, now) {
    const minutes = Math.max(1,Math.ceil((reset.getTime() - now) / 60000));
    const days = Math.floor(minutes / 1440), hours = Math.floor(minutes % 1440 / 60), remainder = minutes % 60;
    return (days ? days + ' 天 ' : '') + (hours ? hours + ' 小时 ' : '')
      + (remainder || !days && !hours ? remainder + ' 分钟' : '') + '后';
  }
  function renderAccount() {
    const now = Date.now(), fetched = timestamp(account?.fetched_at);
    const fresh = account?.status === 'available' && fetched
      && fetched.getTime() <= now + 60000 && now - fetched.getTime() <= 15 * 60000;
    // The user's 20x estimate is weekly. Never substitute a Spark or reserve bucket.
    const windows = (Array.isArray(account?.windows) ? account.windows : []).filter(window => window.bucket === 'codex');
    const weekly = windows.find(window => window.window_minutes === 10080);
    const window = weekly || windows.find(window => {
      const reset = timestamp(window.resets_at);
      return reset && reset.getTime() > now;
    });
    const reset = timestamp(window?.resets_at), future = reset && reset.getTime() > now;
    const status = byId('account-status');
    status.textContent = weekly ? 'Codex · 周额度' : 'Codex · 额度窗口';
    status.dataset.state = fresh && future ? 'available' : 'unavailable';
    byId('account-reset-time').textContent = fresh && future
      ? reset.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '等待最新时间';
    byId('account-countdown').textContent = fresh && future ? countdown(reset,now)
      : account?.refreshing ? '正在读取官方重置时间…' : fetched && !fresh ? '快照已过期，请刷新' : '暂未获得下轮重置时间';
    byId('account-source').textContent = fetched
      ? '按官方计划 · 更新于 ' + fetched.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})
      : '以官方返回的重置时间为准';
    const error = account?.refresh_error || account?.error;
    byId('account-error').hidden = !error;
    byId('account-error').textContent = error ? String(error) : '';
  }
  async function requestJson(url, options = {}) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(),12000);
    try {
      const response = await fetch(url,{...options,cache:'no-store',signal:controller.signal});
      if (!response.ok) throw new Error('暂时无法读取账户重置时间，请稍后重试。');
      return await response.json();
    } finally { clearTimeout(timeout); }
  }
  async function loadAccount(force = false) {
    if (accountBusy) return;
    clearTimeout(accountTimer); accountTimer = null; accountBusy = true; byId('account-refresh').disabled = true;
    try {
      let next;
      if (force) {
        const setup = await requestJson('/api/setup');
        if (!setup.csrf_token) throw new Error('当前服务暂不支持刷新账户重置时间。');
        next = await requestJson('/api/account/refresh',{method:'POST',headers:{'Content-Type':'application/json','X-Monitor-Token':setup.csrf_token},body:'{}'});
      } else next = await requestJson('/api/account');
      if (!next || !['available','stale','unavailable'].includes(next.status) || !Array.isArray(next.windows)) throw new Error('暂时无法识别重置信息，请刷新后重试。');
      account = next;
      renderAccount();
      if (next.refreshing) {
        if (!pollStarted) pollStarted = Date.now();
        if (Date.now() - pollStarted < 20000) accountTimer = setTimeout(() => loadAccount(),1000);
      } else pollStarted = 0;
    } catch (error) {
      if (!account) account = {status:'unavailable',windows:[]};
      account.refreshing = false;
      account.refresh_error = error.name === 'AbortError' ? '读取超时，请稍后刷新。' : error instanceof TypeError ? '无法连接监控器，请重新打开页面。' : error.message;
      renderAccount(); pollStarted = 0;
    } finally { accountBusy = false; byId('account-refresh').disabled = false; }
  }
  byId('account-refresh').addEventListener('click',() => { pollStarted = 0; loadAccount(true); });
  setInterval(() => {
    if (account) renderAccount();
    if (!document.hidden && byId('pause').getAttribute('aria-pressed') !== 'true' && !accountTimer) loadAccount();
  },60000);
  loadAccount();
  return {selectedCost,moneyText,renderCost,clearCost};
})();
