const drawers = new WeakMap();
const SNAP = 30;

function mountDrawer(el, options) {
  const screen = matchMedia('(max-width: 720px)');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const events = new AbortController();
  let config = options;
  let position = config.open ? 0 : 1;
  let velocity = 0;
  let target = position;
  let previousTime = 0;
  let frame = 0;
  let gesture;

  const enabled = () => !config.mobileOnly || screen.matches;
  const bounded = value => Math.min(1, Math.max(0, value));

  function paint() {
    el.style.transform = reduced.matches ? 'none' : `translateX(${config.side * bounded(position) * 100}%)`;
    el.style.visibility = position >= 1 && target === 1 ? 'hidden' : 'visible';
  }

  function tick(time) {
    const dt = (time - previousTime) / 1000;
    const offset = position - target;
    const coefficient = velocity + SNAP * offset;
    const decay = Math.exp(-SNAP * dt);
    position = target + (offset + coefficient * dt) * decay;
    velocity = (velocity - SNAP * coefficient * dt) * decay;
    if (Math.abs(position - target) < 0.003 && Math.abs(velocity) < 0.065) {
      position = target;
      velocity = 0;
      frame = 0;
      paint();
      return;
    }
    previousTime = time;
    paint();
    frame = requestAnimationFrame(tick);
  }

  function settle() {
    cancelAnimationFrame(frame);
    frame = 0;
    target = config.open ? 0 : 1;
    el.inert = enabled() && !config.open;
    if (!enabled()) {
      el.style.removeProperty('transform');
      el.style.removeProperty('visibility');
      position = target;
      velocity = 0;
      return;
    }
    if (reduced.matches || position === target && !velocity) {
      position = target;
      velocity = 0;
      paint();
      return;
    }
    previousTime = performance.now();
    paint();
    frame = requestAnimationFrame(tick);
  }

  el.addEventListener('pointerdown', e => {
    if (config.open && enabled() && e.pointerType === 'touch' && e.isPrimary) {
      gesture = { id: e.pointerId, x: e.clientX, y: e.clientY, dragging: false };
    }
  }, { signal: events.signal });

  el.addEventListener('pointermove', e => {
    if (!gesture || gesture.id !== e.pointerId) return;
    const dx = (e.clientX - gesture.x) * config.side;
    if (!gesture.dragging) {
      const dy = Math.abs(e.clientY - gesture.y);
      if (Math.max(Math.abs(dx), dy) < 8) return;
      if (dy >= Math.abs(dx) || dx < 0) {
        gesture = undefined;
        return;
      }
      cancelAnimationFrame(frame);
      frame = 0;
      el.setPointerCapture(e.pointerId);
      Object.assign(gesture, { dragging: true, width: el.offsetWidth, origin: position, time: e.timeStamp });
    }
    const next = bounded(gesture.origin + dx / gesture.width);
    const dt = (e.timeStamp - gesture.time) / 1000;
    if (dt > 0) velocity = (next - position) / dt;
    position = next;
    gesture.time = e.timeStamp;
    paint();
  }, { signal: events.signal });

  function endGesture(e) {
    if (!gesture || gesture.id !== e.pointerId) return;
    if (e.type === 'lostpointercapture' && e.target !== el) return;
    const drag = gesture;
    gesture = undefined;
    if (!drag.dragging) return;
    if (e.type !== 'pointerup' || e.timeStamp - drag.time > 90) velocity = 0;
    if (e.type === 'pointerup' && position + velocity * 0.12 >= 0.4) {
      config.close();
    } else {
      settle();
    }
  }

  for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    el.addEventListener(name, endGesture, { signal: events.signal });
  }

  function reset() {
    gesture = undefined;
    position = config.open ? 0 : 1;
    velocity = 0;
    settle();
  }

  screen.addEventListener('change', reset, { signal: events.signal });
  reduced.addEventListener('change', reset, { signal: events.signal });
  settle();
  return {
    update(next) {
      const changed = next.open !== config.open;
      config = next;
      if (changed) {
        gesture = undefined;
        settle();
      }
    },
    stop() {
      events.abort();
      cancelAnimationFrame(frame);
    },
  };
}

export const vSlidingDrawer = {
  mounted(el, { value }) {
    drawers.set(el, mountDrawer(el, value));
  },
  updated(el, { value }) {
    drawers.get(el).update(value);
  },
  beforeUnmount(el) {
    drawers.get(el).stop();
    drawers.delete(el);
  },
};
