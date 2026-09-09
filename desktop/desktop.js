(() => {
  'use strict';
  const actions = document.querySelector('.hero-actions');
  if (!actions) return;
  const switchProject = document.createElement('a');
  switchProject.className = 'button';
  switchProject.href = '/setup';
  switchProject.textContent = '切换项目';
  switchProject.style.color = 'inherit';
  switchProject.style.textDecoration = 'none';
  actions.append(switchProject);

  const quit = document.createElement('button');
  quit.type = 'button';
  quit.className = 'button';
  quit.id = 'quit-monitor';
  quit.textContent = '退出监控';
  actions.append(quit);
  quit.addEventListener('click', async () => {
    quit.disabled = true;
    quit.textContent = '正在退出…';
    const pause = document.getElementById('pause');
    const refresh = document.getElementById('refresh');
    if (pause && pause.getAttribute('aria-pressed') !== 'true') pause.click();
    try {
      // Let any refresh already in progress settle before shutting its server down.
      for (let attempt = 0; refresh?.disabled && attempt < 65; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 200));
      }
      if (refresh?.disabled) throw new Error('当前刷新尚未结束，请稍后再点一次退出。');
      const setup = await fetch('/api/setup', {cache: 'no-store'});
      const state = await setup.json();
      if (!setup.ok || !state.csrf_token) throw new Error(state.error || '没有连接上监控器，请稍后再试。');
      const response = await fetch('/api/quit', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Monitor-Token': state.csrf_token},
        body: '{}'
      });
      if (!response.ok) {
        const problem = await response.json();
        throw new Error(problem.error || '没有完成退出，请稍后再试。');
      }
      if (pause) pause.disabled = true;
      if (refresh) refresh.disabled = true;
      switchProject.removeAttribute('href');
      switchProject.setAttribute('aria-disabled', 'true');
      switchProject.style.opacity = '.5';
      quit.textContent = '已退出';
      document.getElementById('status-text').textContent = '监控已退出，下次双击程序即可重新打开';
      document.getElementById('status').dataset.state = 'paused';
    } catch (error) {
      quit.disabled = false;
      quit.textContent = '退出监控';
      document.getElementById('status-text').textContent = error instanceof TypeError
        ? '无法连接监控器。当前刷新已暂停，可以重新打开应用。' : error.message;
      document.getElementById('status').dataset.state = 'error';
    }
  });
})();
