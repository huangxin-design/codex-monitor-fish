const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname,
  '../skills/codex-token-monitor/assets/app/assets/insights.js'), 'utf8');
const now = Date.parse('2026-09-12T10:00:00Z');
const window = (bucket, minutes, hours) => ({
  bucket, window_minutes: minutes, resets_at: hours === null ? null : now / 1000 + hours * 3600,
});
const account = (windows, extra = {}) => ({
  status: 'available', fetched_at: new Date(now - 60000).toISOString(), windows, ...extra,
});

async function mount(snapshot) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', value: '', hidden: false, disabled: false, dataset: {},
      attributes: {}, listeners: {},
      addEventListener(name, callback) { this.listeners[name] = callback; },
      setAttribute(name, value) { this.attributes[name] = value; },
      getAttribute(name) { return this.attributes[name] ?? null; },
      removeAttribute(name) { delete this.attributes[name]; delete this[name]; },
    });
    return elements.get(id);
  };
  class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const context = {
    window: {dispatchEvent() {}}, document: {getElementById: element, hidden: false},
    Date: FixedDate, Intl, Event, AbortController,
    localStorage: {getItem() { return null; }, setItem() {}},
    setTimeout() { return 1; }, clearTimeout() {}, setInterval() { return 1; },
    fetch: async () => ({ok: true, json: async () => snapshot}),
  };
  vm.runInNewContext(source, context, {filename: 'insights.js'});
  await new Promise(setImmediate);
  return {element, insights: context.window.FishInsights};
}

function taskRenderer(ui) {
  const html = fs.readFileSync(path.join(__dirname,
    '../skills/codex-token-monitor/assets/app/index.html'), 'utf8');
  const createElement = tag => ({
    tagName: tag, className: '', children: [], dataset: {},
    get textContent() {
      return this.children.map(child => typeof child === 'string' ? child : child.textContent).join('');
    },
    set textContent(value) { this.children = [String(value)]; },
    append(...nodes) { this.children.push(...nodes); },
    prepend(...nodes) { this.children.unshift(...nodes); },
    setAttribute() {}, addEventListener() {},
    set innerHTML(value) { throw new Error('Task content must be rendered as text'); },
  });
  const helpers = html.slice(html.indexOf('const $ ='), html.indexOf('const hero ='));
  const renderers = html.slice(html.indexOf('function totalsFor('), html.indexOf('function render('));
  return vm.runInNewContext(helpers + renderers + '\n({childBreakdown, taskRows})', {
    document: {getElementById: ui.element, createElement,
      createElementNS(namespace, tag) { return createElement(tag); }},
    FishInsights: ui.insights, Intl,
  }, {filename: 'index.html'});
}

function nodesWithClass(node, className) {
  if (typeof node === 'string') return [];
  return (node.className.split(' ').includes(className) ? [node] : [])
    .concat(node.children.flatMap(child => nodesWithClass(child, className)));
}

test('the Codex weekly window wins over earlier Spark and short-window resets', async () => {
  const ui = await mount(account([
    window('codex_bengalfox', 300, 1), window('codex', 300, 2), window('codex', 10080, 54),
  ]));
  assert.equal(ui.element('account-status').textContent, 'Codex · 周额度');
  assert.equal(ui.element('account-status').dataset.state, 'available');
  assert.equal(ui.element('account-countdown').textContent, '2 天 6 小时 后');
});

test('Spark and reserve windows cannot substitute for a missing main window', async () => {
  const ui = await mount(account([
    window('codex_bengalfox', 10080, 1), window('base_model_inference', 300, 2),
  ]));
  assert.equal(ui.element('account-status').dataset.state, 'unavailable');
  assert.equal(ui.element('account-reset-time').textContent, '等待最新时间');
  assert.equal(ui.element('account-countdown').textContent, '暂未获得下轮重置时间');
});

test('stale, expired, unknown and clock-skewed resets never become predictions', async () => {
  const cases = [
    account([window('codex', 10080, 2)], {status: 'stale'}),
    account([window('codex', 10080, 2)], {fetched_at: new Date(now - 16 * 60000).toISOString()}),
    account([window('codex', 10080, -1), window('codex', 300, 2)]),
    account([window('codex', 10080, null)]),
    account([{...window('codex', 10080, 2), resets_at: 'not a timestamp'}]),
    account([window('codex', 10080, 2)], {fetched_at: new Date(now + 2 * 60000).toISOString()}),
  ];
  for (const snapshot of cases) {
    const ui = await mount(snapshot);
    assert.equal(ui.element('account-status').dataset.state, 'unavailable');
    assert.equal(ui.element('account-reset-time').textContent, '等待最新时间');
    assert.doesNotMatch(ui.element('account-countdown').textContent, /后$/);
  }
});

test('a fresh valid main reset displays its real timestamp and countdown', async () => {
  const ui = await mount(account([window('codex', 10080, 2)]));
  assert.equal(ui.element('account-countdown').textContent, '2 小时 后');
  assert.equal(ui.element('account-reset-time').textContent,
    new Date(now + 2 * 3600000).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    }));
});

test('the uniform subscription cost uses the selected FX and retains it after invalid input', async () => {
  const ui = await mount(account([]));
  const cost = {usd: 2.5, priced_responses: 1};
  assert.equal(ui.insights.moneyText(cost), '约 ¥ 17.50');
  const input = ui.element('usd-cny');
  input.value = '8'; input.listeners.input({target: input});
  assert.equal(ui.insights.moneyText(cost), '约 ¥ 20.00');
  for (const value of ['', '0', '-1', 'Infinity', '1001', 'invalid']) {
    input.value = value; input.listeners.input({target: input});
    assert.equal(ui.insights.moneyText(cost), '约 ¥ 20.00');
    assert.equal(input.getAttribute('aria-invalid'), 'true');
    assert.equal(ui.element('exchange-error').hidden, false);
  }
});

test('partial data without any confirmed cost displays unknown, not zero', async () => {
  const ui = await mount(account([]));
  for (const tasks of [[{}], [{total_cost: {usd: 0, priced_responses: 0, incomplete: true}}]]) {
    ui.insights.renderCost({partial: true}, tasks, true);
    assert.equal(ui.element('cost-value').textContent, '—');
  }
  assert.equal(ui.insights.moneyText({usd: 0, priced_responses: 0, incomplete: true}), '—');
});

test('task details show each child with literal titles and the current RMB conversion', async () => {
  const ui = await mount(account([]));
  const renderer = taskRenderer(ui);
  const child = {id: 'child', title: '<img src=x onerror=alert(1)>', model: 'gpt-5.5',
    usage: {total_tokens: 100000000}, cost: {usd: 2.5, priced_responses: 1}};
  const task = {id: 'parent', child_count: 1, child_details: [child]};
  const details = () => renderer.taskRows(task, 0)[1];
  const section = nodesWithClass(details(), 'child-breakdown')[0];
  assert.ok(section);
  assert.equal(section.children[0].textContent, '子任务明细');
  assert.equal(nodesWithClass(section, 'child-note')[0].textContent,
    '每项仅统计该子任务本体；更深层子任务单独列出。');
  assert.equal(nodesWithClass(section, 'child-item').length, 1);
  assert.equal(nodesWithClass(section, 'child-title')[0].textContent, child.title);
  assert.equal(nodesWithClass(section, 'child-model')[0].textContent, child.model);
  assert.equal(nodesWithClass(section, 'child-money')[0].textContent, '约 ¥ 17.50');
  const input = ui.element('usd-cny');
  input.value = '8'; input.listeners.input({target: input});
  assert.equal(nodesWithClass(details(), 'child-money')[0].textContent, '约 ¥ 20.00');
  for (const child_details of [undefined, []]) {
    const emptyDetails = renderer.taskRows({...task, child_details}, 0)[1];
    assert.equal(nodesWithClass(emptyDetails, 'child-breakdown').length, 0);
  }
});

test('child RMB values retain unknown, partial and sub-cent cost distinctions', async () => {
  const renderer = taskRenderer(await mount(account([])));
  const costs = [
    null,
    {usd: 0, priced_responses: 0, incomplete: true},
    {usd: 1, priced_responses: 1, unpriced_responses: 1},
    {usd: 0.0001, priced_responses: 1},
    {usd: 0, priced_responses: 1},
  ];
  const section = renderer.childBreakdown(costs.map((cost, index) => ({
    id: String(index), title: '子任务 ' + index, usage: {}, cost,
  })));
  assert.deepEqual(nodesWithClass(section, 'child-money').map(node => node.textContent), [
    '—', '—', '已确认部分约 ¥ 7.00', '约 ¥ <0.01', '约 ¥ 0.00',
  ]);
});

test('child token values follow the selected unit and preserve incomplete usage markers', async () => {
  const ui = await mount(account([]));
  const renderer = taskRenderer(ui);
  const children = [
    {id: 'complete', usage: {total_tokens: 100000000}},
    {id: 'partial', usage: {total_tokens: 200000000}, unavailable: true},
    {id: 'unknown', usage: {}, unavailable: true},
  ];
  const tokens = () => nodesWithClass(renderer.childBreakdown(children), 'child-tokens')
    .map(node => node.textContent);
  ui.element('unit').value = 'yi';
  assert.deepEqual(tokens(), ['1 亿 Token', '≥ 2 亿 Token', '— Token']);
  ui.element('unit').value = 'raw';
  assert.deepEqual(tokens(), ['100,000,000 Token', '≥ 200,000,000 Token', '— Token']);
});
