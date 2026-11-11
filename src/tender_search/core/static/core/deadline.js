// One timer for all tables/cards. Anchor to server time, not the PC's clock.
(() => {
  const clocks = new WeakMap();
  const pad = value => String(value).padStart(2, '0');
  function update() {
    const tick = performance.now();
    for (const element of document.querySelectorAll('[data-deadline][data-deadline-now]')) {
      let clock = clocks.get(element);
      if (!clock) {
        const end = Date.parse(element.dataset.deadline);
        const server = Date.parse(element.dataset.deadlineNow);
        if (!Number.isFinite(end) || !Number.isFinite(server)) continue;
        clock = {remaining: end - server, started: tick};
        clocks.set(element, clock);
      }
      const seconds = Math.max(0, Math.ceil((clock.remaining - (tick - clock.started)) / 1000));
      const days = Math.floor(seconds / 86400);
      const hours = Math.floor(seconds % 86400 / 3600);
      const minutes = Math.floor(seconds % 3600 / 60);
      const text = seconds === 0 ? 'Приём завершён' :
        `Осталось: ${days ? days + ' д ' : ''}${pad(hours)}:${pad(minutes)}:${pad(seconds % 60)}`;
      if (element.textContent !== text) element.textContent = text;
      element.classList.toggle('deadline-remaining--urgent', seconds > 0 && seconds <= 86400);
    }
  }
  update();
  document.addEventListener('htmx:afterSwap', update);
  document.addEventListener('visibilitychange', update);
  setInterval(() => { if (!document.hidden) update(); }, 1000);
})();
