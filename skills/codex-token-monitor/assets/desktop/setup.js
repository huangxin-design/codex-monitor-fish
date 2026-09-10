(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let csrfToken = '', sourceHome = '', busy = false;

  function showError(message) {
    $('setup-error').textContent = message || '';
    $('setup-error').hidden = !message;
  }

  function updateControls() {
    $('start-monitor').disabled = busy || !csrfToken || !document.querySelector('input[name="project"]:checked');
    $('rediscover').disabled = busy;
    $('codex-home').disabled = busy;
    $('project-options').disabled = busy;
  }

  async function request(url, options) {
    const response = await fetch(url, {cache: 'no-store', ...options});
    let body;
    try { body = await response.json(); }
    catch { throw new Error('监控器暂时没有响应，请重新打开应用后再试。'); }
    if (!response.ok) throw new Error(body.error || '没有完成操作，请稍后重试。');
    return body;
  }

  async function discover(customHome) {
    busy = true;
    showError('');
    $('projects').replaceChildren();
    $('project-message').hidden = false;
    $('project-message').textContent = '正在查找你用过的项目…';
    updateControls();
    try {
      if (customHome !== undefined && !csrfToken) {
        csrfToken = (await request('/api/setup')).csrf_token || '';
      }
      const data = customHome === undefined ? await request('/api/setup') : await request('/api/discover', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Monitor-Token': csrfToken},
        body: JSON.stringify({codex_home: customHome})
      });
      csrfToken = data.csrf_token || '';
      sourceHome = data.codex_home || '';
      $('codex-home').value = sourceHome;
      const projects = Array.isArray(data.projects) ? data.projects : [];
      const selected = projects.some(project => project.directory === data.project_directory)
        ? data.project_directory : projects[0]?.directory;
      for (const project of projects) {
        const option = document.createElement('label');
        option.className = 'project-option';
        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'project';
        radio.value = project.directory;
        radio.checked = project.directory === selected;
        radio.addEventListener('change', updateControls);
        const copy = document.createElement('span');
        copy.className = 'project-copy';
        const name = document.createElement('span');
        name.className = 'project-name';
        name.textContent = project.name || project.directory;
        const path = document.createElement('span');
        path.className = 'project-path';
        path.textContent = project.directory;
        copy.append(name, path);
        const count = document.createElement('span');
        count.className = 'project-count';
        count.textContent = `${Number(project.task_count || 0).toLocaleString('zh-CN')} 个任务`;
        option.append(radio, copy, count);
        $('projects').append(option);
      }
      $('project-message').hidden = projects.length > 0;
      $('project-message').textContent = projects.length
        ? `找到 ${projects.length} 个项目，请选择一个。`
        : '还没有找到项目。开始一次 Codex 任务后，点击“重新查找”。';
      if (!projects.length) $('advanced').open = true;
      if (data.error) showError(data.error);
    } catch (error) {
      $('project-message').textContent = '暂时无法读取项目，请检查使用记录目录后重新查找。';
      $('advanced').open = true;
      showError(error instanceof TypeError ? '无法连接监控器，请重新打开应用后再试。' : error.message);
    } finally {
      busy = false;
      updateControls();
    }
  }

  $('rediscover').addEventListener('click', () => discover($('codex-home').value.trim()));
  $('codex-home').addEventListener('keydown', event => {
    if (event.key === 'Enter' && !busy) {
      event.preventDefault();
      discover($('codex-home').value.trim());
    }
  });
  $('setup-form').addEventListener('submit', async event => {
    event.preventDefault();
    const selected = document.querySelector('input[name="project"]:checked');
    if (busy || !selected || !csrfToken) return;
    busy = true;
    showError('');
    $('start-label').textContent = '正在打开…';
    updateControls();
    try {
      await request('/api/setup', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Monitor-Token': csrfToken},
        body: JSON.stringify({project_directory: selected.value, codex_home: sourceHome})
      });
      location.assign('/');
    } catch (error) {
      showError(error instanceof TypeError ? '无法连接监控器，请重新打开应用后再试。' : error.message);
      busy = false;
      $('start-label').textContent = '开始监控';
      updateControls();
    }
  });
  discover();
})();
